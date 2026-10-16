"""scripts/run_ingest_v2.py --concurrent: every selected stage at once, a
thread each, each working through its own gate ("Concurrent mode" in the
runner's docstring).

The stages here are real Stage subclasses (real gate queries, run_batch and
write rules) whose process_row is the test's, on each test's own SQLite
file. Timings are bounded generously; the events make the order of what
happens deterministic, not the clock.
"""
import _thread
import itertools
import logging
import re
import secrets
import signal
import sqlite3
import threading
import time

import numpy as np
import pytest

import run_ingest_v2 as runner
from ingest_pipeline import base
from ingest_pipeline.base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, STATUS_NO_MATCH
from ingest_pipeline.fused_vector import MERT_DIM
from ingest_pipeline.stage_classify import ClassifyStage
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION
from ingest_pipeline.stage_download import DownloadStage
from ingest_pipeline.stage_language import LanguageStage
from ingest_pipeline.stage_librosa import LibrosaStage
from ingest_pipeline.stage_preview import PreviewStage

STATUS = ("preview_status", "download_status", "librosa_status", "ml_status",
          "language_status", "fuse_status", "youtube_status")
STAGE_THREAD = re.compile(rf"^({'|'.join(runner.STAGE_CLASSES)})(_\d+)?$")


def add(sql, n, **cols):
    """n pending tracks in production's shape (no column defaults, so every
    stage column NULL), with `cols` set. Returns their ids."""
    values = {c: None for c in STATUS}
    values.update(cols)
    ids = []
    for _ in range(n):
        cur = sql.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, "
                          "vibe_score) VALUES ('T', 'A', ?, 'pending', 0.0)",
                          (secrets.token_hex(8),))
        ids.append(int(cur.lastrowid))
        sql.execute(f"UPDATE tracks SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                    (*values.values(), ids[-1]))
    sql.commit()
    return ids


def column(sql, name, ids):
    marks = ",".join("?" * len(ids))
    return [r[0] for r in sql.execute(f"SELECT {name} FROM tracks WHERE id IN ({marks}) "
                                      "ORDER BY id", ids)]


def path_of(sql):
    return sql.execute("PRAGMA database_list").fetchone()[2]


def elsewhere(path, write):
    """write(conn) on a connection of its own, as the app or a tagging
    session would, from whichever thread calls this."""
    conn = sqlite3.connect(path, timeout=30)
    try:
        result = write(conn)
        conn.commit()
        return result
    finally:
        conn.close()


class Fake(Stage):
    """A stage whose per-row work is `work(track_id)` and whose result is
    `result(track_id)`, ('done', {}) unless given."""
    max_workers = 1

    def __init__(self, name, status_column, gate_sql, arms=(), work=None, result=None,
                 gate_params=(), finalizes=None):
        self.name, self.status_column, self.gate_sql = name, status_column, gate_sql
        self.gate_params, self.arms = gate_params, arms
        self.finalizes = finalizes or {}
        self.work, self.result = work, result
        self.seen = []                 # (track id, perf_counter) per row processed
        self.written = []              # perf_counter after each batch written
        self.busy_timeouts = []        # the connection's, per batch

    def process_row(self, row):
        tid = int(row["id"])
        if self.work:
            self.work(tid)
        self.seen.append((tid, time.perf_counter()))
        status, fields = self.result(tid) if self.result else (STATUS_DONE, {})
        return RowResult(tid, status, dict(fields))

    def run_batch(self, conn, limit, log, only_ids=None):
        if isinstance(conn, sqlite3.Connection):
            self.busy_timeouts.append(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        counts = super().run_batch(conn, limit, log, only_ids)
        self.written.append(time.perf_counter())
        return counts


def chain(**work):
    """librosa -> classify -> fuse on their real status columns, with gates
    that are only each column 'pending'; `work` by stage name."""
    return (Fake("librosa", "librosa_status", "t.librosa_status = 'pending'",
                 arms=("ml_status",), work=work.get("librosa")),
            Fake("classify", "ml_status", "t.ml_status = 'pending'",
                 arms=("fuse_status",), work=work.get("classify")),
            Fake("fuse", "fuse_status", "t.fuse_status = 'pending'", work=work.get("fuse")))


def bare(cls):
    """A real stage without its __init__ (which loads librosa, yt-dlp or
    MERT); its gate is class-level."""
    return object.__new__(cls)


@pytest.fixture(autouse=True)
def no_stage_thread_outlives_its_run():
    yield
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        left = [t.name for t in threading.enumerate() if STAGE_THREAD.match(t.name)]
        if not left:
            return
        time.sleep(0.05)
    pytest.fail(f"stage threads still alive after the run: {left}")


@pytest.fixture(autouse=True)
def no_write_retry_pauses(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


@pytest.fixture(autouse=True)
def watchdog():
    """A run that never ends must not hang the suite: after 30 s this
    presses Ctrl-C (a concurrent run stops on it; anywhere else it raises).
    Every test here is done in a few seconds."""
    timer = threading.Timer(30, _thread.interrupt_main)
    timer.daemon = True
    timer.start()
    yield
    timer.cancel()


@pytest.fixture(autouse=True)
def exits(monkeypatch):
    """What a second Ctrl-C would have exited the process with. Recorded
    instead, so not even the watchdog's press after a test's own can end
    pytest."""
    codes = []
    monkeypatch.setattr(runner, "_exit_now", codes.append)
    return codes


@pytest.fixture
def recomputes(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "recompute_scores", lambda: calls.append(1) or True)
    return calls


class Waits(list):
    """Every idle wait a stage made: (thread, timeout, began, ended, woken).
    asleep(name) is set once that stage has begun one."""

    def __init__(self):
        super().__init__()
        self._asleep = {}
        self._lock = threading.Lock()

    def asleep(self, name):
        with self._lock:
            return self._asleep.setdefault(name, threading.Event())


@pytest.fixture
def waits(monkeypatch):
    seen = Waits()
    real = runner._wait_for

    def recording(event, timeout):
        name = threading.current_thread().name
        began = time.perf_counter()
        seen.asleep(name).set()
        woken = real(event, timeout)
        seen.append((name, timeout, began, time.perf_counter(), woken))
        return woken
    monkeypatch.setattr(runner, "_wait_for", recording)
    return seen


@pytest.fixture
def stop_called(monkeypatch):
    """Set once the run has asked every stage to stop."""
    called = threading.Event()
    real = runner._ConcurrentRun.stop

    def stop(self):
        real(self)
        called.set()
    monkeypatch.setattr(runner._ConcurrentRun, "stop", stop)
    return called


# ------------------------------------------------------------- overlap

def two_batches_then_downstream(sql, before_first=None):
    """librosa has two batches of two rows; inside its second it waits until
    classify has been through its first (classify waits until librosa is in
    that second batch, so both are mid-batch at once). `before_first` runs
    in librosa's first batch. Returns the stages, the ids, what classify saw
    and whether librosa's waits ended in time."""
    ids = add(sql, 4, librosa_status="pending")
    in_second, b_through = threading.Event(), threading.Event()
    seen_by_b = []
    up_waited = []

    def up_work(tid):
        if tid in ids[:2] and before_first:
            before_first()
        if tid in ids[2:]:
            in_second.set()
            up_waited.append(b_through.wait(10))

    def down_work(tid):
        seen_by_b.append((tid, in_second.wait(10), len(up.written)))
        if tid == ids[1]:                        # the last row of classify's first batch
            b_through.set()
    up, down, _ = chain(librosa=up_work, classify=down_work)
    return up, down, ids, seen_by_b, up_waited


def test_the_next_stage_works_while_the_first_is_inside_its_second_batch(
        sql, monkeypatch, recomputes):
    # Not merely that the totals match: classify processes its first batch
    # while librosa is inside its second, with one batch written. Its feeder
    # running, classify waits for the hand-off with a 600 s safety timer, so
    # only the hand-off can wake it in time.
    up, down, ids, seen_by_b, up_waited = two_batches_then_downstream(sql)
    t0 = time.perf_counter()

    assert runner.run_concurrent([up, down], 2, mode="until_drained") == 0

    took = time.perf_counter() - t0
    # classify's first two rows: librosa inside its second batch, one written
    assert seen_by_b[:2] == [(ids[0], True, 1), (ids[1], True, 1)]
    assert up_waited == [True, True]
    assert sorted(t for t, _ in down.seen) == ids
    assert column(sql, "ml_status", ids) == ["done"] * 4
    gap = down.seen[0][1] - up.written[0]
    print(f"classify's first row {gap * 1000:.1f} ms after librosa's first batch was "
          f"written, while librosa was inside its second; run took {took:.2f}s")
    assert took < 10


def test_a_hand_off_wakes_the_next_stage_long_before_its_timer(
        sql, monkeypatch, recomputes, waits):
    # librosa's first batch is held until classify has looked, found nothing
    # and gone to sleep: for the feeder wait, librosa still running.
    up, down, ids, _, _ = two_batches_then_downstream(
        sql, before_first=lambda: waits.asleep("classify").wait(10))

    assert runner.run_concurrent([up, down], 2, mode="until_drained") == 0

    # The hand-off woke it within a second of librosa's first batch landing.
    first = next(w for w in waits if w[0] == "classify")
    _, timeout, began, ended, woken = first
    assert (timeout, woken) == (runner.FEEDER_WAIT_S, True)
    assert began < up.written[0] < ended < up.written[0] + 1.0
    print(f"classify woke {(ended - up.written[0]) * 1000:.1f} ms after librosa's first "
          "batch was written; its timer had 600 s to go")


def test_a_stage_whose_feeder_still_runs_waits_for_its_hand_off_not_a_timer(
        sql, monkeypatch, recomputes, waits):
    # In review a concurrent drain on a prod-shaped copy read 13 times the
    # rows a sequential one did; most of its looks were empty, many of them
    # timer looks by a stage waiting on a slower feeder (2026-10-16). Such a
    # stage now waits on its event alone, FEEDER_WAIT_S being only a safety
    # timer, while a stage with no feeder running keeps the back-off: rows
    # reach it from outside the run. classify idles between librosa's three
    # batches here; with the back-off it looked every 20-50 ms.
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 0.02)
    monkeypatch.setattr(runner, "IDLE_BACKOFF_MAX_S", 0.05)
    ids = add(sql, 6, librosa_status="pending")
    up, down, _ = chain(librosa=lambda t: time.sleep(0.15))

    assert runner.run_concurrent([up, down], 2, mode="until_drained") == 0

    assert sorted(t for t, _ in down.seen) == ids
    down_waits = [(timeout, woken) for name, timeout, _, _, woken in waits if name == "classify"]
    assert down_waits and set(down_waits) == {(runner.FEEDER_WAIT_S, True)}, down_waits
    assert len(down_waits) <= 5                 # once before each hand-off, once at the end
    up_waits = [timeout for name, timeout, *_ in waits if name == "librosa"]
    assert up_waits and max(up_waits) <= 0.05   # no feeder: the back-off


# ------------------------------------------------------------- draining

def test_until_drained_waits_out_a_slow_upstream_and_recomputes_once(
        sql, monkeypatch, recomputes, waits):
    # classify's gate is empty between librosa's batches, again and again;
    # the run must not end then.
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 0.02)
    monkeypatch.setattr(runner, "IDLE_BACKOFF_MAX_S", 0.05)
    ids = add(sql, 6, librosa_status="pending")
    up, down, _ = chain(librosa=lambda tid: time.sleep(0.15))

    t0 = time.perf_counter()
    assert runner.run_concurrent([up, down], 2, mode="until_drained") == 0
    took = time.perf_counter() - t0

    assert len(up.written) == 3 and sorted(t for t, _ in down.seen) == ids
    idle_between = [w for w in waits if w[0] == "classify" and w[2] < up.written[-1]]
    assert len(idle_between) >= 2, "classify never went idle while librosa still had work"
    assert recomputes == [1]                 # librosa is a scoring stage: once, at the end
    assert took < 0.9 + 3


def test_no_recompute_unless_a_scoring_stage_finished_rows(sql, recomputes):
    ids = add(sql, 3, preview_status="pending")
    preview = Fake("preview", "preview_status", "t.preview_status = 'pending'",
                   arms=("download_status",))
    download = Fake("download", "download_status", "t.download_status = 'pending'")
    classify = Fake("classify", "ml_status", "t.ml_status = 'pending'")   # nothing to do
    assert runner.run_concurrent([preview, download, classify], 2, mode="until_drained") == 0
    assert column(sql, "download_status", ids) == ["done"] * 3
    assert recomputes == []


def test_until_drained_exits_1_when_the_recompute_fails(sql, monkeypatch):
    add(sql, 2, librosa_status="pending")
    monkeypatch.setattr(runner, "recompute_scores", lambda: False)
    assert runner.run_concurrent(list(chain()), 5, mode="until_drained") == 1


def test_a_pass_and_a_stage_thread_count_a_batch_the_same_way():
    # One rule for both: left armed is not progress; a scoring stage's done
    # rows are owed a recompute.
    counts = {STATUS_DONE: 2, STATUS_NO_MATCH: 1, STATUS_FAILED: 1, "left_armed": 3}
    assert runner.batch_progress("classify", counts) == (4, 2)
    assert runner.batch_progress("fuse", counts) == (4, 0)
    assert runner.batch_progress("librosa", {"left_armed": 5}) == (0, 0)


def test_a_finished_track_left_armed_does_not_keep_a_concurrent_run_busy(sql, recomputes):
    # A finished track the stage fails on every time is left armed (base.py,
    # "Writing a batch"): no progress, so the run drains after one batch.
    # Counted as progress, the stage would look again at once for good; the
    # work below stops the run after five, should it ever spin.
    (tid,) = add(sql, 1, ingestion_status="done", fuse_status="pending")
    stage = Fake("fuse", "fuse_status", "t.fuse_status = 'pending'",
                 result=lambda t: (STATUS_FAILED, {}))
    run = runner._ConcurrentRun([stage], 10, None, "until_drained")
    stage.work = lambda t: run.stop() if len(stage.seen) >= 5 else None

    assert run.run() == 0
    assert len(stage.written) == 1 and [t for t, _ in stage.seen] == [tid]
    assert run.workers[0].totals["left_armed"] == 1
    assert tuple(sql.execute("SELECT ingestion_status, fuse_status FROM tracks WHERE id = ?",
                             (tid,)).fetchone()) == ("done", "pending")


def test_a_loop_recomputes_each_time_it_drains_not_per_batch(sql, monkeypatch):
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 0.02)
    monkeypatch.setattr(runner, "IDLE_BACKOFF_MAX_S", 0.05)
    ids = add(sql, 6, librosa_status="pending")
    # Batches spread over more than one of the main thread's 0.5 s ticks.
    up, down, _ = chain(librosa=lambda t: time.sleep(0.15))
    calls = []

    def recompute():
        calls.append(sorted(t for t, _ in down.seen))
        if len(calls) == 1:
            # More work arrives after the first drain: the loop keeps going.
            elsewhere(path_of(sql), lambda c: c.execute(
                "UPDATE tracks SET librosa_status = 'pending' WHERE id = ?", (ids[0],)))
        else:
            _thread.interrupt_main()             # Ctrl-C, to end the loop
        return True
    monkeypatch.setattr(runner, "recompute_scores", recompute)

    assert runner.run_concurrent([up, down], 2, mode="loop") == 130
    # once after all three batches had gone through both stages, once more
    # after the one re-armed row had
    assert calls == [ids, sorted(ids + [ids[0]])]


