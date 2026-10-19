"""The DJ's language re-rank: backend/dj_language.py (pure), its use in
POST /api/tracks/{seed}/similar, and parity between the two ranking paths."""
import sqlite3

import numpy as np
import pytest

import dj_language as dl
from ingest_pipeline import fused_vector as fv


def done(t):
    return {"id": t, "action": "completed", "played_ratio": 1.0}


def skip(t):
    return {"id": t, "action": "skipped", "played_ratio": 0.1}


def langs(**by_lang):
    """langs(kn=[1, 2], hi=[3]) -> {1: 'kn', 2: 'kn', 3: 'hi'}"""
    return {t: k for k, ts in by_lang.items() for t in ts}


# --------------------------------------------------- the session's scores

def test_shares_are_raw_so_a_rare_language_does_not_outpull_a_common_one():
    """Equal scores, equal credit. The centred vector's bonus would favour
    Malayalam (~1 - 2p), tilting a mixed session toward it."""
    scores = {"en": 1.0, "ml": 1.0}
    assert dl.affinity("en", scores, {}) == pytest.approx(dl.affinity("ml", scores, {}))


def test_one_stray_skip_does_not_flip_a_language():
    lo = langs(kn=list(range(1, 6)))
    s = dl.session_scores([done(1), done(2), done(3), done(4), skip(5)], lo)
    assert dl.affinity("kn", s) == 1.0


def test_two_or_three_skips_demote_the_language():
    """Three Kannada songs finished, then Kannada skips: the language goes
    negative, below every language the session has said nothing about."""
    lo = langs(kn=list(range(1, 7)))
    for n in (2, 3):
        s = dl.session_scores([done(1), done(2), done(3)] + [skip(t) for t in range(4, 4 + n)], lo)
        assert dl.affinity("kn", s) < 0
        assert dl.affinity("en", s) == 0


def test_a_switch_is_followed_within_three_songs():
    """Five Kannada songs, then Hindi: recency, not volume, decides. (In the
    replay benchmark the ranking itself follows after two: the replayed
    query moves with the last songs too.)"""
    lo = langs(kn=list(range(1, 6)), hi=[6, 7, 8])
    base = [done(t) for t in range(1, 6)]
    three = dl.session_scores(base + [done(6), done(7), done(8)], lo)
    assert dl.affinity("hi", three, {}) > dl.affinity("kn", three, {})


def test_recent_songs_count_more_than_old_ones():
    lo = langs(kn=[1], hi=[2])
    s = dl.session_scores([done(1), done(2)], lo)
    assert s["hi"] == 1.0 and s["kn"] == pytest.approx(0.5 ** (1 / dl.HALF_LIFE))


def test_each_track_counts_once_by_its_last_verdict():
    lo = langs(kn=[1, 2], hi=[3])
    s = dl.session_scores([{"id": 1, "action": "searched"}, done(1),
                           {"id": 2, "action": "searched"}, skip(2),
                           {"id": 3, "action": "next", "played_ratio": 0.6}], lo)
    assert s["hi"] == dl.NEXT_CREDIT
    assert s["kn"] == pytest.approx(-dl.SKIP_PENALTY * 0.5 ** (1 / dl.HALF_LIFE)
                                    + 0.5 ** (2 / dl.HALF_LIFE))


def test_cold_start_uses_the_seed_language_never_anything_else():
    lo = langs(kn=[1], hi=[2])
    assert dl.session_scores([], lo, seed=2) == {"hi": 1.0}
    # a seed the session skipped is no signal for its language
    assert dl.session_scores([skip(2)], lo, seed=2)["hi"] < 0
    # once something is accepted the seed adds nothing of its own
    assert dl.session_scores([done(1)], lo, seed=2) == {"kn": 1.0}


def test_close_languages_get_partial_credit_but_never_negative():
    s = {"hi": 2.0}
    assert 0 < dl.affinity("ur", s) < dl.affinity("hi", s)
    assert dl.affinity("ur", s, {}) == 0
    assert dl.affinity("en", s) == 0
    # skipping Hindi demotes Hindi, not Urdu
    assert dl.affinity("ur", {"hi": -2.0}) == 0


def test_untagged_is_its_own_language():
    """An instrumental session asks for instrumentals; a Kannada one gives
    untagged tracks no credit (they used to share 'other' with every rare
    language in the vector)."""
    lo = {1: "kn", 2: None, 3: ""}
    assert dl.affinity(None, dl.session_scores([done(1)], lo)) == 0
    assert dl.affinity(None, dl.session_scores([done(2), done(3)], lo)) == 1
    assert dl.lang_key(None) == dl.lang_key("") == ""


# ----------------------------------------------------------- the re-rank

