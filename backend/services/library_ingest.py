"""The online half of the two-phase ingest: _process_track and _link_track,
which write a metadata-only `ingestion_status='pending'` row (or reuse a
matching one) and link it to the user. Everything else about a track is the
offline pipeline's (ingest_pipeline/, scripts/run_ingest_v2.py).

Moved out of app.py unchanged (refactor PR 4, 2026-10-25). Every
/api/ingest/* path ends here: the three job workers call _process_track,
POST /api/ingest/single calls _link_track. _process_track is patched by
tests (test_ingest_jobs), so callers read it as
`library_ingest._process_track` at call time and never copy it with
`from services.library_ingest import ...`.
"""
import sqlite3
from typing import Optional

from bootstrap import log
from services.jobs import _update_job
from track_rows import _isrc_of


# _itunes_search_track() lived here for the synchronous worker. Preview
# resolution is now ingest_pipeline/preview_providers.py, which owns the
# rate limiting and retry policy.


def _process_track(conn, track: dict, job_id: str, user_id: int, source: str = "manual") -> str:
    """
    Online fast path used by all /api/ingest/* endpoints. Writes metadata
    only — the preview cascade, ML scoring and language tagging all happen
    later, offline, in ingest_pipeline/ (scripts/run_ingest_v2.py).

    Returns one of the sync-modal bucket names:
      - added_to_library     — track was already fully ingested globally,
                               user_tracks link created (playable now)
      - already_in_library   — user already had this track linked (no-op),
                               under this spotify_id or another release of
                               the same recording (same ISRC)
      - queued_for_analysis  — new metadata row (or existing pending row)
                               linked to the user, will appear in library
                               once the offline worker finishes it
      - skip:no_id           — track missing spotify_id
    """
    name = track.get("name") or "?"
    artists = track.get("artists") or []
    artist_name = (artists[0].get("name") if artists and isinstance(artists[0], dict) else "?") or "?"
    _update_job(job_id, current_track=f"{name} - {artist_name}")
    return _link_track(conn, track, user_id, source)[0]


# The rows a Spotify track may use: its own spotify_id, or any release of
# the same recording (same ISRC). A NULL ISRC matches nothing. Params:
# (spotify_id, isrc).
_TRACK_MATCH = "(t.spotify_id = ? OR t.isrc = ?)"
# Which of them: the one the user already has, else the exact spotify_id,
# else a finished row over a pending one, else the lowest id. Params:
# (user_id, spotify_id).
_TRACK_PICK = (
    "ORDER BY EXISTS (SELECT 1 FROM user_tracks ut "
    "                 WHERE ut.track_id = t.id AND ut.user_id = ?) DESC, "
    "         (t.spotify_id IS ?) DESC, "
    "         (COALESCE(t.ingestion_status, 'done') = 'done') DESC, "
    "         t.id "
    "LIMIT 1"
)