def test_a_failed_loop_recompute_is_retried_at_the_next_idle_look(sql, monkeypatch):
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 0.02)
    monkeypatch.setattr(runner, "IDLE_BACKOFF_MAX_S", 0.05)
    add(sql, 2, librosa_status="pending")
    calls = []

    def recompute():
        calls.append(time.perf_counter())
        if len(calls) == 1:
            return False
        _thread.interrupt_main()                 # Ctrl-C, to end the loop
        return True
    monkeypatch.setattr(runner, "recompute_scores", recompute)
    assert runner.run_concurrent(list(chain()), 5, mode="loop") == 130
    # no new work came: the retry is the next idle look, a back-off away
    assert len(calls) == 2 and calls[1] - calls[0] < 2


# ------------------------------------------------------------- single pass

def test_a_stage_whose_feeder_stops_without_handing_off_finishes_at_once(sql, monkeypatch,
                                                                         recomputes):
    # preview's batch settles every row no_match, so it hands download
    # nothing; download is asleep for 30 s by then, waiting on preview.
    # Stopping, preview wakes it, and download sees nothing will come.
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 30)
    add(sql, 2, preview_status="pending")
    preview = Fake("preview", "preview_status", "t.preview_status = 'pending'",
                   arms=("download_status",), work=lambda t: time.sleep(0.3),
                   result=lambda t: (STATUS_NO_MATCH, {}))
    download = Fake("download", "download_status", "t.download_status = 'pending'")
    t0 = time.perf_counter()
    assert runner.run_concurrent([preview, download], 10) == 0
    assert time.perf_counter() - t0 < 5 and download.seen == []


