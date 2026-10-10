"""
Shared primitives for stage implementations.

A Stage:
  - is gated by exactly one status column on tracks (e.g. preview_status)
  - processes only rows where that column = 'pending'
  - writes back its own domain columns AND its own status column
  - on success, ARMS the next stage(s) by setting their status columns
    to 'pending' (see `arms`)
  - runs its per-row work concurrently across the fetched batch
    (I/O-bound; a thread pool is plenty)

Arming, and why it exists
-------------------------
A stage used to become eligible purely because its status column was
already 'pending' — which was never written by anything at runtime. It
came only from the column DEFAULT in backend/db.py's ALTER TABLE
migration. That holds in local sqlite and does NOT hold in Turso, whose
tracks table was rebuilt by scripts/_push_local_to_turso.py from
PRAGMA table_info's `type` field alone, silently dropping every
DEFAULT 'pending'. Result: app-inserted rows land NULL in all six stage
columns, `= 'pending'` matches nothing, and the whole pipeline idles on
a full backlog while looking perfectly healthy.

So stages now arm each other explicitly. Entry stages (preview,
youtube) trigger off ingestion_status='pending' — the one column the
app writes literally — and every later stage is armed by the stage
before it. Nothing depends on a schema default any more.

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
# Written to ingestion_status (NOT to a stage column) when a stage
# returns 'failed'. Named after the stage that broke — 'preview_stage_error',
# 'classify_stage_error', … — so a single GROUP BY on ingestion_status
# tells you which stage is failing and how often, with no log parsing.
#
# It also parks the row: preview's entry gate only matches
# ingestion_status='pending', so a broken row stops re-entering the
# pipeline every pass. The stage's own column keeps 'failed', so
# --retry-failed still knows where to resume.
def stage_error_status(stage_name: str) -> str:
    return f"{stage_name}_stage_error"
# RETIRED 2026-10-10 with the Whisper language stage. It meant "Whisper
# produced a tag but nothing has verified it", and nothing writes it any
# more. LanguageStage normalises leftover rows in this state back to
# 'pending' so a tagging session can find them with one predicate; see
# ingest_pipeline/language_tagging.py.
STATUS_WHISPER_DONE = "whisper_done"


def id_filter(only_ids, alias: str = "") -> tuple[str, list]:
    """
    Build an "AND <alias>id IN (?,?,…)" fragment restricting a stage to a
    cohort of track ids, plus its params.

    This is what makes a pass carry the SAME tracks through every stage.
    Without it each stage independently selects its own pending rows, so
    a pass could classify one set of tracks and fuse a completely
    different set — which is exactly what happened once fuse_status was
    'pending' library-wide after a migration.

    `alias` is the table prefix the stage's SQL uses ("t." for the stages
    that join, "" for the ones that don't).
    """
    if not only_ids:
        return "", []
    marks = ",".join("?" for _ in only_ids)
    return f"AND {alias}id IN ({marks}) ", [int(x) for x in only_ids]


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

    # Status columns to set to 'pending' when THIS stage returns 'done',
    # making the next stage eligible. Only on 'done': a 'no_match' or
    # 'failed' row must not arm anything downstream. Nothing cascades a
    # failure forward any more — an unarmed stage simply never runs.
    arms: tuple[str, ...] = ()

    # Which of this stage's statuses count as "succeeded" for arming.
    # Defaults to 'done' and no stage overrides it any more. LanguageStage
    # used to, because Whisper's success was spelled 'whisper_done'; it
    # now arms nothing at all — the arming moves to the moment a tagging
    # session writes the tag (language_tagging.tag), which is the only
    # point at which fuse could act on it.
    arms_on: tuple[str, ...] = (STATUS_DONE,)

    # Stage status -> the ingestion_status it settles the whole track on.
    # Used where a stage's outcome is terminal for the track, not just for
    # itself: preview/download finding nothing means the track can never
    # proceed ('no_preview'), and youtube finishing means it is fully
    # ingested ('done').
    #
    # This replaces promote(). That pass existed to cascade dead rows
    # into 'no_match' so they would stop being retried — which was only
    # necessary because every status column started at 'pending' from a
    # schema default. Arming removed that, leaving promote with nothing
    # to do but write the terminal ingestion_status, which the stage that
    # actually knows the outcome can do itself, in the same UPDATE.
    finalizes: dict[str, str] = {}

    @abstractmethod
    def fetch_pending(self, conn, limit: int, only_ids=None) -> list:
        """Return rows to process this pass.

        `only_ids` restricts the stage to a cohort so a pass advances the
        same tracks through every stage. See id_filter().
        """

    @abstractmethod
    def process_row(self, row) -> RowResult:
        """Do the per-row work. Called from worker threads. No DB access."""

    def run_batch(self, conn, limit: int, log, only_ids=None) -> dict[str, int]:
        """
        Fetch a batch of pending rows, dispatch process_row across a thread
        pool, commit results (one row per UPDATE), and return per-status
        counts. Called by the orchestrator once per pass.
        """
        rows = self.fetch_pending(conn, limit, only_ids)
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
            fields = self._finalize_fields(dict(res.fields or {}), res)
            self._commit_row_update(conn, int(res.track_id), fields, log)
            counts[res.status] = counts.get(res.status, 0) + 1
        conn.commit()

        log.info("[%s] batch done: %s", self.name, counts)
        return counts

    def _finalize_fields(self, fields: dict, res: "RowResult") -> dict:
        """
        Stamp a result's own status column, arm the next stage(s), and
        record a failure on the row.

        Shared by run_batch and by EmbeddingStage's override — which has
        its own commit loop for the embedding blobs, and silently skipped
        arming until this was factored out. Any future override must call
        this rather than re-implementing it.
        """
        fields[self.status_column] = res.status
        if res.status in self.arms_on:
            # Arm the next stage(s) in the same UPDATE, so a row can never
            # be left finished here but un-triggered downstream.
            for col in self.arms:
                fields[col] = STATUS_PENDING
        elif res.status == STATUS_FAILED:
            # Surface the failure on the row, not only in the log, and
            # stop it re-entering at preview.
            fields["ingestion_status"] = stage_error_status(self.name)
            if res.error:
                fields["ingestion_error"] = f"[{self.name}] {res.error}"[:500]
        # A terminal verdict for the whole track, if this stage has one.
        # Not elif: a 'done' that both arms the next stage and settles the
        # track (youtube) must do both.
        settled = self.finalizes.get(res.status)
        if settled and res.status != STATUS_FAILED:
            fields["ingestion_status"] = settled
        return fields

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
