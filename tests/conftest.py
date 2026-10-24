"""Shared fixtures for the Python suites (tests/backend, tests/database).

Every test gets its OWN empty SQLite file under pytest's tmp_path, built from
schema.sql by the app's normal bootstrap. Nothing here can reach the
developer's data/vibescape.db or production Turso:

  * DB_BACKEND is forced to sqlite and the Turso variables are removed
    BEFORE the backend is imported.
  * VIBESCAPE_DB_PATH (the only thing db_client reads, see backend/CLAUDE.md)
    is pointed at the per-test file, and `db` asserts the app really opened it.
  * `--target prod` (scripts/_target.py) reads .env.prod; every test points
    that at a file that does not exist, so a real one is never loaded.
  * And whatever a test loads, it cannot send: db_client's one way out to
    Turso, _HttpConnection._post, raises in every test unless the test gave
    that connection a fake _post of its own.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]

os.environ["DB_BACKEND"] = "sqlite"
for _k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "DJ_EMBEDDING_VARIANT"):
    os.environ.pop(_k, None)
# Import-time placeholder; every test re-points this before touching the DB.
os.environ["VIBESCAPE_DB_PATH"] = str(REPO / "tests" / ".never-used.db")

sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.append(str(REPO / "ingest"))   # last: ingest/db.py must not shadow backend/db.py


@pytest.fixture(autouse=True)
def _fresh_spotify_guard():
    """The guard is process-wide state: a block recorded by one test must not
    leak into the next."""
    import spotify_guard
    spotify_guard._reset_for_tests()
    yield
    spotify_guard._reset_for_tests()


@pytest.fixture(autouse=True)
def _no_prod_credentials(monkeypatch):
    """A developer's real .env.prod holds the production token. No test may
    load it, even through runner.main(['--target', 'prod'])."""
    import _target
    monkeypatch.setattr(_target, "ENV_PROD", REPO / "tests" / ".never-used.env.prod")


@pytest.fixture(autouse=True)
def _no_turso(monkeypatch):
    """No test may send a request to a real database, however it came by
    credentials. On 2026-10-15 a check of a new test against the previous
    commit ran the old backup_prod_db.main(), which read the token from
    scripts/_load_gcp_secrets.ps1 and may have reached production; nothing
    here stopped it. The adapter tests set `conn._post` on their own
    connection, which shadows this."""
    import db_client

    def refuse(self, requests_list, timeout=30.0):
        raise AssertionError("a test tried to reach Turso; give the connection a fake _post")
    monkeypatch.setattr(db_client._HttpConnection, "_post", refuse)


@pytest.fixture(autouse=True)
def _no_lrclib(monkeypatch):
    """No test may call LRCLIB. backend/lyrics.py's only way out is _http;
    a test that wants answers sets its own (tests/backend/test_lyrics.py)."""
    import lyrics

    def refuse(url, params):
        raise AssertionError(f"a test tried to reach LRCLIB ({url}); give lyrics a fake _http")
    monkeypatch.setattr(lyrics, "_http", refuse)
    monkeypatch.setattr(lyrics, "_sleep", lambda s: None)


@pytest.fixture
def batches(monkeypatch):
    """Every execute_batch() on a db_client SQLite connection (what get_conn()
    returns), recorded as its list of (sql, params) before it runs."""
    import db_client
    calls = []
    real = db_client._SqliteConnection.execute_batch

    def recording(self, stmts):
        stmts = list(stmts)
        calls.append(stmts)
        return real(self, stmts)
    monkeypatch.setattr(db_client._SqliteConnection, "execute_batch", recording)
    return calls


class FlakyReads:
    """A connection whose first `fails` reads (an execute() of a SELECT)
    raise `error` before reaching the database, every one of them if `fails`
    is None; anything else passes through. `reads` counts the attempts."""

    def __init__(self, conn, error=None, fails=2):
        import requests
        self._conn, self._fails = conn, fails
        self._error = error or requests.exceptions.ConnectionError("connection reset")
        self.reads = 0

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT"):
            self.reads += 1
            if self._fails is None or self.reads <= self._fails:
                raise self._error
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def flaky_reads():
    """FlakyReads(conn, error=ConnectionError, fails=2): a connection whose
    reads fail on cue, as a dropped connection or an HTTP 503 would."""
    return FlakyReads


@pytest.fixture
def pauses(monkeypatch):
    """The pauses between read and write retries (base._sleep), recorded
    instead of slept."""
    from ingest_pipeline import base
    recorded = []
    monkeypatch.setattr(base, "_sleep", recorded.append)
    return recorded

from ingest_pipeline.fused_vector import FUSED_COLUMN, FUSED_DIM  # noqa: E402


@pytest.fixture(scope="session")
def app_module():
    import logging
    logging.getLogger("vibescape").setLevel(logging.ERROR)
    import app  # noqa: WPS433 — imported once, after the env above is set
    return app


@pytest.fixture
def db(tmp_path, monkeypatch, app_module):
    """Path of a fresh, bootstrapped database for this test."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("VIBESCAPE_DB_PATH", str(path))
    app_module._dj_mean_cache.clear()
    app_module._search_rows_cache.clear()
    app_module._fused_v2_state.update(ready=None, checked_at=0.0, warned=False)
    app_module._fused_v3_state.update(ready=None, checked_at=0.0, warned=False)
    app_module._genre_scores_state.update(ready=None, checked_at=0.0, warned=False)
    conn = app_module.get_conn()  # runs ensure_db() -> schema.sql + migrations
    opened = conn.execute("PRAGMA database_list").fetchall()[0][2]
    conn.close()
    assert Path(opened).resolve() == path.resolve(), f"app opened {opened}, not the test DB"
    return path