def test_rerank_lifts_the_session_language_and_returns_the_whole_pool():
    cands = [(0.50, "en"), (0.49, "en"), (0.48, "kn"), (0.47, "en"), (0.46, "kn")]
    out = dl.rerank(cands, {"kn": 1.0}, limit=3, weight=3.0, close={})
    order = [i for i, _ in out]
    assert sorted(order) == list(range(5))
    assert order[:2] == [2, 4]


def test_rerank_pushes_a_skipped_language_below_the_rest():
    cands = [(0.50, "kn"), (0.49, "kn"), (0.48, "en"), (0.47, "kn"), (0.46, "hi")]
    order = [i for i, _ in dl.rerank(cands, {"kn": -1.0}, limit=3, weight=3.0)]
    assert order[:2] == [2, 4]


def test_rerank_is_relative_to_the_window_so_scale_does_not_matter():
    rng = np.random.default_rng(3)
    s = np.sort(rng.uniform(0.2, 0.4, 40))[::-1]
    ls = [rng.choice(["en", "kn", "hi", None]) for _ in s]
    scores = {"kn": 1.2, "hi": 0.4, "en": -0.5}
    a = [i for i, _ in dl.rerank(list(zip(s, ls)), scores, 10)]
    b = [i for i, _ in dl.rerank(list(zip(0.5 * s + 0.4, ls)), scores, 10)]
    assert a == b


@pytest.mark.parametrize("scores,weight", [({}, 1.0), ({"kn": 0.0}, 1.0), ({"kn": 1.0}, 0.0)])
def test_rerank_leaves_the_order_alone_with_nothing_to_act_on(scores, weight):
    cands = [(0.5, "en"), (0.4, "kn"), (0.3, "kn")]
    assert dl.rerank(cands, scores, 2, weight) == [(0, 0.5), (1, 0.4), (2, 0.3)]


def test_a_flat_window_leaves_the_order_alone():
    cands = [(0.5, "en"), (0.5, "kn"), (0.5, "kn")]
    assert [i for i, _ in dl.rerank(cands, {"kn": 1.0}, 3)] == [0, 1, 2]


# --------------------------------------------- the vector recipe vs the app

def test_the_app_and_the_recipe_agree_on_the_space(app_module):
    assert app_module.FUSED_MODEL_VERSION == fv.FUSED_MODEL_VERSION
    assert app_module.FUSED_DIM == fv.FUSED_DIM == 788
    assert app_module.FUSED_LANG_DIMS == fv.LANG_DIMS


# ------------------------------------------------------------ the endpoint

FUSED_DIM = 788


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


@pytest.fixture
def bilingual(make_user, make_tracks, sql):
    """One sound, two languages: 40 tracks drawn around a single centre, the
    even ones Malayalam and the odd ones English, plus 40 elsewhere so the
    library mean is not the cluster itself. Vectors carry no language, so
    only the re-rank can tell the two halves apart."""
    rng = np.random.default_rng(11)
    base = rng.normal(size=FUSED_DIM) * 3.0
    c, other = rng.normal(size=FUSED_DIM), rng.normal(size=FUSED_DIM)
    uid, h = make_user()
    near = make_tracks(uid, [unit(base + c + 0.25 * rng.normal(size=FUSED_DIM)) for _ in range(40)])
    far = make_tracks(uid, [unit(base + other + 0.25 * rng.normal(size=FUSED_DIM)) for _ in range(40)])
    for i, t in enumerate(near):
        sql.execute("UPDATE tracks SET language = ? WHERE id = ?", ("ml" if i % 2 == 0 else "en", t))
    for t in far:
        sql.execute("UPDATE tracks SET language = 'en' WHERE id = ?", (t,))
    sql.commit()
    ml = [t for i, t in enumerate(near) if i % 2 == 0]
    return uid, h, ml, near


