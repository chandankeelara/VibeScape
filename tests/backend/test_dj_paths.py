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
import json
import secrets
import sqlite3

import numpy as np
import pytest

from ingest_pipeline import fused_vector as fv
from services import dj as dj_service
from services import vectors
from services import recency as recency_service  # `recency` is a test parameter here


def done(t):
    return {"id": t, "action": "completed", "played_ratio": 1.0}


def skip(t):
    return {"id": t, "action": "skipped", "played_ratio": 0.1}


def _title():
    """A distinct title per track: same-titled tracks by one artist in one
    language are one song to the DJ's repeat filter (backend/same_song.py)."""
    return f"Song {secrets.token_hex(4)}"


def test_the_app_the_schema_and_the_recipe_agree_on_the_space(app_module, sql):
    import db
    assert vectors.FUSED_MODEL_VERSION == fv.FUSED_MODEL_VERSION
    assert vectors.FUSED_V2_MODEL_VERSION == fv.FUSED_V2_MODEL_VERSION
    assert vectors.FUSED_MODEL_VERSION != vectors.FUSED_V2_MODEL_VERSION
    assert vectors.FUSED_DIM == fv.FUSED_DIM
    assert vectors.FUSED_V2_DIM == fv.FUSED_V2_DIM
    assert vectors.FUSED_LANG_DIMS == fv.LANG_DIMS
    assert vectors.FUSED_GENRE_DIMS == fv.GENRE_DIMS
    assert vectors.FUSED_COLUMN == db.FUSED_V3_COLUMN == fv.FUSED_COLUMN
    assert vectors.FUSED_V2_COLUMN == db.FUSED_V2_COLUMN == fv.FUSED_V2_COLUMN
    assert db.FUSED_V3_DDL == f"{fv.FUSED_COLUMN} F32_BLOB({fv.FUSED_DIM})"
    assert db.FUSED_V2_DDL == f"{fv.FUSED_V2_COLUMN} F32_BLOB({fv.FUSED_V2_DIM})"
    cols = {r["name"]: r["type"] for r in sql.execute("PRAGMA table_info(track_embeddings)")}
    assert cols[fv.FUSED_COLUMN] == f"F32_BLOB({fv.FUSED_DIM})"
    assert cols[fv.FUSED_V2_COLUMN] == f"F32_BLOB({fv.FUSED_V2_DIM})"
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
            (tid,) = make_tracks(uid, [fv._build_fused(row, mert)], titles={0: _title()})
            sql.execute("UPDATE tracks SET language = ? WHERE id = ?", (lang, tid))
            # fused_v2 beside it, as the fuse stage writes both
            sql.execute(f"UPDATE track_embeddings SET {fv.FUSED_V2_COLUMN} = ? WHERE track_id = ?",
                        (fv._build_fused_v2(row, mert).astype(np.float32).tobytes(), tid))
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


def test_kannada_skips_after_a_move_to_hindi_remove_kannada(client, library):
    """The taste has moved to Hindi, so skipping Kannada is about Kannada."""
    h, L = library
    session = L["kn"][:3] + L["hi"][:3] + L["kn"][3:5]
    events = [done(t) for t in session[:6]] + [skip(t) for t in session[6:]]
    out = _post(client, h, session[-1], {"events": events, "exclude_ids": session})
    assert _share(out, L["kn"]) <= 0.1


def test_mood_skips_inside_a_language_keep_the_language(client, make_user, make_tracks, sql):
    """One sound, two moods, two languages: the session finishes four plain
    Kannada songs, then skips three Kannada songs that sound the same plus a
    mood the listener does not want. The query must avoid the mood and keep
    Kannada. Before 2026-10-20 the avoid term also carried what the skipped
    songs share with the taste -- the language -- and the top 10 turned
    Hindi. A regression guard: the angle bound alone already passes it."""
    rng = np.random.default_rng(29)
    sound, mood = rng.normal(size=fv.MERT_DIM), rng.normal(size=fv.MERT_DIM)
    uid, h = make_user()
    groups = {}
    for lang in ("kn", "hi"):
        for name, centre in (("plain", sound), ("mood", sound + 0.8 * mood)):
            ids = []
            for _ in range(40):
                mert = (centre + 0.5 * rng.normal(size=fv.MERT_DIM)).astype(np.float32)
                row = {c: 0.5 * (fv.SCALAR_RANGE[c][0] + fv.SCALAR_RANGE[c][1]) for c in fv.SCALAR_COLS}
                row["language"] = lang
                (tid,) = make_tracks(uid, [fv._build_fused(row, mert)], titles={0: _title()})
                sql.execute("UPDATE tracks SET language = ? WHERE id = ?", (lang, tid))
                ids.append(tid)
            groups[(lang, name)] = ids
    sql.commit()
    session = groups[("kn", "plain")][:4] + groups[("kn", "mood")][:3]
    events = [done(t) for t in session[:4]] + [skip(t) for t in session[4:]]
    out = _post(client, h, session[-1], {"events": events, "exclude_ids": session})
    kn = groups[("kn", "plain")] + groups[("kn", "mood")]
    moody = groups[("kn", "mood")] + groups[("hi", "mood")]
    assert _share(out, kn) >= 0.9
    assert _share(out, moody) <= 0.1


