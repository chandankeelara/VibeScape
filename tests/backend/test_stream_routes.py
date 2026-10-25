"""The two audio stream routes, characterised before backend/app.py is split
into routers: full body, Range (206 / 416), the ?token= fallback that
<audio> needs, auth failures, and a row whose file is missing.

PROJECT_ROOT is pointed at the test's tmp dir, because audio paths resolve
against it and anything outside it is refused.
"""
import pytest

import bootstrap  # backend/bootstrap.py: app_config and PROJECT_ROOT live there

AUDIO = bytes(range(256)) * 4          # 1024 bytes


@pytest.fixture
def root(tmp_path, monkeypatch, app_module):
    root = tmp_path / "root"
    monkeypatch.setattr(bootstrap, "PROJECT_ROOT", root)
    (root / "data" / "audio").mkdir(parents=True)
    (root / "data" / "audio" / "a.mp3").write_bytes(AUDIO)
    (root / "data" / "audio" / "b.m4a").write_bytes(AUDIO[:10])
    (tmp_path / "escape.mp3").write_bytes(AUDIO)      # real, but outside the root
    return root


@pytest.fixture
def tracks(sql, root):
    def add(spotify_id, audio_path):
        cur = sql.execute("INSERT INTO tracks (title, artist, spotify_id, audio_path, vibe_score) "
                          "VALUES ('S', 'A', ?, ?, 50)", (spotify_id, audio_path))
        sql.commit()
        return int(cur.lastrowid)
    return {
        "mp3": add("SPMP3", "data/audio/a.mp3"),
        "m4a": add("SPM4A", str(root / "data" / "audio" / "b.m4a")),     # absolute inside root
        "gone": add("SPGONE", "data/audio/missing.mp3"),
        "none": add("SPNONE", None),
        "outside": add("SPOUT", "../escape.mp3"),
    }


ROUTES = ["/api/stream/{}", "/api/stream/spotify/{}"]


@pytest.mark.parametrize("route", ROUTES)
def test_full_body(client, make_user, tracks, route):
    _, h = make_user()
    r = client.get(route.format("SPMP3"), headers=h)
    assert r.status_code == 200
    assert r.content == AUDIO
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["content-length"] == str(len(AUDIO))
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.parametrize("route", ROUTES)
def test_anything_but_mp3_is_served_as_mp4(client, make_user, tracks, route):
    _, h = make_user()
    r = client.get(route.format("SPM4A"), headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mp4"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("header,start,end", [
    ("bytes=10-19", 10, 19),
    ("bytes=1000-", 1000, 1023),
    ("bytes=-5", 1019, 1023),
    ("bytes=0-0", 0, 0),
])
def test_range_is_a_206(client, make_user, tracks, route, header, start, end):
    _, h = make_user()
    r = client.get(route.format("SPMP3"), headers={**h, "Range": header})
    assert r.status_code == 206
    assert r.content == AUDIO[start:end + 1]
    assert r.headers["content-range"] == f"bytes {start}-{end}/1024"
    assert r.headers["content-length"] == str(end - start + 1)


@pytest.mark.parametrize("route", ROUTES)
def test_unsatisfiable_range_is_a_bare_416(client, make_user, tracks, route):
    _, h = make_user()
    for header in ("bytes=1024-", "bytes=5-2000", "bytes=9-3"):
        r = client.get(route.format("SPMP3"), headers={**h, "Range": header})
        assert r.status_code == 416, header
        assert r.headers["content-range"] == "bytes */1024"


@pytest.mark.parametrize("route", ROUTES)
def test_malformed_range_is_a_416_with_a_detail(client, make_user, tracks, route):
    _, h = make_user()
    for header in ("items=0-1", "bytes=-", "bytes=-0"):
        r = client.get(route.format("SPMP3"), headers={**h, "Range": header})
        assert r.status_code == 416, header
        assert r.json() == {"detail": "invalid range"}


@pytest.mark.parametrize("route", ROUTES)
def test_the_query_token_stands_in_for_the_header(client, make_user, tracks, route):
    _, h = make_user()
    token = h["Authorization"].split()[1]
    r = client.get(route.format("SPMP3") + f"?token={token}")
    assert r.status_code == 200 and r.content == AUDIO


@pytest.mark.parametrize("route", ROUTES)
def test_the_header_wins_over_the_query_token(client, make_user, tracks, route):
    _, h = make_user()
    r = client.get(route.format("SPMP3") + "?token=bogus", headers=h)
    assert r.status_code == 200


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("suffix,headers,error", [
    ("", {}, "missing_authorization"),
    ("?token=", {}, "missing_authorization"),
    ("?token=bogus", {}, "invalid_session"),
    ("", {"Authorization": "Bearer bogus"}, "invalid_session"),
])
def test_without_a_valid_session_it_is_a_401(client, tracks, route, suffix, headers, error):
    r = client.get(route.format("SPMP3") + suffix, headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"] == {"error": error}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("key,detail", [
    ("SPGONE", "audio file missing on disk"),
    ("SPOUT", "audio file missing on disk"),        # outside PROJECT_ROOT is refused
    ("SPNONE", "audio not found"),
    ("unknown", "audio not found"),
])
def test_missing_audio_is_a_404(client, make_user, tracks, route, key, detail):
    _, h = make_user()
    r = client.get(route.format(key), headers=h)
    assert r.status_code == 404
    assert r.json() == {"detail": detail}


def test_the_generic_route_does_not_take_a_numeric_track_id(client, make_user, tracks):
    """ODD, pinned: /api/stream/{track_key} looks the key up as a spotify_id
    only, unlike the other {track_key} routes, which also accept tracks.id."""
    _, h = make_user()
    r = client.get(f"/api/stream/{tracks['mp3']}", headers=h)
    assert r.status_code == 404 and r.json() == {"detail": "audio not found"}


@pytest.mark.parametrize("route", ROUTES)
def test_streams_are_not_scoped_to_the_callers_library(client, make_user, tracks, route):
    """ODD, pinned: any session can stream any catalogue track; no
    user_tracks check (the `tracks` fixture links nobody)."""
    _, h = make_user()
    assert client.get(route.format("SPMP3"), headers=h).status_code == 200
