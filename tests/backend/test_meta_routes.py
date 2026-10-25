"""Small public and per-track routes, characterised before backend/app.py is
split into routers: health, moods, demo/moods, client-config,
spotify/config, the /callback bridge page, track/{apple_id}/spotify and
tracks/{key}/features. Pinned as they are on main at 535efff, odd parts
included and noted.
"""
from types import SimpleNamespace

import pytest

import bootstrap  # backend/bootstrap.py: app_config and PROJECT_ROOT live there


def add_track(sql, user_id=None, **cols):
    cols.setdefault("title", "Song")
    cols.setdefault("artist", "Artist")
    cols.setdefault("vibe_score", 50)
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    tid = int(sql.execute(f"INSERT INTO tracks ({names}) VALUES ({marks})", tuple(cols.values())).lastrowid)
    if user_id is not None:
        sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (user_id, tid))
    sql.commit()
    return tid


# ------------------------------------------------------------------ health

def test_health_counts_every_catalogue_row(client, sql):
    assert client.get("/api/health").json() == {"status": "ok", "track_count": 0}
    add_track(sql, ingestion_status="pending")
    add_track(sql, ingestion_status="done")
    assert client.get("/api/health").json() == {"status": "ok", "track_count": 2}


def test_moods_is_the_scoring_list(client):
    assert client.get("/api/moods").json() == {"moods": ["sleep", "chill", "melancholy", "steady",
                                                         "moody", "hype", "aggressive", "beast"]}


# ------------------------------------------------------------- demo/moods

def test_demo_moods_picks_the_track_nearest_each_band_centre(client, sql):
    add_track(sql, title="near", artist="A", album="Al", artwork_url="https://n",
              ingestion_status="done", vibe_score=31)
    add_track(sql, title="far", artwork_url="https://f", ingestion_status="done", vibe_score=21)
    add_track(sql, title="ml wins", artwork_url="https://m", ingestion_status="done",
              vibe_score=5, vibe_score_ml=0.70)            # COALESCE(ml * 100, vibe_score)
    add_track(sql, title="no art", ingestion_status="done", vibe_score=50)
    add_track(sql, title="pending", artwork_url="https://p", ingestion_status="pending", vibe_score=90)
    moods = client.get("/api/demo/moods").json()["moods"]
    assert [m["mood"] for m in moods] == ["sleep", "chill", "steady", "hype", "beast"]
    by = {m["mood"]: m for m in moods}
    assert by["chill"] == {"mood": "chill", "title": "near", "artist": "A", "album": "Al",
                           "artwork_url": "https://n", "vibe": 31}
    assert by["hype"]["title"] == "ml wins" and by["hype"]["vibe"] == 70
    for empty, target in (("sleep", 10), ("steady", 50), ("beast", 90)):
        assert by[empty] == {"mood": empty, "title": None, "artist": None, "album": None,
                             "artwork_url": None, "vibe": target}


def test_demo_moods_reports_a_zero_vibe_as_the_band_centre(client, sql):
    """ODD, pinned: `int(round(v or target))` turns a real 0 into 10."""
    add_track(sql, title="silent", artwork_url="https://s", ingestion_status="done", vibe_score=0)
    sleep = client.get("/api/demo/moods").json()["moods"][0]
    assert sleep["title"] == "silent" and sleep["vibe"] == 10


# ---------------------------------------------------- client and Spotify config

