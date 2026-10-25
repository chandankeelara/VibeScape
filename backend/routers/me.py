"""The signed-in caller's own data: listening stats, and adding or removing
one track from their library (the player's heart)."""
import json
from datetime import datetime, timezone

import bootstrap  # noqa: F401  — first: .env, sys.path (see bootstrap.py)

from fastapi import Depends, HTTPException, Query

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
from deps import require_user
from routers import Router
from services.vectors import _resolve_anchor
from track_rows import _search_rows_cache

router = Router()


def _library_track_id(conn, track_key: str) -> int:
    anchor = _resolve_anchor(conn, track_key)
    if not anchor:
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})
    return int(anchor["id"])


@router.put("/api/me/library/{track_key}")
def library_link(track_key: str, sess: dict = Depends(require_user)):
    """Put one catalogue track in the caller's library (the player's heart).

    Idempotent: a track already linked keeps its row, its added_at and its
    source. The track need not be analysed; the DJ and the vibe lists pick
    it up once the offline pipeline has marked it done.
    """
    uid = sess["user_id"]
    conn = db.get_conn()
    try:
        tid = _library_track_id(conn, track_key)
        # added_at written, not defaulted: production's tables were not made
        # from schema.sql, and `tracks` there has no defaults (backlog 3.11).
        conn.execute(
            "INSERT OR IGNORE INTO user_tracks (user_id, track_id, added_at, source) "
            "VALUES (?, ?, CURRENT_TIMESTAMP, 'heart')", (uid, tid))
        conn.commit()
    finally:
        conn.close()
    _search_rows_cache.pop(uid, None)
    return {"in_library": True, "track_id": tid}


@router.delete("/api/me/library/{track_key}")
def library_unlink(track_key: str, sess: dict = Depends(require_user)):
    """Take one track out of the caller's library, and nothing else.

    Only the caller's `user_tracks` row goes. The catalogue row, its
    embeddings, and the caller's track_events and user_track_stats stay, for
    the reason ingest_clear gives (backlog 1.10): on Turso they cascade from
    `tracks`, and history is the source of truth. Idempotent.

    The DJ and every track list read `user_tracks` per request, so the next
    one no longer offers the track. user_embedding_mean is not touched: it
    is rebuilt past 5% drift in the library count, and one track is inside
    that from the 50-track floor up (below it the global mean is used).
    """
    uid = sess["user_id"]
    conn = db.get_conn()
    try:
        tid = _library_track_id(conn, track_key)
        conn.execute("DELETE FROM user_tracks WHERE user_id = ? AND track_id = ?", (uid, tid))
        conn.commit()
    finally:
        conn.close()
    _search_rows_cache.pop(uid, None)
    return {"in_library": False, "track_id": tid}


# ---------------- Listening stats for the signed-in user ----------------
#
# Read-only, computed on request from user_track_stats (the all-time totals,
# already maintained by POST /api/events) and from a bounded window of raw
# track_events / user_events (anything that needs time of day or local
# calendar days). Nothing here writes.
#
# "Listened" is listened_ms where the client sent it (telemetry v2,
# 2026-10-10) and the playhead before that, exactly as total_listened_ms.
# Local time uses each event's own tz_offset_min, else the user's most recent
# one, else UTC.

_STATS_WINDOW_DAYS_MAX = 365