def test_a_stage_inside_a_batch_is_not_idle_though_it_looked_after_the_last_work():
    # Its last empty look began at the current generation, and its next one
    # found rows that came from outside the run (the app inserted them), so
    # no work is recorded yet. The run is not drained until that batch ends.
    run = runner._ConcurrentRun(list(chain()), 50, None, "until_drained")
    with run.lock:
        run.generation = 3
        for w in run.workers:
            w.looked_at = 3
        run.workers[0].busy = True
        run._check_drained()
    assert not run.stopping.is_set() and run.drained_at is None
    with run.lock:
        run.workers[0].busy = False
        run._check_drained()
    assert run.stopping.is_set() and run.drained_at == 3


def test_a_single_pass_takes_what_the_gates_hold_and_what_feeders_hand_on(
        sql, monkeypatch, recomputes):
    # Each stage stops at a look that finds nothing and began after every
    # stage feeding it had stopped. So librosa (no feeder) stops once its
    # gate is empty, and a row reaching its gate later is not taken;
    # classify keeps going while librosa runs, then until it too finds
    # nothing, so a row reaching its gate while it still runs is taken.
    # youtube has no feeder and nothing to do: it stops at its first look.
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 30)
    path = path_of(sql)
    ids = add(sql, 4, librosa_status="pending")
    late = {}

    def alive(name):
        return any(t.name == name and t.is_alive() for t in threading.enumerate())

    def up_work(tid):
        if tid == ids[2]:
            late["youtube_alive_in_second_batch"] = alive("youtube")
        else:
            time.sleep(0.2)

    def down_work(tid):
        if tid != ids[3]:
            return
        deadline = time.monotonic() + 10
        while alive("librosa") and time.monotonic() < deadline:
            time.sleep(0.01)
        late["for_librosa"], = elsewhere(path, lambda c: add(c, 1, librosa_status="pending"))
        late["for_classify"], = elsewhere(path, lambda c: add(c, 1, ml_status="pending"))
    up, down, _ = chain(librosa=up_work, classify=down_work)
    lone = Fake("youtube", "youtube_status", "t.youtube_status = 'pending'")

    assert runner.run_concurrent([up, down, lone], 2) == 0

    assert late["youtube_alive_in_second_batch"] is False
    assert sorted(t for t, _ in up.seen) == ids
    assert sorted(t for t, _ in down.seen) == sorted(ids + [late["for_classify"]])
    assert column(sql, "librosa_status", [late["for_librosa"]]) == ["pending"]
    assert lone.seen == [] and recomputes == [1]


