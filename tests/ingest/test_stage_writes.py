"""How a stage writes its batch (ingest_pipeline/base.py, "Writing a batch").

Until 2026-10-15 Stage.run_batch UPDATEd row by row and committed after the
loop: one row that raised aborted it once the GPU work was done, SQLite
rolled the whole batch back, and Turso, which commits per statement, kept
whatever came before the bad row. The first fix then rewrote every row
alone after a failed batch and parked any row whose rewrite failed, for any
reason: a timeout parked good rows, and a schema gap parked them all.
"""
import logging
import sqlite3

import numpy as np
import pytest
import requests

import run_ingest_v2 as runner
from ingest_pipeline import base
from ingest_pipeline.base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, id_filter
from ingest_pipeline import fused_vector as fv
from ingest_pipeline.fused_vector import FUSED_COLUMN, FUSED_DIM, MERT_DIM
from ingest_pipeline.stage_classify import ClassifyStage
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION, FuseStage

log = logging.getLogger("test")


def add_rows(sql, n):
    ids = []
    for i in range(n):
        cur = sql.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, "
                          "vibe_score) VALUES (?, 'A', ?, 'pending', 50)", (f"T{i}", f"S{i}"))
        ids.append(int(cur.lastrowid))
    sql.commit()
    return ids


class Scripted(Stage):
    """Returns whatever fields the test scripted for each row."""
    name = "scripted"
    status_column = "preview_status"
    arms = ("download_status",)
    max_workers = 1

    def __init__(self, fields):
        self.fields = fields                     # track id -> fields

    def fetch_pending(self, conn, limit, only_ids=None):
        idf, idp = id_filter(only_ids)
        return conn.execute(f"SELECT id FROM tracks WHERE ingestion_status = 'pending' {idf}"
                            "ORDER BY id LIMIT ?", (*idp, limit)).fetchall()

    def process_row(self, row):
        return RowResult(int(row["id"]), STATUS_DONE, dict(self.fields[int(row["id"])]))


class Refuse(Stage):
    """A stage that runs on finished tracks, as fuse does after a language
    correction: its gate is its own column, whatever ingestion_status says."""
    name = "fuse"
    status_column = "fuse_status"
    arms = ("youtube_status",)
    max_workers = 1

    def __init__(self, results):
        self.results = results                   # track id -> (status, fields, error)

    def fetch_pending(self, conn, limit, only_ids=None):
        return conn.execute("SELECT id FROM tracks WHERE fuse_status = 'pending' "
                            "ORDER BY id LIMIT ?", (limit,)).fetchall()

    def process_row(self, row):
        status, fields, error = self.results[int(row["id"])]
        return RowResult(int(row["id"]), status, dict(fields), error)


def finished(sql, n):
    """n finished tracks re-armed for fuse, as a language correction leaves them."""
    ids = add_rows(sql, n)
    sql.executemany("UPDATE tracks SET ingestion_status = 'done', fuse_status = 'pending' "
                    "WHERE id = ?", [(i,) for i in ids])
    sql.commit()
    return ids


@pytest.fixture
def conn(app_module, db):
    """What the runner hands a stage: get_conn()'s connection."""
    c = app_module.get_conn()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """The pauses between write retries, recorded instead of slept."""
    pauses = []
    monkeypatch.setattr(base, "_sleep", pauses.append)
    return pauses


class Flaky:
    """A connection whose execute_batch calls after the first `after` raise
    `error`: `fails` of them, or all of them if `fails` is None. They do not
    reach the database, unless `commits`: then each lands and commits
    before it raises, like a request the server finished after the client
    gave up. `calls` counts them all."""

    def __init__(self, conn, error, fails=None, after=0, commits=False):
        self._conn, self._error, self._fails, self._after = conn, error, fails, after
        self._commits = commits
        self.calls = 0

    def execute(self, *args):
        return self._conn.execute(*args)

    def execute_batch(self, stmts):
        self.calls += 1
        n = self.calls - self._after
        if n > 0 and (self._fails is None or n <= self._fails):
            if self._commits:
                self._conn.execute_batch(stmts)
            raise self._error
        return self._conn.execute_batch(stmts)