@pytest.fixture
def sql(db):
    """A plain sqlite3 connection to the test DB, for arranging and asserting."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def client(app_module, db):
    from fastapi.testclient import TestClient
    # Not used as a context manager, so the startup hook (LAN logging, audio
    # dir) does not run. get_conn() bootstraps the DB on its own.
    return TestClient(app_module.app)


@pytest.fixture
def make_user(sql, app_module):
    """make_user(name) -> (user_id, auth_headers)."""
    def _make(name=None):
        name = name or f"user-{secrets.token_hex(4)}"
        cur = sql.execute("INSERT INTO users (display_name) VALUES (?)", (name,))
        sql.commit()
        uid = int(cur.lastrowid)
        conn = app_module.get_conn()
        try:
            token = app_module._issue_session(conn, uid)
        finally:
            conn.close()
        return uid, {"Authorization": f"Bearer {token}"}
    return _make


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


@pytest.fixture
def make_tracks(sql):
    """make_tracks(user_id, vectors, artist=..., duration_ms=...) -> [track ids].

    Each vector becomes an analysed ('done') track in the user's library with
    that fused embedding. Pass vector=None for a track with no embedding.
    """
    def _make(user_id, vectors, artist="Artist", duration_ms=200_000, titles=None):
        ids = []
        for i, vec in enumerate(vectors):
            cur = sql.execute(
                # vibe_score: NOT NULL in the legacy local shape (every existing
                # dev DB), nullable in schema.sql — set it so both shapes work.
                "INSERT INTO tracks (title, artist, spotify_id, duration_ms, ingestion_status, vibe_score) "
                "VALUES (?, ?, ?, ?, 'done', 50)",
                ((titles or {}).get(i, f"Track {i}"), artist, secrets.token_hex(11), duration_ms),
            )
            tid = int(cur.lastrowid)
            sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (user_id, tid))
            if vec is not None:
                blob = np.asarray(unit(vec), dtype=np.float32).tobytes()
                sql.execute(f"INSERT INTO track_embeddings (track_id, {FUSED_COLUMN}) VALUES (?, ?)",
                            (tid, blob))
            ids.append(tid)
        sql.commit()
        return ids
    return _make


@pytest.fixture
def clusters():
    """Two well-separated clusters inside one shared cone, like the real
    fused space (everything similar, groups distinguishable after centring)."""
    rng = np.random.default_rng(7)
    base = rng.normal(size=FUSED_DIM) * 3.0          # the shared direction
    ca, cb = rng.normal(size=FUSED_DIM), rng.normal(size=FUSED_DIM)

    def draw(center, n):
        return [unit(base + center + 0.25 * rng.normal(size=FUSED_DIM)) for _ in range(n)]
    return {"A": lambda n: draw(ca, n), "B": lambda n: draw(cb, n)}
