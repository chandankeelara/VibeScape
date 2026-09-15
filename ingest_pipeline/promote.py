"""
Derive `ingestion_status` from the per-stage status columns AND cascade
audio-availability failures to downstream stages.

Rules (confirmed by user):
  ingestion_status = 'done'        when preview + download + ml all 'done'
  ingestion_status = 'no_preview'  when preview_status='no_match'
                                   OR download_status='no_match'
                                   (both mean "we couldn't get audio")

Cascade: if download_status='no_match', propagate 'no_match' to every
downstream stage that requires cached audio (ml, language, embedding)
so pending counts stay meaningful — these rows will never advance until
DownloadStage picks up a working URL.

youtube_status is independent of audio and never cascades.

Idempotent: only writes rows whose derived status disagrees with what's
already there. Safe to call after every stage or at end of pass.
"""
from __future__ import annotations

import logging


log = logging.getLogger("vibescape.ingest.promote")


def promote(conn) -> dict[str, int]:
    counts: dict[str, int] = {
        "->done": 0, "->no_preview": 0,
        "cascade_download": 0, "cascade_ml": 0,
        "cascade_language": 0, "cascade_embedding": 0,
    }

    # Cascade preview failures to download first — if we never resolved
    # a URL, there's nothing to fetch, so download can't ever advance.
    cur = conn.execute(
        "UPDATE tracks SET download_status = 'no_match' "
        "WHERE preview_status = 'no_match' AND download_status = 'pending'"
    )
    counts["cascade_download"] = cur.rowcount or 0

    # Cascade download failures (either intrinsic or cascaded-from-preview
    # above) to every stage that needs the cached audio.
    for col in ("ml_status", "language_status", "embedding_status"):
        cur = conn.execute(
            f"UPDATE tracks SET {col} = 'no_match' "
            f"WHERE download_status = 'no_match' AND {col} = 'pending'"
        )
        counts[f"cascade_{col.split('_')[0]}"] = cur.rowcount or 0

    # ingestion_status='done' — full happy path
    cur = conn.execute(
        "UPDATE tracks SET ingestion_status = 'done' "
        "WHERE preview_status = 'done' "
        "AND download_status = 'done' "
        "AND ml_status = 'done' "
        "AND (ingestion_status IS NULL OR ingestion_status != 'done')"
    )
    counts["->done"] = cur.rowcount or 0

    # ingestion_status='no_preview' — either provider chain gave up
    # (preview no_match) OR the URL couldn't be fetched (download no_match)
    cur = conn.execute(
        "UPDATE tracks SET ingestion_status = 'no_preview' "
        "WHERE (preview_status = 'no_match' OR download_status = 'no_match') "
        "AND (ingestion_status IS NULL OR ingestion_status != 'no_preview')"
    )
    counts["->no_preview"] = cur.rowcount or 0

    conn.commit()
    if any(counts.values()):
        log.info("[promote] %s", {k: v for k, v in counts.items() if v})
    return counts
