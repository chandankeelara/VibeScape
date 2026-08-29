import glob
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Load .env before any env-var reads (config.py, ADMIN_USER_ID, VIBESCAPE_ML_MODE).
# No-op on platforms where python-dotenv isn't installed or no .env exists.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import asyncio
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import ensure_db, get_conn

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ingest"))
try:
    import config as app_config
except Exception:
    app_config = None

import deezer_client  # noqa: E402
import itunes_client  # noqa: E402
import ml_backend  # noqa: E402
import scoring  # noqa: E402
import spotify_library as splib  # noqa: E402
import spotify_matcher  # noqa: E402

# `features` pulls in librosa/numpy — heavy deps not needed in the
# playback-only prod deployment. Import lazily at call sites that need it
# (only the ingest hot path, and only when Modal ML backend is unavailable).
feat = None

log = logging.getLogger("vibescape")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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
]
TRACK_SELECT = ", ".join(TRACK_COLUMNS)

MOODS = list(scoring.MOODS)

AUDIO_DIR = PROJECT_ROOT / "data" / "audio"

app = FastAPI(title="VibeScape API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    ensure_db()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    _log_lan_bind_info()


def _lan_ip() -> Optional[str]:
    """Best-effort local LAN IP so the operator knows where phones should point."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None


def _log_lan_bind_info() -> None:
    ip = _lan_ip()
    port = os.environ.get("VIBESCAPE_PORT", "8000")
    log.info("VibeScape backend listening on http://0.0.0.0:%s", port)
    if ip:
        log.info("LAN URL (share with phones on same WiFi): http://%s:%s/", ip, port)


# ---------------- Auth: users + sessions ----------------


def _hash_pin(pin: str) -> str:
    """
    Salted scrypt hash for a user PIN. Format: 'scrypt$<hex_salt>$<hex_hash>'.
    Cheap enough for LAN, hard enough to brute-force via network.
    """
    if not pin:
        return ""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + dk.hex()


def _verify_pin(pin: str, stored: Optional[str]) -> bool:
    if not stored:
        # user has no PIN configured -> any/empty PIN accepted
        return True
    if not pin:
        return False
    try:
        scheme, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    dk = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(dk, expected)


def _issue_session(conn, user_id: int) -> str:
    token = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
        (token, user_id),
    )
    conn.commit()
    return token


def _lookup_session(conn, token: str) -> Optional[dict]:
    if not token:
        return None
    row = conn.execute(
        "SELECT s.token, s.user_id, u.display_name, u.pin_hash, u.spotify_user_id, u.spotify_display_name, u.created_at "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    ).fetchone()
    if not row:
        return None
    # bump last_used_at, best-effort
    try:
        conn.execute("UPDATE sessions SET last_used_at = CURRENT_TIMESTAMP WHERE token = ?", (token,))
        conn.commit()
    except Exception:
        pass
    return dict(row)


def _extract_bearer(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    tok = parts[1].strip()
    return tok or None


def require_user(request: Request, authorization: Optional[str] = Header(None)) -> dict:
    """
    FastAPI dependency. Returns the auth session dict {user_id, display_name, ...}
    or raises 401. Attaches user_id onto request.state for logging.
    """
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_authorization"})
    conn = get_conn()
    try:
        sess = _lookup_session(conn, token)
    finally:
        conn.close()
    if not sess:
        raise HTTPException(status_code=401, detail={"error": "invalid_session"})
    request.state.user_id = sess["user_id"]
    request.state.session_token = token
    return sess


def require_user_stream(request: Request, authorization: Optional[str] = Header(None)) -> dict:
    """
    Stream-only variant of require_user. Accepts EITHER:
      * Authorization: Bearer <session_token>  (normal path, curl/tooling)
      * ?token=<session_token>                 (fallback for <audio>, which
                                                cannot set custom headers)
    Do NOT use this on any endpoint other than /api/stream/*. The query-string
    fallback is a controlled compromise for browser <audio> tags — other
    endpoints should keep header-only auth so tokens don't leak into
    server logs / referer / URL-shaped caches.
    """
    token = _extract_bearer(authorization)
    if not token:
        token = (request.query_params.get("token") or "").strip() or None
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_authorization"})
    conn = get_conn()
    try:
        sess = _lookup_session(conn, token)
    finally:
        conn.close()
    if not sess:
        raise HTTPException(status_code=401, detail={"error": "invalid_session"})
    request.state.user_id = sess["user_id"]
    request.state.session_token = token
    return sess


class SignupBody(BaseModel):
    display_name: str
    pin: Optional[str] = None


class LoginBody(BaseModel):
    user_id: int
    pin: Optional[str] = None


class SetPinBody(BaseModel):
    # new_pin: 4 digits. Set to null/empty to remove the PIN.
    new_pin: Optional[str] = None
    # current_pin required if the user already has one (auth to change).
    current_pin: Optional[str] = None


class SpotifyLinkBody(BaseModel):
    spotify_user_id: str
    spotify_display_name: Optional[str] = None


@app.get("/api/users")
def list_users():
    """Public: profile picker. Does NOT expose pin_hash."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, display_name, pin_hash, spotify_display_name "
            "FROM users ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "user_id": r["id"],
            "display_name": r["display_name"],
            "has_pin": bool(r["pin_hash"]),
            "spotify_display_name": r["spotify_display_name"],
        }
        for r in rows
    ]


@app.post("/api/auth/signup")
def auth_signup(body: SignupBody):
    name = (body.display_name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail={"error": "display_name required"})
    pin_hash = _hash_pin(body.pin) if body.pin else None
    conn = get_conn()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO users (display_name, pin_hash) VALUES (?, ?)",
                (name, pin_hash),
            )
            conn.commit()
            user_id = int(cur.lastrowid)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail={"error": "display_name_taken"})
        token = _issue_session(conn, user_id)
    finally:
        conn.close()
    return {
        "user_id": user_id,
        "display_name": name,
        "session_token": token,
        "has_pin": bool(pin_hash),
    }


@app.post("/api/auth/login")
def auth_login(body: LoginBody):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, display_name, pin_hash FROM users WHERE id = ?",
            (body.user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
        if row["pin_hash"] and not _verify_pin(body.pin or "", row["pin_hash"]):
            raise HTTPException(status_code=401, detail={"error": "bad_pin"})
        token = _issue_session(conn, int(row["id"]))
    finally:
        conn.close()
    return {
        "user_id": int(row["id"]),
        "display_name": row["display_name"],
        "session_token": token,
        "has_pin": bool(row["pin_hash"]),
        "is_admin": int(row["id"]) == ADMIN_USER_ID,
    }


@app.post("/api/auth/logout", status_code=204)
def auth_logout(sess: dict = Depends(require_user)):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (sess["token"],))
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


@app.get("/api/auth/me")
def auth_me(sess: dict = Depends(require_user)):
    return {
        "user_id": sess["user_id"],
        "display_name": sess["display_name"],
        "has_pin": bool(sess.get("pin_hash")),
        "spotify_connected": bool(sess.get("spotify_user_id")),
        "spotify_display_name": sess.get("spotify_display_name"),
        "created_at": sess.get("created_at"),
        "is_admin": int(sess["user_id"]) == ADMIN_USER_ID,
    }


@app.post("/api/auth/spotify-link")
def auth_spotify_link(body: SpotifyLinkBody, sess: dict = Depends(require_user)):
    if not body.spotify_user_id:
        raise HTTPException(status_code=422, detail={"error": "spotify_user_id required"})
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET spotify_user_id = ?, spotify_display_name = ? WHERE id = ?",
            (body.spotify_user_id, body.spotify_display_name, sess["user_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "spotify_user_id": body.spotify_user_id, "spotify_display_name": body.spotify_display_name}


def _row_to_dict(row):
    out = {}
    for col in TRACK_COLUMNS:
        try:
            out[col] = row[col]
        except (IndexError, KeyError):
            out[col] = None
    # frontend slider works against vibe_score, but callers may prefer
    # activation_relative once library z-scores exist. Expose both; when
    # activation_relative is populated, mirror it into vibe_score for
    # backward-compat filter queries.
    return out


@app.get("/api/health")
def health():
    conn = get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    except Exception:
        count = 0
    finally:
        conn.close()
    return {"status": "ok", "track_count": count}


@app.get("/api/moods")
def moods():
    return {"moods": MOODS}


@app.get("/api/tracks")
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
    where = ["ut.user_id = ?", f"{vibe_expr} BETWEEN ? AND ?"]
    params: list = [sess["user_id"], vibe_min, vibe_max]
    if mood:
        where.append("t.mood = ?")
        params.append(mood)

    order = "ORDER BY RANDOM()" if shuffle else f"ORDER BY {vibe_expr}"
    select = ", ".join(f"t.{c}" for c in TRACK_COLUMNS)
    sql = (
        f"SELECT {select} FROM tracks t "
        f"JOIN user_tracks ut ON ut.track_id = t.id "
        f"WHERE {' AND '.join(where)} {order} LIMIT ?"
    )
    params.append(limit)

    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@app.get("/api/tracks/search")
def search_tracks(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(15, ge=1, le=50),
    sess: dict = Depends(require_user),
):
    """
    Case-insensitive substring search over the caller's library, matching
    title, artist, or album. Ordered by title prefix hit first, then
    generic hits. Returns full track rows so results are directly playable.
    """
    query = (q or "").strip()
    if not query:
        return {"tracks": []}

    like = f"%{query}%"
    prefix = f"{query}%"
    select = ", ".join(f"t.{c}" for c in TRACK_COLUMNS)
    sql = (
        f"SELECT {select} FROM tracks t "
        f"JOIN user_tracks ut ON ut.track_id = t.id "
        f"WHERE ut.user_id = ? "
        f"AND (t.title LIKE ? COLLATE NOCASE "
        f"     OR t.artist LIKE ? COLLATE NOCASE "
        f"     OR t.album LIKE ? COLLATE NOCASE) "
        f"ORDER BY "
        f"  CASE WHEN t.title LIKE ? COLLATE NOCASE THEN 0 "
        f"       WHEN t.artist LIKE ? COLLATE NOCASE THEN 1 "
        f"       ELSE 2 END, "
        f"  t.title COLLATE NOCASE "
        f"LIMIT ?"
    )
    params = [sess["user_id"], like, like, like, prefix, prefix, limit]

    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        log.warning("search_tracks failed for q=%r: %s", query, e)
        rows = []
    finally:
        conn.close()
    return {"tracks": [_row_to_dict(r) for r in rows]}


@app.get("/api/tracks/{track_key}/similar")
def similar_tracks(
    track_key: str,
    limit: int = Query(8, ge=1, le=25),
    sess: dict = Depends(require_user),
):
    """
    Return N tracks from the caller's library most similar to the given
    track. Similarity = weighted L1 distance across the four ML feature
    dimensions (vibe_score_ml, energy_pred, danceability_pred,
    valence_pred), with a small bonus for matching mood. The current
    track is excluded from results.

    track_key can be a spotify_id (string) or an apple_id (numeric string).
    """
    conn = get_conn()
    try:
        anchor = None
        try:
            apple_id_int = int(track_key)
            anchor = conn.execute(
                "SELECT id, apple_id, spotify_id, mood, "
                "vibe_score, vibe_score_ml, energy_pred, "
                "danceability_pred, valence_pred "
                "FROM tracks WHERE apple_id = ?",
                (apple_id_int,),
            ).fetchone()
        except ValueError:
            pass
        if not anchor:
            anchor = conn.execute(
                "SELECT id, apple_id, spotify_id, mood, "
                "vibe_score, vibe_score_ml, energy_pred, "
                "danceability_pred, valence_pred "
                "FROM tracks WHERE spotify_id = ?",
                (track_key,),
            ).fetchone()
        if not anchor:
            raise HTTPException(status_code=404, detail={"error": "track_not_found"})

        # Anchor features. If ML predictions haven't run for this track yet,
        # fall back to the formula vibe (0-100 scaled to 0-1) so we still
        # get sensible ordering. Missing prediction cols become 0.5 (neutral).
        def _norm(v, default):
            if v is None:
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        a_vibe = _norm(anchor["vibe_score_ml"], _norm(anchor["vibe_score"], 50.0) / 100.0)
        a_energy = _norm(anchor["energy_pred"], 0.5)
        a_dance = _norm(anchor["danceability_pred"], 0.5)
        a_valence = _norm(anchor["valence_pred"], 0.5)
        a_mood = anchor["mood"] or ""

        # Weighted L1 in SQL. Weights emphasise the ML vibe (most user-visible
        # signal), then energy/danceability, then valence. Missing prediction
        # columns coalesce to 0.5 so those rows land in the middle of the
        # ranking rather than being excluded entirely. Mood match gives a
        # discount of 0.15 (roughly one dimension's worth of distance) to
        # nudge same-mood tracks up the list.
        select = ", ".join(f"t.{c}" for c in TRACK_COLUMNS)
        sql = f"""
            SELECT {select},
              (
                1.5 * ABS(COALESCE(t.vibe_score_ml,        {a_vibe})    - {a_vibe})
              + 1.0 * ABS(COALESCE(t.energy_pred,         0.5)          - {a_energy})
              + 1.0 * ABS(COALESCE(t.danceability_pred,   0.5)          - {a_dance})
              + 0.8 * ABS(COALESCE(t.valence_pred,        0.5)          - {a_valence})
              - CASE WHEN t.mood = ? THEN 0.15 ELSE 0.0 END
              ) AS distance
            FROM tracks t
            JOIN user_tracks ut ON ut.track_id = t.id
            WHERE ut.user_id = ?
              AND t.id != ?
            ORDER BY distance ASC, t.title COLLATE NOCASE
            LIMIT ?
        """
        rows = conn.execute(sql, (a_mood, sess["user_id"], anchor["id"], limit)).fetchall()
    finally:
        conn.close()

    return {
        "anchor": {
            "spotify_id": anchor["spotify_id"],
            "apple_id": anchor["apple_id"],
            "mood": a_mood,
        },
        "tracks": [_row_to_dict(r) for r in rows],
    }


@app.get("/api/tracks/random")
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

    conn = get_conn()
    try:
        select = ", ".join(f"t.{c}" for c in TRACK_COLUMNS)
        for attempt in range(2):
            tol = tolerance + (10 if attempt else 0)
            lo, hi = vibe - tol, vibe + tol
            # Prefer ML-predicted vibe (0-1 -> 0-100), fall back to formula.
            sql = (
                f"SELECT {select} FROM tracks t "
                f"JOIN user_tracks ut ON ut.track_id = t.id "
                f"WHERE ut.user_id = ? "
                f"AND COALESCE(t.vibe_score_ml * 100.0, t.vibe_score) BETWEEN ? AND ?"
            )
            params: list = [sess["user_id"], lo, hi]
            if exclude:
                placeholders = ",".join("?" * len(exclude))
                sql += f" AND t.apple_id NOT IN ({placeholders})"
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


@app.get("/api/spotify/config")
def spotify_config():
    client_id = getattr(app_config, "SPOTIFY_CLIENT_ID", "") if app_config else ""
    redirect_uri = getattr(app_config, "SPOTIFY_REDIRECT_URI", "") if app_config else ""
    return {"client_id": client_id or "", "redirect_uri": redirect_uri or ""}


@app.get("/api/client-config")
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


@app.get("/api/track/{apple_id}/spotify")
def get_track_spotify(apple_id: int, sess: dict = Depends(require_user)):
    conn = get_conn()
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


_CALLBACK_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>VibeScape - Spotify</title>
<style>body{background:#08080c;color:#f2f2f5;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}</style>
</head><body>
<div>
  <p id="msg">Signing you in…</p>
</div>
<noscript>
  <meta http-equiv="refresh" content="0;url=/">
</noscript>
<script>
(function(){
  try {
    var params = new URLSearchParams(window.location.search);
    var code = params.get('code');
    var error = params.get('error');
    var state = params.get('state');
    var payload = {
      code: code || null,
      error: error || null,
      state: state || null,
      ts: Date.now()
    };
    try { localStorage.setItem('spotify_pending_auth', JSON.stringify(payload)); } catch(e){}
    var msgEl = document.getElementById('msg');
    var isPopup = !!window.opener || window.name === 'vibescape-oauth-popup';
    if (isPopup) {
      if (msgEl) msgEl.textContent = 'Signed in. Closing this window…';
      try { window.opener && window.opener.focus(); } catch(e){}
      setTimeout(function(){ try { window.close(); } catch(e){} }, 80);
    } else {
      if (msgEl) msgEl.textContent = 'Signed in. Redirecting…';
      var out = new URLSearchParams();
      if (code) out.set('spotify_code', code);
      if (error) out.set('spotify_error', error);
      if (state) out.set('spotify_state', state);
      var qs = out.toString();
      window.location.replace('/' + (qs ? ('?' + qs) : ''));
    }
  } catch(e) {
    var el = document.getElementById('msg');
    if (el) el.textContent = 'Auth error: ' + e.message;
  }
})();
</script>
</body></html>
"""


@app.get("/callback")
def spotify_callback():
    return HTMLResponse(_CALLBACK_HTML)


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _resolve_audio_path(audio_path: str) -> Optional[Path]:
    if not audio_path:
        return None
    p = Path(audio_path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    try:
        p = p.resolve()
    except OSError:
        return None
    try:
        p.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return None
    return p


def _stream_audio_row(row, request: Request):
    if not row or not row["audio_path"]:
        raise HTTPException(status_code=404, detail="audio not found")

    audio_file = _resolve_audio_path(row["audio_path"])
    if not audio_file or not audio_file.exists() or not audio_file.is_file():
        raise HTTPException(status_code=404, detail="audio file missing on disk")

    file_size = audio_file.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    ext = audio_file.suffix.lower()
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/mp4"

    common_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
        "Content-Type": media_type,
    }

    if range_header:
        m = _RANGE_RE.match(range_header.strip())
        if not m:
            raise HTTPException(status_code=416, detail="invalid range")
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "" and end_s == "":
            raise HTTPException(status_code=416, detail="invalid range")
        if start_s == "":
            suffix = int(end_s)
            if suffix <= 0:
                raise HTTPException(status_code=416, detail="invalid range")
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            headers = {"Content-Range": f"bytes */{file_size}"}
            return Response(status_code=416, headers=headers)

        length = end - start + 1

        def _iter_range(path: Path, offset: int, remaining: int, chunk: int = 64 * 1024):
            with open(path, "rb") as f:
                f.seek(offset)
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            _iter_range(audio_file, start, length),
            status_code=206,
            headers=headers,
            media_type=media_type,
        )

    def _iter_full(path: Path, chunk: int = 64 * 1024):
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                yield data

    headers = {
        **common_headers,
        "Content-Length": str(file_size),
    }
    return StreamingResponse(
        _iter_full(audio_file),
        status_code=200,
        headers=headers,
        media_type=media_type,
    )


