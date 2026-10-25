"""Spotify library ingest, characterised before backend/app.py is split into
routers: GET /api/spotify/library, POST /api/ingest/spotify and its worker
_run_ingest_job, POST /api/ingest/spotify-public (every preflight branch and
its job), and DELETE /api/ingest/status/{id}. Pinned as on main at 535efff,
odd parts included and noted.

Spotify is never reached: the spotify_library (splib) functions the app
calls are replaced, spotify_guard's requests.request is a fake where a route
calls it directly, and any other request raises. The job threads,
_process_track and the database are real.
"""
import threading
import time
from types import SimpleNamespace

import pytest
import requests

import spotify_library as splib

PID = "37i9dQZF1DXcBWIGoYBM5M"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(self, method, url, *a, **kw):
        raise AssertionError(f"a test tried to reach {url}")
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


def sp(cid, name="Song", artist="Artist", isrc=None):
    return {"id": cid, "name": name, "artists": [{"name": artist}], "duration_ms": 200_000,
            "album": {"name": "Alb", "images": [{"url": "https://img"}]},
            "external_ids": {"isrc": isrc} if isrc else {}}


def wait(client, headers, job_id, until=("complete", "error", "cancelled"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/ingest/status/{job_id}", headers=headers).json()
        if s["status"] in until if isinstance(until, tuple) else until(s):
            return s
        time.sleep(0.02)
    raise AssertionError(f"job did not get there: {s}")


def links(sql, user_id):
    return {r[0]: r[1] for r in sql.execute(
        "SELECT t.spotify_id, ut.source FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
        "WHERE ut.user_id = ?", (user_id,))}


# ----------------------------------------------------------- /api/spotify/library

@pytest.fixture
def library(monkeypatch):
    state = SimpleNamespace(
        tokens=[], resolved=[], me={"id": "me"}, error=None,
        playlists=[
            {"id": "p1", "name": "Mine", "owner": {"id": "me", "display_name": "Me"}, "tracks": {"total": 12}},
            {"id": "p2", "name": "Mine, no count", "owner": {"id": "me"}, "tracks": {"total": ""}},
            {"id": "p3", "name": "Theirs", "owner": {"id": "other", "display_name": "Other"},
             "tracks": {"total": None}},
            None,
        ])

    def counted(n):
        def f(token):
            state.tokens.append(token)
            if state.error:
                raise state.error
            return n
        return f

    def fake_get(url, token, params=None):
        if url.endswith("/me"):
            if isinstance(state.me, Exception):
                raise state.me
            return state.me
        state.resolved.append((url, params))
        return {"total": 7}
    monkeypatch.setattr(splib, "get_liked_count", counted(321))
    monkeypatch.setattr(splib, "get_top_tracks_count", counted(898))
    monkeypatch.setattr(splib, "get_playlists", lambda token: state.playlists)
    monkeypatch.setattr(splib, "_get", fake_get)
    return state


def test_library_manifest(client, make_user, library):
    _, h = make_user()
    r = client.get("/api/spotify/library", headers={**h, "X-Spotify-Authorization": "Bearer SPOT"})
    assert r.status_code == 200
    assert r.json() == {"liked_count": 321, "top_tracks_count": 898, "playlists": [
        {"id": "p1", "name": "Mine", "track_count": 12, "owner": "Me", "owned_by_me": True},
        {"id": "p2", "name": "Mine, no count", "track_count": 7, "owner": "me", "owned_by_me": True},
        {"id": "p3", "name": "Theirs", "track_count": 0, "owner": "Other", "owned_by_me": False},
    ]}
    assert library.tokens == ["SPOT", "SPOT"]
    # only the caller's own empty-count playlist is resolved
    assert library.resolved == [(f"{splib.BASE}/playlists/p2/items", {"limit": 1, "fields": "total"})]


def test_library_resolves_every_empty_count_when_me_fails(client, make_user, library):
    library.me = splib.SpotifyAPIError("me down")
    _, h = make_user()
    body = client.get("/api/spotify/library", headers={**h, "X-Spotify-Authorization": "Bearer SPOT"}).json()
    assert [p["owned_by_me"] for p in body["playlists"]] == [False, False, False]
    assert [u for u, _ in library.resolved] == [f"{splib.BASE}/playlists/p2/items",
                                                 f"{splib.BASE}/playlists/p3/items"]


def test_library_without_the_spotify_header_sends_the_session_token_to_spotify(client, make_user, library):
    """ODD, pinned: the legacy fallback reads Authorization, which under
    multi-user auth is the VibeScape session token, so that is what gets
    sent to Spotify as the access token."""
    _, h = make_user()
    assert client.get("/api/spotify/library", headers=h).status_code == 200
    assert library.tokens[0] == h["Authorization"].split()[1]


def test_library_expired_token_is_a_401_without_detail(client, make_user, library):
    library.error = splib.SpotifyAuthError("401")
    _, h = make_user()
    r = client.get("/api/spotify/library", headers={**h, "X-Spotify-Authorization": "Bearer SPOT"})
    assert r.status_code == 401
    assert r.json() == {"error": "spotify_token_expired"}          # top level, not under detail


def test_library_api_error_is_a_500_with_the_message(client, make_user, library):
    library.error = splib.SpotifyAPIError("spotify 503")
    _, h = make_user()
    r = client.get("/api/spotify/library", headers={**h, "X-Spotify-Authorization": "Bearer SPOT"})
    assert r.status_code == 500 and r.json() == {"detail": "spotify 503"}


def test_library_needs_a_session(client, library):
    assert client.get("/api/spotify/library", headers={"X-Spotify-Authorization": "Bearer SPOT"}).status_code == 401


# ------------------------------------------------------------ /api/ingest/spotify

@pytest.fixture
def spotify_lib(monkeypatch):
    """What the library job reads: .liked / .top are lists or an exception,
    .playlists maps id -> list, .me is the /v1/me answer or an exception."""
    state = SimpleNamespace(liked=[], top=[], playlists={}, me={"id": "me"}, gate=None)

    def stream(name):
        def f(token):
            items = getattr(state, name)
            if isinstance(items, Exception):
                raise items
            for i, t in enumerate(items):
                if isinstance(t, Exception):
                    raise t
                if state.gate and i == state.gate[0]:
                    state.gate[1].wait(10)
                yield t
        return f

    def fake_get(url, token, params=None):
        if isinstance(state.me, Exception):
            raise state.me
        return state.me
    monkeypatch.setattr(splib, "iter_liked", stream("liked"))
    monkeypatch.setattr(splib, "iter_top_tracks", stream("top"))
    monkeypatch.setattr(splib, "fetch_playlist_tracks", lambda pid, token: state.playlists[pid])
    monkeypatch.setattr(splib, "_get", fake_get)
    return state


def start_sync(client, h, **sources):
    return client.post("/api/ingest/spotify", json={"access_token": "SPOT", "sources": sources}, headers=h)


def test_sync_job_runs_to_complete_with_its_buckets(client, sql, make_user, spotify_lib):
    uid, h = make_user()
    sql.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, vibe_score) "
                "VALUES ('Done', 'A', 'L2', 'done', 50)")
    sql.commit()
    spotify_lib.liked = [sp("L1"), sp("L2"), {"name": "no id"}]
    spotify_lib.top = [sp("L1"), sp("T1")]                     # L1 again: deduped across sources
    spotify_lib.playlists = {"P": [sp("T1"), sp("P1")]}
    r = start_sync(client, h, liked=True, top_tracks=True, playlist_ids=["P", ""])
    assert r.status_code == 202 and set(r.json()) == {"job_id"}
    s = wait(client, h, r.json()["job_id"])
    assert s == {"status": "complete", "user_id": uid, "current_track": None, "total": 4, "processed": 4,
                 "collecting": False, "added_to_library": 1, "already_in_library": 0,
                 "queued_for_analysis": 3, "skipped": 0, "cancel_requested": False, "error_message": None}
    assert links(sql, uid) == {"L1": "liked_songs", "L2": "liked_songs", "T1": "top_tracks",
                               "P1": "playlist:P"}
    assert sql.execute("SELECT ingestion_status FROM tracks WHERE spotify_id = 'P1'").fetchone()[0] == "pending"


