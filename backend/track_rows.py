"""The track row: which columns a track dict carries, the SELECT list that
reads them, the dict every track endpoint returns (_row_to_dict), the
narrow per-user rows library search scores against, and the ISRC a Spotify
track is matched to catalogue rows by.

Moved out of app.py unchanged (refactor PR 2, 2026-10-25). Mutable state
here (_genre_scores_state, _search_rows_cache) is mutated in place by
callers and tests, never rebound.
"""
import time as _time
from typing import Optional

from bootstrap import log  # first: puts ingest/ on sys.path for `scoring`

import scoring  # noqa: E402
import track_genres as _track_genres  # noqa: E402

TRACK_COLUMNS = [
    "id",
    "apple_id",
    "title",
    "artist",
    "album",
    "genre",
    "artwork_url",
    "preview_url",
    "track_view_url",
    "duration_ms",
    "vibe_score",
    "mood",
    "spotify_id",
    "classification_source",
    # extended derived / raw scalars exposed to frontend
    "activation",
    "valence",
    "activation_relative",
    "acousticness",
    "valence_mode",
    "tempo",
    "energy_mean",
    "youtube_id",
    # ML predictions (nullable until scripts/predict_ml.py has run for the track)
    "energy_pred",
    "danceability_pred",
    "valence_pred",
    "vibe_score_ml",
    "model_version",
    # Whisper language classifier — nullable when confidence < 0.2 (instrumental
    # / non-speech tracks) or when the classifier hasn't run yet.
    "language",
    "language_confidence",
    # Two-phase ingest state. 'done' = playable, 'pending' = awaiting the
    # offline worker, 'no_preview' / 'failed' = terminal errors. The
    # library / mood-grid endpoints filter to 'done'.
    "ingestion_status",
]
TRACK_SELECT = ", ".join(TRACK_COLUMNS)

# tracks.genre_scores, read into every track dict as `genres`
# (track_genres.genres_list). Not in TRACK_COLUMNS because production's
# `tracks` has no such column until scripts/_turso_add_genre_columns.py has
# run there, and a SELECT naming it would fail every player list. Whether it
# exists is asked once per process (_genre_scores_ready); a no is re-asked
# every _GENRE_RECHECK_S so the migration is picked up without a restart.
GENRE_SCORES_COLUMN = "genre_scores"
_GENRE_RECHECK_S = 60.0
_genre_scores_state = {"ready": None, "checked_at": 0.0, "warned": False}


def _genre_scores_ready(conn) -> bool:
    """Does `tracks` have the genre_scores column? A zero-row SELECT, which
    fails the same way on SQLite and over Hrana and reads no rows."""
    st = _genre_scores_state
    now = _time.time()
    if st["ready"] or (st["ready"] is False and now - st["checked_at"] < _GENRE_RECHECK_S):
        return bool(st["ready"])
    try:
        conn.execute(f"SELECT {GENRE_SCORES_COLUMN} FROM tracks LIMIT 0").fetchall()
        ready = True
    except Exception as e:
        ready = False
        if not st["warned"]:
            st["warned"] = True
            log.warning("tracks.%s missing (%s): track payloads carry genres: [] "
                        "until scripts/_turso_add_genre_columns.py has run",
                        GENRE_SCORES_COLUMN, e)
    st["ready"], st["checked_at"] = ready, now
    return ready


def _track_select(conn, alias: str = "t") -> str:
    """The SELECT list for a track dict (_row_to_dict): TRACK_COLUMNS, plus
    genre_scores where the database has it."""
    cols = list(TRACK_COLUMNS)
    if _genre_scores_ready(conn):
        cols.append(GENRE_SCORES_COLUMN)
    return ", ".join(f"{alias}.{c}" for c in cols)

MOODS = list(scoring.MOODS)


def _row_to_dict(row):
    out = {}
    for col in TRACK_COLUMNS:
        try:
            out[col] = row[col]
        except (IndexError, KeyError):
            out[col] = None
    # Absent from the row when the database has no genre_scores column
    # (_track_select), or the caller selected its own columns.
    try:
        raw_genres = row[GENRE_SCORES_COLUMN]
    except (IndexError, KeyError):
        raw_genres = None
    out["genres"] = _track_genres.genres_list(raw_genres)
    # frontend slider works against vibe_score, but callers may prefer
    # activation_relative once library z-scores exist. Expose both; when
    # activation_relative is populated, mirror it into vibe_score for
    # backward-compat filter queries.
    #
    # in_library (the player's heart) costs no query: every caller passes a
    # row its query took through `JOIN user_tracks ut ... ut.user_id = <the
    # caller>` in the same request (tests/backend/test_library_heart.py
    # checks each endpoint). A caller that returns rows from outside the
    # caller's library must set it itself, and so must `discover` (true only
    # for a song the caller owns no release of), false here for the same
    # reason. Discovery mode's /similar sets both: _dj_tag_library.
    out["in_library"] = True
    out["discover"] = False
    return out


# user_id -> (fetched_at, [{id, title, artist, album, language}]). Narrow rows
# only; a burst of keystrokes reads the library once. 60 s staleness is fine:
# new tracks only become searchable after the offline pipeline anyway.
_SEARCH_ROWS_TTL_S = 60
_search_rows_cache: dict = {}


def _search_rows(conn, user_id: int) -> list:
    hit = _search_rows_cache.get(user_id)
    now = _time.time()
    if hit and now - hit[0] < _SEARCH_ROWS_TTL_S:
        return hit[1]
    rows = conn.execute(
        "SELECT t.id, t.title, t.artist, t.album, t.language FROM tracks t "
        "JOIN user_tracks ut ON ut.track_id = t.id "
        "WHERE ut.user_id = ? AND t.ingestion_status = 'done'",
        (user_id,),
    ).fetchall()
    out = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3], "language": r[4]}
           for r in rows]
    if len(_search_rows_cache) > 256:
        _search_rows_cache.clear()
    _search_rows_cache[user_id] = (now, out)
    return out


# Shared by the ingest path's _link_track (services/library_ingest.py) and Spotify search's
# in_library flag (routers/spotify.py), so it lives with neither.
def _isrc_of(track: dict) -> Optional[str]:
    """A Spotify track's ISRC, None when it has none (or an empty one)."""
    return (track.get("external_ids") or {}).get("isrc") or None
