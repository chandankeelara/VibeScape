"""Language and skips in the DJ, from the taste vector alone, end to end
through POST /api/tracks/{seed}/similar; and parity between the two ranking
paths (numpy on SQLite, vector_distance_cos on Turso).

Every track here gets a vector from the real fused recipe
(ingest_pipeline/fused_vector._build_fused): MERT drawn around shared
"sounds", so the audio cannot tell the languages apart, plus the track's
language. What separates Kannada from Hindi is the language block at its
real weight, and what pushes a skipped language out is dj_replay's avoid
term at its real strength.
"""
import sqlite3

import numpy as np
import pytest

from ingest_pipeline import fused_vector as fv


def done(t):
    return {"id": t, "action": "completed", "played_ratio": 1.0}


def skip(t):
    return {"id": t, "action": "skipped", "played_ratio": 0.1}


def test_the_app_the_schema_and_the_recipe_agree_on_the_space(app_module, sql):
    import db
    assert app_module.FUSED_MODEL_VERSION == fv.FUSED_MODEL_VERSION
    assert app_module.FUSED_DIM == fv.FUSED_DIM == 841
    assert app_module.FUSED_LANG_DIMS == fv.LANG_DIMS == 64
    assert app_module.FUSED_COLUMN == db.FUSED_V2_COLUMN == fv.FUSED_COLUMN
    assert db.FUSED_V2_DDL == f"{fv.FUSED_COLUMN} F32_BLOB({fv.FUSED_DIM})"
    cols = {r["name"]: r["type"] for r in sql.execute("PRAGMA table_info(track_embeddings)")}
    assert cols[fv.FUSED_COLUMN] == f"F32_BLOB({fv.FUSED_DIM})"
    # fused_v1's column stays, untouched, for a rollback
    assert cols["fused_embedding"] == "F32_BLOB(788)"


@pytest.fixture
def library(make_user, make_tracks, sql):
    """A library that is 70% Kannada and 30% Hindi: 140 kn and 60 hi tracks
    over two shared sounds (100 of each sound, both languages in each).
    Returns (headers, {lang: [ids], (lang, sound): [ids]})."""
    rng = np.random.default_rng(23)
    sounds = [rng.normal(size=fv.MERT_DIM) for _ in range(2)]
    uid, h = make_user()
    by_lang = {"kn": [], "hi": []}
    for snd, centre in enumerate(sounds):
        for i in range(100):
            lang = "kn" if i % 10 < 7 else "hi"
            mert = (centre + 0.6 * rng.normal(size=fv.MERT_DIM)).astype(np.float32)
            row = {c: rng.uniform(0.3, 0.7) * (fv.SCALAR_RANGE[c][1] - fv.SCALAR_RANGE[c][0])
                   + fv.SCALAR_RANGE[c][0] for c in fv.SCALAR_COLS}
            row["language"] = lang
            (tid,) = make_tracks(uid, [fv._build_fused(row, mert)])
            sql.execute("UPDATE tracks SET language = ? WHERE id = ?", (lang, tid))
            by_lang[lang].append(tid)
            by_lang.setdefault((lang, snd), []).append(tid)
    sql.commit()
    return h, by_lang


