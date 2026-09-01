"""
Offline ingest worker.

The online sync path (/api/ingest/*) inserts metadata-only rows into
`tracks` with `ingestion_status='pending'`. This script drains that queue
by running the full pipeline — preview URL cascade (Spotify → iTunes →
Deezer ISRC → Deezer term-search), ML scoring (MERT via Modal or local
GPU), Whisper language detection, or librosa fallback — and flipping
`ingestion_status` to `'done'` / `'no_preview'` / `'failed'`.

No HTTP surface. Runs against the same DB the backend uses (sqlite or
Turso via DB_BACKEND=turso).

Usage:
    # Drain one batch of 50 pending rows and exit.
    python scripts/run_ingest_worker.py --batch 50

    # Loop forever, 30 s between empty polls.
    python scripts/run_ingest_worker.py --loop --interval 30

    # Also retry rows the worker previously marked failed / no_preview.
    python scripts/run_ingest_worker.py --retry-failed
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "ingest"))
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env so DB_BACKEND / TURSO_* / VIBESCAPE_ML_MODE are picked up when the
# worker runs standalone (backend/app.py does the same at import time).
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from db import ensure_db, get_conn  # noqa: E402
from app import _ingest_track_row, _recompute_axes_and_zscores  # noqa: E402

log = logging.getLogger("vibescape.worker")


PENDING_SELECT_COLS = (
    "id, spotify_id, apple_id, isrc, title, artist, album, artwork_url, "
    "preview_url, duration_ms, audio_path, ingestion_status"
)


def _fetch_pending(conn, batch: int, retry_failed: bool) -> list:
    if retry_failed:
        statuses = ("pending", "failed", "no_preview")
    else:
        statuses = ("pending",)
    placeholders = ",".join("?" for _ in statuses)
    sql = (
        f"SELECT {PENDING_SELECT_COLS} FROM tracks "
        f"WHERE ingestion_status IN ({placeholders}) "
        f"ORDER BY id ASC LIMIT ?"
    )
    rows = conn.execute(sql, (*statuses, batch)).fetchall()
    return list(rows)


def _pending_count(conn, retry_failed: bool) -> int:
    if retry_failed:
        row = conn.execute(
            "SELECT COUNT(*) FROM tracks "
            "WHERE ingestion_status IN ('pending', 'failed', 'no_preview')"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE ingestion_status = 'pending'"
        ).fetchone()
    return int(row[0] if row else 0)


def run_once(batch: int, retry_failed: bool) -> tuple[int, dict[str, int]]:
    """Drain up to `batch` rows in a single pass. Returns (processed, counts)."""
    conn = get_conn()
    processed = 0
    counts: dict[str, int] = {"done": 0, "no_preview": 0, "failed": 0}
    try:
        rows = _fetch_pending(conn, batch, retry_failed)
        if not rows:
            return 0, counts
        log.info("draining %d pending rows", len(rows))
        for row in rows:
            spotify_id = row["spotify_id"]
            title = row["title"]
            artist = row["artist"]
            log.info("-> [%s] %r - %r", spotify_id, title, artist)
            status = _ingest_track_row(conn, row)
            counts[status] = counts.get(status, 0) + 1
            processed += 1
        # Refresh z-scores once per batch (activation_relative + vibe_score).
        try:
            _recompute_axes_and_zscores(conn)
        except Exception as e:
            log.warning("post-batch recompute failed: %s", e)
    finally:
        conn.close()
    return processed, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=50,
                        help="max rows per pass (default 50)")
    parser.add_argument("--loop", action="store_true",
                        help="keep running, sleeping --interval between empty polls")
    parser.add_argument("--interval", type=int, default=30,
                        help="seconds between polls when --loop and queue empty (default 30)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="also pick up rows previously marked 'failed' or 'no_preview'")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ensure_db is a no-op on Turso and idempotent on sqlite.
    ensure_db()

    conn = get_conn()
    try:
        remaining = _pending_count(conn, args.retry_failed)
    finally:
        conn.close()
    log.info("startup: %d rows waiting (retry_failed=%s)", remaining, args.retry_failed)

    if not args.loop:
        processed, counts = run_once(args.batch, args.retry_failed)
        log.info("done: processed=%d counts=%s", processed, counts)
        return 0

    while True:
        processed, counts = run_once(args.batch, args.retry_failed)
        if processed == 0:
            log.info("queue empty; sleeping %ds", args.interval)
            time.sleep(args.interval)
        else:
            log.info("batch done: processed=%d counts=%s", processed, counts)
    # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
