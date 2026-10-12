"""
Fusion stage — builds the fused vector that DJ retrieval ranks on.

Split out of EmbeddingStage, which used to encode MERT and fuse in one
pass. Two reasons:

1. ORDER. The fused vector is 20% a language one-hot. Fusing inside the
   embedding stage meant it ran BEFORE language had a tag, so every
   freshly-ingested track fused with the 'other' bucket and only got a
   correct language dim if something later rebuilt it. Fusing after
   language makes the vector right the first time.

2. COST. Fusion is pure numpy over values already in the row — no audio,
   no GPU. Bundled with MERT, correcting one language tag meant a full
   GPU re-encode of the track just to flip a one-hot
   (scripts/_llm_verify_apply.py does exactly this, by resetting
   embedding_status). Split, that rebuild is this stage alone: set
   fuse_status='pending' and the vector is rebuilt in milliseconds from
   the stored mert_embedding.

Reads mert_embedding from track_embeddings (written by EmbeddingStage),
the scalar columns from tracks, and writes back fused_embedding.
"""
from __future__ import annotations

import logging

import numpy as np

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, id_filter, iso_now, id_filter
from .fused_vector import FUSED_DIM, MERT_DIM, SCALAR_COLS, _build_fused

log = logging.getLogger("vibescape.ingest.fuse")

# Must match what ClassifyStage stamps on track_embeddings.model_version.
CURRENT_MERT_VERSION = "mert_v1_ft_768_30s"

# Qualified with the table alias because this stage joins track_embeddings.
_FETCH_COLS = ", ".join(
    ["t.id", "t.spotify_id", "t.language"] + [f"t.{c}" for c in SCALAR_COLS]
)


class FuseStage(Stage):
    name = "fuse"
    status_column = "fuse_status"
    # Last stage before the finisher.
    arms = ("youtube_status",)
    # Pure numpy on data already in hand — no audio, no GPU, no network.
    max_workers = 4

    def fetch_pending(self, conn, limit: int, only_ids=None) -> list:
        # Needs the raw MERT vector to fuse, so it joins rather than
        # trusting embedding_status alone: a row whose track_embeddings
        # write was rolled back would otherwise fail per-row every pass.
        _idf, _idp = id_filter(only_ids, "t.")
        rows = conn.execute(
            f"SELECT {_FETCH_COLS}, te.mert_embedding AS mert_blob "
            f"FROM tracks t "
            f"JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE t.fuse_status = 'pending' "
            # ClassifyStage writes mert_embedding now, so ml_status is the
            # gate. embedding_status is retired.
            f"AND t.ml_status = 'done' "
            f"AND te.mert_embedding IS NOT NULL "
            # Only fuse vectors from the CURRENT encoder. Without this,
            # fusing a leftover base-checkpoint mert_embedding would mint
            # a brand-new fused vector in the old space and quietly
            # re-poison the pool the migration is trying to clean. A row
            # whose mert vector is stale simply waits for classify to
            # re-encode it.
            f"AND te.model_version = ? "
            # Language must have a real verdict. STRICT 'done', decided
            # 2026-10-10: fusing without one is not a loss of precision,
            # it puts the track in the wrong region of the similarity
            # space (20% of this vector is the language one-hot) and
            # nothing downstream knows the vector is provisional.
            #
            # The tuple used to also admit 'whisper_done' and 'no_match'.
            # Neither has a producer any more — Whisper is gone, and the
            # queue flow's equivalent of "no language" is an explicit
            # clear, which writes language=NULL with language_status
            # ='done' and so passes this gate on its own merits. Leaving
            # them in would be dead tolerance that silently let an
            # untagged row through.
            #
            # The cost of strictness is real and accepted: a track
            # still waiting for a tag never fuses, so it never reaches the
            # DJ pool. That makes the tagging step load-bearing, not
            # housekeeping — `python scripts/language_tags.py --status`
            # is how you find out how many are waiting and for how long.
            f"AND t.language_status = 'done' "
            f"{_idf}"
            f"ORDER BY t.id ASC LIMIT ?",
            (CURRENT_MERT_VERSION, *_idp, limit),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        blob = row["mert_blob"]
        if not blob:
            return RowResult(int(row["id"]), STATUS_FAILED,
                             {"ingestion_attempted_at": iso_now()},
                             error="mert_embedding missing")
        mert_vec = np.frombuffer(blob, dtype=np.float32)
        if mert_vec.size != MERT_DIM:
            return RowResult(int(row["id"]), STATUS_FAILED,
                             {"ingestion_attempted_at": iso_now()},
                             error=f"unexpected MERT dim {mert_vec.size}")
        row_dict = {k: row[k] for k in row.keys()}
        fused = _build_fused(row_dict, mert_vec.astype(np.float32, copy=True))
        if fused.shape[0] != FUSED_DIM:
            return RowResult(int(row["id"]), STATUS_FAILED,
                             {"ingestion_attempted_at": iso_now()},
                             error=f"unexpected fused dim {fused.shape[0]}")
        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields={
                "__fused_blob__": fused.astype(np.float32, copy=False).tobytes(),
                "ingestion_attempted_at": iso_now(),
            },
        )

    def run_batch(self, conn, limit: int, log_, only_ids=None):
        """Override: the fused blob goes to track_embeddings, not tracks."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        rows = self.fetch_pending(conn, limit, only_ids)
        counts = {STATUS_DONE: 0, STATUS_FAILED: 0}
        if not rows:
            log_.info("[%s] no pending rows", self.name)
            return counts
        log_.info("[%s] processing %d rows (max_workers=%d)",
                  self.name, len(rows), self.max_workers)

        results: list[RowResult] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(self._safe_process, r): r for r in rows}
            for fut in as_completed(futs):
                results.append(fut.result())

        for res in results:
            fields = dict(res.fields or {})
            fused_blob = fields.pop("__fused_blob__", None)
            # Shared with the base run_batch: status + arming + failure.
            fields = self._finalize_fields(fields, res)
            self._commit_row_update(conn, int(res.track_id), fields, log_)
            if res.status == STATUS_DONE and fused_blob:
                conn.execute(
                    "UPDATE track_embeddings SET fused_embedding = ?, "
                    "  updated_at = ? WHERE track_id = ?",
                    (fused_blob, iso_now(), int(res.track_id)),
                )
            counts[res.status] = counts.get(res.status, 0) + 1
        conn.commit()
        log_.info("[%s] batch done: %s", self.name, counts)
        return counts
