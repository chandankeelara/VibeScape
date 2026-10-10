"""GET /api/me/stats."""
from datetime import datetime, timedelta, timezone


def test_requires_a_session(client):
    assert client.get("/api/me/stats").status_code == 401


def test_empty_user_gets_zeros_not_errors(client, make_user):
    _, h = make_user()
    r = client.get("/api/me/stats", headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["all_time"]["listened_ms"] == 0 and j["all_time"]["completion_rate"] is None
    assert j["all_time"]["last_played"] is None and j["window"]["sessions"] == 0
    assert j["window"]["by_local_hour_ms"] == [0] * 24


def test_totals_rates_and_top_lists(client, make_user, make_tracks):
    uid, h = make_user()
    t1, t2, t3 = make_tracks(uid, [None, None, None], titles={0: "Long", 1: "Short", 2: "Skipped"})
    env = {"session_id": "s", "tz_offset_min": 0, "vibe_source": "user"}
    client.post("/api/events", headers=h, json={"events": [
        {**env, "type": "play_start", "track_id": t1},
        {**env, "type": "play_end", "track_id": t1, "reason": "completed", "position_ms": 200000, "listened_ms": 200000},
        {**env, "type": "play_start", "track_id": t2},
        {**env, "type": "play_end", "track_id": t2, "reason": "completed", "position_ms": 100000, "listened_ms": 100000},
        {**env, "type": "play_start", "track_id": t3},
        {**env, "type": "play_end", "track_id": t3, "reason": "skipped", "position_ms": 30000, "listened_ms": 10000},
        {**env, "type": "session_end", "data": {"duration_ms": 600000}},
        {**env, "type": "session_end", "data": {"duration_ms": 200000}},
    ]})
    j = client.get("/api/me/stats", headers=h).json()
    a = j["all_time"]
    assert a["listened_ms"] == 310000 and a["plays"] == 3 and a["distinct_tracks"] == 3
    assert a["completion_rate"] == round(2 / 3, 4) and a["skip_rate"] == round(1 / 3, 4)
    assert a["mean_skip_position_ms"] == 30000
    assert [t["title"] for t in a["top_tracks"]] == ["Long", "Short", "Skipped"]
    assert a["top_artists"][0]["listened_ms"] == 310000
    assert a["last_played"]["title"] in ("Long", "Short")       # the skip never qualifies
    w = j["window"]
    assert w["listened_ms"] == 310000 and j["today_listened_ms"] == 310000
    assert w["sessions"] == 2 and w["mean_session_ms"] == 400000
    assert w["by_playback_ms"] == {"unknown": 310000}


def test_local_hours_use_the_event_timezone(client, make_user, make_tracks, sql):
    uid, h = make_user()
    (t,) = make_tracks(uid, [None])
    client.post("/api/events", headers=h, json={"events": [
        {"type": "play_end", "track_id": t, "reason": "completed", "position_ms": 60000,
         "listened_ms": 60000, "tz_offset_min": 330, "playback": "preview"},
    ]})
    # pin the event to 20:00 UTC, which is 01:30 the next day in IST (+330)
    sql.execute("UPDATE track_events SET server_ts = ? WHERE user_id = ?",
                ((datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d 20:00:00"), uid))
    sql.commit()
    w = client.get("/api/me/stats", headers=h).json()["window"]
    assert w["by_local_hour_ms"][1] == 60000
    assert w["by_playback_ms"] == {"preview": 60000}


def test_window_excludes_older_events(client, make_user, make_tracks, sql):
    uid, h = make_user()
    (t,) = make_tracks(uid, [None])
    client.post("/api/events", headers=h, json={"events": [
        {"type": "play_end", "track_id": t, "reason": "completed", "position_ms": 1000, "listened_ms": 1000}]})
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    sql.execute("UPDATE track_events SET server_ts = ? WHERE user_id = ?", (old, uid))
    sql.commit()
    j = client.get("/api/me/stats?days=30", headers=h).json()
    assert j["window"]["listened_ms"] == 0 and j["last_7_days_listened_ms"] == 0
    assert j["all_time"]["listened_ms"] == 1000     # all-time still has it


def test_one_users_stats_never_include_another(client, make_user, make_tracks):
    u1, h1 = make_user()
    u2, h2 = make_user()
    (t,) = make_tracks(u1, [None])
    client.post("/api/events", headers=h1, json={"events": [
        {"type": "play_end", "track_id": t, "reason": "completed", "position_ms": 5000, "listened_ms": 5000}]})
    assert client.get("/api/me/stats", headers=h2).json()["all_time"]["listened_ms"] == 0
