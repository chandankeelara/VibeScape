"""
The genre tagging interface: find the rows waiting for genre scores, and
write them safely.

The database is the queue, as for language (language_tagging.py): the
pipeline's GenreStage leaves every live row at `genre_status='pending'`, a
Claude Code session reads title / artist / album (plus the language tag and
two audio predictions as hints) and scores it against the taxonomy in
genre_taxonomy.py, and the scores come back through tag_many(). The
procedure is ingest_pipeline/README.md § Tagging genres.

What a score is
---------------
A JSON object, slug -> score, stored in tracks.genre_scores:

    {"film_melody": 100, "folk": 50}

  - per genre, INDEPENDENT: a track can be 100 on two genres. Scores do not
    sum to anything.
  - each value is 50 (partly / clearly an influence) or 100 (squarely this);
    zero is the absence of the genre and is OMITTED, never written.
  - at least one genre: an empty object is invalid. There is no skip and no
    clear verdict, unlike language: every song sounds like something, and
    'other' exists for a sound the taxonomy does not name.
  - no confidence field. 50 already says "partly".

Genre does not gate anything
----------------------------
Unlike language, genre is not in the fused vector yet, so a tag re-arms
nothing and an untagged track still reaches the DJ pool.

TODO(dj-vector genre block): when the DJ vector gains a genre block (a
later PR, indexed by genre_taxonomy.GENRES with spare slots), tag_many must
re-arm fuse (`fuse_status='pending'`) for every row whose scores CHANGED,
exactly like language_tagging.tag's cascade, or a corrected genre leaves a
stale vector that nothing flags.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .genre_taxonomy import GENRE_TAXONOMY_VERSION, GENRES, SCORE_VALUES


log = logging.getLogger("vibescape.ingest.genre")


# THE PREDICATE, spelled once: quoted by GenreStage, report() and
# ingest_pipeline/README.md. GenreStage normalises production's NULL into
# it for live rows, so a tagging session has one spelling to remember.
WAITING_PREDICATE = "genre_status = 'pending'"

# What a tagging session reads. NOT the iTunes `genre` column: that is one
# store's label for the release (often just "Soundtrack" or "Indian Pop"),
# and showing it would anchor the session on exactly the coarse label this
# scoring replaces. language, energy_pred and danceability_pred are there
# as hints: an energetic Kannada film song is more likely film_mass.
WAITING_COLUMNS = ("id", "spotify_id", "title", "artist", "album", "language",
                   "energy_pred", "danceability_pred")

WAITING_SQL = (
    f"SELECT {', '.join(WAITING_COLUMNS)} FROM tracks "
    f"WHERE {WAITING_PREDICATE} ORDER BY id ASC"
)

# Stamped into genre_model_version: who scored it, against which taxonomy.
MODEL_VERSION = f"claude_session_metadata_v1+taxonomy_{GENRE_TAXONOMY_VERSION}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def fetch_waiting(conn, limit: int | None = None) -> list:
    """Rows waiting for genre scores, oldest id first, with WAITING_COLUMNS."""
    sql = WAITING_SQL
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (int(limit),)
    return list(conn.execute(sql, params).fetchall())


def validate_scores(scores) -> dict[str, int]:
    """Check one track's scores and return them in taxonomy order.

    Raises ValueError on anything that is not a non-empty object of known
    slugs to 50 or 100. A 0 is rejected rather than dropped: zeros are
    omitted by the writer of the file, and a 0 there most likely means the
    scale was misread.
    """
    if not isinstance(scores, dict):
        raise ValueError(f"scores must be an object, got {type(scores).__name__}")
    if not scores:
        raise ValueError("scores is empty; every track needs at least one genre at 50 or 100")
    unknown = sorted(k for k in scores if k not in GENRES)
    if unknown:
        raise ValueError(f"unknown genre(s) {unknown}; see ingest_pipeline/genre_taxonomy.py")
    for slug, value in scores.items():
        # bool is an int subclass: True must not pass as a score.
        if isinstance(value, bool) or not isinstance(value, int) or value not in SCORE_VALUES:
            raise ValueError(f"{slug}={value!r}: a score is 50 or 100 (omit a genre for 0)")
    return {slug: int(scores[slug]) for slug in GENRES if slug in scores}


def encode_scores(scores: dict) -> str:
    """The stored form: compact JSON, keys in taxonomy order."""
    return json.dumps(validate_scores(scores), separators=(",", ":"))


def tag_many(conn, items) -> dict:
    """Write genre scores for many tracks in ONE transaction.

    `items` is [(track_id, scores), ...]; an item may carry a third element,
    the row's spotify_id, and then it is written only where both match (a
    file made from one database's ids applied to another misses instead of
    tagging the wrong song: local and prod ids diverge, README § Prod).

    Every item is validated before anything is written, so one bad score
    writes nothing at all. Then every UPDATE goes out in one
    conn.execute_batch: one transaction, and on Turso one request. A write
    is an UPDATE of absolute values, so re-running a file is harmless.

    Returns {"written": n, "missing": [track ids that matched no row]}.
    A missing id is not an error here: the rows that matched are written,
    and the caller reports the rest.
    """
    stmts, ids, seen = [], [], set()
    stamp = _iso_now()
    for n, item in enumerate(items):
        if not isinstance(item, (tuple, list)) or len(item) not in (2, 3):
            raise ValueError(f"item {n}: expected (track_id, scores[, spotify_id])")
        track_id, scores = item[0], item[1]
        spotify_id = item[2] if len(item) == 3 else None
        if isinstance(track_id, bool) or not isinstance(track_id, int) or track_id <= 0:
            raise ValueError(f"item {n}: track id must be a positive integer, got {track_id!r}")
        if track_id in seen:
            raise ValueError(f"item {n}: track {track_id} appears twice")
        seen.add(track_id)
        try:
            encoded = encode_scores(scores)
        except ValueError as e:
            raise ValueError(f"item {n} (track {track_id}): {e}") from None
        sql = ("UPDATE tracks SET genre_scores = ?, genre_status = 'done', "
               "genre_model_version = ?, genre_tagged_at = ? WHERE id = ?")
        params = [encoded, MODEL_VERSION, stamp, track_id]
        if spotify_id:
            sql += " AND spotify_id = ?"
            params.append(str(spotify_id))
        stmts.append((sql, params))
        ids.append(track_id)
    # TODO(dj-vector genre block): add the fuse cascade here; see the module
    # docstring. Nothing reads genre_scores into a vector yet.
    if not stmts:
        return {"written": 0, "missing": []}
    cursors = conn.execute_batch(stmts)
    missing = [tid for tid, cur in zip(ids, cursors) if cur.rowcount == 0]
    return {"written": len(ids) - len(missing), "missing": missing}


def report(conn) -> dict:
    """Counts by genre_status, and how many wait. Printed by
    scripts/genre_tags.py --status."""
    by_status = {
        (r[0] or "NULL"): int(r[1]) for r in conn.execute(
            "SELECT genre_status, COUNT(*) FROM tracks GROUP BY 1"
        ).fetchall()
    }
    return {"by_status": by_status, "waiting": by_status.get("pending", 0)}
