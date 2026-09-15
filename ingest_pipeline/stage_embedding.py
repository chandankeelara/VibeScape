"""
Embedding stage — raw MERT + fused vectors for DJ recommendations.

For each track with preview_status='done' + ml_status='done', downloads
the preview URL to a tempfile, runs the raw MERT-v1-95M encoder over 30
s of audio, and writes:

  - track_embeddings row  model_version='mert_v1_95m_fp32_30s'  (768-d)
  - track_embeddings row  model_version='fused_v1_mert_scalar_lang' (fused)

The fused vector uses the exact same recipe as
scripts/_backfill_fused_embeddings.py (SCALAR_COLS, TOP_LANGS, weights)
so backfilled and freshly-ingested tracks live in the same embedding
space.

Blocks on preview_status='done' + ml_status='done' (fused vector needs
the scalar predictions). language_status is soft — if language hasn't
run yet we fall through to the 'other' one-hot bucket, which is what
the backfill script also does for unknown languages.

GPU-bound (MERT encoder ≈ 400 MB VRAM); max_workers=1 for the same
reason as ClassifyStage.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, iso_now


log = logging.getLogger("vibescape.ingest.embedding")


# ---- Config (mirrors scripts/_backfill_{mert,fused}_embeddings.py) ---------

MERT_MODEL_NAME = "m-a-p/MERT-v1-95M"
SAMPLE_RATE = 24_000
MAX_DURATION_S = 30
MERT_DIM = 768
MERT_MODEL_VERSION = "mert_v1_95m_fp32_30s"
FUSED_MODEL_VERSION = "fused_v1_mert_scalar_lang"

SCALAR_COLS = [
    "energy_pred", "danceability_pred", "valence_pred",
    "vibe_score", "activation", "valence",
    "acousticness", "tempo", "brightness",
]
SCALAR_RANGE = {
    "energy_pred":       (0.0, 1.0),
    "danceability_pred": (0.0, 1.0),
    "valence_pred":      (0.0, 1.0),
    "vibe_score":        (0.0, 100.0),
    "activation":        (0.0, 100.0),
    "valence":           (0.0, 100.0),
    "acousticness":      (0.0, 1.0),
    "tempo":             (40.0, 220.0),
    "brightness":        (0.0, 8000.0),
}
TOP_LANGS = ["en", "kn", "te", "hi", "pa", "sa", "ta", "ur", "km", "pt"]
LANG_DIMS = len(TOP_LANGS) + 1
W_MERT, W_SCALAR, W_LANG = 0.55, 0.25, 0.20
FUSED_DIM = MERT_DIM + len(SCALAR_COLS) + LANG_DIMS


# ---- Vector-builder helpers (duplicated from _backfill_fused_embeddings) ----


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n < 1e-8 else v / n


def _norm_scalar(name: str, val) -> float:
    if val is None:
        return 0.0
    lo, hi = SCALAR_RANGE[name]
    if hi <= lo:
        return 0.0
    x = (float(val) - lo) / (hi - lo)
    return max(0.0, min(1.0, x))


def _lang_onehot(language: Optional[str]) -> np.ndarray:
    v = np.zeros(LANG_DIMS, dtype=np.float32)
    if language:
        code = str(language).lower()
        v[TOP_LANGS.index(code) if code in TOP_LANGS else LANG_DIMS - 1] = 1.0
    else:
        v[-1] = 1.0
    return v


def _build_fused(row_dict: dict, mert_vec: np.ndarray) -> np.ndarray:
    scalars = np.array(
        [_norm_scalar(c, row_dict.get(c)) for c in SCALAR_COLS],
        dtype=np.float32,
    )
    lang = _lang_onehot(row_dict.get("language"))
    fused = np.concatenate([
        W_MERT   * _l2(mert_vec),
        W_SCALAR * _l2(scalars),
        W_LANG   * lang,
    ])
    return _l2(fused)


# ---- Librosa piggyback (extract scalar features from the same waveform) ----


def _extract_librosa_scalars(waveform: np.ndarray) -> dict:
    """Extract just the three scalars the fused vector uses. Uses MERT's
    sample rate (24 kHz) — negligible drift vs the 22.05 kHz that the
    legacy ingest/features.py uses, and cheaper than resampling.

    Failures are non-fatal — return an empty dict and the fused vector
    will fall back to zeros for these dims (same as if the columns were
    NULL). librosa import is lazy so this module still loads on hosts
    without it."""
    try:
        import librosa
    except ImportError:
        return {}
    out: dict = {}
    try:
        tempo, _ = librosa.beat.beat_track(y=waveform, sr=SAMPLE_RATE)
        out["tempo"] = float(np.asarray(tempo).flatten()[0])
    except Exception as e:
        log.warning("librosa tempo failed: %s", e)
    try:
        centroid = librosa.feature.spectral_centroid(y=waveform, sr=SAMPLE_RATE)
        out["brightness"] = float(np.mean(centroid))
    except Exception as e:
        log.warning("librosa centroid failed: %s", e)
    try:
        y_h, y_p = librosa.effects.hpss(waveform)
        h_energy = float(np.mean(librosa.feature.rms(y=y_h)))
        p_energy = float(np.mean(librosa.feature.rms(y=y_p)))
        out["acousticness"] = float(h_energy / (h_energy + p_energy + 1e-6))
    except Exception as e:
        log.warning("librosa hpss failed: %s", e)
    return out


# ---- Audio download + decode ------------------------------------------------


def _download_to_tempfile(url: str, suffix: str = ".mp3") -> Optional[str]:
    try:
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            with urllib.request.urlopen(url, timeout=30) as r:
                fh.write(r.read())
        return path
    except Exception as e:
        log.warning("preview download failed for %s: %s", url[:80], e)
        return None


def _decode_via_ffmpeg(path: str, target_sr: int) -> Optional[np.ndarray]:
    """m4a/aac containers libsndfile can't open — pipe through ffmpeg."""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    cmd = [ffmpeg, "-i", str(path), "-f", "s16le", "-ac", "1",
           "-ar", str(target_sr), "-loglevel", "error", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        pcm = np.frombuffer(proc.stdout, dtype=np.int16)
        return (pcm.astype(np.float32) / 32768.0)
    except Exception as e:
        log.warning("ffmpeg decode failed for %s: %s", Path(path).name, e)
        return None


