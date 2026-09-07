"""
Whisper language-detection stage.

Streams the preview URL through ml_backend.predict_language_from_url and
records the top-1 language + confidence + top-3 distribution. Blocks on
preview_status='done' (needs a URL) but is otherwise independent — a
'failed' here does not block ingestion_status='done'.

Confidence gate: below 0.20 we treat the prediction as no_match (not
enough signal to trust). Above the gate, we persist all three top guesses
so the frontend can degrade gracefully.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, STATUS_NO_MATCH, iso_now


log = logging.getLogger("vibescape.ingest.language")


_MIN_TOP1_CONFIDENCE = 0.20


def _ml_backend():
    _root = Path(__file__).resolve().parents[1]
    for _sub in ("backend", "ingest"):
        p = _root / _sub
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import ml_backend  # type: ignore
    return ml_backend


class LanguageStage(Stage):
    name = "language"
    status_column = "language_status"
    # Whisper on local GPU: same story as ClassifyStage — share the card,
    # no concurrent model loads. Modal mode can bump this back up.
    max_workers = 1

    def __init__(self, model_size: str = "small"):
        self._ml = _ml_backend()
        self._model_size = model_size

    def fetch_pending(self, conn, limit: int) -> list:
        # Strict gate on DownloadStage — no URL fallback.
        rows = conn.execute(
            "SELECT id, spotify_id, title, artist, audio_path "
            "FROM tracks "
            "WHERE language_status = 'pending' "
            "AND download_status = 'done' "
            "AND audio_path IS NOT NULL AND audio_path != '' "
            "ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        from .stage_download import resolve_audio_path
        local = resolve_audio_path(row["audio_path"])
        if local is None:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields={"ingestion_attempted_at": iso_now()},
                error="cached audio missing",
            )
        preds = self._ml.predict_language_from_path(str(local), model_size=self._model_size)
        if not preds:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields={"ingestion_attempted_at": iso_now()},
                error="whisper returned nothing",
            )
        top1_prob = float(preds.get("top1_prob", 0.0))
        now = iso_now()
        if top1_prob < _MIN_TOP1_CONFIDENCE:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_NO_MATCH,
                fields={
                    "language_predicted_at": now,
                    "ingestion_attempted_at": now,
                },
            )
        top3_json = json.dumps({
            "top1": [preds.get("top1_lang"), top1_prob],
            "top2": [preds.get("top2_lang"), float(preds.get("top2_prob", 0.0))],
            "top3": [preds.get("top3_lang"), float(preds.get("top3_prob", 0.0))],
        })
        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields={
                "language":               preds.get("top1_lang"),
                "language_confidence":    top1_prob,
                "language_top3_json":     top3_json,
                "language_model_version": str(preds.get("model_version") or f"whisper_{self._model_size}"),
                "language_predicted_at":  now,
                "ingestion_attempted_at": now,
            },
        )
