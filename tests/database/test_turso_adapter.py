"""backend/db_client.py's Turso adapter, with no network.

`_post` is replaced by FakeHrana, which runs each pipeline request on an
in-memory SQLite database the way the Hrana spec describes a server doing
it: every request in a pipeline runs, a batch step whose condition is false
is skipped, errors come back as values, and blobs go out as standard base64
WITHOUT padding, like libsql's server (libsql-hrana/src/proto.rs,
bytes_as_base64).
"""
import base64
import sqlite3

import numpy as np
import pytest

import db_client


def _arg(v):
    t = v["type"]
    if t == "null":
        return None
    if t == "integer":
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "blob":
        s = v["base64"].rstrip("=")   # the server trims padding, then decodes
        return base64.b64decode(s + "=" * (-len(s) % 4))
    return v["value"]


def _value(x):
    if x is None:
        return {"type": "null"}
    if isinstance(x, int):
        return {"type": "integer", "value": str(x)}
    if isinstance(x, float):
        return {"type": "float", "value": x}
    if isinstance(x, bytes):
        return {"type": "blob", "base64": base64.b64encode(x).decode().rstrip("=")}
    return {"type": "text", "value": str(x)}


class FakeHrana:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.requests = []          # one entry per POST: its `requests` list

    def post(self, requests_list, timeout=30.0):
        self.requests.append(requests_list)
        results = []
        for req in requests_list:
            if req["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
            elif req["type"] == "execute":
                res, err = self._run(req["stmt"])
                results.append({"type": "error", "error": err} if err else
                               {"type": "ok", "response": {"type": "execute", "result": res}})
            elif req["type"] == "batch":
                results.append({"type": "ok", "response": {
                    "type": "batch", "result": self._batch(req["batch"]["steps"])}})
        return {"baton": None, "base_url": None, "results": results}

    def _run(self, stmt):
        try:
            cur = self.db.execute(stmt["sql"], [_arg(a) for a in stmt.get("args", [])])
            rows = cur.fetchall()
        except sqlite3.IntegrityError as e:
            return None, {"message": str(e), "code": "SQLITE_CONSTRAINT"}
        except sqlite3.Error as e:
            return None, {"message": str(e), "code": "SQLITE_ERROR"}
        return {"cols": [{"name": d[0]} for d in cur.description or []],
                "rows": [[_value(x) for x in r] for r in rows],
                "affected_row_count": max(cur.rowcount, 0),
                "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None}, None

    def _cond(self, c, results, errors):
        t = c["type"]
        if t == "ok":
            return results[c["step"]] is not None
        if t == "error":
            return errors[c["step"]] is not None
        if t == "not":
            return not self._cond(c["cond"], results, errors)
        if t == "and":
            return all(self._cond(x, results, errors) for x in c["conds"])
        if t == "or":
            return any(self._cond(x, results, errors) for x in c["conds"])
        raise AssertionError(f"condition {t!r} is not in Hrana 2")

    def _batch(self, steps):
        results, errors = [], []
        for step in steps:
            cond = step.get("condition")
            if cond is not None and not self._cond(cond, results, errors):
                results.append(None)
                errors.append(None)
                continue
            res, err = self._run(step["stmt"])
            results.append(res)
            errors.append(err)
        return {"step_results": results, "step_errors": errors}


@pytest.fixture
def turso():
    """(adapter connection, fake server). The token never leaves the process."""
    server = FakeHrana()
    conn = db_client._HttpConnection("https://db.example.invalid", "test-token")
    conn._post = server.post
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT UNIQUE, v BLOB)")
    server.requests.clear()
    return conn, server


def names(server):
    return [r[0] for r in server.db.execute("SELECT name FROM t ORDER BY id")]


# -------------------------------------------------------------- blob reads

@pytest.mark.parametrize("n", [0, 1, 2, 3, 3072, 3152])
@pytest.mark.parametrize("encode", [
    lambda b: base64.b64encode(b).decode(),                         # padded
    lambda b: base64.b64encode(b).decode().rstrip("="),             # libsql's server
    lambda b: base64.urlsafe_b64encode(b).decode().rstrip("="),     # url-safe alphabet
], ids=["padded", "unpadded", "urlsafe"])
def test_a_blob_decodes_with_or_without_padding(n, encode):
    data = bytes(range(256)) * 13
    data = data[:n]
    assert db_client._unwrap_cell({"type": "blob", "base64": encode(data)}) == data


def test_the_two_shapes_production_sends():
    # Read-only probe of production, 2026-10-15: a 3072-byte mert_embedding
    # arrived as 4096 chars and a 3152-byte fused_embedding as 4203,
    # standard alphabet, no '='. The old reader returned 0 bytes for the
    # second.
    for n, chars in ((3072, 4096), (3152, 4203)):
        data = np.linspace(-1, 1, n // 4, dtype=np.float32).tobytes()
        text = base64.b64encode(data).decode().rstrip("=")
        assert len(text) == chars and "=" not in text
        assert db_client._unwrap_cell({"type": "blob", "base64": text}) == data


def test_a_fused_vector_written_and_read_back_is_intact(turso):
    # 788 float32 = 3152 bytes, 2 short of a multiple of 3: unpadded base64
    # needs one '='. The old decoder raised on it and read b"".
    conn, _ = turso
    vec = np.arange(788, dtype=np.float32) / 7
    conn.execute("INSERT INTO t (name, v) VALUES (?, ?)", ("x", vec.tobytes()))
    blob = conn.execute("SELECT v FROM t").fetchone()[0]
    assert np.array_equal(np.frombuffer(blob, dtype=np.float32), vec)


def test_an_undecodable_blob_raises_instead_of_reading_empty():
    with pytest.raises(sqlite3.DataError):
        db_client._unwrap_cell({"type": "blob", "base64": "not*base64"})
    with pytest.raises(sqlite3.DataError):
        db_client._unwrap_cell({"type": "blob"})


# ---------------------------------------------------------- error mapping

@pytest.mark.parametrize("code,exc", [
    ("SQLITE_CONSTRAINT", sqlite3.IntegrityError),
    ("SQLITE_CONSTRAINT_UNIQUE", sqlite3.IntegrityError),
    ("SQLITE_ERROR", sqlite3.OperationalError),
    ("SQLITE_BUSY", sqlite3.OperationalError),
    (None, sqlite3.OperationalError),
])
def test_hrana_error_codes_map_to_sqlite3_exceptions(code, exc):
    err = db_client._hrana_error({"message": "boom", "code": code})
    assert type(err) is exc and "boom" in str(err)


def test_a_constraint_violation_through_execute_is_an_integrity_error(turso):
    conn, _ = turso
    conn.execute("INSERT INTO t (name) VALUES ('a')")
    with pytest.raises(sqlite3.IntegrityError, match="SQLITE_CONSTRAINT"):
        conn.execute("INSERT INTO t (name) VALUES ('a')")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO nope VALUES (1)")


# ----------------------------------------------------------- execute_batch

def test_a_batch_is_one_request_wrapped_in_a_conditional_transaction(turso):
    conn, server = turso
    conn.execute_batch([("INSERT INTO t (name) VALUES (?)", ("a",)),
                        ("INSERT INTO t (name) VALUES (?)", ("b",))])

    assert len(server.requests) == 1
    batch, close = server.requests[0]
    assert close == {"type": "close"}
    steps = batch["batch"]["steps"]
    assert [s["stmt"]["sql"] for s in steps] == [
        "BEGIN", "INSERT INTO t (name) VALUES (?)", "INSERT INTO t (name) VALUES (?)",
        "COMMIT", "ROLLBACK"]
    assert "condition" not in steps[0]
    assert [s["condition"] for s in steps[1:4]] == [
        {"type": "ok", "step": 0}, {"type": "ok", "step": 1}, {"type": "ok", "step": 2}]
    assert steps[4]["condition"] == {"type": "not", "cond": {"type": "ok", "step": 3}}
    assert steps[1]["stmt"]["args"] == [{"type": "text", "value": "a"}]
    assert names(server) == ["a", "b"]


def test_a_batch_returns_a_cursor_per_statement(turso):
    conn, _ = turso
    conn.execute("INSERT INTO t (name) VALUES ('a'), ('b')")
    cursors = conn.execute_batch([("UPDATE t SET v = x'00'", ()),
                                  ("DELETE FROM t WHERE name = ?", ("a",)),
                                  ("DELETE FROM t WHERE name = ?", ("zzz",))])
    # A statement that matched nothing reports 0, not -1.
    assert [c.rowcount for c in cursors] == [2, 1, 0]


def test_a_failing_statement_lands_nothing_and_names_its_step(turso):
    conn, server = turso
    conn.execute("INSERT INTO t (name) VALUES ('taken')")
    with pytest.raises(sqlite3.IntegrityError, match=r"batch step 2 \(statement 2 of 3\)"):
        conn.execute_batch([("INSERT INTO t (name) VALUES ('first')", ()),
                            ("INSERT INTO t (name) VALUES ('taken')", ()),
                            ("INSERT INTO t (name) VALUES ('third')", ())])
    # 'first' ran and was rolled back; 'third' never ran.
    assert names(server) == ["taken"]


def test_a_failing_batch_raises_an_operational_error_for_other_codes(turso):
    conn, server = turso
    with pytest.raises(sqlite3.OperationalError, match="statement 2 of 2"):
        conn.execute_batch([("INSERT INTO t (name) VALUES ('a')", ()),
                            ("INSERT INTO missing VALUES (1)", ())])
    assert names(server) == []


def test_a_stream_level_error_raises(turso):
    conn, _ = turso
    conn._post = lambda reqs, timeout=30.0: {"results": [
        {"type": "error", "error": {"message": "stream expired", "code": "STREAM_EXPIRED"}}]}
    with pytest.raises(sqlite3.OperationalError, match="STREAM_EXPIRED"):
        conn.execute_batch([("INSERT INTO t (name) VALUES ('a')", ())])


def test_an_empty_batch_sends_nothing(turso):
    conn, server = turso
    assert conn.execute_batch([]) == []
    assert server.requests == []


def test_commit_and_rollback_send_nothing(turso):
    # Each execute() is its own stream, so there is never anything to end.
    conn, server = turso
    conn.commit()
    conn.rollback()
    assert server.requests == []


# ------------------------------------------------- the SQLite counterpart

@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBESCAPE_DB_PATH", str(tmp_path / "local.db"))
    conn = db_client.create_connection()
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    conn.commit()
    yield conn
    conn.close()


def test_sqlite_execute_batch_commits_everything(local):
    cursors = local.execute_batch([("INSERT INTO t (name) VALUES (?)", (n,)) for n in "abc"])
    assert len(cursors) == 3 and not local.in_transaction
    assert [r[0] for r in local.execute("SELECT name FROM t ORDER BY id")] == ["a", "b", "c"]


def test_sqlite_execute_batch_is_all_or_nothing(local):
    with pytest.raises(sqlite3.IntegrityError):
        local.execute_batch([("INSERT INTO t (name) VALUES ('a')", ()),
                             ("INSERT INTO t (name) VALUES ('a')", ())])
    assert local.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_sqlite_execute_batch_refuses_to_swallow_uncommitted_writes(local):
    local.execute("INSERT INTO t (name) VALUES ('pending')")
    with pytest.raises(sqlite3.OperationalError, match="within a transaction"):
        local.execute_batch([("INSERT INTO t (name) VALUES ('b')", ())])
