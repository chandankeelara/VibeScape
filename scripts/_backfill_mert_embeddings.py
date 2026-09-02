"""Backfill MERT-v1-95M embeddings into a dedicated track_embeddings table.

For each track with a local audio file and ingestion_status='done', run the
raw MERT encoder (no head) over up to 30 s of audio and store the mean-pooled
last_hidden_state as a float32 BLOB in `track_embeddings`. Idempotent: skips
tracks that already have an embedding for the current EMBEDDING_VERSION.

Storage layout (separate from tracks so we can:
  - track multiple embedding versions per track cleanly
  - keep tracks row-scans lean — the recommender pays the load cost only
    when it needs vectors, not on every 'get track metadata' query
  - swap in a new model without touching the tracks schema):

    CREATE TABLE track_embeddings (
      track_id       INTEGER NOT NULL,
      model_version  TEXT NOT NULL,
      dim            INTEGER NOT NULL,
      embedding      BLOB NOT NULL,   -- float32 tobytes(), dim floats
      created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (track_id, model_version),
      FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
    );

Why 30 s single-pass: MERT's HuBERT-style relative positional encoding
extrapolates gracefully to 30 s, and a single forward pass keeps
cross-frame attention intact (each token attends to every other token
across the whole clip), which preserves information the window-and-average
approach throws away.

Why raw MERT instead of the trained VibeRegressor checkpoint: the trained
head is specialized for danceability/energy/valence prediction. For
similarity search we want a general-purpose acoustic representation.

Run:
    D:/Softwares/MiniConda/python.exe scripts/_backfill_mert_embeddings.py [--limit N]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np


# ---- DB connection (local SQLite by default; matches _turso_*.py pattern) ----
os.environ.setdefault("DB_BACKEND", "sqlite")
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
import db_client  # noqa: E402


# ---- Config -----------------------------------------------------------------

MERT_MODEL_NAME = "m-a-p/MERT-v1-95M"
SAMPLE_RATE = 24_000       # MERT-v1 pretraining rate
MAX_DURATION_S = 30        # Full preview length
# Encodes: base model + fp32 + full 30s window. Bump when any of those change.
EMBEDDING_VERSION = "mert_v1_95m_fp32_30s"


# ---- Schema migration -------------------------------------------------------


def ensure_embeddings_table(conn) -> None:
    """Create track_embeddings if missing. Also cleans up the previous
    per-track JSON column so we don't leave dead schema around."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS track_embeddings (
            track_id       INTEGER NOT NULL,
            model_version  TEXT NOT NULL,
            dim            INTEGER NOT NULL,
            embedding      BLOB NOT NULL,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (track_id, model_version),
            FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_track_embeddings_model "
        "ON track_embeddings(model_version)"
    )

    # Drop the earlier per-tracks JSON column if it was created by an
    # older version of this script. Only 3 rows ever got populated (from
    # the smoke test) and we're about to re-embed them into the new table.
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    if "mert_embedding_json" in cols:
        print("[migrate] dropping legacy tracks.mert_embedding_json column", flush=True)
        try:
            conn.execute("ALTER TABLE tracks DROP COLUMN mert_embedding_json")
        except Exception as e:
            # DROP COLUMN needs SQLite 3.35+ (2021). If unavailable, leave
            # the column in place — it's harmless, just unused.
            print(f"[migrate] DROP COLUMN not supported ({e}); leaving column, "
                  "it will just sit unused.", flush=True)
    conn.commit()


# ---- Audio loading ----------------------------------------------------------


_FFMPEG_EXE: Optional[str] = None


def _ffmpeg_path() -> Optional[str]:
    """Return path to the bundled ffmpeg binary (from imageio-ffmpeg)."""
    global _FFMPEG_EXE
    if _FFMPEG_EXE is not None:
        return _FFMPEG_EXE
    try:
        import imageio_ffmpeg
        _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _FFMPEG_EXE = ""
    return _FFMPEG_EXE or None


def _decode_via_ffmpeg(path: Path, target_sr: int,
                       max_duration_s: int = MAX_DURATION_S) -> Optional[np.ndarray]:
    """Pipe m4a/mp3/etc through ffmpeg into raw mono f32 PCM at target_sr.

    Hard-capped at max_duration_s via ffmpeg's `-t` flag so a giant or
    malformed file can't blow up the subprocess reader thread. Reads stdout
    incrementally with a size ceiling as a second layer of protection.
    """
    exe = _ffmpeg_path()
    if not exe:
        return None
    import subprocess
    cmd = [
        exe, "-nostdin", "-loglevel", "error",
        "-t", str(max_duration_s),
        "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1",
        "-ar", str(target_sr), "-",
    ]
    max_bytes = 4 * target_sr * max_duration_s * 4
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        chunks = []
        total = 0
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
        if proc.returncode != 0 or not chunks:
            return None
        return np.frombuffer(b"".join(chunks), dtype=np.float32).copy()
    except Exception:
        try: proc.kill()
        except Exception: pass
        return None


def _load_audio(path: Path, target_sr: int, max_samples: int) -> Optional[np.ndarray]:
    """Load audio -> mono float32 at target_sr, clipped to max_samples."""
    y: Optional[np.ndarray] = None
    try:
        import soundfile as sf
        y, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
        if sr != target_sr:
            import librosa
            y = librosa.resample(y.astype(np.float32), orig_sr=sr, target_sr=target_sr)
    except Exception:
        y = _decode_via_ffmpeg(path, target_sr)
        if y is None:
            print(f"    ! audio load failed for {path.name} "
                  f"(soundfile + ffmpeg both failed)", flush=True)
            return None

    if y is None or len(y) == 0:
        return None
    if len(y) > max_samples:
        y = y[:max_samples]
    elif len(y) < target_sr:
        print(f"    ! audio too short ({len(y)/target_sr:.2f}s) for {path.name}", flush=True)
        return None
    peak = float(np.max(np.abs(y)))
    if peak > 1.0:
        y = y / peak
    return y


