"""
Shared primitives for stage implementations.

A Stage:
  - is gated by exactly one status column on tracks (e.g. preview_status)
  - processes only rows where that column = 'pending'
  - writes back its own domain columns AND its own status column
  - runs its per-row work concurrently across the fetched batch
    (I/O-bound; a thread pool is plenty)

Status vocabulary (per stage):
    pending  — not yet attempted this stage
    done     — finished successfully
    no_match — stage completed but produced no result (e.g. no preview
               provider found a URL, no YouTube hit). Terminal.
    failed   — an unexpected exception. Terminal for this pass; the
               orchestrator can be re-run with --retry-failed later.

Note: no row-level locking. If two workers ever race on the same row,
the last UPDATE wins and both stage writes are idempotent for their own
columns. Cheap and correct enough for the current volume.
"""
from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


# Fields we're willing to drop and retry with when a legacy uniqueness
# constraint fires during UPDATE. These are all "nice-to-have" metadata
# fields backfilled from external providers — the row's own status +
# preview_url still lands.
_RETRY_DROPPABLE_FIELDS = ("apple_id", "track_view_url", "genre")


STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_NO_MATCH = "no_match"
STATUS_FAILED = "failed"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


@dataclass
class RowResult:
    """Per-row outcome returned by a Stage's process_row()."""
    track_id: int
    status: str                        # one of STATUS_*
    fields: dict[str, Any] | None      # column -> value updates (excluding status)
    error: str | None = None           # populated when status == STATUS_FAILED


class Stage(ABC):
    """
    Base class for all pipeline stages. Subclasses implement:
      - name (class attr): short identifier used in logs.
      - status_column (class attr): the tracks column this stage owns.
      - fetch_sql: SELECT that pulls all rows currently 'pending' for this
        stage. Returns full sqlite Row objects.
      - process_row(row): the work for one track. Runs in a worker thread.
        Return RowResult; do NOT touch the DB from here (the orchestrator
        commits results on the main thread).
      - update_sql(fields): return (sql, params_prefix) for updating one
        row given a dict of field-name -> value. The 'WHERE id = ?' + status
        column update are appended automatically. Default: builds a generic
        UPDATE from `fields`.
    """

    name: str = "stage"
    status_column: str = "status"
    max_workers: int = 8

    @abstractmethod
    def fetch_pending(self, conn, limit: int) -> list:
        """Return rows to process this pass."""

    @abstractmethod
    def process_row(self, row) -> RowResult:
        """Do the per-row work. Called from worker threads. No DB access."""

    def run_batch(self, conn, limit: int, log) -> dict[str, int]:
        """
        Fetch a batch of pending rows, dispatch process_row across a thread
        pool, commit results (one row per UPDATE), and return per-status
        counts. Called by the orchestrator once per pass.
        """
        rows = self.fetch_pending(conn, limit)
        counts = {STATUS_DONE: 0, STATUS_NO_MATCH: 0, STATUS_FAILED: 0}
        if not rows:
            log.info("[%s] no pending rows", self.name)
            return counts

        log.info("[%s] processing %d rows (max_workers=%d)",
                 self.name, len(rows), self.max_workers)

        results: list[RowResult] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            future_to_row = {ex.submit(self._safe_process, r): r for r in rows}
            for fut in as_completed(future_to_row):
                results.append(fut.result())

        # Commit sequentially on the main thread — sqlite doesn't love
        # concurrent writers, and this is fast enough.
        now = iso_now()
        for res in results:
            fields = dict(res.fields or {})
            fields[self.status_column] = res.status
            self._commit_row_update(conn, int(res.track_id), fields, log)
            counts[res.status] = counts.get(res.status, 0) + 1
        conn.commit()

        log.info("[%s] batch done: %s", self.name, counts)
        return counts

    @staticmethod
    def _commit_row_update(conn, track_id: int, fields: dict, log) -> None:
        """
        UPDATE one row with `fields`. If a legacy UNIQUE constraint fires
        (typically `UNIQUE (user_id, apple_id)` from the pre-split-tracks
        era), retry the UPDATE with the collision-prone metadata fields
        stripped out. The row's own status column always survives so the
        pipeline never loops on the same row.
        """
        def _do(fields_):
            set_clause = ", ".join(f"{k} = ?" for k in fields_.keys())
            params = list(fields_.values()) + [track_id]
            conn.execute(f"UPDATE tracks SET {set_clause} WHERE id = ?", params)

        try:
            _do(fields)
            return
        except sqlite3.IntegrityError as e:
            dropped = [k for k in _RETRY_DROPPABLE_FIELDS if k in fields]
            if not dropped:
                log.warning("row %d IntegrityError with no droppable fields to retry: %s",
                            track_id, e)
                raise
            reduced = {k: v for k, v in fields.items() if k not in dropped}
            try:
                _do(reduced)
                log.warning("row %d retried without %s due to unique-constraint collision",
                            track_id, dropped)
            except sqlite3.IntegrityError as e2:
                log.warning("row %d retry still failed after dropping %s: %s",
                            track_id, dropped, e2)
                raise

    def _safe_process(self, row) -> RowResult:
        """Wrap process_row so an exception in one row doesn't kill the batch."""
        try:
            return self.process_row(row)
        except Exception as e:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields=None,
                error=f"{e.__class__.__name__}: {e}"[:500],
            )
