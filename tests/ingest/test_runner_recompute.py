"""scripts/run_ingest_v2.py re-runs the score recompute after a pass that
scored tracks, and only then (moved out of the import jobs 2026-10-13)."""
import run_ingest_v2 as runner


class FakeStage:
    def __init__(self, name, done):
        self.name, self.done = name, done

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
