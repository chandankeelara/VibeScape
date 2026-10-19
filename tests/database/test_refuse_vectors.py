"""scripts/refuse_vectors.py on the test SQLite file. Local target only;
prod is pointed at a .env.prod that does not exist (conftest)."""
import numpy as np
import pytest

import refuse_vectors as rf
from ingest_pipeline import fused_vector as fv
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION


@pytest.fixture
def fused_rows(sql):
    """Five tracks with a MERT vector and a fused vector in the OLD space
    (NULL language in 'other'), one with a stale encoder, and two cached
    means. Returns {track_id: (row, mert)} for the current-encoder rows."""
    rng = np.random.default_rng(5)
    rows = {}
    langs = ["kn", None, "ml", "en", "ar", "kn"]
    for i, lang in enumerate(langs):
        cur = sql.execute(
            "INSERT INTO tracks (title, artist, spotify_id, ingestion_status, vibe_score, "
            "language, energy_pred, tempo) VALUES (?, 'A', ?, 'done', 50, ?, 0.7, 120)",
            (f"t{i}", f"sp{i}", lang))
        tid = int(cur.lastrowid)
        mert = rng.normal(size=fv.MERT_DIM).astype(np.float32)
        old = np.zeros(fv.FUSED_DIM, dtype=np.float32)
        old[:fv.MERT_DIM] = mert
        old[-1] = 1.0                       # fused_v1's 'other' slot
        version = CURRENT_MERT_VERSION if i < 5 else "mert_v0_base"
        sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, fused_embedding, "
                    "model_version) VALUES (?, ?, ?, ?)",
                    (tid, mert.tobytes(), (old / np.linalg.norm(old)).tobytes(), version))
        if i < 5:
            row = dict(sql.execute("SELECT * FROM tracks WHERE id = ?", (tid,)).fetchone())
            rows[tid] = (row, mert)
    for uid, mv in ((0, "fused_v1_mert_scalar_lang"), (7, "fused_v1_mert_scalar_lang")):
        sql.execute("INSERT INTO user_embedding_mean (user_id, model_version, n_tracks, mean_json) "
                    "VALUES (?, ?, 5, '[]')", (uid, mv))
    sql.commit()
    return rows


def _fused(sql, tid):
    blob = sql.execute("SELECT fused_embedding FROM track_embeddings WHERE track_id = ?",
                       (tid,)).fetchone()[0]
    return np.frombuffer(blob, dtype=np.float32)


def test_dry_run_writes_nothing(fused_rows, sql, db):
    before = {t: _fused(sql, t).copy() for t in fused_rows}
    assert rf.main([]) == 0
    assert all(np.array_equal(_fused(sql, t), v) for t, v in before.items())
    assert sql.execute("SELECT COUNT(*) FROM user_embedding_mean").fetchone()[0] == 2


def test_apply_rebuilds_with_the_stage_recipe_and_drops_every_mean(fused_rows, sql, db,
                                                                   batches):
    assert rf.main(["--apply", "--batch", "2"]) == 0
    for tid, (row, mert) in fused_rows.items():
        want = fv._build_fused(row, mert.copy()).astype(np.float32)
        assert np.array_equal(_fused(sql, tid), want)
    untagged = next(t for t, (row, _) in fused_rows.items() if row["language"] is None)
    assert not _fused(sql, untagged)[fv.MERT_DIM + len(fv.SCALAR_COLS):].any()
    assert sql.execute("SELECT COUNT(*) FROM user_embedding_mean").fetchone()[0] == 0
    # 5 rows, batches of 2: 3 vector batches, then the means.
    assert [len(b) for b in batches] == [2, 2, 1, 1]


def test_a_stale_encoder_row_is_left_alone(fused_rows, sql, db):
    stale = sql.execute("SELECT track_id, fused_embedding FROM track_embeddings "
                        "WHERE model_version != ?", (CURRENT_MERT_VERSION,)).fetchone()
    rf.main(["--apply"])
    assert _fused(sql, stale[0]).tobytes() == stale[1]


def test_a_second_run_writes_nothing(fused_rows, sql, db, batches):
    rf.main(["--apply"])
    batches.clear()
    assert rf.main(["--apply"]) == 0
    assert batches == []


def test_prod_without_its_env_file_exits_before_connecting():
    with pytest.raises(SystemExit, match=r"\.env\.prod"):
        rf.main(["--target", "prod", "--apply"])
