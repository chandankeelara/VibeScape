"""Genre scores: the taxonomy, the write path (genre_tagging.tag_many), the
queue stage (GenreStage), the CLI (scripts/genre_tags.py) and the column
migration (db.py locally, scripts/_turso_add_genre_columns.py on Turso).

Nothing here reaches production: the CLI and the migration run with
--target local against each test's own SQLite file (conftest.py).
"""
import json
import sqlite3

import numpy as np
import pytest

import _turso_add_genre_columns as add_columns
import genre_tags
import run_ingest_v2 as runner
from db import TRACKS_GENRE_COLUMNS, ensure_db, get_conn
from ingest_pipeline import genre_tagging as gt
from ingest_pipeline import genre_taxonomy as tax
from ingest_pipeline.fused_vector import MERT_DIM
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION
from ingest_pipeline.stage_genre import GenreStage

# The taxonomy as first shipped (2026-10-23). The order is append-only: a
# later DJ-vector block indexes by it, so this list may only ever be a
# PREFIX of GENRES. Never edit it; append to genre_taxonomy.py instead.
FROZEN_V1 = ("pop", "hiphop_rap", "dance_edm", "ambient_chill", "rock_alt_metal",
             "rnb_soul", "film_melody", "film_mass", "folk", "devotional", "classical",
             "instrumental_score", "latin_afro", "reggae_dancehall", "country",
             "jazz_blues", "other")

GENRE_COLS = [c for c, _ in TRACKS_GENRE_COLUMNS]


def add_track(sql, title="T", ingestion_status="pending", **cols):
    names = ["title", "artist", "spotify_id", "ingestion_status", "vibe_score", *cols]
    values = [title, "A", f"sp{sql.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]}",
              ingestion_status, 0.0, *cols.values()]
    cur = sql.execute(f"INSERT INTO tracks ({', '.join(names)}) "
                      f"VALUES ({', '.join('?' * len(values))})", values)
    sql.commit()
    return int(cur.lastrowid)


def genre_row(sql, tid):
    return sql.execute(f"SELECT {', '.join(GENRE_COLS)} FROM tracks WHERE id = ?",
                       (tid,)).fetchone()


# ------------------------------------------------------------- taxonomy

def test_the_taxonomy_is_append_only_safe():
    assert tax.GENRES[:len(FROZEN_V1)] == FROZEN_V1
    assert len(set(tax.GENRES)) == len(tax.GENRES)
    assert tax.GENRE_TAXONOMY_VERSION
    assert all(d.strip() and "\n" not in d for d in tax.DEFINITIONS.values())
    assert gt.MODEL_VERSION.endswith(f"+taxonomy_{tax.GENRE_TAXONOMY_VERSION}")


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("scores", [
    {},                               # every song gets at least one genre
    {"bhangra": 100},                 # not in the taxonomy
    {"pop": 0},                       # zeros are omitted, not written
    {"pop": 75},
    {"pop": "100"},
    {"pop": True},
    {"pop": 100.0},
    ["pop"],
    None,
])
def test_validation_rejects_anything_but_known_slugs_at_50_or_100(scores):
    with pytest.raises(ValueError):
        gt.validate_scores(scores)


def test_validation_returns_scores_in_taxonomy_order():
    assert list(gt.validate_scores({"folk": 50, "film_melody": 100})) == ["film_melody", "folk"]
    assert gt.encode_scores({"folk": 50, "film_melody": 100}) == '{"film_melody":100,"folk":50}'


# ------------------------------------------------------------- tag_many

def test_tag_many_writes_every_row_in_one_batch(sql, batches):
    a, b, c = (add_track(sql, genre_status="pending") for _ in range(3))
    conn = get_conn()
    try:
        res = gt.tag_many(conn, [(a, {"pop": 100}), (b, {"folk": 50, "devotional": 100}),
                                 (c, {"other": 50})])
    finally:
        conn.close()
    assert res == {"written": 3, "missing": []}
    assert len(batches) == 1 and len(batches[0]) == 3
    scores, status, version, at = genre_row(sql, b)
    assert (json.loads(scores), status, version) == (
        {"folk": 50, "devotional": 100}, "done", gt.MODEL_VERSION)
    assert at