def test_sync_a_second_time_is_already_in_library(client, make_user, spotify_lib):
    _, h = make_user()
    spotify_lib.liked = [sp("L1")]
    wait(client, h, start_sync(client, h, liked=True).json()["job_id"])
    s = wait(client, h, start_sync(client, h, liked=True).json()["job_id"])
    assert (s["already_in_library"], s["queued_for_analysis"]) == (1, 0)


def test_sync_a_track_that_fails_is_skipped_and_the_job_completes(client, make_user, spotify_lib,
                                                                   monkeypatch, app_module):
    real = app_module._process_track

    def flaky(conn, track, job_id, user_id, source="manual"):
        if track["id"] == "BAD":
            raise ValueError("bad row")
        return real(conn, track, job_id, user_id, source=source)
    monkeypatch.setattr(app_module, "_process_track", flaky)
    _, h = make_user()
    spotify_lib.liked = [sp("BAD"), sp("OK")]
    s = wait(client, h, start_sync(client, h, liked=True).json()["job_id"])
    assert (s["status"], s["processed"], s["skipped"], s["queued_for_analysis"]) == ("complete", 2, 1, 1)


@pytest.mark.parametrize("where,error,message", [
    ("me", splib.SpotifyAuthError("401"), "spotify_token_expired"),
    ("liked", splib.SpotifyAuthError("401"), "spotify_token_expired"),
    ("liked", splib.SpotifyAPIError("502 from spotify"), "spotify_api_error: 502 from spotify"),
    ("liked", RuntimeError("boom"), "RuntimeError: boom"),
])
def test_sync_failures_end_the_job_in_error(client, make_user, spotify_lib, where, error, message):
    _, h = make_user()
    setattr(spotify_lib, where, error)
    s = wait(client, h, start_sync(client, h, liked=True).json()["job_id"])
    assert s["status"] == "error" and s["error_message"] == message


