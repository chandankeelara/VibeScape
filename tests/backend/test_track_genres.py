"""`genres` on every track dict the player reads (backend/track_genres.py,
track_rows._track_select / _row_to_dict).

`tracks.genre_scores` exists locally (db.py adds it) but not on production
until scripts/_turso_add_genre_columns.py has run there, so the SELECTs name
it only where it exists: on a database without it every list still answers,
with `genres: []`, and the column is picked up without a restart once the
migration adds it.
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest

import track_genres
import track_rows
from ingest_pipeline.genre_taxonomy import GENRES
from test_dj_paths import _turso_shaped

REPO = Path(__file__).resolve().parents[2]
SCORES = {"pop": 50, "film_mass": 100, "other": 50, "dance_edm": 100}
EXPECTED = [{"slug": "dance_edm", "score": 100}, {"slug": "film_mass", "score": 100},
            {"slug": "pop", "score": 50}, {"slug": "other", "score": 50}]


# ------------------------------------------------------------ genres_list

def test_order_is_the_taxonomy():
    assert track_genres.GENRE_ORDER == GENRES


def test_every_slug_has_a_frontend_label():
    src = (REPO / "frontend-next" / "src" / "lib" / "genres.js").read_text(encoding="utf-8")
    labels = re.search(r"GENRE_LABELS = \{(.*?)\};", src, re.S).group(1)
    assert re.findall(r"^\s*(\w+):", labels, re.M) == list(GENRES)


def test_sorted_by_score_then_taxonomy():
    assert track_genres.genres_list(json.dumps(SCORES)) == EXPECTED


@pytest.mark.parametrize("raw", [None, "", "{}", "not json", "[1, 2]", "null",
                                 json.dumps({"polka": 100, "pop": 75, "folk": True, "jazz_blues": "50"})])
def test_untagged_or_unreadable_is_empty(raw):
    assert track_genres.genres_list(raw) == []


# ------------------------------------------------------------ the payloads

def _lists(client, h, ids):
    return {
        "tracks": client.get("/api/tracks", headers=h).json(),
        "random": [client.get("/api/tracks/random", params={"vibe": 50}, headers=h).json()],
        "search": client.get("/api/tracks/search", params={"q": "genresong"}, headers=h).json()["tracks"],
        "vibe": client.get(f"/api/tracks/{ids[1]}/similar", headers=h).json()["tracks"],
        "dj": client.post(f"/api/tracks/{ids[1]}/similar", headers=h,
                          json={"mode": "dj", "limit": 8}).json()["tracks"],
        "dj_replay": _replay(client, h, ids)["tracks"],
    }


def _replay(client, h, ids):
    """The DJ with a session log: on DB_BACKEND=turso, the vector_distance_cos
    SQL; else the numpy path, whose rows come from a second SELECT."""
    return client.post(f"/api/tracks/{ids[1]}/similar", headers=h, json={
        "mode": "dj", "limit": 8, "exclude_ids": [ids[1]],
        "events": [{"id": ids[1], "action": "completed", "played_ratio": 1.0}]}).json()


def _tag(sql, tid):
    sql.execute("UPDATE tracks SET genre_scores = ?, genre_status = 'done' WHERE id = ?",
                (json.dumps(SCORES), tid))
    sql.commit()


def test_every_player_list_carries_genres(client, sql, make_user, make_tracks, clusters):
    me, h = make_user()
    ids = make_tracks(me, clusters["A"](6), titles={0: "Genresong"})
    for tid in ids:
        _tag(sql, tid)
    sql.execute("UPDATE tracks SET genre_scores = NULL, genre_status = NULL WHERE id = ?", (ids[2],))
    sql.commit()
    for name, tracks in _lists(client, h, ids).items():
        assert tracks, name
        for t in tracks:
            assert t["genres"] == ([] if t["id"] == ids[2] else EXPECTED), name
            assert "genre_scores" not in t, name


def test_already_ingested_single_carries_genres(client, sql, make_user, make_tracks):
    me, h = make_user()
    (tid,) = make_tracks(me, [None])
    _tag(sql, tid)
    sp = sql.execute("SELECT spotify_id FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    r = client.post("/api/ingest/single", headers=h, json={"spotify_id": sp})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "already_ingested"
    assert r.json()["track"]["genres"] == EXPECTED


@pytest.mark.parametrize("backend", ["sqlite", "turso"])
def test_without_the_column_every_list_answers_with_no_genres(client, sql, db, app_module, make_user,
                                                               make_tracks, clusters, monkeypatch,
                                                               use_conn, caplog, backend):
    """Production's shape: no genre_scores column. Without the check every
    list SELECT fails with 'no such column' -- a 500 on search and random,
    an empty /api/tracks, and the Turso DJ path falling back to numpy."""
    import logging
    me, h = make_user()
    ids = make_tracks(me, clusters["A"](6), titles={0: "Genresong"})
    for col in ("genre_scores", "genre_status", "genre_model_version", "genre_tagged_at"):
        sql.execute(f"ALTER TABLE tracks DROP COLUMN {col}")
    sql.commit()
    # No ensure_db, which would add the columns back.
    use_conn(_turso_shaped(db))
    if backend == "turso":
        monkeypatch.setenv("DB_BACKEND", "turso")
    caplog.set_level(logging.WARNING, logger="vibescape")

    for _ in range(2):
        lists = _lists(client, h, ids)
        for name, tracks in lists.items():
            assert tracks, name
            assert all(t["genres"] == [] for t in tracks), name
    mode = _replay(client, h, ids)["mode_used"]
    assert mode == ("dj_replay_fused_turso" if backend == "turso" else "dj_replay_fused"), mode
    warnings = [r for r in caplog.records if "genre_scores missing" in r.getMessage()]
    assert len(warnings) == 1

    # The migration lands: picked up at the next check, no restart.
    sql.execute("ALTER TABLE tracks ADD COLUMN genre_scores TEXT")
    sql.execute("UPDATE tracks SET genre_scores = ? WHERE id = ?", (json.dumps(SCORES), ids[0]))
    sql.commit()
    track_rows._genre_scores_state["checked_at"] = 0.0
    out = client.get("/api/tracks/search", params={"q": "genresong"}, headers=h).json()["tracks"]
    assert out[0]["genres"] == EXPECTED


def test_the_column_check_is_cached(sql, db, app_module):
    """Asked once per process once it is there; a no is re-asked only after
    _GENRE_RECHECK_S."""
    calls = []
    real = _turso_shaped(db)

    class Counting:
        def __init__(self):
            self._c = real()

        def execute(self, q, p=()):
            if "LIMIT 0" in q:
                calls.append(q)
            return self._c.execute(q, p)

    conn = Counting()
    for _ in range(3):
        assert "t.genre_scores" in track_rows._track_select(conn)
    assert len(calls) == 1
