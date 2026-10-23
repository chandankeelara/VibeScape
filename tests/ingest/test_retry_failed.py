"""run_ingest_v2.py --retry-failed: rows parked at <stage>_stage_error go
back into the pipeline. ingest_pipeline/base.py promised the flag long
before it existed (backlog 2.5)."""
import logging

import pytest

import run_ingest_v2 as runner


def add(sql, ingestion_status, **cols):
    names = ["title", "artist", "spotify_id", "ingestion_status", "vibe_score", *cols]
    values = ["T", "A", f"S{len(cols)}{ingestion_status}", ingestion_status, 0.0, *cols.values()]
    cur = sql.execute(f"INSERT INTO tracks ({', '.join(names)}) "
                      f"VALUES ({', '.join('?' * len(values))})", values)
    sql.commit()
    return int(cur.lastrowid)


def state(sql, tid, col):
    return tuple(sql.execute(f"SELECT ingestion_status, {col}, ingestion_error FROM tracks "
                             "WHERE id = ?", (tid,)).fetchone())


@pytest.fixture
def parked(sql):
    return {
        "classify": add(sql, "classify_stage_error", ml_status="failed",
                        ingestion_error="[classify] boom"),
        "preview": add(sql, "preview_stage_error", preview_status="failed",
                       ingestion_error="[preview] write failed: x"),
        "done": add(sql, "done", ml_status="done"),
    }


def test_one_stage_is_rearmed_and_nothing_else(sql, parked):
    conn = runner.get_conn()
    try:
        assert runner.rearm_failed(conn, "classify") == {"classify": 1}
    finally:
        conn.close()
    # ml_status is classify's column: the stage name and the column differ
    assert state(sql, parked["classify"], "ml_status") == ("pending", "pending", None)
    assert state(sql, parked["preview"], "preview_status") == (
        "preview_stage_error", "failed", "[preview] write failed: x")
    assert state(sql, parked["done"], "ml_status") == ("done", "done", None)


def test_all_rearms_every_stage_in_one_go(sql, parked):
    conn = runner.get_conn()
    try:
        counts = runner.rearm_failed(conn, "all")
    finally:
        conn.close()
    assert counts == {"preview": 1, "download": 0, "librosa": 0, "classify": 1,
                      "language": 0, "genre": 0, "fuse": 0, "youtube": 0}
    assert state(sql, parked["preview"], "preview_status") == ("pending", "pending", None)


def test_the_flag_rearms_then_runs_and_reports_the_count(sql, parked, monkeypatch, caplog):
    monkeypatch.setattr(runner, "build_stages", lambda names: [])
    with caplog.at_level(logging.INFO, logger="vibescape.ingest.orch"):
        assert runner.main(["--retry-failed", "all"]) == 0
    assert "re-armed 2 row(s)" in caplog.text
    assert state(sql, parked["classify"], "ml_status") == ("pending", "pending", None)


def test_an_unknown_stage_is_refused(db):
    with pytest.raises(SystemExit):
        runner.main(["--retry-failed", "nope"])