def test_a_crashed_worker_still_writes_a_terminal_status(client, make_user, spotify_lib):
    """backend/CLAUDE.md, Background work: a worker exception must end the
    job, or the client polls stale zeros forever. ODD, pinned: `collecting`
    stays True on this path."""
    _, h = make_user()
    spotify_lib.liked = [sp("L1"), RuntimeError("mid-stream")]
    s = wait(client, h, start_sync(client, h, liked=True).json()["job_id"])
    assert (s["status"], s["error_message"], s["collecting"]) == ("error", "RuntimeError: mid-stream", True)


def test_a_preflight_me_that_fails_otherwise_is_ignored(client, make_user, spotify_lib):
    _, h = make_user()
    spotify_lib.me = splib.SpotifyAPIError("me 500")
    spotify_lib.liked = [sp("L1")]
    assert wait(client, h, start_sync(client, h, liked=True).json()["job_id"])["status"] == "complete"


def test_sync_cancel_stops_at_the_next_track(client, sql, make_user, spotify_lib):
    uid, h = make_user()
    release = threading.Event()
    spotify_lib.liked = [sp(f"L{i}") for i in range(60)]
    spotify_lib.gate = (50, release)                # the 51st track waits until released
    job = start_sync(client, h, liked=True).json()["job_id"]
    wait(client, h, job, until=lambda s: s["processed"] == 50)
    r = client.delete(f"/api/ingest/status/{job}", headers=h)
    assert r.status_code == 204
    assert client.get(f"/api/ingest/status/{job}", headers=h).json()["cancel_requested"] is True
    release.set()
    s = wait(client, h, job)
    assert (s["status"], s["note"], s["processed"], s["current_track"]) == \
        ("cancelled", "Cancelled by user", 50, None)
    assert len(links(sql, uid)) == 50


@pytest.mark.parametrize("body,detail", [
    ({"access_token": "", "sources": {"liked": True}}, "access_token required"),
    ({"access_token": "SPOT", "sources": {}}, "no sources selected"),
    ({"access_token": "SPOT", "sources": {"playlist_ids": []}}, "no sources selected"),
])
def test_sync_validation_is_a_422(client, make_user, body, detail):
    _, h = make_user()
    r = client.post("/api/ingest/spotify", json=body, headers=h)
    assert r.status_code == 422 and r.json() == {"detail": detail}


def test_sync_needs_a_session(client):
    assert client.post("/api/ingest/spotify",
                       json={"access_token": "SPOT", "sources": {"liked": True}}).status_code == 401


# ------------------------------------------------------- DELETE /api/ingest/status

@pytest.fixture
def job(app_module):
    """job(user_id, status) -> a job id planted straight into JOBS."""
    made = []

    def _make(user_id, status="running"):
        jid = f"test-{len(made)}-{time.time_ns()}"
        with app_module.JOBS_LOCK:
            app_module.JOBS[jid] = {"status": status, "user_id": user_id, "cancel_requested": False}
        made.append(jid)
        return jid
    yield _make
    with app_module.JOBS_LOCK:
        for jid in made:
            app_module.JOBS.pop(jid, None)


