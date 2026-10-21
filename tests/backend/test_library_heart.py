"""PUT / DELETE /api/me/library/{track_key}: the player's heart.

Membership is one user_tracks row. Unlinking removes that row and nothing
else (the catalogue, embeddings and the caller's history stay, as with
/api/ingest/clear), and every list the player reads stops offering the
track on its next request.
"""
import numpy as np
from ingest_pipeline.fused_vector import FUSED_DIM

ENV = {"session_id": "s-heart", "tz_offset_min": 0, "vibe": 50, "vibe_source": "user", "dj_mode": False}


def count(sql, q, *args):
    return sql.execute(q, args).fetchone()[0]


def linked(sql, uid, tid):
    return count(sql, "SELECT COUNT(*) FROM user_tracks WHERE user_id=? AND track_id=?", uid, tid)


def test_put_links_by_id_or_spotify_id_and_is_idempotent(client, sql, make_user, make_tracks):
    other, _ = make_user()
    me, h = make_user()
    a, b = make_tracks(other, [None, None])
    sp_b = sql.execute("SELECT spotify_id FROM tracks WHERE id=?", (b,)).fetchone()[0]

    for _ in range(2):
        r = client.put(f"/api/me/library/{a}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json() == {"in_library": True, "track_id": a}
    assert linked(sql, me, a) == 1
    assert tuple(sql.execute("SELECT source, added_at IS NOT NULL FROM user_tracks "
                       "WHERE user_id=? AND track_id=?", (me, a)).fetchone()) == ("heart", 1)

    r = client.put(f"/api/me/library/{sp_b}", headers=h)
    assert r.json() == {"in_library": True, "track_id": b}
    assert linked(sql, me, b) == 1


def test_put_keeps_an_existing_link(client, sql, make_user, make_tracks):
    me, h = make_user()
    (a,) = make_tracks(me, [None])
    sql.execute("UPDATE user_tracks SET source='liked', added_at='2026-01-01 00:00:00' "
                "WHERE user_id=? AND track_id=?", (me, a))
    sql.commit()
    assert client.put(f"/api/me/library/{a}", headers=h).status_code == 200
    assert tuple(sql.execute("SELECT source, added_at FROM user_tracks WHERE user_id=? AND track_id=?",
                       (me, a)).fetchone()) == ("liked", "2026-01-01 00:00:00")


def test_delete_unlinks_only_the_callers_row_and_keeps_history(client, sql, make_user, make_tracks):
    me, h = make_user()
    other, _ = make_user()
    (a,) = make_tracks(me, [np.ones(FUSED_DIM)])
    sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (other, a))
    sql.commit()
    r = client.post("/api/events", headers=h, json={"events": [
        {**ENV, "type": "play_start", "track_id": a},
        {**ENV, "type": "play_end", "track_id": a, "reason": "completed",
         "position_ms": 200000, "duration_ms": 200000, "listened_ms": 200000},
    ]})
    assert r.status_code == 202, r.text

    for _ in range(2):
        r = client.delete(f"/api/me/library/{a}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json() == {"in_library": False, "track_id": a}
    assert linked(sql, me, a) == 0
    assert linked(sql, other, a) == 1
    assert count(sql, "SELECT COUNT(*) FROM tracks WHERE id=?", a) == 1
    assert count(sql, "SELECT LENGTH(fused_v2_embedding) FROM track_embeddings WHERE track_id=?", a) == FUSED_DIM * 4
    assert count(sql, "SELECT COUNT(*) FROM track_events WHERE user_id=? AND track_id=?", me, a) == 2
    assert count(sql, "SELECT COUNT(*) FROM user_track_stats WHERE user_id=? AND track_id=?", me, a) == 1


def test_unknown_track_is_404_and_a_session_is_required(client, make_user):
    _, h = make_user()
    for call in (client.put, client.delete):
        r = call("/api/me/library/999999", headers=h)
        assert r.status_code == 404 and r.json()["detail"] == {"error": "track_not_found"}
        r = call("/api/me/library/nope", headers=h)
        assert r.status_code == 404
        assert call("/api/me/library/1").status_code == 401


def test_player_payloads_carry_in_library(client, make_user, make_tracks, clusters):
    me, h = make_user()
    ids = make_tracks(me, clusters["A"](6), titles={0: "Heartsong"})
    lists = {
        "tracks": client.get("/api/tracks", headers=h).json(),
        "random": [client.get("/api/tracks/random", params={"vibe": 50}, headers=h).json()],
        "search": client.get("/api/tracks/search", params={"q": "heartsong"}, headers=h).json()["tracks"],
        "vibe": client.get(f"/api/tracks/{ids[0]}/similar", headers=h).json()["tracks"],
        "dj": client.post(f"/api/tracks/{ids[0]}/similar", headers=h,
                          json={"mode": "dj", "limit": 8}).json()["tracks"],
    }
    for name, tracks in lists.items():
        assert tracks, name
        assert all(t["in_library"] is True for t in tracks), name
        assert all(t["discover"] is False for t in tracks), name


def test_discovery_is_the_exception_and_tags_each_track_itself(client, make_user, make_tracks, clusters):
    """/similar with `discover` reads outside the caller's library, so the
    default above does not hold there: each row says what it is."""
    me, h = make_user()
    other, _ = make_user()
    mine = make_tracks(me, clusters["A"](3), artist="Mine")
    theirs = make_tracks(other, clusters["A"](3), artist="Theirs")
    for out in (client.get(f"/api/tracks/{mine[0]}/similar", params={"discover": 1}, headers=h).json(),
                client.post(f"/api/tracks/{mine[0]}/similar", headers=h,
                            json={"mode": "dj", "limit": 8, "discover": True}).json()):
        got = {t["id"]: (t["in_library"], t["discover"]) for t in out["tracks"]}
        assert got == {**{t: (True, False) for t in mine[1:]},
                       **{t: (False, True) for t in theirs}}, out["mode_used"]


def test_unlinked_track_leaves_dj_search_and_lists_on_the_next_request(client, make_user, make_tracks, clusters):
    me, h = make_user()
    ids = make_tracks(me, clusters["A"](6), titles={1: "Heartsong", 2: "Heartsongs"})
    gone, weaker = ids[1], ids[2]

    def dj():
        r = client.post(f"/api/tracks/{ids[0]}/similar", headers=h, json={"mode": "dj", "limit": 8})
        return [t["id"] for t in r.json()["tracks"]]

    def search():
        # limit 1: a stale cached hit would take the only slot and come back empty
        return [t["id"] for t in client.get("/api/tracks/search", params={"q": "heartsong", "limit": 1},
                                            headers=h).json()["tracks"]]
    assert gone in dj() and search() == [gone]          # warms the search cache too

    assert client.delete(f"/api/me/library/{gone}", headers=h).status_code == 200
    assert gone not in dj()
    assert search() == [weaker]
    assert gone not in [t["id"] for t in client.get("/api/tracks", headers=h).json()]
    assert gone not in [t["id"] for t in client.get(f"/api/tracks/{ids[0]}/similar",
                                                    headers=h).json()["tracks"]]

    assert client.put(f"/api/me/library/{gone}", headers=h).status_code == 200
    assert gone in dj() and search() == [gone]


def test_search_drops_a_track_another_instance_unlinked(client, sql, make_user, make_tracks, app_module):
    """The narrow-row cache is per process; on Cloud Run the unlink may have
    landed on a different instance, so only the cache here is stale."""
    me, h = make_user()
    other, _ = make_user()
    (a,) = make_tracks(me, [None], titles={0: "Heartsong"})
    sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (other, a))
    q = {"q": "heartsong"}
    assert [t["id"] for t in client.get("/api/tracks/search", params=q, headers=h).json()["tracks"]] == [a]
    sql.execute("DELETE FROM user_tracks WHERE user_id=? AND track_id=?", (me, a))
    sql.commit()
    assert client.get("/api/tracks/search", params=q, headers=h).json()["tracks"] == []
