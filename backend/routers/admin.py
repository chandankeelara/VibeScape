"""Admin-only endpoints (require_admin): the user list, one user's stats and
tracks, deleting a user, and the catalogue-wide score recompute.

The admin is deps.ADMIN_USER_ID, read at call time because tests patch it."""
from bootstrap import log  # first: .env, sys.path (see bootstrap.py)

from fastapi import Depends, HTTPException, Query, Response

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
import deps
from deps import require_admin
from routers import Router

import score_recompute  # ingest/, on sys.path through bootstrap

router = Router()


@router.post("/api/recompute-scores")
def api_recompute_scores(sess: dict = Depends(require_admin)):
    """
    Re-run scoring.compute_axes over the WHOLE catalogue from persisted
    features, z-score activation into activation_relative and mirror it into
    vibe_score (ingest/score_recompute.py). GLOBAL, not per-user: tracks are
    shared, so this rewrites rows every user sees — hence admin-only.
    No audio re-analysis; rows whose scores are unchanged are not written.
    The offline runner does the same after a pass that scored tracks.
    """
    conn = db.get_conn()
    try:
        summary = score_recompute.recompute_axes_and_zscores(conn)
    finally:
        conn.close()
    return summary


# ---------------- Admin (chandan-only) ----------------
@router.get("/api/admin/users")
def admin_list_users(sess: dict = Depends(require_admin)):
    conn = db.get_conn()
    try:
        rows = conn.execute(
            """
            SELECT u.id, u.display_name, u.spotify_user_id, u.spotify_display_name,
                   u.spotify_email, u.spotify_country, u.spotify_product,
                   u.spotify_avatar_url, u.created_at, u.last_login_at,
                   COUNT(ut.track_id) AS track_count
            FROM users u
            LEFT JOIN user_tracks ut ON ut.user_id = u.id
            GROUP BY u.id
            ORDER BY u.id
            """
        ).fetchall()
    finally:
        conn.close()
    return {
        "users": [
            {
                "user_id": int(r["id"]),
                "display_name": r["display_name"],
                "spotify_user_id": r["spotify_user_id"],
                "spotify_display_name": r["spotify_display_name"],
                "spotify_email": r["spotify_email"],
                "spotify_country": r["spotify_country"],
                "spotify_product": r["spotify_product"],
                "avatar_url": r["spotify_avatar_url"],
                "created_at": r["created_at"],
                "last_login_at": r["last_login_at"],
                "track_count": int(r["track_count"] or 0),
                "is_admin": int(r["id"]) == deps.ADMIN_USER_ID,
                "is_guest": r["display_name"] == "Guest",
            }
            for r in rows
        ]
    }