def _post(client, h, seed, body):
    r = client.post(f"/api/tracks/{seed}/similar", json={"mode": "dj", "limit": 8, **body},
                    headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _ml_session(ml):
    played = ml[:4]
    events = [{"id": t, "action": "completed", "played_ratio": 1} for t in played]
    return played, {"events": events, "exclude_ids": played}


def test_a_malayalam_session_gets_malayalam(client, bilingual, app_module, monkeypatch):
    """Fails with the re-rank disabled in place (DJ_LANG_WEIGHT=0): the
    vectors cannot separate the halves, so about half the picks are English."""
    uid, h, ml, near = bilingual
    played, body = _ml_session(ml)
    out = _post(client, h, played[-1], body)
    got = [t["id"] for t in out["tracks"]]
    assert sum(t in ml for t in got) >= 7, got
    # `score` keeps meaning cosine; the adjusted value is final_score.
    t0 = out["tracks"][0]
    assert t0["final_score"] > t0["score"]

    monkeypatch.setattr(app_module, "_DJ_LANG_WEIGHT", 0.0)
    off = [t["id"] for t in _post(client, h, played[-1], body)["tracks"]]
    assert sum(t in ml for t in off) <= 6, off


@pytest.fixture
def kannada_library(make_user, make_tracks, sql):
    """A library that is 70% Kannada and 30% Hindi, every track around one
    sound (plus a second cluster for the mean), so the vectors cannot tell
    the languages apart and only the re-rank can."""
    rng = np.random.default_rng(13)
    base = rng.normal(size=FUSED_DIM) * 3.0
    c, other = rng.normal(size=FUSED_DIM), rng.normal(size=FUSED_DIM)
    uid, h = make_user()
    near = make_tracks(uid, [unit(base + c + 0.25 * rng.normal(size=FUSED_DIM)) for _ in range(60)])
    far = make_tracks(uid, [unit(base + other + 0.25 * rng.normal(size=FUSED_DIM)) for _ in range(40)])
    hi = [t for i, t in enumerate(near) if i % 10 < 3] + [t for i, t in enumerate(far) if i % 10 < 3]
    for t in near + far:
        sql.execute("UPDATE tracks SET language = ? WHERE id = ?", ("hi" if t in hi else "kn", t))
    sql.commit()
    return h, [t for t in near if t in hi]


def test_a_kannada_library_playing_hindi_gets_hindi(client, kannada_library):
    h, hi = kannada_library
    played = hi[:3]
    out = _post(client, h, played[-1], {"events": [done(t) for t in played],
                                        "exclude_ids": played})
    assert sum(t["id"] in hi for t in out["tracks"]) >= 7, out["tracks"]


def test_cold_start_on_a_hindi_seed_follows_the_seed_not_the_library(client, kannada_library):
    """No accepted song yet: the seed's language is the only signal. A
    library prior (PR #21's first version) made this Kannada."""
    h, hi = kannada_library
    out = _post(client, h, hi[0], {"events": []})
    assert out["mode_used"] == "dj_replay_fused_from_seed"
    assert sum(t["id"] in hi for t in out["tracks"]) >= 7, out["tracks"]


def test_kannada_skips_push_kannada_out_even_when_it_fills_the_neighbourhood(
        client, make_user, make_tracks, sql):
    """200 Kannada tracks around the session's sound, 40 Hindi ones in a
    cluster next to it, 60 English far away. After three Kannada skips the
    first Hindi track ranks about 190th by cosine: outside the usual
    150-track pool, so the demotion has nothing to promote unless the pool
    widens while a language is being skipped. Fails with the widening
    disabled."""
    rng = np.random.default_rng(17)
    base = rng.normal(size=FUSED_DIM) * 3.0
    c, e, far = (rng.normal(size=FUSED_DIM) for _ in range(3))
    uid, h = make_user()
    kn = make_tracks(uid, [unit(base + c + 0.25 * rng.normal(size=FUSED_DIM)) for _ in range(200)])
    hi = make_tracks(uid, [unit(base + c + 0.4 * e + 0.25 * rng.normal(size=FUSED_DIM))
                           for _ in range(40)])
    en = make_tracks(uid, [unit(base + far + 0.25 * rng.normal(size=FUSED_DIM)) for _ in range(60)])
    for lang, ts in (("kn", kn), ("hi", hi), ("en", en)):
        for t in ts:
            sql.execute("UPDATE tracks SET language = ? WHERE id = ?", (lang, t))
    sql.commit()
    session = kn[:6]
    events = [done(t) for t in session[:3]] + [skip(t) for t in session[3:]]
    out = _post(client, h, session[-1], {"events": events, "exclude_ids": session})
    assert sum(t["id"] in kn for t in out["tracks"]) <= 1, out["tracks"]


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
def test_both_ranking_paths_return_the_same_order(client, bilingual, app_module, db,
                                                  monkeypatch, recency):
    """The numpy path (SQLite) and the vector_distance_cos path (Turso),
    with the language re-rank active on both. The Turso SQL runs on SQLite
    with libSQL's vector functions stood in for; what it cannot show is
    libSQL's own float rounding (see the PR)."""
    import db_client
    uid, h, ml, near = bilingual
    played, body = _ml_session(ml)
    if not recency:
        monkeypatch.setattr(app_module, "_DJ_RECENCY_WEIGHT", 0.0)
    local = _post(client, h, played[-1], body)
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
    turso = _post(client, h, played[-1], body)
    assert turso["mode_used"] == "dj_replay_fused_turso"

    assert [t["id"] for t in turso["tracks"]] == [t["id"] for t in local["tracks"]]
    for a, b in zip(turso["tracks"], local["tracks"]):
        assert a["score"] == pytest.approx(b["score"], abs=1e-5)
        assert a["final_score"] == pytest.approx(b["final_score"], abs=1e-5)
    assert sum(t["id"] in ml for t in turso["tracks"]) >= 7
