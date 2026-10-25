"""Auth routes, characterised before backend/app.py is split into routers:
email signup / login, logout, /api/auth/me, Spotify OAuth sign-in and
spotify-link. These pin what the code does on main at 535efff, odd parts
included (each noted where it is pinned); they are a safety net for moving
code, not a statement that the behaviour is right.

Spotify is never reached: requests.post (the token exchange) and
requests.request (spotify_guard's /v1/me) are fakes, and any other request
raises.
"""
from types import SimpleNamespace

import pytest

import deps  # backend/deps.py: ADMIN_USER_ID and the session helpers live there
import bootstrap  # backend/bootstrap.py: app_config and PROJECT_ROOT live there
import requests
import db as backend_db  # backend/db.py; `db` is the fixture with the test database path


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(self._payload)
        self.headers = {}
        self.url = "fake"

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(self, method, url, *a, **kw):
        raise AssertionError(f"a test tried to reach {url}")
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


@pytest.fixture
def admin(monkeypatch, app_module, make_user):
    """An admin who owns two tracks (what an email signup is seeded from)."""
    uid, h = make_user("admin")
    monkeypatch.setattr(deps, "ADMIN_USER_ID", uid)
    return uid, h


def signup(client, email="new.person@example.com", password="secret1"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


# ------------------------------------------------------------------ signup

def test_signup_creates_a_user_seeds_the_admin_library_and_signs_in(client, sql, admin, make_tracks):
    admin_id, _ = admin
    seeded = make_tracks(admin_id, [None, None])
    r = signup(client, email="  Chandanke@Example.COM ")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"user_id", "display_name", "email", "session_token", "spotify_connected",
                         "is_premium", "is_admin", "is_guest"}
    assert body["email"] == "chandanke@example.com"           # trimmed and lower-cased
    assert body["display_name"] == "chandan..."              # local part, 7 chars + '...'
    assert (body["spotify_connected"], body["is_premium"], body["is_admin"], body["is_guest"]) == \
        (False, False, False, False)
    row = sql.execute("SELECT password_hash FROM users WHERE id = ?", (body["user_id"],)).fetchone()
    assert row[0].startswith("scrypt$") and "secret1" not in row[0]
    links = sql.execute("SELECT track_id, source FROM user_tracks WHERE user_id = ? ORDER BY track_id",
                        (body["user_id"],)).fetchall()
    assert [tuple(l) for l in links] == [(t, "email_signup_seed") for t in seeded]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['session_token']}"})
    assert me.status_code == 200 and me.json()["user_id"] == body["user_id"]


def test_signup_takes_a_free_display_name_when_the_short_name_is_taken(client, make_user, admin):
    make_user("new.per...")
    assert signup(client).json()["display_name"] == "new.per... 2"


def test_signup_refuses_a_taken_email_with_409(client, admin):
    assert signup(client).status_code == 200
    r = signup(client, email="NEW.person@example.com")
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "email_taken"}


@pytest.mark.parametrize("email,password,error", [
    ("not-an-email", "secret1", "invalid_email"),
    ("", "secret1", "invalid_email"),
    ("a@b.co", "five5", "password_too_short"),
])
def test_signup_validation_is_a_422(client, email, password, error):
    r = signup(client, email=email, password=password)
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": error}


def test_on_an_empty_database_the_first_signup_is_the_admin(client, monkeypatch, app_module):
    """ODD, pinned: is_admin is `user_id == ADMIN_USER_ID` (default 1), so on
    a fresh database whoever signs up first gets id 1 and admin rights."""
    monkeypatch.setattr(deps, "ADMIN_USER_ID", 1)
    body = signup(client).json()
    assert body["user_id"] == 1 and body["is_admin"] is True


# ------------------------------------------------------------------- login