@router.get("/api/admin/users/{user_id}/stats")
def admin_user_stats(user_id: int, sess: dict = Depends(require_admin)):
    conn = db.get_conn()
    try:
        u = conn.execute(
            "SELECT id, display_name, spotify_display_name, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not u:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})

        total = conn.execute(
            "SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        by_mood = conn.execute(
            """
            SELECT t.mood, COUNT(*) AS n
            FROM user_tracks ut
            JOIN tracks t ON t.id = ut.track_id
            WHERE ut.user_id = ?
            GROUP BY t.mood
            ORDER BY n DESC
            """,
            (user_id,),
        ).fetchall()

        by_source = conn.execute(
            """
            SELECT t.classification_source, COUNT(*) AS n
            FROM user_tracks ut
            JOIN tracks t ON t.id = ut.track_id
            WHERE ut.user_id = ?
            GROUP BY t.classification_source
            ORDER BY n DESC
            """,
            (user_id,),
        ).fetchall()

        top_artists = conn.execute(
            """
            SELECT t.artist, COUNT(*) AS n
            FROM user_tracks ut
            JOIN tracks t ON t.id = ut.track_id
            WHERE ut.user_id = ?
            GROUP BY t.artist
            ORDER BY n DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()

        vibe_stats = conn.execute(
            """
            SELECT AVG(t.vibe_score_ml) AS avg_ml, AVG(t.activation) AS avg_act,
                   AVG(t.valence) AS avg_val
            FROM user_tracks ut
            JOIN tracks t ON t.id = ut.track_id
            WHERE ut.user_id = ?
            """,
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    return {
        "user_id": int(u["id"]),
        "display_name": u["display_name"],
        "spotify_display_name": u["spotify_display_name"],
        "created_at": u["created_at"],
        "track_count": int(total or 0),
        "by_mood": [{"mood": r["mood"] or "unknown", "count": int(r["n"])} for r in by_mood],
        "by_source": [
            {"source": r["classification_source"] or "unknown", "count": int(r["n"])}
            for r in by_source
        ],
        "top_artists": [{"artist": r["artist"], "count": int(r["n"])} for r in top_artists],
        "avg_vibe_ml": float(vibe_stats["avg_ml"]) if vibe_stats["avg_ml"] is not None else None,
        "avg_activation": float(vibe_stats["avg_act"]) if vibe_stats["avg_act"] is not None else None,
        "avg_valence": float(vibe_stats["avg_val"]) if vibe_stats["avg_val"] is not None else None,
    }


@router.get("/api/admin/users/{user_id}/tracks")
def admin_user_tracks(
    user_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sess: dict = Depends(require_admin),
):
    conn = db.get_conn()
    try:
        # 'last_played' in the response is MAX(last_played, last_skipped_at) --
        # the DB column 'last_played' now means 'qualified listen' only (play_end
        # completed OR position_ms >= DJ_QUALIFIED_PLAY_MS); the UI wants 'last
        # time the user saw this track', which is either event.
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.artist, t.album, t.mood, t.vibe_score_ml,
                   t.classification_source, ut.added_at,
                   COALESCE(uts.play_count, 0) AS play_count,
                   NULLIF(MAX(COALESCE(uts.last_played, ''),
                              COALESCE(uts.last_skipped_at, '')), '') AS last_shown_at
            FROM user_tracks ut
            JOIN tracks t ON t.id = ut.track_id
            LEFT JOIN user_track_stats uts
                   ON uts.user_id = ut.user_id AND uts.track_id = ut.track_id
            WHERE ut.user_id = ?
            ORDER BY ut.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return {
        "tracks": [
            {
                "id": int(r["id"]),
                "title": r["title"],
                "artist": r["artist"],
                "album": r["album"],
                "mood": r["mood"],
                "vibe_score_ml": r["vibe_score_ml"],
                "classification_source": r["classification_source"],
                "added_at": r["added_at"],
                # play_count / last_played now come from user_track_stats, the
                # materialised aggregate of track_events. The identically named
                # user_tracks columns are the dead pre-telemetry originals —
                # SUM(play_count) over them is 0 and always has been, because
                # nothing ever wrote them. See docs/backend-todo.md.
                "play_count": int(r["play_count"] or 0),
                # Response field name preserved (frontend contract). Value is now
                # MAX(uts.last_played, uts.last_skipped_at) — see SELECT comment.
                "last_played": r["last_shown_at"],
            }
            for r in rows
        ]
    }


@router.delete("/api/admin/users/{user_id}", status_code=204)
def admin_delete_user(user_id: int, sess: dict = Depends(require_admin)):
    # Guardrail: admin can't delete themselves.
    if int(user_id) == deps.ADMIN_USER_ID:
        raise HTTPException(status_code=400, detail={"error": "cannot_delete_admin"})
    conn = db.get_conn()
    try:
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
        # Cascade manually so we don't rely on PRAGMA foreign_keys being on
        # (SQLite disables it by default per-connection).
        conn.execute("DELETE FROM user_tracks WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        log.info("[admin] user=%s deleted user_id=%s", sess["user_id"], user_id)
    finally:
        conn.close()
    return Response(status_code=204)
