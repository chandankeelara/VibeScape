"""POST /api/spotify/refresh — session required, secret stays server-side."""
from types import SimpleNamespace

import pytest


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


@pytest.fixture
def spotify(monkeypatch, app_module):
    """Configured client id/secret and a recorder in place of requests.post."""
    monkeypatch.setattr(app_module, "app_config",
                        SimpleNamespace(SPOTIFY_CLIENT_ID="cid", SPOTIFY_CLIENT_SECRET="secret"))
    calls = []
    state = {"response": FakeResponse(200, {"access_token": "new", "expires_in": 3600})}

    def fake_post(url, data=None, timeout=None, **_):
        calls.append({"url": url, "data": data})
        return state["response"]
    monkeypatch.setattr(app_module.requests, "post", fake_post)
    return SimpleNamespace(calls=calls, state=state)


def test_requires_a_session(client, spotify):
    r = client.post("/api/spotify/refresh", json={"refresh_token": "rt"})
    assert r.status_code == 401
    assert spotify.calls == []          # never reached Spotify with our secret


def test_refreshes_with_the_server_secret(client, make_user, spotify):
    _, h = make_user()
    r = client.post("/api/spotify/refresh", json={"refresh_token": "rt-1"}, headers=h)
    assert r.status_code == 200
    assert r.json()["access_token"] == "new"
    sent = spotify.calls[0]["data"]
    assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "rt-1"
    assert sent["client_secret"] == "secret"


def test_rotated_refresh_token_is_passed_through(client, make_user, spotify):
    _, h = make_user()
    spotify.state["response"] = FakeResponse(200, {"access_token": "a", "expires_in": 3600,
                                                    "refresh_token": "rt-2"})
    assert client.post("/api/spotify/refresh", json={"refresh_token": "rt-1"},
                       headers=h).json()["refresh_token"] == "rt-2"


def test_spotify_refusal_is_a_400_the_client_can_act_on(client, make_user, spotify):
    _, h = make_user()
    spotify.state["response"] = FakeResponse(400, {"error": "invalid_grant"})
    r = client.post("/api/spotify/refresh", json={"refresh_token": "dead"}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "spotify_refresh_failed"


def test_missing_refresh_token_is_rejected(client, make_user, spotify):
    _, h = make_user()
    r = client.post("/api/spotify/refresh", json={"refresh_token": ""}, headers=h)
    assert r.status_code == 422
    assert spotify.calls == []