# ------------------------------------------------------------- feeders

def test_who_feeds_whom_and_nothing_across_a_gap():
    run = runner._ConcurrentRun([bare(c) for c in runner.STAGE_CLASSES.values()], 50, None,
                                "single")
    fed = {w.name: [v.name for v in w.fed] for w in run.workers}
    assert fed == {"preview": ["download", "language"], "download": ["librosa", "language"],
                   "librosa": ["classify"], "classify": ["fuse"], "language": [],
                   "fuse": ["youtube"], "youtube": []}
    chain = ["preview", "download", "librosa", "classify", "fuse", "youtube"]
    upstream = {w.name: {a.name for a in w.ancestors} for w in run.workers}
    assert upstream == {**{s: set(chain[:i]) for i, s in enumerate(chain)},
                        "language": {"preview", "download"}}
    gap = runner._ConcurrentRun([bare(PreviewStage), bare(runner.STAGE_CLASSES["fuse"])], 50,
                                None, "single")
    assert [(w.name, w.feeders, w.fed, w.ancestors) for w in gap.workers] == [
        ("preview", [], [], []), ("fuse", [], [], [])]


def test_a_stage_upstream_of_another_never_shares_a_row_with_it(sql):
    # Every row state from a small matrix: each stage column NULL, 'pending'
    # or 'done', ingestion_status pending or done (4,374 rows). With only a
    # stage's direct feeders left out of its look, preview and librosa,
    # preview and classify, preview and fuse, download and fuse, and librosa
    # and fuse could take one row at once (found in review by enumerating
    # 983,040 states). With every stage upstream of it left out, two stages
    # one of which is upstream of the other never do.
    cols = ("preview_status", "download_status", "librosa_status", "ml_status",
            "language_status", "fuse_status", "youtube_status")
    states = [(ingestion, *combo) for ingestion in ("pending", "done")
              for combo in itertools.product((None, "pending", "done"), repeat=len(cols))]
    sql.executemany(
        f"INSERT INTO tracks (title, artist, spotify_id, vibe_score, preview_url, audio_path, "
        f"ingestion_status, {', '.join(cols)}) "
        f"VALUES ('T', 'A', ?, 0, 'https://p', 'a.m4a', ?{', ?' * len(cols)})",
        [(f"s{i}", *state) for i, state in enumerate(states)])
    sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, model_version) "
                "SELECT id, ?, ? FROM tracks WHERE ml_status = 'done'",
                (np.ones(MERT_DIM, np.float32).tobytes(), CURRENT_MERT_VERSION))
    sql.commit()
    run = runner._ConcurrentRun([bare(c) for c in runner.STAGE_CLASSES.values()], 10 ** 6,
                                None, "single")

    looks = {w.name: {r[0] for r in sql.execute(*run._look_for(w))} for w in run.workers}
    gates = {w.name: {r[0] for r in sql.execute(*runner._look_query(w.stage, [], 10 ** 6, None))}
             for w in run.workers}

    # Upstream, spelled out here rather than read off the run under test.
    chain = ["preview", "download", "librosa", "classify", "fuse", "youtube"]
    upstream = {**{s: chain[:i] for i, s in enumerate(chain)},
                "language": ["preview", "download"]}
    shared = {(a, b): len(looks[a] & looks[b])
              for b, above in upstream.items() for a in above if looks[a] & looks[b]}
    assert shared == {}
    # The matrix does put rows in both gates of each pair review found.
    for a, b in [("preview", "librosa"), ("preview", "classify"), ("preview", "fuse"),
                 ("download", "fuse"), ("librosa", "fuse")]:
        assert gates[a] & gates[b], (a, b)