def test_cancel_flags_a_running_job_only(client, make_user, job, app_module):
    uid, h = make_user()
    for status in ("pending", "running"):
        jid = job(uid, status)
        assert client.delete(f"/api/ingest/status/{jid}", headers=h).status_code == 204
        assert app_module.JOBS[jid]["cancel_requested"] is True
    for status in ("complete", "error", "cancelled"):
        jid = job(uid, status)
        r = client.delete(f"/api/ingest/status/{jid}", headers=h)
        assert r.status_code == 204 and r.content == b""
        assert app_module.JOBS[jid]["cancel_requested"] is False


def test_cancel_of_someone_elses_or_no_job_is_the_same_404(client, make_user, job, app_module):
    owner, _ = make_user()
    _, h = make_user()
    jid = job(owner)
    for target in (jid, "no-such-job"):
        r = client.delete(f"/api/ingest/status/{target}", headers=h)
        assert r.status_code == 404 and r.json() == {"detail": "job not found"}
    assert app_module.JOBS[jid]["cancel_requested"] is False
    assert client.delete(f"/api/ingest/status/{jid}").status_code == 401


# ---------------------------------------------------- /api/ingest/spotify-public

@pytest.fixture
def public(monkeypatch, app_module):
    """The preflight's Spotify: .meta is the metadata status (or an
    exception), .probe a list of /tracks probe statuses in call order,
    .follow the follow result (or an exception), .tracks the job's fetch
    (a list or an exception). .calls records (what, token)."""
    state = SimpleNamespace(meta=200, probe=[200], follow="ok", tracks=[], calls=[])

    def fake_request(method, url, headers=None, **kw):
        state.calls.append(("meta", headers["Authorization"].split()[1]))
        if isinstance(state.meta, Exception):
            raise state.meta
        return SimpleNamespace(status_code=state.meta, headers={}, url=url)

    def fake_probe(pid, token):
        state.calls.append(("probe", token))
        return state.probe.pop(0)

    def fake_follow(pid, token, public=False):
        state.calls.append(("follow", token))
        if isinstance(state.follow, Exception):
            raise state.follow
        return state.follow

    def fake_fetch(pid, token):
        state.calls.append(("fetch", token))
        if isinstance(state.tracks, Exception):
            raise state.tracks
        return state.tracks
    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.setattr(splib, "probe_playlist_tracks", fake_probe)
    monkeypatch.setattr(splib, "follow_playlist", fake_follow)
    monkeypatch.setattr(splib, "fetch_public_playlist_tracks", fake_fetch)
    monkeypatch.setattr(app_module, "_get_app_token", lambda force_refresh=False: "APP")
    return state


def start_public(client, h, token="USER", **body):
    body.setdefault("playlist_url", f"https://open.spotify.com/playlist/{PID}?si=x")
    if token:
        body["access_token"] = token
    return client.post("/api/ingest/spotify-public", json=body, headers=h)


def test_public_job_imports_the_playlist(client, sql, make_user, public):
    uid, h = make_user()
    public.tracks = [sp("A"), sp("B"), sp("A"), {"name": "no id"}]
    r = start_public(client, h)
    assert r.status_code == 202
    body = r.json()
    assert body == {"job_id": body["job_id"], "playlist_id": PID, "note": None}
    s = wait(client, h, body["job_id"])
    assert s == {"status": "complete", "user_id": uid, "current_track": None, "playlist_id": PID,
                 "source": "public_playlist", "auth_mode": "user_oauth", "note": None, "total": 2,
                 "processed": 2, "collecting": False, "added_to_library": 0, "already_in_library": 0,
                 "queued_for_analysis": 2, "skipped": 0, "cancel_requested": False, "error_message": None}
    assert links(sql, uid) == {"A": f"playlist:{PID}", "B": f"playlist:{PID}"}
    assert public.calls == [("meta", "USER"), ("probe", "USER"), ("fetch", "USER")]


def test_public_job_without_a_user_token_uses_the_app_token(client, make_user, public):
    _, h = make_user()
    public.tracks = [sp("A")]
    r = start_public(client, h, token=None, playlist_url=None, playlist_id=PID)
    s = wait(client, h, r.json()["job_id"])
    assert (s["status"], s["auth_mode"]) == ("complete", "app_token")
    assert public.calls == [("meta", "APP"), ("probe", "APP"), ("fetch", "APP")]


