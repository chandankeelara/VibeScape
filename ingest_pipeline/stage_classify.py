"""
Classification stage — MERT embedding + vibe/mood/ML scores.

Streams the preview URL through ml_backend.predict_from_url (which does
its own in-memory download/decode → MERT + scalar prediction) and writes
back all the ML-derived columns. No local audio file is created.

Blocks on preview_status='done' (needs a URL). Skips rows where the
preview stage marked 'no_match' — those go to ingestion_status='no_preview'
via promote.py without ever running ML.

Also writes MERT embeddings into track_embeddings (both raw MERT and
fused variants) so DJ mode has vectors to search against.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, STATUS_NO_MATCH, iso_now


log = logging.getLogger("vibescape.ingest.classify")


_FETCH_COLS = (
    "id, spotify_id, title, artist, preview_url"
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
        # Only rows where preview stage has already succeeded.
        rows = conn.execute(
            f"SELECT {_FETCH_COLS} FROM tracks "
            f"WHERE ml_status = 'pending' "
            f"AND preview_status = 'done' "
            f"AND preview_url IS NOT NULL AND preview_url != '' "
            f"ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        url = row["preview_url"]
        preds = self._ml.predict_from_url(url)
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

        fields = {
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
        }
        # If ml_backend also returned MERT/fused embeddings inline, stash
        # them so the orchestrator can persist to track_embeddings. We
        # smuggle them via a dunder field the stage's commit step strips.
        embeddings = preds.get("embeddings") if isinstance(preds, dict) else None
        if embeddings:
            fields["__embeddings__"] = embeddings

        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields=fields,
        )

    def run_batch(self, conn, limit: int, log_):  # override to persist embeddings
        # Rely on the base to do the fetch + dispatch + main-thread commits.
        # We augment: after the base commits fields, sink any embeddings.
        # Simplest approach — call super().run_batch, which strips __embeddings__
        # only if we handle it beforehand. Handle inline instead.
        rows = self.fetch_pending(conn, limit)
        counts = {STATUS_DONE: 0, STATUS_NO_MATCH: 0, STATUS_FAILED: 0}
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

        for res in results:
            fields = dict(res.fields or {})
            embeddings = fields.pop("__embeddings__", None)
            fields[self.status_column] = res.status
            set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
            params = list(fields.values()) + [int(res.track_id)]
            conn.execute(f"UPDATE tracks SET {set_clause} WHERE id = ?", params)
            if embeddings and res.status == STATUS_DONE:
                self._persist_embeddings(conn, int(res.track_id), embeddings)
            counts[res.status] = counts.get(res.status, 0) + 1
        conn.commit()
        log_.info("[%s] batch done: %s", self.name, counts)
        return counts

    @staticmethod
    def _persist_embeddings(conn, track_id: int, embeddings: dict):
        """embeddings shape: {'mert_v1_95m_fp32_30s': [floats...],
                              'fused_v1_mert_scalar_lang': [floats...]}"""
        for model_version, vec in embeddings.items():
            if not vec:
                continue
            blob = json.dumps(list(vec))
            conn.execute(
                "INSERT OR REPLACE INTO track_embeddings "
                "(track_id, model_version, dim, embedding_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (track_id, model_version, len(vec), blob, iso_now()),
            )