def _load_audio(path: str, target_sr: int, max_samples: int) -> Optional[np.ndarray]:
    """Load audio -> mono float32 at target_sr, clipped to max_samples."""
    y: Optional[np.ndarray] = None
    try:
        import soundfile as sf
        y, sr = sf.read(path, dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
        if sr != target_sr:
            import librosa
            y = librosa.resample(y.astype(np.float32), orig_sr=sr, target_sr=target_sr)
    except Exception:
        y = _decode_via_ffmpeg(path, target_sr)
        if y is None:
            return None
    if y is None or len(y) == 0:
        return None
    if len(y) > max_samples:
        y = y[:max_samples]
    elif len(y) < target_sr:
        return None  # < 1s of audio isn't worth embedding
    peak = float(np.max(np.abs(y)))
    if peak > 1.0:
        y = y / peak
    return y


# ---- MERT encoder (lazy singleton, GPU) -------------------------------------


class _MertEmbedder:
    """Wraps MERT-v1-95M + its feature extractor. 768-d vector per clip."""

    def __init__(self, device: Optional[str] = None):
        import torch
        from transformers import AutoModel, Wav2Vec2FeatureExtractor
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        log.info("loading %s on %s", MERT_MODEL_NAME, self.device)
        self.feat = Wav2Vec2FeatureExtractor.from_pretrained(
            MERT_MODEL_NAME, trust_remote_code=True
        )
        self.model = AutoModel.from_pretrained(
            MERT_MODEL_NAME, trust_remote_code=True
        ).to(self.device).eval()
        for p in self.model.parameters():
            p.requires_grad_(False)
        log.info("MERT loaded (hidden_size=%d)", int(self.model.config.hidden_size))

    def embed(self, waveform: np.ndarray) -> np.ndarray:
        inputs = self.feat([waveform], sampling_rate=SAMPLE_RATE,
                           return_tensors="pt", padding=True)
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


_embedder_singleton: _MertEmbedder | None = None


def _get_embedder() -> _MertEmbedder:
    """Shared MERT instance so we don't reload weights per row."""
    global _embedder_singleton
    if _embedder_singleton is None:
        _embedder_singleton = _MertEmbedder()
    return _embedder_singleton


# ---- Stage ------------------------------------------------------------------


_FETCH_COLS = (
    "id, spotify_id, preview_url, audio_path, language, "
    + ", ".join(SCALAR_COLS)
)


class EmbeddingStage(Stage):
    name = "embedding"
    status_column = "embedding_status"
    # GPU-bound like classify; MERT weights are heavy. Serialize.
    max_workers = 1

    def __init__(self):
        # Import guard — the pipeline shouldn't refuse to construct on
        # machines without torch. If we can't extract, per-row process
        # will fail and rows go to STATUS_FAILED.
        try:
            import torch  # noqa: F401
        except ImportError as e:
            raise SystemExit(f"EmbeddingStage requires torch: {e}")
        self._max_samples = SAMPLE_RATE * MAX_DURATION_S

    def fetch_pending(self, conn, limit: int) -> list:
        # Strict gates: download must have cached audio, and ml must
        # have populated the scalar columns the fused vector needs.
        rows = conn.execute(
            f"SELECT {_FETCH_COLS} FROM tracks "
            f"WHERE embedding_status = 'pending' "
            f"AND download_status = 'done' "
            f"AND ml_status = 'done' "
            f"AND audio_path IS NOT NULL AND audio_path != '' "
            f"ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        from .stage_download import resolve_audio_path
        cached = resolve_audio_path(row["audio_path"])
        if cached is None:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields={"ingestion_attempted_at": iso_now()},
                error="cached audio missing",
            )
        source_path = str(cached)
        tmp = None  # kept for the finally block's tempfile cleanup contract
        try:
            waveform = _load_audio(source_path, SAMPLE_RATE, self._max_samples)
            if waveform is None:
                return RowResult(
                    track_id=int(row["id"]),
                    status=STATUS_FAILED,
                    fields={"ingestion_attempted_at": iso_now()},
                    error="audio decode failed",
                )
            embedder = _get_embedder()
            mert_vec = embedder.embed(waveform)
            if mert_vec.size != MERT_DIM:
                return RowResult(
                    track_id=int(row["id"]),
                    status=STATUS_FAILED,
                    fields={"ingestion_attempted_at": iso_now()},
                    error=f"unexpected MERT dim {mert_vec.size}",
                )
            # Piggyback: extract librosa scalar features (acousticness /
            # tempo / brightness) from the same waveform so the fused
            # vector below sees real values instead of the zero defaults
            # that hit when those columns are NULL. Cheap — no second
            # audio load. Failures here are non-fatal; we just fall
            # through to zeros for those 3 dims.
            librosa_scalars = _extract_librosa_scalars(waveform)
            row_dict = {k: row[k] for k in row.keys()}
            for k, v in librosa_scalars.items():
                # Only overwrite when the row's value is missing — never
                # clobber a legacy value that came from ingest/features.py.
                if row_dict.get(k) is None:
                    row_dict[k] = v
            fused = _build_fused(row_dict, mert_vec.astype(np.float32, copy=True))
            if fused.shape[0] != FUSED_DIM:
                return RowResult(
                    track_id=int(row["id"]),
                    status=STATUS_FAILED,
                    fields={"ingestion_attempted_at": iso_now()},
                    error=f"unexpected fused dim {fused.shape[0]}",
                )
            # Persist the librosa scalars back to tracks alongside the
            # blobs (only for columns that were previously NULL). This is
            # what makes future fused-vector rebuilds also see real values.
            persist_fields = {
                "__mert_blob__":  mert_vec.astype(np.float32, copy=False).tobytes(),
                "__mert_dim__":   int(mert_vec.size),
                "__fused_blob__": fused.astype(np.float32, copy=False).tobytes(),
                "__fused_dim__":  int(fused.shape[0]),
                "ingestion_attempted_at": iso_now(),
            }
            for k in ("acousticness", "tempo", "brightness"):
                if row[k] is None and k in librosa_scalars:
                    persist_fields[k] = float(librosa_scalars[k])
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_DONE,
                fields=persist_fields,
            )
        finally:
            # Only clean up if we downloaded a tempfile; the DownloadStage
            # cache must persist for later stages / re-runs.
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def run_batch(self, conn, limit: int, log_):
        """Override: pull embedding blobs out of fields and INSERT OR REPLACE
        into track_embeddings before the generic UPDATE runs."""
        rows = self.fetch_pending(conn, limit)
        counts = {STATUS_DONE: 0, STATUS_FAILED: 0}
        if not rows:
            log_.info("[%s] no pending rows", self.name)
            return counts
        log_.info("[%s] processing %d rows (max_workers=%d)",
                  self.name, len(rows), self.max_workers)

        from concurrent.futures import ThreadPoolExecutor, as_completed
        results: list[RowResult] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(self._safe_process, r): r for r in rows}
            for fut in as_completed(futs):
                results.append(fut.result())

        now = iso_now()
        for res in results:
            fields = dict(res.fields or {})
            mert_blob  = fields.pop("__mert_blob__", None)
            fused_blob = fields.pop("__fused_blob__", None)
            # dims are implicit in the column definition now (F32_BLOB(dim))
            fields.pop("__mert_dim__", None)
            fields.pop("__fused_dim__", None)
            fields[self.status_column] = res.status
            self._commit_row_update(conn, int(res.track_id), fields, log_)
            if res.status == STATUS_DONE and mert_blob and fused_blob:
                # Option A layout: one row per track with both variants inline.
                # UPSERT so re-embeds (e.g. after a model bump) update in place.
                conn.execute(
                    "INSERT INTO track_embeddings "
                    "  (track_id, mert_embedding, fused_embedding, model_version, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(track_id) DO UPDATE SET "
                    "  mert_embedding  = excluded.mert_embedding, "
                    "  fused_embedding = excluded.fused_embedding, "
                    "  model_version   = excluded.model_version, "
                    "  updated_at      = excluded.updated_at",
                    (int(res.track_id), mert_blob, fused_blob, "mert_v1", now),
                )
            counts[res.status] = counts.get(res.status, 0) + 1
        conn.commit()
        log_.info("[%s] batch done: %s", self.name, counts)
        return counts