def test_a_stage_given_twice_or_a_circle_of_feeders_is_refused():
    with pytest.raises(SystemExit, match="given twice"):
        runner._ConcurrentRun([bare(PreviewStage), bare(PreviewStage)], 50, None, "single")
    a = Fake("preview", "preview_status", "1", arms=("download_status",))
    b = Fake("download", "download_status", "1", arms=("preview_status",))
    with pytest.raises(SystemExit, match="circle"):
        runner._ConcurrentRun([a, b], 50, None, "single")


def test_a_row_its_feeder_can_still_act_on_never_reaches_a_stages_batch(sql, recomputes):
    # Gates that overlap (these two do, by construction): the second row is
    # in both. Only the look leaves it to the feeder; run_batch's
    # fetch_pending re-applies the stage's own gate, not the exclusion, so
    # the guarantee rests on _look handing run_batch exactly the ids it
    # found. The feeder holds that row until the stage's first batch is
    # written, so a batch that took it would have done so first.
    only_down, both = (add(sql, 1, librosa_status="done", ml_status="pending")
                       + add(sql, 1, librosa_status="pending", ml_status="pending"))
    first_written = threading.Event()

    def up_work(tid):
        first_written.wait(10)
    up = Fake("librosa", "librosa_status", "t.librosa_status = 'pending'",
              arms=("ml_status",), work=up_work)
    down = Fake("classify", "ml_status", "t.ml_status = 'pending'")
    real_run_batch = down.run_batch

    def run_batch(conn, limit, log, only_ids=None):
        counts = real_run_batch(conn, limit, log, only_ids)
        first_written.set()
        return counts
    down.run_batch = run_batch

    assert runner.run_concurrent([up, down], 10, mode="until_drained") == 0

    assert [t for t, _ in down.seen] == [only_down, both] and len(down.written) == 2
    assert down.seen[1][1] > up.written[0]           # after its feeder wrote it


def test_classify_never_takes_a_row_librosa_can_still_act_on(sql, recomputes):
    # A schema.sql database (a dev checkout, every test) defaults ml_status
    # to 'pending', so a downloaded row is armed for classify before librosa
    # has run. Classify's gate now requires librosa_status 'done', so even
    # with librosa not in the run, where no feeder holds the row back, a
    # pass and a concurrent run alike leave it; MERT on it first would be
    # wasted once librosa re-armed it.
    (tid,) = add(sql, 1, preview_status="done", download_status="done",
                 librosa_status="pending", ml_status="pending", audio_path="data/audio/x.m4a")

    def classify():
        return Fake("classify", "ml_status", ClassifyStage.gate_sql, arms=ClassifyStage.arms)
    alone = classify()
    assert runner.run_pass([alone], 10) == (0, 0)
    assert runner.run_concurrent([alone], 10) == 0
    assert alone.seen == []

    librosa = Fake("librosa", "librosa_status", LibrosaStage.gate_sql, arms=LibrosaStage.arms,
                   work=lambda t: time.sleep(0.3))
    after = classify()
    assert runner.run_concurrent([librosa, after], 10) == 0
    assert [t for t, _ in after.seen] == [tid]                # once,
    assert after.seen[0][1] > librosa.written[0]              # after librosa's write


def test_language_waits_for_preview_and_download_so_no_preview_rows_keep_null(sql, recomputes):
    # Production rows start with every stage column NULL, in preview's gate
    # and language's at once. A row preview settles 'no_preview' must keep
    # language_status NULL, as it does in a sequential pass.
    found, missing = add(sql, 2)

    def preview_result(t):
        if t == missing:
            return STATUS_NO_MATCH, {}
        return STATUS_DONE, {"preview_url": "https://p.example/x.m4a"}
    preview = Fake("preview", "preview_status", PreviewStage.gate_sql, arms=PreviewStage.arms,
                   finalizes=PreviewStage.finalizes, result=preview_result,
                   work=lambda t: time.sleep(0.2))
    download = Fake("download", "download_status", DownloadStage.gate_sql,
                    arms=DownloadStage.arms, result=lambda t: (STATUS_DONE, {"audio_path": "a"}))
    language = LanguageStage()

    assert runner.run_concurrent([preview, download, language], 10) == 0

    rows = {r[0]: tuple(r[1:]) for r in sql.execute(
        "SELECT id, ingestion_status, language_status FROM tracks")}
    assert rows == {found: ("pending", "pending"), missing: ("no_preview", None)}


def test_a_feeder_gate_that_is_null_for_a_row_does_not_hide_it(sql, recomputes):
    # A row with ingestion_status NULL: language's gate takes it, and
    # preview's is NULL rather than false for it (NULL = 'pending' is NULL).
    # NOT NULL is NULL, which would leave the row to no one; a pass gives it
    # to language.
    (tid,) = add(sql, 1, ingestion_status=None)
    assert sql.execute(f"SELECT ({PreviewStage.gate_sql}) FROM tracks t WHERE id = ?",
                       (tid,)).fetchone()[0] is None
    stages = [bare(PreviewStage), bare(DownloadStage), LanguageStage()]
    assert runner.run_concurrent(stages, 10) == 0
    assert sql.execute("SELECT language_status FROM tracks WHERE id = ?",
                       (tid,)).fetchone()[0] == "pending"


