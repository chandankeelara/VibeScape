"""POST /api/ingest/playlist-url, end to end on a throwaway DB.

The service readers and Spotify search are faked; the job thread,
_process_track and the database are real.
"""
import time

import pytest

import playlist_import as P


@pytest.fixture
def importer(app_module, monkeypatch):
    """Fake the network: `.playlist` is what the reader returns, `.search`
    answers Spotify searches, `.searches` records them."""
    class State:
        playlist = P.Playlist("Fake list", [])
        fetch_error = None
        searches = []

        @staticmethod
        def search(query):
            return []

    state = State()

    def fake_fetch(source, get, ytdlp_extract=None):
        if state.fetch_error:
            raise state.fetch_error
        return state.playlist

    def fake_search(token, query, get, limit=5, pacer=None):
        state.searches.append(query)
        return state.search(query)

    monkeypatch.setattr(P, "fetch", fake_fetch)
    monkeypatch.setattr(P, "spotify_search", fake_search)
    monkeypatch.setattr(app_module, "_get_app_token", lambda force_refresh=False: "app-token")
    return state


def start(client, headers, url="https://music.apple.com/us/playlist/x/pl.abc", **extra):
    r = client.post("/api/ingest/playlist-url", json={"url": url, **extra}, headers=headers)
    return r