def _post(client, h, seed, body):
    r = client.post(f"/api/tracks/{seed}/similar", json={"mode": "dj", "limit": 10, **body},
                    headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _share(out, ids):
    return sum(t["id"] in ids for t in out["tracks"]) / len(out["tracks"])


def test_a_kannada_library_playing_hindi_gets_hindi(client, library):
    h, L = library
    played = L["hi"][:3]
    out = _post(client, h, played[-1], {"events": [done(t) for t in played],
                                        "exclude_ids": played})
    assert _share(out, L["hi"]) >= 0.9


def test_cold_start_on_a_hindi_seed_gets_hindi(client, library):
    h, L = library
    out = _post(client, h, L["hi"][0], {"events": []})
    assert out["mode_used"] == "dj_replay_fused_from_seed"
    assert _share(out, L["hi"]) >= 0.9


def test_three_kannada_skips_remove_kannada(client, library):
    h, L = library
    session = L["kn"][:6]
    events = [done(t) for t in session[:3]] + [skip(t) for t in session[3:]]
    out = _post(client, h, session[-1], {"events": events, "exclude_ids": session})
    assert _share(out, L["kn"]) <= 0.1


def test_one_stray_skip_does_not_flip_the_language(client, library):
    h, L = library
    session = L["kn"][:5]
    events = [done(t) for t in session[:4]] + [skip(session[4])]
    out = _post(client, h, session[-1], {"events": events, "exclude_ids": session})
    assert _share(out, L["kn"]) >= 0.9


def test_a_switch_to_hindi_is_followed_within_two_songs(client, library):
    h, L = library
    session = L["kn"][:5] + L["hi"][:2]
    out = _post(client, h, session[-1], {"events": [done(t) for t in session],
                                         "exclude_ids": session})
    assert _share(out, L["hi"]) >= 0.9


def _register_turso_functions(conn):
    """libSQL's vector functions, on SQLite, so the production SQL runs here:
    vector32('[..]') -> text, vector_extract(blob) -> '[..]',
    vector_distance_cos(blob, '[..]') -> 1 - cosine."""
    def parse(x):
        if isinstance(x, (bytes, bytearray, memoryview)):
            return np.frombuffer(bytes(x), dtype=np.float32).astype(np.float64)
        return np.array([float(v) for v in str(x).strip("[]").split(",")])

    def dist(a, b):
        if a is None or b is None:
            return None
        u, v = parse(a), parse(b)
        return float(1.0 - (u @ v) / (np.linalg.norm(u) * np.linalg.norm(v)))
    conn.create_function("vector32", 1, lambda s: s)
    conn.create_function("vector_extract", 1,
                         lambda b: None if b is None else "[" + ",".join(
                             repr(float(x)) for x in parse(b)) + "]")
    conn.create_function("vector_distance_cos", 2, dist)


@pytest.mark.parametrize("recency", [True, False])
def test_both_ranking_paths_return_the_same_order(client, library, app_module, db,
                                                  monkeypatch, recency):
    """The numpy path (SQLite) and the vector_distance_cos path (Turso), on a
    session with accepts and skips. The Turso SQL runs on SQLite with
    libSQL's vector functions stood in for; what it cannot show is libSQL's
    own float rounding."""
    import db_client
    h, L = library
    session = L["kn"][:4] + L["hi"][:2]
    events = [done(t) for t in session[:4]] + [skip(t) for t in session[4:]]
    body = {"events": events, "exclude_ids": session}
    if not recency:
        monkeypatch.setattr(app_module, "_DJ_RECENCY_WEIGHT", 0.0)
    local = _post(client, h, session[-1], body)
    assert local["mode_used"] == "dj_replay_fused"

    def turso_shaped():
        # The test file, opened as db_client opens SQLite, whatever
        # DB_BACKEND now says.
        conn = sqlite3.connect(str(db), check_same_thread=False,
                               factory=db_client._SqliteConnection)
        conn.row_factory = sqlite3.Row
        _register_turso_functions(conn)
        return conn
    monkeypatch.setattr(app_module, "get_conn", turso_shaped)
    monkeypatch.setenv("DB_BACKEND", "turso")
    app_module._dj_mean_cache.clear()
    turso = _post(client, h, session[-1], body)
    assert turso["mode_used"] == "dj_replay_fused_turso"

    assert [t["id"] for t in turso["tracks"]] == [t["id"] for t in local["tracks"]]
    for a, b in zip(turso["tracks"], local["tracks"]):
        assert a["score"] == pytest.approx(b["score"], abs=1e-5)


def test_before_the_migration_the_dj_ranks_fused_v1_instead_of_failing(
        client, library, app_module, db, sql, monkeypatch, caplog):
    """A deploy that lands before scripts/refuse_vectors.py: production's
    shape, no fused_v2 column, fused_v1 vectors in fused_embedding. Turso-
    shaped (no ensure_db, which would add the column on SQLite). Without the
    fallback every request is a 500 (no such column)."""
    import logging
    import db_client
    h, L = library
    # fused_v1 vectors: the same MERT and scalars, the 11-slot language block
    ten = ["en", "hi", "kn", "te", "ta", "pa", "ml", "es", "fr", "pt"]
    for tid, lang, blob in sql.execute(
            f"SELECT te.track_id, t.language, te.{fv.FUSED_COLUMN} FROM track_embeddings te "
            f"JOIN tracks t ON t.id = te.track_id").fetchall():
        v2 = np.frombuffer(blob, dtype=np.float32)
        head = v2[:fv.MERT_DIM + len(fv.SCALAR_COLS)]
        onehot = np.zeros(11, dtype=np.float32)
        onehot[ten.index(lang)] = 0.2
        v1 = np.concatenate([head, onehot])
        sql.execute("UPDATE track_embeddings SET fused_embedding = ? WHERE track_id = ?",
                    ((v1 / np.linalg.norm(v1)).astype(np.float32).tobytes(), tid))
    sql.execute(f"ALTER TABLE track_embeddings DROP COLUMN {fv.FUSED_COLUMN}")
    sql.commit()

    def turso_shaped():
        conn = sqlite3.connect(str(db), check_same_thread=False,
                               factory=db_client._SqliteConnection)
        conn.row_factory = sqlite3.Row
        _register_turso_functions(conn)
        return conn
    monkeypatch.setattr(app_module, "get_conn", turso_shaped)
    monkeypatch.setenv("DB_BACKEND", "turso")
    caplog.set_level(logging.WARNING, logger="vibescape")

    played = L["hi"][:3]
    body = {"events": [done(t) for t in played], "exclude_ids": played}
    for _ in range(2):
        out = _post(client, h, played[-1], body)
        assert out["mode_used"] == "dj_replay_fused_turso"
        assert _share(out, L["hi"]) >= 0.9
    warnings = [r for r in caplog.records if fv.FUSED_COLUMN in r.getMessage()]
    assert len(warnings) == 1

    # The migration adds the column: picked up at the next check, no restart.
    sql.execute(f"ALTER TABLE track_embeddings ADD COLUMN {fv.FUSED_COLUMN} F32_BLOB({fv.FUSED_DIM})")
    sql.commit()
    app_module._fused_v2_state["checked_at"] = 0.0
    conn = turso_shaped()
    try:
        assert app_module._variant_spec("fused", conn) == (fv.FUSED_MODEL_VERSION, fv.FUSED_DIM)
    finally:
        conn.close()