def test_an_idle_look_is_one_ids_only_query_for_all_seven_real_gates(sql, monkeypatch,
                                                                     recomputes):
    # What an idle stage costs per look, on Turso billed by rows read: one
    # ids-only query, no fetch and no language waiting count.
    statements = []
    real = runner._concurrent_conn

    def traced():
        conn = real()
        name = threading.current_thread().name
        conn.set_trace_callback(lambda s: statements.append((name, s)))
        return conn
    monkeypatch.setattr(runner, "_concurrent_conn", traced)
    add(sql, 3, ingestion_status="done", **{c: "done" for c in STATUS})

    assert runner.run_concurrent([bare(c) for c in runner.STAGE_CLASSES.values()], 50) == 0

    looks = [(n, s) for n, s in statements if n != "MainThread" and not s.startswith("PRAGMA")]
    assert {n for n, _ in looks} == set(runner.STAGE_CLASSES)
    assert all(s.startswith("SELECT t.id FROM tracks t WHERE") for _, s in looks), looks


def test_a_pass_and_a_concurrent_look_ask_for_ids_the_same_way(sql):
    # Both are lowest_ids_query, so a rule about which rows a run takes
    # (backlog 3.12's fix, say) is written once: for one stage, a pass's
    # cohort query is that stage's look, word for word.
    sent = []

    class Spy:
        def execute(self, q, params=()):
            sent.append((q, tuple(params)))
            return sql.execute(q, params)

    for cls in runner.STAGE_CLASSES.values():
        stage = bare(cls)
        sent.clear()
        runner.select_cohort(Spy(), [stage], 7, only_ids=[1, 2])
        assert sent == [runner._look_query(stage, [], 7, [1, 2])], cls.__name__


# ------------------------------------------------------------- failure, Ctrl-C

def test_one_failing_stage_stops_the_others_after_their_batch(sql, monkeypatch, recomputes,
                                                              caplog):
    # A column the database lacks: base.py raises with nothing parked, and
    # it escapes the stage's thread. youtube is inside its first of two
    # batches then; fuse is asleep with 30 s to go.
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 30)
    boom_ids = add(sql, 1, preview_status="pending")
    slow_ids = add(sql, 4, youtube_status="pending")
    slow_started = threading.Event()
    boom = Fake("preview", "preview_status", "t.preview_status = 'pending'",
                work=lambda t: slow_started.wait(10),
                result=lambda t: (STATUS_DONE, {"no_such_column": 1}))
    slow = Fake("youtube", "youtube_status", "t.youtube_status = 'pending'",
                work=lambda t: (slow_started.set(), time.sleep(0.5)))
    idle = Fake("fuse", "fuse_status", "t.fuse_status = 'pending'")
    t0 = time.perf_counter()

    with caplog.at_level(logging.INFO, logger="vibescape.ingest.orch"):
        assert runner.run_concurrent([boom, slow, idle], 2, mode="until_drained") == 1

    took = time.perf_counter() - t0
    assert took < 5                                        # fuse was woken, not waited out
    # youtube's batch landed, and it started no other
    assert column(sql, "youtube_status", slow_ids) == ["done", "done", "pending", "pending"]
    assert column(sql, "preview_status", boom_ids) == ["pending"]
    assert idle.seen == [] and recomputes == []
    assert "[preview] failed; stopping every stage after its current batch" in caplog.text
    assert "no_such_column" in caplog.text
    assert "the run stopped because [preview] failed" in caplog.text


def test_a_stage_that_cannot_connect_stops_the_run(sql, monkeypatch, recomputes):
    real = runner._concurrent_conn

    def refuse():
        if threading.current_thread().name == "classify":
            raise RuntimeError("no connection for classify")
        return real()
    monkeypatch.setattr(runner, "_concurrent_conn", refuse)
    add(sql, 1, librosa_status="pending")
    assert runner.run_concurrent(list(chain()), 5, mode="loop") == 1


def test_a_look_that_drops_twice_is_retried_and_the_run_drains(sql, monkeypatch, recomputes,
                                                               flaky_reads, pauses, caplog):
    # One dropped connection or HTTP 503 on a look used to end the run with
    # exit 1 (only writes were retried). The look is the stage's first read.
    ids = add(sql, 3, preview_status="pending")
    stage = Fake("preview", "preview_status", "t.preview_status = 'pending'")
    real = runner._concurrent_conn
    monkeypatch.setattr(runner, "_concurrent_conn", lambda: flaky_reads(real()))
    with caplog.at_level(logging.INFO, logger="vibescape.ingest.orch"):
        assert runner.run_concurrent([stage], 10, mode="until_drained") == 0
    assert column(sql, "preview_status", ids) == ["done"] * 3
    assert pauses == [2, 4] and "[preview] reading failed" in caplog.text


def test_a_look_that_keeps_failing_stops_the_run_after_its_third_retry(sql, monkeypatch,
                                                                       recomputes, flaky_reads,
                                                                       pauses):
    add(sql, 1, preview_status="pending")
    stage = Fake("preview", "preview_status", "t.preview_status = 'pending'")
    conns = []
    real = runner._concurrent_conn

    def always_failing():
        conns.append(flaky_reads(real(), fails=None))
        return conns[-1]
    monkeypatch.setattr(runner, "_concurrent_conn", always_failing)
    assert runner.run_concurrent([stage], 10, mode="until_drained") == 1
    assert (conns[0].reads, pauses, stage.seen) == (4, [2, 4, 8], [])