def states(sql):
    return [tuple(r) for r in sql.execute(
        "SELECT album, preview_status, ingestion_status FROM tracks ORDER BY id")]


def test_a_row_that_violates_a_constraint_is_parked_and_the_rest_land(sql, conn):
    a, b, c = add_rows(sql, 3)
    stage = Scripted({a: {"album": "A"},
                      # spotify_id is unique and S0 is a's; apple_id is
                      # droppable, so the retry without it fails again
                      b: {"album": "B", "spotify_id": "S0", "apple_id": 901},
                      c: {"album": "C"}})

    counts = stage.run_batch(conn, 10, log)

    assert counts == {"done": 2, "no_match": 0, "failed": 1}
    rows = {r["id"]: r for r in sql.execute(
        "SELECT id, album, preview_status, download_status, ingestion_status, ingestion_error "
        "FROM tracks").fetchall()}
    for ok, album in ((a, "A"), (c, "C")):
        assert (rows[ok]["album"], rows[ok]["preview_status"], rows[ok]["download_status"],
                rows[ok]["ingestion_status"]) == (album, "done", "pending", "pending")
    bad = rows[b]
    assert (bad["album"], bad["preview_status"], bad["ingestion_status"]) == (
        None, "failed", "scripted_stage_error")
    assert "write failed" in bad["ingestion_error"] and "UNIQUE" in bad["ingestion_error"]


def test_a_schema_error_on_one_row_stops_the_stage_and_parks_nothing(sql, conn, sleeps):
    # Only b writes the missing column, so the rest would write fine; but a
    # column the database lacks is never a row's fault. Parking b would hide
    # a schema gap behind one 'failed' row per batch.
    a, b, c = add_rows(sql, 3)
    stage = Scripted({a: {"album": "A"},
                      b: {"album": "B", "no_such_column": 1},
                      c: {"album": "C"}})
    with pytest.raises(sqlite3.OperationalError, match="no_such_column"):
        stage.run_batch(conn, 10, log)
    assert states(sql) == [(None, "pending", "pending")] * 3
    assert sleeps == []                                  # and not worth a retry


def test_a_unique_collision_is_retried_without_the_droppable_fields(sql, conn):
    a, b = add_rows(sql, 2)
    sql.execute("UPDATE tracks SET apple_id = 777 WHERE id = ?", (a,))
    sql.commit()
    stage = Scripted({a: {"album": "A"},
                      b: {"album": "B", "apple_id": 777, "genre": "pop"}})

    assert stage.run_batch(conn, 10, log)["done"] == 2
    row = sql.execute("SELECT album, apple_id, genre, preview_status FROM tracks WHERE id = ?",
                      (b,)).fetchone()
    assert tuple(row) == ("B", None, None, "done")


def test_a_bad_row_costs_one_more_batch_not_a_rewrite_of_every_row(sql, conn, batches):
    a, b, c = add_rows(sql, 3)
    stage = Scripted({a: {"album": "A"},
                      b: {"album": "B", "spotify_id": "S0"},     # a's: UNIQUE
                      c: {"album": "C"}})
    stage.run_batch(conn, 10, log)
    # the batch, the batch without b, then b's 'failed' status
    assert [len(x) for x in batches] == [3, 2, 1]
    assert [params[-1] for _, params in batches[1]] == [a, c]


TRANSIENT = {
    "connection": requests.exceptions.ConnectionError("connection reset"),   # the network
    "timeout": TimeoutError("read timed out"),                               # a timeout
    "locked": sqlite3.OperationalError("database is locked"),                # busy database
}


@pytest.mark.parametrize("error", list(TRANSIENT.values()), ids=list(TRANSIENT))
def test_a_transient_failure_is_retried_and_the_batch_lands(sql, conn, error, sleeps, caplog):
    # Every statement is an UPDATE or upsert of absolute values, so a retry
    # is safe even if the failed request did commit.
    ids = add_rows(sql, 3)
    stage = Scripted({i: {"album": "X"} for i in ids})
    flaky = Flaky(conn, error, fails=2)
    assert stage.run_batch(flaky, 10, log) == {"done": 3, "no_match": 0, "failed": 0}
    assert (flaky.calls, sleeps) == (3, [2, 4])
    assert states(sql) == [("X", "done", "pending")] * 3
    assert "retry 1 of 3 in 2s" in caplog.text and "retry 2 of 3 in 4s" in caplog.text