@app.get("/api/stream/spotify/{spotify_id}")
def stream_track_by_spotify(spotify_id: str, request: Request, sess: dict = Depends(require_user_stream)):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT audio_path FROM tracks WHERE spotify_id = ?",
            (spotify_id,),
        ).fetchone()
    finally:
        conn.close()
    return _stream_audio_row(row, request)


@app.get("/api/stream/{track_key}")
def stream_track(track_key: str, request: Request, sess: dict = Depends(require_user_stream)):
    conn = get_conn()
    try:
        row = None
        try:
            apple_id_int = int(track_key)
            row = conn.execute(
                "SELECT audio_path FROM tracks WHERE apple_id = ?",
                (apple_id_int,),
            ).fetchone()
        except ValueError:
            pass
        if not row:
            row = conn.execute(
                "SELECT audio_path FROM tracks WHERE spotify_id = ?",
                (track_key,),
            ).fetchone()
    finally:
        conn.close()
    return _stream_audio_row(row, request)


# ---------------- Feature reconstitution + scoring helpers ----------------


_FEATURE_SCALARS = [
    "tempo",
    "tempo_stability",
    "onset_rate",
    "energy_mean",
    "energy_std",
    "brightness",
    "bandwidth",
    "rolloff",
    "spectral_contrast",
    "flatness",
    "zcr",
    "timbre_variability",
    "valence_mode",
    "tonnetz_std",
    "acousticness",
]


