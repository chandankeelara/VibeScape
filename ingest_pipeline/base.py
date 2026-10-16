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
    failed   — an unexpected exception, or a write the stage could not
               make. Parks the row (with
               ingestion_status='<stage>_stage_error') until
               `run_ingest_v2.py --retry-failed <stage>|all` re-arms it.
               Never written to a finished track: see "Writing a batch".

Writing a batch
---------------
A stage's results are written in ONE transaction, conn.execute_batch(),
which on Turso is also one HTTPS request for the whole batch rather than
one per statement. A row's own statements (its tracks UPDATE and, for
classify and fuse, its track_embeddings write) always land together, and
rows go in id order.

When that transaction fails, the kind of error decides what happens
(Stage._write_results). Only a constraint violation can be one row's fault:
a constraint judges a row's data, and nothing else a write can hit does.

  - A transient error (_is_transient: anything that is not a sqlite3.Error,
    or a busy or locked database) is first tried again, up to three times,
    2, 4 and 8 s apart, each retry logged (Stage._execute_batch). Only if
    it persists does one of the rules below apply.
  - A transport error, anything that is not a sqlite3.Error (the network,
    an HTTP status, a timeout), is raised with nothing parked. Its outcome
    is unknown: the server may have run and committed the batch after the
    client gave up. That is safe because every statement is an UPDATE or
    an upsert of absolute values: the next pass sees whatever landed and
    redoes whatever did not. The same makes the retries above safe.
  - A constraint violation (sqlite3.IntegrityError) names its statement
    (execute_batch sets `step`), and so its row. Nothing in the batch
    landed. The row is set aside and the rest written again without it;
    once they have landed, the row alone is retried without
    _RETRY_DROPPABLE_FIELDS, and if that still fails it is parked: its own
    column 'failed', '<stage>_stage_error', the error text, like an
    exception in process_row (unless it is finished: below).
    `run_ingest_v2.py --retry-failed` re-arms it.
  - Any other database error is raised with nothing parked and nothing
    landed: a column or table production lacks, a read-only token, a busy
    or locked database that stayed so through its retries, a failed
    COMMIT. None of those is a row's fault, so the stage stops and the
    rows stay pending until the cause is fixed.

A finished track is never parked. Stages do run on ingestion_status='done'
rows (fuse after a language correction, youtube when re-armed), every DJ,
similar and vibe query requires 'done', and README's invariant is that
'done' means every stage passed. So when a stage fails on a finished row,
the park is not written at all: its UPDATE carries `AND ingestion_status
IS NOT 'done'` (one statement, no read), and a rowcount of 0 means the row
was finished. It stays exactly as it was, its stage column still
'pending', so it is still armed and the next pass tries again; the failure
is logged ("left armed") and counted under 'left_armed' for this pass. That
count is not progress: a finished row failing the same way every pass would
otherwise keep --until-drained from ever draining and --loop from ever
backing off. The cost of the asymmetry: no GROUP BY shows such a row, only
the log, and nothing stops it being retried every pass (enough of them
stall a run; see select_cohort in scripts/run_ingest_v2.py).

Until 2026-10-15 one failing row aborted the stage's write loop after the
batch's work was done: SQLite rolled the whole batch back (the next pass
re-ran the same MERT passes and failed on the same row) and Turso kept a
partial batch. The first rework then parked a row on ANY error during a
row-by-row rewrite, so a timeout parked rows whose work was fine and a
schema gap parked every row of every batch. The second judged by position
(a retry failing the same way at its first statement was systematic),
which still parked a row that alone wrote a column production lacks.

