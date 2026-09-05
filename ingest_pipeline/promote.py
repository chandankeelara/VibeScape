"""
Derive `ingestion_status` from the four per-stage status columns.

Rules (confirmed by user):
  ingestion_status = 'done'        when preview_status='done' AND ml_status='done'
  ingestion_status = 'no_preview'  when preview_status='no_match'
  ingestion_status stays 'pending' otherwise (still work in flight)

youtube_status and language_status are best-effort — never block promotion.

Idempotent: only writes rows whose ingestion_status doesn't already match
the derived value. Safe to call after every stage or at the end of a pass.
"""
from __future__ import annotations

import logging


log = logging.getLogger("vibescape.ingest.promote")


def promote(conn) -> dict[str, int]:
    """Flip rows whose derived status disagrees with ingestion_status.
    Returns a per-transition count dict for logging."""
    counts: dict[str, int] = {"->done": 0, "->no_preview": 0}

    cur = conn.execute(
        "UPDATE tracks SET ingestion_status = 'done' "
        "WHERE preview_status = 'done' AND ml_status = 'done' "
        "AND (ingestion_status IS NULL OR ingestion_status != 'done')"
    )
    counts["->done"] = cur.rowcount or 0

    cur = conn.execute(
        "UPDATE tracks SET ingestion_status = 'no_preview' "
        "WHERE preview_status = 'no_match' "
        "AND (ingestion_status IS NULL OR ingestion_status != 'no_preview')"
    )
    counts["->no_preview"] = cur.rowcount or 0

    conn.commit()
    if any(counts.values()):
        log.info("[promote] %s", counts)
    return counts
