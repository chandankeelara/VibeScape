"""Compare vibe-score / energy / dance / valence predictions when the
trained regressor is fed the current production window (center 10 s) vs
the full 30 s preview. READ-ONLY -- nothing written to the database.

Loads N random tracks (default 25) that have local audio, runs the
trained MERTVibeRegressor twice per track -- once on the center-10s crop,
once on the full 30s clip -- and prints a side-by-side comparison plus
summary stats (mean/max delta, correlation).

Answers: does the 10-second crop meaningfully bias predictions vs
seeing the whole preview?

Run in a separate shell (safe to run concurrently with the embedding
backfill; both fit comfortably in RTX 4060 VRAM):
    D:/Softwares/MiniConda/python.exe scripts/_regressor_window_compare.py
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path
from typing import Optional

import numpy as np

# ---- Repo paths + DB (read-only) --------------------------------------------
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO))   # for `ml.src.model` import
os.environ.setdefault("DB_BACKEND", "sqlite")
import db_client  # noqa: E402

CKPT_PATH = _REPO / "ml" / "models" / "mert_v1.ckpt"
SAMPLE_RATE = 24_000
CROP_10S = 10 * SAMPLE_RATE
CROP_30S = 30 * SAMPLE_RATE
VIBE_ENERGY_W = 0.55
VIBE_DANCE_W = 0.45


# ---- Audio loader (same ffmpeg fallback as the embedding script) ------------

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
    import subprocess
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
    """Load audio -> mono float32 at 24kHz. Try soundfile, fall back to ffmpeg."""
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


# ---- Window preparation matches the production regressor exactly ------------


def prep_center_10s(y: np.ndarray) -> np.ndarray:
    """Match ml/src/predict.py + modal_app.py: center-crop to 10s, pad if short."""
    if len(y) < SAMPLE_RATE:
        y = np.pad(y, (0, SAMPLE_RATE - len(y)))
    if len(y) > CROP_10S:
        start = max(0, (len(y) - CROP_10S) // 2)
        y = y[start : start + CROP_10S]
    else:
        y = np.pad(y, (0, CROP_10S - len(y)))
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 1.0:
        y = y / peak
    return y


def prep_full_30s(y: np.ndarray) -> np.ndarray:
    """Take up to 30s from the front, pad if shorter."""
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
    ap.add_argument("--n", type=int, default=25, help="Number of random tracks to sample.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    import torch

    if args.device == "auto":
        args.device = "cuda" if torch.cuda.is_available() else "cpu"
    device = args.device

    if not CKPT_PATH.exists():
        print(f"[abort] trained checkpoint not found at {CKPT_PATH}", flush=True)
        sys.exit(1)

    # Import the model class lazily (it drags in transformers + pl)
    from ml.src.model import MERTVibeRegressor

    print(f"[load] {CKPT_PATH.name} on {device} ...", flush=True)
    model = MERTVibeRegressor.load_from_checkpoint(str(CKPT_PATH), map_location=device)
    model.eval().to(device)
    for p in model.parameters():
        p.requires_grad_(False)
    target_names = list(model.target_names)  # e.g. ['danceability', 'energy', 'valence']
    print(f"[load] targets: {target_names}", flush=True)

    # Sample tracks
    conn = db_client.create_connection()
    rows = conn.execute(
        "SELECT id, title, artist, audio_path, vibe_score AS db_vibe, "
        "       energy_pred AS db_energy, danceability_pred AS db_dance, "
        "       valence_pred AS db_valence "
        "  FROM tracks "
        " WHERE ingestion_status='done' AND audio_path IS NOT NULL"
    ).fetchall()
    conn.close()
    random.seed(args.seed)
    picks = random.sample(rows, min(args.n, len(rows)))
    print(f"[plan] sampled {len(picks)} tracks (seed={args.seed}) of {len(rows)} eligible\n",
          flush=True)

    def score(y_prepped: np.ndarray) -> dict:
        t = torch.from_numpy(y_prepped).unsqueeze(0).to(device)
        with torch.inference_mode():
            preds = model(t).cpu().numpy()[0]
        out = {name: float(preds[i]) for i, name in enumerate(target_names)}
        e = float(out.get("energy", 0.0))
        d = float(out.get("danceability", 0.0))
        out["vibe_score"] = 100.0 * (VIBE_ENERGY_W * e + VIBE_DANCE_W * d)
        return out

    # Header
    print(f"  {'#':<3} {'title':<32} {'artist':<20}  "
          f"{'vibe_10':>8} {'vibe_30':>8} {'dv':>6}   "
          f"{'e_10':>5} {'e_30':>5}  {'d_10':>5} {'d_30':>5}  "
          f"{'v_10':>5} {'v_30':>5}", flush=True)
    print("  " + "-" * 130, flush=True)

    deltas_vibe = []
    deltas_energy = []
    deltas_dance = []
    deltas_valence = []
    v10s, v30s = [], []
    ok = 0
    skipped = 0

    for i, r in enumerate(picks, 1):
        path = _REPO / r["audio_path"] if not Path(r["audio_path"]).is_absolute() \
            else Path(r["audio_path"])
        if not path.exists():
            skipped += 1
            continue
        y = _load_audio(path)
        if y is None or len(y) < SAMPLE_RATE:
            skipped += 1
            continue

        s10 = score(prep_center_10s(y))
        s30 = score(prep_full_30s(y))

        title = (r["title"] or "")[:30]
        artist = (r["artist"] or "")[:18]
        dv = s30["vibe_score"] - s10["vibe_score"]
        de = s30.get("energy", 0.0) - s10.get("energy", 0.0)
        dd = s30.get("danceability", 0.0) - s10.get("danceability", 0.0)
        dvl = s30.get("valence", 0.0) - s10.get("valence", 0.0)
        deltas_vibe.append(dv)
        deltas_energy.append(de)
        deltas_dance.append(dd)
        deltas_valence.append(dvl)
        v10s.append(s10["vibe_score"])
        v30s.append(s30["vibe_score"])

        print(f"  {i:<3} {title:<32} {artist:<20}  "
              f"{s10['vibe_score']:>8.2f} {s30['vibe_score']:>8.2f} {dv:>+6.2f}   "
              f"{s10.get('energy',0):>5.2f} {s30.get('energy',0):>5.2f}  "
              f"{s10.get('danceability',0):>5.2f} {s30.get('danceability',0):>5.2f}  "
              f"{s10.get('valence',0):>5.2f} {s30.get('valence',0):>5.2f}",
              flush=True)
        ok += 1

    # Summary
    print("\n" + "=" * 60, flush=True)
    print(f"processed: ok={ok} skipped={skipped}", flush=True)
    if not deltas_vibe:
        return
    dv = np.array(deltas_vibe)
    de = np.array(deltas_energy)
    dd = np.array(deltas_dance)
    dvl = np.array(deltas_valence)
    v10a = np.array(v10s); v30a = np.array(v30s)
    corr = float(np.corrcoef(v10a, v30a)[0, 1])
    print(f"\nvibe_score delta (30s - 10s):", flush=True)
    print(f"  mean={dv.mean():+.2f}  mean|d|={np.abs(dv).mean():.2f}  "
          f"max|d|={np.abs(dv).max():.2f}  std={dv.std():.2f}", flush=True)
    print(f"  correlation(vibe_10, vibe_30) = {corr:.3f}", flush=True)
    print(f"\nenergy delta       mean={de.mean():+.3f}  mean|d|={np.abs(de).mean():.3f}  "
          f"max|d|={np.abs(de).max():.3f}", flush=True)
    print(f"danceability delta mean={dd.mean():+.3f}  mean|d|={np.abs(dd).mean():.3f}  "
          f"max|d|={np.abs(dd).max():.3f}", flush=True)
    print(f"valence delta      mean={dvl.mean():+.3f}  mean|d|={np.abs(dvl).mean():.3f}  "
          f"max|d|={np.abs(dvl).max():.3f}", flush=True)
    print("\nInterpretation:", flush=True)
    print("  mean|dvibe| < 2 pts and corr > 0.98 -> 10s crop is fine, don't change prod.", flush=True)
    print("  mean|dvibe| in [2, 8] and corr in [0.9, 0.98] -> measurable drift; worth", flush=True)
    print("     re-training on 30s if you care about a few points of precision.", flush=True)
    print("  mean|dvibe| > 8 or corr < 0.9 -> 10s crop is biasing predictions; the", flush=True)
    print("     regressor's picture of a track differs substantially from a full-song view.", flush=True)


if __name__ == "__main__":
    main()
