"""Rescore all locally-audioed tracks using the trained regressor on the
FULL 30-second preview instead of the center-10s crop, and OVERWRITE the
scalar prediction columns in the local SQLite tracks table.

Destructive: replaces existing 10 s scores. Sets model_version='mert_v1_30s'
so rescored rows can be told apart from originals ('mert_v1'). Tracks
without local audio are skipped and keep their 10 s scores.

Fields written per track:
  energy_pred, danceability_pred, valence_pred   (raw 0-1)
  vibe_score_ml                                  (raw 0-1, = 0.55e + 0.45d)
  activation                                     (0-100, = vibe_score_ml * 100)
  valence  [column, 0-100]                       (= valence_pred * 100)
  vibe_score                                     (= activation, matches prod)
  mood                                           (recomputed via scoring.mood_label)
  model_version                                  = 'mert_v1_30s'
  ml_predicted_at                                = now

Run:
    D:/Softwares/MiniConda/python.exe scripts/_rescore_regressor_30s.py [--limit N]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

# ---- Repo paths + DB (local sqlite) ----------------------------------------
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO))
os.environ.setdefault("DB_BACKEND", "sqlite")
import db_client  # noqa: E402
from ingest import scoring  # noqa: E402  (for mood_label)

CKPT_PATH = _REPO / "ml" / "models" / "mert_v1.ckpt"
SAMPLE_RATE = 24_000
CROP_30S = 30 * SAMPLE_RATE
VIBE_ENERGY_W = 0.55
VIBE_DANCE_W = 0.45
NEW_MODEL_VERSION = "mert_v1_30s"


# ---- Audio loader (same fallback pattern as the embedding script) -----------

_FFMPEG_EXE: Optional[str] = None


def _ffmpeg_path() -> Optional[str]:
    global _FFMPEG_EXE
    if _FFMPEG_EXE is not None:
        return _FFMPEG_EXE
    try:
        import imageio_ffmpeg
        _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _FFMPEG_EXE = ""
    return _FFMPEG_EXE or None


def _decode_via_ffmpeg(path: Path, target_sr: int, max_dur_s: int = 30) -> Optional[np.ndarray]:
    exe = _ffmpeg_path()
    if not exe:
        return None
    cmd = [exe, "-nostdin", "-loglevel", "error", "-t", str(max_dur_s),
           "-i", str(path), "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", "1", "-ar", str(target_sr), "-"]
    max_bytes = 4 * target_sr * max_dur_s * 4
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks, total = [], 0
    try:
        while True:
            chunk = proc.stdout.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                proc.kill()
                return None
            chunks.append(chunk)
        proc.wait(timeout=10)
    except Exception:
        try: proc.kill()
        except Exception: pass
        return None
    if proc.returncode != 0 or not chunks:
        return None
    return np.frombuffer(b"".join(chunks), dtype=np.float32).copy()


def _load_audio(path: Path) -> Optional[np.ndarray]:
    try:
        import soundfile as sf
        y, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
        if sr != SAMPLE_RATE:
            import librosa
            y = librosa.resample(y.astype(np.float32), orig_sr=sr, target_sr=SAMPLE_RATE)
        return y
    except Exception:
        return _decode_via_ffmpeg(path, SAMPLE_RATE)


def prep_full_30s(y: np.ndarray) -> np.ndarray:
    if len(y) > CROP_30S:
        y = y[:CROP_30S]
    if len(y) < CROP_30S:
        y = np.pad(y, (0, CROP_30S - len(y)))
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 1.0:
        y = y / peak
    return y


# ---- Main -------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of tracks (smoke test).")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    import torch
    if args.device == "auto":
        args.device = "cuda" if torch.cuda.is_available() else "cpu"
    device = args.device

    if not CKPT_PATH.exists():
        print(f"[abort] ckpt not found: {CKPT_PATH}", flush=True)
        sys.exit(1)

    from ml.src.model import MERTVibeRegressor
    print(f"[load] {CKPT_PATH.name} on {device} ...", flush=True)
    model = MERTVibeRegressor.load_from_checkpoint(str(CKPT_PATH), map_location=device)
    model.eval().to(device)
    for p in model.parameters():
        p.requires_grad_(False)
    target_names = list(model.target_names)
    ti_energy = target_names.index("energy")
    ti_dance = target_names.index("danceability")
    ti_valence = target_names.index("valence")
    print(f"[load] targets: {target_names}", flush=True)

    conn = db_client.create_connection()

    # Local SQLite may be behind the canonical schema.sql (missing
    # ml_predicted_at). Add it idempotently before running the UPDATEs.
    existing_cols = {c["name"] for c in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    if "ml_predicted_at" not in existing_cols:
        print("[migrate] adding column tracks.ml_predicted_at", flush=True)
        conn.execute("ALTER TABLE tracks ADD COLUMN ml_predicted_at TIMESTAMP")
        conn.commit()

    rows = conn.execute(
        "SELECT id, title, artist, audio_path FROM tracks "
        "WHERE ingestion_status='done' AND audio_path IS NOT NULL ORDER BY id"
    ).fetchall()
    total_eligible = len(rows)
    if args.limit is not None:
        rows = rows[: args.limit]
    print(f"[plan] {total_eligible} eligible, processing {len(rows)} (device={device})",
          flush=True)

    ok = skipped = failed = 0
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        tid = int(r["id"])
        raw = r["audio_path"]
        path = Path(raw) if Path(raw).is_absolute() else _REPO / raw
        if not path.exists():
            skipped += 1
            continue
        try:
            y = _load_audio(path)
            if y is None or len(y) < SAMPLE_RATE:
                skipped += 1
                continue
            y30 = prep_full_30s(y)
            t = torch.from_numpy(y30).unsqueeze(0).to(device)
            with torch.inference_mode():
                preds = model(t).cpu().numpy()[0]
            del t
            if device.startswith("cuda"):
                torch.cuda.empty_cache()
            energy = float(preds[ti_energy])
            dance = float(preds[ti_dance])
            valence_raw = float(preds[ti_valence])
            vibe_ml = VIBE_ENERGY_W * energy + VIBE_DANCE_W * dance   # 0-1
            activation = vibe_ml * 100.0                              # 0-100
            valence_col = valence_raw * 100.0                         # 0-100
            vibe_score = activation
            mood = scoring.mood_label(activation, valence_col)
            now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

            conn.execute(
                "UPDATE tracks SET "
                "  energy_pred = ?, danceability_pred = ?, valence_pred = ?, "
                "  vibe_score_ml = ?, "
                "  activation = ?, valence = ?, vibe_score = ?, mood = ?, "
                "  model_version = ?, ml_predicted_at = ? "
                "WHERE id = ?",
                (energy, dance, valence_raw, vibe_ml,
                 activation, valence_col, vibe_score, mood,
                 NEW_MODEL_VERSION, now_iso, tid),
            )
            if i % 20 == 0:
                conn.commit()
            ok += 1
        except Exception as e:
            print(f"[{i:>4}/{len(rows)}] track {tid} - failed: {e}", flush=True)
            failed += 1
            continue

        if i % 20 == 0 or i == len(rows):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed else 0.0
            eta_min = (len(rows) - i) / rate / 60 if rate else 0.0
            print(f"[{i:>4}/{len(rows)}] ok={ok} skipped={skipped} failed={failed} "
                  f"| {rate:.2f} tracks/s | ETA {eta_min:.1f} min", flush=True)

    conn.commit()
    conn.close()
    print(f"\n[done] total={len(rows)} ok={ok} skipped={skipped} failed={failed} "
          f"elapsed={(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