def test_one_bad_score_writes_nothing(sql, batches):
    a, b = (add_track(sql, genre_status="pending") for _ in range(2))
    conn = get_conn()
    try:
        with pytest.raises(ValueError, match=f"track {b}"):
            gt.tag_many(conn, [(a, {"pop": 100}), (b, {"pop": 0})])
        with pytest.raises(ValueError, match="twice"):
            gt.tag_many(conn, [(a, {"pop": 100}), (a, {"folk": 100})])
    finally:
        conn.close()
    assert batches == []
    assert [genre_row(sql, t)[1] for t in (a, b)] == ["pending", "pending"]


def test_tag_many_is_one_transaction(sql):
    # A failure inside the batch lands nothing: the first row's UPDATE is
    # rolled back with the rest.
    a = add_track(sql, genre_status="pending")
    sql.execute("CREATE TRIGGER boom BEFORE UPDATE OF genre_scores ON tracks "
                f"WHEN NEW.id != {a} BEGIN SELECT RAISE(ABORT, 'boom'); END")
    b = add_track(sql, genre_status="pending")
    sql.commit()
    conn = get_conn()
    try:
        with pytest.raises(sqlite3.Error):
            gt.tag_many(conn, [(a, {"pop": 100}), (b, {"pop": 100})])
    finally:
        conn.close()
    assert genre_row(sql, a)[:2] == (None, "pending")


def test_ids_that_match_no_row_or_not_their_spotify_id_are_reported(sql):
    a = add_track(sql, genre_status="pending")
    sid = sql.execute("SELECT spotify_id FROM tracks WHERE id = ?", (a,)).fetchone()[0]
    conn = get_conn()
    try:
        assert gt.tag_many(conn, [(a, {"pop": 100}, "someone-else"), (9999, {"pop": 50})]) == {
            "written": 0, "missing": [a, 9999]}
        assert genre_row(sql, a)[1] == "pending"
        assert gt.tag_many(conn, [(a, {"pop": 100}, sid)]) == {"written": 1, "missing": []}
    finally:
        conn.close()
    assert genre_row(sql, a)[1] == "done"


def test_fetch_waiting_never_returns_the_itunes_genre(sql):
    tid = add_track(sql, genre="Soundtrack", genre_status="pending", language="kn",
                    energy_pred=0.7, danceability_pred=0.4, album="Film")
    add_track(sql, genre_status="done")
    add_track(sql)                                    # NULL: not normalised yet
    conn = get_conn()
    try:
        rows = gt.fetch_waiting(conn, 10)
    finally:
        conn.close()
    assert [r["id"] for r in rows] == [tid]
    assert tuple(rows[0].keys()) == gt.WAITING_COLUMNS
    assert "genre" not in rows[0].keys() and "Soundtrack" not in tuple(rows[0])


# ------------------------------------------------------------- the stage

def test_the_stage_normalises_null_for_live_rows_only(sql):
    live = {s: add_track(sql, ingestion_status=s) for s in ("pending", "done")}
    live["null"] = add_track(sql, ingestion_status=None)
    left = {
        "no_preview": add_track(sql, ingestion_status="no_preview"),
        "untitled": add_track(sql, title=""),
        "tagged": add_track(sql, ingestion_status="done", genre_status="done"),
    }
    conn = get_conn()
    try:
        counts = GenreStage().run_batch(conn, 50, runner.log)
    finally:
        conn.close()
    assert counts.get("pending") == 3
    assert {k: genre_row(sql, t)[1] for k, t in live.items()} == dict.fromkeys(live, "pending")
    assert {k: genre_row(sql, t)[1] for k, t in left.items()} == {
        "no_preview": None, "untitled": None, "tagged": "done"}
    # It writes its own column and nothing else: no fuse arming, no
    # ingestion_status change.
    assert sql.execute("SELECT COUNT(*) FROM tracks WHERE fuse_status IS NOT NULL").fetchone()[0] == 0
    assert sql.execute("SELECT ingestion_status FROM tracks WHERE id = ?",
                       (live["done"],)).fetchone()[0] == "done"