# ---- MERT loader ------------------------------------------------------------


class MertEmbedder:
    """Wraps MERT-v1-95M + its feature extractor. Emits one 768-D vector per clip."""

    def __init__(self, device: str = "cuda"):
        import torch
        from transformers import AutoModel, Wav2Vec2FeatureExtractor

        self.torch = torch
        self.device = device
        print(f"[mert] loading {MERT_MODEL_NAME} on {device} ...", flush=True)
        self.feat = Wav2Vec2FeatureExtractor.from_pretrained(
            MERT_MODEL_NAME, trust_remote_code=True
        )
        self.model = AutoModel.from_pretrained(
            MERT_MODEL_NAME, trust_remote_code=True
        ).to(device).eval()
        for p in self.model.parameters():
            p.requires_grad_(False)
        self.hidden_size = int(getattr(self.model.config, "hidden_size", 768))
        print(f"[mert] loaded. hidden_size={self.hidden_size}, device={device}", flush=True)

    def embed(self, waveform: np.ndarray) -> np.ndarray:
        inputs = self.feat(
            [waveform],
            sampling_rate=SAMPLE_RATE,
            return_tensors="pt",
            padding=True,
        )
        input_values = inputs["input_values"].to(self.device)
        try:
            with self.torch.inference_mode():
                out = self.model(input_values, output_hidden_states=False)
            vec = out.last_hidden_state.mean(dim=1).squeeze(0).float().cpu().numpy()
            return vec.astype(np.float32)
        finally:
            del input_values
            if self.device.startswith("cuda"):
                self.torch.cuda.empty_cache()


# ---- Main -------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of tracks to process (for smoke tests).")
    ap.add_argument("--device", default="auto",
                    help="'auto' | 'cuda' | 'cpu'. Auto uses CUDA if available.")
    ap.add_argument("--force", action="store_true",
                    help="Re-embed tracks that already have an embedding for "
                         f"model_version={EMBEDDING_VERSION!r}.")
    args = ap.parse_args()

    import torch
    if args.device == "auto":
        args.device = "cuda" if torch.cuda.is_available() else "cpu"

    conn = db_client.create_connection()
    ensure_embeddings_table(conn)

    # Eligible: has local audio, ingestion done, AND either --force or no
    # existing embedding for the current model_version. Anti-join via NOT
    # EXISTS is cleaner than LEFT JOIN + IS NULL here.
    where_missing = "" if args.force else (
        f" AND NOT EXISTS ("
        f"    SELECT 1 FROM track_embeddings e "
        f"    WHERE e.track_id = t.id AND e.model_version = ?"
        f" )"
    )
    params = () if args.force else (EMBEDDING_VERSION,)
    rows = conn.execute(
        f"""
        SELECT t.id, t.title, t.artist, t.audio_path
          FROM tracks t
         WHERE t.ingestion_status = 'done'
           AND t.audio_path IS NOT NULL
           {where_missing}
         ORDER BY t.id
        """,
        params,
    ).fetchall()
    total_eligible = len(rows)
    if args.limit is not None:
        rows = rows[: args.limit]
    print(f"[plan] {total_eligible} eligible tracks; processing {len(rows)} "
          f"(device={args.device}, force={args.force}, "
          f"model_version={EMBEDDING_VERSION!r})", flush=True)

    if not rows:
        print("[done] nothing to do.", flush=True)
        conn.close()
        return

    max_samples = int(MAX_DURATION_S * SAMPLE_RATE)
    embedder = MertEmbedder(device=args.device)
    dim = embedder.hidden_size

    t0 = time.time()
    ok = skipped = failed = 0
    for i, r in enumerate(rows, 1):
        tid = int(r["id"])
        title = (r["title"] or "")[:50]
        artist = (r["artist"] or "")[:30]
        raw_path = r["audio_path"]
        path = Path(raw_path)
        if not path.is_absolute():
            path = _REPO / raw_path
        if not path.exists():
            print(f"[{i:>4}/{len(rows)}] track {tid} - missing file: {raw_path}",
                  flush=True)
            skipped += 1
            continue

        try:
            y = _load_audio(path, SAMPLE_RATE, max_samples)
            if y is None:
                skipped += 1
                continue
            vec = embedder.embed(y)
            # Upsert: INSERT OR REPLACE on the composite PK (track_id, model_version)
            # so --force cleanly overwrites without needing a separate DELETE.
            conn.execute(
                "INSERT OR REPLACE INTO track_embeddings "
                "  (track_id, model_version, dim, embedding, created_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (tid, EMBEDDING_VERSION, int(dim), vec.tobytes()),
            )
            if i % 10 == 0:
                conn.commit()
            ok += 1
        except Exception as e:
            print(f"[{i:>4}/{len(rows)}] track {tid} - embed failed: {e}", flush=True)
            failed += 1
            continue

        if i % 20 == 0 or i == len(rows):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0.0
            eta = (len(rows) - i) / rate if rate > 0 else 0.0
            print(f"[{i:>4}/{len(rows)}] ok={ok} skipped={skipped} failed={failed} "
                  f"| {rate:.2f} tracks/s | ETA {eta/60:.1f} min | "
                  f"last: {title} - {artist}", flush=True)

    conn.commit()
    conn.close()
    elapsed = time.time() - t0
    print(f"\n[done] total={len(rows)}  ok={ok}  skipped={skipped}  failed={failed}  "
          f"elapsed={elapsed/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
