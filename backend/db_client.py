"""
Database connection factory that transparently switches between local
SQLite (default) and Turso/libSQL based on the DB_BACKEND env var.

The returned connection object exposes the same surface that
`backend/app.py` and `backend/db.py` rely on today:

    conn.execute(sql, params) -> Cursor        # positional params only
    conn.executescript(sql)
    conn.commit()
    conn.rollback()
    conn.close()
    conn.row_factory  (readable/writable; sqlite3.Row semantics)

Rows returned by fetchone()/fetchall()/iteration are sqlite3.Row for the
sqlite backend and a lookalike shim for the Turso backend that supports:

  * integer indexing (row[0])
  * string indexing  (row["col"])
  * .keys()          (list of column names)
  * iteration        (yields values, matching sqlite3.Row)

The Turso backend talks to the remote libSQL instance over Turso's
HTTP Hrana v2 pipeline API using only the `requests` library. We
deliberately do NOT use `libsql-client` — its 0.3.x releases crash
with KeyError('result') whenever Turso returns an error response,
which makes it unusable for a production API.

Env vars:
  DB_BACKEND         "sqlite" (default) | "turso" | "libsql"
  TURSO_DATABASE_URL required when backend is turso/libsql.
                     Accepts libsql://, https://, or wss:// scheme —
                     always normalized to https:// for the pipeline API.
  TURSO_AUTH_TOKEN   required when backend is turso/libsql
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _ROOT / "data" / "vibescape.db"


# ---------------- Turso HTTP adapter ----------------

def _hrana_arg(v):
    """Encode a Python value as a Hrana v2 Value."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": float(v)}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def _unwrap_cell(cell):
    """Decode a Hrana v2 Value back into a Python primitive."""
    if not isinstance(cell, dict):
        return cell
    t = cell.get("type")
    if t == "null":
        return None
    v = cell.get("value")
    if v is None and t != "blob":
        return None
    if t == "integer":
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    if t == "float":
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    if t == "blob":
        try:
            return base64.b64decode(cell.get("base64") or "")
        except Exception:
            return b""
    return v  # text


class _HttpRow:
    """sqlite3.Row-compatible view over one Hrana result row."""

    __slots__ = ("_cols", "_values")

    def __init__(self, cols, values):
        self._cols = cols
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        try:
            idx = self._cols.index(key)
        except ValueError as e:
            raise IndexError(f"No item with key {key!r}") from e
        return self._values[idx]

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __contains__(self, key):
        return key in self._values


class _HttpCursor:
    __slots__ = ("_cols", "_rows", "_pos", "lastrowid", "rowcount")

    def __init__(self, response):
        self._cols = [c.get("name") for c in (response.get("cols") or [])]
        self._rows = response.get("rows") or []
        self._pos = 0
        rid = response.get("last_insert_rowid")
        if isinstance(rid, str):
            try:
                rid = int(rid)
            except ValueError:
                rid = None
        self.lastrowid = rid
        try:
            self.rowcount = int(response.get("affected_row_count", -1) or -1)
        except (TypeError, ValueError):
            self.rowcount = -1

    def _row(self, cells):
        return _HttpRow(self._cols, [_unwrap_cell(c) for c in cells])

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        r = self._rows[self._pos]
        self._pos += 1
        return self._row(r)

    def fetchall(self):
        out = [self._row(r) for r in self._rows[self._pos:]]
        self._pos = len(self._rows)
        return out

    def __iter__(self):
        while True:
            r = self.fetchone()
            if r is None:
                return
            yield r


class _HttpConnection:
    """
    Thin adapter that mimics the sqlite3.Connection API but talks to a
    Turso libSQL instance over the /v2/pipeline HTTP endpoint.
    """

    def __init__(self, base_url: str, token: str):
        # Lazy import — keeps `import db_client` cheap when Turso isn't used.
        import requests
        self._session = requests.Session()
        self._pipeline_url = base_url.rstrip("/") + "/v2/pipeline"
        self._token = token
        self.row_factory = sqlite3.Row  # for API compat; unused

    def _post(self, requests_list):
        r = self._session.post(
            self._pipeline_url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            data=json.dumps({"baton": None, "requests": requests_list}),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def execute(self, sql: str, params=()):
        if params is None:
            params = ()
        stmt = {"sql": sql}
        args = list(params) if params else []
        if args:
            stmt["args"] = [_hrana_arg(p) for p in args]
        payload = self._post([
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ])
        results = payload.get("results") or []
        if not results:
            raise sqlite3.OperationalError("Turso pipeline returned no results")
        result = results[0]
        if result.get("type") == "error":
            err = result.get("error") or {}
            raise sqlite3.OperationalError(
                f"Turso error [{err.get('code', 'UNKNOWN')}]: {err.get('message', '?')}"
            )
        response = ((result.get("response") or {}).get("result")) or {}
        return _HttpCursor(response)

    def executescript(self, script: str):
        # Turso rejects multi-statement bodies in a single execute — split
        # on `;` and issue one at a time. Safe for schema.sql and the
        # migration SQL in db.py (no triggers, no `;` in string literals).
        for stmt in script.split(";"):
            s = stmt.strip()
            if s:
                self.execute(s)
        return None

    def commit(self):
        # Turso HTTP pipeline autocommits each request; explicit
        # BEGIN/COMMIT/ROLLBACK travels through .execute() already.
        return None

    def rollback(self):
        try:
            self.execute("ROLLBACK")
        except Exception:
            pass

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


# ---------------- factory ----------------

def _resolve_db_path() -> Path:
    p = os.environ.get("VIBESCAPE_DB_PATH") or os.environ.get("DB_PATH")
    return Path(p) if p else _DEFAULT_DB_PATH


def _normalize_turso_url(url: str) -> str:
    """Turso accepts libsql://, https://, wss:// — HTTP pipeline needs https://."""
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    if url.startswith("ws://"):
        return "http://" + url[len("ws://"):]
    return url


def create_connection():
    """
    Return a Connection whose behavior matches the sqlite3.Connection
    surface used across backend/. Backend chosen by DB_BACKEND env var.
    """
    backend = (os.environ.get("DB_BACKEND") or "sqlite").strip().lower()

    if backend in ("", "sqlite", "sqlite3"):
        db_path = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    if backend in ("turso", "libsql"):
        url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
        token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
        if not url:
            raise RuntimeError(
                "DB_BACKEND=turso but TURSO_DATABASE_URL is not set."
            )
        if not token:
            raise RuntimeError(
                "DB_BACKEND=turso but TURSO_AUTH_TOKEN is not set."
            )
        return _HttpConnection(_normalize_turso_url(url), token)

    raise RuntimeError(
        f"Unknown DB_BACKEND={backend!r}. Expected 'sqlite' or 'turso'."
    )