def test_login_issues_a_new_session_and_stamps_last_login(client, sql, admin):
    first = signup(client).json()
    sql.execute("UPDATE users SET last_login_at = NULL WHERE id = ?", (first["user_id"],))
    sql.commit()
    r = client.post("/api/auth/login", json={"email": "New.Person@example.com ", "password": "secret1"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == set(first)
    assert body["user_id"] == first["user_id"] and body["session_token"] != first["session_token"]
    assert sql.execute("SELECT last_login_at FROM users WHERE id = ?",
                       (body["user_id"],)).fetchone()[0] is not None
    assert sql.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?",
                       (body["user_id"],)).fetchone()[0] == 2


@pytest.mark.parametrize("email,password", [
    ("new.person@example.com", "wrong-password"),
    ("nobody@example.com", "secret1"),
])
def test_bad_credentials_are_one_401(client, admin, email, password):
    signup(client)
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401
    assert r.json()["detail"] == {"error": "bad_credentials"}


def test_a_spotify_only_account_cannot_password_login(client, sql):
    sql.execute("INSERT INTO users (display_name, email, spotify_user_id) VALUES ('sp', 'sp@x.io', 'spid')")
    sql.commit()
    r = client.post("/api/auth/login", json={"email": "sp@x.io", "password": "anything"})
    assert r.status_code == 401


@pytest.mark.parametrize("email,password,error", [
    ("  ", "secret1", "invalid_email"),
    ("a@b.co", "", "password_required"),
])
def test_login_validation_is_a_422(client, email, password, error):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": error}


# ------------------------------------------------------------ logout and me

def test_logout_ends_only_that_session(client, sql, make_user, app_module):
    uid, h = make_user()
    conn = backend_db.get_conn()
    other = deps._issue_session(conn, uid)
    conn.close()
    r = client.post("/api/auth/logout", headers=h)
    assert r.status_code == 204 and r.content == b""
    assert client.get("/api/auth/me", headers=h).json()["detail"] == {"error": "invalid_session"}
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code == 200


@pytest.mark.parametrize("headers,error", [
    ({}, "missing_authorization"),
    ({"Authorization": "Token abc"}, "missing_authorization"),
    ({"Authorization": "Bearer nope"}, "invalid_session"),
])
def test_me_and_logout_need_a_valid_bearer(client, headers, error):
    for call in (lambda: client.get("/api/auth/me", headers=headers),
                 lambda: client.post("/api/auth/logout", headers=headers)):
        r = call()
        assert r.status_code == 401
        assert r.json()["detail"] == {"error": error}


def test_me_reports_the_spotify_profile(client, sql, make_user, monkeypatch, app_module):
    uid, h = make_user("listener")
    sql.execute("UPDATE users SET spotify_user_id='sp1', spotify_display_name='Lis', spotify_email='l@x.io', "
                "spotify_country='IN', spotify_product='premium', spotify_avatar_url='https://a', "
                "spotify_profile_url='https://p' WHERE id = ?", (uid,))
    sql.commit()
    monkeypatch.setattr(deps, "ADMIN_USER_ID", uid)
    body = client.get("/api/auth/me", headers=h).json()
    created = body.pop("created_at")
    assert created
    assert body == {
        "user_id": uid, "display_name": "listener", "spotify_connected": True,
        "spotify_display_name": "Lis", "spotify_email": "l@x.io", "spotify_country": "IN",
        "spotify_product": "premium", "is_premium": True, "avatar_url": "https://a",
        "profile_url": "https://p", "last_login_at": None, "is_admin": True, "is_guest": False,
    }


def test_me_calls_any_user_named_guest_a_guest(client, make_user):
    """ODD, pinned: is_guest is display_name == 'Guest', not a flag."""
    _, h = make_user("Guest")
    assert client.get("/api/auth/me", headers=h).json()["is_guest"] is True


# ------------------------------------------------------------ Spotify OAuth

@pytest.fixture
def spotify(monkeypatch, app_module):
    """Configured app credentials; .token is the token-exchange answer,
    .me the /v1/me answer, .calls every request made."""
    monkeypatch.setattr(bootstrap, "app_config", SimpleNamespace(
        SPOTIFY_CLIENT_ID="cid", SPOTIFY_CLIENT_SECRET="secret", SPOTIFY_REDIRECT_URI="https://cfg/callback"))
    state = SimpleNamespace(calls=[],
                            token=FakeResponse(200, {"access_token": "AT", "refresh_token": "RT",
                                                     "expires_in": 3600, "scope": "user-read-email"}),
                            me=FakeResponse(200, {"id": "sp-123", "display_name": "Chandan",
                                                  "email": "chandan@x.io", "country": "IN",
                                                  "product": "premium",
                                                  "external_urls": {"spotify": "https://open/u"},
                                                  "images": [{"url": "https://img/a"}]}))

    def fake_post(url, data=None, timeout=None, **_):
        state.calls.append(("POST", url, data))
        return state.token

    def fake_request(method, url, **kw):
        state.calls.append((method, url, kw.get("headers")))
        return state.me
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "request", fake_request)
    return state


def oauth(client, **body):
    return client.post("/api/auth/spotify-oauth", json={"code": "C0DE", **body})


def test_oauth_signs_up_a_new_spotify_user(client, sql, spotify):
    r = oauth(client, redirect_uri="https://app/callback")
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "Chandan"
    assert {k: body[k] for k in ("spotify_connected", "spotify_display_name", "spotify_email",
                                 "spotify_country", "spotify_product", "is_premium", "avatar_url",
                                 "profile_url", "spotify_access_token", "spotify_refresh_token",
                                 "spotify_expires_in", "spotify_scope")} == {
        "spotify_connected": True, "spotify_display_name": "Chandan", "spotify_email": "chandan@x.io",
        "spotify_country": "IN", "spotify_product": "premium", "is_premium": True,
        "avatar_url": "https://img/a", "profile_url": "https://open/u",
        "spotify_access_token": "AT", "spotify_refresh_token": "RT", "spotify_expires_in": 3600,
        "spotify_scope": "user-read-email"}
    assert "is_guest" not in body                              # unlike login and signup
    exchange = spotify.calls[0]
    assert exchange[1] == "https://accounts.spotify.com/api/token"
    assert exchange[2] == {"grant_type": "authorization_code", "code": "C0DE",
                           "redirect_uri": "https://app/callback", "client_id": "cid",
                           "client_secret": "secret"}
    assert spotify.calls[1][:2] == ("GET", "https://api.spotify.com/v1/me")
    assert spotify.calls[1][2] == {"Authorization": "Bearer AT"}
    row = sql.execute("SELECT spotify_user_id, last_login_at FROM users WHERE id = ?",
                      (body["user_id"],)).fetchone()
    assert row[0] == "sp-123" and row[1] is not None


