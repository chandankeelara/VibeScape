"""scripts/refuse_vectors.py: the fused_v2 and fused_v3 migrations (new
columns, re-fuse, cache drop) on a production-shaped SQLite file, a genre
change after them, and a pipeline from before fused_v2 running after it.
Local target only; prod is pointed at a .env.prod that does not exist
(conftest)."""
import json

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
    """track_embeddings in production's shape before fused_v2 (neither
    fused_v2 nor fused_v3 column), five tracks fused in v1 (788 wide), one
    with a stale encoder, two cached means; two of the five are
    genre-tagged. Returns {track_id: (row, mert, v1 blob)} for the current
    rows."""
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
        if i in (0, 3):
            sql.execute("UPDATE tracks SET genre_scores = ?, genre_status = 'done' WHERE id = ?",
                        (json.dumps({"film_mass": 100, "folk": 50} if i == 0 else {"pop": 100}), tid))
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


def _new(sql, tid, col=fv.FUSED_COLUMN):
    blob = sql.execute(f"SELECT {col} FROM track_embeddings WHERE track_id = ?",
                       (tid,)).fetchone()[0]
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def test_dry_run_changes_nothing(prodlike, sql):
    assert rf.main([]) == 0
    assert fv.FUSED_COLUMN not in _cols(sql) and fv.FUSED_V2_COLUMN not in _cols(sql)
    assert sql.execute("SELECT COUNT(*) FROM user_embedding_mean").fetchone()[0] == 2


def test_apply_adds_the_columns_and_writes_fused_v3_and_v2_beside_v1(prodlike, sql, batches):
    assert rf.main(["--apply", "--batch", "2"]) == 0
    cols = {r["name"]: r["type"] for r in sql.execute("PRAGMA table_info(track_embeddings)")}
    assert cols[fv.FUSED_COLUMN] == f"F32_BLOB({fv.FUSED_DIM})"
    assert cols[fv.FUSED_V2_COLUMN] == f"F32_BLOB({fv.FUSED_V2_DIM})"
    for tid, (row, mert, v1) in prodlike.items():
        assert np.array_equal(_new(sql, tid), fv._build_fused(row, mert.copy()).astype(np.float32))
        assert np.array_equal(_new(sql, tid, fv.FUSED_V2_COLUMN),
                              fv._build_fused_v2(row, mert.copy()).astype(np.float32))
        # fused_v1 untouched: the previous deploy still reads it
        assert sql.execute("SELECT fused_embedding FROM track_embeddings WHERE track_id = ?",
                           (tid,)).fetchone()[0] == v1
    assert sql.execute("SELECT COUNT(*) FROM user_embedding_mean").fetchone()[0] == 0
    # the two columns, then 5 rows in batches of 2, then the means
    assert [len(b) for b in batches] == [2, 2, 2, 1, 1]
    # the tagged rows carry their genres, the others a neutral block
    genre = slice(fv.FUSED_V2_DIM, fv.FUSED_DIM)
    tagged = [tid for tid, (row, _m, _v) in prodlike.items() if row["genre_status"] == "done"]
    assert len(tagged) == 2
    for tid in prodlike:
        assert _new(sql, tid)[genre].any() == (tid in tagged)


def test_a_stale_encoder_row_gets_no_vector(prodlike, sql):
    rf.main(["--apply"])
    stale = sql.execute("SELECT track_id FROM track_embeddings WHERE model_version != ?",
                        (CURRENT_MERT_VERSION,)).fetchone()[0]
    assert _new(sql, stale) is None
    assert _new(sql, stale, fv.FUSED_V2_COLUMN) is None


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


def test_a_database_on_fused_v2_gains_fused_v3_and_keeps_v2(prodlike, sql, batches):
    """Production's shape today: fused_v2 column, filled; no fused_v3. Only
    the new column is added and only fused_v3 is written."""
    sql.execute(f"ALTER TABLE track_embeddings ADD COLUMN {fv.FUSED_V2_COLUMN} "
                f"F32_BLOB({fv.FUSED_V2_DIM})")
    for tid, (row, mert, _v1) in prodlike.items():
        sql.execute(f"UPDATE track_embeddings SET {fv.FUSED_V2_COLUMN} = ? WHERE track_id = ?",
                    (fv._build_fused_v2(row, mert.copy()).astype(np.float32).tobytes(), tid))
    sql.commit()
    assert rf.main(["--apply"]) == 0
    ddl = [s for b in batches for s, _p in b if s.startswith("ALTER")]
    assert ddl == [f"ALTER TABLE track_embeddings ADD COLUMN {fv.FUSED_COLUMN} F32_BLOB({fv.FUSED_DIM})"]
    updates = [s for b in batches for s, _p in b if s.startswith("UPDATE track_embeddings")]
    assert len(updates) == len(prodlike)
    assert all(fv.FUSED_V2_COLUMN not in s for s in updates)
    for tid, (row, mert, _v1) in prodlike.items():
        assert np.array_equal(_new(sql, tid), fv._build_fused(row, mert.copy()).astype(np.float32))


def test_a_genre_change_rewrites_only_that_rows_fused_v3(prodlike, sql, batches):
    rf.main(["--apply"])
    tid = next(t for t, (row, _m, _v) in prodlike.items() if row["genre_status"] is None)
    v2_before = _new(sql, tid, fv.FUSED_V2_COLUMN).copy()
    sql.execute("UPDATE tracks SET genre_scores = '{\"devotional\":100}', genre_status = 'done' "
                "WHERE id = ?", (tid,))
    sql.commit()
    batches.clear()
    assert rf.main(["--apply"]) == 0
    writes = [(s, p) for b in batches for s, p in b if s.startswith("UPDATE track_embeddings")]
    assert len(writes) == 1 and writes[0][1][-1] == tid
    assert fv.FUSED_V2_COLUMN not in writes[0][0]
    assert np.array_equal(_new(sql, tid, fv.FUSED_V2_COLUMN), v2_before)
    row = dict(sql.execute("SELECT * FROM tracks WHERE id = ?", (tid,)).fetchone())
    mert = prodlike[tid][1]
    assert np.array_equal(_new(sql, tid), fv._build_fused(row, mert.copy()).astype(np.float32))


def test_a_database_without_the_genre_columns_gets_neutral_genre_blocks(prodlike, sql):
    """Production before scripts/_turso_add_genre_columns.py: no track can
    be tagged, so fused_v3 is written with a neutral genre block rather than
    the run failing on the missing columns."""
    for col in ("genre_scores", "genre_status", "genre_model_version", "genre_tagged_at"):
        sql.execute(f"ALTER TABLE tracks DROP COLUMN {col}")
    sql.commit()
    assert rf.main(["--apply"]) == 0
    genre = slice(fv.FUSED_V2_DIM, fv.FUSED_DIM)
    for tid, (row, mert, _v1) in prodlike.items():
        v3 = _new(sql, tid)
        assert v3 is not None and not v3[genre].any()
        untagged = {**row, "genre_scores": None, "genre_status": None}
        assert np.array_equal(v3, fv._build_fused(untagged, mert.copy()).astype(np.float32))
