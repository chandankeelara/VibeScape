"""scripts/_dedupe_tracks.py and scripts/_turso_create_track_indexes.py on a
production-shaped SQLite file: schema.sql's tables with the tracks indexes
dropped, as production has none (backlog 1.13). Local target only; prod is
pointed at a .env.prod that does not exist (conftest)."""
import sqlite3

import pytest

import _dedupe_tracks as dd
import _turso_create_track_indexes as idx
import rebuild_user_track_stats as rebuild
import db as backend_db  # backend/db.py; `db` is the fixture with the test database path

TRACK_INDEXES = ("idx_tracks_spotify_id", "idx_tracks_apple_id", "idx_tracks_isrc",
                 "idx_tracks_ingestion_status")


def R(id, spotify_id=None, isrc=None, apple_id=None, status="done"):
    return {"id": id, "spotify_id": spotify_id or f"sp{id}", "isrc": isrc,
            "apple_id": apple_id, "ingestion_status": status}


# ----------------------------------------------------------------- the plan

def test_isrc_groups_keep_a_finished_row_then_the_lowest_id():
    p = dd.plan([R(1, isrc="X", status="pending"), R(2, isrc="X"), R(3, isrc="X"),
                 R(4, isrc="Y"), R(5, isrc="Y")])
    assert p.groups == [(2, [1, 3]), (4, [5])]


def test_spotify_and_isrc_duplicates_merge_transitively():
    # 1~2 by ISRC, 2~3 by spotify_id: one recording, one group.
    p = dd.plan([R(1, isrc="X"), R(2, spotify_id="dup", isrc="X"), R(3, spotify_id="dup"),
                 R(4, spotify_id="alone")])
    assert p.groups == [(1, [2, 3])]


def test_apple_id_is_not_a_merge_key_and_is_cleared_on_all_but_the_keeper():
    # 1 and 2 are different recordings matched to one iTunes track; 3 merges
    # into 1 by ISRC and goes away, so it needs no clearing.
    p = dd.plan([R(1, isrc="A", apple_id=7), R(2, isrc="B", apple_id=7),
                 R(3, isrc="A", apple_id=7), R(4, isrc="C", apple_id=8, status="pending"),
                 R(5, isrc="D", apple_id=8)])
    assert p.groups == [(1, [3])]
    assert p.clear_apple == [2, 4]


# ------------------------------------------------------------ on a database

@pytest.fixture
def prodlike(db, sql):
    """The test DB without any tracks index, two users, and a helper to add
    rows: add(spotify_id, isrc=None, apple_id=None, status='done') -> id."""
    for name in TRACK_INDEXES:
        sql.execute(f"DROP INDEX IF EXISTS {name}")
    for uid in (1, 2):
        sql.execute("INSERT OR IGNORE INTO users (id, display_name) VALUES (?, ?)", (uid, f"u{uid}"))
    sql.commit()

    def add(spotify_id, isrc=None, apple_id=None, status="done", emb=True):
        cur = sql.execute("INSERT INTO tracks (title, artist, spotify_id, isrc, apple_id, "
                          "ingestion_status, vibe_score) VALUES ('S', 'A', ?, ?, ?, ?, 0)",
                          (spotify_id, isrc, apple_id, status))
        tid = int(cur.lastrowid)
        if emb:
            sql.execute("INSERT INTO track_embeddings (track_id, fused_embedding) VALUES (?, x'00')",
                        (tid,))
        sql.commit()
        return tid
    return add


def link(sql, user, track, added_at):
    sql.execute("INSERT INTO user_tracks (user_id, track_id, added_at, source) VALUES (?, ?, ?, 'x')",
                (user, track, added_at))
    sql.commit()


def play(sql, user, track, ms, reason="completed"):
    """One listen, as POST /api/events stores it, and the stats it implies."""
    sql.execute("INSERT INTO track_events (user_id, track_id, type) VALUES (?, ?, 'play_start')",
                (user, track))
    sql.execute("INSERT INTO track_events (user_id, track_id, type, position_ms, listened_ms, reason) "
                "VALUES (?, ?, 'play_end', ?, ?, ?)", (user, track, ms, ms, reason))
    sql.commit()


def rebuild_stats(sql):
    """The cache as scripts/rebuild_user_track_stats.py would write it."""
    sql.execute("DELETE FROM user_track_stats")
    cols = ", ".join(rebuild._COLS)
    exprs = ", ".join(e for _, e in rebuild._ALL)
    sql.execute(f"INSERT INTO user_track_stats ({cols}) SELECT user_id, track_id, {exprs}, "
                f"CURRENT_TIMESTAMP FROM track_events WHERE type IN ('play_start', 'play_end') "
                f"GROUP BY user_id, track_id")
    sql.commit()