def _link_track(conn, track: dict, user_id: int, source: str) -> tuple[str, Optional[int]]:
    """_process_track's work: (bucket, tracks.id the user is linked to).

    The id is None only for skip:no_id. It may be a row whose spotify_id
    differs from the track's: a single, album, deluxe and regional release
    of one recording share an ISRC but not a Spotify id, and matching the
    id alone put the same song in the catalogue once per release (on the
    2026-10-16 backup 270 ISRCs had more than one row, and 260 of the 804
    rows still pending were songs already there). A caller that reads the
    row back must read it by this id, not by spotify_id.

    One execute_batch, three statements:
      1. insert a pending metadata row unless some row matches
         (_TRACK_MATCH);
      2. link the user to the row _TRACK_PICK chooses, INSERT OR IGNORE;
      3. read that row's id and status back.
    A user who already had any matching row has it ranked first by
    _TRACK_PICK, so step 2 is ignored and its rowcount of 0 is the
    "already in library" answer; no read of the "before" state is needed.

    The batch is one transaction on both backends (read 2026-10-17 in
    backend/db_client.py): `_SqliteConnection.execute_batch` sends BEGIN,
    runs each statement on the one connection, COMMITs, and ROLLBACKs on
    any error; `_HttpConnection.execute_batch` sends one Hrana `batch` on
    one stream whose steps are BEGIN, each statement conditioned on the one
    before, COMMIT, and ROLLBACK unless the COMMIT succeeded. BEGIN and
    COMMIT are real statements on that stream's connection there too.

    Why a double submit cannot insert twice, even on production where
    `tracks` has no unique index (1.13): step 1 is a write, so SQLite takes
    the write lock as that statement starts, before its NOT EXISTS reads.
    A second batch waits for the lock until the first commits (locally the
    30 s busy timeout), then sees the first's row. Keep every read AFTER
    step 1: a batch that reads first holds a read snapshot and must
    upgrade it, which fails with "database is locked" under contention
    instead of waiting. Tested locally with a write lock held and no unique
    index (tests/backend/test_process_track.py). NOT verified on a real
    libsql server: that it serialises writers is SQLite's design, but it
    may answer a contended batch with SQLITE_BUSY rather than waiting,
    which fails the request without a duplicate.

    Cost: three statements that each scan `tracks` on production (no
    index there), in one request, whatever the case. Locally they are
    index lookups (idx_tracks_spotify_id, idx_tracks_isrc).
    """
    spotify_id = track.get("id")
    if not spotify_id:
        return "skip:no_id", None

    name = track.get("name") or "?"
    artists = track.get("artists") or []
    artist_name = (artists[0].get("name") if artists and isinstance(artists[0], dict) else "?") or "?"
    album_obj = track.get("album") or {}
    album_name = album_obj.get("name")
    images = album_obj.get("images") or []
    artwork_url = images[0].get("url") if images and isinstance(images[0], dict) else None
    duration_ms = track.get("duration_ms")
    isrc = _isrc_of(track)
    preview_url = track.get("preview_url")

    match = (spotify_id, isrc)
    pick = (user_id, spotify_id)
    # vibe_score is NOT NULL in the current schema (legacy from the
    # pre-split-ingest era). Insert 0.0 as a placeholder; the offline
    # pipeline's classify stage overwrites it with the real score.
    # created_at is written here because production's tracks table has no
    # column defaults; rows made before 2026-10-15 are NULL there (4,844 of
    # 6,238 that day, backlog 3.11).
    stmts = [
        ("INSERT INTO tracks "
         "(spotify_id, isrc, title, artist, album, artwork_url, preview_url, "
         " duration_ms, vibe_score, ingestion_status, created_at) "
         "SELECT ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 'pending', CURRENT_TIMESTAMP "
         f"WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE {_TRACK_MATCH})",
         (spotify_id, isrc, name, artist_name, album_name,
          artwork_url, preview_url, duration_ms, *match)),
        ("INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) "
         f"SELECT ?, t.id, ? FROM tracks t WHERE {_TRACK_MATCH} {_TRACK_PICK}",
         (user_id, source, *match, *pick)),
        (f"SELECT t.id, t.ingestion_status FROM tracks t WHERE {_TRACK_MATCH} {_TRACK_PICK}",
         (*match, *pick)),
    ]
    try:
        cursors = conn.execute_batch(stmts)
    except sqlite3.IntegrityError:
        # Two connections collided on the local unique spotify_id index
        # anyway. Nothing of ours was written; once more, and step 1's NOT
        # EXISTS sees the winner's row.
        cursors = conn.execute_batch(stmts)
    inserted, linked, chosen = cursors
    row = chosen.fetchone()
    if row is None:  # step 1 guarantees a match; this would be a bug
        raise RuntimeError(f"no tracks row for spotify_id={spotify_id} after its insert")
    if inserted.rowcount == 1:
        log.info("[ingest] user=%s queued %r - %r (spotify_id=%s)",
                 user_id, name, artist_name, spotify_id)
    if linked.rowcount == 0:
        return "already_in_library", int(row["id"])
    status = row["ingestion_status"] or "done"
    return ("added_to_library" if status == "done" else "queued_for_analysis"), int(row["id"])


# _upsert() and the synchronous ingest worker that used to live here are
# gone. Ingestion is async: _process_track writes a metadata row with
# ingestion_status='pending' and returns; ingest_pipeline/ does the rest
# out of band and owns every write to those columns.