def _row_features(row) -> dict:
    """Rebuild a feature-dict shape from a DB row (for scoring / API responses)."""
    f: dict = {}
    for k in _FEATURE_SCALARS:
        try:
            f[k] = row[k]
        except (IndexError, KeyError):
            f[k] = None
    # Legacy alias support
    try:
        if f.get("energy_mean") is None:
            f["energy_mean"] = row["energy"]
    except (IndexError, KeyError):
        pass
    try:
        mfcc_json = row["mfcc_json"]
        f["mfcc_mean"] = json.loads(mfcc_json) if mfcc_json else []
    except (IndexError, KeyError, ValueError, TypeError):
        f["mfcc_mean"] = []
    try:
        chroma_json = row["chroma_mean_json"]
        f["chroma_mean"] = json.loads(chroma_json) if chroma_json else []
    except (IndexError, KeyError, ValueError, TypeError):
        f["chroma_mean"] = []
    return f


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _recompute_axes_and_zscores(conn, user_id: Optional[int] = None) -> dict:
    """
    Recompute activation/valence from stored features for every track,
    then z-score-normalize activation across the global library and mirror
    into vibe_score. Since tracks are now global (song-level truth), the
    z-score baseline is shared across users; the user_id parameter is
    accepted for backward-compat but ignored.
    """
    del user_id
    base_select = ("SELECT id, tempo, energy, energy_mean, energy_std, brightness, "
                   "tempo_stability, onset_rate, bandwidth, rolloff, "
                   "spectral_contrast, flatness, zcr, timbre_variability, "
                   "valence_mode, tonnetz_std, acousticness, mfcc_json, "
                   "chroma_mean_json FROM tracks")
    rows = conn.execute(base_select).fetchall()

    activations: list[float] = []
    per_row: list[tuple[int, float, float]] = []
    for row in rows:
        f = _row_features(row)
        # If no scalars at all, skip (leaves existing values intact).
        if all((f.get(k) is None) for k in _FEATURE_SCALARS):
            continue
        axes = scoring.compute_axes(f)
        activation = axes["activation"]
        valence = axes["valence"]
        activations.append(activation)
        per_row.append((row["id"], activation, valence))

    if not per_row:
        return {"updated": 0, "activation_stats": None, "mood_distribution": {}}

    import statistics as _stats
    mean = float(_stats.fmean(activations))
    std = float(_stats.pstdev(activations)) if len(activations) > 1 else 0.0
    mn = float(min(activations))
    mx = float(max(activations))

    mood_counts: dict[str, int] = {}
    for row_id, activation, valence in per_row:
        if std > 1e-9:
            rel = 50.0 + ((activation - mean) / std) * 15.0
        else:
            rel = 50.0
        rel = _clamp(rel, 0.0, 100.0)
        mood = scoring.mood_label(activation, valence)
        mood_counts[mood] = mood_counts.get(mood, 0) + 1
        conn.execute(
            "UPDATE tracks SET activation = ?, valence = ?, activation_relative = ?, "
            "vibe_score = ?, mood = ? WHERE id = ?",
            (activation, valence, rel, rel, mood, row_id),
        )
    conn.commit()

    return {
        "updated": len(per_row),
        "activation_stats": {
            "mean": mean,
            "std": std,
            "min": mn,
            "max": mx,
        },
        "mood_distribution": mood_counts,
    }


@app.post("/api/recompute-scores")
def api_recompute_scores(sess: dict = Depends(require_user)):
    """
    Re-run scoring.compute_axes on the caller's tracks using persisted
    features, then z-score-normalize activation into activation_relative
    and mirror into vibe_score. Cheap: no audio re-analysis. Per-user
    scoped: only touches rows belonging to the caller.
    """
    conn = get_conn()
    try:
        summary = _recompute_axes_and_zscores(conn, user_id=sess["user_id"])
    finally:
        conn.close()
    return summary


@app.get("/api/tracks/{track_key}/features")
def get_track_features(track_key: str, sess: dict = Depends(require_user)):
    """
    Return the full stored feature blob plus derived axes for a track,
    keyed by spotify_id (string) or apple_id (numeric string). Per-user.
    """
    conn = get_conn()
    try:
        row = None
        try:
            apple_id_int = int(track_key)
            row = conn.execute(
                "SELECT * FROM tracks WHERE apple_id = ?",
                (apple_id_int,),
            ).fetchone()
        except ValueError:
            pass
        if not row:
            row = conn.execute(
                "SELECT * FROM tracks WHERE spotify_id = ?",
                (track_key,),
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


# ---------------- YouTube lookup ----------------

_YT_SEARCH_TIMEOUT_S = 15.0


def _yt_search_sync(artist: str, title: str) -> Optional[str]:
    """
    Blocking yt-dlp search. Returns the first EMBEDDABLE 11-char YouTube
    video ID or None. Uses ytsearch5 across a query ladder, then validates
    each candidate via full extract (playable_in_embed True, age_limit 0,
    availability public/unlisted).
    """
    try:
        from yt_dlp import YoutubeDL
    except Exception:
        log.warning("[youtube] yt_dlp import failed")
        return None

    queries = [
        f'ytsearch5:"{title} - {artist} official music video"',
        f'ytsearch5:"{title} - {artist}"',
        f'ytsearch5:{title} {artist} official music video',
        f'ytsearch5:{title} {artist}',
        f'ytsearch5:{title}',
        f'ytsearch5:{artist}',
    ]
    flat_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "noplaylist": True}
    full_opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["default", "android", "web_embedded"]}},
    }

    seen_ids: set = set()
    for q in queries:
        try:
            with YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(q, download=False)
        except Exception as e:
            log.warning("[youtube] search failed for %r: %s", q, e)
            continue
        entries = info.get("entries") if isinstance(info, dict) else None
        if not entries:
            continue
        for entry in entries:
            if not entry:
                continue
            vid = entry.get("id") if isinstance(entry, dict) else None
            if not (isinstance(vid, str) and len(vid) == 11):
                continue
            if vid in seen_ids:
                continue
            seen_ids.add(vid)
            try:
                with YoutubeDL(full_opts) as ydl:
                    full = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            except Exception:
                continue
            if not full:
                continue
            if full.get("playable_in_embed") is False:
                continue
            if full.get("age_limit"):
                continue
            avail = full.get("availability")
            if avail not in (None, "public", "unlisted"):
                continue
            if full.get("live_status") in ("is_upcoming", "post_live"):
                continue
            return vid
    return None


