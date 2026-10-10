"""Shared fixtures for the Python suites (tests/backend, tests/database).

Every test gets its OWN empty SQLite file under pytest's tmp_path, built from
schema.sql by the app's normal bootstrap. Nothing here can reach the
developer's data/vibescape.db or production Turso:

  * DB_BACKEND is forced to sqlite and the Turso variables are removed
    BEFORE the backend is imported.
  * VIBESCAPE_DB_PATH (the only thing db_client reads, see backend/CLAUDE.md)
    is pointed at the per-test file, and `db` asserts the app really opened it.
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

FUSED_DIM = 788


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
                sql.execute("INSERT INTO track_embeddings (track_id, fused_embedding) VALUES (?, ?)",
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
