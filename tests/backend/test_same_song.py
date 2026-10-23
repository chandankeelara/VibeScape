"""The DJ never serves another release of a song the session already had
(backend/same_song.py, backend/app.py _dj_drop_repeats), and a skip of
such a repeat is not a taste signal (backend/dj_replay.py song_keys)."""
import sqlite3

import numpy as np
import pytest

import dj_replay as R
import same_song as ss

from ingest_pipeline.fused_vector import FUSED_DIM  # noqa: E402


# ------------------------------------------------------------------ the key

@pytest.mark.parametrize("a,b", [
    (("Belakina Kavithe", "Sonu Nigam", "kn"),
     ('Belakina Kavithe (From "Banaras")', "Sonu Nigam", "kn")),
    (("Mask Off", "Future", "en"), ("Mask Off (feat. Kendrick Lamar) - Remix", "Future", "en")),
    (("Old Town Road", "Lil Nas X", "en"),
     ("Old Town Road (feat. Billy Ray Cyrus) - Remix", "Lil Nas X, Billy Ray Cyrus", "en")),
    (("Preetiya Hesare Neenu", "Raghu Dixit", "kn"),
     ("Preetiya Hesare Neenu - Live Version", "Raghu Dixit & Friends", "kn")),
    (("Song", "A feat. B", "en"), ("Song - Remastered 2011", "A ft. C", "en")),
    (("ಬೆಳಕಿನ ಕವಿತೆ", "X", "kn"), ("ಬೆಳಕಿನ ಕವಿತೆ (From \"Y\")", "X", "kn")),
])
def test_releases_of_one_song_share_a_key(a, b):
    assert ss.song_key(*a) is not None
    assert ss.song_key(*a) == ss.song_key(*b)


def test_a_dubbed_song_is_two_songs():
    """Tabaahi's Kannada and Hindi versions: same title, same composer."""
    assert ss.song_key('Tabaahi - From "Toxic - Kannada"', "Ravi Basrur", "kn") != \
        ss.song_key('Tabaahi - From "Toxic"', "Ravi Basrur", "hi")


@pytest.mark.parametrize("title,artist", [("?", "?"), ("", "A"), ("Song", ""), ("روحي مرتحالك", "Amr Diab")])
def test_no_usable_title_is_no_key(title, artist):
    """The junk rows titled '?', and titles in a script the key keeps no
    letters of: an empty key would make all of an artist's songs one."""
    assert ss.song_key(title, artist, "ar") is None


# ------------------------------------------------------------- the replay

def test_a_skip_of_a_repeat_does_not_move_the_query():
    rng = np.random.default_rng(1)
    vecs = {i: rng.normal(size=16) for i in (1, 2, 3)}
    mu = np.zeros(16)
    keys = {1: ("belakina kavithe", "sonu nigam", "kn"), 2: ("belakina kavithe", "sonu nigam", "kn"),
            3: ("other", "x", "kn")}
    done = {"id": 1, "action": "completed", "played_ratio": 1.0}
    rep = {"id": 2, "action": "skipped", "played_ratio": 0.1}
    alone = R.replay([done], vecs, mu, song_keys=keys)
    with_repeat = R.replay([done, rep], vecs, mu, song_keys=keys)
    assert np.allclose(with_repeat.query, alone.query)
    assert with_repeat.streak == 0
    # a skip of a different song still counts
    other = R.replay([done, {"id": 3, "action": "skipped", "played_ratio": 0.1}], vecs, mu,
                     song_keys=keys)
    assert other.streak == 1 and not np.allclose(other.query, alone.query)


# ----------------------------------------------------------- the endpoint

def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


@pytest.fixture
def releases(make_user, make_tracks, sql):
    """Twelve songs in one cluster, each with three releases (album, single,
    'From "Film"') whose vectors are near copies -- releases of one song sit
    next to each other in the space, which is why the DJ served them back to
    back -- plus forty other songs further out. Returns (headers,
    [[release ids] per song], [other ids])."""
    rng = np.random.default_rng(41)
    base, c, far = (rng.normal(size=FUSED_DIM) for _ in range(3))
    uid, h = make_user()
    songs = []
    for s in range(12):
        v = base * 3 + c + 0.3 * rng.normal(size=FUSED_DIM)
        ids = []
        for rel, title in enumerate((f"Song {s}", f"Song {s} - Single", f'Song {s} (From "Film")')):
            (tid,) = make_tracks(uid, [unit(v + 0.01 * rng.normal(size=FUSED_DIM))],
                                 artist=f"Artist {s}", titles={0: title})
            ids.append(tid)
        songs.append(ids)
    others = make_tracks(uid, [unit(base * 3 + far + 0.3 * rng.normal(size=FUSED_DIM))
                               for _ in range(40)], artist="Other",
                         titles={i: f"Other {i}" for i in range(40)})
    sql.execute("UPDATE tracks SET language = 'kn'")
    sql.commit()
    return h, songs, others