def test_public_follow_then_reprobe_carries_a_note(client, make_user, public):
    _, h = make_user()
    public.probe = [403, 200]
    public.tracks = [sp("A")]
    r = start_public(client, h)
    note = "Followed playlist to enable ingest — visible in your Spotify library."
    assert r.status_code == 202 and r.json()["note"] == note
    s = wait(client, h, r.json()["job_id"])
    assert (s["status"], s["note"]) == ("complete", note)
    assert [c[0] for c in public.calls] == ["meta", "probe", "follow", "probe", "fetch"]


@pytest.mark.parametrize("token,meta,probe,follow,status,error", [
    ("USER", 404, [], None, 404, "playlist_not_found"),
    ("USER", 400, [], None, 404, "playlist_not_found"),
    ("USER", 401, [], None, 401, "spotify_token_expired"),
    (None, 401, [], None, 403, "playlist_private"),
    ("USER", 403, [], None, 403, "playlist_private"),
    ("USER", 500, [], None, 502, "spotify_api_error: 500"),
    ("USER", 200, [404], None, 404, "playlist_not_found"),
    ("USER", 200, [401], None, 401, "spotify_token_expired"),
    (None, 200, [401], None, 403, "playlist_private"),
    (None, 200, [403], None, 403, "playlist_private"),
    ("USER", 200, [403], "scope", 401, "spotify_scope_upgrade_required"),
    ("USER", 200, [403], "error", 403, "playlist_private"),
    ("USER", 200, [403, 403], "ok", 403, "playlist_private"),
    ("USER", 200, [403], splib.SpotifyAuthError("401"), 401, "spotify_token_expired"),
    ("USER", 200, [500], None, 502, "spotify_api_error: 500"),
])
def test_public_preflight_refusals(client, make_user, public, app_module,
                                   token, meta, probe, follow, status, error):
    _, h = make_user()
    public.meta, public.probe, public.follow = meta, list(probe), follow
    before = set(app_module.JOBS)
    r = start_public(client, h, token=token)
    assert r.status_code == status
    assert r.json()["detail"] == {"error": error}
    assert set(app_module.JOBS) == before                       # no job is created


def test_public_preflight_network_errors_are_502s(client, make_user, public):
    _, h = make_user()
    public.meta = requests.ConnectionError("down")
    r = start_public(client, h)
    assert r.status_code == 502 and r.json()["detail"] == {"error": "spotify_unreachable: down"}
    public.meta, public.probe, public.follow = 200, [403], requests.ConnectionError("follow down")
    r = start_public(client, h)
    assert r.status_code == 502 and r.json()["detail"] == {"error": "spotify_unreachable: follow down"}


def test_public_without_app_credentials_is_a_500(client, make_user, monkeypatch, app_module):
    """The real _get_app_token: its HTTPException is not caught by the
    preflight, so it reaches the client as is."""
    monkeypatch.setattr(app_module, "app_config", None)
    monkeypatch.setattr(app_module, "_APP_TOKEN_STATE", {"token": None, "expires_at": 0.0})
    _, h = make_user()
    r = start_public(client, h, token=None)
    assert r.status_code == 500 and r.json()["detail"] == {"error": "spotify_app_credentials_missing"}


@pytest.mark.parametrize("body,status,detail", [
    ({}, 422, {"error": "playlist_url or playlist_id required"}),
    ({"playlist_url": "https://example.com/not-a-playlist"}, 400, {"error": "invalid_playlist_url"}),
])
def test_public_needs_a_playlist(client, make_user, public, body, status, detail):
    _, h = make_user()
    r = client.post("/api/ingest/spotify-public", json=body, headers=h)
    assert r.status_code == status and r.json()["detail"] == detail
    assert public.calls == []


@pytest.mark.parametrize("error,message", [
    (splib.PlaylistNotFoundError("gone"), "playlist_not_found"),
    (splib.SpotifyAPIError("503"), "spotify_api_error: 503"),
    (RuntimeError("boom"), "RuntimeError: boom"),
])
def test_public_job_failures_end_the_job_in_error(client, make_user, public, error, message):
    _, h = make_user()
    public.tracks = error
    s = wait(client, h, start_public(client, h).json()["job_id"])
    assert (s["status"], s["error_message"]) == ("error", message)
