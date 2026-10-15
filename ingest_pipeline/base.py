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

Writing a batch
---------------
A stage's results are written in ONE transaction, conn.execute_batch(),
which on Turso is also one HTTPS request for the whole batch rather than
one per statement. A row's own statements (its tracks UPDATE and, for
classify and fuse, its track_embeddings write) always land together, and
rows go in id order.

When that transaction fails nothing in it landed, and what happens next
depends on what failed (Stage._write_results):

  - An error that says nothing about the rows is raised: the network or
    the HTTP layer (anything that is not a sqlite3.Error), a busy or
    locked database, or a failure no statement owns. Nothing is parked,
    the rows stay pending, and the next pass tries them again.
  - Otherwise the error names its statement (execute_batch sets `step`),
    and so one row. That row is set aside and the batch is written again
    without it. If the retry fails at its own first statement with the
    same error, the failure is not about any one row (a column production
    lacks, a read-only token): it is raised, the stage stops, and nothing
    is parked. A constraint violation is never read that way, since two
    neighbouring rows can share one (two legacy apple_id collisions).
  - Once the rest has landed, each row set aside is settled alone: a
    UNIQUE collision is retried without _RETRY_DROPPABLE_FIELDS, and a row
    that still cannot be written is marked 'failed' with its
    '<stage>_stage_error' and the error text, like an exception in
    process_row.

Until 2026-10-15 one failing row aborted the stage's write loop after the
batch's work was done: SQLite rolled the whole batch back (the next pass
re-ran the same MERT passes and failed on the same row) and Turso kept a
partial batch. The first version of this file then parked a row on ANY
error during a row-by-row rewrite, so a timeout parked rows whose work was
fine and a schema gap parked every row of every batch.

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
from typing import Any, NamedTuple


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


def update_stmt(track_id: int, fields: dict) -> tuple[str, list]:
    """(sql, params) for `UPDATE tracks SET <fields> WHERE id = track_id`."""
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    return f"UPDATE tracks SET {set_clause} WHERE id = ?", [*fields.values(), int(track_id)]


def _is_transient(e: BaseException) -> bool:
    """A write error that says nothing about the rows written: the network
    or the HTTP layer (anything that is not a sqlite3.Error), or a busy or
    locked database. Trying again later is the fix, so nothing is parked."""
    if not isinstance(e, sqlite3.Error):
        return True
    text = (getattr(e, "detail", None) or str(e)).lower()
    return isinstance(e, sqlite3.OperationalError) and any(
        word in text for word in ("locked", "busy", "timeout"))


def _same_failure(a: BaseException, b: BaseException) -> bool:
    """The same error wherever it happened: its type and its text without
    the position (db_client puts that in `detail` for Turso errors)."""
    def key(e):
        return type(e), getattr(e, "detail", None) or str(e)
    return key(a) == key(b)


@dataclass
class RowResult:
    """Per-row outcome returned by a Stage's process_row()."""
    track_id: int
    status: str                        # one of STATUS_*
    fields: dict[str, Any] | None      # column -> value updates (excluding status)
    error: str | None = None           # populated when status == STATUS_FAILED


class RowWrite(NamedTuple):
    """Everything one result writes: its tracks columns, plus statements
    that must land in the same transaction (see Stage.row_writes)."""
    track_id: int
    fields: dict
    extra: list