@pytest.mark.parametrize("error,calls,pauses", [
    *((e, 4, [2, 4, 8]) for e in TRANSIENT.values()),
    (sqlite3.OperationalError("cannot commit"), 1, []),        # no statement owns it
], ids=[*TRANSIENT.keys(), "no-step"])
def test_an_error_that_is_not_about_a_row_parks_nothing(sql, conn, error, calls, pauses,
                                                        sleeps, caplog):
    # It never stops failing: the first version retried row by row after a
    # failed batch, and parked any row whose own rewrite then failed.
    ids = add_rows(sql, 3)
    stage = Scripted({i: {"album": "X"} for i in ids})
    flaky = Flaky(conn, error)
    with pytest.raises(type(error)):
        stage.run_batch(flaky, 10, log)
    assert (flaky.calls, sleeps) == (calls, pauses)
    assert states(sql) == [(None, "pending", "pending")] * 3
    if not isinstance(error, sqlite3.Error):
        # the server may have committed it after the client gave up
        assert "outcome unknown, rows left for the next pass" in caplog.text


def test_a_set_aside_rows_own_write_is_retried_too(sql, conn, sleeps):
    a, b = add_rows(sql, 2)
    stage = Scripted({a: {"album": "A"}, b: {"album": "B", "spotify_id": "S0"}})  # a's
    # the batch (b breaks it), the batch without b, then b's park, which
    # the network fails once
    flaky = Flaky(conn, requests.exceptions.ConnectionError("connection reset"),
                  fails=1, after=2)
    assert stage.run_batch(flaky, 10, log) == {"done": 1, "no_match": 0, "failed": 1}
    assert (flaky.calls, sleeps) == (4, [2])
    assert states(sql) == [("A", "done", "pending"), (None, "failed", "scripted_stage_error")]


def test_the_same_error_on_every_row_stops_the_stage_and_parks_nothing(sql, conn):
    # A column the stage writes and the database lacks (a schema gap on
    # prod) fails every row the same way: no single row is the problem.
    ids = add_rows(sql, 3)
    stage = Scripted({i: {"album": "X", "no_such_column": 1} for i in ids})
    with pytest.raises(sqlite3.OperationalError, match="no_such_column"):
        stage.run_batch(conn, 10, log)
    assert states(sql) == [(None, "pending", "pending")] * 3


def test_two_unique_collisions_side_by_side_are_each_retried(sql, conn):
    # Same error on neighbouring rows, but a constraint violation is about a
    # row's data, so it is never read as a systematic failure.
    a, b, c, d = add_rows(sql, 4)
    sql.execute("UPDATE tracks SET apple_id = 701 WHERE id = ?", (c,))
    sql.execute("UPDATE tracks SET apple_id = 702 WHERE id = ?", (d,))
    sql.commit()
    stage = Scripted({a: {"album": "A", "apple_id": 701}, b: {"album": "B", "apple_id": 702},
                      c: {"album": "C"}, d: {"album": "D"}})
    assert stage.run_batch(conn, 10, log)["done"] == 4
    rows = sql.execute("SELECT id, album, apple_id FROM tracks ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(a, "A", None), (b, "B", None),
                                        (c, "C", 701), (d, "D", 702)]


# ------------------------------------------------------- finished tracks
#
# Every DJ, similar and vibe query requires ingestion_status='done', and
# 'done' means every stage passed. A stage that fails on a finished track
# never parks it: the track stays exactly as it was, still armed, and the
# next pass tries again.

def finished_and_in_flight(sql):
    """A finished track re-armed for fuse and an in-flight one at fuse, plus
    a third track (spotify_id S2) that fuse does not select."""
    done, flight, _ = add_rows(sql, 3)
    sql.execute("UPDATE tracks SET ingestion_status = 'done', fuse_status = 'pending' "
                "WHERE id = ?", (done,))
    sql.execute("UPDATE tracks SET fuse_status = 'pending' WHERE id = ?", (flight,))
    sql.commit()
    return done, flight