async def _yt_search_with_timeout(artist: str, title: str) -> Optional[str]:
    try:
        return await asyncio.wait_for(
            run_in_threadpool(_yt_search_sync, artist, title),
            timeout=_YT_SEARCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("[youtube] search timed out for %r - %r", artist, title)
        return None
    except Exception as e:
        log.warning("[youtube] unexpected error for %r - %r: %s", artist, title, e)
        return None


@app.get("/api/tracks/{track_id}/youtube")
async def get_track_youtube(track_id: int, sess: dict = Depends(require_user)):
    """
    Return the cached YouTube video ID for a track, or null if we don't have
    one. Never triggers a search — resolution is done offline by
    scripts/prewarm_youtube.py so users never eat the ~3–5s yt-dlp latency.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT youtube_id FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})

    return {"youtube_id": row["youtube_id"], "cached": True}


_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _yt_manual_search_sync(query: str, limit: int) -> list[dict]:
    try:
        from yt_dlp import YoutubeDL
    except Exception:
        log.warning("[youtube] yt_dlp import failed")
        return []

    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["default", "android", "web_embedded"]}},
    }

    try:
        with YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception as e:
        log.warning("[youtube] manual search failed for %r: %s", query, e)
        return []

    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return []

    out: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("id")
        if not (isinstance(vid, str) and len(vid) == 11):
            continue
        title = entry.get("title") or entry.get("fulltitle")
        if not isinstance(title, str) or not title.strip():
            continue

        channel = entry.get("channel") or entry.get("uploader") or entry.get("channel_title")
        if not isinstance(channel, str):
            channel = None

        duration = entry.get("duration")
        if isinstance(duration, (int, float)):
            duration = int(duration)
        else:
            duration = None

        thumb = None
        thumbs = entry.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            # Pick highest resolution by width*height, falling back to last.
            best = None
            best_area = -1
            for t in thumbs:
                if not isinstance(t, dict):
                    continue
                url = t.get("url")
                if not isinstance(url, str):
                    continue
                w = t.get("width") or 0
                h = t.get("height") or 0
                area = (w or 0) * (h or 0)
                if area > best_area:
                    best_area = area
                    best = url
            thumb = best or (thumbs[-1].get("url") if isinstance(thumbs[-1], dict) else None)
        if not thumb:
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

        out.append({
            "youtube_id": vid,
            "title": title,
            "channel": channel,
            "duration": duration,
            "thumbnail_url": thumb,
        })
        if len(out) >= limit:
            break
    return out


@app.get("/api/tracks/{track_id}/youtube/search")
async def search_track_youtube(
    track_id: int,
    q: str = Query(...),
    limit: int = Query(5),
    sess: dict = Depends(require_user),
):
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})

    query = (q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail={"error": "empty_query"})
    if len(query) > 300:
        raise HTTPException(status_code=400, detail={"error": "query_too_long"})

    n = max(1, min(int(limit), 10))

    try:
        results = await asyncio.wait_for(
            run_in_threadpool(_yt_manual_search_sync, query, n),
            timeout=_YT_SEARCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("[youtube] manual search timed out for track=%s q=%r", track_id, query)
        results = []
    except Exception as e:
        log.warning("[youtube] manual search error for track=%s q=%r: %s", track_id, query, e)
        results = []

    return {"results": results}


@app.post("/api/tracks/{track_id}/youtube")
async def set_track_youtube(
    track_id: int,
    request: Request,
    sess: dict = Depends(require_user),
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "invalid_body"})
    vid = body.get("youtube_id")
    if not isinstance(vid, str) or not _YT_ID_RE.match(vid):
        raise HTTPException(status_code=400, detail={"error": "invalid_youtube_id"})

    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"error": "track_not_found"})
        conn.execute(
            "UPDATE tracks SET youtube_id = ?, youtube_queried_at = CURRENT_TIMESTAMP WHERE id = ?",
            (vid, track_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"youtube_id": vid, "cached": True}


# ---------------- Spotify library ingest ----------------

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _bearer_token(auth_header: Optional[str]) -> str:
    if not auth_header:
        raise HTTPException(status_code=401, detail={"error": "missing_authorization"})
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail={"error": "invalid_authorization"})
    return parts[1].strip()


@app.get("/api/spotify/library")
def spotify_library(
    spotify_authorization: Optional[str] = Header(None, alias="X-Spotify-Authorization"),
    authorization: Optional[str] = Header(None),
    sess: dict = Depends(require_user),
):
    # The VibeScape session token comes in Authorization: Bearer ...
    # The Spotify access token comes in X-Spotify-Authorization: Bearer ...
    # (Legacy: if X-Spotify-Authorization is missing, fall back to the
    # Authorization header — but that only works when VibeScape auth is
    # disabled, which is not the case in multi-user mode. We keep the
    # fallback for smoother migration; the frontend should send the
    # dedicated header.)
    token = _extract_bearer(spotify_authorization) or _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_spotify_authorization"})
    try:
        liked = splib.get_liked_count(token)
        top = splib.get_top_tracks_count(token)
        playlists_raw = splib.get_playlists(token, max_items=200)
        # Fetch the caller's Spotify user id so we can identify which
        # playlists are theirs (the only ones /items reliably returns 200
        # for after the Nov 2024 API lockdown). Fall back gracefully.
        my_spotify_id: Optional[str] = None
        try:
            me = splib._get(f"{splib.BASE}/me", token)
            my_spotify_id = me.get("id")
        except Exception as e:
            log.debug("could not resolve /v1/me for manifest owner-check: %s", e)
    except splib.SpotifyAuthError:
        return JSONResponse(status_code=401, content={"error": "spotify_token_expired"})
    except splib.SpotifyAPIError as e:
        raise HTTPException(status_code=500, detail=str(e))

    def _coerce_total(v) -> int:
        try:
            if v is None or v == "":
                return 0
            return int(v)
        except (TypeError, ValueError):
            return 0

    playlists = []
    to_resolve: list[str] = []  # playlists whose track_count came back empty/0
    for p in playlists_raw:
        if not p:
            continue
        owner_obj = p.get("owner") or {}
        owner_id = owner_obj.get("id") or ""
        owner_name = owner_obj.get("display_name") or owner_id or ""
        tracks_info = p.get("tracks") or {}
        total = _coerce_total(tracks_info.get("total"))
        pid = p.get("id")
        is_owned_by_caller = bool(my_spotify_id and owner_id == my_spotify_id)
        playlists.append({
            "id": pid,
            "name": p.get("name"),
            "track_count": total,
            "owner": owner_name,
            "owned_by_me": is_owned_by_caller,
        })
        # /me/playlists sometimes returns tracks.total as "" (empty string)
        # post Nov 2024 API changes. Only resolve via /items for playlists
        # the caller OWNS — third-party / editorial playlists return 403
        # on /items after the lockdown, so hitting them just burns rate
        # limit and spams warning logs for expected failures. If we can't
        # confirm ownership (my_spotify_id unknown), fall back to the
        # optimistic path and let the try/except quietly drop 403s.
        if total == 0 and pid and (is_owned_by_caller or my_spotify_id is None):
            to_resolve.append(pid)

    # Resolve real track counts via /items?limit=1&fields=total on
    # playlists we own. Failures are silent — UI falls back to 0.
    if to_resolve:
        for pid in to_resolve:
            try:
                data = splib._get(f"{splib.BASE}/playlists/{pid}/items", token,
                                  {"limit": 1, "fields": "total"})
                real_total = _coerce_total(data.get("total"))
                if real_total > 0:
                    for entry in playlists:
                        if entry["id"] == pid:
                            entry["track_count"] = real_total
                            break
            except splib.SpotifyAuthError:
                return JSONResponse(status_code=401,
                                    content={"error": "spotify_token_expired"})
            except splib.SpotifyAPIError as e:
                # 403 (deprecated / third-party lockdown) or other — expected
                # in post-Nov-2024 API. Log at debug to avoid warning-log spam.
                log.debug("manifest resolve for %s failed silently: %s", pid, e)
                continue

    return {
        "liked_count": liked,
        "top_tracks_count": top,
        "playlists": playlists,
    }


@app.get("/api/spotify/search")
def spotify_search(
    q: str = Query(..., min_length=1, max_length=200),
    # Spotify /v1/search caps `limit` at 10 as of late 2024 (docs say range 0-10,
    # default 5). Requesting 11+ returns 400 "Invalid limit".
    limit: int = Query(10, ge=1, le=10),
    spotify_authorization: Optional[str] = Header(None, alias="X-Spotify-Authorization"),
    sess: dict = Depends(require_user),
):
    """
    Search the Spotify catalog on behalf of the signed-in user. Returns a
    trimmed list of tracks with an `in_library` flag indicating whether
    the caller has already ingested that spotify_id.
    """
    token = _extract_bearer(spotify_authorization)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_spotify_authorization"})
    query = (q or "").strip()
    if not query:
        return {"tracks": []}

    try:
        data = splib._get(
            f"{splib.BASE}/search",
            token,
            {"q": query, "type": "track", "limit": limit},
        )
    except splib.SpotifyAuthError:
        return JSONResponse(status_code=401, content={"error": "spotify_token_expired"})
    except splib.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail={"error": "spotify_api_error", "message": str(e)})

    items = ((data.get("tracks") or {}).get("items")) or []
    spotify_ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]

    # For each Spotify id we recognise: pull vibe + mood so the frontend can
    # render "vibe NN" alongside the badge without a second round-trip.
    lib_meta: dict[str, dict] = {}
    if spotify_ids:
        placeholders = ",".join("?" * len(spotify_ids))
        conn = get_conn()
        try:
            rows = conn.execute(
                f"SELECT t.spotify_id, t.vibe_score, t.vibe_score_ml, "
                f"       t.mood, t.language, t.language_confidence "
                f"FROM tracks t "
                f"JOIN user_tracks ut ON ut.track_id = t.id "
                f"WHERE ut.user_id = ? AND t.spotify_id IN ({placeholders})",
                [sess["user_id"], *spotify_ids],
            ).fetchall()
            for r in rows:
                sid = r["spotify_id"]
                if not sid:
                    continue
                lib_meta[sid] = {
                    "vibe_score": r["vibe_score"],
                    "vibe_score_ml": r["vibe_score_ml"],
                    "mood": r["mood"],
                    "language": r["language"],
                    "language_confidence": r["language_confidence"],
                }
        finally:
            conn.close()

    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        sid = it.get("id")
        artists = it.get("artists") or []
        artist_name = ""
        if artists and isinstance(artists[0], dict):
            artist_name = artists[0].get("name") or ""
        album = it.get("album") or {}
        images = album.get("images") or []
        artwork_url = images[-1].get("url") if images and isinstance(images[-1], dict) else None
        meta = lib_meta.get(sid) or {}
        out.append({
            "spotify_id": sid,
            "title": it.get("name") or "",
            "artist": artist_name,
            "album": album.get("name") or "",
            "artwork_url": artwork_url,
            "preview_url": it.get("preview_url"),
            "duration_ms": it.get("duration_ms"),
            "in_library": sid in lib_meta,
            "vibe_score": meta.get("vibe_score"),
            "vibe_score_ml": meta.get("vibe_score_ml"),
            "mood": meta.get("mood"),
            "language": meta.get("language"),
            "language_confidence": meta.get("language_confidence"),
        })
    return {"tracks": out}


@app.post("/api/ingest/clear")
def ingest_clear(sess: dict = Depends(require_user)):
    """Clear only the caller's library membership. Global tracks that no
    other user still references are pruned in a second pass along with
    their on-disk audio files."""
    conn = get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (sess["user_id"],)
        ).fetchone()[0]
        my_track_ids = [r[0] for r in conn.execute(
            "SELECT track_id FROM user_tracks WHERE user_id = ?",
            (sess["user_id"],),
        ).fetchall()]
        conn.execute("DELETE FROM user_tracks WHERE user_id = ?", (sess["user_id"],))
        conn.commit()

        removed_tracks = 0
        removed_files = 0
        for tid in my_track_ids:
            still = conn.execute(
                "SELECT 1 FROM user_tracks WHERE track_id = ? LIMIT 1", (tid,),
            ).fetchone()
            if still:
                continue
            row = conn.execute(
                "SELECT audio_path FROM tracks WHERE id = ?", (tid,),
            ).fetchone()
            audio = row["audio_path"] if row else None
            conn.execute("DELETE FROM tracks WHERE id = ?", (tid,))
            removed_tracks += 1
            if not audio:
                continue
            resolved = _resolve_audio_path(audio)
            if resolved and resolved.exists() and resolved.is_file():
                try:
                    resolved.unlink()
                    removed_files += 1
                except OSError as e:
                    log.warning("failed to unlink %s: %s", resolved, e)
        conn.commit()
    finally:
        conn.close()

    log.info("ingest/clear user=%s removed %d user_tracks, pruned %d orphan tracks, %d audio files",
             sess["user_id"], n, removed_tracks, removed_files)
    return {"cleared": int(n), "tracks_pruned": removed_tracks, "audio_files_removed": removed_files}


class IngestSources(BaseModel):
    liked: bool = False
    top_tracks: bool = False
    playlist_ids: list[str] = []


class IngestRequest(BaseModel):
    access_token: str
    sources: IngestSources


class PublicPlaylistIngestRequest(BaseModel):
    # Either shape is accepted:
    #   {"playlist_url": "https://open.spotify.com/playlist/..."}
    #   {"playlist_id":  "37i9dQZF1DXcBWIGoYBM5M"}
    # Optional: the caller's Spotify OAuth access token. If provided we use
    # it for Spotify API calls (works for any playlist the user can see);
    # if omitted we fall back to the app's client-credentials token, which
    # since Nov 2024 is 403-forbidden for essentially all playlists.
    # The token is used only for the lifetime of this request/job — never
    # persisted server-side.
    playlist_url: Optional[str] = None
    playlist_id: Optional[str] = None
    access_token: Optional[str] = None


# Cache the client-credentials app token in-process. Spotify tokens are valid
# for ~3600s; we refresh on any 401 or when the cached copy is close to expiry.
_APP_TOKEN_LOCK = threading.Lock()
_APP_TOKEN_STATE: dict = {"token": None, "expires_at": 0.0}


def _get_app_token(force_refresh: bool = False) -> str:
    """Return a cached client-credentials token, refreshing when stale."""
    import time as _time
    with _APP_TOKEN_LOCK:
        now = _time.time()
        tok = _APP_TOKEN_STATE.get("token")
        exp = float(_APP_TOKEN_STATE.get("expires_at") or 0.0)
        if tok and not force_refresh and now < (exp - 60):
            return tok
        client_id = getattr(app_config, "SPOTIFY_CLIENT_ID", "") if app_config else ""
        client_secret = getattr(app_config, "SPOTIFY_CLIENT_SECRET", "") if app_config else ""
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=500,
                detail={"error": "spotify_app_credentials_missing"},
            )
        # spotify_matcher.get_client_credentials_token returns the token string
        # only; we don't know the expiry precisely. Assume ~3600s standard.
        new_tok = spotify_matcher.get_client_credentials_token(client_id, client_secret)
        _APP_TOKEN_STATE["token"] = new_tok
        _APP_TOKEN_STATE["expires_at"] = now + 3300.0
        return new_tok


def _itunes_lookup_by_isrc(isrc: str) -> Optional[dict]:
    try:
        r = requests.get("https://itunes.apple.com/lookup", params={"isrc": isrc}, timeout=15)
        r.raise_for_status()
        results = r.json().get("results") or []
        return results[0] if results else None
    except requests.RequestException as e:
        log.warning("itunes ISRC lookup failed for %s: %s", isrc, e)
        return None


def _itunes_search_track(title: str, artist: str) -> Optional[dict]:
    if not title or not artist:
        return None
    term = f"{title} {artist}".strip()
    try:
        results = itunes_client.search(term, limit=5)
    except Exception as e:
        log.warning("itunes search failed for %r: %s", term, e)
        return None
    if not results:
        return None
    title_l = title.lower()
    artist_l = artist.lower()
    for r in results:
        if not isinstance(r, dict) or not r.get("previewUrl"):
            continue
        r_title = (r.get("trackName") or "").lower()
        r_artist = (r.get("artistName") or "").lower()
        if r_title == title_l and r_artist == artist_l:
            return r
    for r in results:
        if not isinstance(r, dict) or not r.get("previewUrl"):
            continue
        r_title = (r.get("trackName") or "").lower()
        r_artist = (r.get("artistName") or "").lower()
        if title_l in r_title and artist_l in r_artist:
            return r
    for r in results:
        if isinstance(r, dict) and r.get("previewUrl"):
            return r
    return None


def _download(url: str, suffix: str) -> str:
    r = requests.get(url, timeout=30, stream=True)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=suffix)
    with open(fd, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return path


def _update_job(job_id: str, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)


def _bump(job_id: str, key: str, amount: int = 1):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job[key] = int(job.get(key, 0)) + amount


def _is_cancelled(job_id: str) -> bool:
    """Check whether the caller requested cancellation of this ingest job."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))


def _process_track(conn, track: dict, job_id: str, user_id: int, source: str = "manual") -> str:
    spotify_id = track.get("id")
    if not spotify_id:
        return "skip:no_id"

    name = track.get("name") or "?"
    artists = track.get("artists") or []
    artist_name = (artists[0].get("name") if artists and isinstance(artists[0], dict) else "?") or "?"
    album_obj = track.get("album") or {}
    album_name = album_obj.get("name")
    images = album_obj.get("images") or []
    artwork_url = images[0].get("url") if images and isinstance(images[0], dict) else None
    duration_ms = track.get("duration_ms")
    ext_ids = track.get("external_ids") or {}
    isrc = ext_ids.get("isrc")
    preview_url = track.get("preview_url")

    _update_job(job_id, current_track=f"{name} - {artist_name}")
    log.info("[ingest] user=%s track=%r artist=%r spotify_preview=%s isrc=%s",
             user_id, name, artist_name, bool(preview_url), isrc or "-")

    existing_link = conn.execute(
        "SELECT 1 FROM tracks t "
        "JOIN user_tracks ut ON ut.track_id = t.id "
        "WHERE t.spotify_id = ? AND ut.user_id = ?",
        (spotify_id, user_id),
    ).fetchone()
    if existing_link:
        return "skip:already_ingested"

    # Track exists globally but this user hasn't linked it yet. Reuse
    # features + audio, just add the user_tracks row.
    existing_global = conn.execute(
        "SELECT id FROM tracks WHERE spotify_id = ?",
        (spotify_id,),
    ).fetchone()
    if existing_global:
        conn.execute(
            "INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) "
            "VALUES (?, ?, ?)",
            (user_id, int(existing_global["id"]), source),
        )
        conn.commit()
        return "ok:linked_existing"

    audio_url = None
    audio_ext = ".mp3"
    apple_id = None
    itunes_result = None
    source = None
    classification_source = "none"

    if preview_url:
        audio_url = preview_url
        audio_ext = ".mp3"
        source = "spotify"
        classification_source = "spotify_preview"
    else:
        # Cascade: iTunes term-search -> Deezer ISRC -> Deezer term-search.
        # (iTunes /lookup?isrc= is dead — Apple never officially supported it
        # and it now returns 0 for every ISRC. Skipping saves ~1s/track.)
        itunes_result = _itunes_search_track(name, artist_name)
        log.info("[ingest]   itunes term-search %r/%r -> hit=%s",
                 name, artist_name,
                 bool(itunes_result and itunes_result.get("previewUrl")))
        if itunes_result and itunes_result.get("previewUrl"):
            classification_source = "itunes_term_search"
            audio_url = itunes_result["previewUrl"]
            audio_ext = ".m4a"
            apple_id = itunes_result.get("trackId")
            source = "itunes"

        if not audio_url and isrc:
            deezer_result = deezer_client.lookup_by_isrc(isrc)
            log.info("[ingest]   deezer isrc-lookup isrc=%s -> hit=%s",
                     isrc, bool(deezer_result and deezer_result.get("previewUrl")))
            if deezer_result and deezer_result.get("previewUrl"):
                itunes_result = deezer_result  # reuse the merge slot below
                classification_source = "deezer_isrc"
                audio_url = deezer_result["previewUrl"]
                audio_ext = ".mp3"
                source = "deezer"

        if not audio_url:
            deezer_result = deezer_client.search_track(name, artist_name)
            log.info("[ingest]   deezer term-search %r/%r -> hit=%s",
                     name, artist_name,
                     bool(deezer_result and deezer_result.get("previewUrl")))
            if deezer_result and deezer_result.get("previewUrl"):
                itunes_result = deezer_result  # reuse the merge slot below
                classification_source = "deezer_search"
                audio_url = deezer_result["previewUrl"]
                audio_ext = ".mp3"
                source = "deezer"

    if not audio_url:
        log.info("[ingest]   -> no_preview (spotify+itunes+deezer all failed)")
        return "skip:no_preview"

    # If a prior ingest downloaded this spotify_id, reuse the file
    # (audio is content-addressed by spotify_id).
    shared_row = conn.execute(
        "SELECT audio_path FROM tracks WHERE spotify_id = ? AND audio_path IS NOT NULL LIMIT 1",
        (spotify_id,),
    ).fetchone()
    shared_local: Optional[str] = None
    if shared_row and shared_row["audio_path"]:
        p = _resolve_audio_path(shared_row["audio_path"])
        if p and p.exists() and p.is_file():
            shared_local = str(p)
            log.info("[ingest]   reusing existing local audio (shared): %s", shared_row["audio_path"])

    # Two-path scoring:
    #   * ML (Modal in prod OR local GPU in dev): MERT + Whisper via
    #     ingest/ml_backend.py — env var VIBESCAPE_ML_MODE selects.
    #   * Librosa fallback: only if the ML backend is 'none' or a call fails.
    # If neither works we skip the track.
    ml_preds = None
    lang_preds = None
    if ml_backend.is_available():
        ml_preds = ml_backend.predict_from_url(audio_url)
        # Language uses the same backend; ok if it fails, DB column stays NULL.
        lang_preds = ml_backend.predict_language_from_url(audio_url)

    if ml_preds:
        # MERT-only path. Derive activation/valence/mood from predictions.
        energy_pred = float(ml_preds.get("energy", 0.0))
        dance_pred = float(ml_preds.get("danceability", 0.0))
        valence_pred = float(ml_preds.get("valence", 0.0))
        vibe_score_ml = float(ml_preds.get("vibe_score", 0.55 * energy_pred + 0.45 * dance_pred))
        model_version = str(ml_preds.get("model_version") or "mert_v1")

        activation = (0.55 * energy_pred + 0.45 * dance_pred) * 100.0
        valence = valence_pred * 100.0
        mood = scoring.mood_label(activation, valence)
        classification_source = "ml_mert"

        language = None
        language_confidence = None
        language_top3_json = None
        language_model_version = None
        language_predicted_at = None
        if lang_preds:
            top1_prob = float(lang_preds.get("top1_prob", 0.0))
            # Only persist if we're at least 'uncertain' (top1 >= 0.2). Below
            # that, Whisper is guessing on non-speech / instrumental audio.
            if top1_prob >= 0.2:
                language = lang_preds.get("top1_lang") or None
                language_confidence = top1_prob
                language_top3_json = json.dumps({
                    "top1": [lang_preds.get("top1_lang"), top1_prob],
                    "top2": [lang_preds.get("top2_lang"), float(lang_preds.get("top2_prob", 0.0))],
                    "top3": [lang_preds.get("top3_lang"), float(lang_preds.get("top3_prob", 0.0))],
                })
                language_model_version = str(lang_preds.get("model_version") or "whisper_small")
                language_predicted_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

        merged_preview_url = preview_url or (itunes_result.get("previewUrl") if itunes_result else None)
        merged_genre = (itunes_result or {}).get("primaryGenreName")
        merged_track_view_url = (itunes_result or {}).get("trackViewUrl")

        track_data = {
            "user_id": user_id,
            "source": source,
            "apple_id": apple_id,
            "spotify_id": spotify_id,
            "isrc": isrc,
            "title": name,
            "artist": artist_name,
            "album": album_name or (itunes_result or {}).get("collectionName"),
            "genre": merged_genre,
            "artwork_url": artwork_url or (itunes_result or {}).get("artworkUrl100"),
            "preview_url": merged_preview_url,
            "track_view_url": merged_track_view_url,
            "duration_ms": duration_ms or (itunes_result or {}).get("trackTimeMillis"),
            "activation": activation,
            "valence": valence,
            "vibe_score": activation,  # backward-compat
            "mood": mood,
            "audio_path": None,  # prod doesn't store audio locally
            "classification_source": classification_source,
            "energy_pred": energy_pred,
            "danceability_pred": dance_pred,
            "valence_pred": valence_pred,
            "vibe_score_ml": vibe_score_ml,
            "model_version": model_version,
            "language": language,
            "language_confidence": language_confidence,
            "language_top3_json": language_top3_json,
            "language_model_version": language_model_version,
            "language_predicted_at": language_predicted_at,
        }
        _upsert(conn, track_data)
        return f"ok:ml:{source}"

    # Librosa fallback (local dev only — Fly image doesn't ship librosa).
    tmp_path = None
    _cleanup_tmp = False
    try:
        if shared_local:
            tmp_path = shared_local
            _cleanup_tmp = False
        else:
            tmp_path = _download(audio_url, audio_ext)
            _cleanup_tmp = True
        global feat
        if feat is None:
            import features as feat  # heavy: librosa/numpy
        f = feat.extract(tmp_path)
        axes = scoring.compute_axes(f)
        activation = axes["activation"]
        valence = axes["valence"]
        mood = scoring.mood_label(activation, valence)

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        if shared_local:
            audio_rel = shared_row["audio_path"]
        else:
            target_name = f"{spotify_id}{audio_ext}"
            target = AUDIO_DIR / target_name
            try:
                shutil.copyfile(tmp_path, target)
                audio_rel = f"data/audio/{target_name}"
            except OSError as e:
                log.warning("failed to save audio for %s: %s", spotify_id, e)
                audio_rel = None

        merged_preview_url = preview_url or (itunes_result.get("previewUrl") if itunes_result else None)
        merged_genre = (itunes_result or {}).get("primaryGenreName")
        merged_track_view_url = (itunes_result or {}).get("trackViewUrl")

        track_data = {
            "user_id": user_id,
            "source": source,
            "apple_id": apple_id,
            "spotify_id": spotify_id,
            "isrc": isrc,
            "title": name,
            "artist": artist_name,
            "album": album_name or (itunes_result or {}).get("collectionName"),
            "genre": merged_genre,
            "artwork_url": artwork_url or (itunes_result or {}).get("artworkUrl100"),
            "preview_url": merged_preview_url,
            "track_view_url": merged_track_view_url,
            "duration_ms": duration_ms or (itunes_result or {}).get("trackTimeMillis"),
            "tempo": f.get("tempo"),
            "energy": f.get("energy_mean"),
            "brightness": f.get("brightness"),
            "zcr": f.get("zcr"),
            "mfcc_json": json.dumps(f.get("mfcc_mean") or f.get("mfcc") or []),
            "tempo_stability": f.get("tempo_stability"),
            "onset_rate": f.get("onset_rate"),
            "energy_mean": f.get("energy_mean"),
            "energy_std": f.get("energy_std"),
            "bandwidth": f.get("bandwidth"),
            "rolloff": f.get("rolloff"),
            "spectral_contrast": f.get("spectral_contrast"),
            "flatness": f.get("flatness"),
            "timbre_variability": f.get("timbre_variability"),
            "valence_mode": f.get("valence_mode"),
            "tonnetz_std": f.get("tonnetz_std"),
            "acousticness": f.get("acousticness"),
            "chroma_mean_json": json.dumps(f.get("chroma_mean") or []),
            "activation": activation,
            "valence": valence,
            "vibe_score": activation,
            "mood": mood,
            "audio_path": audio_rel,
            "classification_source": classification_source,
        }
        _upsert(conn, track_data)
        return f"ok:{source}"
    finally:
        if tmp_path and _cleanup_tmp and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _upsert(conn, td: dict) -> None:
    cols = [
        "apple_id", "spotify_id", "isrc", "title", "artist", "album", "genre",
        "artwork_url", "preview_url", "track_view_url", "duration_ms",
        "tempo", "energy", "brightness", "zcr", "mfcc_json",
        "tempo_stability", "onset_rate", "energy_mean", "energy_std",
        "bandwidth", "rolloff", "spectral_contrast", "flatness",
        "timbre_variability", "valence_mode", "tonnetz_std", "acousticness",
        "chroma_mean_json",
        "activation", "valence", "activation_relative",
        "vibe_score", "mood", "audio_path", "classification_source",
        # ML prediction columns (populated by the Modal path)
        "energy_pred", "danceability_pred", "valence_pred",
        "vibe_score_ml", "model_version",
        # Language detection columns (populated by the Whisper Modal path)
        "language", "language_confidence", "language_top3_json",
        "language_model_version", "language_predicted_at",
    ]
    values = [td.get(c) for c in cols]
    uid = td.get("user_id")
    source = td.get("source") or "manual"

    existing = None
    if td.get("spotify_id"):
        row = conn.execute(
            "SELECT id FROM tracks WHERE spotify_id = ?", (td["spotify_id"],)
        ).fetchone()
        if row:
            existing = row[0]
    if existing is None and td.get("apple_id"):
        row = conn.execute(
            "SELECT id FROM tracks WHERE apple_id = ?", (td["apple_id"],)
        ).fetchone()
        if row:
            existing = row[0]

    if existing is None:
        placeholders = ", ".join(["?"] * len(cols))
        cur = conn.execute(
            f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
        track_id = int(cur.lastrowid)
    else:
        set_clause = ", ".join([f"{c} = ?" for c in cols])
        conn.execute(f"UPDATE tracks SET {set_clause} WHERE id = ?", values + [existing])
        track_id = int(existing)

    if uid is not None:
        conn.execute(
            "INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) VALUES (?, ?, ?)",
            (uid, track_id, source),
        )
    conn.commit()


def _collect_tracks(token: str, sources: IngestSources) -> list[tuple[dict, str]]:
    seen: set[str] = set()
    out: list[tuple[dict, str]] = []

    def _add(items: list[dict], src: str):
        for t in items:
            tid = t.get("id") if isinstance(t, dict) else None
            if not tid or tid in seen:
                continue
            seen.add(tid)
            out.append((t, src))

    if sources.liked:
        _add(splib.fetch_liked(token), "liked_songs")
    if sources.top_tracks:
        _add(splib.fetch_top_tracks(token), "top_tracks")
    for pid in sources.playlist_ids or []:
        if not pid:
            continue
        _add(splib.fetch_playlist_tracks(pid, token), f"playlist:{pid}")
    return out


def _run_ingest_job(job_id: str, token: str, sources: IngestSources, user_id: int):
    try:
        _update_job(job_id, status="running", current_track="collecting library…")
        log.info("[ingest job=%s user=%s] starting collect: liked=%s top=%s playlists=%s "
                 "token_len=%d token_head=%s token_tail=%s",
                 job_id, user_id,
                 sources.liked, sources.top_tracks,
                 (sources.playlist_ids or []),
                 len(token or ""),
                 (token or "")[:12],
                 (token or "")[-6:] if token else "")

        # Preflight: probe /v1/me to distinguish "token is bad" from "endpoint
        # is bad". If /me returns 200, the token is fine and any downstream
        # 401 is endpoint-specific — bubble a clearer error.
        try:
            me = splib._get(f"{splib.BASE}/me", token)
            log.info("[ingest job=%s] /v1/me OK id=%s display=%s product=%s",
                     job_id, me.get("id"), me.get("display_name"), me.get("product"))
        except splib.SpotifyAuthError:
            log.warning("[ingest job=%s] /v1/me returned 401 — token itself is bad", job_id)
            _update_job(job_id, status="error", error_message="spotify_token_expired")
            return
        except Exception as e:
            log.warning("[ingest job=%s] /v1/me preflight failed: %s", job_id, e)

        try:
            tracks = _collect_tracks(token, sources)
        except splib.SpotifyAuthError:
            log.warning("[ingest job=%s] _collect_tracks raised SpotifyAuthError "
                        "(but /me was OK — endpoint-specific 401?)", job_id)
            _update_job(job_id, status="error", error_message="spotify_token_expired")
            return
        except splib.SpotifyAPIError as e:
            _update_job(job_id, status="error", error_message=f"spotify_api_error: {e}")
            return

        # Log the collect count + a small breakdown by source so we can
        # trace any unexpected shrinkage between fetch and processing.
        by_src: dict[str, int] = {}
        for _t, _src in tracks:
            by_src[_src] = by_src.get(_src, 0) + 1
        log.info("[ingest job=%s] _collect_tracks returned total=%d by_source=%s",
                 job_id, len(tracks), by_src)
        _update_job(job_id, total=len(tracks))

        conn = get_conn()
        try:
            cancelled = False
            for track, src in tracks:
                if _is_cancelled(job_id):
                    log.info("[ingest job=%s] cancel_requested — stopping loop", job_id)
                    cancelled = True
                    break
                try:
                    result = _process_track(conn, track, job_id, user_id, source=src)
                    # Result strings emitted by _process_track:
                    #   ok:spotify / ok:itunes / ok:deezer          (librosa branch, fresh)
                    #   ok:ml:spotify / ok:ml:itunes / ok:ml:deezer (ML branch, fresh)
                    #   ok:linked_existing                          (global DB dedup hit)
                    #   skip:no_preview / skip:no_id / skip:already_ingested
                    #
                    # Four exclusive buckets (sum == processed):
                    #   newly_analyzed     — fresh ingest with audio
                    #   linked_from_global — track existed globally, user_tracks added
                    #   already_in_library — user already had this exact link
                    #   no_preview         — dropped (no audio anywhere)
                    #   skipped            — degenerate: no id / exception
                    if result and result.startswith("ok:") and result not in ("ok:linked_existing",):
                        _bump(job_id, "newly_analyzed", 1)
                        # Legacy counter mirroring so old frontends keep working.
                        if "spotify" in result:
                            _bump(job_id, "matched_spotify", 1)
                        else:
                            _bump(job_id, "preview_only", 1)
                    elif result == "ok:linked_existing":
                        _bump(job_id, "linked_from_global", 1)
                        _bump(job_id, "matched_spotify", 1)  # legacy mirror
                    elif result == "skip:already_ingested":
                        _bump(job_id, "already_in_library", 1)
                        _bump(job_id, "skipped", 1)  # legacy mirror
                    elif result == "skip:no_preview":
                        _bump(job_id, "no_preview", 1)
                    else:
                        _bump(job_id, "skipped", 1)
                except splib.SpotifyAuthError:
                    _update_job(job_id, status="error", error_message="spotify_token_expired")
                    return
                except Exception as e:
                    log.exception("track ingest failed: %s", e)
                    _bump(job_id, "skipped", 1)
                _bump(job_id, "processed", 1)
        finally:
            conn.close()

        # library-wide z-score recompute (per-user) so the vibe slider
        # reflects the actual distribution of the (now larger) library.
        # Runs even on cancel so activation_relative reflects what did land.
        try:
            conn2 = get_conn()
            try:
                _recompute_axes_and_zscores(conn2, user_id=user_id)
            finally:
                conn2.close()
        except Exception as e:
            log.exception("post-ingest recompute failed: %s", e)

        if cancelled:
            _update_job(job_id, status="cancelled", current_track=None,
                        note="Cancelled by user")
        else:
            _update_job(job_id, status="complete", current_track=None)
    except Exception as e:
        log.exception("ingest job crashed: %s", e)
        _update_job(job_id, status="error", error_message=f"{e.__class__.__name__}: {e}")


@app.post("/api/ingest/spotify", status_code=202)
def ingest_spotify(req: IngestRequest, sess: dict = Depends(require_user)):
    if not req.access_token:
        raise HTTPException(status_code=422, detail="access_token required")
    if not (req.sources.liked or req.sources.top_tracks or req.sources.playlist_ids):
        raise HTTPException(status_code=422, detail="no sources selected")
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "user_id": sess["user_id"],
            "current_track": None,
            "total": 0,
            "processed": 0,
            # Four buckets, mutually exclusive, sum == processed
            "newly_analyzed": 0,     # fresh ingest with audio (spotify/itunes/deezer)
            "linked_from_global": 0, # track existed in global DB, added user_tracks link
            "already_in_library": 0, # user already had this track linked
            "no_preview": 0,         # dropped — no preview URL from any source
            "skipped": 0,            # skip:no_id or exceptions
            # Legacy field names kept for backward compat while frontend
            # migrates. Mirror newly_analyzed and linked_from_global into these.
            "matched_spotify": 0,
            "preview_only": 0,
            "cancel_requested": False,
            "error_message": None,
        }
    t = threading.Thread(
        target=_run_ingest_job,
        args=(job_id, req.access_token, req.sources, sess["user_id"]),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


class SingleIngestRequest(BaseModel):
    spotify_id: str
    # Optional user OAuth token — required for /v1/tracks/{id} on premium/full
    # metadata. Falls back to app client-credentials token which works for
    # public track lookups.
    access_token: Optional[str] = None


@app.post("/api/ingest/single")
async def ingest_single(
    req: SingleIngestRequest,
    sess: dict = Depends(require_user),
):
    """
    Ingest a single Spotify track into the caller's library. Idempotent:
    if the caller already has this track, returns the existing row.
    Reuses the _process_track pipeline (Modal ML → librosa fallback →
    iTunes preview lookup) so features/audio path are populated the same
    way as playlist/liked ingest.
    """
    spotify_id = (req.spotify_id or "").strip()
    if not spotify_id:
        raise HTTPException(status_code=422, detail={"error": "missing_spotify_id"})

    user_id = sess["user_id"]

    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT {', '.join('t.' + c for c in TRACK_COLUMNS)} FROM tracks t "
            f"JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE t.spotify_id = ? AND ut.user_id = ?",
            (spotify_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return {"status": "already_ingested", "track": _row_to_dict(row)}

    token = (req.access_token or "").strip()
    if not token:
        try:
            token = _get_app_token()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail={"error": "spotify_token_unavailable", "message": str(e)})

    try:
        track_obj = await run_in_threadpool(
            splib._get, f"{splib.BASE}/tracks/{spotify_id}", token
        )
    except splib.SpotifyAuthError:
        return JSONResponse(status_code=401, content={"error": "spotify_token_expired"})
    except splib.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail={"error": "spotify_api_error", "message": str(e)})

    if not isinstance(track_obj, dict) or not track_obj.get("id"):
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})

    job_id = f"single:{uuid.uuid4().hex}"
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "user_id": user_id,
            "current_track": None,
            "total": 1,
            "processed": 0,
            # Four buckets, mutually exclusive, sum == processed
            "newly_analyzed": 0,     # fresh ingest with audio (spotify/itunes/deezer)
            "linked_from_global": 0, # track existed in global DB, added user_tracks link
            "already_in_library": 0, # user already had this track linked
            "no_preview": 0,         # dropped — no preview URL from any source
            "skipped": 0,            # skip:no_id or exceptions
            # Legacy field names kept for backward compat while frontend
            # migrates. Mirror newly_analyzed and linked_from_global into these.
            "matched_spotify": 0,
            "preview_only": 0,
            "cancel_requested": False,
            "error_message": None,
        }
    try:
        def _do_process() -> str:
            conn = get_conn()
            try:
                return _process_track(conn, track_obj, job_id, user_id, source="search")
            finally:
                conn.close()
        result = await run_in_threadpool(_do_process)
    except Exception as e:
        log.exception("single ingest failed for %s: %s", spotify_id, e)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        raise HTTPException(status_code=500, detail={"error": "ingest_failed", "message": str(e)})
    finally:
        with JOBS_LOCK:
            JOBS.pop(job_id, None)

    if result.startswith("skip:no_preview"):
        raise HTTPException(status_code=422, detail={"error": "no_preview_available", "result": result})
    if result.startswith("skip:no_id"):
        raise HTTPException(status_code=422, detail={"error": "invalid_track", "result": result})

    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT {', '.join('t.' + c for c in TRACK_COLUMNS)} FROM tracks t "
            f"JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE t.spotify_id = ? AND ut.user_id = ?",
            (spotify_id, user_id),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=500, detail={"error": "post_ingest_missing", "result": result})
    return {"status": "ok", "result": result, "track": _row_to_dict(row)}


