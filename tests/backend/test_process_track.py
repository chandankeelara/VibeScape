"""_process_track: one catalogue row per recording, one user link per song.

Real database (the test's SQLite file, schema.sql's indexes included);
only Spotify is faked, for the two endpoints at the end.
"""
import sqlite3

import pytest


def sp(cid, isrc=None, name="Song", artist="Artist"):
    return {"id": cid, "name": name, "artists": [{"name": artist}], "duration_ms": 200_000,
            "album": {"name": "Alb", "images": [{"url": "https://img"}]},
            "external_ids": {"isrc": isrc} if isrc else {}}


@pytest.fixture
def add_track(sql):
    """add_track(spotify_id, isrc=None, status='done', user=None) -> tracks.id"""
    def _add(spotify_id, isrc=None, status="done", user=None):
        cur = sql.execute("INSERT INTO tracks (title, artist, spotify_id, isrc, ingestion_status, "
                          "vibe_score) VALUES ('Song', 'Artist', ?, ?, ?, 0)",
                          (spotify_id, isrc, status))
        tid = int(cur.lastrowid)
        if user is not None:
            sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (user, tid))
        sql.commit()
        return tid
    return _add


@pytest.fixture
def process(app_module, db):
    """process(track, user_id) -> the bucket, on a connection of the app's own."""
    def _run(track, user_id):
        conn = app_module.get_conn()
        try:
            return app_module._process_track(conn, track, "no-job", user_id, source="search")
        finally:
            conn.close()
    return _run


def links(sql, user_id):
    return [r[0] for r in sql.execute(
        "SELECT track_id FROM user_tracks WHERE user_id = ? ORDER BY track_id", (user_id,))]


def track_count(sql):
    return sql.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]


# ------------------------------------------------------------------ ISRC reuse

def test_another_release_of_a_finished_song_links_to_its_row(sql, make_user, add_track, process):
    uid, _ = make_user()
    album = add_track("album-id", isrc="USABC0000001")
    assert process(sp("single-id", isrc="USABC0000001"), uid) == "added_to_library"
    assert track_count(sql) == 1
    assert links(sql, uid) == [album]


def test_a_release_the_user_already_has_is_already_in_library(sql, make_user, add_track, process):
    uid, _ = make_user()
    album = add_track("album-id", isrc="USABC0000001", user=uid)
    assert process(sp("deluxe-id", isrc="USABC0000001"), uid) == "already_in_library"
    assert track_count(sql) == 1
    assert links(sql, uid) == [album]


def test_a_finished_row_is_preferred_then_the_lowest_id(sql, make_user, add_track, process):
    uid, _ = make_user()
    add_track("pending-id", isrc="X1", status="pending")
    done_a = add_track("done-a", isrc="X1")
    add_track("done-b", isrc="X1")
    assert process(sp("new-id", isrc="X1"), uid) == "added_to_library"
    assert links(sql, uid) == [done_a]


def test_a_pending_match_is_reused_and_queued(sql, make_user, add_track, process):
    uid, _ = make_user()
    pending = add_track("pending-id", isrc="X1", status="pending")
    assert process(sp("new-id", isrc="X1"), uid) == "queued_for_analysis"
    assert track_count(sql) == 1 and links(sql, uid) == [pending]


def test_the_exact_spotify_id_wins_over_an_isrc_match(sql, make_user, add_track, process):
    uid, _ = make_user()
    add_track("other-release", isrc="X1")
    own = add_track("exact-id", isrc="X1", status="pending")
    assert process(sp("exact-id", isrc="X1"), uid) == "queued_for_analysis"
    assert links(sql, uid) == [own]


def test_no_isrc_never_matches_rows_without_one(sql, make_user, add_track, process):
    uid, _ = make_user()
    add_track("no-isrc-row")
    assert process(sp("new-id"), uid) == "queued_for_analysis"
    assert track_count(sql) == 2
    row = sql.execute("SELECT isrc, ingestion_status FROM tracks WHERE spotify_id = 'new-id'").fetchone()
    assert row["isrc"] is None and row["ingestion_status"] == "pending"


# ------------------------------------------------------------ query budget

class CountingConn:
    """Records every statement sent through execute() and execute_batch()."""

    def __init__(self, conn):
        self._conn, self.reads, self.writes, self.batches = conn, [], [], []

    def execute(self, sql, params=()):
        (self.reads if sql.lstrip().upper().startswith("SELECT") else self.writes).append(sql)
        return self._conn.execute(sql, params)

    def execute_batch(self, stmts):
        stmts = list(stmts)
        self.batches.append(stmts)
        return self._conn.execute_batch(stmts)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def counted(app_module, db):
    def _run(track, user_id):
        conn = CountingConn(app_module.get_conn())
        try:
            return app_module._process_track(conn, track, "no-job", user_id), conn
        finally:
            conn.close()
    return _run


