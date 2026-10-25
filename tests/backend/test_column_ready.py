"""The three "is this optional column there yet?" checks the app makes:
tracks.genre_scores (track_rows._genre_scores_ready), and fused_v2 and
fused_v3 in track_embeddings (services/vectors.py). Each answers from a
per-process cache: a yes for the life of the process, a no until its
recheck interval has passed since the last ask, and it warns once.

The end-to-end tests (test_track_genres, test_dj_paths) cover a yes being
cached, the single warning and a reset picking a migration up; this pins
the rest of the cache on all three with a fake connection, so the shared
helper they were merged into (refactor PR 6) cannot drift for one of them.
"""
import logging

import pytest

import track_rows
from services import vectors


class FakeConn:
    """Answers the readiness probes: `absent` raises like a missing column,
    `empty` has the column with no vector in it, `filled` has a vector."""

    def __init__(self, shape):
        self.shape, self.calls = shape, []

    def execute(self, q, params=()):
        self.calls.append(q)
        if self.shape == "absent":
            raise RuntimeError("no such column: x")
        rows = [(1,)] if self.shape == "filled" and "IS NOT NULL" in q else []
        return _Cursor(rows)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


CHECKS = {
    "genre_scores": (track_rows._genre_scores_ready, track_rows._genre_scores_state,
                     track_rows._GENRE_RECHECK_S, "tracks.genre_scores missing"),
    "fused_v2": (vectors._fused_v2_ready, vectors._fused_v2_state,
                 vectors._FUSED_V2_RECHECK_S, f"[dj] {vectors.FUSED_V2_COLUMN} not ready"),
    "fused_v3": (vectors._fused_v3_ready, vectors._fused_v3_state,
                 vectors._FUSED_V2_RECHECK_S, f"[dj] {vectors.FUSED_COLUMN} not ready"),
}


def _warnings(caplog, prefix):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


@pytest.mark.parametrize("name", CHECKS)
def test_a_yes_is_asked_once(db, name):
    check, state, _, _ = CHECKS[name]
    conn = FakeConn("filled")
    assert [check(conn) for _ in range(3)] == [True, True, True]
    assert len(conn.calls) == 1
    state["checked_at"] = 0.0          # a yes never expires
    assert check(conn) is True and len(conn.calls) == 1


@pytest.mark.parametrize("name", CHECKS)
def test_a_no_is_re_asked_only_after_the_interval(db, name, caplog):
    check, state, recheck_s, prefix = CHECKS[name]
    caplog.set_level(logging.WARNING, logger="vibescape")
    conn = FakeConn("absent")
    assert [check(conn) for _ in range(3)] == [False, False, False]
    assert len(conn.calls) == 1
    assert state["ready"] is False

    state["checked_at"] -= recheck_s - 5          # still inside the interval
    assert check(conn) is False and len(conn.calls) == 1
    state["checked_at"] -= 10                      # now past it
    assert check(conn) is False and len(conn.calls) == 2
    assert check(conn) is False and len(conn.calls) == 2   # the window restarts

    warned = _warnings(caplog, prefix)
    assert len(warned) == 1 and "no such column: x" in warned[0]

    conn.shape = "filled"                          # the migration lands
    state["checked_at"] -= recheck_s + 1
    assert check(conn) is True and len(conn.calls) == 3
    assert len(_warnings(caplog, prefix)) == 1


def test_fused_v3_needs_a_vector_and_fused_v2_only_the_column(db, caplog):
    """An empty fused_v3 column is not ready (nothing to serve); an empty
    fused_v2 column is (it only has to exist)."""
    caplog.set_level(logging.WARNING, logger="vibescape")
    assert vectors._fused_v3_ready(FakeConn("empty")) is False
    assert vectors._fused_v2_ready(FakeConn("empty")) is True
    assert _warnings(caplog, f"[dj] {vectors.FUSED_COLUMN} not ready") == [
        f"[dj] {vectors.FUSED_COLUMN} not ready (no vectors): ranking "
        f"{vectors.FUSED_V2_COLUMN} until scripts/refuse_vectors.py has run"]


def test_the_genre_warning_text(db, caplog):
    caplog.set_level(logging.WARNING, logger="vibescape")
    track_rows._genre_scores_ready(FakeConn("absent"))
    assert _warnings(caplog, "tracks.genre_scores") == [
        "tracks.genre_scores missing (no such column: x): track payloads carry "
        "genres: [] until scripts/_turso_add_genre_columns.py has run"]
