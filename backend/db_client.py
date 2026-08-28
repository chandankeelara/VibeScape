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
sqlite backend and a lookalike shim for the libSQL backend that supports:

  * integer indexing (row[0])
  * string indexing  (row["col"])
  * .keys()          (list of column names)
  * "col" in row.keys()
  * iteration        (yields values, matching sqlite3.Row)

Env vars:
  DB_BACKEND         "sqlite" (default) | "turso" | "libsql"
  TURSO_DATABASE_URL required when backend is turso/libsql
  TURSO_AUTH_TOKEN   required when backend is turso/libsql
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _ROOT / "data" / "vibescape.db"


def _load_libsql():
    """Lazy import so `import db_client` never fails on missing dep."""
    try:
        import libsql_client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "DB_BACKEND=turso requires the `libsql-client` package. "
            "Install with `pip install libsql-client>=0.3.1`."
        ) from e
    return libsql_client


class _LibsqlRow:
    """sqlite3.Row-compatible view over one libsql result row."""

    __slots__ = ("_cols", "_values")

    def __init__(self, columns, values):
        self._cols = columns
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        # string lookup, case-sensitive to match sqlite3.Row default
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
        # sqlite3.Row's `in` checks values, but callers here use row.keys();
        # match sqlite3.Row: iteration is over values, so `in` checks values.
        return key in self._values


class _LibsqlCursor:
    """Cursor-like object returned by _LibsqlConnection.execute()."""

    __slots__ = ("_result", "_pos", "lastrowid", "rowcount")

    def __init__(self, result):
        self._result = result
        self._pos = 0
        # libsql ResultSet exposes last_insert_rowid / rows_affected
        self.lastrowid = getattr(result, "last_insert_rowid", None)
        self.rowcount = getattr(result, "rows_affected", -1)

    def _cols(self):
        return list(getattr(self._result, "columns", []) or [])

    def fetchone(self):
        rows = getattr(self._result, "rows", []) or []
        if self._pos >= len(rows):
            return None
        row = rows[self._pos]
        self._pos += 1
        return _LibsqlRow(self._cols(), list(row))

    def fetchall(self):
        rows = getattr(self._result, "rows", []) or []
        cols = self._cols()
        out = [_LibsqlRow(cols, list(r)) for r in rows[self._pos:]]
        self._pos = len(rows)
        return out

    def __iter__(self):
        while True:
            r = self.fetchone()
            if r is None:
                return
            yield r


class _LibsqlConnection:
    """
    Thin adapter over libsql_client.Client that mimics the subset of the
    sqlite3.Connection API used by VibeScape.
    """

    def __init__(self, client):
        self._client = client
        # row_factory is read/written by db.py; libsql always returns rows
        # in _LibsqlRow shape, so we accept the attribute but ignore it.
        self.row_factory = sqlite3.Row

    def execute(self, sql: str, params=()):
        # libsql expects a list of positional params
        if params is None:
            params = ()
        elif isinstance(params, tuple):
            params = list(params)
        result = self._client.execute(sql, params)
        return _LibsqlCursor(result)

    def executescript(self, script: str):
        # Split on `;` boundaries. libsql-client sync API has no direct
        # equivalent to sqlite3.executescript, so we execute statements
        # one by one. This matches how sqlite3.executescript behaves for
        # our schema.sql / migration scripts (no embedded semicolons in
        # string literals).
        for stmt in _split_sql_statements(script):
            if stmt.strip():
                self._client.execute(stmt)
        return _LibsqlCursor(_EmptyResult())

    def commit(self):
        # libsql-client autocommits per statement; explicit BEGIN/COMMIT
        # travels through .execute() already. This is a no-op to match the
        # sqlite3.Connection.commit() surface expected by callers.
        return None

    def rollback(self):
        # Best-effort: attempt ROLLBACK. Ignored if no txn is active.
        try:
            self._client.execute("ROLLBACK")
        except Exception:
            pass

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


class _EmptyResult:
    columns = []
    rows = []
    last_insert_rowid = None
    rows_affected = 0


def _split_sql_statements(script: str):
    """Naive `;`-splitter. Adequate for our schema.sql — no triggers,
    no string literals containing `;`, no `BEGIN...END` blocks."""
    return [s for s in script.split(";")]


def _resolve_db_path() -> Path:
    p = os.environ.get("VIBESCAPE_DB_PATH") or os.environ.get("DB_PATH")
    return Path(p) if p else _DEFAULT_DB_PATH


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
        url = os.environ.get("TURSO_DATABASE_URL", "").strip()
        token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
        if not url:
            raise RuntimeError(
                "DB_BACKEND=turso but TURSO_DATABASE_URL is not set."
            )
        if not token:
            raise RuntimeError(
                "DB_BACKEND=turso but TURSO_AUTH_TOKEN is not set."
            )
        libsql_client = _load_libsql()
        client = libsql_client.create_client_sync(url=url, auth_token=token)
        return _LibsqlConnection(client)

    raise RuntimeError(
        f"Unknown DB_BACKEND={backend!r}. Expected 'sqlite' or 'turso'."
    )