@pytest.mark.parametrize("genre_status", [None, "pending"])
def test_genre_does_not_gate_fuse_or_youtube(sql, genre_status):
    done = dict(preview_status="done", download_status="done", librosa_status="done",
                ml_status="done", language_status="done")
    fuse = add_track(sql, genre_status=genre_status, fuse_status="pending", **done)
    youtube = add_track(sql, genre_status=genre_status, fuse_status="done",
                        youtube_status="pending", **done)
    sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, model_version) "
                "VALUES (?, ?, ?)", (fuse, np.ones(MERT_DIM, np.float32).tobytes(),
                                     CURRENT_MERT_VERSION))
    sql.commit()
    for name, tid in (("fuse", fuse), ("youtube", youtube)):
        stage = object.__new__(runner.STAGE_CLASSES[name])   # no model loading
        assert [int(r["id"]) for r in stage.fetch_pending(sql, 50)] == [tid], name
    assert GenreStage.arms == ()
    assert not any("genre" in getattr(c, "after", ()) for c in runner.STAGE_CLASSES.values())


def test_genre_is_a_default_stage(db, monkeypatch):
    built = []
    monkeypatch.setattr(runner, "build_stages", lambda names: built.append(names) or [])
    assert runner.main([]) == 0
    assert built == [["preview", "download", "librosa", "classify", "language", "genre",
                      "fuse", "youtube"]]


# ------------------------------------------------------------- the CLI

def write_lines(tmp_path, lines):
    path = tmp_path / "scores.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


def test_the_cli_dry_run_writes_nothing(sql, tmp_path, batches, capsys):
    ids = [add_track(sql, genre_status="pending") for _ in range(3)]
    path = write_lines(tmp_path, [{"id": t, "scores": {"pop": 100}} for t in ids])
    assert genre_tags.main(["--apply-file", str(path)]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert batches == []
    assert [genre_row(sql, t)[1] for t in ids] == ["pending"] * 3


def test_the_cli_writes_in_batches_of_200(sql, tmp_path, batches, capsys):
    ids = [add_track(sql, genre_status="pending") for _ in range(450)]
    path = write_lines(tmp_path, [{"id": t, "scores": {"film_mass": 100, "folk": 50}}
                                  for t in ids])
    assert genre_tags.main(["--apply-file", str(path), "--apply"]) == 0
    assert [len(b) for b in batches] == [200, 200, 50]
    out = capsys.readouterr().out
    assert "batch 3: rows 401..450: 50 written, 0 missing" in out
    assert sql.execute("SELECT COUNT(*) FROM tracks WHERE genre_status = 'done'").fetchone()[0] == 450


def test_the_cli_refuses_a_file_with_one_bad_line_and_writes_nothing(sql, tmp_path, batches,
                                                                      capsys):
    a, b = (add_track(sql, genre_status="pending") for _ in range(2))
    path = write_lines(tmp_path, [{"id": a, "scores": {"pop": 100}},
                                  {"id": b, "scores": {"pop": 100, "bhangra": 50}}])
    assert genre_tags.main(["--apply-file", str(path), "--apply"]) == 2
    assert "line 2" in capsys.readouterr().out
    assert batches == []
    assert genre_row(sql, a)[1] == "pending"


def test_the_cli_status_and_waiting(sql, capsys):
    tid = add_track(sql, genre="Bollywood", genre_status="pending")
    assert genre_tags.main(["--status"]) == 1
    capsys.readouterr()
    assert genre_tags.main(["--waiting", "--limit", "5"]) == 0
    (line,) = capsys.readouterr().out.strip().splitlines()
    row = json.loads(line)
    assert row["id"] == tid and set(row) == set(gt.WAITING_COLUMNS)
    sql.execute("UPDATE tracks SET genre_status = 'done'")
    sql.commit()
    assert genre_tags.main(["--status"]) == 0


# ------------------------------------------------------------- the columns

def drop_genre_columns(path):
    conn = sqlite3.connect(path)
    for col in GENRE_COLS:
        conn.execute(f"ALTER TABLE tracks DROP COLUMN {col}")
    conn.commit()
    conn.close()


def columns(path):
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    finally:
        conn.close()


def test_a_local_database_without_the_columns_gains_them(db):
    drop_genre_columns(db)
    assert not set(GENRE_COLS) & columns(db)
    ensure_db()
    assert set(GENRE_COLS) <= columns(db)


def test_the_column_script_is_dry_run_by_default_and_idempotent(db, capsys):
    drop_genre_columns(db)
    assert add_columns.main([]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert not set(GENRE_COLS) & columns(db)
    assert add_columns.main(["--apply"]) == 0
    assert set(GENRE_COLS) <= columns(db)
    assert add_columns.main(["--apply"]) == 0
    assert "nothing to do" in capsys.readouterr().out