def fuse_state(sql, tid):
    return tuple(sql.execute("SELECT ingestion_status, fuse_status, album, ingestion_error "
                             "FROM tracks WHERE id = ?", (tid,)).fetchone())


def test_a_finished_track_whose_write_keeps_failing_is_left_armed(sql, conn, batches, caplog):
    done, flight = finished_and_in_flight(sql)
    # S2 is taken (UNIQUE), and apple_id is droppable, so each write raises
    # IntegrityError twice: in the batch, then alone without apple_id.
    stage = Refuse({done: (STATUS_DONE, {"album": "D", "spotify_id": "S2", "apple_id": 1}, None),
                    flight: (STATUS_DONE, {"album": "F", "spotify_id": "S2", "apple_id": 2},
                             None)})

    counts = stage.run_batch(conn, 10, log)

    assert counts == {"done": 0, "no_match": 0, "failed": 1, "left_armed": 1}
    assert fuse_state(sql, done) == ("done", "pending", None, None)
    status, fuse, album, error = fuse_state(sql, flight)
    assert (status, fuse, album) == ("fuse_stage_error", "failed", None)
    assert "write failed" in error and "UNIQUE" in error
    assert f"row {done} is finished; left armed for the next pass" in caplog.text
    # the batch, the batch without done, then each alone: without apple_id, park
    assert [len(x) for x in batches] == [2, 1, 1, 1, 1, 1]


def test_a_stage_that_fails_on_a_finished_track_leaves_it_armed(sql, conn, caplog):
    done, flight = finished_and_in_flight(sql)
    stage = Refuse({done: (STATUS_FAILED, {}, "unexpected MERT dim 0"),
                    flight: (STATUS_FAILED, {}, "unexpected MERT dim 0")})

    counts = stage.run_batch(conn, 10, log)

    assert counts == {"done": 0, "no_match": 0, "failed": 1, "left_armed": 1}
    assert fuse_state(sql, done) == ("done", "pending", None, None)
    assert fuse_state(sql, flight) == (
        "fuse_stage_error", "failed", None, "[fuse] unexpected MERT dim 0")
    assert f"row {done} is finished; left armed for the next pass" in caplog.text


def test_a_retry_after_a_write_that_did_commit_lands_the_same(sql, conn, sleeps):
    # The first attempt committed and then timed out on the client. The
    # retry runs the same statements: the in-flight row's park matches it
    # again (it is no longer 'pending', but it is not 'done' either), and
    # the finished row's still matches nothing.
    done, flight = finished_and_in_flight(sql)
    stage = Refuse({done: (STATUS_FAILED, {}, "boom"), flight: (STATUS_FAILED, {}, "boom")})
    flaky = Flaky(conn, TimeoutError("read timed out"), fails=1, commits=True)

    counts = stage.run_batch(flaky, 10, log)

    assert counts == {"done": 0, "no_match": 0, "failed": 1, "left_armed": 1}
    assert (flaky.calls, sleeps) == (2, [2])
    assert fuse_state(sql, done) == ("done", "pending", None, None)
    assert fuse_state(sql, flight) == ("fuse_stage_error", "failed", None, "[fuse] boom")


def test_a_pass_that_only_left_finished_tracks_armed_is_not_progress(sql):
    # Counted as processed, a finished track failing every pass would keep
    # --until-drained passing forever without a sleep.
    (done,) = finished(sql, 1)
    stage = Refuse({done: (STATUS_FAILED, {}, "unexpected MERT dim 0")})
    assert runner.run_pass([stage], 10) == (0, 0)
    assert runner.run_pass([stage], 10) == (0, 0)
    assert fuse_state(sql, done) == ("done", "pending", None, None)


def test_a_clean_batch_is_one_execute_batch(sql, app_module, batches):
    ids = add_rows(sql, 4)
    stage = Scripted({i: {"album": "X"} for i in ids})
    conn = app_module.get_conn()
    try:
        stage.run_batch(conn, 10, log)
    finally:
        conn.close()
    assert [len(b) for b in batches] == [4]