Note: no row-level locking. In a concurrent run (run_ingest_v2.py
--concurrent) two stages can act on one row at once. Each UPDATE sets only
its stage's columns and the column it arms, so neither write undoes the
other. Two runs against one database are not guarded against:
the last UPDATE wins, and every stage write is idempotent for its own
columns. Cheap and correct enough for the current volume.
"""
from __future__ import annotations

import logging
import sqlite3
import time
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

# A write that fails transiently (_is_transient) is tried again after each
# of these pauses, and then the error is raised: 14 s of waiting at most.
# Tests stub _sleep.
_RETRY_SLEEPS_S = (2, 4, 8)
_sleep = time.sleep


def _is_transient(e: BaseException) -> bool:
    """A write error that says nothing about the rows written: the network
    or the HTTP layer (anything that is not a sqlite3.Error), or a busy or
    locked database. The same write can succeed a few seconds later."""
    if not isinstance(e, sqlite3.Error):
        return True
    text = (getattr(e, "detail", None) or str(e)).lower()
    return isinstance(e, sqlite3.OperationalError) and any(
        word in text for word in ("locked", "busy", "timeout"))


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
# pipeline every pass. `run_ingest_v2.py --retry-failed <stage>` finds the
# row by this value and sets it and the stage's own column (which reads
# 'failed') back to 'pending'. Never written over 'done': a finished row is
# left armed instead ("Writing a batch").
def stage_error_status(stage_name: str) -> str:
    return f"{stage_name}_stage_error"


# The run_batch count of finished rows a stage failed on and left armed.
# Not progress: the runner leaves it out of a pass's processed total.
LEFT_ARMED = "left_armed"

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

    `alias` is the table prefix the SQL uses ("t." in fetch_pending and the
    runner's cohort query).

    None means no restriction. An empty cohort matches nothing: it used to
    mean "no restriction" too, which every caller had to guard against.
    """
    if only_ids is None:
        return "", []
    if not only_ids:
        return f"AND {alias}id IN (NULL) ", []
    marks = ",".join("?" for _ in only_ids)
    return f"AND {alias}id IN ({marks}) ", [int(x) for x in only_ids]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def update_stmt(track_id: int, fields: dict, unless_done: bool = False) -> tuple[str, list]:
    """(sql, params) for `UPDATE tracks SET <fields> WHERE id = track_id`.

    unless_done: only where the row is not finished, so a park never lands
    on a 'done' track (see "Writing a batch"); its rowcount says which.
    `IS NOT`, not `!=`, so a NULL ingestion_status still counts as not done.
    """
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    where = "id = ? AND ingestion_status IS NOT 'done'" if unless_done else "id = ?"
    return f"UPDATE tracks SET {set_clause} WHERE {where}", [*fields.values(), int(track_id)]


@dataclass
class RowResult:
    """Per-row outcome returned by a Stage's process_row()."""
    track_id: int
    status: str                        # one of STATUS_*
    fields: dict[str, Any] | None      # column -> value updates (excluding status)
    error: str | None = None           # populated when status == STATUS_FAILED


class RowWrite(NamedTuple):
    """Everything one result writes: its tracks columns, plus statements
    that must land in the same transaction (see Stage.row_writes). `park`
    marks a failed result, whose UPDATE must not land on a finished row."""
    track_id: int
    fields: dict
    extra: list
    park: bool = False


