"""Track reads for the signed-in caller: the vibe-range list, fuzzy library
search, a random pick in a vibe band, one track's stored features, its
lyrics (cached, else LRCLIB), and the Spotify id of an Apple-keyed track.

The DJ's /api/tracks/{key}/similar routes are not here: they are
routers/dj.py."""
import json
import sqlite3
from typing import Optional

from bootstrap import log  # first: .env, sys.path (see bootstrap.py)

import requests
from fastapi import Depends, HTTPException, Query

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
import library_search as _libsearch
import lyrics as _lyrics
from deps import require_user
from routers import Router
from services.vectors import _resolve_anchor
from track_rows import _row_to_dict, _search_rows, _track_select

router = Router()


@router.get("/api/tracks")
def list_tracks(
    vibe_min: float = 0,
    vibe_max: float = 100,
    limit: int = 20,
    mood: Optional[str] = None,
    shuffle: bool = False,
    sess: dict = Depends(require_user),
):
    # Prefer the ML-predicted vibe (0-1 scaled to 0-100) when available,
    # fall back to the legacy formula-based vibe_score for tracks the
    # predictor hasn't run against yet.
    vibe_expr = "COALESCE(t.vibe_score_ml * 100.0, t.vibe_score)"
    where = ["ut.user_id = ?", "t.ingestion_status = 'done'",
             f"{vibe_expr} BETWEEN ? AND ?"]
    params: list = [sess["user_id"], vibe_min, vibe_max]
    if mood:
        where.append("t.mood = ?")
        params.append(mood)

    order = "ORDER BY RANDOM()" if shuffle else f"ORDER BY {vibe_expr}"
    params.append(limit)

    conn = db.get_conn()
    try:
        sql = (
            f"SELECT {_track_select(conn)} FROM tracks t "
            f"JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE {' AND '.join(where)} {order} LIMIT ?"
        )
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@router.get("/api/tracks/search")
def search_tracks(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(15, ge=1, le=50),
    sess: dict = Depends(require_user),
):
    """
    Fuzzy search over the caller's analysed library: title, artist, album and
    language by name ("kannada"), tolerant of typos and partial words, every
    query word required. Scoring is in library_search.py. Returns full track
    rows, best first, so results are directly playable.
    """
    query = (q or "").strip()
    if not query:
        return {"tracks": []}

    conn = db.get_conn()
    try:
        hits = _libsearch.search(_search_rows(conn, sess["user_id"]), query, limit)
        if not hits:
            return {"tracks": []}
        ids = [h["id"] for h in hits]
        select = _track_select(conn)
        # Joined again because the narrow rows are cached and per process: a
        # track unlinked on another instance stays in its cache for 60 s.
        rows = conn.execute(
            f"SELECT {select} FROM tracks t JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE ut.user_id = ? AND t.id IN ({','.join('?' * len(ids))})",
            [sess["user_id"], *ids],
        ).fetchall()
    except Exception as e:
        log.warning("search_tracks failed for q=%r: %s", query, e)
        return {"tracks": []}
    finally:
        conn.close()
    by_id = {r["id"]: r for r in rows}
    return {"tracks": [_row_to_dict(by_id[i]) for i in ids if i in by_id]}


@router.get("/api/tracks/random")
def random_track(
    vibe: float = Query(..., ge=0, le=100),
    tolerance: float = 12,
    exclude_ids: Optional[str] = None,
    sess: dict = Depends(require_user),
):
    exclude: list = []
    if exclude_ids:
        for part in exclude_ids.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                exclude.append(int(part))
            except ValueError:
                continue

    conn = db.get_conn()
    try:
        select = _track_select(conn)
        lo, hi = vibe - tolerance, vibe + tolerance
        # Prefer ML-predicted vibe (0-1 -> 0-100), fall back to formula.
        sql = (
            f"SELECT {select} FROM tracks t "
            f"JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE ut.user_id = ? "
            f"AND t.ingestion_status = 'done' "
            f"AND COALESCE(t.vibe_score_ml * 100.0, t.vibe_score) BETWEEN ? AND ?"
        )
        params: list = [sess["user_id"], lo, hi]
        if exclude:
            placeholders = ",".join("?" * len(exclude))
            # Filter by internal tracks.id — frontend now sends t.id in the
            # exclude_ids query param (used to be apple_id).
            sql += f" AND t.id NOT IN ({placeholders})"
            params.extend(exclude)
        sql += " ORDER BY RANDOM() LIMIT 1"
        try:
            row = conn.execute(sql, params).fetchone()
        except Exception:
            row = None
        if row:
            return _row_to_dict(row)
    finally:
        conn.close()

    raise HTTPException(status_code=404, detail={"error": "no tracks in vibe range"})


@router.get("/api/track/{apple_id}/spotify")
def get_track_spotify(apple_id: int, sess: dict = Depends(require_user)):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT t.spotify_id FROM tracks t "
            "JOIN user_tracks ut ON ut.track_id = t.id "
            "WHERE t.apple_id = ? AND ut.user_id = ?",
            (apple_id, sess["user_id"]),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["spotify_id"]:
        raise HTTPException(status_code=404, detail="no spotify id for track")
    return {"spotify_id": row["spotify_id"], "uri": f"spotify:track:{row['spotify_id']}"}