class Stage(ABC):
    """
    Base class for all pipeline stages. Subclasses implement:
      - name (class attr): short identifier used in logs.
      - status_column (class attr): the tracks column this stage owns.
      - fetch_pending(conn, limit, only_ids): the rows this stage can act
        on right now. Its WHERE clause is the stage's whole gate, and the
        orchestrator builds each pass's cohort from it.
      - process_row(row): the work for one track. Runs in a worker thread.
        Return RowResult; do NOT touch the DB from here (run_batch writes
        results on the main thread).
      - row_writes(res), optionally: statements that must land with the
        row's tracks UPDATE (classify and fuse write track_embeddings).
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

    def row_writes(self, res: RowResult) -> tuple[dict, list]:
        """Split a result into (tracks columns, extra statements).

        The extra (sql, params) run after the row's tracks UPDATE, in the
        same transaction, so a row can never be marked done here without
        them. ClassifyStage and FuseStage put their vectors in
        track_embeddings this way; the other stages have none.
        """
        return dict(res.fields or {}), []

    def run_batch(self, conn, limit: int, log, only_ids=None) -> dict[str, int]:
        """
        Fetch a batch of pending rows, dispatch process_row across a thread
        pool, write the results (see "Writing a batch" in the module
        docstring), and return per-status counts. A row whose write failed
        counts as 'failed'. Called by the orchestrator once per pass.
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
        # as_completed order is arbitrary; write in id order.
        results.sort(key=lambda r: int(r.track_id))

        # Written on the main thread, after every row is processed.
        writes = []
        for res in results:
            fields, extra = self.row_writes(res)
            writes.append(RowWrite(int(res.track_id),
                                   self._finalize_fields(fields, res), extra))
        unwritten = self._write_results(conn, writes, log)
        for res in results:
            status = STATUS_FAILED if int(res.track_id) in unwritten else res.status
            counts[status] = counts.get(status, 0) + 1

        log.info("[%s] batch done: %s", self.name, counts)
        return counts

    def _write_results(self, conn, writes: list[RowWrite], log) -> set[int]:
        """Write the batch by the rules in "Writing a batch" (module
        docstring). Returns the track ids that could not be written; those
        rows are parked at <stage>_stage_error."""
        batch = list(writes)
        set_aside: list[tuple[RowWrite, Exception]] = []
        while batch:
            stmts, owner = [], []
            for i, w in enumerate(batch):
                for stmt in self._statements(w):
                    stmts.append(stmt)
                    owner.append(i)
            try:
                conn.execute_batch(stmts)
                break
            except Exception as e:
                step = getattr(e, "step", None)
                if _is_transient(e) or step is None:
                    raise
                if (set_aside and step == 0
                        and not isinstance(e, sqlite3.IntegrityError)
                        and _same_failure(e, set_aside[-1][1])):
                    log.error("[%s] the batch fails the same way without row %d (%s): "
                              "not a problem with one row, stopping with nothing parked",
                              self.name, set_aside[-1][0].track_id, e)
                    raise
                bad = batch.pop(owner[step])
                log.warning("[%s] row %d broke the batch write (%s); writing the "
                            "other %d without it", self.name, bad.track_id, e, len(batch))
                set_aside.append((bad, e))
        return {w.track_id for w, e in set_aside if not self._settle(conn, w, e, log)}

    @staticmethod
    def _statements(w: RowWrite) -> list:
        return [update_stmt(w.track_id, w.fields), *w.extra]

    def _settle(self, conn, w: RowWrite, error: Exception, log) -> bool:
        """Deal with a row the batch could not write, alone. True if it
        landed after all.

        A UNIQUE collision (typically the legacy `UNIQUE (user_id,
        apple_id)` from the pre-split-tracks era) is retried without the
        collision-prone metadata fields, so the row's own status still
        lands and the pipeline does not loop on it. db_client has raised
        IntegrityError for Turso's SQLITE_CONSTRAINT since 2026-10-15, so
        this should hold in production too; unverified there as of
        2026-10-15, since nothing in this pipeline has run against it.

        Otherwise the row is parked: 'failed', '<stage>_stage_error' and the
        error text. If even that write fails, its error propagates: the
        database is refusing a status write and the stage should stop.
        """
        if isinstance(error, sqlite3.IntegrityError):
            dropped = [k for k in _RETRY_DROPPABLE_FIELDS if k in w.fields]
            if dropped:
                reduced = {k: v for k, v in w.fields.items() if k not in dropped}
                try:
                    conn.execute_batch(self._statements(w._replace(fields=reduced)))
                    log.warning("row %d retried without %s due to unique-constraint "
                                "collision", w.track_id, dropped)
                    return True
                except Exception as e:
                    if _is_transient(e):
                        raise
                    error = e
        log.warning("[%s] row %d could not be written, marking it failed: %s",
                    self.name, w.track_id, error)
        conn.execute_batch([update_stmt(w.track_id, {
            self.status_column: STATUS_FAILED,
            "ingestion_status": stage_error_status(self.name),
            "ingestion_error": f"[{self.name}] write failed: {error}"[:500],
        })])
        return False

    def _finalize_fields(self, fields: dict, res: "RowResult") -> dict:
        """
        Stamp a result's own status column, arm the next stage(s), and
        record a failure on the row.

        Called by run_batch for every result, including ClassifyStage's
        and FuseStage's. Those used to override run_batch with their own
        commit loops, and the embedding stage they replaced silently
        skipped arming until this was factored out.
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