def test_an_off_target_seed_is_left_when_its_vibe_is_skipped(client, make_user, make_tracks, sql):
    """The only song finished was the wrong vibe (a party song for a listener
    who wants romance), then four party songs skipped. The query must leave
    party and stay in the language. Avoiding all of what the skips share
    with the taste (PR #23's first version) cancelled every skip -- the skips
    WERE the taste -- and the DJ held party for fifteen skips
    (indian-heartbreak-9c8502); only the language is protected now."""
    rng = np.random.default_rng(37)
    party, mellow = rng.normal(size=fv.MERT_DIM), rng.normal(size=fv.MERT_DIM)
    uid, h = make_user()
    groups = {}
    for lang, name, centre in (("hi", "party", party), ("hi", "mellow", mellow),
                               ("kn", "party", party), ("kn", "mellow", mellow)):
        ids = []
        for _ in range(40):
            mert = (centre + 0.5 * rng.normal(size=fv.MERT_DIM)).astype(np.float32)
            row = {c: 0.5 * (fv.SCALAR_RANGE[c][0] + fv.SCALAR_RANGE[c][1]) for c in fv.SCALAR_COLS}
            row["language"] = lang
            (tid,) = make_tracks(uid, [fv._build_fused(row, mert)], titles={0: _title()})
            sql.execute("UPDATE tracks SET language = ? WHERE id = ?", (lang, tid))
            ids.append(tid)
        groups[(lang, name)] = ids
    sql.commit()
    seed, skipped = groups[("hi", "party")][0], groups[("hi", "party")][1:5]
    events = ([{"id": seed, "action": "searched"}, done(seed)] + [skip(t) for t in skipped])
    out = _post(client, h, skipped[-1], {"events": events, "exclude_ids": [seed, *skipped]})
    hi = groups[("hi", "party")] + groups[("hi", "mellow")]
    assert _share(out, hi) >= 0.9
    assert _share(out, groups[("hi", "party")]) <= 0.5


def test_skips_of_another_sound_in_the_same_language_keep_the_language(client, library):
    """Four Kannada songs of one sound, then three Kannada songs of a wholly
    different sound skipped: N is that other sound plus Kannada. With the
    language the taste holds protected, the query leaves the sound and keeps
    Kannada (with all of N avoided, the top 10 turned Hindi)."""
    h, L = library
    session = L[("kn", 0)][:4] + L[("kn", 1)][:3]
    events = [done(t) for t in session[:4]] + [skip(t) for t in session[4:]]
    out = _post(client, h, session[-1], {"events": events, "exclude_ids": session})
    assert _share(out, L["kn"]) >= 0.9
    assert _share(out, L[("kn", 0)] + L[("hi", 0)]) >= 0.9


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
                                                  monkeypatch, use_conn, recency):
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
        monkeypatch.setattr(recency_service, "_DJ_RECENCY_WEIGHT", 0.0)
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
    use_conn(turso_shaped)
    monkeypatch.setenv("DB_BACKEND", "turso")
    dj_service._dj_mean_cache.clear()
    turso = _post(client, h, session[-1], body)
    assert turso["mode_used"] == "dj_replay_fused_turso"

    assert [t["id"] for t in turso["tracks"]] == [t["id"] for t in local["tracks"]]
    for a, b in zip(turso["tracks"], local["tracks"]):
        assert a["score"] == pytest.approx(b["score"], abs=1e-5)


def _turso_shaped(db):
    """The test file opened as db_client opens SQLite, with libSQL's vector
    functions and no ensure_db (which would add missing columns back)."""
    import db_client

    def connect():
        conn = sqlite3.connect(str(db), check_same_thread=False,
                               factory=db_client._SqliteConnection)
        conn.row_factory = sqlite3.Row
        _register_turso_functions(conn)
        return conn
    return connect


def _means(sql):
    return {r["model_version"]: len(json.loads(r["mean_json"]))
            for r in sql.execute("SELECT model_version, mean_json FROM user_embedding_mean")}


