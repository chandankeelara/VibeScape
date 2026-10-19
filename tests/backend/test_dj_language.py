"""The DJ's language re-rank: backend/dj_language.py (pure), its use in
POST /api/tracks/{seed}/similar, and parity between the two ranking paths."""
import sqlite3

import numpy as np
import pytest

import dj_language as dl
from ingest_pipeline import fused_vector as fv


# ------------------------------------------------------------- the mix

def test_shares_are_raw_so_a_rare_language_does_not_outpull_a_common_one():
    """Half English, half Malayalam: equal credit. The centred vector's
    bonus would favour Malayalam (~1 - 2p), tilting the session."""
    mix = dl.language_mix({"en": 2, "ml": 2}, {})
    assert dl.affinity("en", mix, {}) == pytest.approx(dl.affinity("ml", mix, {}))


def test_the_library_prior_offers_a_language_not_played_this_session():
    """A Kannada session from a listener whose library is a third Hindi:
    Hindi gets credit, a language they never play gets none."""
    mix = dl.language_mix({"kn": 3}, {"kn": 50, "hi": 50, "en": 0})
    assert dl.affinity("hi", mix, {}) > 0
    assert dl.affinity("es", mix, {}) == 0
    assert dl.affinity("kn", mix, {}) > dl.affinity("hi", mix, {})


def test_session_tracks_outvote_the_prior_as_they_accumulate():
    prior = {"en": 90, "kn": 10}
    few = dl.language_mix({"kn": 1}, prior)
    many = dl.language_mix({"kn": 10}, prior)
    assert few["kn"] < many["kn"]
    assert many["kn"] > many["en"]


def test_skips_and_repeats_do_not_count_toward_the_session():
    lang_of = {1: "kn", 2: "en", 3: "kn"}
    events = [{"id": 1, "action": "searched"}, {"id": 1, "action": "completed"},
              {"id": 2, "action": "skipped"}, {"id": 3, "action": "next"}]
    assert dl.session_languages(events, lang_of) == {"kn": 2}


def test_close_languages_get_partial_credit_and_it_can_be_turned_off():
    mix = dl.language_mix({"hi": 4}, {})
    assert 0 < dl.affinity("ur", mix) < dl.affinity("hi", mix)
    assert dl.affinity("ur", mix, {}) == 0
    assert dl.affinity("en", mix) == 0


def test_untagged_is_its_own_language():
    """An instrumental session asks for instrumentals; a Kannada one gives
    untagged tracks no credit (they used to share 'other' with every rare
    language in the vector)."""
    kn = dl.language_mix({"kn": 3}, {})
    inst = dl.language_mix({"": 3}, {})
    assert dl.affinity(None, kn) == 0
    assert dl.affinity(None, inst) == 1
    assert dl.lang_key(None) == dl.lang_key("") == ""


# ----------------------------------------------------------- the re-rank

def test_rerank_lifts_the_session_language_and_returns_the_whole_pool():
    cands = [(0.50, "en"), (0.49, "en"), (0.48, "kn"), (0.47, "en"), (0.46, "kn")]
    out = dl.rerank(cands, {"kn": 1.0}, limit=3, weight=3.0, close={})
    order = [i for i, _ in out]
    assert sorted(order) == list(range(5))
    assert order[:2] == [2, 4]


def test_rerank_is_relative_to_the_window_so_scale_does_not_matter():
    rng = np.random.default_rng(3)
    s = np.sort(rng.uniform(0.2, 0.4, 40))[::-1]
    langs = [rng.choice(["en", "kn", "hi", None]) for _ in s]
    mix = dl.language_mix({"kn": 2, "hi": 1}, {"en": 5, "kn": 1})
    a = [i for i, _ in dl.rerank(list(zip(s, langs)), mix, 10)]
    b = [i for i, _ in dl.rerank(list(zip(0.5 * s + 0.4, langs)), mix, 10)]
    assert a == b


@pytest.mark.parametrize("mix,weight", [({}, 1.0), ({"kn": 1.0}, 0.0)])
def test_rerank_leaves_the_order_alone_with_nothing_to_act_on(mix, weight):
    cands = [(0.5, "en"), (0.4, "kn"), (0.3, "kn")]
    assert dl.rerank(cands, mix, 2, weight) == [(0, 0.5), (1, 0.4), (2, 0.3)]


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