@pytest.fixture
def scene(prodlike, sql):
    """Two duplicate groups and one apple_id collision, with history:
      keeper 1 + loser 2 (ISRC X): user 1 holds both, user 2 only the loser,
      and both users listened to the loser; user 1 to the keeper too.
      keeper 3 + pending loser 4 share a spotify_id (no ISRC); user 2 has 4.
      6 and 7 share apple_id 70 but are different recordings."""
    k1 = prodlike("album", isrc="X")
    l2 = prodlike("single", isrc="X")
    k3 = prodlike("dup", isrc=None)
    l4 = prodlike("dup", isrc=None, status="pending")
    a6 = prodlike("video", isrc="V1", apple_id=70)
    a7 = prodlike("cut", isrc="V2", apple_id=70)
    link(sql, 1, k1, "2026-01-05")
    link(sql, 1, l2, "2026-01-01")
    link(sql, 2, l2, "2026-02-01")
    link(sql, 2, l4, "2026-03-01")
    play(sql, 1, k1, 200_000)
    play(sql, 1, l2, 100_000, "skipped")
    play(sql, 2, l2, 150_000)
    play(sql, 2, l4, 95_000)
    rebuild_stats(sql)
    return dict(k1=k1, l2=l2, k3=k3, l4=l4, a6=a6, a7=a7)


def ids(sql, q, *a):
    return sorted(tuple(r) for r in sql.execute(q, a))


def test_a_merge_keeps_every_link_listen_and_stat(scene, sql):
    s = scene
    assert dd.main(["--apply"]) == 0
    assert ids(sql, "SELECT id FROM tracks") == [(s["k1"],), (s["k3"],), (s["a6"],), (s["a7"],)]
    # user 1 had both rows: one link, the earlier added_at; user 2 had only
    # losers: re-pointed.
    assert ids(sql, "SELECT user_id, track_id, added_at FROM user_tracks") == [
        (1, s["k1"], "2026-01-01"), (2, s["k1"], "2026-02-01"), (2, s["k3"], "2026-03-01")]
    assert ids(sql, "SELECT user_id, track_id, COUNT(*) FROM track_events GROUP BY 1, 2") == [
        (1, s["k1"], 4), (2, s["k1"], 2), (2, s["k3"], 2)]
    # The cache equals a rebuild from the moved events.
    assert rebuild._expected(sql, None).keys() == rebuild._existing(sql, None).keys()
    stats = {(r[0], r[1]): tuple(r[2:]) for r in sql.execute(
        "SELECT user_id, track_id, play_count, end_count FROM user_track_stats")}
    assert stats == {(1, s["k1"]): (2, 2), (2, s["k1"]): (1, 1), (2, s["k3"]): (1, 1)}
    assert ids(sql, "SELECT track_id FROM track_embeddings") == [
        (s["k1"],), (s["k3"],), (s["a6"],), (s["a7"],)]
    assert ids(sql, "SELECT id, apple_id FROM tracks WHERE apple_id IS NOT NULL") == [(s["a6"], 70)]


def test_the_check_finds_nothing_wrong_and_the_unique_indexes_build(scene, sql):
    import db_client
    conn = db_client.create_connection()
    before = dd.snapshot(conn)
    p = dd.plan(conn.execute("SELECT id, spotify_id, apple_id, isrc, ingestion_status FROM tracks"))
    dd.run(conn, p, log=lambda *_: None)
    assert dd.check(conn, before) == []
    conn.close()
    for name in ("idx_tracks_spotify_id", "idx_tracks_apple_id"):
        col = name.split("_", 2)[2]
        sql.execute(f"CREATE UNIQUE INDEX {name} ON tracks({col}) WHERE {col} IS NOT NULL")


def test_stats_on_both_rows_fold_into_one_rebuilt_row(scene, sql):
    """User 1 has stats on keeper and loser: one row after, equal to what
    rebuild_user_track_stats.py derives from the moved events."""
    s = scene
    assert dd.main(["--apply"]) == 0
    want = rebuild._expected(sql, None)
    have = rebuild._existing(sql, None)
    assert want.keys() == have.keys() == {(1, s["k1"]), (2, s["k1"]), (2, s["k3"])}
    for k in want:
        assert all((want[k][c] or 0) == (have[k][c] or 0) for c, _ in rebuild._COUNTERS), k
        assert all((want[k][c] or "") == (have[k][c] or "") for c, _ in rebuild._TIMESTAMPS), k


