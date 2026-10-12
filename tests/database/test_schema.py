"""schema.sql, the local bootstrap, and the Turso one-shot scripts' parsing."""
import importlib
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCHEMA = (REPO / "schema.sql").read_text(encoding="utf-8")


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_schema_applies_to_an_empty_db_and_is_idempotent():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.executescript(SCHEMA)          # every CREATE is IF NOT EXISTS
    assert {"tracks", "track_events", "user_events", "user_track_stats",
            "user_embedding_mean", "user_stats"} <= tables(c)


def split_like_the_turso_scripts(text):
    """The exact parsing scripts/_turso_*.py use: split on ';', drop comment
    lines, slice from the first CREATE."""
    out = []
    for chunk in text.split(";"):
        body = "\n".join(ln for ln in chunk.splitlines() if not ln.strip().startswith("--")).strip()
        head = body.upper().find("CREATE")
        if head >= 0:
            out.append(body[head:])
    return out


def test_no_semicolon_in_a_comment_breaks_the_turso_split():
    """Commit 322c7c1: a ';' inside a comment inside a CREATE cut the
    statement in half for every script that splits schema.sql. Executing the
    split statements one by one must build the same tables as executescript."""
    whole = sqlite3.connect(":memory:")
    whole.executescript(SCHEMA)
    piecewise = sqlite3.connect(":memory:")
    for stmt in split_like_the_turso_scripts(SCHEMA):
        try:
            piecewise.execute(stmt)
        except sqlite3.OperationalError as e:
            pytest.fail(f"split statement does not run: {e}\n---\n{stmt[:300]}")
    assert tables(piecewise) == tables(whole)


def test_turso_script_extractors_find_their_statements():
    m = importlib.import_module("_turso_create_user_embedding_mean")
    assert "user_embedding_mean" in m._statement()
    v2 = importlib.import_module("_turso_migrate_telemetry_v2")
    stmts = v2._create_statements()
    assert len(stmts) == 2 and any("CREATE TABLE IF NOT EXISTS user_events" in s for s in stmts)
    ev = importlib.import_module("_turso_create_event_tables")
    assert len(ev._statements()) == 5


def test_turso_scripts_refuse_to_run_without_turso(monkeypatch, capsys):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    for name in ("_turso_create_user_embedding_mean", "_turso_migrate_telemetry_v2"):
        m = importlib.import_module(name)
        monkeypatch.setattr("sys.argv", [name])
        assert m.main() == 1
    assert "refusing" in capsys.readouterr().out


# ------------------------------------------------------------- local bootstrap

def test_fresh_bootstrap_survives_many_connections(db, app_module):
    """Regression, 2026-10-10. On main a fresh DB died on the first
    connection (pin_hash, backlog 1.7); with that fixed it died on the third
    (the ladder added tracks.user_id, went legacy, and dropped `language`)."""
    for _ in range(5):
        app_module.get_conn().close()


def test_fresh_bootstrap_keeps_the_schema_sql_shape(db):
    c = sqlite3.connect(db)
    ref = sqlite3.connect(":memory:")
    ref.executescript(SCHEMA)
    for t in ("tracks", "track_events", "user_track_stats", "user_events"):
        assert set(columns(ref, t)) <= set(columns(c, t)), t
    assert "user_id" not in columns(c, "tracks")     # modern shape, as production


def test_old_telemetry_tables_are_widened_in_place(tmp_path, monkeypatch, app_module):
    """A DB whose telemetry tables predate v2 gets the new columns, keeps its rows."""
    path = tmp_path / "old.db"
    # Pre-v2 schema: today's schema.sql with the v2 column lines removed, so
    # the "old" tables are real, not a hand-written approximation.
    from db import TRACK_EVENTS_ADDED_COLUMNS, USER_TRACK_STATS_ADDED_COLUMNS
    v2 = {c for c, _ in TRACK_EVENTS_ADDED_COLUMNS[2:]} | {c for c, _ in USER_TRACK_STATS_ADDED_COLUMNS}
    kept = [ln for ln in SCHEMA.splitlines()
            if not (ln.lstrip() != ln and ln.strip().split(" ")[0] in v2)]
    old_schema = "\n".join(kept).replace(
        "    server_ts   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n",   # now the last column
        "    server_ts   TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n")
    c = sqlite3.connect(path)
    c.executescript(old_schema)
    assert "listened_ms" not in columns(c, "track_events")
    assert "total_listened_ms" not in columns(c, "user_track_stats")
    c.executescript("""
        INSERT INTO track_events (user_id, track_id, type) VALUES (1, 1, 'play_start');
        INSERT INTO user_track_stats (user_id, track_id, play_count) VALUES (1, 1, 7);
    """)
    c.commit()
    c.close()
    monkeypatch.setenv("VIBESCAPE_DB_PATH", str(path))
    app_module.get_conn().close()
    c = sqlite3.connect(path)
    for col in ("listened_ms", "end_trigger", "playback", "session_id", "tz_offset_min", "data"):
        assert col in columns(c, "track_events"), col
    assert c.execute("SELECT COUNT(*) FROM track_events").fetchone()[0] == 1
    assert c.execute("SELECT play_count, total_listened_ms FROM user_track_stats").fetchone() == (7, 0)
