"""POST /api/events — validation, routing, and the user_track_stats rollup."""
import json

import pytest

ENV = {"session_id": "s-1", "tz_offset_min": 330, "vibe": 60, "vibe_source": "user", "dj_mode": True}


@pytest.fixture
def user(make_user, make_tracks):
    uid, h = make_user()
    tracks = make_tracks(uid, [None] * 6)
    return uid, h, tracks


def send(client, h, events):
    r = client.post("/api/events", json={"events": events}, headers=h)
    assert r.status_code == 202, r.text
    return r.json()


def stats(sql, uid, tid):
    return sql.execute("SELECT * FROM user_track_stats WHERE user_id=? AND track_id=?",
                       (uid, tid)).fetchone()


# ------------------------------------------------------------------ contract

def test_requires_a_session(client):
    assert client.post("/api/events", json={"events": []}).status_code == 401


@pytest.mark.parametrize("body", [None, "nope", {"events": "x"}, {"no": "events"}])
def test_garbage_bodies_are_still_202(client, user, body):
    _, h, _ = user
    r = client.post("/api/events", content=json.dumps(body), headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 202


def test_counts_add_up(client, user):
    _, h, (t, *_) = user
    res = send(client, h, [
        {**ENV, "type": "play_start", "track_id": t},
        {**ENV, "type": "teleport", "track_id": t},              # unknown type
        {**ENV, "type": "play_end"},                              # no track
        {**ENV, "type": "play_end", "track_id": 99999999},        # unknown track
        {**ENV, "type": "search", "data": {"query": "x"}},        # app event, no track needed
    ])
    assert res == {"accepted": 2, "rejected": 3}


def test_batch_over_the_cap_rejects_the_overflow_not_the_request(client, user, app_module):
    _, h, (t, *_) = user
    n = app_module._EVENT_BATCH_CAP + 5
    res = send(client, h, [{**ENV, "type": "pause", "track_id": t}] * n)
    assert res == {"accepted": app_module._EVENT_BATCH_CAP, "rejected": 5}


# ------------------------------------------------------------------- routing

def test_track_events_store_the_v2_envelope(client, user, sql):
    uid, h, (t, *_) = user
    send(client, h, [
        {**ENV, "type": "play_start", "track_id": t, "source": "pick", "playback": "spotify"},
        {**ENV, "type": "seek", "track_id": t, "data": {"from_ms": 1000, "to_ms": 90000}},
        {**ENV, "type": "play_end", "track_id": t, "reason": "skipped", "trigger": "next_button",
         "position_ms": 95000, "duration_ms": 200000, "listened_ms": 20000, "playback": "spotify"},
    ])
    rows = sql.execute("SELECT * FROM track_events WHERE user_id=? ORDER BY id", (uid,)).fetchall()
    assert [r["type"] for r in rows] == ["play_start", "seek", "play_end"]
    assert all(r["session_id"] == "s-1" and r["tz_offset_min"] == 330 for r in rows)
    assert rows[0]["source"] == "pick" and rows[0]["playback"] == "spotify"
    assert json.loads(rows[1]["data"]) == {"from_ms": 1000, "to_ms": 90000}
    end = rows[2]
    assert (end["end_trigger"], end["listened_ms"], end["reason"]) == ("next_button", 20000, "skipped")


def test_trigger_and_listened_only_kept_on_play_end(client, user, sql):
    uid, h, (t, *_) = user
    send(client, h, [{**ENV, "type": "play_start", "track_id": t, "trigger": "x", "listened_ms": 5}])
    r = sql.execute("SELECT end_trigger, listened_ms FROM track_events WHERE user_id=?", (uid,)).fetchone()
    assert tuple(r) == (None, None)


def test_app_events_go_to_user_events(client, user, sql):
    uid, h, _ = user
    send(client, h, [
        {**ENV, "type": "session_start", "data": {"platform": "pwa"}},
        {**ENV, "type": "search", "data": {"query": "kivi mathu", "results_library": 3, "outcome": "played"}},
        {**ENV, "type": "vibe_change", "data": {"from": 40, "to": 80}},
        {**ENV, "type": "dj_toggle", "data": {"on": True}},
        {**ENV, "type": "session_end", "data": {"duration_ms": 60000}},
    ])
    rows = sql.execute("SELECT type, data, session_id, vibe_source FROM user_events WHERE user_id=? ORDER BY id",
                       (uid,)).fetchall()
    assert [r["type"] for r in rows] == ["session_start", "search", "vibe_change", "dj_toggle", "session_end"]
    assert json.loads(rows[1]["data"])["query"] == "kivi mathu"
    assert all(r["session_id"] == "s-1" and r["vibe_source"] == "user" for r in rows)
    assert sql.execute("SELECT COUNT(*) FROM track_events WHERE user_id=?", (uid,)).fetchone()[0] == 0


@pytest.mark.parametrize("data", [
    {"nested": {"a": 1}}, {"list": [1, 2]}, "a string", {"x" * 40: 1},
    {f"k{i}": i for i in range(20)}, {"q": "y" * 5000, "a": "y" * 300, "b": "y" * 300, "c": "y" * 300,
                                      "d": "y" * 300, "e": "y" * 300, "f": "y" * 300, "g": "y" * 300},
])
def test_bad_data_is_dropped_but_the_event_kept(client, user, sql, data):
    uid, h, _ = user
    assert send(client, h, [{**ENV, "type": "search", "data": data}])["accepted"] == 1
    assert sql.execute("SELECT data FROM user_events WHERE user_id=?", (uid,)).fetchone()[0] is None


def test_long_strings_in_data_are_capped(client, user, sql):
    uid, h, _ = user
    send(client, h, [{**ENV, "type": "search", "data": {"query": "z" * 1000}}])
    stored = json.loads(sql.execute("SELECT data FROM user_events WHERE user_id=?", (uid,)).fetchone()[0])
    assert len(stored["query"]) == 300


def test_unknown_vibe_source_is_stored_as_null_not_guessed(client, user, sql):
    uid, h, (t, *_) = user
    send(client, h, [{**ENV, "type": "play_start", "track_id": t, "vibe_source": "robot"}])
    assert sql.execute("SELECT vibe_source FROM track_events WHERE user_id=?", (uid,)).fetchone()[0] is None


# -------------------------------------------------------------------- rollup

def test_only_plays_feed_the_aggregate(client, user, sql):
    uid, h, (t, queued_only, *_) = user
    send(client, h, [
        {**ENV, "type": "play_start", "track_id": t},
        {**ENV, "type": "pause", "track_id": t, "position_ms": 1000},
        {**ENV, "type": "resume", "track_id": t, "position_ms": 1000},
        {**ENV, "type": "seek", "track_id": t, "data": {"from_ms": 1, "to_ms": 2}},
        {**ENV, "type": "queue_add", "track_id": queued_only},
    ])
    s = stats(sql, uid, t)
    assert (s["play_count"], s["end_count"]) == (1, 0)      # a pause is not an ended play
    assert stats(sql, uid, queued_only) is None            # a queue-add is not a play


def test_listened_time_and_its_legacy_fallback(client, user, sql):
    uid, h, (t, *_) = user
    send(client, h, [
        {**ENV, "type": "play_end", "track_id": t, "reason": "skipped", "position_ms": 150000, "listened_ms": 30000},
        {**ENV, "type": "play_end", "track_id": t, "reason": "completed", "position_ms": 120000},   # pre-v2 client
    ])
    s = stats(sql, uid, t)
    assert s["total_played_ms"] == 270000
    assert s["total_listened_ms"] == 30000 + 120000


def test_seeking_ahead_is_not_a_listen(client, user, sql):
    uid, h, (t, *_) = user
    send(client, h, [{**ENV, "type": "play_end", "track_id": t, "reason": "skipped",
                      "position_ms": 150000, "listened_ms": 30000}])
    s = stats(sql, uid, t)
    assert s["last_played"] is None and s["last_skipped_at"] is not None


@pytest.mark.parametrize("listened,qualifies", [(89_999, False), (90_000, True)])
def test_ninety_seconds_heard_qualifies(client, user, sql, listened, qualifies):
    uid, h, (t, *_) = user
    send(client, h, [{**ENV, "type": "play_end", "track_id": t, "reason": "skipped",
                      "position_ms": 1000, "listened_ms": listened}])
    assert (stats(sql, uid, t)["last_played"] is not None) is qualifies


def test_any_skip_counts_as_a_skip_whatever_the_trigger(client, user, sql):
    uid, h, tracks = user
    for t, trig in zip(tracks, ["next_button", "media_key", "search", "pick", "queue_jump"]):
        send(client, h, [{**ENV, "type": "play_end", "track_id": t, "reason": "skipped",
                          "trigger": trig, "position_ms": 5000, "listened_ms": 5000}])
        s = stats(sql, uid, t)
        assert s["u_skip_count"] == 1 and s["last_skipped_at"] is not None, trig


def test_abandoned_counts_time_but_no_bucket(client, user, sql):
    uid, h, (t, *_) = user
    send(client, h, [{**ENV, "type": "play_end", "track_id": t, "reason": "abandoned",
                      "position_ms": 40000, "listened_ms": 40000}])
    s = stats(sql, uid, t)
    assert s["end_count"] == 1 and s["total_listened_ms"] == 40000
    assert (s["u_complete_count"], s["u_skip_count"], s["u_replace_count"]) == (0, 0, 0)


def test_preference_and_reward_stay_apart(client, user, sql):
    uid, h, (t, *_) = user
    send(client, h, [
        {**ENV, "vibe_source": "user", "type": "play_end", "track_id": t, "reason": "completed", "position_ms": 1},
        {**ENV, "vibe_source": "system", "type": "play_end", "track_id": t, "reason": "skipped", "position_ms": 1},
        {**ENV, "vibe_source": None, "type": "play_end", "track_id": t, "reason": "skipped", "position_ms": 1},
    ])
    s = stats(sql, uid, t)
    assert (s["u_complete_count"], s["s_skip_count"], s["u_skip_count"]) == (1, 1, 0)
    assert s["end_count"] == 3          # the unlabelled one is in the totals only


def test_events_resolve_by_spotify_id(client, user, sql):
    uid, h, (t, *_) = user
    sid = sql.execute("SELECT spotify_id FROM tracks WHERE id=?", (t,)).fetchone()[0]
    send(client, h, [{**ENV, "type": "play_start", "spotify_id": sid}])
    assert stats(sql, uid, t)["play_count"] == 1
