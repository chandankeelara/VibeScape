"""Unauthenticated read-only endpoints: health, the mood bands, the landing
page's demo moods, and the runtime config the browser fetches at start."""
import os

import bootstrap

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
from routers import Router
from track_rows import MOODS

router = Router()


@router.get("/api/demo/moods")
def demo_moods():
    """
    Public endpoint powering the landing-page hero card. Returns one
    real track per mood band (sleep/chill/steady/hype/beast) from the
    library, picking the track whose vibe score is closest to the
    middle of each band and has a non-empty artwork URL. No auth.
    """
    vibe_expr = "COALESCE(t.vibe_score_ml * 100.0, t.vibe_score)"
    bands = [
        ("sleep",   0.0,  20.0, 10.0),
        ("chill",  20.0,  40.0, 30.0),
        ("steady", 40.0,  60.0, 50.0),
        ("hype",   60.0,  80.0, 70.0),
        ("beast",  80.0, 100.0, 90.0),
    ]
    out = []
    conn = db.get_conn()
    try:
        for mood, lo, hi, target in bands:
            row = conn.execute(
                f"SELECT t.title, t.artist, t.album, t.artwork_url, "
                f"       {vibe_expr} AS v "
                f"FROM tracks t "
                f"WHERE t.ingestion_status = 'done' "
                f"  AND {vibe_expr} >= ? AND {vibe_expr} < ? "
                f"  AND t.artwork_url IS NOT NULL AND t.artwork_url != '' "
                f"ORDER BY ABS({vibe_expr} - ?) LIMIT 1",
                (lo, hi, target),
            ).fetchone()
            if row:
                out.append({
                    "mood": mood,
                    "title": row["title"],
                    "artist": row["artist"],
                    "album": row["album"],
                    "artwork_url": row["artwork_url"],
                    "vibe": int(round(row["v"] or target)),
                })
            else:
                out.append({
                    "mood": mood,
                    "title": None, "artist": None, "album": None,
                    "artwork_url": None, "vibe": int(target),
                })
    finally:
        conn.close()
    return {"moods": out}


@router.get("/api/health")
def health():
    conn = db.get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    except Exception:
        count = 0
    finally:
        conn.close()
    return {"status": "ok", "track_count": count}


@router.get("/api/moods")
def moods():
    return {"moods": MOODS}


@router.get("/api/spotify/config")
def spotify_config():
    client_id, _, redirect_uri = bootstrap.spotify_creds()
    return {"client_id": client_id or "", "redirect_uri": redirect_uri or ""}


@router.get("/api/client-config")
def client_config():
    """
    Runtime config for the browser. Currently just the deployment env,
    used by app.js to decide whether to emit verbose debug logs.
    Defaults to 'prod' so prod stays quiet by default; set
    VIBESCAPE_ENV=dev in a local `.env` to enable debug logs.
    """
    env = (os.environ.get("VIBESCAPE_ENV") or "prod").strip().lower()
    if env not in ("dev", "prod"):
        env = "prod"
    return {"env": env, "debug": env == "dev"}
