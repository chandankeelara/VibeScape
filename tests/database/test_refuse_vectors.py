"""scripts/refuse_vectors.py: the fused_v2 migration (new column, re-fuse,
cache drop) on a production-shaped SQLite file, and a pipeline from before
fused_v2 running after it. Local target only; prod is pointed at a
.env.prod that does not exist (conftest)."""
import numpy as np
import pytest

import refuse_vectors as rf
from ingest_pipeline import fused_vector as fv
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION

OLD_DDL = """CREATE TABLE track_embeddings (
    track_id         INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    mert_embedding   F32_BLOB(768),
    fused_embedding  F32_BLOB(788),
    model_version    TEXT,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""


def _cols(sql):
    return {r["name"] for r in sql.execute("PRAGMA table_info(track_embeddings)")}


@pytest.fixture
def prodlike(sql, db):
    """track_embeddings in production's shape (no fused_v2 column), five
    tracks fused in v1 (788 wide), one with a stale encoder, two cached
    means. Returns {track_id: (row, mert, v1 blob)} for the current rows."""
    sql.execute("DROP TABLE track_embeddings")
    sql.execute(OLD_DDL)
    rng = np.random.default_rng(5)
    rows = {}
    for i, lang in enumerate(["kn", None, "ur", "ko", "xx", "kn"]):
        cur = sql.execute(
            "INSERT INTO tracks (title, artist, spotify_id, ingestion_status, vibe_score, "
            "language, energy_pred, tempo) VALUES (?, 'A', ?, 'done', 50, ?, 0.7, 120)",
            (f"t{i}", f"sp{i}", lang))
        tid = int(cur.lastrowid)
        mert = rng.normal(size=fv.MERT_DIM).astype(np.float32)
        v1 = rng.normal(size=788).astype(np.float32).tobytes()
        version = CURRENT_MERT_VERSION if i < 5 else "mert_v0_base"
        sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, fused_embedding, "
                    "model_version) VALUES (?, ?, ?, ?)", (tid, mert.tobytes(), v1, version))
        if i < 5:
            row = dict(sql.execute("SELECT * FROM tracks WHERE id = ?", (tid,)).fetchone())
            rows[tid] = (row, mert, v1)
    for uid in (0, 7):
        sql.execute("INSERT INTO user_embedding_mean (user_id, model_version, n_tracks, mean_json) "
                    "VALUES (?, 'fused_v1_mert_scalar_lang', 5, '[]')", (uid,))
    sql.commit()
    return rows


def _new(sql, tid):
    blob = sql.execute(f"SELECT {fv.FUSED_COLUMN} FROM track_embeddings WHERE track_id = ?",
                       (tid,)).fetchone()[0]
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def test_dry_run_changes_nothing(prodlike, sql):
    assert rf.main([]) == 0
    assert fv.FUSED_COLUMN not in _cols(sql)
    assert sql.execute("SELECT COUNT(*) FROM user_embedding_mean").fetchone()[0] == 2


def test_apply_adds_the_column_and_writes_fused_v2_beside_v1(prodlike, sql, batches):
    assert rf.main(["--apply", "--batch", "2"]) == 0
    cols = {r["name"]: r["type"] for r in sql.execute("PRAGMA table_info(track_embeddings)")}
    assert cols[fv.FUSED_COLUMN] == f"F32_BLOB({fv.FUSED_DIM})"
    for tid, (row, mert, v1) in prodlike.items():
        assert np.array_equal(_new(sql, tid), fv._build_fused(row, mert.copy()).astype(np.float32))
        # fused_v1 untouched: the previous deploy still reads it
        assert sql.execute("SELECT fused_embedding FROM track_embeddings WHERE track_id = ?",
                           (tid,)).fetchone()[0] == v1
    assert sql.execute("SELECT COUNT(*) FROM user_embedding_mean").fetchone()[0] == 0
    # the column, then 5 rows in batches of 2, then the means
    assert [len(b) for b in batches] == [1, 2, 2, 1, 1]


def test_a_stale_encoder_row_gets_no_vector(prodlike, sql):
    rf.main(["--apply"])
    stale = sql.execute("SELECT track_id FROM track_embeddings WHERE model_version != ?",
                        (CURRENT_MERT_VERSION,)).fetchone()[0]
    assert _new(sql, stale) is None


def test_a_second_run_writes_nothing(prodlike, sql, batches):
    rf.main(["--apply"])
    batches.clear()
    assert rf.main(["--apply"]) == 0
    assert batches == []


def test_a_pipeline_from_before_fused_v2_corrupts_nothing(prodlike, sql):
    """A machine that has not pulled fuses a track the old way: fused_v1's
    statement, into fused_v1's column. The new column is never touched, the
    app never ranks the old vector (it reads only the new column), and the
    next run of the script gives the track its fused_v2 vector."""
    rf.main(["--apply"])
    tid, (row, mert, _v1) = next(iter(prodlike.items()))
    sql.execute(f"UPDATE track_embeddings SET {fv.FUSED_COLUMN} = NULL WHERE track_id = ?", (tid,))
    sql.execute("INSERT INTO track_embeddings (track_id, fused_embedding, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(track_id) DO UPDATE SET "
                "fused_embedding = excluded.fused_embedding, updated_at = excluded.updated_at",
                (tid, np.ones(788, dtype=np.float32).tobytes()))
    sql.commit()
    assert _new(sql, tid) is None
    assert rf.wrong_width(sql) == 1
    assert rf.main(["--apply"]) == 0
    assert np.array_equal(_new(sql, tid), fv._build_fused(row, mert.copy()).astype(np.float32))


def test_the_end_check_reads_widths_from_the_database(prodlike, sql):
    rf.main(["--apply"])
    tid = next(iter(prodlike))
    sql.execute(f"UPDATE track_embeddings SET {fv.FUSED_COLUMN} = ? WHERE track_id = ?",
                (np.zeros(788, dtype=np.float32).tobytes(), tid))
    sql.commit()
    assert rf.wrong_width(sql) == 1


def test_prod_without_its_env_file_exits_before_connecting():
    with pytest.raises(SystemExit, match=r"\.env\.prod"):
        rf.main(["--target", "prod", "--apply"])
