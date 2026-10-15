"""How a stage writes its batch (ingest_pipeline/base.py, "Writing a batch").

Until 2026-10-15 Stage.run_batch UPDATEd row by row and committed after the
loop: one row that raised aborted it once the GPU work was done, SQLite
rolled the whole batch back, and Turso, which commits per statement, kept
whatever came before the bad row.
"""
import logging
import sqlite3

import numpy as np
import pytest

from ingest_pipeline.base import RowResult, Stage, STATUS_DONE, id_filter
from ingest_pipeline.fused_vector import FUSED_DIM, MERT_DIM
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


@pytest.fixture(params=["db_client", "plain sqlite3"])
def conn(request, app_module, db):
    """Both kinds of connection a stage can be handed: get_conn()'s, which
    has execute_batch, and a bare sqlite3 one, which does not."""
    c = app_module.get_conn() if request.param == "db_client" else sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_a_row_whose_write_fails_does_not_cost_the_batch(sql, conn):
    a, b, c = add_rows(sql, 3)
    stage = Scripted({a: {"album": "A"},
                      b: {"album": "B", "no_such_column": 1},   # this UPDATE raises
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
    assert "write failed" in bad["ingestion_error"] and "no_such_column" in bad["ingestion_error"]


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
                "audio_path = ? WHERE id = ?", (str(audio), tid))
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
    row = sql.execute("SELECT t.fuse_status, t.youtube_status, LENGTH(te.fused_embedding) "
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
    fused = sql.execute("SELECT LENGTH(fused_embedding) FROM track_embeddings WHERE track_id = ?",
                        (tid,)).fetchone()
    assert status == "done" and fused is not None and fused[0] == FUSED_DIM * 4
