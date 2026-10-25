"""_process_track: one catalogue row per recording, one user link per song.

Real database (the test's SQLite file, schema.sql's indexes included);
only Spotify is faked, for the two endpoints at the end.
"""
import sqlite3

import pytest
import db as backend_db  # backend/db.py; `db` is the fixture with the test database path
from services import library_ingest  # backend/services/library_ingest.py
import spotify_library as splib  # ingest/


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
        conn = backend_db.get_conn()
        try:
            return library_ingest._process_track(conn, track, "no-job", user_id, source="search")
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


def test_a_user_with_any_release_gets_no_second_link(sql, make_user, add_track, process):
    """The exact spotify_id row exists, but the user already has another
    release of it: that counts, and the exact row is not linked too."""
    uid, _ = make_user()
    add_track("exact-id", isrc="X1")
    theirs = add_track("other-release", isrc="X1", user=uid)
    assert process(sp("exact-id", isrc="X1"), uid) == "already_in_library"
    assert links(sql, uid) == [theirs]


def test_adding_the_same_song_twice_is_one_row(sql, make_user, process):
    uid, _ = make_user()
    assert process(sp("song", isrc="X1"), uid) == "queued_for_analysis"
    assert process(sp("song", isrc="X1"), uid) == "already_in_library"
    assert track_count(sql) == 1 and len(links(sql, uid)) == 1


# ------------------------------------------------------- one request per track

class CountingConn:
    """Records every statement sent through execute() and execute_batch()."""

    def __init__(self, conn):
        self._conn, self.executes, self.batches = conn, [], []

    def execute(self, sql, params=()):
        self.executes.append(sql)
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
        conn = CountingConn(backend_db.get_conn())
        try:
            return library_ingest._process_track(conn, track, "no-job", user_id), conn
        finally:
            conn.close()
    return _run


def test_every_case_is_one_batch_and_nothing_else(sql, make_user, add_track, counted):
    """Check, insert, link and read back are one transaction (backlog 2.2:
    on Turso each execute() autocommits, and a crash between the INSERT
    and the link used to orphan the row for good)."""
    uid, _ = make_user()
    add_track("known", isrc="X1")
    cases = [(sp("known"), "added_to_library"),
             (sp("other-release", isrc="X1"), "already_in_library"),
             (sp("brand-new", isrc="Y1"), "queued_for_analysis"),
             (sp("brand-new", isrc="Y1"), "already_in_library")]
    for track, bucket in cases:
        result, conn = counted(track, uid)
        assert result == bucket
        assert not conn.executes, conn.executes
        assert len(conn.batches) == 1 and len(conn.batches[0]) == 3
    assert track_count(sql) == 2


def test_a_failed_link_leaves_no_row(sql, make_user, process):
    uid, _ = make_user()
    sql.execute("CREATE TRIGGER no_links BEFORE INSERT ON user_tracks "
                "BEGIN SELECT RAISE(ABORT, 'link refused'); END")
    sql.commit()
    with pytest.raises(sqlite3.Error, match="link refused"):
        process(sp("brand-new"), uid)
    assert track_count(sql) == 0


# ------------------------------------------------------------------- the race

def test_a_double_submit_inserts_once_with_no_unique_index(sql, make_user, app_module):
    """Production's tracks has no unique index (1.13), so only the batch's
    own transaction can stop a double submit: tracks 6465 and 6466 on prod.
    The other request holds the write lock with its row uncommitted; ours
    must wait for it and then see that row, not check before it commits."""
    import threading
    import time
    uid, _ = make_user()
    conn = backend_db.get_conn()            # runs ensure_db(), which makes the index
    sql.execute("DROP INDEX idx_tracks_spotify_id")
    sql.commit()
    sql.execute("INSERT INTO tracks (title, artist, spotify_id, isrc, ingestion_status, vibe_score) "
                "VALUES ('Song', 'Artist', 'raced', 'X1', 'pending', 0)")   # uncommitted
    out = {}

    def ours():
        try:
            out["result"] = library_ingest._process_track(conn, sp("raced", isrc="X1"), "no-job", uid)
        except Exception as e:  # surfaced by the asserts below
            out["error"] = e
    t = threading.Thread(target=ours)
    t.start()
    time.sleep(1.0)                         # ours is now waiting on the lock
    sql.commit()
    t.join(30)
    conn.close()
    assert "error" not in out, out
    assert out["result"] == "queued_for_analysis"
    assert track_count(sql) == 1
    winner = sql.execute("SELECT id FROM tracks").fetchone()[0]
    assert links(sql, uid) == [winner]


class CollidesOnce:
    """The first execute_batch loses to another connection on the unique
    index: that connection's row lands and IntegrityError is raised, as
    SQLite would."""

    def __init__(self, conn, db_path, user_id=None):
        self._conn, self._db, self._user, self.batches = conn, db_path, user_id, 0

    def execute_batch(self, stmts):
        self.batches += 1
        if self.batches == 1:
            other = sqlite3.connect(self._db)
            cur = other.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, "
                                "vibe_score) VALUES ('Song', 'Artist', 'raced', 'pending', 0)")
            if self._user is not None:
                other.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)",
                              (self._user, cur.lastrowid))
            other.commit()
            other.close()
            raise sqlite3.IntegrityError("UNIQUE constraint failed: tracks.spotify_id")
        return self._conn.execute_batch(stmts)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.mark.parametrize("same_user, bucket", [(False, "queued_for_analysis"),
                                               (True, "already_in_library")])
def test_losing_on_the_unique_index_retries_onto_the_winner(sql, make_user, app_module, db,
                                                            same_user, bucket):
    uid, _ = make_user()
    conn = CollidesOnce(backend_db.get_conn(), db, uid if same_user else None)
    try:
        assert library_ingest._process_track(conn, sp("raced"), "no-job", uid) == bucket
    finally:
        conn.close()
    assert conn.batches == 2 and track_count(sql) == 1
    assert links(sql, uid) == [sql.execute("SELECT id FROM tracks").fetchone()[0]]


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
    monkeypatch.setattr(splib, "_get", fake_get)
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