@pytest.mark.parametrize("v3_column", ["absent", "empty"])
def test_without_fused_v3_the_dj_ranks_fused_v2(client, library, app_module, db, sql,
                                                 monkeypatch, use_conn, caplog, v3_column):
    """A deploy that lands before scripts/refuse_vectors.py has filled
    fused_v3: production's shape, fused_v2 vectors in fused_v2_embedding and
    the fused_v3 column missing, or present with nothing in it (db.py adds
    it to every local file). The DJ must rank fused_v2 -- its column, width
    and mean-cache key -- as the previous revision did. Without the
    fallback an absent column is a 500 on every request (no such column)
    and an empty one serves nothing."""
    import logging
    h, L = library
    if v3_column == "absent":
        sql.execute(f"ALTER TABLE track_embeddings DROP COLUMN {fv.FUSED_COLUMN}")
    else:
        sql.execute(f"UPDATE track_embeddings SET {fv.FUSED_COLUMN} = NULL")
    sql.commit()
    turso_shaped = _turso_shaped(db)
    use_conn(turso_shaped)
    monkeypatch.setenv("DB_BACKEND", "turso")
    caplog.set_level(logging.WARNING, logger="vibescape")

    played = L["hi"][:3]
    body = {"events": [done(t) for t in played], "exclude_ids": played}
    for _ in range(2):
        out = _post(client, h, played[-1], body)
        assert out["mode_used"] == "dj_replay_fused_turso"
        assert _share(out, L["hi"]) >= 0.9
    assert _means(sql) == {fv.FUSED_V2_MODEL_VERSION: fv.FUSED_V2_DIM}
    warnings = [r for r in caplog.records
                if r.getMessage().startswith(f"[dj] {fv.FUSED_COLUMN} ")]
    assert len(warnings) == 1

    # The migration fills the column: picked up at the next check, no restart.
    if v3_column == "absent":
        sql.execute(f"ALTER TABLE track_embeddings ADD COLUMN "
                    f"{fv.FUSED_COLUMN} F32_BLOB({fv.FUSED_DIM})")
    sql.execute(f"UPDATE track_embeddings SET {fv.FUSED_COLUMN} = ? WHERE track_id = ?",
                (np.ones(fv.FUSED_DIM, dtype=np.float32).tobytes(), L["hi"][0]))
    sql.commit()
    vectors._fused_v3_state["checked_at"] = 0.0
    conn = turso_shaped()
    try:
        assert vectors._variant_spec("fused", conn) == (fv.FUSED_MODEL_VERSION, fv.FUSED_DIM)
    finally:
        conn.close()


def test_with_fused_v3_the_dj_ranks_fused_v3(client, library, sql):
    """A database where fused_v3 holds vectors: the DJ ranks those."""
    h, L = library
    played = L["hi"][:3]
    out = _post(client, h, played[-1], {"events": [done(t) for t in played],
                                        "exclude_ids": played})
    assert _share(out, L["hi"]) >= 0.9
    assert _means(sql) == {fv.FUSED_MODEL_VERSION: fv.FUSED_DIM}


def test_before_the_v2_migration_the_dj_ranks_fused_v1_instead_of_failing(
        client, library, app_module, db, sql, monkeypatch, use_conn, caplog):
    """A deploy that lands on a database from before fused_v2: no fused_v2
    or fused_v3 column, fused_v1 vectors in fused_embedding. Turso-shaped
    (no ensure_db, which would add the columns on SQLite). Without the
    fallback every request is a 500 (no such column)."""
    import logging
    h, L = library
    # fused_v1 vectors: the same MERT and scalars, the 11-slot language block
    ten = ["en", "hi", "kn", "te", "ta", "pa", "ml", "es", "fr", "pt"]
    for tid, lang, blob in sql.execute(
            f"SELECT te.track_id, t.language, te.{fv.FUSED_V2_COLUMN} FROM track_embeddings te "
            f"JOIN tracks t ON t.id = te.track_id").fetchall():
        v2 = np.frombuffer(blob, dtype=np.float32)
        head = v2[:fv.MERT_DIM + len(fv.SCALAR_COLS)]
        onehot = np.zeros(11, dtype=np.float32)
        onehot[ten.index(lang)] = 0.2
        v1 = np.concatenate([head, onehot])
        sql.execute("UPDATE track_embeddings SET fused_embedding = ? WHERE track_id = ?",
                    ((v1 / np.linalg.norm(v1)).astype(np.float32).tobytes(), tid))
    sql.execute(f"ALTER TABLE track_embeddings DROP COLUMN {fv.FUSED_COLUMN}")
    sql.execute(f"ALTER TABLE track_embeddings DROP COLUMN {fv.FUSED_V2_COLUMN}")
    sql.commit()

    turso_shaped = _turso_shaped(db)
    use_conn(turso_shaped)
    monkeypatch.setenv("DB_BACKEND", "turso")
    caplog.set_level(logging.WARNING, logger="vibescape")

    played = L["hi"][:3]
    body = {"events": [done(t) for t in played], "exclude_ids": played}
    for _ in range(2):
        out = _post(client, h, played[-1], body)
        assert out["mode_used"] == "dj_replay_fused_turso"
        assert _share(out, L["hi"]) >= 0.9
    warnings = [r for r in caplog.records
                if r.getMessage().startswith(f"[dj] {fv.FUSED_V2_COLUMN} ")]
    assert len(warnings) == 1

    # The v2 migration adds its column: picked up at the next check.
    sql.execute(f"ALTER TABLE track_embeddings ADD COLUMN "
                f"{fv.FUSED_V2_COLUMN} F32_BLOB({fv.FUSED_V2_DIM})")
    sql.commit()
    vectors._fused_v2_state["checked_at"] = 0.0
    vectors._fused_v3_state["checked_at"] = 0.0
    conn = turso_shaped()
    try:
        assert vectors._variant_spec("fused", conn) == (fv.FUSED_V2_MODEL_VERSION,
                                                           fv.FUSED_V2_DIM)
    finally:
        conn.close()