# ------------------------------------------------------------------ classify

def test_classify_writes_its_vector_with_the_row(sql, app_module, batches, tmp_path,
                                                 monkeypatch):
    (tid,) = add_rows(sql, 1)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\0" * 2048)
    sql.execute("UPDATE tracks SET ml_status = 'pending', download_status = 'done', "
                "librosa_status = 'done', audio_path = ? WHERE id = ?", (str(audio), tid))
    sql.commit()
    vec = np.linspace(-1, 1, MERT_DIM, dtype=np.float32)
    stage = ClassifyStage()
    monkeypatch.setattr(stage._ml, "predict_and_embed_from_path", lambda path: (
        {"energy": 0.5, "danceability": 0.5, "valence": 0.5, "vibe_score": 0.5,
         "model_version": CURRENT_MERT_VERSION}, vec))

    conn = app_module.get_conn()
    try:
        assert stage.run_batch(conn, 10, log)["done"] == 1
    finally:
        conn.close()

    assert len(batches) == 1 and len(batches[0]) == 2       # UPDATE + upsert, one transaction
    row = sql.execute("SELECT t.ml_status, t.fuse_status, te.model_version, te.mert_embedding "
                      "FROM tracks t JOIN track_embeddings te ON te.track_id = t.id").fetchone()
    assert tuple(row)[:3] == ("done", "pending", CURRENT_MERT_VERSION)
    assert np.array_equal(np.frombuffer(row[3], dtype=np.float32), vec)


@pytest.mark.parametrize("preds,vec,field", [
    ({"energy": float("nan"), "danceability": 0.5, "valence": 0.5}, "ok", "energy"),
    ({"energy": 0.5, "danceability": 0.5, "valence": float("inf")}, "ok", "valence"),
    ({"energy": 0.5, "danceability": 0.5, "valence": 0.5}, "nan", "mert_embedding"),
], ids=["nan-energy", "inf-valence", "nan-vector"])
def test_classify_fails_a_row_whose_model_output_is_not_finite(sql, app_module, tmp_path,
                                                                monkeypatch, preds, vec, field):
    # A NaN score is a failed classification, not a value to store: SQLite
    # keeps NaN as NULL, and Turso refuses the whole batch request over it.
    (tid,) = add_rows(sql, 1)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\0" * 2048)
    sql.execute("UPDATE tracks SET ml_status = 'pending', download_status = 'done', "
                "librosa_status = 'done', audio_path = ? WHERE id = ?", (str(audio), tid))
    sql.commit()
    vector = np.linspace(-1, 1, MERT_DIM, dtype=np.float32)
    if vec == "nan":
        vector[7] = np.nan
    stage = ClassifyStage()
    monkeypatch.setattr(stage._ml, "predict_and_embed_from_path",
                        lambda path: ({**preds, "model_version": CURRENT_MERT_VERSION}, vector))

    conn = app_module.get_conn()
    try:
        assert stage.run_batch(conn, 10, log)["failed"] == 1
    finally:
        conn.close()

    ml, status, error = sql.execute("SELECT ml_status, ingestion_status, ingestion_error "
                                    "FROM tracks WHERE id = ?", (tid,)).fetchone()
    assert (ml, status) == ("failed", "classify_stage_error")
    assert f"model emitted non-finite {field}" in error
    assert sql.execute("SELECT COUNT(*) FROM track_embeddings").fetchone()[0] == 0


# ---------------------------------------------------------------------- fuse

def ready_to_fuse(sql, tid):
    sql.execute("UPDATE tracks SET ml_status = 'done', language_status = 'done', language = 'en', "
                "fuse_status = 'pending' WHERE id = ?", (tid,))
    sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, model_version) "
                "VALUES (?, ?, ?)",
                (tid, np.ones(MERT_DIM, dtype=np.float32).tobytes(), CURRENT_MERT_VERSION))
    sql.commit()


