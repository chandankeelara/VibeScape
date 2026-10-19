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

Reads mert_embedding from track_embeddings (written by ClassifyStage),
the scalar columns from tracks, and writes back FUSED_COLUMN (fused_v2,
841 wide). It never touches fused_v1's 'fused_embedding', so a checkout from
before fused_v2 that still runs this stage writes only that old column: the
new one stays NULL for that row (out of the DJ pool, never wrong) until
scripts/refuse_vectors.py, re-run, fills it.
"""
from __future__ import annotations

import logging

import numpy as np

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, iso_now
from .fused_vector import FUSED_COLUMN, FUSED_DIM, MERT_DIM, SCALAR_COLS, _build_fused

log = logging.getLogger("vibescape.ingest.fuse")

# Must match what ClassifyStage stamps on track_embeddings.model_version.
CURRENT_MERT_VERSION = "mert_v1_ft_768_30s"


class FuseStage(Stage):
    name = "fuse"
    status_column = "fuse_status"
    # Last stage before the finisher.
    arms = ("youtube_status",)
    # Pure numpy on data already in hand — no audio, no GPU, no network.
    max_workers = 4

    # Needs the raw MERT vector to fuse, so the gate checks the vector
    # itself rather than trusting embedding_status alone: a row whose
    # track_embeddings write was rolled back would otherwise fail per-row
    # every pass. Through EXISTS, so the runner can OR this gate with the
    # others in one query over `tracks`; fetch_pending JOINs the same row
    # to read the blob.
    gate_sql = (
        "t.fuse_status = 'pending' "
        # ClassifyStage writes mert_embedding now, so ml_status is the
        # gate. embedding_status is retired.
        "AND t.ml_status = 'done' "
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
        "AND t.language_status = 'done' "
        "AND EXISTS (SELECT 1 FROM track_embeddings e "
        "            WHERE e.track_id = t.id AND e.mert_embedding IS NOT NULL "
        # Only fuse vectors from the CURRENT encoder. Without this,
        # fusing a leftover base-checkpoint mert_embedding would mint
        # a brand-new fused vector in the old space and quietly
        # re-poison the pool the migration is trying to clean. A row
        # whose mert vector is stale simply waits for classify to
        # re-encode it.
        "            AND e.model_version = ?)"
    )
    gate_params = (CURRENT_MERT_VERSION,)
    # Qualified with the table alias because of the join.
    fetch_cols = ", ".join(["t.id", "t.spotify_id", "t.language"]
                           + [f"t.{c}" for c in SCALAR_COLS]
                           + ["te.mert_embedding AS mert_blob"])
    fetch_join = "JOIN track_embeddings te ON te.track_id = t.id "

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

    def row_writes(self, res: RowResult) -> tuple[dict, list]:
        """The fused vector goes to track_embeddings, in the same
        transaction as the UPDATE that marks the row done.

        An upsert, like ClassifyStage's. It was a plain UPDATE, which
        matches nothing when the track_embeddings row is gone (a reset
        deletes it; fetch_pending's JOIN only proves it existed when the
        batch was read) while fuse_status still went to 'done': the track
        would finish with no vector and silently leave the DJ pool.
        Failing the row instead was ruled out: in that race the reset has
        just requeued the track, and parking it at fuse_stage_error would
        override that.
        """
        fields = dict(res.fields or {})
        fused_blob = fields.pop("__fused_blob__", None)
        if res.status != STATUS_DONE or not fused_blob:
            return fields, []
        return fields, [(
            f"INSERT INTO track_embeddings (track_id, {FUSED_COLUMN}, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET "
            f"  {FUSED_COLUMN} = excluded.{FUSED_COLUMN}, "
            "  updated_at      = excluded.updated_at",
            (int(res.track_id), fused_blob, iso_now()),
        )]
