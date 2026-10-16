"""
V2 modular ingest orchestrator.

Runs each stage across its full pending batch (concurrent I/O within a
stage), then advances to the next stage. Each stage settles
ingestion_status itself; there is no separate promote pass.
Songs move through the pipeline in waves — not one-by-one. With
--concurrent the stages run at the same time instead ("Concurrent mode",
below).

Wired stages, in order:
  preview   (stage_preview.py)   — resolves a 30s preview URL
  download  (stage_download.py)  — caches the audio locally
  librosa   (stage_librosa.py)   — DSP feature bank
  classify  (stage_classify.py)  — MERT: vibe/mood scalars + the 768-d vector
  language  (stage_language.py)  — the stopping point; classifies nothing
  fuse      (stage_fuse.py)      — builds the 788-d retrieval vector
  youtube   (stage_youtube.py)   — first ytsearch hit, then settles ingestion_status

Dependencies: preview -> download -> librosa -> classify -> fuse -> youtube
is one armed chain. `language` sits outside it — since 2026-10-10 it is
read off metadata rather than audio, so it needs no preview, no cached
file and no GPU, and is armed at ingest entry in parallel with preview.

A pipeline run STOPS at language. The stage classifies nothing; it only
normalises every live row into one unambiguous waiting state,
`language_status='pending'`. A Claude Code session then queries the
database for those rows, reads title/artist/album, and writes the tag plus
the `fuse_status='pending'` cascade (ingest_pipeline/language_tagging.py).
The next run sees 'done' and proceeds to fuse -> youtube.

`fuse` gates strictly on language_status='done' — 20% of the fused vector
is a language one-hot, so fusing early puts a track in the wrong region of
the similarity space rather than merely a less precise one. That makes the
tagging step load-bearing: untagged tracks never reach the DJ pool.
`python scripts/language_tags.py --status` says how many are waiting and
for how long.

Usage:
    # One pass across every stage, up to 50 rows per stage:
    python scripts/run_ingest_v2.py --batch 50

    # Pass after pass until nothing is left to do, then exit:
    python scripts/run_ingest_v2.py --until-drained

    # Run for good (watching a dev DB). Idle sleeps start at --interval and
    # double up to 10 min; work resets them:
    python scripts/run_ingest_v2.py --loop --interval 30

    # Restrict to a subset of stages (comma-separated):
    python scripts/run_ingest_v2.py --stages preview,youtube

    # Only re-run the library-wide score recompute, no stages:
    python scripts/run_ingest_v2.py --recompute-only

    # Re-arm rows parked at <stage>_stage_error (one stage, or all), then run:
    python scripts/run_ingest_v2.py --retry-failed classify

    # Every stage at once, a thread each, until all are idle:
    python scripts/run_ingest_v2.py --concurrent --until-drained

    # Production Turso instead of the local SQLite file (the default,
    # --target local). Credentials come from the gitignored .env.prod, see
    # scripts/_target.py. Back up first: python scripts/backup_prod_db.py
    python scripts/run_ingest_v2.py --target prod --batch 10
    python scripts/run_ingest_v2.py --target prod --batch 50 --until-drained

Every log line names the target, so a prod run cannot be mistaken for a
local one.

Each pass carries one cohort: the lowest --batch ids that at least one of
the selected stages can act on, by that stage's own gate (select_cohort).

The runner re-runs the library-wide score recompute
(ingest/score_recompute.py): activation/valence from the librosa features,
activation z-scored across the catalogue into activation_relative and
vibe_score, and mood. Only librosa and classify move it — librosa writes its
inputs and adds rows to the baseline, classify overwrites the columns it
owns — so it runs only if one of them finished a track:

  * a single pass: once at the end of the pass;
  * --loop and --until-drained: ONCE WHEN THE LOOP DRAINS (the first pass
    that processes nothing), not after every scoring pass. Every newly
    scored track shifts the catalogue mean and std, so nearly every row's
    activation_relative moves and the skip-unchanged check saves nothing:
    draining 1,000 tracks at --batch 50 would cost 20 full recomputes of
    ~3,800 UPDATEs each instead of one. A failed recompute keeps its
    pending count; --loop retries it at the next idle point, and
    --until-drained exits 1.

Imports used to run it after every sync; a sync only adds unanalysed rows,
so it moved here (2026-10-13).

Concurrent mode (--concurrent)
------------------------------
A pass runs its stages one after another, so the GPU waits while iTunes is
asked and the network waits while MERT runs. With --concurrent every
selected stage runs at once, in a thread of its own with its own
connection, working through its own gate: it looks (one ids-only query,
the lowest --batch ids its gate accepts), runs a batch of those rows with
run_batch as a pass does, and looks again. There is no cohort.

A stage's feeders are the selected stages that arm it (Stage.arms) and the
ones it comes after (Stage.after: language after preview and download). A
feeder hands off: its batch with 'done' rows wakes the stages it feeds, so
they look at once. And it goes first: a look leaves out the rows a feeder's
gate still accepts, because gates can overlap. Language's does, beside the
chain: a new production row is in preview's gate and language's, and
language waits for preview and download, either of which can settle it
'no_preview', which keeps language_status NULL. Librosa's and classify's
overlapped on a schema.sql file, whose ml_status defaults to 'pending', so
classify could take a downloaded row first and MERT it twice; since
2026-10-16 classify's own gate requires librosa_status 'done'. So per row
the order is a pass's. With --stages preview,fuse nothing feeds across the
gap.

An idle stage waits for a hand-off, or for IDLE_BACKOFF_FIRST_S (5 s)
doubling to IDLE_BACKOFF_MAX_S (60 s), reset by a look that does work. An
idle look is one ids-only query and nothing else. Measured 2026-10-16 on a
SQLite file of 6,238 tracks (production's count on 2026-10-15) with nothing
to do: 28 looks in the first minute (each stage at 0, 5, 15 and 35 s), then
7 a minute, nothing else sent. By EXPLAIN QUERY PLAN six of them scan
tracks there and preview's uses idx_tracks_ingestion_status; production's
tracks has no indexes at all (backlog 1.13), so all seven scan it. Turso
bills rows read, so an idle --concurrent --loop there reads ~44,000 rows a
minute at that size, ~63M a day, where the sequential --loop backs off to
one scan per 10 minutes. Against production, --until-drained.

How a run ends:

  * No loop flag, a single pass: a stage stops at the first look that finds
    nothing to do and began after every stage feeding it had stopped. So it
    takes what its gate holds and what its feeders hand it, in batches of
    --batch, and a stage nothing in the run feeds stops at its first empty
    look. A row that reaches a gate from outside the run (the app, a
    tagging session) is taken only while that stage still runs. --batch
    does not bound the run: for a small first look, run a sequential pass.
  * --until-drained: every stage is idle, and each one's latest look began
    after the latest batch that did work anywhere. Not when one gate is
    empty while its upstream is in a batch. When all are idle but some
    looked before the latest work, those are woken to look again at once,
    so the end does not wait out their back-off.
  * --loop: never ends by itself; each time it drains, the owed recompute
    runs, and again at the next idle look if it failed. With --only-ids it
    ends when drained, as the passes do.

The score recompute runs once at the end, or each time a --loop drains, if
a SCORING_STAGES stage finished rows; a failed one exits 1. Under --loop
the stages may write while it runs, and what they score is owed to the
next one.

An exception that escapes a stage's thread (base.py raises on a transport
error after its retries, and on a write error that is not one row's fault)
is logged with the stage's name and stops the run: every other stage
finishes the batch it is in and exits, an idle one at once, nothing is
recomputed, exit 1. Ctrl-C does the same, exit 130, as soon as the main
thread acts on it: within _SUPERVISE_TICK_S, except during a --loop's
recompute after a drain, which it finishes first (~8 s on production,
~0.3 s locally). A second Ctrl-C exits as soon as the main thread runs
Python again (during that recompute, after the database call in hand), and
each batch still in flight lands whole or not at all.

Every line names its stage ([preview]; [runner] for the main thread), and
every SUMMARY_EVERY_S (60 s) a summary line gives each stage's
done/no_match/failed and what it is doing. On SQLite, which stays in
rollback-journal mode, every connection waits 30 s for a lock: a stage's
commit waits out a read another connection holds open, up to that long
(db_client._SQLITE_LOCK_WAIT_S has the measurements). On Turso each
thread's connection is its own HTTP session; concurrent writers there have
not been run (2026-10-16).

Shared state, read 2026-10-16: each stage object is used by its own thread
only, and process_row by that stage's pool, as in a pass. ItunesPreview's
throttle lock is class-level and only preview's pool takes it. ml_backend
loads MERT lazily without a lock, and only classify's single pool thread
calls it. logging's handlers lock per record. LanguageStage.run_batch counts
waiting rows on the connection it is given, its own thread's.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
# For _target. Explicit, not left to the script directory being
# sys.path[0]: that entry is missing under `python -P` / PYTHONSAFEPATH.
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
# Appended, not inserted: ingest/db.py must not shadow backend/db.py.
sys.path.append(str(PROJECT_ROOT / "ingest"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from db import ensure_db, get_conn  # noqa: E402
from ingest_pipeline.stage_preview import PreviewStage  # noqa: E402
from ingest_pipeline.stage_download import DownloadStage  # noqa: E402
from ingest_pipeline.stage_classify import ClassifyStage  # noqa: E402
from ingest_pipeline.stage_youtube import YoutubeStage  # noqa: E402
from ingest_pipeline.stage_language import LanguageStage  # noqa: E402
from ingest_pipeline.stage_librosa import LibrosaStage  # noqa: E402
from ingest_pipeline.stage_fuse import FuseStage  # noqa: E402
from ingest_pipeline.base import (  # noqa: E402
    LEFT_ARMED, STATUS_DONE, STATUS_FAILED, STATUS_NO_MATCH, id_filter, stage_error_status)
import score_recompute  # noqa: E402
import _target  # noqa: E402


log = logging.getLogger("vibescape.ingest.orch")

# Stages whose 'done' rows change what the score recompute produces. See the
# module docstring; fuse, youtube etc. touch none of its inputs or outputs.
SCORING_STAGES = ("librosa", "classify")

# An idle --loop sleeps --interval, doubling each idle pass up to this, and
# drops back to --interval after a pass that did work. Every idle pass is
# one scan of `tracks` (select_cohort), and Turso bills rows read: at a
# fixed 30 s that is 2,880 scans a day for a loop nobody is watching; at
# this cap, 144. --until-drained is what a run that should end uses.
IDLE_SLEEP_MAX_S = 600

# Every stage, by name, in chain order.
STAGE_CLASSES: dict[str, type] = {
    "preview":   PreviewStage,
    "download":  DownloadStage,
    "librosa":   LibrosaStage,
    "classify":  ClassifyStage,
    "language":  LanguageStage,
    "fuse":      FuseStage,
    "youtube":   YoutubeStage,
}


def build_stages(names: list[str]) -> list:
    unknown = [n for n in names if n not in STAGE_CLASSES]
    if unknown:
        raise SystemExit(f"unknown stages: {unknown}. known: {list(STAGE_CLASSES)}")
    return [STAGE_CLASSES[n]() for n in names]


def rearm_failed(conn, which: str) -> dict[str, int]:
    """Send rows parked at <stage>_stage_error back into the pipeline.

    A row that failed a stage (an exception in process_row, or a write the
    stage could not make) is parked: ingestion_status='<stage>_stage_error',
    the stage's own column 'failed', the error in ingestion_error. That
    keeps it out of every gate until somebody decides the cause is fixed.
    This undoes exactly that, for one stage or `all`: ingestion_status and
    the stage's column back to 'pending', ingestion_error cleared, in one
    transaction. Returns {stage: rows re-armed}.

    A finished track is never parked (ingest_pipeline/base.py, "Writing a
    batch"): a failure leaves it 'done' and armed, and the next pass retries
    it without this flag.
    """
    names = list(STAGE_CLASSES) if which == "all" else [which]
    unknown = [n for n in names if n not in STAGE_CLASSES]
    if unknown:
        raise SystemExit(f"--retry-failed: unknown stage {which!r}; "
                         f"expected all or one of {list(STAGE_CLASSES)}")
    cursors = conn.execute_batch([
        (f"UPDATE tracks SET ingestion_status = 'pending', "
         f"{STAGE_CLASSES[n].status_column} = 'pending', ingestion_error = NULL "
         "WHERE ingestion_status = ?", (stage_error_status(STAGE_CLASSES[n].name),))
        for n in names])
    return {n: max(c.rowcount, 0) for n, c in zip(names, cursors)}


def _gated(stages: list) -> list:
    """The stages that declare a gate (all real ones; test doubles may only
    implement fetch_pending)."""
    return [s for s in stages if getattr(s, "gate_sql", None)]


def pending_snapshot(conn, stages: list) -> dict[str, int]:
    """How many rows each stage's gate accepts now: one query, one scan.

    It used to count `<column> = 'pending'` per status column, which is not
    what any stage acts on: rows waiting for a language tag showed as
    language_status 28 / fuse_status 28 and the next pass said drained.
    Counting by gate makes the startup line and the cohort agree.
    """
    gated = _gated(stages)
    if not gated:
        return {}
    cols = ", ".join(f"SUM(CASE WHEN ({s.gate_sql}) THEN 1 ELSE 0 END)" for s in gated)
    row = conn.execute(f"SELECT {cols} FROM tracks t",
                       [p for s in gated for p in s.gate_params]).fetchone()
    return {s.name: int(row[i] or 0) for i, s in enumerate(gated)}


def select_cohort(conn, stages: list, batch: int, only_ids=None) -> list[int]:
    """
    Pick the tracks this pass will advance: the lowest `batch` ids that at
    least one of `stages` can act on now.

    "Can act on" is each stage's own gate (Stage.gate_sql), which already
    spells out that stage's real preconditions, so the cohort is the union
    of what the gates accept, lowest first: ONE ids-only query over tracks
    that ORs them. That covers a track still moving through the chain, a
    finished one with a stage re-armed (a corrected language tag sets
    fuse_status='pending'), and a leftover language_status='whisper_done'
    row (LanguageStage's gate), and nothing else. Only the stages being run
    count: with --stages language,fuse a row waiting for preview is not this
    pass's work.

    One query because an idle --loop runs this every pass: the first
    version of the union asked each stage's fetch_pending in turn, seven
    queries of which six scanned tracks and fuse's read a 3 KB MERT blob
    per row, and Turso bills rows read. Idle now costs one scan of ids.

    It used to be a predicate of its own: ingestion_status 'pending' or
    NULL, or any stage column 'pending'. Since 2026-10-10 language never
    writes 'done' (a tagging session does) and fuse waits for it, so every
    row that had been through classify sat at language_status='pending' +
    fuse_status='pending' with ingestion_status still 'pending'. Those were
    the lowest ids, every pass selected them again, no stage could touch
    them, the pass processed nothing, and the loop declared itself drained
    while every higher id waited. Live on production: a read-only probe on
    2026-10-15 found 28 such rows at the head of the id order, ahead of 799
    pending rows that had never entered the pipeline.

    A pass on a non-empty cohort always acts on something: its lowest id
    came from some stage's gate, and only an earlier stage acting on the
    row can move it out of that gate. That counts as processed, except
    where the stage failed on a finished track and left it armed (base.py,
    "Writing a batch"): that is not progress, and run_pass leaves it out.
    So if --batch finished tracks at the head of the id order fail every
    pass, they fill every cohort and a loop ends as drained while higher
    ids wait; each such failure is in the log as "left armed".

    `only_ids` (--only-ids) restricts every gate, and so the union; an
    empty list selects nothing.
    """
    ids: set[int] = set()
    gated = _gated(stages)
    if gated:
        ids.update(int(r[0]) for r in conn.execute(
            *lowest_ids_query(gated, batch, only_ids)).fetchall())
    for stage in stages:
        if stage not in gated:
            ids.update(int(r["id"]) for r in stage.fetch_pending(conn, batch, only_ids))
    return sorted(ids)[:batch]


def lowest_ids_query(stages: list, batch: int, only_ids=None,
                     leave_out: list = ()) -> tuple[str, tuple]:
    """(sql, params) for the lowest `batch` track ids, in id order, that
    some stage in `stages` has in its gate and no stage in `leave_out` does,
    within `only_ids` (id_filter): one ids-only scan of tracks.

    A pass's cohort asks it with every selected stage (select_cohort), a
    concurrent stage's look with itself alone, leaving out its feeders
    (_look_query), so a rule about which rows a run takes is written here
    once. COALESCE because a gate can be NULL rather than false for a row (a
    NULL status column compared with 'pending'), and NOT NULL is NULL: the
    row would be dropped as if a left-out stage could act on it.
    """
    def union(group):
        return " OR ".join(f"({s.gate_sql})" for s in group)
    sql = f"SELECT t.id FROM tracks t WHERE ({union(stages)}) "
    params = [p for s in stages for p in s.gate_params]
    if leave_out:
        sql += f"AND NOT COALESCE({union(leave_out)}, 0) "
        params += [p for s in leave_out for p in s.gate_params]
    idf, idp = id_filter(only_ids, "t.")
    return f"{sql}{idf}ORDER BY t.id ASC LIMIT ?", (*params, *idp, batch)


def run_pass(stages: list, batch: int, only_ids=None) -> tuple[int, int]:
    """One orchestrator pass.

    Selects ONE cohort of tracks and walks it through every stage in
    order, so the same tracks advance together: preview then download
    then librosa … on the same ids, within the same pass. Stages still
    process their slice as a batch (thread pools, warm models) — the
    cohort just pins WHICH rows they are allowed to touch.

    Previously each stage independently ran its own SELECT, so a pass
    could classify one set of tracks and fuse a completely different set.
    That is not hypothetical: after a migration left fuse_status='pending'
    library-wide, fuse picked the lowest ids it could find rather than the
    tracks classify had just encoded.

    Each stage settles ingestion_status itself; there is no promote pass.

    Returns (rows processed across all stages, rows a SCORING_STAGES stage
    finished as 'done'), each batch counted by batch_progress(). The second
    says whether a recompute is owed: at the end of a single pass, or —
    accumulated across passes — once the loop drains (see the module
    docstring for why not per pass).
    """
    conn = get_conn()
    total_processed = 0
    scored = 0
    try:
        cohort = select_cohort(conn, stages, batch, only_ids)
        if not cohort:
            return 0, 0
        log.info("pass cohort: %d tracks (ids %d..%d)",
                 len(cohort), cohort[0], cohort[-1])
        for stage in stages:
            processed, owed = batch_progress(
                stage.name, stage.run_batch(conn, batch, log, only_ids=cohort))
            total_processed += processed
            scored += owed
    finally:
        conn.close()
    return total_processed, scored


def batch_progress(stage_name: str, counts: dict) -> tuple[int, int]:
    """What one run_batch result counts for, in a pass and in a concurrent
    run alike: (rows that are progress, rows owed a score recompute).

    A finished track the stage failed on and left armed (LEFT_ARMED) is not
    progress. Counted, one that fails the same way every time keeps
    --until-drained passing forever with no sleep and --loop from backing
    off, and keeps a concurrent stage looking again at once: in review a
    stage counting it did 9,110 batches in 3 s and never drained. A
    SCORING_STAGES stage's 'done' rows are owed a recompute.
    """
    processed = sum(n for status, n in counts.items() if status != LEFT_ARMED)
    owed = int(counts.get(STATUS_DONE, 0)) if stage_name in SCORING_STAGES else 0
    return processed, owed


def recompute_scores() -> bool:
    """Run the library-wide score recompute once and log its summary.

    Returns False (after logging the traceback) if it failed, so a --loop
    run keeps draining the pipeline instead of dying on a scoring error.
    """
    conn = get_conn()
    try:
        summary = score_recompute.recompute_axes_and_zscores(conn)
    except Exception:
        log.exception("score recompute failed")
        return False
    finally:
        conn.close()
    stats = summary.get("activation_stats") or {}
    log.info("score recompute: scored=%d written=%d unchanged=%d "
             "activation mean=%.2f std=%.2f moods=%s",
             summary["updated"], summary["written"], summary["unchanged"],
             stats.get("mean", float("nan")), stats.get("std", float("nan")),
             summary["mood_distribution"])
    return True


def after_pass(scored: int) -> bool:
    """Single-pass mode: recompute iff the pass finished something that
    moves scores. --loop does not use this; it recomputes once on draining."""
    if scored <= 0:
        return True
    log.info("pass scored %d rows; recomputing library scores", scored)
    return recompute_scores()


# ------------------------------------------------------------ --concurrent
#
# "Concurrent mode" in the module docstring says what this does and why.

# An idle stage looks again after this long, doubling after each look that
# finds nothing up to the cap, and back to the start after one that does
# work. Its hand-off event usually wakes it first; the back-off is for rows
# that reach its gate from outside the run (the app, a tagging session).
IDLE_BACKOFF_FIRST_S = 5
IDLE_BACKOFF_MAX_S = 60
# How often a concurrent run logs what each stage has done and is doing.
SUMMARY_EVERY_S = 60
# The main thread waits at most this long at a time, so it acts on a Ctrl-C
# within it, except while it runs a --loop's recompute after a drain: then
# when that returns, ~8 s on production's 4,918 scored rows and ~0.3 s
# locally (the lead session's timings, 2026-10-16). --until-drained
# recomputes only after the stage threads have stopped.
_SUPERVISE_TICK_S = 0.5

_MODE_TEXT = {"single": "single pass", "until_drained": "until drained", "loop": "loop"}


def _wait_for(event: threading.Event, timeout: float) -> bool:
    """An idle stage's sleep: until its event is set or `timeout` passes.
    Tests stub it."""
    return event.wait(timeout)


# A second Ctrl-C ends the process here rather than by returning: at exit
# the interpreter joins every ThreadPoolExecutor's threads, so a stage in
# the middle of a batch would still run its last rows first (2026-10-16,
# Python 3.13.11: three queued 1.5 s jobs on a pool started from a daemon
# thread held the process 4.6 s after its main thread returned). Tests
# stub it.
_exit_now = os._exit


def _install_sigint(handler):
    """Make `handler` this process's Ctrl-C for a concurrent run; returns a
    function that puts back the one before. Off the main thread, which
    signals never reach, it installs nothing."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = signal.signal(signal.SIGINT, handler)
    # None: the one before was not set from Python.
    return lambda: signal.signal(signal.SIGINT,
                                 signal.SIG_DFL if previous is None else previous)


def _concurrent_conn():
    """A stage thread's own connection, from get_conn(): one per thread,
    never shared. A Turso connection holds a requests.Session, which is not
    thread-safe. SQLite needs nothing more: every connection db_client opens
    waits 30 s for a lock, where short stage batches never come near
    (db_client._SQLITE_LOCK_WAIT_S has the measurements). Tests stub it."""
    return get_conn()


def _look_query(stage, ahead: list, batch: int, only_ids) -> tuple[str, tuple] | None:
    """The ids-only query a concurrent stage looks with: lowest_ids_query on
    its own gate, leaving out the rows a stage in `ahead` (its feeders) can
    still act on. None for a stage that declares no gate (a test double)."""
    if not getattr(stage, "gate_sql", None):
        return None
    return lowest_ids_query([stage], batch, only_ids, leave_out=_gated(ahead))


class _Worker:
    """One stage of a concurrent run: its thread, its hand-off event, and
    what the summary reports. All but `stage`, `name`, `wake`, `feeders`
    and `fed` is read and written under the run's lock."""

    def __init__(self, stage):
        self.stage = stage
        self.name = stage.name
        self.wake = threading.Event()
        self.feeders: list[_Worker] = []      # arm this stage, or it runs after them
        self.fed: list[_Worker] = []          # the stages this one feeds
        self.thread: threading.Thread | None = None
        self.totals = {STATUS_DONE: 0, STATUS_NO_MATCH: 0, STATUS_FAILED: 0}
        self.busy = False
        self.stopped = False
        # The run's generation when this stage's latest look that found
        # nothing to do began; -1 before its first.
        self.looked_at = -1
        self.state, self.since, self.next_look = "starting", time.monotonic(), None


class _ConcurrentRun:
    """See "Concurrent mode" in the module docstring."""

    def __init__(self, stages: list, batch: int, only_ids, mode: str):
        names = [s.name for s in stages]
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            raise SystemExit(f"--concurrent runs each stage once; given twice: {twice}")
        self.batch, self.only_ids, self.mode = batch, only_ids, mode
        self.workers = [_Worker(s) for s in stages]
        for w in self.workers:
            column = getattr(w.stage, "status_column", None)
            w.feeders = [v for v in self.workers if v is not w and (
                column in getattr(v.stage, "arms", ()) or v.name in getattr(w.stage, "after", ()))]
        for w in self.workers:
            w.fed = [v for v in self.workers if w in v.feeders]
        # A single pass stops a stage after its feeders, so they must not
        # feed each other in a circle.
        left = {w.name: {f.name for f in w.feeders} for w in self.workers}
        while left:
            free = [n for n, fs in left.items() if not fs & left.keys()]
            if not free:
                raise SystemExit(f"--concurrent: {sorted(left)} feed each other in a circle")
            for n in free:
                del left[n]
        self.lock = threading.Lock()
        self.stopping = threading.Event()   # every stage: stop after the current batch
        self.changed = threading.Event()    # the main thread: something to look at
        self.generation = 0                 # +1 for every batch that did work
        self.drained_at = None              # the generation the run last drained at
        self.scored = 0                     # rows SCORING_STAGES finished, not recomputed yet
        self.failure = None                 # (stage name, exception), the first one
        # Ctrl-C presses, counted by _on_sigint, and whether the second one
        # gave up on the stages still in a batch.
        self.interrupts = 0
        self.abandoned = False
        self._told = False                  # the first press has been logged

    # ------------------------------------------------------------ main thread

    def run(self) -> int:
        log.info("concurrent run, %s, --batch %d: %s", _MODE_TEXT[self.mode], self.batch,
                 "; ".join(f"{w.name} hands off to {', '.join(v.name for v in w.fed) or 'none'}"
                           for w in self.workers) or "no stages")
        restore = _install_sigint(self._on_sigint)
        try:
            for w in self.workers:
                w.thread = threading.Thread(target=self._work, args=(w,), name=w.name,
                                            daemon=True)
                w.thread.start()
            self._supervise()
            for w in self.workers:
                while w.thread.is_alive() and not self.abandoned:
                    w.thread.join(_SUPERVISE_TICK_S)
        except BaseException:
            self.stop()
            raise
        finally:
            restore()
        if self.abandoned:
            return 130
        return self._finish()

    def _on_sigint(self, signum, frame) -> None:
        """Ctrl-C during the run. The first press only counts: the main
        thread's next tick stops the stages, since a signal handler that
        took a lock the interrupted code holds would deadlock. The second
        gives up on the batches in flight: a batch is one transaction, so
        each lands whole or not at all. os.write, not logging, for the same
        reason."""
        self.interrupts += 1
        if self.interrupts < 2:
            return
        busy = [w.name for w in self.workers if not w.stopped]
        os.write(2, f"interrupted again: exiting now, leaving {busy} mid-batch\n".encode())
        self.abandoned = True
        _exit_now(130)

    def _supervise(self) -> None:
        """Wait until every stage has stopped; meanwhile act on Ctrl-C, run
        a loop's owed recompute each time it drains, and log the summary."""
        next_summary = time.monotonic() + SUMMARY_EVERY_S
        handled = None
        while not self.abandoned:
            self._act_on_interrupt()
            self.changed.wait(_SUPERVISE_TICK_S)
            self.changed.clear()
            with self.lock:
                # A thread ends through _work's finally, which marks it
                # stopped; going by liveness as well keeps Ctrl-C able to
                # end the run should one ever die some other way.
                done = all(w.stopped or not w.thread.is_alive() for w in self.workers)
                drained = self.drained_at
            if done:
                return
            if (self.mode == "loop" and not self.stopping.is_set()
                    and drained is not None and drained != handled):
                handled = drained
                log.info("drained: every stage is idle")
                if not self._recompute_owed():
                    with self.lock:
                        self.drained_at = None      # reported again at the next idle look
                    handled = None
            if time.monotonic() >= next_summary:
                self._log_summary()
                next_summary = time.monotonic() + SUMMARY_EVERY_S

    def _recompute_owed(self) -> bool:
        with self.lock:
            owed = self.scored
        if owed <= 0:
            return True
        log.info("%d rows scored since the last recompute; recomputing library scores", owed)
        if not recompute_scores():
            log.warning("score recompute owed for %d rows; retrying at the next idle point", owed)
            return False
        with self.lock:
            self.scored -= owed
        return True

    def _act_on_interrupt(self) -> None:
        """The first Ctrl-C, once (_on_sigint only counts it): say so, and
        stop every stage. _supervise acts at each tick; _finish acts again,
        so a press that came while the last stage was ending, after the
        last tick, is still said and not only counted in the exit status."""
        if self.interrupts and not self._told:
            self._told = True
            log.warning("interrupted: every stage stops after its current batch "
                        "(Ctrl-C again to exit at once)")
            self.stop()

    def _finish(self) -> int:
        """After every stage thread has ended: the owed recompute, and the
        run's exit status."""
        self._act_on_interrupt()
        self._log_summary()
        owed_note = ("score recompute owed for %d rows, not run; once the cause is fixed: "
                     "python scripts/run_ingest_v2.py --recompute-only")
        if self.failure or self.interrupts:
            if self.failure:
                name, e = self.failure
                log.error("the run stopped because [%s] failed (%s: %s); every other stage "
                          "finished its batch first", name, type(e).__name__, e)
            if self.scored:
                log.warning(owed_note, self.scored)
            return 1 if self.failure else 130
        log.info("%s; done", "drained" if self.mode != "single" else "single pass finished")
        if self.scored <= 0:
            return 0
        log.info("%d rows scored in this run; recomputing library scores", self.scored)
        try:
            return 0 if recompute_scores() else 1
        except KeyboardInterrupt:
            log.warning(owed_note, self.scored)
            return 130

    def _log_summary(self) -> None:
        now = time.monotonic()
        with self.lock:
            parts = []
            for w in self.workers:
                t = w.totals
                text = f"{w.name} {t[STATUS_DONE]}/{t[STATUS_NO_MATCH]}/{t[STATUS_FAILED]}"
                text += "".join(f" +{n} {k}" for k, n in t.items()
                                if n and k not in (STATUS_DONE, STATUS_NO_MATCH, STATUS_FAILED))
                text += f", {w.state} for {now - w.since:.0f}s"
                if w.state == "idle" and w.next_look is not None:
                    text += f", next look in {max(0.0, w.next_look - now):.0f}s"
                parts.append(text)
        log.info("summary, done/no_match/failed: %s", "; ".join(parts) or "no stages")

    def stop(self) -> None:
        """Every stage stops after the batch it is in; an idle one at once."""
        self.stopping.set()
        for w in self.workers:
            w.wake.set()
        self.changed.set()

    # ----------------------------------------------------------- stage threads

    def _work(self, w: _Worker) -> None:
        conn = None
        try:
            conn = _concurrent_conn()
            look = _look_query(w.stage, [f.stage for f in w.feeders], self.batch, self.only_ids)
            pause = IDLE_BACKOFF_FIRST_S
            while not self.stopping.is_set():
                # Cleared before the look, so a hand-off written after the
                # look read the table still wakes the next wait.
                w.wake.clear()
                with self.lock:
                    generation = self.generation
                    feeders_stopped = all(f.stopped for f in w.feeders)
                    w.busy, w.state, w.since = True, "looking", time.monotonic()
                counts = self._look(conn, w, look)
                if counts is None:               # stopped during the look
                    break
                progressed, owed = batch_progress(w.name, counts)
                with self.lock:
                    w.busy = False
                    for k, n in counts.items():
                        w.totals[k] = w.totals.get(k, 0) + n
                    self.scored += owed
                    if progressed:
                        self.generation += 1
                    else:
                        w.looked_at = generation
                        if self.mode == "single" and feeders_stopped:
                            log.info("[%s] finished: nothing left to do, and every stage "
                                     "feeding it had stopped", w.name)
                            w.state = "finished"
                            break
                        w.state, w.since = "idle", time.monotonic()
                        w.next_look = w.since + pause
                        self._check_drained()
                if progressed:
                    if counts.get(STATUS_DONE):
                        for v in w.fed:
                            v.wake.set()
                    pause = IDLE_BACKOFF_FIRST_S
                    continue
                # stop() sets the flag before this stage's event, and the
                # clear above wipes an event set just before it; the flag
                # stays. Checked here, no stop waits out the back-off.
                if self.stopping.is_set():
                    break
                log.debug("[%s] nothing to do; next look within %gs", w.name, pause)
                _wait_for(w.wake, pause)
                pause = min(pause * 2, IDLE_BACKOFF_MAX_S)
        except BaseException as e:
            log.exception("[%s] failed; stopping every stage after its current batch", w.name)
            with self.lock:
                if self.failure is None:
                    self.failure = (w.name, e)
                w.state = f"failed ({type(e).__name__})"
            self.stop()
        finally:
            with self.lock:
                w.stopped, w.busy = True, False
                if w.state not in ("finished",) and not w.state.startswith("failed"):
                    w.state = "stopped"
                w.since = time.monotonic()
            # A stage waiting on this one may be able to finish now.
            for v in w.fed:
                v.wake.set()
            self.changed.set()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _look(self, conn, w: _Worker, look) -> dict[str, int] | None:
        """One look: the ids query, then a batch of those rows if any. None
        if the run was told to stop meanwhile: a stop that lands during the
        look must not start a batch (one more batch of MERT after another
        stage failed, say)."""
        if look is None:
            if self.stopping.is_set():
                return None
            with self.lock:
                w.state = "in a batch"
            return w.stage.run_batch(conn, self.batch, log, only_ids=self.only_ids)
        sql, params = look
        ids = [int(r[0]) for r in conn.execute(sql, params).fetchall()]
        if not ids:
            return {}
        if self.stopping.is_set():
            return None
        with self.lock:
            w.state, w.since = f"in a batch of {len(ids)}", time.monotonic()
        return w.stage.run_batch(conn, self.batch, log, only_ids=ids)

    def _check_drained(self) -> None:
        """Called under the lock by a stage whose look found nothing to do.

        The run is drained when every stage is idle and each one's latest
        look began after the latest batch that did work. If all are idle
        but some looked before that work, they are woken to look again now
        rather than when their back-off ends.
        """
        if self.mode == "single" or self.stopping.is_set():
            return
        running = [w for w in self.workers if not w.stopped]
        if any(w.busy for w in running):
            return
        stale = [w for w in running if w.looked_at != self.generation]
        if stale:
            for w in stale:
                w.wake.set()
            return
        if self.drained_at == self.generation:
            return
        self.drained_at = self.generation
        if self.mode == "until_drained":
            self.stopping.set()
            for w in self.workers:
                w.wake.set()
        self.changed.set()


def run_concurrent(stages: list, batch: int, only_ids=None, mode: str = "single") -> int:
    """Run `stages` at once, one thread each (see "Concurrent mode" in the
    module docstring); mode is 'single', 'until_drained' or 'loop'.
    Returns the exit status: 0, 1 if a stage failed or the owed recompute
    did, 130 after Ctrl-C."""
    return _ConcurrentRun(stages, batch, only_ids, mode).run()


_POOL_SUFFIX = re.compile(r"_\d+$")


class _StageFormatter(logging.Formatter):
    """Puts the stage on every line of a concurrent run. A record's thread
    is a stage's own (named after it) or one of its run_batch pool's
    (<stage>_<n>); the main thread is the runner. A line that already opens
    with its stage's tag, as base.py's do, gets no second one."""

    def format(self, record: logging.LogRecord) -> str:
        thread = record.threadName or ""
        stage = "runner" if thread == "MainThread" else _POOL_SUFFIX.sub("", thread)
        record.stage_tag = ("" if record.getMessage().startswith(f"[{stage}]")
                            else f"[{stage}] ")
        return super().format(record)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _target.add_argument(parser)
    parser.add_argument("--batch", type=int, default=50,
                        help="max rows per stage per pass, or per batch with --concurrent "
                             "(default 50)")
    parser.add_argument("--stages", type=str,
                        default="preview,download,librosa,classify,language,fuse,youtube",
                        help="comma-separated stage names (default: all seven in order)")
    parser.add_argument("--concurrent", action="store_true",
                        help="run the stages at the same time, a thread each, each through "
                             "its own gate. Alone: every stage works until it and the "
                             "stages feeding it have nothing left, so --batch does not "
                             "bound the run. See 'Concurrent mode' above")
    looping = parser.add_mutually_exclusive_group()
    looping.add_argument("--until-drained", action="store_true",
                         help="pass after pass until one finds nothing to do, run the "
                              "owed score recompute, exit (1 if that failed)")
    looping.add_argument("--loop", action="store_true",
                         help="keep running for good (a dev DB); idle sleeps start at "
                              "--interval and double up to 10 min, work resets them")
    parser.add_argument("--interval", type=int, default=30,
                        help="--loop's first idle sleep, in seconds (default 30); "
                             "--concurrent stages back off from 5 s to 60 s instead")
    parser.add_argument("--only-ids", type=str, default=None,
                        help="file of tracks.id, one per line: advance only these "
                             "rows; a loop exits once they are idle")
    parser.add_argument("--recompute-only", action="store_true",
                        help="run only the library-wide score recompute "
                             "(activation_relative / vibe_score / mood), no stages")
    parser.add_argument("--retry-failed", choices=[*STAGE_CLASSES, "all"], default=None,
                        metavar="STAGE|all",
                        help="first re-arm the rows parked at <STAGE>_stage_error "
                             "(or at any stage's), then run as usual")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    # Before anything connects; exits here if --target prod is not set up.
    where = _target.apply(args.target)
    level = logging.DEBUG if args.verbose else logging.INFO
    if args.concurrent:
        handler = logging.StreamHandler()
        handler.setFormatter(_StageFormatter(
            f"%(asctime)s %(levelname)s target={args.target} %(name)s: "
            "%(stage_tag)s%(message)s"))
        logging.basicConfig(level=level, handlers=[handler])
    else:
        logging.basicConfig(
            level=level,
            format=f"%(asctime)s %(levelname)s target={args.target} %(name)s: %(message)s",
        )
    log.info("%s", where)

    ensure_db()
    if args.retry_failed:
        conn = get_conn()
        try:
            rearmed = rearm_failed(conn, args.retry_failed)
        finally:
            conn.close()
        log.info("--retry-failed %s: re-armed %d row(s) %s",
                 args.retry_failed, sum(rearmed.values()), rearmed)
    if args.recompute_only:
        return 0 if recompute_scores() else 1

    stages = build_stages([s.strip() for s in args.stages.split(",") if s.strip()])

    conn = get_conn()
    try:
        snap = pending_snapshot(conn, stages)
    finally:
        conn.close()
    log.info("startup, rows each stage can act on: %s", snap)

    only_ids = None
    if args.only_ids:
        only_ids = [int(x) for x in Path(args.only_ids).read_text().split() if x.strip()]
        log.info("scoped to %d ids from %s", len(only_ids), args.only_ids)

    if args.concurrent:
        # --loop over --only-ids ends once they are idle, as the passes do.
        mode = ("loop" if args.loop and only_ids is None
                else "until_drained" if args.loop or args.until_drained else "single")
        return run_concurrent(stages, args.batch, only_ids, mode)

    if not (args.loop or args.until_drained):
        processed, scored = run_pass(stages, args.batch, only_ids)
        log.info("pass done: processed=%d scored=%d", processed, scored)
        return 0 if after_pass(scored) else 1

    # Scored rows not yet folded into a recompute. Recomputing per pass
    # would rewrite ~every row each time (the mean moves), so it waits for
    # the loop to drain; see the module docstring.
    owed = 0
    idle_sleep = args.interval
    while True:
        processed, scored = run_pass(stages, args.batch, only_ids)
        owed += scored
        if processed != 0:
            idle_sleep = args.interval
            continue
        if owed > 0:
            log.info("loop drained; %d rows scored since the last recompute, "
                     "recomputing library scores", owed)
            if recompute_scores():
                owed = 0
            else:
                log.warning("score recompute owed for %d rows; retrying at the "
                            "next idle point", owed)
        if args.until_drained or only_ids is not None:
            log.info("%s; done", "scoped ids idle" if only_ids is not None else "drained")
            return 0 if owed == 0 else 1
        log.info("all stages idle; sleeping %ds", idle_sleep)
        time.sleep(idle_sleep)
        idle_sleep = min(idle_sleep * 2, max(IDLE_SLEEP_MAX_S, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
