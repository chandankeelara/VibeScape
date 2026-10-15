"""
Classification stage — vibe/mood/ML scalar scores and the MERT vector.

One MERT forward pass over the file DownloadStage cached
(ml_backend.predict_and_embed_from_path) gives both the ML-derived tracks
columns and the 768-d embedding, which goes to track_embeddings in the
same transaction as the row's UPDATE.

Always in-process: predict_and_embed_from_path loads the local checkpoint
whatever VIBESCAPE_ML_MODE says, and Modal's predict_from_url returns no
embedding, so Modal cannot serve this stage. The mode only decides the
availability warning in __init__.

Gated on download_status='done' with an audio_path, so a track with no
preview never reaches it.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, iso_now


log = logging.getLogger("vibescape.ingest.classify")


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
    # Emits the MERT vector itself now, so there is no separate encoding
    # stage to arm — fusion is next.
    #
    # It used to arm language. It no longer does: language stopped being a
    # Whisper pass over the audio and became a metadata question answered
    # off a queue, so it needs nothing from this stage and is armed at
    # ingest entry instead (stage_language.py). Fusion still waits for
    # BOTH — this arms the ml half, language_tagging.tag arms the
    # language half, and fuse's own gate requires both to be 'done'.
    # Writing 'pending' twice is harmless, which is what makes the two
    # arming paths safe in either order.
    arms = ("fuse_status",)
    # MERT weights are ~4 GB, so concurrent loads on an 8 GB card OOM.
    # Sequential. (This used to suggest raising it under Modal; the stage
    # never runs on Modal, see the module docstring.)
    max_workers = 1

    # Strict gate on DownloadStage — audio must be cached locally.
    # No URL fallback here: if download didn't succeed it never armed
    # ml_status, so this stage simply never sees the row.
    gate_sql = (
        "ml_status = 'pending' "
        "AND download_status = 'done' "
        "AND audio_path IS NOT NULL AND audio_path != ''"
    )
    fetch_cols = "id, spotify_id, title, artist, preview_url, audio_path"

    def __init__(self):
        self._ml, self._scoring = _ml_backend()
        if not self._ml.is_available():
            log.warning("ml_backend not available (mode=%s); classify stage "
                        "will mark every row failed", self._ml.current_mode())

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
        # ONE forward pass, both outputs. See ml_backend for why: the
        # head takes cat([mean_pool, max_pool]) but the encoder emits 768
        # per frame regardless, so the mean_pool half IS the embedding.
        # EmbeddingStage used to recompute it with the BASE checkpoint,
        # doubling the GPU cost per track.
        preds, mert_vec = self._ml.predict_and_embed_from_path(str(local))
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
                # Split off by row_writes below, not a tracks column.
                "__mert_blob__": (
                    mert_vec.astype("float32", copy=False).tobytes()
                    if mert_vec is not None else None
                ),
            },
        )

    def row_writes(self, res: RowResult) -> tuple[dict, list]:
        """The MERT vector goes to track_embeddings, in the same transaction
        as the UPDATE that marks the row done and arms fuse. Written apart,
        on Turso a failed vector write left a row armed for fuse whose gate
        (it joins track_embeddings) would never select it."""
        fields = dict(res.fields or {})
        mert_blob = fields.pop("__mert_blob__", None)
        if res.status != STATUS_DONE or not mert_blob:
            return fields, []
        # fused_embedding is FuseStage's and is left untouched.
        return fields, [(
            "INSERT INTO track_embeddings "
            "  (track_id, mert_embedding, model_version, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET "
            "  mert_embedding = excluded.mert_embedding, "
            "  model_version  = excluded.model_version, "
            "  updated_at     = excluded.updated_at",
            (int(res.track_id), mert_blob,
             fields.get("model_version") or "mert_v1_ft_768_30s", iso_now()),
        )]
