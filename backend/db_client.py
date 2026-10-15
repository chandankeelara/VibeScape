"""
Database connection factory that transparently switches between local
SQLite (default) and Turso/libSQL based on the DB_BACKEND env var.

The returned connection object exposes the same surface that
`backend/app.py` and `backend/db.py` rely on today:

    conn.execute(sql, params) -> Cursor        # positional params only
    conn.execute_batch([(sql, params), ...])   # one transaction, see below
    conn.executescript(sql)
    conn.commit()
    conn.rollback()
    conn.close()
    conn.row_factory  (readable/writable; sqlite3.Row semantics)

execute_batch() is the only way to make several writes atomic on Turso,
where every execute() is its own stream and autocommits. On SQLite it is
the same statements in one transaction. On Turso it is also ONE HTTPS
round trip for the whole list instead of one per statement.

Turso errors are raised as the sqlite3 exceptions sqlite3 itself would
raise: a constraint violation is sqlite3.IntegrityError, anything else
sqlite3.OperationalError.

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
import binascii
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


_URLSAFE_TO_STANDARD = str.maketrans("-_", "+/")


def _decode_blob(text) -> bytes:
    """Decode the base64 of a Hrana blob Value, padded or not.

    Measured on production by a read-only probe on 2026-10-15, one row of
    each: a mert_embedding (LENGTH() 3072) arrives as 4096 base64 chars,
    len % 4 == 0; a fused_embedding (LENGTH() 3152) as 4203 chars,
    len % 4 == 3. Both use the standard alphabet ('+' and '/', no '-' or
    '_') and carry no '='. Plain base64.b64decode returns the 3072 bytes but
    raises "Incorrect padding" on the fused one, where
    b64decode(s + "=" * (-len(s) % 4)) returns 3152 bytes (so does
    urlsafe_b64decode with the same padding). The reader before this, a
    bare `except` around plain b64decode, returned 3072 bytes and 0 bytes.
    So only blobs whose length is not a multiple of 3 broke: every fused
    vector, the empty read backend/app.py works around with
    vector_extract(), and never the MERT vector. libsql's source agrees
    (STANDARD_NO_PAD in libsql-hrana/src/proto.rs); the Hrana spec names
    neither padding nor alphabet.

    This is that padded standard decode. It also takes the url-safe
    alphabet, and raises on anything else: an undecodable blob must not
    read as an empty one.
    """
    if not isinstance(text, str):
        raise sqlite3.DataError(f"Turso blob without base64 text: {text!r:.80}")
    s = text.translate(_URLSAFE_TO_STANDARD)
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except binascii.Error as e:
        raise sqlite3.DataError(
            f"Turso blob is not base64 ({len(text)} chars): {e}"
        ) from e


def _hrana_error(err, where: str = "") -> sqlite3.Error:
    """The sqlite3 exception sqlite3 itself would raise for a Hrana Error.

    libsql's server reports every constraint violation with the code
    "SQLITE_CONSTRAINT" (sqlite_error_code in libsql-server/src/hrana/stmt.rs,
    read 2026-10-15). The prefix match also takes extended codes such as
    SQLITE_CONSTRAINT_UNIQUE, should a server send them. Before this every
    Turso error was an OperationalError, so each `except
    sqlite3.IntegrityError` in the codebase was dead in production.
    """
    err = err or {}
    code = str(err.get("code") or "UNKNOWN")
    message = err.get("message", "?")
    cls = (sqlite3.IntegrityError if code.startswith("SQLITE_CONSTRAINT")
           else sqlite3.OperationalError)
    exc = cls(f"Turso error [{code}]{where}: {message}")
    # The error without its position, so two failures can be compared.
    exc.detail = f"[{code}] {message}"
    return exc


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
        # Unpadded standard base64 from libsql; _decode_blob has the
        # production measurement (2026-10-15).
        return _decode_blob(cell.get("base64"))
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
        # -1 only when the server sent no count. It used to be `... or -1`,
        # which also turned a real 0 ("matched nothing") into -1.
        try:
            self.rowcount = int(response.get("affected_row_count"))
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

    def _post(self, requests_list, timeout: float = 30.0):
        r = self._session.post(
            self._pipeline_url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            data=json.dumps({"baton": None, "requests": requests_list}),
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _stmt(sql: str, params) -> dict:
        stmt = {"sql": sql}
        args = list(params) if params else []
        if args:
            stmt["args"] = [_hrana_arg(p) for p in args]
        return stmt

    @staticmethod
    def _first_result(payload) -> dict:
        results = payload.get("results") or []
        if not results:
            raise sqlite3.OperationalError("Turso pipeline returned no results")
        result = results[0]
        if result.get("type") == "error":
            raise _hrana_error(result.get("error"))
        return ((result.get("response") or {}).get("result")) or {}

    def execute(self, sql: str, params=()):
        # DiskANN index builds on CREATE INDEX ... libsql_vector_idx() can
        # take several minutes on large tables — bump timeout for that path.
        _timeout = 300.0 if "libsql_vector_idx" in sql else 30.0
        payload = self._post([
            {"type": "execute", "stmt": self._stmt(sql, params)},
            {"type": "close"},
        ], timeout=_timeout)
        return _HttpCursor(self._first_result(payload))

    def execute_batch(self, stmts):
        """Run [(sql, params), ...] as ONE transaction in ONE request.

        One pipeline POST carrying a Hrana `batch`, then `close`:

            step 0        BEGIN
            steps 1..n    statement i, only if step i-1 was ok
            step n+1      COMMIT, only if step n was ok
            step n+2      ROLLBACK, only if COMMIT was not ok

        The conditions are what make it atomic. A server "always executes
        all requests, even if some of them return errors" (Hrana 3 spec,
        HTTP pipeline), and a batch step whose condition is false is
        skipped, so the first failure skips everything after it, COMMIT
        included, and the ROLLBACK runs. It is the shape @libsql/client
        builds for a transactional batch (executeHranaBatch,
        libsql-client-ts/packages/libsql-client/src/hrana.ts, read
        2026-10-15), minus its Hrana-3-only `is_autocommit` guard: this
        adapter speaks /v2/pipeline.

        Raises the first failing step's error, mapped like execute()'s and
        naming the step, after which nothing in the batch has been written.
        The error's `step` is the 0-based index into `stmts` of the
        statement that failed, or None when the failure is not one of them
        (BEGIN, COMMIT), so a caller can tell which row broke the batch.
        Returns one cursor per statement.
        """
        stmts = list(stmts)
        if not stmts:
            return []
        n = len(stmts)
        commit_step = n + 1
        steps = [{"stmt": {"sql": "BEGIN"}}]
        for i, (sql, params) in enumerate(stmts):
            steps.append({"condition": {"type": "ok", "step": i},
                          "stmt": self._stmt(sql, params)})
        steps.append({"condition": {"type": "ok", "step": n},
                      "stmt": {"sql": "COMMIT"}})
        steps.append({"condition": {"type": "not",
                                    "cond": {"type": "ok", "step": commit_step}},
                      "stmt": {"sql": "ROLLBACK"}})
        payload = self._post([
            {"type": "batch", "batch": {"steps": steps}},
            {"type": "close"},
        ])
        result = self._first_result(payload)
        step_results = result.get("step_results") or []
        step_errors = result.get("step_errors") or []
        # The first error is the cause: every later step was skipped or is
        # the ROLLBACK cleaning up after it.
        for i, err in enumerate(step_errors):
            if err:
                if 1 <= i <= n:
                    exc = _hrana_error(err, f" in batch step {i} (statement {i} of {n})")
                    exc.step = i - 1
                else:
                    exc = _hrana_error(err, f" in batch step {i} ({steps[i]['stmt']['sql']})")
                    exc.step = None
                raise exc
        if len(step_results) <= commit_step or step_results[commit_step] is None:
            raise sqlite3.OperationalError(
                "Turso batch did not commit, and no step reported an error")
        return [_HttpCursor(step_results[i] or {}) for i in range(1, n + 1)]

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
        """Nothing to commit: every execute() is its own Hrana stream.

        execute() opens a stream (baton None) and closes it in the same
        request, so each statement autocommits. BEGIN/COMMIT sent through
        execute() cannot group statements either: the BEGIN's stream is gone
        before the next statement arrives. Atomic multi-statement writes go
        through execute_batch().
        """
        return None

    def rollback(self):
        """Nothing to roll back, for the reason commit() gives.

        This used to send ROLLBACK through execute(): a fresh stream with no
        transaction, an error every time, swallowed. Same outcome, minus the
        round trip.
        """
        return None

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


# ---------------- SQLite ----------------

class _SqliteConnection(sqlite3.Connection):
    """sqlite3.Connection plus execute_batch(), so callers can use one
    atomic-write call whichever backend they were handed."""

    def execute_batch(self, stmts):
        """Run [(sql, params), ...] in one transaction and commit it.

        On any failure everything in the batch is rolled back and the error
        re-raised, with `step` set to the failing statement's 0-based index
        as the Turso path does. The explicit BEGIN fails if the connection
        already holds uncommitted writes, rather than committing or
        discarding them along with the batch. Returns one cursor per
        statement.
        """
        stmts = list(stmts)
        if not stmts:
            return []
        self.execute("BEGIN")
        try:
            cursors = []
            for step, (sql, params) in enumerate(stmts):
                try:
                    cursors.append(self.execute(sql, params))
                except sqlite3.Error as e:
                    e.step = step
                    raise
        except BaseException:
            self.rollback()
            raise
        self.commit()
        return cursors


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
        conn = sqlite3.connect(str(db_path), check_same_thread=False,
                               factory=_SqliteConnection)
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
