"""Discovery mode: POST/GET /api/tracks/{seed}/similar with `discover`.

The same ranking over the whole catalogue instead of the caller's library;
each track then carries its real `in_library` (this row's link) and
`discover` (not in the library, and no library song shares its same_song
key). Two users: A owns cluster A, B owns cluster B, so for A every B track
is outside the library.
"""
import sqlite3

import pytest

ENV = {"session_id": "s-disc", "tz_offset_min": 0, "vibe": 50, "vibe_source": "user", "dj_mode": True}


def done(t):
    return {"id": t, "action": "completed", "played_ratio": 1.0}


def _post(client, h, seed, body):
    r = client.post(f"/api/tracks/{seed}/similar",
                    json={"mode": "dj", "limit": 25, **body}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def two(make_user, make_tracks, clusters):
    """A owns 30 A-cluster tracks, B owns 10 B-cluster tracks, so a top 25
    with discovery on holds B's (minus the log) and then A's own."""
    a_uid, a_h = make_user()
    b_uid, _ = make_user()
    a = make_tracks(a_uid, clusters["A"](30), artist="Artist A")
    b = make_tracks(b_uid, clusters["B"](10), artist="Artist B")
    return a_h, a, b


def _turso_shaped(use_conn, monkeypatch, app_module, db):
    """The Turso ranking SQL on the test file, with libSQL's vector functions
    stood in for (test_dj_paths._register_turso_functions)."""
    import db_client
    from test_dj_paths import _register_turso_functions

    def conn():
        c = sqlite3.connect(str(db), check_same_thread=False, factory=db_client._SqliteConnection)
        c.row_factory = sqlite3.Row
        _register_turso_functions(c)
        return c
    use_conn(conn)
    monkeypatch.setenv("DB_BACKEND", "turso")
    app_module._dj_mean_cache.clear()


def test_off_stays_in_the_library_and_on_reaches_the_catalogue(client, two):
    h, a, b = two
    log = b[:3]
    body = {"events": [done(t) for t in log], "exclude_ids": log}

    off = _post(client, h, log[-1], body)
    assert off["mode_used"] == "dj_replay_fused"
    assert off["tracks"] and not any(t["id"] in b for t in off["tracks"])
    assert all(t["in_library"] is True and t["discover"] is False for t in off["tracks"])

    on = _post(client, h, log[-1], {**body, "discover": True})
    assert on["mode_used"] == "dj_replay_fused_discover"
    got = [t["id"] for t in on["tracks"]]
    assert got[:7] and set(got[:7]) <= set(b[3:]), got      # the log's cluster first
    for t in on["tracks"]:
        mine = t["id"] in a
        assert t["in_library"] is mine and t["discover"] is (not mine), t["id"]
    assert any(t["id"] in a for t in on["tracks"])


@pytest.mark.parametrize("recency", [True, False])
def test_the_turso_path_ranks_the_catalogue_without_the_numpy_fallback(
        client, two, app_module, db, monkeypatch, use_conn, recency):
    """`_UTS_JOIN` names the user_tracks alias that discovery drops: with it,
    the Turso SQL fails and the numpy path answers (mode_used loses _turso),
    which on Turso reads every fused vector. Without the re-rank there is no
    stats join and the user id binds nowhere."""
    h, a, b = two
    if not recency:
        monkeypatch.setattr(app_module, "_DJ_RECENCY_WEIGHT", 0.0)
    log = b[:3]
    body = {"events": [done(t) for t in log], "exclude_ids": log, "discover": True}
    local = _post(client, h, log[-1], body)
    _turso_shaped(use_conn, monkeypatch, app_module, db)
    turso = _post(client, h, log[-1], body)
    assert turso["mode_used"] == "dj_replay_fused_turso_discover"
    assert [t["id"] for t in turso["tracks"]] == [t["id"] for t in local["tracks"]]
    assert set(b[3:]) <= {t["id"] for t in turso["tracks"]}


@pytest.mark.parametrize("recency", [True, False])
def test_get_similar_with_discover_returns_catalogue_tracks(client, sql, two, app_module,
                                                            monkeypatch, recency):
    """The vibe path: B's tracks share the seed's vibe, A's others do not.
    Without the re-rank there is no stats join to bind the user id to."""
    h, a, b = two
    if not recency:
        monkeypatch.setattr(app_module, "_DJ_RECENCY_WEIGHT", 0.0)
    seed = a[0]
    sql.execute(f"UPDATE tracks SET vibe_score_ml = 0.1 WHERE id IN ({','.join('?' * len(a))})", a)
    sql.execute(f"UPDATE tracks SET vibe_score_ml = 0.9 WHERE id IN ({','.join('?' * (len(b) + 1))})",
                (*b, seed))
    sql.commit()
    off = client.get(f"/api/tracks/{seed}/similar", params={"limit": 5}, headers=h).json()
    assert off["mode_used"] == "vibe" and not any(t["id"] in b for t in off["tracks"])
    on = client.get(f"/api/tracks/{seed}/similar", params={"limit": 5, "discover": 1}, headers=h).json()
    assert on["mode_used"] == "vibe_discover"
    assert [t["id"] in b for t in on["tracks"]] == [True] * 5
    assert all(t["discover"] is True and t["in_library"] is False for t in on["tracks"])


def test_another_release_of_a_library_song_is_not_a_discovery(client, sql, make_user, make_tracks, two):
    """A owns "Kaveri" (no vector, so it is never a candidate); B's copy is
    the single. The heart is empty (that row is not linked) and there is no
    Discover tag (the song is A's)."""
    h, a, b = two
    a_uid = sql.execute("SELECT user_id FROM user_tracks WHERE track_id=?", (a[0],)).fetchone()[0]
    make_tracks(a_uid, [None], artist="Artist B", titles={0: "Kaveri"})
    sql.execute("UPDATE tracks SET title = 'Kaveri - Single' WHERE id = ?", (b[5],))
    sql.commit()
    log = b[:3]
    out = _post(client, h, log[-1], {"events": [done(t) for t in log], "exclude_ids": log,
                                     "discover": True})
    by_id = {t["id"]: t for t in out["tracks"]}
    assert (by_id[b[5]]["in_library"], by_id[b[5]]["discover"]) == (False, False)
    assert by_id[b[6]]["discover"] is True


@pytest.mark.parametrize("path", ["numpy", "turso"])
def test_a_skipped_discovery_is_penalised_on_the_next_request(
        client, two, app_module, db, monkeypatch, use_conn, path):
    h, a, b = two
    heard = b[5]
    r = client.post("/api/events", headers=h, json={"events": [
        {**ENV, "type": "play_start", "track_id": heard},
        {**ENV, "type": "play_end", "track_id": heard, "reason": "skipped",
         "position_ms": 20000, "duration_ms": 200000, "listened_ms": 20000},
    ]})
    assert r.status_code == 202, r.text
    if path == "turso":
        _turso_shaped(use_conn, monkeypatch, app_module, db)
    log = b[:3]
    # fewer candidates than the limit, so the penalised track stays in it
    out = _post(client, h, log[-1], {"events": [done(t) for t in log],
                                     "exclude_ids": log + a[:20],
                                     "discover": True, "explain": True})
    by_id = {t["id"]: t for t in out["tracks"]}
    assert by_id[heard]["final_score"] < by_id[heard]["score"]
    assert by_id[b[6]]["final_score"] == pytest.approx(by_id[b[6]]["score"])


def test_the_dj_vibe_fallback_discovers_and_tags_too(client, sql, make_tracks, two):
    """A seed with no vector and no log drops to the vibe ranking, which
    overwrites mode_used; discovery and its tags must survive that."""
    h, a, b = two
    a_uid = sql.execute("SELECT user_id FROM user_tracks WHERE track_id=?", (a[0],)).fetchone()[0]
    (seed,) = make_tracks(a_uid, [None], artist="Artist A", titles={0: "Pending"})
    out = _post(client, h, seed, {"discover": True})
    assert out["mode_used"] == "vibe_fallback_no_seed_embedding_discover"
    tags = {t["id"]: (t["in_library"], t["discover"]) for t in out["tracks"]}
    assert set(tags) & set(b)                            # all tie; titles break it
    assert all(tags[t] == ((True, False) if t in a else (False, True)) for t in tags)
