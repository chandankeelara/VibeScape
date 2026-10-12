"""ingest/spotify_guard.py — one 429 anywhere, no more Spotify calls until it ends.

The network is replaced by a stub; a test fails if a blocked call reaches it.
"""
import pytest
import requests

import spotify_guard as G


class Resp:
    def __init__(self, status=200, payload=None, retry_after=None, url="https://api.spotify.com/v1/x"):
        self.status_code = status
        self._payload = payload or {}
        self.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
        self.url = url
        self.text = ""

    def json(self):
        return self._payload


@pytest.fixture
def net(monkeypatch):
    """requests.request stub: `.answers` are returned in order, `.calls` counted."""
    class Net:
        answers = []
        calls = []
    n = Net()
    n.answers, n.calls = [], []

    def fake(method, url, **kw):
        n.calls.append((method, url))
        if not n.answers:
            raise AssertionError(f"unexpected Spotify call while it should be held: {url}")
        return n.answers.pop(0)
    monkeypatch.setattr(requests, "request", fake)
    return n


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(G, "_clock", lambda: t["now"])
    return t


def test_calls_go_out_when_nothing_is_blocked(net, clock):
    net.answers = [Resp(200, {"ok": 1})]
    assert G.get("https://api.spotify.com/v1/search").json() == {"ok": 1}
    assert len(net.calls) == 1 and G.status() == {"blocked": False, "retry_after_s": 0}


def test_one_429_holds_every_later_call_without_the_network(net, clock):
    net.answers = [Resp(429, retry_after=70320)]
    first = G.get("https://api.spotify.com/v1/search")
    assert first.status_code == 429 and len(net.calls) == 1
    for url in ("https://api.spotify.com/v1/search", "https://api.spotify.com/v1/me",
                "https://api.spotify.com/v1/me/player"):
        r = G.request("PUT" if url.endswith("player") else "GET", url)
        assert r.status_code == 429 and int(r.headers["Retry-After"]) == 70320
    assert len(net.calls) == 1                                  # nothing else went out
    assert G.status() == {"blocked": True, "retry_after_s": 70320}


def test_the_hold_counts_down_and_then_lifts(net, clock):
    net.answers = [Resp(429, retry_after=100), Resp(200)]
    G.get("https://api.spotify.com/v1/search")
    clock["now"] += 60
    assert int(G.get("https://api.spotify.com/v1/search").headers["Retry-After"]) == 40
    clock["now"] += 41
    assert G.get("https://api.spotify.com/v1/search").status_code == 200
    assert len(net.calls) == 2


def test_a_shorter_429_never_shortens_a_longer_block(clock):
    G.note(Resp(429, retry_after=1000))
    G.note(Resp(429, retry_after=5))
    assert G.blocked_for() == 1000


def test_a_missing_retry_after_still_holds_briefly(clock):
    G.note(Resp(429))
    assert 0 < G.blocked_for() <= 30


def test_should_retry_only_short_waits():
    assert G.should_retry(Resp(429, retry_after=2))
    assert not G.should_retry(Resp(429, retry_after=70320))
    assert not G.should_retry(Resp(200))


def test_the_held_answer_is_not_recorded_again(clock):
    G.note(Resp(429, retry_after=50))
    held = G.get("https://api.spotify.com/v1/search")
    clock["now"] += 49
    G.note(held)                                  # must not re-extend from "now"
    assert G.blocked_for() == pytest.approx(1)


# ------------------------------------------------------- the callers honour it

def test_library_reads_fail_fast_instead_of_sleeping(net, clock, monkeypatch):
    import time
    import spotify_library as L
    monkeypatch.setattr(time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("slept")))
    G.note(Resp(429, retry_after=70320))
    with pytest.raises(L.SpotifyAPIError):
        L._get(f"{L.BASE}/me", "token")
    assert net.calls == []


def test_the_offline_matcher_gives_up_instead_of_sleeping_19_hours(net, clock, monkeypatch):
    import time
    import spotify_matcher as M
    monkeypatch.setattr(time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError(f"slept {s}")))
    G.note(Resp(429, retry_after=70320))
    assert M.match_by_title_artist("Song", "Artist", "token") is None
    assert net.calls == []


def test_playlist_import_stops_without_calling(net, clock):
    import playlist_import as P
    G.note(Resp(429, retry_after=70320))
    with pytest.raises(P.SpotifyRateLimited):
        P.resolve(P.Item(title="T", artist="A"), "token", G.get, pacer=P._Pacer(0))
    assert net.calls == []


def test_status_endpoint_reports_without_calling_spotify(client, clock):
    assert client.get("/api/spotify/status").json() == {"blocked": False, "retry_after_s": 0}
    G.note(Resp(429, retry_after=3600))
    assert client.get("/api/spotify/status").json() == {"blocked": True, "retry_after_s": 3600}
