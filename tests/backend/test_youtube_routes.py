"""GET /api/tracks/{id}/youtube and /youtube/search, characterised before
backend/app.py is split into routers. (The admin-only POST is covered by
test_track_youtube_auth.py.) yt-dlp is never run: the search goes through
_yt_manual_search_sync, which every test replaces.
"""
import time

import pytest


@pytest.fixture
def track(sql):
    cur = sql.execute("INSERT INTO tracks (title, artist, spotify_id, vibe_score) VALUES ('S', 'A', 'SPY', 50)")
    sql.commit()
    return int(cur.lastrowid)


@pytest.fixture
def search(monkeypatch, app_module):
    calls = []
    state = {"results": [{"youtube_id": "abcdefghijk", "title": "T", "channel": "C",
                          "duration": 200, "thumbnail_url": "https://i"}]}

    def fake(query, limit):
        calls.append((query, limit))
        if isinstance(state["results"], Exception):
            raise state["results"]
        return state["results"]
    monkeypatch.setattr(app_module, "_yt_manual_search_sync", fake)
    return calls, state


# ------------------------------------------------------------------- GET

def test_get_returns_the_cached_id_or_null(client, sql, make_user, track):
    _, h = make_user()
    assert client.get(f"/api/tracks/{track}/youtube", headers=h).json() == {"youtube_id": None, "cached": True}
    sql.execute("UPDATE tracks SET youtube_id = 'dQw4w9WgXcQ' WHERE id = ?", (track,))
    sql.commit()
    assert client.get(f"/api/tracks/{track}/youtube", headers=h).json() == \
        {"youtube_id": "dQw4w9WgXcQ", "cached": True}


def test_get_is_by_numeric_id_only(client, make_user, track):
    _, h = make_user()
    r = client.get("/api/tracks/999999/youtube", headers=h)
    assert r.status_code == 404 and r.json()["detail"] == {"error": "track_not_found"}
    assert client.get("/api/tracks/SPY/youtube", headers=h).status_code == 422
    assert client.get(f"/api/tracks/{track}/youtube").status_code == 401


# ---------------------------------------------------------------- search

def test_search_passes_the_trimmed_query_and_a_clamped_limit(client, make_user, track, search):
    calls, state = search
    _, h = make_user()                                       # any user, not admin-only
    r = client.get(f"/api/tracks/{track}/youtube/search", params={"q": "  song name  "}, headers=h)
    assert r.status_code == 200
    assert r.json() == {"results": state["results"]}
    for limit, sent in ((3, 3), (0, 1), (-4, 1), (50, 10)):
        client.get(f"/api/tracks/{track}/youtube/search", params={"q": "x", "limit": limit}, headers=h)
    assert calls == [("song name", 5), ("x", 3), ("x", 1), ("x", 1), ("x", 10)]


@pytest.mark.parametrize("q,error", [("   ", "empty_query"), ("x" * 301, "query_too_long")])
def test_search_query_validation_is_a_400(client, make_user, track, search, q, error):
    _, h = make_user()
    r = client.get(f"/api/tracks/{track}/youtube/search", params={"q": q}, headers=h)
    assert r.status_code == 400 and r.json()["detail"] == {"error": error}
    assert search[0] == []


def test_search_checks_the_track_before_the_query(client, make_user, search):
    _, h = make_user()
    r = client.get("/api/tracks/999999/youtube/search", params={"q": "   "}, headers=h)
    assert r.status_code == 404 and r.json()["detail"] == {"error": "track_not_found"}


def test_search_without_q_is_a_422_and_without_a_session_a_401(client, make_user, track, search):
    _, h = make_user()
    assert client.get(f"/api/tracks/{track}/youtube/search", headers=h).status_code == 422
    assert client.get(f"/api/tracks/{track}/youtube/search", params={"q": "x"}).status_code == 401


def test_a_failing_search_is_an_empty_200(client, make_user, track, search):
    _, state = search
    state["results"] = RuntimeError("yt-dlp broke")
    _, h = make_user()
    r = client.get(f"/api/tracks/{track}/youtube/search", params={"q": "x"}, headers=h)
    assert r.status_code == 200 and r.json() == {"results": []}


def test_a_slow_search_times_out_to_an_empty_200(client, make_user, track, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "_YT_SEARCH_TIMEOUT_S", 0.05)
    monkeypatch.setattr(app_module, "_yt_manual_search_sync", lambda q, n: time.sleep(0.5) or ["late"])
    _, h = make_user()
    r = client.get(f"/api/tracks/{track}/youtube/search", params={"q": "x"}, headers=h)
    assert r.status_code == 200 and r.json() == {"results": []}
