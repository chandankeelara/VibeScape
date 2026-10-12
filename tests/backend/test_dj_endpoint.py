"""POST /api/tracks/{seed}/similar in DJ mode, end to end over HTTP."""
import json


def post(client, headers, seed, body):
    r = client.post(f"/api/tracks/{seed}/similar", json={"mode": "dj", "limit": 8, **body},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_requires_a_session(client):
    r = client.post("/api/tracks/1/similar", json={"mode": "dj", "events": []})
    assert r.status_code == 401


def test_replay_follows_a_search_into_a_new_cluster(client, make_user, make_tracks, clusters):
    uid, h = make_user()
    a = make_tracks(uid, clusters["A"](30), artist="A")
    b = make_tracks(uid, clusters["B"](30), artist="B")
    searched = b[0]
    # a long session in A, then a search for a B track, which is now the seed
    events = [{"id": t, "action": "completed", "played_ratio": 1} for t in a[:12]]
    events.append({"id": searched, "action": "searched", "played_ratio": None})
    out = post(client, h, searched, {"events": events, "exclude_ids": a[:12]})
    got = [t["id"] for t in out["tracks"]]
    assert out["mode_used"] == "dj_replay_fused"
    assert searched not in got                          # the seed itself is never returned
    assert sum(t in b for t in got) >= 7, got           # but its search steers the picks


def test_legacy_path_drops_the_seed_and_stays_in_the_old_cluster(client, make_user, make_tracks, clusters):
    """Documents why the replay exists: the weighted path ignores the seed's
    own verdict, so a search-and-play cannot steer the picks while it plays."""
    uid, h = make_user()
    a = make_tracks(uid, clusters["A"](30), artist="A")
    b = make_tracks(uid, clusters["B"](30), artist="B")
    searched = b[0]
    positives = [{"id": t, "weight": 1.0} for t in a[:12]] + [{"id": searched, "weight": 0.8}]
    out = post(client, h, searched, {"positive_ids": positives, "negative_ids": [],
                                     "exclude_ids": a[:12]})
    got = [t["id"] for t in out["tracks"]]
    assert out["mode_used"] == "dj_fused"
    assert sum(t in a for t in got) >= 7, got


def test_cold_start_uses_the_seed_not_random(client, make_user, make_tracks, clusters):
    uid, h = make_user()
    make_tracks(uid, clusters["A"](30), artist="A")
    b = make_tracks(uid, clusters["B"](30), artist="B")
    out = post(client, h, b[0], {"events": []})
    assert out["mode_used"] == "dj_replay_fused_from_seed"
    assert sum(t["id"] in b for t in out["tracks"]) >= 7


def test_seed_without_a_vector_does_not_discard_the_session(client, make_user, make_tracks, clusters):
    uid, h = make_user()
    b = make_tracks(uid, clusters["B"](30), artist="B")
    (pending,) = make_tracks(uid, [None])               # e.g. a just-searched, unanalysed track
    events = [{"id": t, "action": "completed", "played_ratio": 1} for t in b[:5]]
    out = post(client, h, pending, {"events": events, "exclude_ids": b[:5]})
    assert out["mode_used"].startswith("dj_replay")
    assert sum(t["id"] in b for t in out["tracks"]) >= 7


def test_spotify_ids_in_events_resolve(client, make_user, make_tracks, clusters, sql):
    uid, h = make_user()
    b = make_tracks(uid, clusters["B"](20), artist="B")
    make_tracks(uid, clusters["A"](20), artist="A")
    sid = sql.execute("SELECT spotify_id FROM tracks WHERE id = ?", (b[0],)).fetchone()[0]
    out = post(client, h, b[1], {"events": [{"id": sid, "action": "searched"}], "exclude_ids": [b[0]]})
    assert sum(t["id"] in b for t in out["tracks"]) >= 7


def test_library_mean_is_stored_per_user(client, make_user, make_tracks, clusters, sql, app_module):
    uid, h = make_user()
    a = make_tracks(uid, clusters["A"](60), artist="A")
    post(client, h, a[0], {"events": [{"id": a[1], "action": "completed", "played_ratio": 1}]})
    row = sql.execute("SELECT n_tracks, mean_json FROM user_embedding_mean WHERE user_id = ?",
                      (uid,)).fetchone()
    assert row is not None and row["n_tracks"] == 60
    assert len(json.loads(row["mean_json"])) == 788


def test_small_library_uses_the_global_mean(client, make_user, make_tracks, clusters, sql):
    uid, h = make_user()
    a = make_tracks(uid, clusters["A"](10), artist="A")      # under _DJ_MEAN_MIN_TRACKS
    post(client, h, a[0], {"events": [{"id": a[1], "action": "completed", "played_ratio": 1}]})
    scopes = {r[0] for r in sql.execute("SELECT user_id FROM user_embedding_mean")}
    assert scopes == {0}


def test_unknown_event_actions_never_cause_an_error(client, make_user, make_tracks, clusters):
    uid, h = make_user()
    a = make_tracks(uid, clusters["A"](20))
    out = post(client, h, a[0], {"events": [{"id": a[1], "action": "from-the-future"}, "junk", None]})
    assert out["mode_used"] == "dj_replay_fused_from_seed"
