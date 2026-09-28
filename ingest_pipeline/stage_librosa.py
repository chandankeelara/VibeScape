"""
Librosa feature-bank stage.

Extracts the classical DSP feature set (tempo, spectral shape, MFCC,
chroma, tonnetz, …) from the cached preview and writes it to the raw
feature columns on tracks.

Why this exists as its own stage
--------------------------------
These columns were populated by the legacy ingest/ingest.py worker and
were simply never ported to the v2 pipeline, so every track ingested by
v2 had them NULL. That is not cosmetic: three of them — acousticness,
tempo, brightness — are in stage_fuse.SCALAR_COLS, and _norm_scalar()
maps NULL to 0.0. So those dims were constant-zero for v2 tracks and
real-valued for legacy ones, which separates old and new tracks in the
fused space along axes that carry no actual signal.

EmbeddingStage grew a partial workaround: it piggybacked an extraction
of exactly those three scalars off the waveform it had already loaded.
That patched the fused vector but left the other fourteen columns NULL,
and it only ran if embedding ran. This stage does the whole bank once,
early, so every later consumer sees real values.

Reads the cached file from DownloadStage — never re-downloads.

Does NOT write activation / valence / vibe_score / mood. Those are the
ML head's output and belong to ClassifyStage; the legacy worker derived
them from these features via scoring.compute_axes(), but v2 uses MERT.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, iso_now

log = logging.getLogger("vibescape.ingest.librosa")

_REPO = Path(__file__).resolve().parents[1]
for _sub in ("ingest",):
    _p = _REPO / _sub
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


class LibrosaStage(Stage):
    name = "librosa"
    status_column = "librosa_status"
    # Feeds the ML stage's row and everything downstream of it.
    arms = ("ml_status",)
    # CPU-bound (FFT-heavy) rather than GPU-bound, and librosa releases
    # the GIL inside numpy/scipy, so a few workers genuinely help. Kept
    # modest to leave headroom for the GPU stages in the same pass.
    max_workers = 3

    def __init__(self):
        try:
            import librosa  # noqa: F401
        except ImportError as e:
            raise SystemExit(f"LibrosaStage requires librosa: {e}")

    def fetch_pending(self, conn, limit: int) -> list:
        rows = conn.execute(
            "SELECT id, spotify_id, audio_path FROM tracks "
            "WHERE librosa_status = 'pending' "
            "AND download_status = 'done' "
            "AND audio_path IS NOT NULL AND audio_path != '' "
            "ORDER BY id ASC LIMIT ?",
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
        try:
            import features  # ingest/features.py
            f = features.extract(str(cached))
        except Exception as e:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields={"ingestion_attempted_at": iso_now()},
                error=f"librosa extract failed: {e}",
            )

        # Column names match the legacy writer in ingest/ingest.py so
        # legacy-ingested and v2-ingested rows stay directly comparable.
        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields={
                "tempo":              f.get("tempo"),
                "tempo_stability":    f.get("tempo_stability"),
                "onset_rate":         f.get("onset_rate"),
                "energy_mean":        f.get("energy_mean"),
                # 'energy' is the legacy alias for energy_mean; both are
                # kept populated because older queries still read it.
                "energy":             f.get("energy_mean"),
                "energy_std":         f.get("energy_std"),
                "brightness":         f.get("brightness"),
                "bandwidth":          f.get("bandwidth"),
                "rolloff":            f.get("rolloff"),
                "spectral_contrast":  f.get("spectral_contrast"),
                "flatness":           f.get("flatness"),
                "zcr":                f.get("zcr"),
                "timbre_variability": f.get("timbre_variability"),
                "valence_mode":       f.get("valence_mode"),
                "tonnetz_std":        f.get("tonnetz_std"),
                "acousticness":       f.get("acousticness"),
                "mfcc_json":          json.dumps(f.get("mfcc_mean") or []),
                "chroma_mean_json":   json.dumps(f.get("chroma_mean") or []),
                "ingestion_attempted_at": iso_now(),
            },
        )
