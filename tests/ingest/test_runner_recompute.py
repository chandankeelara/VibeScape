"""scripts/run_ingest_v2.py re-runs the score recompute after a pass that
scored tracks, and only then (moved out of the import jobs 2026-10-13); and
how its loops end or idle."""
import pytest

import run_ingest_v2 as runner


class FakeStage:
    def __init__(self, name, done):
        self.name, self.done = name, done

    def fetch_pending(self, conn, limit, only_ids=None):
        # The pass's cohort is built from the stages' own gates.
        return conn.execute("SELECT id FROM tracks WHERE ingestion_status = 'pending' "
                            "LIMIT ?", (limit,)).fetchall()

    def run_batch(self, conn, batch, log, only_ids=None):
        return {"done": self.done, "no_match": 0, "failed": 1}


def test_only_librosa_and_classify_count_as_scoring(sql):
    sql.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, vibe_score) "
                "VALUES ('P', 'A', 'PENDING1', 'pending', 50)")
    sql.commit()
    stages = [FakeStage("preview", 4), FakeStage("librosa", 2), FakeStage("classify", 1),
              FakeStage("fuse", 5), FakeStage("youtube", 3)]
    processed, scored = runner.run_pass(stages, 10)
    assert (processed, scored) == (15 + 5, 3)


def test_a_pass_with_no_cohort_scores_nothing(db):
    assert runner.run_pass([FakeStage("librosa", 2)], 10) == (0, 0)


def test_recompute_runs_only_after_a_scoring_pass(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "recompute_scores", lambda: calls.append(1) or True)
    assert runner.after_pass(0) is True and calls == []
    assert runner.after_pass(3) is True and calls == [1]


def test_recompute_only_writes_scores_and_builds_no_stage(sql, monkeypatch):
    sql.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, vibe_score, "
                "tempo, energy_mean) VALUES ('T', 'A', 'S1', 'done', 50, 120.0, 0.1)")
    sql.commit()

    def no_stages(names):
        raise AssertionError("--recompute-only built stages")
    monkeypatch.setattr(runner, "build_stages", no_stages)

    assert runner.main(["--recompute-only"]) == 0
    row = sql.execute("SELECT activation_relative, mood FROM tracks WHERE spotify_id='S1'").fetchone()
    assert row[0] == 50.0 and row[1] is not None   # a lone track sits at the mean


# ------------------------------------------------------------------ --loop
#
# A loop recomputes ONCE when it drains, not after every scoring pass: each
# newly scored track moves the catalogue mean, so a per-pass recompute would
# rewrite ~every row every pass (20 full recomputes to drain 1,000 tracks at
# --batch 50, instead of one).

class Idle(Exception):
    """Raised by the fake sleep to stop an otherwise endless --loop."""


def drive_loop(monkeypatch, passes, recompute_results=(), argv=("--loop",), sleeps_before_stop=1,
               slept=None):
    """Run main() in --loop over scripted (processed, scored) passes.

    Returns (recompute call count, main's return value or 'stopped'). The
    sleeps' durations go into `slept` if given."""
    passes = list(passes)
    results = list(recompute_results)
    calls = []

    def fake_run_pass(stages, batch, only_ids=None):
        return passes.pop(0) if passes else (0, 0)

    def fake_recompute():
        calls.append(1)
        return results.pop(0) if results else True

    slept = [] if slept is None else slept

    def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= sleeps_before_stop:
            raise Idle

    monkeypatch.setattr(runner, "build_stages", lambda names: [])
    monkeypatch.setattr(runner, "run_pass", fake_run_pass)
    monkeypatch.setattr(runner, "recompute_scores", fake_recompute)
    monkeypatch.setattr(runner.time, "sleep", fake_sleep)
    try:
        rc = runner.main(list(argv))
    except Idle:
        rc = "stopped"
    return len(calls), rc


def test_a_loop_recomputes_once_when_it_drains(db, monkeypatch):
    passes = [(50, 50), (50, 40), (50, 0), (30, 25), (0, 0)]
    assert drive_loop(monkeypatch, passes) == (1, "stopped")


def test_an_idle_loop_with_nothing_scored_never_recomputes(db, monkeypatch):
    assert drive_loop(monkeypatch, [(20, 0), (0, 0)], sleeps_before_stop=3) == (0, "stopped")


def test_an_only_ids_loop_recomputes_before_exiting(db, monkeypatch, tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("1\n2\n")
    n, rc = drive_loop(monkeypatch, [(10, 2), (10, 1), (0, 0)],
                       argv=("--loop", "--only-ids", str(ids)))
    assert (n, rc) == (1, 0)


def test_a_failed_recompute_is_retried_at_the_next_idle_point(db, monkeypatch):
    # scores, drains (recompute fails), idles again (retry succeeds), idles a
    # third time with nothing owed.
    n, rc = drive_loop(monkeypatch, [(50, 50), (0, 0), (0, 0), (0, 0)],
                       recompute_results=(False, True), sleeps_before_stop=3)
    assert (n, rc) == (2, "stopped")


# ------------------------------------------------- --until-drained, backoff
#
# A forgotten --loop on prod scans `tracks` every idle pass for good, and
# Turso bills rows read. --until-drained ends; --loop backs off.

def test_until_drained_recomputes_once_and_exits_without_sleeping(db, monkeypatch):
    slept = []
    assert drive_loop(monkeypatch, [(50, 50), (50, 10), (0, 0)], argv=("--until-drained",),
                      slept=slept) == (1, 0)
    assert slept == []


def test_until_drained_exits_1_when_the_owed_recompute_fails(db, monkeypatch):
    assert drive_loop(monkeypatch, [(5, 5), (0, 0)], recompute_results=(False,),
                      argv=("--until-drained",)) == (1, 1)


def test_an_idle_loop_backs_off_to_ten_minutes_and_work_resets_it(db, monkeypatch):
    slept = []
    passes = [(0, 0)] * 7 + [(3, 0)]          # then idle again
    drive_loop(monkeypatch, passes, argv=("--loop", "--interval", "30"),
               sleeps_before_stop=9, slept=slept)
    assert slept == [30, 60, 120, 240, 480, 600, 600, 30, 60]


def test_loop_and_until_drained_are_exclusive(db):
    with pytest.raises(SystemExit):
        runner.main(["--loop", "--until-drained"])
