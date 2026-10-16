"""Reads are retried like writes (ingest_pipeline/base.py execute_read).

Until 2026-10-16 only a stage's batch write was tried again on a transient
error. One HTTP 503, 429 or dropped connection on a read (a stage's fetch, a
pass's cohort query or startup counts, a concurrent stage's look) ended the
run with exit 1 and the score recompute owed. In review, against a fake
Hrana server with a simulated 8 s outage, 22 of 30 concurrent drains
survived and 30 of 30 sequential ones. A read is idempotent, so it now gets
the write's retries: 2, 4 and 8 s apart, each logged, then raised.
"""
import logging
import secrets
import sqlite3

import pytest
import requests

import run_ingest_v2 as runner
from ingest_pipeline.base import RowResult, Stage, STATUS_DONE

log = logging.getLogger("test")


class Gate(Stage):
    """A real Stage (fetch_pending, run_batch) on preview_status."""
    name = "preview"
    status_column = "preview_status"
    gate_sql = "t.preview_status = 'pending'"
    max_workers = 1

    def process_row(self, row):
        return RowResult(int(row["id"]), STATUS_DONE, {})


def add(sql, n):
    ids = [int(sql.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, "
                           "vibe_score, preview_status) VALUES ('T', 'A', ?, 'pending', 0, "
                           "'pending')", (secrets.token_hex(8),)).lastrowid) for _ in range(n)]
    sql.commit()
    return ids


@pytest.fixture
def conn(db):
    c = runner.get_conn()
    yield c
    c.close()


def test_a_fetch_that_drops_twice_is_retried_and_the_batch_runs(sql, conn, flaky_reads,
                                                                pauses, caplog):
    add(sql, 3)
    flaky = flaky_reads(conn)
    # fetch_pending logs its retries on base's own logger (it is given none)
    with caplog.at_level(logging.WARNING, logger="vibescape.ingest.base"):
        assert Gate().run_batch(flaky, 10, log)[STATUS_DONE] == 3
    assert (flaky.reads, pauses) == (3, [2, 4])
    assert "[preview] reading failed (connection reset); retry 1 of 3 in 2s" in caplog.text
    assert "retry 2 of 3 in 4s" in caplog.text


def test_a_read_that_keeps_failing_is_raised_after_its_third_retry(sql, conn, flaky_reads,
                                                                   pauses):
    add(sql, 1)
    flaky = flaky_reads(conn, fails=None)
    with pytest.raises(requests.exceptions.ConnectionError):
        Gate().fetch_pending(flaky, 10)
    assert (flaky.reads, pauses) == (4, [2, 4, 8])


def test_a_read_error_that_is_not_transient_is_raised_at_once(sql, conn, flaky_reads, pauses):
    flaky = flaky_reads(conn, error=sqlite3.OperationalError("no such column: x"), fails=None)
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        Gate().fetch_pending(flaky, 10)
    assert (flaky.reads, pauses) == (1, [])


def test_a_passs_cohort_query_and_startup_counts_are_retried(sql, conn, flaky_reads, pauses):
    ids = add(sql, 2)
    assert runner.select_cohort(flaky_reads(conn), [Gate()], 10) == ids
    assert runner.pending_snapshot(flaky_reads(conn), [Gate()]) == {"preview": 2}
    assert pauses == [2, 4, 2, 4]