def test_a_dry_run_writes_nothing(scene, db):
    def dump():
        c = sqlite3.connect(db)
        try:
            return list(c.iterdump())
        finally:
            c.close()
    before = dump()
    assert dd.main([]) == 0
    assert dump() == before


def test_a_group_that_fails_lands_none_of_its_writes(scene, sql):
    """One transaction per group: the failing group keeps its links,
    events and rows exactly; the group before it is merged."""
    s = scene
    sql.execute(f"CREATE TRIGGER no_delete BEFORE DELETE ON tracks WHEN old.id = {s['l4']} "
                f"BEGIN SELECT RAISE(ABORT, 'refused'); END")
    sql.commit()
    with pytest.raises(sqlite3.Error, match="refused"):
        dd.main(["--apply"])
    assert ids(sql, "SELECT id FROM tracks WHERE id IN (?, ?, ?)", s["l2"], s["k3"], s["l4"]) == [
        (s["k3"],), (s["l4"],)]
    assert ids(sql, "SELECT user_id, track_id FROM user_tracks WHERE user_id = 2") == [
        (2, s["k1"]), (2, s["l4"])]
    assert ids(sql, "SELECT track_id FROM track_events WHERE track_id = ?", s["l4"]) == [
        (s["l4"],), (s["l4"],)]


def test_prod_without_its_env_file_exits_before_connecting():
    with pytest.raises(SystemExit, match=r"\.env\.prod"):
        dd.main(["--target", "prod"])
    with pytest.raises(SystemExit, match=r"\.env\.prod"):
        idx.main(["--target", "prod", "--apply"])


# ------------------------------------------------------------ the index script

def tracks_indexes(sql):
    return {r[0] for r in sql.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tracks'")}


def test_the_index_script_reads_its_four_statements_from_schema_sql():
    stmts = idx.statements()
    assert set(stmts) == set(idx.INDEXES)
    assert stmts["idx_tracks_spotify_id"].startswith("CREATE UNIQUE INDEX IF NOT EXISTS")
    assert stmts["idx_tracks_isrc"] == "CREATE INDEX IF NOT EXISTS idx_tracks_isrc ON tracks(isrc)"


def test_the_index_script_refuses_while_duplicates_remain(scene, sql):
    assert idx.main(["--apply"]) == 1
    assert not tracks_indexes(sql) & set(TRACK_INDEXES)


def test_after_the_merge_the_index_script_creates_all_four_once(scene, sql):
    assert dd.main(["--apply"]) == 0
    assert idx.main([]) == 0 and not tracks_indexes(sql) & set(TRACK_INDEXES)   # dry run
    assert idx.main(["--apply"]) == 0
    assert set(TRACK_INDEXES) <= tracks_indexes(sql)
    assert idx.main(["--apply"]) == 0                                           # idempotent
    with pytest.raises(sqlite3.IntegrityError):
        sql.execute("INSERT INTO tracks (title, artist, spotify_id, vibe_score) "
                    "VALUES ('S', 'A', 'album', 0)")


# ------------------------------------------------- a copy of prod, bootstrapped

def test_a_split_db_with_a_dead_user_id_column_survives_bootstrap(prodlike, sql, db, app_module):
    """Production's tracks carries an unused user_id column. Once its
    duplicates are merged and the unique indexes built, the local bootstrap
    took it for the pre-split shape and rebuilt tracks without `language`
    (and every later column), and schema.sql's language index failed."""
    prodlike("a", isrc="X")
    sql.execute("ALTER TABLE tracks ADD COLUMN user_id INTEGER")
    sql.execute("CREATE UNIQUE INDEX idx_tracks_spotify_id ON tracks(spotify_id) "
                "WHERE spotify_id IS NOT NULL")
    sql.commit()
    for _ in range(3):
        backend_db.get_conn().close()
    cols = [r[1] for r in sql.execute("PRAGMA table_info(tracks)")]
    assert "language" in cols and "fuse_status" in cols
    assert [tuple(r) for r in sql.execute("SELECT spotify_id, isrc FROM tracks")] == [("a", "X")]


def test_each_group_is_one_batch(scene, batches, sql):
    """Locally an exception before COMMIT rolls back anyway; on Turso every
    execute() autocommits, so only execute_batch keeps a group whole."""
    s = scene
    assert dd.main(["--apply"]) == 0
    assert [len(b) for b in batches] == [len(dd.group_statements(1, [2]))] * 2 + [1]
    assert any(f"IN ({s['l2']})" in q for q, _ in batches[0])
    assert any(f"IN ({s['l4']})" in q for q, _ in batches[1])
    assert batches[2][0][0].startswith("UPDATE tracks SET apple_id = NULL")