def _post(client, h, seed, body, limit=8):
    r = client.post(f"/api/tracks/{seed}/similar", json={"mode": "dj", "limit": limit, **body},
                    headers=h)
    assert r.status_code == 200, r.text
    return [t["id"] for t in r.json()["tracks"]]


def test_another_release_of_a_played_song_is_not_served(client, releases):
    """Belakina Kavithe's single right after its album release, in the
    Kannada vibe tests; fails with the filter off."""
    h, songs, _ = releases
    played = songs[0][0]
    got = _post(client, h, played, {"events": [{"id": played, "action": "completed",
                                                "played_ratio": 1.0}], "exclude_ids": [played]})
    assert not set(got) & set(songs[0])
    # and no song twice in one response
    owner = {t: i for i, ids in enumerate(songs) for t in ids}
    picked = [owner[t] for t in got if t in owner]
    assert len(picked) == len(set(picked))


@pytest.mark.parametrize("recency", [True, False])
def test_the_response_still_has_limit_tracks(client, releases, app_module, monkeypatch, recency):
    """Ten songs played: thirty of the nearest candidates are their other
    releases. With the recency re-rank off the pool used to be exactly
    `limit`, so filtering would leave a short response."""
    h, songs, _ = releases
    if not recency:
        monkeypatch.setattr(app_module, "_DJ_RECENCY_WEIGHT", 0.0)
    played = [ids[0] for ids in songs[:10]]
    events = [{"id": t, "action": "completed", "played_ratio": 1.0} for t in played]
    got = _post(client, h, played[-1], {"events": events, "exclude_ids": played})
    assert len(got) == 8
    assert not set(got) & {t for ids in songs[:10] for t in ids}


def test_a_dubbed_version_is_not_filtered(app_module):
    rows = [{"title": 'Tabaahi - From "Toxic"', "artist": "Ravi Basrur", "language": "hi"},
            {"title": "Other", "artist": "Y", "language": "kn"}]
    session = {ss.song_key('Tabaahi - From "Toxic - Kannada"', "Ravi Basrur", "kn")}
    kept = app_module._dj_drop_repeats(
        rows, lambda r: ss.song_key(r["title"], r["artist"], r["language"]), session)
    assert kept == rows


def _register_turso_functions(conn):
    def parse(x):
        if isinstance(x, (bytes, bytearray, memoryview)):
            return np.frombuffer(bytes(x), dtype=np.float32).astype(np.float64)
        return np.array([float(v) for v in str(x).strip("[]").split(",")])

    def dist(a, b):
        if a is None or b is None:
            return None
        u, v = parse(a), parse(b)
        return float(1.0 - (u @ v) / (np.linalg.norm(u) * np.linalg.norm(v)))
    conn.create_function("vector32", 1, lambda s: s)
    conn.create_function("vector_extract", 1,
                         lambda b: None if b is None else "[" + ",".join(
                             repr(float(x)) for x in parse(b)) + "]")
    conn.create_function("vector_distance_cos", 2, dist)


@pytest.mark.parametrize("recency", [True, False])
def test_both_ranking_paths_filter_repeats_alike(client, releases, app_module, db,
                                                 monkeypatch, recency):
    import db_client
    h, songs, _ = releases
    if not recency:
        monkeypatch.setattr(app_module, "_DJ_RECENCY_WEIGHT", 0.0)
    played = [ids[0] for ids in songs[:6]]
    body = {"events": [{"id": t, "action": "completed", "played_ratio": 1.0} for t in played]
            + [{"id": songs[0][1], "action": "skipped", "played_ratio": 0.1}],
            "exclude_ids": played + [songs[0][1]]}
    local = _post(client, h, played[-1], body)

    def turso_shaped():
        conn = sqlite3.connect(str(db), check_same_thread=False,
                               factory=db_client._SqliteConnection)
        conn.row_factory = sqlite3.Row
        _register_turso_functions(conn)
        return conn
    monkeypatch.setattr(app_module, "get_conn", turso_shaped)
    monkeypatch.setenv("DB_BACKEND", "turso")
    app_module._dj_mean_cache.clear()
    turso = _post(client, h, played[-1], body)
    assert turso == local
    assert not set(turso) & {t for ids in songs[:6] for t in ids}