@pytest.mark.parametrize("env,expected", [
    (None, {"env": "prod", "debug": False}),
    ("dev", {"env": "dev", "debug": True}),
    (" DEV ", {"env": "dev", "debug": True}),
    ("staging", {"env": "prod", "debug": False}),
])
def test_client_config_follows_vibescape_env(client, monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("VIBESCAPE_ENV", raising=False)
    else:
        monkeypatch.setenv("VIBESCAPE_ENV", env)
    assert client.get("/api/client-config").json() == expected


def test_spotify_config_exposes_the_id_and_redirect_never_the_secret(client, monkeypatch, app_module):
    monkeypatch.setattr(bootstrap, "app_config", SimpleNamespace(
        SPOTIFY_CLIENT_ID="cid", SPOTIFY_CLIENT_SECRET="secret", SPOTIFY_REDIRECT_URI="https://x/callback"))
    r = client.get("/api/spotify/config")
    assert r.json() == {"client_id": "cid", "redirect_uri": "https://x/callback"}
    assert "secret" not in r.text


def test_spotify_config_is_empty_strings_without_a_config(client, monkeypatch, app_module):
    monkeypatch.setattr(bootstrap, "app_config", None)
    assert client.get("/api/spotify/config").json() == {"client_id": "", "redirect_uri": ""}


# ------------------------------------------------------------- /callback

def test_callback_is_a_static_bridge_page_that_renames_the_oauth_params(client):
    """Frontend contract (backend/CLAUDE.md): the bridge page hands the code
    back to the app as `spotify_code`, not `code`. Breaking this silently
    broke in-app Spotify connect once."""
    r = client.get("/callback?code=abc&state=vs_next")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    page = r.text
    assert "abc" not in page                                    # read client-side, never echoed
    assert "out.set('spotify_code', code)" in page
    assert "out.set('spotify_error', error)" in page
    assert "out.set('spotify_state', state)" in page
    assert "localStorage.setItem('spotify_pending_auth'" in page
    assert "var RETURN_PATHS = { vs_next: '/' };" in page      # fixed paths: no open redirect
    assert "window.name === 'vibescape-oauth-popup'" in page
    assert client.get("/callback").text == page


# ---------------------------------------------------- track/{apple_id}/spotify

def test_apple_id_resolves_to_the_callers_spotify_id(client, sql, make_user):
    uid, h = make_user()
    add_track(sql, uid, apple_id=111, spotify_id="SP111")
    assert client.get("/api/track/111/spotify", headers=h).json() == \
        {"spotify_id": "SP111", "uri": "spotify:track:SP111"}


def test_apple_id_outside_the_callers_library_or_without_spotify_is_a_404(client, sql, make_user):
    owner, _ = make_user()
    uid, h = make_user()
    add_track(sql, owner, apple_id=222, spotify_id="SP222")
    add_track(sql, uid, apple_id=333, spotify_id=None)
    for apple_id in (222, 333, 444):
        r = client.get(f"/api/track/{apple_id}/spotify", headers=h)
        assert r.status_code == 404
        assert r.json() == {"detail": "no spotify id for track"}   # a string, not {"error": ...}
    assert client.get("/api/track/abc/spotify", headers=h).status_code == 422
    assert client.get("/api/track/111/spotify").status_code == 401


# ------------------------------------------------------- tracks/{key}/features

def test_features_by_spotify_id_or_numeric_id(client, sql, make_user):
    uid, h = make_user()
    tid = add_track(sql, uid, apple_id=5, spotify_id="SPF", title="T", artist="A", tempo=120.5,
                    energy_mean=0.4, energy=0.9, mfcc_json="[1.5, 2.5]", chroma_mean_json="not json",
                    activation=61.0, valence=40.0, activation_relative=55.0, mood="hype",
                    classification_source="librosa")
    body = client.get("/api/tracks/SPF/features", headers=h).json()
    assert client.get(f"/api/tracks/{tid}/features", headers=h).json() == body
    assert set(body) == {"apple_id", "spotify_id", "title", "artist", "features", "axes", "mood",
                         "classification_source"}
    assert (body["apple_id"], body["spotify_id"], body["title"], body["artist"]) == (5, "SPF", "T", "A")
    f = body["features"]
    assert set(f) == {"tempo", "tempo_stability", "onset_rate", "energy_mean", "energy_std",
                      "brightness", "bandwidth", "rolloff", "spectral_contrast", "flatness", "zcr",
                      "timbre_variability", "valence_mode", "tonnetz_std", "acousticness",
                      "mfcc_mean", "chroma_mean"}
    assert f["tempo"] == 120.5 and f["energy_mean"] == 0.4
    assert f["mfcc_mean"] == [1.5, 2.5] and f["chroma_mean"] == []    # unreadable JSON -> []
    assert body["axes"] == {"activation": 61.0, "valence": 40.0, "activation_relative": 55.0}
    assert (body["mood"], body["classification_source"]) == ("hype", "librosa")


def test_features_energy_mean_falls_back_to_energy(client, sql, make_user):
    uid, h = make_user()
    add_track(sql, uid, spotify_id="SPE", energy=0.9)
    assert client.get("/api/tracks/SPE/features", headers=h).json()["features"]["energy_mean"] == 0.9


def test_features_are_not_scoped_to_the_callers_library(client, sql, make_user):
    """ODD, pinned: the docstring says per-user, but any signed-in user can
    read any catalogue track's features."""
    owner, _ = make_user()
    _, h = make_user()
    add_track(sql, owner, spotify_id="THEIRS")
    assert client.get("/api/tracks/THEIRS/features", headers=h).status_code == 200


def test_features_unknown_track_is_a_404(client, make_user):
    _, h = make_user()
    r = client.get("/api/tracks/nope/features", headers=h)
    assert r.status_code == 404 and r.json() == {"detail": "track not found"}
    assert client.get("/api/tracks/nope/features").status_code == 401