def _ts_utc(value):
    """server_ts ('YYYY-MM-DD HH:MM:SS', UTC) -> aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@router.get("/api/me/stats")
def my_listening_stats(days: int = Query(30, ge=1, le=_STATS_WINDOW_DAYS_MAX),
                       sess: dict = Depends(require_user)):
    """Listening metrics for the caller.

    all_time    from user_track_stats: hours listened, plays, completion and
                skip rates, mean skip position, top tracks and artists by
                time listened, last played.
    today / last_7_days / window (the last `days` days, default 30)
                from track_events: hours listened (local calendar day for
                today), hours by local hour of day, time per playback kind,
                and sessions from user_events.
    """
    from datetime import timedelta

    uid = int(sess["user_id"])
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=max(days, 7))).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    try:
        tot = conn.execute(
            "SELECT COALESCE(SUM(total_listened_ms), 0) AS listened, "
            "       COALESCE(SUM(play_count), 0) AS plays, "
            "       COALESCE(SUM(u_complete_count + s_complete_count), 0) AS completed, "
            "       COALESCE(SUM(u_skip_count + s_skip_count), 0) AS skipped, "
            "       COALESCE(SUM(u_end_count + s_end_count), 0) AS ended, "
            "       COALESCE(SUM(u_skip_position_ms_sum + s_skip_position_ms_sum), 0) AS skip_pos, "
            "       COUNT(*) AS distinct_tracks "
            "FROM user_track_stats WHERE user_id = ?", (uid,)).fetchone()

        top_tracks = conn.execute(
            "SELECT t.id, t.title, t.artist, s.total_listened_ms AS listened, s.play_count AS plays "
            "FROM user_track_stats s JOIN tracks t ON t.id = s.track_id "
            "WHERE s.user_id = ? AND s.total_listened_ms > 0 "
            "ORDER BY s.total_listened_ms DESC LIMIT 10", (uid,)).fetchall()
        top_artists = conn.execute(
            "SELECT t.artist, SUM(s.total_listened_ms) AS listened, SUM(s.play_count) AS plays "
            "FROM user_track_stats s JOIN tracks t ON t.id = s.track_id "
            "WHERE s.user_id = ? AND s.total_listened_ms > 0 AND t.artist IS NOT NULL "
            "GROUP BY t.artist ORDER BY listened DESC LIMIT 10", (uid,)).fetchall()
        last = conn.execute(
            "SELECT t.id, t.title, t.artist, s.last_played "
            "FROM user_track_stats s JOIN tracks t ON t.id = s.track_id "
            "WHERE s.user_id = ? AND s.last_played IS NOT NULL "
            "ORDER BY s.last_played DESC LIMIT 1", (uid,)).fetchone()

        ends = conn.execute(
            "SELECT server_ts, tz_offset_min, playback, "
            "       COALESCE(listened_ms, position_ms, 0) AS heard "
            "FROM track_events WHERE user_id = ? AND type = 'play_end' AND server_ts >= ?",
            (uid, since)).fetchall()
        tz_row = conn.execute(
            "SELECT tz_offset_min FROM track_events WHERE user_id = ? AND tz_offset_min IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        try:
            sessions = conn.execute(
                "SELECT data FROM user_events WHERE user_id = ? AND type = 'session_end' "
                "AND server_ts >= ?", (uid, since)).fetchall()
        except Exception:
            sessions = []  # user_events not created yet (Turso before the v2 migration)
    finally:
        conn.close()

    user_tz = int(tz_row["tz_offset_min"]) if tz_row and tz_row["tz_offset_min"] is not None else 0
    local_today = (now + timedelta(minutes=user_tz)).date()
    window_start = now - timedelta(days=days)
    week_start = now - timedelta(days=7)
    today_ms = week_ms = window_ms = 0
    by_hour = [0] * 24
    by_playback: dict = {}
    for r in ends:
        at = _ts_utc(r["server_ts"])
        if at is None:
            continue
        heard = int(r["heard"] or 0)
        off = r["tz_offset_min"] if r["tz_offset_min"] is not None else user_tz
        local = at + timedelta(minutes=int(off))
        if local.date() == local_today:
            today_ms += heard
        if at >= week_start:
            week_ms += heard
        if at >= window_start:
            window_ms += heard
            by_hour[local.hour] += heard
            k = r["playback"] or "unknown"
            by_playback[k] = by_playback.get(k, 0) + heard

    durations = []
    for r in sessions:
        try:
            d = json.loads(r["data"] or "{}").get("duration_ms")
        except (TypeError, ValueError):
            d = None
        if isinstance(d, (int, float)) and d >= 0:
            durations.append(int(d))

    ended = int(tot["ended"] or 0)
    skipped = int(tot["skipped"] or 0)
    return {
        "all_time": {
            "listened_ms": int(tot["listened"] or 0),
            "plays": int(tot["plays"] or 0),
            "distinct_tracks": int(tot["distinct_tracks"] or 0),
            "completion_rate": round(int(tot["completed"] or 0) / ended, 4) if ended else None,
            "skip_rate": round(skipped / ended, 4) if ended else None,
            "mean_skip_position_ms": int(tot["skip_pos"] or 0) // skipped if skipped else None,
            "top_tracks": [{"id": int(r["id"]), "title": r["title"], "artist": r["artist"],
                            "listened_ms": int(r["listened"]), "plays": int(r["plays"])}
                           for r in top_tracks],
            "top_artists": [{"artist": r["artist"], "listened_ms": int(r["listened"]),
                             "plays": int(r["plays"])} for r in top_artists],
            "last_played": ({"id": int(last["id"]), "title": last["title"],
                             "artist": last["artist"], "at": last["last_played"]} if last else None),
        },
        "today_listened_ms": today_ms,
        "last_7_days_listened_ms": week_ms,
        "window": {
            "days": days,
            "listened_ms": window_ms,
            "by_local_hour_ms": by_hour,
            "by_playback_ms": by_playback,
            "sessions": len(durations),
            "mean_session_ms": (sum(durations) // len(durations)) if durations else None,
        },
        "tz_offset_min": user_tz,
    }