def test_fuse_writes_the_vector_and_arms_youtube(sql, app_module):
    (tid,) = add_rows(sql, 1)
    ready_to_fuse(sql, tid)
    conn = app_module.get_conn()
    try:
        assert FuseStage().run_batch(conn, 10, log)["done"] == 1
    finally:
        conn.close()
    row = sql.execute(f"SELECT t.fuse_status, t.youtube_status, LENGTH(te.{FUSED_COLUMN}) "
                      "FROM tracks t JOIN track_embeddings te ON te.track_id = t.id").fetchone()
    assert tuple(row) == ("done", "pending", FUSED_DIM * 4)


def test_fuse_never_marks_a_row_done_without_its_vector(sql, app_module, monkeypatch):
    # The track_embeddings row disappears between fuse reading the batch and
    # writing it, as when a reset deletes a re-ingested track's vectors.
    # The old blind UPDATE matched nothing and fuse_status still went 'done'.
    (tid,) = add_rows(sql, 1)
    ready_to_fuse(sql, tid)
    stage = FuseStage()
    real_fetch = stage.fetch_pending

    def fetch_then_lose_the_row(conn, limit, only_ids=None):
        rows = real_fetch(conn, limit, only_ids)
        sql.execute("DELETE FROM track_embeddings WHERE track_id = ?", (tid,))
        sql.commit()
        return rows
    monkeypatch.setattr(stage, "fetch_pending", fetch_then_lose_the_row)

    conn = app_module.get_conn()
    try:
        stage.run_batch(conn, 10, log)
    finally:
        conn.close()
    status = sql.execute("SELECT fuse_status FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    fused = sql.execute(f"SELECT LENGTH({FUSED_COLUMN}) FROM track_embeddings WHERE track_id = ?",
                        (tid,)).fetchone()
    assert status == "done" and fused is not None and fused[0] == FUSED_DIM * 4


def test_fuse_writes_fused_v3_with_the_genre_block_and_fused_v2_beside_it(sql, app_module):
    """fused_v3 reads the row's genre scores; fused_v2 is written too, so a
    database whose app still serves fused_v2 stays right."""
    a, b = add_rows(sql, 2)
    for tid in (a, b):
        ready_to_fuse(sql, tid)
    sql.execute("UPDATE tracks SET genre_status = 'done', genre_scores = '{\"film_mass\":100}' "
                "WHERE id = ?", (a,))
    sql.commit()
    conn = app_module.get_conn()
    try:
        assert FuseStage().run_batch(conn, 10, log)["done"] == 2
    finally:
        conn.close()
    genre = slice(fv.FUSED_V2_DIM, fv.FUSED_DIM)
    for tid in (a, b):
        r = sql.execute(f"SELECT t.*, te.{fv.FUSED_COLUMN} AS v3, te.{fv.FUSED_V2_COLUMN} AS v2 "
                        "FROM tracks t JOIN track_embeddings te ON te.track_id = t.id "
                        "WHERE t.id = ?", (tid,)).fetchone()
        row = {k: r[k] for k in r.keys()}
        mert = np.ones(MERT_DIM, dtype=np.float32)
        v3 = np.frombuffer(r["v3"], dtype=np.float32)
        assert np.array_equal(v3, fv._build_fused(row, mert.copy()).astype(np.float32))
        assert np.array_equal(np.frombuffer(r["v2"], dtype=np.float32),
                              fv._build_fused_v2(row, mert.copy()).astype(np.float32))
        assert v3[genre].any() == (tid == a)


def test_fuse_on_a_database_without_fused_v3_stops_and_parks_nothing(sql, conn, sleeps):
    """Production until its migration: the write names a column the
    database lacks. The stage raises, nothing is parked, nothing retried."""
    (tid,) = add_rows(sql, 1)
    ready_to_fuse(sql, tid)
    sql.execute(f"ALTER TABLE track_embeddings DROP COLUMN {fv.FUSED_COLUMN}")
    sql.commit()
    with pytest.raises(sqlite3.OperationalError, match=fv.FUSED_COLUMN):
        FuseStage().run_batch(conn, 10, log)
    assert sql.execute("SELECT fuse_status FROM tracks WHERE id = ?",
                       (tid,)).fetchone()[0] == "pending"
    assert sleeps == []