class Stage(ABC):
    """
    Base class for all pipeline stages. Subclasses implement:
      - name (class attr): short identifier used in logs.
      - status_column (class attr): the tracks column this stage owns.
      - gate_sql / gate_params, and fetch_cols (class attrs): which rows
        the stage can act on now, and what it reads from them. See below.
      - process_row(row): the work for one track. Runs in a worker thread.
        Return RowResult; do NOT touch the DB from here (run_batch writes
        results on the thread that called it).
      - row_writes(res), optionally: statements that must land with the
        row's tracks UPDATE (classify and fuse write track_embeddings).
    """

    name: str = "stage"
    status_column: str = "status"
    max_workers: int = 8

    # THE GATE: a condition over `tracks` (aliased t) that holds for exactly
    # the rows this stage can act on now, and its parameters. It is the
    # stage's whole eligibility rule, spelled once: fetch_pending selects
    # with it, and scripts/run_ingest_v2.py ORs the selected stages' gates
    # into ONE ids-only query to pick a pass's cohort and another to count
    # what is pending at startup. A gate may reach other tables only
    # through EXISTS, so that it ORs cleanly with the rest.
    gate_sql: str | None = None
    gate_params: tuple = ()
    # What fetch_pending reads for process_row, and a join it needs for that.
    fetch_cols: str = "t.id"
    fetch_join: str = ""

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

    def fetch_pending(self, conn, limit: int, only_ids=None) -> list:
        """Return rows to process this pass: the gate's rows, lowest id
        first, at most `limit` of them.

        `only_ids` restricts the stage to a cohort so a pass advances the
        same tracks through every stage. See id_filter().
        """
        if not self.gate_sql:
            raise NotImplementedError(f"{type(self).__name__} declares no gate_sql")
        idf, idp = id_filter(only_ids, "t.")
        return list(conn.execute(
            f"SELECT {self.fetch_cols} FROM tracks t {self.fetch_join}"
            f"WHERE ({self.gate_sql}) {idf}ORDER BY t.id ASC LIMIT ?",
            (*self.gate_params, *idp, limit),
        ).fetchall())

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
        docstring), and return per-status counts. A row parked after a
        failed write counts as 'failed'; a finished row a failure left armed
        counts as LEFT_ARMED. Called by the orchestrator once per pass, or
        by a concurrent run's stage thread once per batch.
        """
        rows = self.fetch_pending(conn, limit, only_ids)
        counts = {STATUS_DONE: 0, STATUS_NO_MATCH: 0, STATUS_FAILED: 0}
        if not rows:
            log.info("[%s] no pending rows", self.name)
            return counts

        log.info("[%s] processing %d rows (max_workers=%d)",
                 self.name, len(rows), self.max_workers)

        results: list[RowResult] = []
        # Pool threads are named <stage>_<n>, so a concurrent run's log can
        # say which stage a line from process_row came from.
        with ThreadPoolExecutor(max_workers=self.max_workers,
                                thread_name_prefix=self.name) as ex:
            future_to_row = {ex.submit(self._safe_process, r): r for r in rows}
            for fut in as_completed(future_to_row):
                results.append(fut.result())
        # as_completed order is arbitrary; write in id order.
        results.sort(key=lambda r: int(r.track_id))

        # Written on the calling thread, after every row is processed.
        writes = []
        for res in results:
            fields, extra = self.row_writes(res)
            writes.append(RowWrite(int(res.track_id), self._finalize_fields(fields, res),
                                   extra, park=res.status == STATUS_FAILED))
        instead = self._write_results(conn, writes, log)
        for res in results:
            status = instead.get(int(res.track_id), res.status)
            counts[status] = counts.get(status, 0) + 1

        log.info("[%s] batch done: %s", self.name, counts)
        return counts

    def _write_results(self, conn, writes: list[RowWrite], log) -> dict[int, str]:
        """Write the batch by the rules in "Writing a batch" (module
        docstring). Returns the rows that did not end as their result said,
        with what to count them as: STATUS_FAILED for a row parked because
        its write failed, LEFT_ARMED for a finished row a failure left as it
        was."""
        batch = list(writes)
        set_aside: list[tuple[RowWrite, Exception]] = []
        instead: dict[int, str] = {}
        while batch:
            stmts, owner, heads = [], [], []
            for i, w in enumerate(batch):
                heads.append(len(stmts))
                for stmt in self._statements(w):
                    stmts.append(stmt)
                    owner.append(i)
            try:
                cursors = self._execute_batch(conn, stmts, log)
            except Exception as e:
                if not isinstance(e, sqlite3.Error):
                    log.warning("[%s] transport error writing %d rows (%s): outcome "
                                "unknown, rows left for the next pass",
                                self.name, len(batch), e)
                    raise
                step = getattr(e, "step", None)
                if not isinstance(e, sqlite3.IntegrityError) or step is None:
                    log.error("[%s] writing %d rows failed (%s): not one row's fault, "
                              "nothing landed, stopping with nothing parked",
                              self.name, len(batch), e)
                    raise
                bad = batch.pop(owner[step])
                log.warning("[%s] row %d broke the batch write (%s); writing the "
                            "other %d without it", self.name, bad.track_id, e, len(batch))
                set_aside.append((bad, e))
                continue
            # A failed result's park that matched nothing: the row is
            # finished. Its UPDATE is its first statement, and its only one.
            for i, w in enumerate(batch):
                if w.park and cursors[heads[i]].rowcount == 0:
                    instead[w.track_id] = self._leave_armed(
                        w.track_id, w.fields.get("ingestion_error"), log)
            break
        for w, e in set_aside:
            settled = self._settle(conn, w, e, log)
            if settled:
                instead[w.track_id] = settled
        return instead

    def _execute_batch(self, conn, stmts: list, log) -> list:
        """conn.execute_batch(stmts), tried again after each pause in
        _RETRY_SLEEPS_S while it fails transiently (_is_transient); then the
        error is raised. Safe even when a failed attempt did commit (the
        client gave up on a request the server finished): every statement a
        stage writes is an UPDATE or an upsert of absolute values, and a
        park matches the same row again, so a second run lands the same."""
        for n, pause in enumerate((*_RETRY_SLEEPS_S, None), start=1):
            try:
                return conn.execute_batch(stmts)
            except Exception as e:
                if pause is None or not _is_transient(e):
                    raise
                log.warning("[%s] writing %d statements failed (%s); retry %d of %d in %ds",
                            self.name, len(stmts), e, n, len(_RETRY_SLEEPS_S), pause)
                _sleep(pause)

    @staticmethod
    def _statements(w: RowWrite) -> list:
        return [update_stmt(w.track_id, w.fields, unless_done=w.park), *w.extra]

    def _leave_armed(self, track_id: int, error, log) -> str:
        log.warning("[%s] row %d is finished; left armed for the next pass (%s)",
                    self.name, track_id, error)
        return LEFT_ARMED

    def _settle(self, conn, w: RowWrite, error: sqlite3.IntegrityError, log) -> str | None:
        """Deal with a row whose constraint violation broke the batch,
        alone. Returns None if its write landed after all, else what to
        count it as: STATUS_FAILED once parked, LEFT_ARMED if it is finished
        and so was left as it was.

        A UNIQUE collision (typically the legacy `UNIQUE (user_id,
        apple_id)` from the pre-split-tracks era) is retried without the
        collision-prone metadata fields, so the row's own status still
        lands and the pipeline does not loop on it. db_client has raised
        IntegrityError for Turso's SQLITE_CONSTRAINT since 2026-10-15, so
        this should hold in production too; unverified there as of
        2026-10-15, since nothing in this pipeline has run against it.

        If the row still violates a constraint it is parked: 'failed',
        '<stage>_stage_error' and the error text, unless it is finished
        (the park's WHERE skips 'done' rows; a rowcount of 0 says so, and
        would also say so for a row deleted meanwhile, which is equally
        fine to leave alone). Any other error on the way, the park write's
        included, propagates (a transient one after its retries): it is not
        this row's fault (see "Writing a batch") and the stage should stop.
        """
        dropped = [k for k in _RETRY_DROPPABLE_FIELDS if k in w.fields]
        if dropped:
            reduced = {k: v for k, v in w.fields.items() if k not in dropped}
            try:
                self._execute_batch(conn, self._statements(w._replace(fields=reduced)), log)
                log.warning("row %d retried without %s due to unique-constraint "
                            "collision", w.track_id, dropped)
                return None
            except sqlite3.IntegrityError as e:
                error = e
        parked = self._execute_batch(conn, [update_stmt(w.track_id, {
            self.status_column: STATUS_FAILED,
            "ingestion_status": stage_error_status(self.name),
            "ingestion_error": f"[{self.name}] write failed: {error}"[:500],
        }, unless_done=True)], log)[0]
        if parked.rowcount == 0:
            return self._leave_armed(w.track_id, f"write failed: {error}", log)
        log.warning("[%s] row %d could not be written, marked it failed: %s",
                    self.name, w.track_id, error)
        return STATUS_FAILED

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
            # stop it re-entering at preview. run_batch writes this as a
            # park, which never lands on a finished track ("Writing a
            # batch").
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