def test_each_case_reads_tracks_once(sql, make_user, add_track, counted):
    """Production's `tracks` has no index (backlog 1.13): every read is a scan
    Turso bills. It used to be two reads before any write."""
    uid, _ = make_user()
    add_track("known", isrc="X1")
    for track in (sp("known"), sp("other-release", isrc="X1"), sp("brand-new", isrc="Y1")):
        _, conn = counted(track, uid)
        assert len(conn.reads) == 1, conn.reads
    _, conn = counted(sp("known"), uid)          # now linked
    assert len(conn.reads) == 1 and not conn.writes and not conn.batches


# ------------------------------------------------- one write for row + link

def test_a_new_row_and_its_link_are_one_batch(sql, make_user, counted):
    """On Turso each execute() autocommits; a crash between the INSERT and
    the link used to orphan the row for good (backlog 2.2)."""
    uid, _ = make_user()
    result, conn = counted(sp("brand-new", isrc="Y1"), uid)
    assert result == "queued_for_analysis"
    assert not conn.writes
    assert len(conn.batches) == 1 and len(conn.batches[0]) == 2
    tid = sql.execute("SELECT id FROM tracks WHERE spotify_id = 'brand-new'").fetchone()[0]
    assert links(sql, uid) == [tid]


def test_a_failed_link_leaves_no_row(sql, make_user, process):
    uid, _ = make_user()
    sql.execute("CREATE TRIGGER no_links BEFORE INSERT ON user_tracks "
                "BEGIN SELECT RAISE(ABORT, 'link refused'); END")
    sql.commit()
    with pytest.raises(sqlite3.Error, match="link refused"):
        process(sp("brand-new"), uid)
    assert track_count(sql) == 0


# ------------------------------------------------------------------- the race

@pytest.fixture
def lost_race(app_module, monkeypatch):
    """The first lookup misses, as when another request inserts the same
    spotify_id between our read and our INSERT. Returns the lookups made."""
    real = app_module._find_track
    calls = []

    def first_misses(conn, *a):
        calls.append(a)
        return None if len(calls) == 1 else real(conn, *a)
    monkeypatch.setattr(app_module, "_find_track", first_misses)
    return calls


def test_losing_the_insert_race_links_the_winners_row(sql, make_user, add_track, process, lost_race):
    uid, _ = make_user()
    winner = add_track("raced", status="pending")
    assert process(sp("raced"), uid) == "queued_for_analysis"
    assert track_count(sql) == 1 and links(sql, uid) == [winner]
    assert len(lost_race) == 2


def test_a_double_submit_is_already_in_library(sql, make_user, add_track, process, lost_race):
    uid, _ = make_user()
    winner = add_track("raced", status="pending", user=uid)
    assert process(sp("raced"), uid) == "already_in_library"
    assert track_count(sql) == 1 and links(sql, uid) == [winner]


# ---------------------------------------------------------------- endpoints

@pytest.fixture
def spotify(app_module, monkeypatch):
    """Fake Spotify GETs: `.tracks[id]` for /tracks/{id}, `.search` for /search."""
    class State:
        tracks, search = {}, []

    def fake_get(url, token, params=None):
        if url.endswith("/search"):
            return {"tracks": {"items": State.search}}
        return State.tracks[url.rsplit("/", 1)[-1]]
    monkeypatch.setattr(app_module.splib, "_get", fake_get)
    return State


def test_ingest_single_returns_the_reused_row(client, sql, make_user, add_track, spotify):
    uid, h = make_user()
    album = add_track("album-id", isrc="X1")
    spotify.tracks["single-id"] = sp("single-id", isrc="X1")
    r = client.post("/api/ingest/single", json={"spotify_id": "single-id", "access_token": "t"},
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "added_to_library"
    assert r.json()["track"]["spotify_id"] == "album-id"
    assert links(sql, uid) == [album]


def test_search_marks_another_release_in_library(client, make_user, add_track, spotify):
    uid, h = make_user()
    add_track("album-id", isrc="X1", user=uid)
    spotify.search = [sp("single-id", isrc="X1"), sp("unrelated", isrc="Z9")]
    r = client.get("/api/spotify/search", params={"q": "song"},
                   headers={**h, "X-Spotify-Authorization": "Bearer t"})
    assert r.status_code == 200, r.text
    flags = {t["spotify_id"]: t["in_library"] for t in r.json()["tracks"]}
    assert flags == {"single-id": True, "unrelated": False}