def _run_public_playlist_job(job_id: str, playlist_id: str, user_id: int,
                             user_token: Optional[str] = None,
                             followed_note: Optional[str] = None):
    """
    Background job for /api/ingest/spotify-public. Uses the caller's Spotify
    OAuth token when supplied (works for arbitrary public playlists that
    the user follows or can follow), else falls back to the app's
    client-credentials token (largely broken since Nov 2024 but preserved
    for API completeness). Reuses the same _process_track pipeline as
    OAuth ingest — tracks land under `user_id`.

    If the tracks endpoint returns 403 with a user token, we attempt to
    silently follow the playlist ({"public": false}) — Spotify then
    unlocks the tracks endpoint. Follow policy: leave followed (matches
    user intent; frontend surfaces the note).

    The user's OAuth token is used in-memory only; nothing is persisted.
    """
    try:
        _update_job(job_id, status="running", current_track=f"loading playlist {playlist_id}…")
        if followed_note:
            _update_job(job_id, note=followed_note)

        use_user_token = bool(user_token)
        try:
            fetch_token = user_token if use_user_token else _get_app_token()
        except HTTPException as e:
            _update_job(job_id, status="error",
                        error_message=str(e.detail if hasattr(e, "detail") else e))
            return

        def _do_fetch(tok: str) -> list[dict]:
            return splib.fetch_public_playlist_tracks(playlist_id, tok)

        try:
            tracks = _do_fetch(fetch_token)
        except splib.PlaylistNotFoundError:
            _update_job(job_id, status="error", error_message="playlist_not_found")
            return
        except splib.PlaylistPrivateError:
            # 403 on /tracks. With a user token, try the follow-then-retry
            # workaround. With app token, this is genuinely inaccessible.
            if not use_user_token:
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            log.info("[public-playlist] user=%s playlist=%s: /tracks 403, attempting follow-then-retry",
                     user_id, playlist_id)
            try:
                follow_result = splib.follow_playlist(playlist_id, user_token, public=False)
            except splib.SpotifyAuthError:
                _update_job(job_id, status="error",
                            error_message="spotify_token_expired")
                return
            except Exception as e:
                log.exception("[public-playlist] follow crashed: %s", e)
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            if follow_result == "scope":
                _update_job(job_id, status="error",
                            error_message="spotify_scope_upgrade_required")
                return
            if follow_result != "ok":
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            log.info("[public-playlist] user=%s playlist=%s: followed, retrying /tracks",
                     user_id, playlist_id)
            note = ("Followed playlist to enable ingest — "
                    "visible in your Spotify library.")
            _update_job(job_id, note=note)
            try:
                tracks = _do_fetch(user_token)
            except splib.PlaylistPrivateError:
                # Follow succeeded but tracks still 403 → genuinely restricted.
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            except splib.PlaylistNotFoundError:
                _update_job(job_id, status="error", error_message="playlist_not_found")
                return
            except splib.SpotifyAuthError:
                _update_job(job_id, status="error",
                            error_message="spotify_token_expired")
                return
            except splib.SpotifyAPIError as e:
                _update_job(job_id, status="error",
                            error_message=f"spotify_api_error: {e}")
                return
        except splib.SpotifyAuthError:
            if use_user_token:
                _update_job(job_id, status="error", error_message="spotify_token_expired")
                return
            try:
                fetch_token = _get_app_token(force_refresh=True)
                tracks = _do_fetch(fetch_token)
            except Exception as e2:
                _update_job(job_id, status="error",
                            error_message=f"spotify_api_error: {e2}")
                return
        except splib.SpotifyAPIError as e:
            _update_job(job_id, status="error",
                        error_message=f"spotify_api_error: {e}")
            return

        # De-dupe within the fetch (Spotify sometimes returns dupes in playlists).
        seen: set[str] = set()
        unique_tracks: list[dict] = []
        for t in tracks:
            tid = t.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            unique_tracks.append(t)

        _update_job(job_id, total=len(unique_tracks))

        conn = get_conn()
        try:
            cancelled = False
            src_label = f"playlist:{playlist_id}"
            for track in unique_tracks:
                if _is_cancelled(job_id):
                    log.info("[public-playlist job=%s] cancel_requested — stopping loop", job_id)
                    cancelled = True
                    break
                try:
                    result = _process_track(conn, track, job_id, user_id, source=src_label)
                    # Same 4-bucket scheme as _run_ingest_job. See the comment
                    # there for the mapping rationale.
                    if result and result.startswith("ok:") and result != "ok:linked_existing":
                        _bump(job_id, "newly_analyzed", 1)
                        if "spotify" in result:
                            _bump(job_id, "matched_spotify", 1)
                        else:
                            _bump(job_id, "preview_only", 1)
                    elif result == "ok:linked_existing":
                        _bump(job_id, "linked_from_global", 1)
                        _bump(job_id, "matched_spotify", 1)  # legacy mirror
                    elif result == "skip:already_ingested":
                        _bump(job_id, "already_in_library", 1)
                        _bump(job_id, "skipped", 1)  # legacy mirror
                    elif result == "skip:no_preview":
                        _bump(job_id, "no_preview", 1)
                    else:
                        _bump(job_id, "skipped", 1)
                except Exception as e:
                    log.exception("public playlist track ingest failed: %s", e)
                    _bump(job_id, "skipped", 1)
                _bump(job_id, "processed", 1)
        finally:
            conn.close()

        # Per-user z-score refresh after growing the library. Runs even on
        # cancel so activation_relative reflects what did land.
        try:
            conn2 = get_conn()
            try:
                _recompute_axes_and_zscores(conn2, user_id=user_id)
            finally:
                conn2.close()
        except Exception as e:
            log.exception("post-ingest recompute (public playlist) failed: %s", e)

        if cancelled:
            # Preserve any existing followed_note; add a cancelled note only
            # if there isn't already a meaningful one.
            with JOBS_LOCK:
                existing_note = (JOBS.get(job_id) or {}).get("note")
            new_note = existing_note or "Cancelled by user"
            _update_job(job_id, status="cancelled", current_track=None, note=new_note)
        else:
            _update_job(job_id, status="complete", current_track=None)
    except Exception as e:
        log.exception("public playlist job crashed: %s", e)
        _update_job(job_id, status="error", error_message=f"{e.__class__.__name__}: {e}")