def test_ctrl_c_stops_every_stage_after_its_current_batch(sql, recomputes, stop_called):
    ids = add(sql, 4, librosa_status="pending")
    in_batch, release = threading.Event(), threading.Event()
    up, down, _ = chain(librosa=lambda t: (in_batch.set(), release.wait(10)))

    def press_ctrl_c():
        in_batch.wait(10)
        _thread.interrupt_main()
        stop_called.wait(10)
        release.set()
    threading.Thread(target=press_ctrl_c, daemon=True).start()

    assert runner.run_concurrent([up, down], 2, mode="loop") == 130
    # the batch it was in landed; the second batch was never started
    assert sorted(t for t, _ in up.seen) == ids[:2]
    assert column(sql, "librosa_status", ids) == ["done", "done", "pending", "pending"]


def test_a_ctrl_c_as_the_last_stage_ends_is_still_logged(sql, monkeypatch, recomputes, caplog):
    # The press comes inside the only batch; the stage then finishes and the
    # run is over before the main thread's next tick (made long here, so only
    # the stage's own exit wakes it). It used to return without a word, the
    # press showing only in the exit status.
    monkeypatch.setattr(runner, "_SUPERVISE_TICK_S", 30)
    add(sql, 1, preview_status="pending")
    stage = Fake("preview", "preview_status", "t.preview_status = 'pending'")
    run = runner._ConcurrentRun([stage], 10, None, "single")
    stage.work = lambda t: run._on_sigint(signal.SIGINT, None)
    with caplog.at_level(logging.INFO, logger="vibescape.ingest.orch"):
        assert run.run() == 130
    assert "interrupted: every stage stops after its current batch" in caplog.text


def test_ctrl_c_ends_the_run_even_if_a_stage_thread_died_unmarked(sql, monkeypatch,
                                                                  recomputes, exits):
    # _work marks its stage stopped in a finally, so this cannot happen
    # today; if it ever did, the run must still end on one Ctrl-C.
    real = runner._ConcurrentRun._work
    dying = threading.Event()

    def work(self, w):
        if w.name == "classify":
            dying.set()
            raise RuntimeError("died before its try")
        real(self, w)
    monkeypatch.setattr(runner._ConcurrentRun, "_work", work)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)   # expected
    add(sql, 1, librosa_status="pending")

    def press_ctrl_c():
        dying.wait(10)
        deadline = time.monotonic() + 10
        while any(t.name == "classify" for t in threading.enumerate()) \
                and time.monotonic() < deadline:
            time.sleep(0.01)
        _thread.interrupt_main()
    threading.Thread(target=press_ctrl_c, daemon=True).start()
    t0 = time.perf_counter()

    assert runner.run_concurrent(list(chain()), 5, mode="loop") == 130
    assert time.perf_counter() - t0 < 5 and exits == []


def test_a_second_ctrl_c_exits_without_waiting_for_the_batch(sql, recomputes, stop_called,
                                                             exits):
    add(sql, 2, librosa_status="pending")
    in_batch, release = threading.Event(), threading.Event()
    up, down, _ = chain(librosa=lambda t: (in_batch.set(), release.wait(10)))

    def press_ctrl_c_twice():
        in_batch.wait(10)
        _thread.interrupt_main()
        stop_called.wait(10)
        _thread.interrupt_main()
    threading.Thread(target=press_ctrl_c_twice, daemon=True).start()

    try:
        assert runner.run_concurrent([up, down], 2, mode="loop") == 130
        assert exits == [130]
        assert any(t.name == "librosa" for t in threading.enumerate())   # left mid-batch
    finally:
        release.set()                       # let it finish, so it outlives no test


class StopBeforeClear(threading.Event):
    """A stage's event whose first clear() comes right after the run's
    stop(): the stop lands between the loop's check of the flag and its
    clear of the event, which wipes the event stop() just set."""

    def __init__(self, run):
        super().__init__()
        self.run, self.fired = run, False

    def clear(self):
        if not self.fired:
            self.fired = True
            self.run.stop()
        super().clear()


def test_a_stop_just_before_a_stage_clears_its_event_is_not_lost(sql, monkeypatch, recomputes):
    # The look finds nothing, so the stage goes to sleep for its back-off:
    # 30 s here, with nothing left to wake it but the flag.
    monkeypatch.setattr(runner, "IDLE_BACKOFF_FIRST_S", 30)
    stage = Fake("preview", "preview_status", "t.preview_status = 'pending'")
    run = runner._ConcurrentRun([stage], 10, None, "loop")
    run.workers[0].wake = StopBeforeClear(run)
    t0 = time.perf_counter()
    assert run.run() == 0
    assert time.perf_counter() - t0 < 2 and run.workers[0].wake.fired


class StopDuringLook:
    """A stage's connection that asks the run to stop as it runs the stage's
    first look, the ids query: stop() lands during the look."""

    def __init__(self, conn, run):
        self._conn, self._run, self.fired = conn, run, False

    def execute(self, sql, params=()):
        if not self.fired and sql.startswith("SELECT t.id FROM tracks t"):
            self.fired = True
            self._run.stop()
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_stop_during_a_look_starts_no_batch(sql, monkeypatch, recomputes):
    # Rows in the gate, so the look finds work; the stop came first.
    ids = add(sql, 3, preview_status="pending")
    stage = Fake("preview", "preview_status", "t.preview_status = 'pending'")
    run = runner._ConcurrentRun([stage], 10, None, "until_drained")
    conns = []
    real = runner._concurrent_conn

    def stopping_conn():
        conns.append(StopDuringLook(real(), run))
        return conns[-1]
    monkeypatch.setattr(runner, "_concurrent_conn", stopping_conn)
    assert run.run() == 0
    assert conns[0].fired and stage.seen == [] and stage.written == []
    assert column(sql, "preview_status", ids) == ["pending"] * 3


# ------------------------------------------------------------- idle back-off