@router.get("/api/tracks/{track_key}/features")
def get_track_features(track_key: str, sess: dict = Depends(require_user)):
    """
    Return the full stored feature blob plus derived axes for a track,
    keyed by spotify_id (string) or numeric internal tracks.id. Per-user.
    """
    conn = db.get_conn()
    try:
        anchor = _resolve_anchor(conn, track_key)
        if not anchor:
            row = None
        else:
            row = conn.execute(
                "SELECT * FROM tracks WHERE id = ?", (int(anchor["id"]),)
            ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="track not found")

    def _val(col):
        try:
            return row[col]
        except (IndexError, KeyError):
            return None

    mfcc_mean = []
    chroma_mean = []
    try:
        if _val("mfcc_json"):
            mfcc_mean = json.loads(row["mfcc_json"])
    except (ValueError, TypeError):
        mfcc_mean = []
    try:
        if _val("chroma_mean_json"):
            chroma_mean = json.loads(row["chroma_mean_json"])
    except (ValueError, TypeError):
        chroma_mean = []

    return {
        "apple_id": _val("apple_id"),
        "spotify_id": _val("spotify_id"),
        "title": _val("title"),
        "artist": _val("artist"),
        "features": {
            "tempo": _val("tempo"),
            "tempo_stability": _val("tempo_stability"),
            "onset_rate": _val("onset_rate"),
            "energy_mean": _val("energy_mean") if _val("energy_mean") is not None else _val("energy"),
            "energy_std": _val("energy_std"),
            "brightness": _val("brightness"),
            "bandwidth": _val("bandwidth"),
            "rolloff": _val("rolloff"),
            "spectral_contrast": _val("spectral_contrast"),
            "flatness": _val("flatness"),
            "zcr": _val("zcr"),
            "timbre_variability": _val("timbre_variability"),
            "valence_mode": _val("valence_mode"),
            "tonnetz_std": _val("tonnetz_std"),
            "acousticness": _val("acousticness"),
            "mfcc_mean": mfcc_mean,
            "chroma_mean": chroma_mean,
        },
        "axes": {
            "activation": _val("activation"),
            "valence": _val("valence"),
            "activation_relative": _val("activation_relative"),
        },
        "mood": _val("mood"),
        "classification_source": _val("classification_source"),
    }


# ---------------- Lyrics ----------------

_LYRICS_COLS = "status, source, synced_lrc, plain, matched_duration_ms, source_id, error, fetched_at"


@router.get("/api/tracks/{track_key}/lyrics")
def get_track_lyrics(track_key: str, sess: dict = Depends(require_user)):
    """
    Lyrics for a track (spotify_id or numeric tracks.id), from the
    track_lyrics cache, else LRCLIB (backend/lyrics.py picks the entry).

        {"status": "synced" | "plain" | "instrumental" | "none",
         "source": "lrclib" | null,
         "lines": [{"t_ms": 12400, "text": "first line"}],   # synced only, else []
         "plain": "full text\\n...",          # null for instrumental / none
         "matched_duration_ms": 229000,       # null for none
         "track_duration_ms": 230000,
         "credit": "Lyrics from LRCLIB"}      # null when source is null

    plus "error": "upstream" when LRCLIB could not be reached; that is not
    an HTTP error, and it is asked again after 10 minutes. A 'none' is asked
    again after 30 days. Blank lines stay in `lines` as "" (gaps). The
    LRCLIB call happens with no DB connection open; it can take ~11 s in
    the worst case (two 5 s timeouts).
    """
    conn = db.get_conn()
    try:
        anchor = _resolve_anchor(conn, track_key)
        track = conn.execute(
            "SELECT id, title, artist, album, duration_ms FROM tracks WHERE id = ?",
            (int(anchor["id"]),),
        ).fetchone() if anchor else None
        if not track:
            raise HTTPException(status_code=404, detail="track not found")
        try:
            cached = conn.execute(
                f"SELECT {_LYRICS_COLS} FROM track_lyrics WHERE track_id = ?", (track["id"],)
            ).fetchone()
        except sqlite3.OperationalError as e:  # Turso before _turso_create_track_lyrics.py
            log.warning("[lyrics] track_lyrics unreadable (%s); not caching", e)
            cached, cache_ok = None, False
        else:
            cache_ok = True
    finally:
        conn.close()

    if cached and _lyrics.is_fresh(cached):
        return _lyrics.response(cached, track["duration_ms"])

    # tracks.artist holds the primary artist only (featured artists live in
    # the title), so it goes to LRCLIB as is.
    found = _lyrics.lookup(track["title"], track["artist"], track["album"], track["duration_ms"])
    row = {**found, "fetched_at": _lyrics.now_ts()}
    if cache_ok:
        conn = db.get_conn()
        try:
            conn.execute_batch([(
                "INSERT INTO track_lyrics (track_id, " + _LYRICS_COLS + ") "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(track_id) DO UPDATE SET status = excluded.status, "
                "source = excluded.source, synced_lrc = excluded.synced_lrc, "
                "plain = excluded.plain, matched_duration_ms = excluded.matched_duration_ms, "
                "source_id = excluded.source_id, error = excluded.error, "
                "fetched_at = excluded.fetched_at",
                (track["id"], row["status"], row["source"], row["synced_lrc"], row["plain"],
                 row["matched_duration_ms"], row["source_id"], row["error"], row["fetched_at"]),
            )])
        except (sqlite3.Error, requests.RequestException) as e:
            # The lyrics are still good; the next request asks again. Turso's
            # transport errors are requests', not sqlite3's (backend/CLAUDE.md).
            log.warning("[lyrics] could not cache track %s: %s", track["id"], e)
        finally:
            conn.close()
    return _lyrics.response(row, track["duration_ms"])