@app.post("/api/ingest/spotify-public", status_code=202)
def ingest_spotify_public(req: PublicPlaylistIngestRequest,
                         sess: dict = Depends(require_user)):
    """
    Ingest a public Spotify playlist by URL or ID. If the caller sends
    their Spotify OAuth `access_token` in the body we use it (works for
    arbitrary public playlists that the user can see, including friends'
    shared playlists). Otherwise falls back to the app's client-credentials
    token, which since Nov 2024 is 403-forbidden for essentially all
    playlists — kept for API completeness.

    New tracks are scoped to the caller's user_id. The Spotify OAuth token
    is used only for this request/job — never persisted.
    """
    raw = req.playlist_url or req.playlist_id or ""
    if not raw:
        raise HTTPException(
            status_code=422,
            detail={"error": "playlist_url or playlist_id required"},
        )
    playlist_id = splib.parse_playlist_id(raw)
    if not playlist_id:
        raise HTTPException(status_code=400, detail={"error": "invalid_playlist_url"})

    user_token = (req.access_token or "").strip() or None
    use_user_token = bool(user_token)
    followed_note: Optional[str] = None

    # Preflight: (1) confirm the playlist exists via metadata,
    #            (2) probe /tracks to see if we can actually read it
    #                (metadata succeeding does NOT imply tracks are readable
    #                 — confirmed live post Nov 2024),
    #            (3) if 403 with a user token, attempt silent follow +
    #                re-probe.
    try:
        preflight_token = user_token if use_user_token else _get_app_token()

        # (1) metadata
        r_meta = requests.get(
            f"https://api.spotify.com/v1/playlists/{playlist_id}",
            headers={"Authorization": f"Bearer {preflight_token}"},
            params={"fields": "id,name,tracks(total)"},
            timeout=15,
        )
        if r_meta.status_code == 404:
            raise HTTPException(status_code=404, detail={"error": "playlist_not_found"})
        if r_meta.status_code == 400:
            raise HTTPException(status_code=404, detail={"error": "playlist_not_found"})
        if r_meta.status_code == 401:
            if use_user_token:
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_token_expired"})
            raise HTTPException(status_code=403, detail={"error": "playlist_private"})
        if r_meta.status_code == 403:
            raise HTTPException(status_code=403, detail={"error": "playlist_private"})
        if r_meta.status_code >= 400:
            raise HTTPException(status_code=502,
                                detail={"error": f"spotify_api_error: {r_meta.status_code}"})

        # (2) probe tracks
        tracks_status = splib.probe_playlist_tracks(playlist_id, preflight_token)
        if tracks_status == 404:
            raise HTTPException(status_code=404, detail={"error": "playlist_not_found"})
        if tracks_status == 401:
            if use_user_token:
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_token_expired"})
            raise HTTPException(status_code=403, detail={"error": "playlist_private"})
        if tracks_status == 403:
            if not use_user_token:
                # App-token path: nothing we can do, Nov 2024 lockdown.
                raise HTTPException(status_code=403, detail={"error": "playlist_private"})
            # (3) user-token 403: try follow-then-retry.
            log.info("[public-playlist preflight] user=%s playlist=%s: /tracks 403, attempting follow",
                     sess["user_id"], playlist_id)
            try:
                follow_result = splib.follow_playlist(playlist_id, user_token, public=False)
            except splib.SpotifyAuthError:
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_token_expired"})
            except requests.RequestException as e:
                raise HTTPException(status_code=502,
                                    detail={"error": f"spotify_unreachable: {e}"})
            if follow_result == "scope":
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_scope_upgrade_required"})
            if follow_result != "ok":
                raise HTTPException(status_code=403,
                                    detail={"error": "playlist_private"})
            # Re-probe after follow.
            reprobe = splib.probe_playlist_tracks(playlist_id, user_token)
            if reprobe != 200:
                # Follow succeeded but tracks still not accessible — genuine.
                raise HTTPException(status_code=403,
                                    detail={"error": "playlist_private"})
            followed_note = ("Followed playlist to enable ingest — "
                             "visible in your Spotify library.")
            log.info("[public-playlist preflight] user=%s playlist=%s: follow+reprobe OK",
                     sess["user_id"], playlist_id)
        elif tracks_status >= 400:
            raise HTTPException(status_code=502,
                                detail={"error": f"spotify_api_error: {tracks_status}"})
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail={"error": f"spotify_unreachable: {e}"})

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "user_id": sess["user_id"],
            "current_track": None,
            "playlist_id": playlist_id,
            "source": "public_playlist",
            "auth_mode": "user_oauth" if use_user_token else "app_token",
            "note": followed_note,
            "total": 0,
            "processed": 0,
            # Four buckets, mutually exclusive, sum == processed
            "newly_analyzed": 0,     # fresh ingest with audio (spotify/itunes/deezer)
            "linked_from_global": 0, # track existed in global DB, added user_tracks link
            "already_in_library": 0, # user already had this track linked
            "no_preview": 0,         # dropped — no preview URL from any source
            "skipped": 0,            # skip:no_id or exceptions
            # Legacy field names kept for backward compat while frontend
            # migrates. Mirror newly_analyzed and linked_from_global into these.
            "matched_spotify": 0,
            "preview_only": 0,
            "cancel_requested": False,
            "error_message": None,
        }
    t = threading.Thread(
        target=_run_public_playlist_job,
        args=(job_id, playlist_id, sess["user_id"], user_token, followed_note),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id, "playlist_id": playlist_id, "note": followed_note}


