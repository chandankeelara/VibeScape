"""
Genre stage -- a stopping point like language. It classifies nothing.

Genre scores (ingest_pipeline/genre_tagging.py) are read off metadata by a
Claude Code session querying the database, as language tags are. This
stage's only job is to leave every live row in one waiting state, so the
session's query is one predicate:

    genre_status = 'pending'

Production's Turso `tracks` has no column defaults (see base.py), and the
local column is added without one too, so a new row's genre_status is
NULL in both: not asked yet. The stage folds NULL into 'pending' for live
rows and writes nothing else.

Live means ingestion_status 'pending', NULL or 'done'. Unlike language,
'done' is included: when the columns arrive every analysed track in the
catalogue needs scores, and those rows are finished. A row parked at
'no_preview' keeps its NULL, since it never reaches the DJ pool and asking
about it buys nothing; one parked at '<stage>_stage_error' is normalised
once --retry-failed brings it back.

It does NOT gate anything. Fuse reads genre_scores / genre_status into the
fused_v3 genre block but does not wait for them (an untagged row gets a
neutral block), youtube and every DJ query ignore them, so an untagged
track is in the DJ pool as before. It arms nothing (`arms = ()`),
and no stage lists it in `after`, so a concurrent run never holds another
stage's row back for it. A tagging session writes 'done' through
genre_tagging.tag_many(); this stage never does.
"""
from __future__ import annotations

import logging

from . import genre_tagging as gt
from .base import RowResult, Stage, STATUS_PENDING


log = logging.getLogger("vibescape.ingest.genre")


class GenreStage(Stage):
    name = "genre"
    status_column = "genre_status"
    # Arms nothing, gates nothing -- see the module docstring.
    arms = ()
    max_workers = 1
    # As LanguageStage: in a concurrent run a pending row waits until
    # preview and download are done with it, since either can settle it
    # 'no_preview', and such a row keeps its NULL.
    after = ("preview", "download")

    gate_sql = (
        "genre_status IS NULL "
        "AND (ingestion_status IS NULL OR ingestion_status IN ('pending', 'done')) "
        "AND title IS NOT NULL AND title != ''"
    )
    gate_params = ()
    fetch_cols = "id"

    def process_row(self, row) -> RowResult:
        """Normalise to the waiting state: the base class stamps 'pending'
        onto genre_status, and since 'pending' is neither in arms_on nor
        'failed', nothing else is written."""
        return RowResult(track_id=int(row["id"]), status=STATUS_PENDING, fields={})

    def run_batch(self, conn, limit: int, log_, only_ids=None) -> dict[str, int]:
        counts = super().run_batch(conn, limit, log_, only_ids)
        # Only after a batch that normalised something: an idle look must
        # stay one ids-only query (a scan on production, billed per row).
        if counts.get(STATUS_PENDING):
            try:
                waiting = conn.execute(
                    f"SELECT COUNT(*) FROM tracks WHERE {gt.WAITING_PREDICATE}"
                ).fetchone()[0]
                log_.info("[%s] %d row(s) waiting for genre scores "
                          "(see ingest_pipeline/README.md 'Tagging genres')",
                          self.name, int(waiting))
            except Exception as e:
                log_.warning("[%s] could not count waiting rows: %s", self.name, e)
        return counts
