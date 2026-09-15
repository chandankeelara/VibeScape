"""
Classification stage — vibe/mood/ML scalar scores.

Streams the preview URL through ml_backend.predict_from_url (which does
its own in-memory download/decode → MERT + scalar prediction) and writes
back all the ML-derived columns. No local audio file is created.

Blocks on preview_status='done' (needs a URL). Skips rows where the
preview stage marked 'no_match' — those go to ingestion_status='no_preview'
via promote.py without ever running ML.

Does NOT write embeddings — that's EmbeddingStage's job (separate GPU
pass using the raw MERT-v1-95M encoder rather than the trained head).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, STATUS_NO_MATCH, iso_now


log = logging.getLogger("vibescape.ingest.classify")


_FETCH_COLS = (
    "id, spotify_id, title, artist, preview_url, audio_path"
)


def _ml_backend():
    """Lazy import — heavy deps (torch, transformers) only load when the
    stage actually runs."""
    _root = Path(__file__).resolve().parents[1]
    for _sub in ("backend", "ingest"):
        p = _root / _sub
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import ml_backend  # type: ignore
    import scoring  # type: ignore
    return ml_backend, scoring


class ClassifyStage(Stage):
    name = "classify"
    status_column = "ml_status"
    # Local GPU mode: MERT weights are ~4 GB, so concurrent loads on an
    # 8 GB card OOM. Sequentialize by default. If running against Modal
    # (VIBESCAPE_ML_MODE=modal), bump this back up (Modal runs each call
    # on its own container, no local memory pressure).
    max_workers = 1

    def __init__(self):
        self._ml, self._scoring = _ml_backend()
        if not self._ml.is_available():
            log.warning("ml_backend not available (mode=%s); classify stage "
                        "will mark every row failed", self._ml.current_mode())

    def fetch_pending(self, conn, limit: int) -> list:
        # Strict gate on DownloadStage — audio must be cached locally.
        # No URL fallback here; if download failed, promote.py cascades
        # this row to 'no_match' so it doesn't sit pending forever.
        rows = conn.execute(
            f"SELECT {_FETCH_COLS} FROM tracks "
            f"WHERE ml_status = 'pending' "
            f"AND download_status = 'done' "
            f"AND audio_path IS NOT NULL AND audio_path != '' "
            f"ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        from .stage_download import resolve_audio_path
        local = resolve_audio_path(row["audio_path"])
        if local is None:
            # audio_path pointed at a missing file — DownloadStage's next
            # pass will re-fetch. Leave this row pending; don't error.
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields={"ingestion_error": "cached audio missing on disk",
                        "ingestion_attempted_at": iso_now()},
                error="cached audio missing",
            )
        preds = self._ml.predict_from_path(str(local))
        if not preds:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields={"ingestion_error": "ml_backend returned nothing",
                        "ingestion_attempted_at": iso_now()},
                error="ml_backend returned nothing",
            )

        energy = float(preds.get("energy", 0.0))
        dance = float(preds.get("danceability", 0.0))
        valence = float(preds.get("valence", 0.0))
        vibe_ml = float(preds.get("vibe_score", 0.55 * energy + 0.45 * dance))
        model_version = str(preds.get("model_version") or "mert_v1")
        activation = (0.55 * energy + 0.45 * dance) * 100.0
        valence_pct = valence * 100.0
        mood = self._scoring.mood_label(activation, valence_pct)

        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields={
                "activation":         activation,
                "valence":            valence_pct,
                "activation_relative": activation,
                "vibe_score":         activation,   # legacy display column
                "mood":               mood,
                "energy_pred":        energy,
                "danceability_pred":  dance,
                "valence_pred":       valence,
                "vibe_score_ml":      vibe_ml,
                "model_version":      model_version,
                "classification_source": "ml_mert",
                "ml_predicted_at":    iso_now(),
                "ingestion_attempted_at": iso_now(),
                "ingestion_error":    None,
            },
        )