def test_an_idle_stage_backs_off_from_5_s_to_60_s_and_work_resets_it(sql, monkeypatch):
    path = path_of(sql)
    timeouts = []

    class Enough(Exception):
        pass

    def fake_wait(event, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 6:              # a row arrives while it sleeps
            elsewhere(path, lambda c: add(c, 1, preview_status="pending"))
        if len(timeouts) == 9:
            raise Enough
        return False
    monkeypatch.setattr(runner, "_wait_for", fake_wait)
    stage = Fake("preview", "preview_status", "t.preview_status = 'pending'")

    assert runner.run_concurrent([stage], 10, mode="loop") == 1    # Enough stops it
    assert timeouts == [5, 10, 20, 40, 60, 60, 5, 10, 20]
    assert len(stage.seen) == 1


# ------------------------------------------------------------- SQLite

def test_two_stage_threads_write_at_once_and_wait_out_a_read_held_open(db, sql, recomputes):
    # The file stays in rollback-journal mode: WAL would stay with it, and a
    # backup copying the .db alone then misses what is still in the -wal
    # (db_client._SQLITE_LOCK_WAIT_S). So a COMMIT waits for any connection
    # holding a read open, here `reader` for 1.5 s from inside the first
    # batch, and db_client's 30 s lock wait outlasts it. Short batches do not
    # lock each other out (measured 2026-10-16).
    a_ids = add(sql, 40, preview_status="pending")
    b_ids = add(sql, 40, youtube_status="pending")
    reader = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    holding, released = threading.Event(), []

    def let_go():
        released.append(time.perf_counter())
        reader.execute("COMMIT")

    def hold_a_read_open(tid):
        if not holding.is_set():
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM tracks").fetchall()
            threading.Timer(1.5, let_go).start()
            holding.set()
        time.sleep(0.005)
    a = Fake("preview", "preview_status", "t.preview_status = 'pending'", work=hold_a_read_open)
    b = Fake("youtube", "youtube_status", "t.youtube_status = 'pending'",
             work=lambda t: (holding.wait(10), time.sleep(0.005)))
    try:
        assert runner.run_concurrent([a, b], 5) == 0
    finally:
        deadline = time.monotonic() + 5
        while not released and time.monotonic() < deadline:
            time.sleep(0.01)
        reader.close()

    assert column(sql, "preview_status", a_ids) == ["done"] * 40
    assert column(sql, "youtube_status", b_ids) == ["done"] * 40
    # the first commits waited for the reader to let go, and nothing failed
    assert a.written[0] > released[0] and b.written[0] > released[0]
    # both were writing during the same stretch, not one after the other
    assert a.written[0] < b.written[-1] and b.written[0] < a.written[-1]
    assert set(a.busy_timeouts + b.busy_timeouts) == {30_000}
    assert sql.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


# ------------------------------------------------------------- CLI, logging

def test_main_runs_concurrently_and_only_ids_restricts_every_stage(sql, monkeypatch, recomputes,
                                                                   tmp_path):
    ids = add(sql, 6, librosa_status="pending")
    scope = tmp_path / "ids.txt"
    scope.write_text("\n".join(str(i) for i in ids[:3]))
    stages = list(chain())
    monkeypatch.setattr(runner, "build_stages", lambda names: stages)

    assert runner.main(["--concurrent", "--loop", "--batch", "2", "--only-ids", str(scope)]) == 0

    assert column(sql, "ml_status", ids) == ["done"] * 3 + [None] * 3
    assert column(sql, "librosa_status", ids) == ["done"] * 3 + ["pending"] * 3


def test_the_summary_says_what_each_stage_has_done_and_is_doing(sql, monkeypatch, recomputes,
                                                                caplog):
    monkeypatch.setattr(runner, "SUMMARY_EVERY_S", 0.2)
    add(sql, 2, librosa_status="pending")
    up, down, _ = chain(librosa=lambda t: time.sleep(0.4))
    with caplog.at_level(logging.INFO, logger="vibescape.ingest.orch"):
        assert runner.run_concurrent([up, down], 2) == 0
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("summary")]
    assert any("librosa 0/0/0, in a batch of 2 for" in m for m in lines), lines
    assert "librosa 2/0/0, finished" in lines[-1] and "classify 2/0/0, finished" in lines[-1]


@pytest.mark.parametrize("thread,message,line", [
    ("preview", "[preview] processing 3 rows", "[preview] processing 3 rows"),
    ("download_3", "download failed for X", "[download] download failed for X"),
    ("classify", "row 7 retried without ['apple_id']", "[classify] row 7 retried"),
    ("MainThread", "summary, done/no_match/failed: ...", "[runner] summary"),
])
def test_every_line_of_a_concurrent_run_names_its_stage(thread, message, line):
    formatter = runner._StageFormatter("%(stage_tag)s%(message)s")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, message, None, None)
    record.threadName = thread
    assert formatter.format(record).startswith(line)


def test_a_line_logged_inside_a_batch_names_its_stage(sql, recomputes):
    # process_row runs on run_batch's pool, whose threads are named after
    # the stage; a provider's or db_client's warning comes from there.
    (tid,) = add(sql, 1, preview_status="pending")
    lines = []
    handler = logging.Handler()
    handler.setFormatter(runner._StageFormatter("%(stage_tag)s%(message)s"))
    handler.emit = lambda record: lines.append(handler.format(record))
    logger = logging.getLogger("vibescape.test.concurrent")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        stage = Fake("preview", "preview_status", "t.preview_status = 'pending'",
                     work=lambda t: logger.info("looking up row %d", t))
        assert runner.run_concurrent([stage], 5) == 0
    finally:
        logger.removeHandler(handler)
    assert lines == [f"[preview] looking up row {tid}"]