def test_oauth_falls_back_to_the_configured_redirect_uri(client, spotify):
    assert oauth(client).status_code == 200
    assert spotify.calls[0][2]["redirect_uri"] == "https://cfg/callback"


def test_oauth_signs_an_existing_spotify_user_back_in_and_refreshes_the_profile(client, sql, spotify):
    first = oauth(client).json()
    spotify.me = FakeResponse(200, {"id": "sp-123", "display_name": "Renamed", "product": "free"})
    again = oauth(client).json()
    assert again["user_id"] == first["user_id"]
    assert again["display_name"] == "Chandan"                  # the VibeScape name stays
    assert again["spotify_display_name"] == "Renamed" and again["is_premium"] is False
    assert sql.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_oauth_name_collision_falls_back_to_the_email_prefix(client, make_user, spotify):
    make_user("Chandan")
    assert oauth(client).json()["display_name"] == "chandan"


@pytest.mark.parametrize("which,status,error", [
    ("token", 400, "spotify_code_invalid"),
    ("me", 400, "spotify_me_failed"),
])
def test_oauth_spotify_refusals_are_400s(client, spotify, which, status, error):
    setattr(spotify, which, FakeResponse(401, {"error": "nope"}))
    r = oauth(client)
    assert r.status_code == status
    assert r.json()["detail"] == {"error": error}


def test_oauth_without_an_access_token_or_id_is_a_400(client, spotify):
    spotify.token = FakeResponse(200, {"refresh_token": "RT"})
    assert oauth(client).json()["detail"] == {"error": "spotify_no_access_token"}
    spotify.token = FakeResponse(200, {"access_token": "AT"})
    spotify.me = FakeResponse(200, {"display_name": "x"})
    assert oauth(client).json()["detail"] == {"error": "spotify_no_id"}


def test_oauth_without_server_credentials_is_a_500(client, spotify, monkeypatch, app_module):
    monkeypatch.setattr(bootstrap, "app_config", SimpleNamespace(SPOTIFY_REDIRECT_URI="https://cfg"))
    r = oauth(client)
    assert r.status_code == 500
    assert r.json()["detail"] == {"error": "spotify_not_configured"}
    assert spotify.calls == []


def test_oauth_with_no_redirect_uri_anywhere_is_a_422(client, spotify, monkeypatch, app_module):
    monkeypatch.setattr(bootstrap, "app_config",
                        SimpleNamespace(SPOTIFY_CLIENT_ID="cid", SPOTIFY_CLIENT_SECRET="secret"))
    r = oauth(client)
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": "redirect_uri required"}


def test_oauth_with_an_empty_code_is_a_422(client, spotify):
    r = client.post("/api/auth/spotify-oauth", json={"code": ""})
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": "code required"}


# ------------------------------------------------------------- spotify-link

def link(client, h, sp_id="sp-9", name="Nine"):
    return client.post("/api/auth/spotify-link", json={"spotify_user_id": sp_id,
                                                        "spotify_display_name": name}, headers=h)


def test_link_stamps_the_spotify_identity_on_the_caller(client, sql, make_user):
    uid, h = make_user()
    r = link(client, h)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "spotify_user_id": "sp-9", "spotify_display_name": "Nine"}
    assert tuple(sql.execute("SELECT spotify_user_id, spotify_display_name FROM users WHERE id = ?",
                             (uid,)).fetchone()) == ("sp-9", "Nine")
    assert link(client, h).json()["ok"] is True                 # relinking your own id is fine


def test_link_to_an_identity_another_user_holds_is_a_200_refusal(client, sql, make_user):
    """ODD, pinned: refusals are HTTP 200 with ok: false, not 4xx (the
    caller treats the call as fire-and-forget)."""
    other, oh = make_user()
    link(client, oh)
    uid, h = make_user()
    r = link(client, h)
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "spotify_account_already_linked", "spotify_user_id": "sp-9"}
    assert sql.execute("SELECT spotify_user_id FROM users WHERE id = ?", (uid,)).fetchone()[0] is None


def test_the_shared_guest_cannot_link(client, sql):
    guest = client.post("/api/auth/guest").json()
    r = link(client, {"Authorization": f"Bearer {guest['session_token']}"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "guest_cannot_link", "spotify_user_id": "sp-9"}
    assert sql.execute("SELECT spotify_user_id FROM users WHERE display_name = 'Guest'").fetchone()[0] is None


def test_link_needs_a_session_and_an_id(client, make_user):
    assert link(client, {}).status_code == 401
    _, h = make_user()
    r = link(client, h, sp_id="")
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": "spotify_user_id required"}