@app.get("/api/ingest/status/{job_id}")
def ingest_status(job_id: str, sess: dict = Depends(require_user)):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if int(job.get("user_id") or 0) != int(sess["user_id"]):
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)


@app.delete("/api/ingest/status/{job_id}", status_code=204)
def ingest_cancel(job_id: str, sess: dict = Depends(require_user)):
    """
    Request cancellation of an in-flight ingest job. Sets a flag the
    background runner polls between tracks; the runner exits cleanly on
    the next boundary and transitions the job to status='cancelled'.
    Tracks already inserted stay in the DB, audio files already saved
    stay on disk, and post-ingest z-score still runs so
    activation_relative reflects what did land.

    Idempotent: DELETE on an already-cancelled/completed/errored job
    returns 204 without side effects. Returns 404 if the job doesn't
    exist or belongs to another user (identical response to hide
    existence of other users' jobs).
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if int(job.get("user_id") or 0) != int(sess["user_id"]):
            raise HTTPException(status_code=404, detail="job not found")
        # No-op on already-terminal jobs — still return 204 for idempotency.
        if job.get("status") in ("complete", "error", "cancelled"):
            return Response(status_code=204)
        job["cancel_requested"] = True
    log.info("[ingest] user=%s requested cancel for job=%s", sess["user_id"], job_id)
    return Response(status_code=204)


# ---------------- Admin (chandan-only) ----------------
# The user_id of the single admin is stored in an env var so it's
# configurable per environment. Default = 1 (chandan on prod as of
# 2026-08-23; verified against the downloaded prod DB).
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "1"))


def require_admin(sess: dict = Depends(require_user)) -> dict:
    if int(sess["user_id"]) != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail={"error": "admin_only"})
    return sess


@app.get("/api/admin/users")
def admin_list_users(sess: dict = Depends(require_admin)):
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT u.id, u.display_name, u.spotify_user_id, u.spotify_display_name,
                   u.created_at,
                   (u.pin_hash IS NOT NULL) AS has_pin,
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
                "has_pin": bool(r["has_pin"]),
                "created_at": r["created_at"],
                "track_count": int(r["track_count"] or 0),
                "is_admin": int(r["id"]) == ADMIN_USER_ID,
            }
            for r in rows
        ]
    }


@app.get("/api/admin/users/{user_id}/stats")
def admin_user_stats(user_id: int, sess: dict = Depends(require_admin)):
    conn = get_conn()
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


@app.get("/api/admin/users/{user_id}/tracks")
def admin_user_tracks(
    user_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sess: dict = Depends(require_admin),
):
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.artist, t.album, t.mood, t.vibe_score_ml,
                   t.classification_source, ut.added_at, ut.play_count
            FROM user_tracks ut
            JOIN tracks t ON t.id = ut.track_id
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
                "play_count": int(r["play_count"] or 0),
            }
            for r in rows
        ]
    }


@app.delete("/api/admin/users/{user_id}", status_code=204)
def admin_delete_user(user_id: int, sess: dict = Depends(require_admin)):
    # Guardrail: admin can't delete themselves.
    if int(user_id) == ADMIN_USER_ID:
        raise HTTPException(status_code=400, detail={"error": "cannot_delete_admin"})
    conn = get_conn()
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


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