def wait(client, headers, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/ingest/status/{job_id}", headers=headers).json()
        if s["status"] in ("complete", "error", "cancelled"):
            return s
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def run(client, headers, **kw):
    r = start(client, headers, **kw)
    assert r.status_code == 202, r.text
    return r.json(), wait(client, headers, r.json()["job_id"])


def sp(cid, name, artist, ms=200_000):
    return {"id": cid, "name": name, "artists": [{"name": artist}], "duration_ms": ms,
            "album": {"name": "Alb", "images": [{"url": "https://img"}]}, "external_ids": {}}


# ------------------------------------------------------------------- contract

def test_requires_a_session(client):
    assert client.post("/api/ingest/playlist-url", json={"url": "x"}).status_code == 401


def test_invalid_link_is_400(client, make_user):
    _, h = make_user()
    r = start(client, h, url="https://example.com/nope")
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_playlist_url"


@pytest.mark.parametrize("url,code,name", [
    ("https://www.deezer.com/en/playlist/3155776842", "deezer_not_supported", "Deezer"),
])
def test_recognised_but_unsupported_services_are_400(client, make_user, url, code, name):
    _, h = make_user()
    r = start(client, h, url=url)
    assert r.status_code == 400 and r.json()["detail"]["error"] == code
    assert name in r.json()["detail"]["message"]


def test_amazon_links_are_imported(client, make_user, importer):
    _, h = make_user()
    importer.playlist = P.Playlist("Lo-Fi Hip-Hop", [P.Item(title="katana", artist="chi square", duration_ms=161000)])
    importer.search = lambda q: [sp("SPK", "katana", "chi square", 161000)]
    body, s = run(client, h, url="https://music.amazon.com/playlists/B07M7FZKWQ")
    assert body["platform"] == "amazon"
    assert s["status"] == "complete" and s["playlist_name"] == "Lo-Fi Hip-Hop" and s["queued_for_analysis"] == 1


def test_returns_the_platform(client, make_user, importer):
    _, h = make_user()
    body, _ = run(client, h, url="https://www.youtube.com/playlist?list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i")
    assert body["platform"] == "youtube"


# --------------------------------------------------------------------- import

def test_spotify_links_import_by_id_with_no_searches(client, make_user, importer, sql):
    uid, h = make_user()
    importer.playlist = P.Playlist("Top", [
        P.Item(title="One", artist="Ann", spotify_id="SPID1", duration_ms=180000, preview_url="https://p/1"),
        P.Item(title="Two", artist="Bob", spotify_id="SPID2"),
    ])
    _, s = run(client, h, url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    assert s["status"] == "complete" and s["playlist_name"] == "Top"
    assert (s["processed"], s["total"], s["queued_for_analysis"]) == (2, 2, 2)
    assert importer.searches == []
    rows = sql.execute("SELECT spotify_id, title, artist, ingestion_status, preview_url FROM tracks "
                       "WHERE spotify_id IN ('SPID1','SPID2') ORDER BY spotify_id").fetchall()
    assert [tuple(r) for r in rows] == [("SPID1", "One", "Ann", "pending", None),
                                        ("SPID2", "Two", "Bob", "pending", None)]
    links = sql.execute("SELECT COUNT(*), MIN(source) FROM user_tracks WHERE user_id=?", (uid,)).fetchone()
    assert links[0] == 2 and links[1].startswith("spotify:")


def test_songs_the_catalogue_has_cost_no_searches(client, make_user, importer, sql):
    uid, h = make_user()
    sql.execute("INSERT INTO tracks (title, artist, spotify_id, isrc, duration_ms, ingestion_status, vibe_score) "
                "VALUES ('Boston', 'Stella Lefty', 'CATSID', 'QZJ842603468', 170000, 'done', 50)")
    sql.commit()
    importer.playlist = P.Playlist("Apple chart", [
        P.Item(title="Boston", artist="STELLA LEFTY", duration_ms=171000),
    ])
    _, s = run(client, h)
    assert s["added_to_library"] == 1 and importer.searches == []
    assert sql.execute("SELECT COUNT(*) FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
                       "WHERE ut.user_id=? AND t.spotify_id='CATSID'", (uid,)).fetchone()[0] == 1


def test_new_songs_are_searched_and_unmatched_ones_listed(client, make_user, importer):
    _, h = make_user()
    importer.playlist = P.Playlist("Apple list", [
        P.Item(title="the cure", artist="Olivia Rodrigo", duration_ms=297000),
        P.Item(title="Unknown Song", artist="Nobody At All", duration_ms=100000),
    ])
    importer.search = lambda q: [sp("SPCURE", "the cure", "Olivia Rodrigo", 297000)] if "cure" in q else []
    _, s = run(client, h)
    assert s["status"] == "complete"
    assert (s["queued_for_analysis"], s["not_matched"], s["processed"]) == (1, 1, 2)
    assert s["unmatched"] == [{"title": "Unknown Song", "artist": "Nobody At All"}]
    assert len([q for q in importer.searches if "Unknown" in q]) <= P.MAX_SEARCHES_PER_SONG


def test_the_same_song_twice_is_linked_once(client, make_user, importer, sql):
    uid, h = make_user()
    item = P.Item(title="One", artist="Ann", spotify_id="DUPE")
    importer.playlist = P.Playlist("Dupes", [item, item])
    _, s = run(client, h, url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    assert (s["queued_for_analysis"], s["already_in_library"], s["processed"]) == (1, 1, 2)
    assert sql.execute("SELECT COUNT(*) FROM user_tracks WHERE user_id=?", (uid,)).fetchone()[0] == 1


def test_a_reader_failure_is_a_job_error(client, make_user, importer):
    _, h = make_user()
    importer.fetch_error = P.ImportError_("playlist_not_found")
    _, s = run(client, h)
    assert s["status"] == "error" and s["error_message"] == "playlist_not_found"


def test_a_rate_limit_stops_the_import_and_keeps_what_landed(client, make_user, importer, sql):
    uid, h = make_user()
    importer.playlist = P.Playlist("Long", [
        P.Item(title="First", artist="Ann", duration_ms=1000),
        P.Item(title="Second", artist="Bob", duration_ms=1000),
        P.Item(title="Third", artist="Cat", duration_ms=1000),
    ])

    def search(q):
        if "First" in q:
            return [sp("SP1", "First", "Ann", 1000)]
        raise P.SpotifyRateLimited(70320)
    importer.search = search
    _, s = run(client, h)
    assert s["status"] == "error" and s["error_message"] == "spotify_rate_limited"
    assert "20 h" in s["note"] or "h)" in s["note"]
    # one landed, the rest were never tried — so NOT reported as unmatched
    assert (s["queued_for_analysis"], s["processed"], s["not_matched"]) == (1, 1, 0)
    assert sql.execute("SELECT COUNT(*) FROM user_tracks WHERE user_id=?", (uid,)).fetchone()[0] == 1


def test_a_spotify_token_is_used_for_spotify_links_first(client, make_user, importer, monkeypatch, app_module):
    _, h = make_user()
    seen = {}

    def fake_api(playlist_id, token):
        seen["args"] = (playlist_id, token)
        return [sp("API1", "Via API", "Ann")]
    monkeypatch.setattr(app_module.splib, "fetch_public_playlist_tracks", fake_api)
    _, s = run(client, h, url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", access_token="user-tok")
    assert seen["args"] == ("37i9dQZF1DXcBWIGoYBM5M", "user-tok")
    assert s["queued_for_analysis"] == 1 and importer.searches == []


def test_when_the_spotify_api_refuses_the_embed_is_used(client, make_user, importer, monkeypatch, app_module):
    _, h = make_user()

    def refused(playlist_id, token):
        raise app_module.splib.PlaylistPrivateError("403")
    monkeypatch.setattr(app_module.splib, "fetch_public_playlist_tracks", refused)
    importer.playlist = P.Playlist("From embed", [P.Item(title="E", artist="F", spotify_id="EMB1")])
    _, s = run(client, h, url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", access_token="user-tok")
    assert s["status"] == "complete" and s["playlist_name"] == "From embed" and s["queued_for_analysis"] == 1


def test_jobs_are_private_to_their_user(client, make_user, importer):
    _, h1 = make_user()
    _, h2 = make_user()
    body, _ = run(client, h1)
    assert client.get(f"/api/ingest/status/{body['job_id']}", headers=h2).status_code == 404
