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
from typing import Iterator, Optional

# Load .env before any env-var reads (config.py, ADMIN_USER_ID, VIBESCAPE_ML_MODE).
# No-op on platforms where python-dotenv isn't installed or no .env exists.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import asyncio
import numpy as np
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel

from db import ensure_db, get_conn
from dj_replay import library_mean as _dj_mean_of
from dj_replay import parse_events as _dj_parse_events
from dj_replay import replay as _dj_replay

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ingest"))
try:
    import config as app_config
except Exception:
    app_config = None

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
    # Two-phase ingest state. 'done' = playable, 'pending' = awaiting the
    # offline worker, 'no_preview' / 'failed' = terminal errors. The
    # library / mood-grid endpoints filter to 'done'.
    "ingestion_status",
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


def _hash_password(password: str) -> str:
    """
    Salted scrypt hash. Format: 'scrypt$<hex_salt>$<hex_hash>'. Same
    algorithm used by the (removed) PIN system — scrypt is memory-hard
    enough to slow down offline attacks against the SQLite dump.
    """
    if not password:
        raise ValueError("password required")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + dk.hex()


def _verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored or not password:
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
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(dk, expected)


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _display_name_from_email(email: Optional[str]) -> Optional[str]:
    """
    Derive a short display name from an email: the local-part truncated
    to 7 chars, with '...' appended if it was longer. 'chandan@x.com'
    → 'chandan'; 'chandanke@x.com' → 'chandan...'.
    """
    prefix = (email or "").split("@", 1)[0].strip()
    if not prefix:
        return None
    return prefix[:7] + "..." if len(prefix) > 7 else prefix


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
        "SELECT s.token, s.user_id, u.display_name, u.spotify_user_id, u.spotify_display_name, "
        "u.spotify_email, u.spotify_country, u.spotify_product, u.spotify_avatar_url, u.spotify_profile_url, "
        "u.created_at, u.last_login_at "
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


class SpotifyLinkBody(BaseModel):
    spotify_user_id: str
    spotify_display_name: Optional[str] = None


class EmailSignupBody(BaseModel):
    email: str
    password: str


class EmailLoginBody(BaseModel):
    email: str
    password: str


@app.post("/api/auth/signup")
def auth_email_signup(body: EmailSignupBody):
    """
    Create a new VibeScape-native user (no Spotify link) with an
    email + scrypt-hashed password. Seeds the user's library from the
    admin's tracks so the demo has something to play right away — user
    can add/remove from there.
    """
    email = _normalize_email(body.email)
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail={"error": "invalid_email"})
    if not body.password or len(body.password) < 6:
        raise HTTPException(status_code=422, detail={"error": "password_too_short"})
    pw_hash = _hash_password(body.password)
    display = _display_name_from_email(email) or "listener"

    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE email = ?", (email,)
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail={"error": "email_taken"})
        try:
            display = _unique_display_name(conn, display)
            cur = conn.execute(
                "INSERT INTO users (display_name, email, password_hash, last_login_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (display, email, pw_hash),
            )
            conn.commit()
            user_id = int(cur.lastrowid)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail={"error": "email_taken"})
        try:
            conn.execute(
                "INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) "
                "SELECT ?, track_id, 'email_signup_seed' FROM user_tracks WHERE user_id = ?",
                (user_id, ADMIN_USER_ID),
            )
            conn.commit()
        except Exception as e:
            log.warning("email signup seed failed for user %s: %s", user_id, e)
        token = _issue_session(conn, user_id)
    finally:
        conn.close()
    return {
        "user_id": user_id,
        "display_name": display,
        "email": email,
        "session_token": token,
        "spotify_connected": False,
        "is_premium": False,
        "is_admin": user_id == ADMIN_USER_ID,
        "is_guest": False,
    }


@app.post("/api/auth/login")
def auth_email_login(body: EmailLoginBody):
    """Password login for VibeScape-native accounts (no Spotify)."""
    email = _normalize_email(body.email)
    if not email:
        raise HTTPException(status_code=422, detail={"error": "invalid_email"})
    if not body.password:
        raise HTTPException(status_code=422, detail={"error": "password_required"})
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, display_name, email, password_hash, spotify_user_id, spotify_product "
            "FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row or not _verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail={"error": "bad_credentials"})
        user_id = int(row["id"])
        conn.execute(
            "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )
        conn.commit()
        token = _issue_session(conn, user_id)
    finally:
        conn.close()
    return {
        "user_id": user_id,
        "display_name": row["display_name"],
        "email": row["email"],
        "session_token": token,
        "spotify_connected": bool(row["spotify_user_id"]),
        "is_premium": row["spotify_product"] == "premium",
        "is_admin": user_id == ADMIN_USER_ID,
        "is_guest": False,
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
    product = sess.get("spotify_product")
    return {
        "user_id": sess["user_id"],
        "display_name": sess["display_name"],
        "spotify_connected": bool(sess.get("spotify_user_id")),
        "spotify_display_name": sess.get("spotify_display_name"),
        "spotify_email": sess.get("spotify_email"),
        "spotify_country": sess.get("spotify_country"),
        "spotify_product": product,
        "is_premium": product == "premium",
        "avatar_url": sess.get("spotify_avatar_url"),
        "profile_url": sess.get("spotify_profile_url"),
        "created_at": sess.get("created_at"),
        "last_login_at": sess.get("last_login_at"),
        "is_admin": int(sess["user_id"]) == ADMIN_USER_ID,
        "is_guest": sess.get("display_name") == "Guest",
    }


class SpotifyOAuthBody(BaseModel):
    code: str
    redirect_uri: Optional[str] = None


class SpotifyRefreshBody(BaseModel):
    refresh_token: str


def _spotify_token_exchange(code: str, redirect_uri: str) -> dict:
    client_id = getattr(app_config, "SPOTIFY_CLIENT_ID", "") if app_config else ""
    client_secret = getattr(app_config, "SPOTIFY_CLIENT_SECRET", "") if app_config else ""
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail={"error": "spotify_not_configured"})
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )
    if r.status_code != 200:
        log.warning("spotify token exchange failed: %s %s", r.status_code, r.text[:200])
        raise HTTPException(status_code=400, detail={"error": "spotify_code_invalid"})
    return r.json()


def _spotify_me(access_token: str) -> dict:
    r = requests.get(
        "https://api.spotify.com/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail={"error": "spotify_me_failed"})
    return r.json()


def _unique_display_name(conn, base: str) -> str:
    """Append a suffix if display_name is taken (users.display_name is UNIQUE)."""
    base = (base or "").strip() or "Listener"
    name = base
    i = 2
    while conn.execute(
        "SELECT 1 FROM users WHERE display_name = ?",
        (name,),
    ).fetchone():
        name = f"{base} {i}"
        i += 1
        if i > 999:
            name = f"{base} {secrets.token_hex(3)}"
            break
    return name


def _pick_display_name_from_spotify(conn, sp_display: Optional[str],
                                    sp_email: Optional[str],
                                    sp_id: str) -> str:
    """
    Choose a display name for a new Spotify-linked user, preferring the
    Spotify display_name if it isn't already taken. If it is taken, fall
    back to the email-derived short name (before the numeric suffix
    dance in _unique_display_name kicks in) — cleaner than 'Chandan 2'.
    """
    def _taken(n: str) -> bool:
        if not n:
            return True
        return bool(conn.execute(
            "SELECT 1 FROM users WHERE display_name = ?", (n,),
        ).fetchone())

    if sp_display:
        candidate = sp_display.strip()
        if candidate and not _taken(candidate):
            return candidate
    from_email = _display_name_from_email(sp_email)
    if from_email and not _taken(from_email):
        return from_email
    id_short = (sp_id or "user")[:7]
    if id_short and not _taken(id_short):
        return id_short
    return _unique_display_name(conn, sp_display or from_email or id_short or "Listener")


@app.post("/api/auth/spotify-oauth")
def auth_spotify_oauth(body: SpotifyOAuthBody):
    """
    Log in (or sign up) using a Spotify authorization code. Exchanges the
    code for tokens, fetches the caller's Spotify identity, and upserts
    a VibeScape user keyed on spotify_user_id. Returns a session_token
    payload identical in shape to /api/auth/login.
    """
    if not body.code:
        raise HTTPException(status_code=422, detail={"error": "code required"})
    redirect_uri = body.redirect_uri or (
        getattr(app_config, "SPOTIFY_REDIRECT_URI", "") if app_config else ""
    )
    if not redirect_uri:
        raise HTTPException(status_code=422, detail={"error": "redirect_uri required"})

    tokens = _spotify_token_exchange(body.code, redirect_uri)
    access_token = tokens.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail={"error": "spotify_no_access_token"})

    me = _spotify_me(access_token)
    sp_id = me.get("id")
    if not sp_id:
        raise HTTPException(status_code=400, detail={"error": "spotify_no_id"})
    sp_display = me.get("display_name") or sp_id
    sp_email = me.get("email")
    sp_country = me.get("country")
    sp_product = me.get("product")  # 'premium' | 'free' | 'open'
    sp_profile_url = ((me.get("external_urls") or {}).get("spotify")) or None
    images = me.get("images") or []
    sp_avatar_url = images[0]["url"] if images and images[0].get("url") else None

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, display_name FROM users WHERE spotify_user_id = ?",
            (sp_id,),
        ).fetchone()
        if row:
            user_id = int(row["id"])
            display_name = row["display_name"]
            conn.execute(
                "UPDATE users SET spotify_display_name = ?, spotify_email = ?, "
                "spotify_country = ?, spotify_product = ?, spotify_avatar_url = ?, "
                "spotify_profile_url = ?, last_login_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (sp_display, sp_email, sp_country, sp_product, sp_avatar_url, sp_profile_url, user_id),
            )
            conn.commit()
        else:
            display_name = _pick_display_name_from_spotify(conn, sp_display, sp_email, sp_id)
            cur = conn.execute(
                "INSERT INTO users (display_name, spotify_user_id, spotify_display_name, "
                "spotify_email, spotify_country, spotify_product, spotify_avatar_url, "
                "spotify_profile_url, last_login_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (display_name, sp_id, sp_display, sp_email, sp_country, sp_product,
                 sp_avatar_url, sp_profile_url),
            )
            conn.commit()
            user_id = int(cur.lastrowid)
        token = _issue_session(conn, user_id)
    finally:
        conn.close()

    return {
        "user_id": user_id,
        "display_name": display_name,
        "session_token": token,
        "spotify_connected": True,
        "spotify_display_name": sp_display,
        "spotify_email": sp_email,
        "spotify_country": sp_country,
        "spotify_product": sp_product,
        "is_premium": sp_product == "premium",
        "avatar_url": sp_avatar_url,
        "profile_url": sp_profile_url,
        "is_admin": user_id == ADMIN_USER_ID,
        # Pass the raw Spotify access token back so the frontend can call
        # /v1/me, /v1/me/tracks, etc. without a second consent round-trip.
        "spotify_access_token": access_token,
        "spotify_refresh_token": tokens.get("refresh_token"),
        "spotify_expires_in": tokens.get("expires_in"),
        "spotify_scope": tokens.get("scope"),
    }


@app.post("/api/spotify/refresh")
def spotify_refresh(body: SpotifyRefreshBody):
    """
    Exchange a Spotify refresh_token for a fresh access_token using the
    server-side client_secret. This lets the browser avoid an interactive
    re-consent (and PKCE dance) when its short-lived access_token expires.
    Refresh tokens minted via the Authorization Code flow require
    client_secret to refresh -- doing this on the server keeps the secret
    off the wire.
    """
    if not body.refresh_token:
        raise HTTPException(status_code=422, detail={"error": "refresh_token required"})
    client_id = getattr(app_config, "SPOTIFY_CLIENT_ID", "") if app_config else ""
    client_secret = getattr(app_config, "SPOTIFY_CLIENT_SECRET", "") if app_config else ""
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail={"error": "spotify_not_configured"})
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )
    if r.status_code != 200:
        log.warning("spotify refresh failed: %s %s", r.status_code, r.text[:200])
        raise HTTPException(status_code=400, detail={"error": "spotify_refresh_failed"})
    j = r.json()
    return {
        "access_token": j.get("access_token"),
        "expires_in": j.get("expires_in"),
        "scope": j.get("scope"),
        # Spotify may rotate the refresh_token; pass through when present so
        # the client can update its stored copy.
        "refresh_token": j.get("refresh_token"),
    }


@app.get("/api/demo/moods")
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
    conn = get_conn()
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


@app.post("/api/auth/guest")
def auth_guest():
    """
    Zero-friction "just listen" mode. Reuses a shared 'Guest' user
    (creating one on first call) and issues a session token. All guests
    share the same library — treat this as a demo profile, not a real
    account.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, display_name FROM users WHERE display_name = 'Guest'",
        ).fetchone()
        if row:
            user_id = int(row["id"])
            display_name = row["display_name"]
        else:
            cur = conn.execute(
                "INSERT INTO users (display_name, last_login_at) "
                "VALUES ('Guest', CURRENT_TIMESTAMP)",
            )
            conn.commit()
            user_id = int(cur.lastrowid)
            display_name = "Guest"

        # Seed the guest library with the full ingested catalog so
        # "just listen" plays from every done track.
        #
        # This runs on EVERY guest login, not just the first. It used to
        # be guarded by "only if the library is empty", which made it a
        # one-shot snapshot: it fired once when the catalogue held ~1527
        # done tracks and never again, so the 2256 tracks ingested after
        # that were invisible to guests while the code read as if it
        # seeded "the full catalog".
        #
        # Running it unconditionally is what makes it self-healing as
        # ingestion adds tracks. INSERT OR IGNORE already carries the
        # idempotency — (user_id, track_id) is the primary key, so rows
        # that exist are skipped and a guest's play_count/last_played
        # are never overwritten.
        try:
            conn.execute(
                "INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) "
                "SELECT ?, id, 'guest_seed' FROM tracks "
                "WHERE ingestion_status = 'done'",
                (user_id,),
            )
            conn.commit()
        except Exception as e:
            log.warning("guest seed failed: %s", e)
        token = _issue_session(conn, user_id)
    finally:
        conn.close()
    return {
        "user_id": user_id,
        "display_name": display_name,
        "session_token": token,
        "is_admin": False,
        "is_guest": True,
    }


@app.post("/api/auth/spotify-link")
def auth_spotify_link(body: SpotifyLinkBody, sess: dict = Depends(require_user)):
    if not body.spotify_user_id:
        raise HTTPException(status_code=422, detail={"error": "spotify_user_id required"})
    conn = get_conn()
    try:
        """
        This used to be a bare UPDATE, which had two problems.

        The visible one: users.spotify_user_id is UNIQUE, so linking an
        identity that already belonged to another row raised IntegrityError
        out of the handler as a 500. That happens on an ordinary path — sign
        in with email, then connect the same Spotify account you already made
        a Spotify-login account with.

        The one the constraint was accidentally hiding: guests all share a
        single row keyed on display_name='Guest' (see auth_guest). Linking a
        real person's Spotify identity to it would have stamped their name
        and account onto the demo profile EVERY other guest is using. That is
        refused outright, not reported as a conflict, because it is not
        something the caller should be able to retry their way out of.
        """
        if (sess.get("display_name") or "") == "Guest":
            return {
                "ok": False,
                "error": "guest_cannot_link",
                "spotify_user_id": body.spotify_user_id,
            }

        owner = conn.execute(
            "SELECT id FROM users WHERE spotify_user_id = ?",
            (body.spotify_user_id,),
        ).fetchone()
        if owner and int(owner["id"]) != int(sess["user_id"]):
            # Someone else holds it. Not an error the user caused, and the
            # caller treats this as fire-and-forget, so say so plainly rather
            # than raising.
            return {
                "ok": False,
                "error": "spotify_account_already_linked",
                "spotify_user_id": body.spotify_user_id,
            }

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
    where = ["ut.user_id = ?", "t.ingestion_status = 'done'",
             f"{vibe_expr} BETWEEN ? AND ?"]
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
        f"AND t.ingestion_status = 'done' "
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


MERT_MODEL_VERSION = "mert_v1_95m_fp32_30s"
MERT_DIM = 768

# Fused variant: MERT (768) + scalar features (9) + language one-hot (10 langs + 'other')
# Recipe defined in scripts/_recommender_feasibility.py + scripts/_backfill_fused_embeddings.py.
FUSED_MODEL_VERSION = "fused_v1_mert_scalar_lang"
FUSED_SCALAR_DIMS = 9
FUSED_LANG_DIMS = 11
FUSED_DIM = MERT_DIM + FUSED_SCALAR_DIMS + FUSED_LANG_DIMS  # 788

# Default DJ variant; override per-request with SimilarBody.variant.
_DJ_VARIANT_DEFAULT = (os.environ.get("DJ_EMBEDDING_VARIANT") or "fused").strip().lower()
if _DJ_VARIANT_DEFAULT not in ("fused", "mert"):
    _DJ_VARIANT_DEFAULT = "fused"


def _variant_spec(variant: str):
    """Return (model_version, dim) for the requested variant."""
    v = (variant or "").strip().lower()
    if v == "mert":
        return MERT_MODEL_VERSION, MERT_DIM
    return FUSED_MODEL_VERSION, FUSED_DIM


_ANCHOR_COLS = (
    "id, apple_id, spotify_id, mood, "
    "vibe_score, vibe_score_ml, energy_pred, "
    "danceability_pred, valence_pred"
)


def _resolve_anchor(conn, track_key):
    """Resolve a track_key to a tracks row.

    Accepts either a spotify_id string (canonical external key) or a numeric
    value matching the internal tracks.id PK. The legacy apple_id lookup path
    has been retired — every row in the DB has a spotify_id (verified in
    audit), and internal callers now pass either spotify_id or tracks.id.
    """
    # Numeric input → treat as internal PK. Ints on the wire come from the
    # frontend's hot path (dj excludes/pos/neg use t.id directly to avoid
    # an N-round-trip resolution loop).
    if isinstance(track_key, int):
        return conn.execute(
            f"SELECT {_ANCHOR_COLS} FROM tracks WHERE id = ?",
            (track_key,),
        ).fetchone()
    # String input → try spotify_id first (fast path), then fall back to
    # int-parseable string as internal id.
    row = conn.execute(
        f"SELECT {_ANCHOR_COLS} FROM tracks WHERE spotify_id = ?",
        (str(track_key),),
    ).fetchone()
    if row:
        return row
    try:
        tid_int = int(track_key)
    except (TypeError, ValueError):
        return None
    return conn.execute(
        f"SELECT {_ANCHOR_COLS} FROM tracks WHERE id = ?",
        (tid_int,),
    ).fetchone()


def _embedding_column_for(model_version: str) -> Optional[str]:
    """Map a logical model_version to the physical column in the new
    Option-A track_embeddings layout (one row per track, both variants
    inline as separate typed vector columns)."""
    if model_version == MERT_MODEL_VERSION:
        return "mert_embedding"
    if model_version == FUSED_MODEL_VERSION:
        return "fused_embedding"
    return None


def _is_turso() -> bool:
    return (os.environ.get("DB_BACKEND") or "").strip().lower() in ("turso", "libsql")


def _decode_embedding_cell(val) -> Optional[np.ndarray]:
    """Turso's HTTP protocol returns F32_BLOB cells in a form our
    db_client shim decodes to empty bytes. Handle both cases:
      - Real bytes (local sqlite path): np.frombuffer
      - Text form '[a,b,c,...]' (Turso via vector_extract()): parse
    Returns None if the cell is empty / invalid."""
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray, memoryview)):
        if len(val) == 0:
            return None
        return np.frombuffer(val, dtype=np.float32).astype(np.float32, copy=True)
    if isinstance(val, str):
        s = val.strip()
        if not s or s == "[]":
            return None
        s = s.lstrip("[").rstrip("]")
        try:
            return np.array([float(x) for x in s.split(",")], dtype=np.float32)
        except ValueError:
            return None
    return None


def _load_mert_vec(conn, track_id: int,
                   model_version: str = MERT_MODEL_VERSION,
                   expected_dim: int = MERT_DIM) -> Optional[np.ndarray]:
    col = _embedding_column_for(model_version)
    if col is None:
        return None
    # On Turso, F32_BLOB reads back as empty bytes over HTTP — use
    # vector_extract() to get the text form we can parse.
    if _is_turso():
        row = conn.execute(
            f"SELECT vector_extract({col}) AS emb "
            f"FROM track_embeddings WHERE track_id = ?",
            (track_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT {col} AS emb FROM track_embeddings WHERE track_id = ?",
            (track_id,),
        ).fetchone()
    if not row:
        return None
    vec = _decode_embedding_cell(row["emb"])
    if vec is None or vec.shape[0] != expected_dim:
        return None
    return vec


def _load_mert_vecs_bulk(conn, track_ids: list,
                         model_version: str = MERT_MODEL_VERSION,
                         expected_dim: int = MERT_DIM) -> dict:
    """Return {track_id: np.ndarray} for track_ids that have an embedding
    for the requested variant. Rows with NULL in the target column are
    skipped (track has the other variant but not this one)."""
    out: dict = {}
    if not track_ids:
        return out
    col = _embedding_column_for(model_version)
    if col is None:
        return out
    # Chunk to keep SQL param counts sane.
    CHUNK = 400
    turso = _is_turso()
    select_expr = f"vector_extract({col})" if turso else col
    for i in range(0, len(track_ids), CHUNK):
        chunk = track_ids[i:i + CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        sql = (
            f"SELECT track_id, {select_expr} AS emb FROM track_embeddings "
            f"WHERE track_id IN ({placeholders}) AND {col} IS NOT NULL"
        )
        rows = conn.execute(sql, tuple(chunk)).fetchall()
        for r in rows:
            v = _decode_embedding_cell(r["emb"])
            if v is None or v.shape[0] != expected_dim:
                continue
            out[int(r["track_id"])] = v
    return out


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return v
    return v / n


# Server-side caps on client-supplied id lists. The frontend already trims
# (DJ_MAX_SENT_IDS / DJ_MAX_EXCLUDES in frontend-next/src/features/queue/dj.js),
# but a request body is user input: without these a large list means one
# embedding row loaded per positive/negative id, and an arbitrarily long
# inlined NOT IN (...) clause for excludes.
_DJ_MAX_WEIGHTED_IDS = 50
_DJ_MAX_EXCLUDE_IDS = 200


# ---------------------------------------------------------------------------
# DJ recency re-ranking.  Design + measurements: docs/dj-recency-plan.md.
#
#   final(c) = score(c) - lambda * P(c)
#   lambda   = W * (score[#1] - score[#limit])   in the UN-penalised order
#   P(c)     = P_play(c) + P_skip(c)             >= 0, always
#   P_play   = 2 ^ (-dt_played  / H_play)        0 if never played
#   P_skip   = B * 2 ^ (-dt_skipped / H_skip)    0 if never skipped
#   B        = 2.0 - 1.5 * bail_fraction         in [0.5, 2.0]; 1.0 if unknown
#
# Three properties this code must keep, in order of importance:
#
#   1. NO TERM MAY RAISE A SCORE.  P is a sum of non-negative terms and it is
#      subtracted.  A user with no user_track_stats rows gets P = 0 for every
#      candidate, so every score shifts by zero and the ordering is exactly
#      what it was before this feature existed.  The feature is inert until
#      there is listening history.  Do not add a term that can go negative.
#   2. lambda is RELATIVE to the width of the output window, never absolute.
#      The usable cosine signal across the top-8 is ~0.016 wide sitting at
#      ~0.95 (measured, 2026-10-02, 3,783 fused vectors).  An absolute lambda
#      is either inside the noise or annihilates the vector match, and it
#      silently becomes wrong on the 'mert' variant or on _similar_vibe, whose
#      "score" is a negated weighted-L1 distance on a completely different
#      scale.  CONSEQUENCE: lambda values are NOT comparable between the DJ
#      path and the vibe path.  A much larger lambda on the vibe path is
#      correct and is the whole point.
#   3. POSITIVE TERMS READ u_* ONLY; NEGATIVE TERMS MAY READ BOTH.  The
#      schema's u_/s_ prohibition is on averaging the recommender's own output
#      back in as stated preference -- that is a prohibition on a POSITIVE
#      feedback path.  Every term here is non-positive and every skip is a
#      human act regardless of who moved the slider, so summing u_ and s_ skip
#      counters is safe.  The rule is about the DIRECTION of the effect, not
#      about the column prefix.  If anyone ever adds a term that RAISES a
#      score, it must read u_* only.
#
# Columns read: last_played, last_skipped_at, (u_+s_)skip_count,
# (u_+s_)skip_position_ms_sum, tracks.duration_ms.  Nothing else.
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("[dj] %s=%r is not a number; using %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


# Defaults live in code so the behaviour is readable from source; the env
# override lets two Cloud Run revisions of the SAME image be compared without
# a rebuild.  Deliberately NOT request-body parameters: a client-settable
# ranking knob becomes a contract you can never change.
_DJ_RECENCY_HALFLIFE_H = _env_float("DJ_RECENCY_HALFLIFE_H", 72.0)
_DJ_SKIP_HALFLIFE_H = _env_float("DJ_SKIP_HALFLIFE_H", 168.0)
# A play counts as "qualified" (updates user_track_stats.last_played) iff
# play_end.reason == 'completed' OR position_ms >= DJ_QUALIFIED_PLAY_MS. 90 s
# is the common recommender-signal bar for a real listen (streaming services
# count a stream at ~30 s, but we want more than "it autoplayed and I didn't
# stop it"). A 2-minute skip still qualifies: having had the track playing
# for two minutes is a real listening event regardless of why the user moved
# on. See _accumulate_stats and scripts/rebuild_user_track_stats.py.
_DJ_QUALIFIED_PLAY_MS = _env_int("DJ_QUALIFIED_PLAY_MS", 90000)
# DJ_RECENCY_WEIGHT=0 is the complete kill switch. It short-circuits before
# the LEFT JOIN is added and before the pool is widened, so with it set the
# emitted SQL and the response are byte-for-byte what they were pre-feature.
_DJ_RECENCY_WEIGHT = _env_float("DJ_RECENCY_WEIGHT", 2.0)
# Candidate pool: POOL = min(POOL_MAX, max(POOL_BASE, 12 * limit)).
#
# THE POOL SIZE IS THE CEILING ON WHAT THIS FEATURE CAN EVER DO. The penalty
# only reorders WITHIN the retrieved pool -- a track that falls below the pool
# cut can never be promoted into the output, no matter how fresh it is and no
# matter how heavily everything above it is penalised. Widening the pool is
# the only way to raise that ceiling; raising W is not.
_DJ_RECENCY_POOL = _env_int("DJ_RECENCY_POOL", 150)
_DJ_RECENCY_POOL_MAX = _env_int("DJ_RECENCY_POOL_MAX", 300)
# The guest row is shared: auth_guest keys every guest on one users row with
# display_name='Guest', so all guests pool one listening history and one
# guest's skips suppress tracks for every other guest. Accepted by default --
# at demo volume the harm is bounded to "tracks any guest touched in the last
# ~2 weeks", and a less repetitive shared demo is arguably mildly good. Set
# DJ_RECENCY_SKIP_GUEST=1 to opt the guest row out. Watch for this if guest
# traffic ever reaches a few hundred plays a week: the symptom presents as
# "the demo recommends weird tracks" with no obvious cause.
_DJ_RECENCY_SKIP_GUEST = (
    (os.environ.get("DJ_RECENCY_SKIP_GUEST") or "").strip().lower()
    in ("1", "true", "yes", "on")
)

# Below this the output window is degenerate (every candidate identical, or
# fewer than two candidates) and dividing the penalty into it would
# manufacture an ordering out of nothing. Skip re-ranking instead.
_DJ_RECENCY_MIN_WINDOW = 1e-6

# The four stats columns, as they appear aliased on the candidate queries.
# Summing u_ and s_ is deliberate -- see property 3 above.
_UTS_SELECT = (
    "       s.last_played                             AS uts_last_played,\n"
    "       s.last_skipped_at                         AS uts_last_skipped_at,\n"
    "       COALESCE(s.u_skip_count, 0)\n"
    "     + COALESCE(s.s_skip_count, 0)               AS uts_skip_count,\n"
    "       COALESCE(s.u_skip_position_ms_sum, 0)\n"
    "     + COALESCE(s.s_skip_position_ms_sum, 0)     AS uts_skip_pos_sum"
)
# Joins on user_track_stats' primary key (user_id, track_id), so this is an
# index seek per candidate row inside the DB and adds ZERO round trips. A
# per-candidate stats query would be one Hrana POST each (backlog 2.2) --
# 150 HTTPS round trips per recommendation. That option is not on the table.
_UTS_JOIN = "LEFT JOIN user_track_stats s ON s.user_id = ut.user_id AND s.track_id = t.id"


def _dj_recency_enabled(display_name=None) -> bool:
    """The single gate. False => not one byte of this feature runs."""
    if _DJ_RECENCY_WEIGHT <= 0:
        return False
    if _DJ_RECENCY_SKIP_GUEST and (display_name or "") == "Guest":
        return False
    return True


def _dj_recency_pool(limit: int) -> int:
    return max(limit, min(_DJ_RECENCY_POOL_MAX, max(_DJ_RECENCY_POOL, 12 * limit)))


def _parse_sql_ts(value):
    """Parse a SQLite/libSQL timestamp into an aware UTC datetime, or None.

    Verified 2026-10-02: the only writer is _upsert_track_stats, which stores
    datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') -- directly
    comparable with datetime('now'), also UTC. The ISO/'Z' branches are
    defensive, for a future writer or a hand-edited row.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1].strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if "+" in s:
        s = s.split("+", 1)[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _recency_penalty(now, last_played, last_skipped_at,
                     skip_count=0, skip_pos_sum=0, duration_ms=None,
                     halflife_h=None):
    """Pure. Returns (P, explain) with P >= 0 ALWAYS. Takes no connection.

    `now` is an aware UTC datetime. The timestamps are whatever the DB handed
    back; they are parsed here.

    Never played means P_play = 0 -- maximally fresh. It is NOT imputed to a
    mid-range value and NOT imputed to "very old" (which happens to give the
    same answer today, for the wrong reason, and would stop being true the
    moment anyone adds a first_played_at term). That is what makes a user with
    no stats a no-op.

    dt is clamped at >= 0: a client clock running ahead of the server would
    otherwise give 2^(+k) and an unbounded SCORE-RAISING penalty. A future
    timestamp yields P = 1 (maximally recent), not P = 40.

    last_played and last_skipped_at are INDEPENDENT since the DJ_QUALIFIED_PLAY_MS
    change: a short skip updates only last_skipped_at, a qualifying listen
    (completed OR >=DJ_QUALIFIED_PLAY_MS, default 90s) updates only last_played,
    and a long-but-skipped play updates both. The two penalty terms are
    ADDITIVE rather than max(): a track skipped an hour ago after two minutes
    of play carries both and should be the most suppressed thing in the pool.
    """
    ex = {
        "p_play": 0.0,
        "p_skip": 0.0,
        "bail_fraction": None,
        "skip_amplitude": None,
        "last_played": last_played if isinstance(last_played, str) else (
            str(last_played) if last_played is not None else None),
        "last_skipped_at": last_skipped_at if isinstance(last_skipped_at, str) else (
            str(last_skipped_at) if last_skipped_at is not None else None),
    }

    def _hours_since(ts):
        parsed = _parse_sql_ts(ts)
        if parsed is None:
            return None
        return max(0.0, (now - parsed).total_seconds() / 3600.0)

    # Per-user half-life falls back to the env default so existing callers
    # (and users with no stats) see pre-feature behaviour byte-for-byte.
    h_half = halflife_h if (halflife_h is not None and halflife_h > 0) else _DJ_RECENCY_HALFLIFE_H
    h_play = _hours_since(last_played)
    if h_play is not None and h_half > 0:
        ex["p_play"] = float(2.0 ** (-h_play / h_half))

    h_skip = _hours_since(last_skipped_at)
    if h_skip is not None and _DJ_SKIP_HALFLIFE_H > 0:
        # Depth of the bail. Mean skip position, not the most recent one:
        # the aggregate stores a sum and a count, and going to track_events
        # for the last position would be a second query per candidate.
        # The mean is monotone in the right direction and that is enough.
        amp = 1.0
        try:
            sc = int(skip_count or 0)
            dur = int(duration_ms or 0)
        except (TypeError, ValueError):
            sc, dur = 0, 0
        if sc > 0 and dur > 0:
            frac = (float(skip_pos_sum or 0) / sc) / float(dur)
            frac = max(0.0, min(1.0, frac))
            amp = 2.0 - 1.5 * frac
            ex["bail_fraction"] = frac
        ex["skip_amplitude"] = amp
        ex["p_skip"] = float(amp * (2.0 ** (-h_skip / _DJ_SKIP_HALFLIFE_H)))

    # Belt and braces. P is non-negative by construction above; this makes it
    # non-negative by assertion too, so a future retune of the constants
    # cannot accidentally turn the penalty into a boost.
    p = max(0.0, ex["p_play"] + ex["p_skip"])
    ex["p"] = p
    return p, ex


class _RecencyCandidate:
    """One candidate on the way through the re-ranker.

    `score` is generic: HIGHER IS BETTER, whatever the path's scale. DJ passes
    cosine similarity; _similar_vibe passes -distance. Nothing below knows
    which.
    """
    __slots__ = ("score", "payload", "last_played", "last_skipped_at",
                 "skip_count", "skip_pos_sum", "duration_ms")

    def __init__(self, score, payload, last_played=None, last_skipped_at=None,
                 skip_count=0, skip_pos_sum=0, duration_ms=None):
        self.score = float(score)
        self.payload = payload
        self.last_played = last_played
        self.last_skipped_at = last_skipped_at
        self.skip_count = skip_count
        self.skip_pos_sum = skip_pos_sum
        self.duration_ms = duration_ms


def _rerank_by_recency(cands, limit, now=None, explain=False, halflife_h=None):
    """Re-rank `cands` (already sorted best-first) and return the first
    `limit`. Each returned item is (candidate, final_score, explain|None).

    Pure apart from the clock. Returns the input order untouched whenever the
    re-rank cannot be meaningful:
      * the gate is off (handled by the caller, which also skips the JOIN),
      * fewer than two candidates,
      * the output window is below _DJ_RECENCY_MIN_WINDOW.

    If every candidate carries the SAME P, every score shifts by the same
    constant and the order is unchanged -- which is why "small library where
    everything is recent" degrades to today's behaviour for free, and why P
    must NOT be normalised within the pool.
    """
    n = len(cands)
    if n == 0:
        return []
    now = now or datetime.now(timezone.utc)

    window_hi = cands[0].score
    window_lo = cands[min(limit, n) - 1].score
    window = window_hi - window_lo
    if n < 2 or window < _DJ_RECENCY_MIN_WINDOW:
        out = []
        for i, c in enumerate(cands[:limit]):
            ex = None
            if explain:
                ex = {"sim": c.score, "lambda": 0.0, "p_play": 0.0, "p_skip": 0.0,
                      "penalty": 0.0, "final": c.score,
                      "rank_before": i + 1, "rank_after": i + 1,
                      "degenerate_window": True,
                      "last_played": None, "last_skipped_at": None}
            out.append((c, c.score, ex))
        return out

    lam = _DJ_RECENCY_WEIGHT * window

    scored = []
    for i, c in enumerate(cands):
        p, ex = _recency_penalty(
            now, c.last_played, c.last_skipped_at,
            c.skip_count, c.skip_pos_sum, c.duration_ms,
            halflife_h=halflife_h,
        )
        penalty = lam * p
        final = c.score - penalty
        scored.append([c, final, i, ex, penalty, p])

    # Stable: equal finals keep their un-penalised relative order.
    scored.sort(key=lambda e: -e[1])

    out = []
    for rank_after, entry in enumerate(scored[:limit]):
        c, final, rank_before, ex, penalty, _p = entry
        block = None
        if explain:
            block = {
                "sim": c.score,
                "lambda": lam,
                "p_play": ex["p_play"],
                "p_skip": ex["p_skip"],
                "bail_fraction": ex["bail_fraction"],
                "penalty": penalty,
                "final": final,
                "rank_before": rank_before + 1,
                "rank_after": rank_after + 1,
                "last_played": ex["last_played"],
                "last_skipped_at": ex["last_skipped_at"],
            }
        out.append((c, final, block))
    return out


def _fetch_recency_stats(conn, user_id, now=None):
    """{track_id: (last_played, last_skipped_at, skip_count, skip_pos_sum)}
    for everything this user touched in the last 30 days.

    ONE query per request, used only on the numpy / cold-start paths where
    there is no candidate query to hang a LEFT JOIN off. The Turso ranking
    path never calls this -- it gets the same columns on the JOIN it was
    already issuing.

    The 30-day cutoff is safe: P_play(30 d) = 0.0009, below the float noise on
    the similarity scores. Served by idx_user_track_stats_recent
    (user_id, last_played DESC) for the first predicate.
    """
    out = {}
    try:
        rows = conn.execute(
            "SELECT track_id, last_played, last_skipped_at, "
            "       COALESCE(u_skip_count, 0) + COALESCE(s_skip_count, 0) AS skip_count, "
            "       COALESCE(u_skip_position_ms_sum, 0) "
            "     + COALESCE(s_skip_position_ms_sum, 0) AS skip_pos_sum "
            "FROM user_track_stats "
            "WHERE user_id = ? "
            "  AND (last_played     > datetime('now', '-30 day') "
            "    OR last_skipped_at > datetime('now', '-30 day'))",
            (user_id,),
        ).fetchall()
    except Exception as e:
        # No stats table (prod before _turso_create_event_tables.py has run),
        # or any other read failure. Degrade to "no history", which is the
        # pre-feature behaviour, rather than failing the recommendation.
        log.warning("[dj] recency stats unavailable: %s", e)
        return out
    for r in rows:
        out[int(r["track_id"])] = (
            r["last_played"], r["last_skipped_at"],
            r["skip_count"], r["skip_pos_sum"],
        )
    return out


# Cold-start gate for the per-user half-life: below this many plays, the
# ema is too noisy to beat the fixed env default. 5 is deliberately small --
# an EMA with alpha=0.1 that has seen 4 updates is still ~65% initialisation
# weight; by the fifth it is ~59%, but we have at least a signal.
_DJ_USER_HALFLIFE_MIN_PLAYS = 5
# Hard bounds on the derived half-life. 12 h keeps a very heavy listener from
# shrinking the "forget this track" window below one listening session; 336 h
# (two weeks) keeps a very light listener from suppressing a track they heard
# once a month ago. Both are generous relative to the shipping fixed default
# of 72 h, which lands inside the window at any realistic cadence.
_DJ_USER_HALFLIFE_MIN_H = 12.0
_DJ_USER_HALFLIFE_MAX_H = 336.0
# Mapping constant: half-life ~= 3 * typical interval between plays. A user
# who plays every 24 h gets 72 h (matches today's default), a 4-hourly heavy
# listener gets 12 h, a 100-hourly light listener gets 300 h.
_DJ_USER_HALFLIFE_MULT = 3.0


def _fetch_user_halflife(conn, user_id) -> float:
    """Return H_play for this user. Falls back to the env default on cold start,
    unknown user, missing table (prod before _turso_create_user_stats.py has
    run), or any read failure.

    One SELECT per request, keyed on the PK. Pure read -- no writes, no side
    effects on the recency re-rank fast path.
    """
    try:
        row = conn.execute(
            "SELECT ema_interval_h, play_count FROM user_stats WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    except Exception as e:
        log.warning("[dj] user_stats unavailable: %s", e)
        return _DJ_RECENCY_HALFLIFE_H
    if row is None:
        return _DJ_RECENCY_HALFLIFE_H
    try:
        pc = int(row["play_count"] or 0)
        ema = row["ema_interval_h"]
        ema_f = float(ema) if ema is not None else None
    except (TypeError, ValueError):
        return _DJ_RECENCY_HALFLIFE_H
    if pc < _DJ_USER_HALFLIFE_MIN_PLAYS or ema_f is None or ema_f <= 0:
        return _DJ_RECENCY_HALFLIFE_H
    h = _DJ_USER_HALFLIFE_MULT * ema_f
    return max(_DJ_USER_HALFLIFE_MIN_H, min(_DJ_USER_HALFLIFE_MAX_H, h))


class SimilarBody(BaseModel):
    mode: Optional[str] = None
    positive_ids: Optional[list] = None
    negative_ids: Optional[list] = None
    # DJ replay log, oldest first: [{id, action, played_ratio, ts}]. When
    # present it replaces positive_ids / negative_ids entirely — see
    # backend/dj_replay.py. [frontend contract] new optional field.
    events: Optional[list] = None
    exclude_ids: Optional[list] = None
    limit: Optional[int] = None
    # "fused" (default) | "mert". Selects which embedding variant DJ mode uses.
    variant: Optional[str] = None
    # Opt-in per-candidate recency breakdown on each track. Additive and off
    # by default, so no existing client sees a change.
    # [frontend contract] — new optional request field, new optional response
    # field `explain` on each track, plus `final_score` alongside `score`.
    explain: Optional[bool] = False


def _similar_vibe(track_key: str, limit: int, user_id,
                  display_name=None, explain: bool = False):
    """Existing weighted L1 scalar-feature similarity. Returns response dict.

    Recency re-ranked on the same terms as DJ mode, and that is deliberate:
    _similar_dj falls back here whenever the seed has no vector in either
    variant, which is exactly the moment the library is thinnest and repeats
    are most likely. If the penalty existed only on the DJ path it would
    silently disappear at the worst possible time, with `mode_used` as the
    only evidence.

    This path has no embedding at all. It ranks by a weighted-L1 `distance`
    computed in SQL, roughly 0-3.3 and ASCENDING, so it feeds the shared
    re-ranker `score = -distance`. Because lambda is window-relative it needs
    no special-casing -- but lambda here is far larger in absolute terms than
    on the cosine path. That is correct. Do not compare the two.
    """
    conn = get_conn()
    try:
        anchor = _resolve_anchor(conn, track_key)
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
        rerank = _dj_recency_enabled(display_name)
        # Kill switch / guest opt-out: emit exactly the pre-feature SQL.
        stats_select = (",\n" + _UTS_SELECT) if rerank else ""
        stats_join = ("              " + _UTS_JOIN + "\n") if rerank else ""
        fetch = _dj_recency_pool(limit) if rerank else limit
        sql = f"""
            SELECT {select},
              (
                1.5 * ABS(COALESCE(t.vibe_score_ml,        {a_vibe})    - {a_vibe})
              + 1.0 * ABS(COALESCE(t.energy_pred,         0.5)          - {a_energy})
              + 1.0 * ABS(COALESCE(t.danceability_pred,   0.5)          - {a_dance})
              + 0.8 * ABS(COALESCE(t.valence_pred,        0.5)          - {a_valence})
              - CASE WHEN t.mood = ? THEN 0.15 ELSE 0.0 END
              ) AS distance{stats_select}
            FROM tracks t
            JOIN user_tracks ut ON ut.track_id = t.id
{stats_join}            WHERE ut.user_id = ?
              AND t.ingestion_status = 'done'
              AND t.id != ?
            ORDER BY distance ASC, t.title COLLATE NOCASE
            LIMIT ?
        """
        rows = conn.execute(sql, (a_mood, user_id, anchor["id"], fetch)).fetchall()
        anchor_out = {
            "spotify_id": anchor["spotify_id"],
            "apple_id": anchor["apple_id"],
            "mood": a_mood,
        }
        h_user = _fetch_user_halflife(conn, user_id) if rerank else None
    finally:
        conn.close()

    if not rerank:
        return {
            "anchor": anchor_out,
            "tracks": [_row_to_dict(r) for r in rows[:limit]],
            "mode_used": "vibe",
        }

    cands = []
    for r in rows:
        dist = r["distance"]
        cands.append(_RecencyCandidate(
            score=-(float(dist) if dist is not None else 999.0),
            payload=r,
            last_played=r["uts_last_played"],
            last_skipped_at=r["uts_last_skipped_at"],
            skip_count=r["uts_skip_count"],
            skip_pos_sum=r["uts_skip_pos_sum"],
            duration_ms=r["duration_ms"],
        ))

    out_tracks = []
    for c, final, block in _rerank_by_recency(cands, limit, explain=explain,
                                              halflife_h=h_user):
        d = _row_to_dict(c.payload)
        d["final_score"] = final
        if block is not None:
            d["explain"] = block
        out_tracks.append(d)

    return {
        "anchor": anchor_out,
        "tracks": out_tracks,
        "mode_used": "vibe",
    }


def _parse_id_weight_list(items) -> list:
    """Normalize [{id, weight}] entries. Returns list of (tid, float_weight).

    Preserves int types (frontend hot path sends internal tracks.id as int
    to skip resolution). Strings are stringified; downstream batch resolver
    handles both.
    """
    out = []
    if not items:
        return out
    for it in items:
        if isinstance(it, dict):
            tid = it.get("id")
            w = it.get("weight", 1.0)
        else:
            tid, w = it, 1.0
        if tid is None:
            continue
        try:
            w = float(w)
        except (TypeError, ValueError):
            w = 1.0
        # Keep int as int; everything else becomes a string. The batch
        # resolver distinguishes on isinstance(k, int).
        if not isinstance(tid, int):
            tid = str(tid)
        out.append((tid, w))
    return out


def _resolve_ids_to_track_ids(conn, keys) -> list:
    """Batch-resolve a mixed list of track keys to internal tracks.id ints.

    Each entry may be:
      * int → already an internal id, kept as-is
      * str parseable as int → treated as an internal id (frontend sends
        ints; JSON parsers may deliver them as either)
      * str spotify_id → resolved via a single batched SELECT

    Callers previously looped `_resolve_anchor` per key, paying one DB
    round-trip each. On a 50-entry exclude list against Cloud Run → Turso
    that added ~750 ms of pure overhead. This helper collapses that to at
    most one round-trip regardless of list size.
    """
    ids: list = []
    spotify_keys: list = []
    for k in keys or []:
        if k is None:
            continue
        if isinstance(k, int):
            ids.append(k)
            continue
        s = str(k)
        # Numeric string → internal id. Spotify IDs are base62 (letters+
        # digits, 22 chars) so an all-digit key is unambiguous.
        if s.isdigit():
            try:
                ids.append(int(s))
                continue
            except ValueError:
                pass
        spotify_keys.append(s)
    if spotify_keys:
        # Single SQL round-trip. `?` placeholders scale fine into the
        # low thousands on sqlite/libsql — well past any realistic DJ
        # exclude buffer.
        placeholders = ",".join("?" * len(spotify_keys))
        rows = conn.execute(
            f"SELECT id FROM tracks WHERE spotify_id IN ({placeholders})",
            spotify_keys,
        ).fetchall()
        ids.extend(int(r["id"]) for r in rows)
    return ids


# ---------------------------------------------------------------------------
# DJ replay: per-user library mean.
#
# dj_replay centres every vector on the mean of the user's analysed library
# before replaying (the fused space is a narrow cone — see its docstring).
# The mean is a cache in user_embedding_mean, keyed (user_id, model_version),
# stored as JSON text because Turso BLOB reads can come back empty. It is
# rebuilt when the live count of analysed library tracks drifts by more than
# _DJ_MEAN_DRIFT from the count it was built on. Libraries under
# _DJ_MEAN_MIN_TRACKS use the all-tracks mean, stored as user_id 0.
#
# Per-user vs global made no measurable difference offline (AUC 0.828 vs
# 0.830, 2026-10-10), but a 343-track library's mean was 0.97 cosine from
# the global one, and the gap grows with how unusual a library is.
# ---------------------------------------------------------------------------
_DJ_MEAN_MIN_TRACKS = 50
_DJ_MEAN_DRIFT = 0.05
_DJ_MEAN_RECHECK_S = 600.0
_dj_mean_cache: dict = {}  # (user_id, model_version) -> (n, mean, checked_at, scope_uid)


def _dj_count_embedded(conn, scope_uid: int, emb_col: str) -> int:
    if scope_uid:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM user_tracks ut "
            f"JOIN tracks t ON t.id = ut.track_id "
            f"JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE ut.user_id = ? AND t.ingestion_status = 'done' "
            f"AND te.{emb_col} IS NOT NULL", (scope_uid,)).fetchone()
    else:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM track_embeddings te "
            f"JOIN tracks t ON t.id = te.track_id "
            f"WHERE t.ingestion_status = 'done' AND te.{emb_col} IS NOT NULL").fetchone()
    return int(row["n"] or 0)


def _dj_library_mean(conn, user_id, model_version: str, dim: int):
    """Mean of unit vectors over the user's analysed library, or None."""
    emb_col = _embedding_column_for(model_version)
    if emb_col is None:
        return None
    now = datetime.now(timezone.utc).timestamp()

    def fresh(n_built, n_live):
        return n_built > 0 and abs(n_live - n_built) <= _DJ_MEAN_DRIFT * n_built

    # Cached per REQUESTING user: a small library resolves to the global
    # mean, a large one to its own, and neither may answer for the other.
    req_key = (int(user_id or 0), model_version)
    hit = _dj_mean_cache.get(req_key)
    if hit and now - hit[2] < _DJ_MEAN_RECHECK_S:
        return hit[1]

    scope = int(user_id or 0)
    live = _dj_count_embedded(conn, scope, emb_col) if scope else 0
    if live < _DJ_MEAN_MIN_TRACKS:
        scope = 0
        live = _dj_count_embedded(conn, 0, emb_col)
    if live == 0:
        return None

    if hit and hit[3] == scope and fresh(hit[0], live):
        _dj_mean_cache[req_key] = (hit[0], hit[1], now, scope)
        return hit[1]

    try:
        row = conn.execute(
            "SELECT n_tracks, mean_json FROM user_embedding_mean "
            "WHERE user_id = ? AND model_version = ?", (scope, model_version)).fetchone()
    except Exception as e:  # table not created yet (Turso needs the one-shot script)
        log.warning("[dj] user_embedding_mean unreadable (%s); computing in-process", e)
        row = None
    if row is not None and fresh(int(row["n_tracks"]), live):
        try:
            mean = np.array(json.loads(row["mean_json"]), dtype=np.float64)
            if mean.shape[0] == dim:
                _dj_mean_cache[req_key] = (int(row["n_tracks"]), mean, now, scope)
                return mean
        except (TypeError, ValueError):
            pass

    # Rebuild: one pass over the library's vectors. On Turso this is the
    # expensive read, which is why the result is stored, not recomputed.
    if scope:
        ids = [int(r["id"]) for r in conn.execute(
            f"SELECT t.id AS id FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
            f"JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE ut.user_id = ? AND t.ingestion_status = 'done' "
            f"AND te.{emb_col} IS NOT NULL", (scope,)).fetchall()]
    else:
        ids = [int(r["id"]) for r in conn.execute(
            f"SELECT t.id AS id FROM track_embeddings te JOIN tracks t ON t.id = te.track_id "
            f"WHERE t.ingestion_status = 'done' AND te.{emb_col} IS NOT NULL").fetchall()]
    vecs = _load_mert_vecs_bulk(conn, ids, model_version, dim)
    mean = _dj_mean_of(vecs.values())
    if mean is None:
        return None
    n = len(vecs)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_embedding_mean "
            "(user_id, model_version, n_tracks, mean_json, computed_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (scope, model_version, n, json.dumps([round(float(x), 7) for x in mean])))
        conn.commit()
    except Exception as e:
        log.warning("[dj] could not store user_embedding_mean (%s); kept in memory", e)
    _dj_mean_cache[req_key] = (n, mean, now, scope)
    return mean


def _dj_events_query(conn, raw_events, user_id, seed_vec, model_version: str, dim: int):
    """Resolve the client's event log and replay it. Returns (query, result)."""
    events = _dj_parse_events(raw_events)
    str_keys = sorted({e["id"] for e in events
                       if isinstance(e["id"], str) and not e["id"].isdigit()})
    by_spotify = {}
    if str_keys:
        ph = ",".join("?" * len(str_keys))
        for r in conn.execute(f"SELECT id, spotify_id FROM tracks WHERE spotify_id IN ({ph})",
                              str_keys).fetchall():
            by_spotify[r["spotify_id"]] = int(r["id"])
    resolved = []
    for e in events:
        k = e["id"]
        tid = k if isinstance(k, int) else (int(k) if k.isdigit() else by_spotify.get(k))
        if tid is not None:
            resolved.append({**e, "id": tid})
    vecs = _load_mert_vecs_bulk(conn, sorted({e["id"] for e in resolved}), model_version, dim)
    mu = _dj_library_mean(conn, user_id, model_version, dim)
    if mu is None:
        mu = np.zeros(dim, dtype=np.float64)
    res = _dj_replay(resolved, vecs, mu, seed_vec)
    q = res.query.astype(np.float32) if res.query is not None else None
    return q, res


def _similar_dj(track_key: str, body: SimilarBody, user_id, display_name=None):
    """Cosine similarity over per-track embedding vectors. Variant selects
    which embedding table row to use ('fused' = MERT + scalars + language,
    'mert' = raw MERT). Falls back cross-variant, then to vibe.

    Results are recency re-ranked -- see the _recency_penalty block above for
    the design and the three properties it must keep. `score` still means
    cosine similarity and nothing else; the post-penalty value is reported
    separately as `final_score`. Redefining an existing numeric field in place
    is backlog item 1.2's exact failure mode.
    """
    limit = body.limit if body.limit else 8
    limit = max(1, min(int(limit), 25))
    rerank = _dj_recency_enabled(display_name)
    explain = bool(getattr(body, "explain", False))
    pool = _dj_recency_pool(limit) if rerank else limit

    requested_variant = (body.variant or _DJ_VARIANT_DEFAULT).strip().lower()
    if requested_variant not in ("fused", "mert"):
        requested_variant = _DJ_VARIANT_DEFAULT

    conn = get_conn()
    try:
        anchor = _resolve_anchor(conn, track_key)
    except Exception:
        conn.close()
        raise
    if not anchor:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})
    anchor_id = int(anchor["id"])
    a_mood = anchor["mood"] or ""
    # One SELECT, done up front so it is shared across the Turso and numpy
    # branches below. None when the gate is off -- the re-ranker treats that
    # as "use the env default".
    h_user = _fetch_user_halflife(conn, user_id) if rerank else None

    # Resolve variant with cross-variant fallback: try requested; if seed
    # has no vector there, try the other variant; if neither, drop to vibe.
    model_version, dim = _variant_spec(requested_variant)
    seed_vec = _load_mert_vec(conn, anchor_id, model_version, dim)
    variant_used = requested_variant

    if seed_vec is None:
        fallback_variant = "mert" if requested_variant == "fused" else "fused"
        fb_model_version, fb_dim = _variant_spec(fallback_variant)
        seed_vec = _load_mert_vec(conn, anchor_id, fb_model_version, fb_dim)
        if seed_vec is not None:
            model_version, dim = fb_model_version, fb_dim
            variant_used = fallback_variant

    # With an event log the seed only matters for cold start, so a seed with
    # no vector (pending ingest — e.g. a just-searched track) no longer
    # throws the whole session away.
    use_events = body.events is not None
    replay_res = None

    if seed_vec is None and not use_events:
        conn.close()
        resp = _similar_vibe(track_key, limit, user_id,
                             display_name=display_name, explain=explain)
        resp["mode_used"] = "vibe_fallback_no_seed_embedding"
        resp["variant_used"] = None
        return resp

    try:
        if use_events:
            # Replay path (backend/dj_replay.py): the client sends its raw
            # event log and every bit of weighting happens here. The seed's
            # OWN events count — a search-and-play is the seed, and dropping
            # it (as the weighted path below does) threw away the strongest
            # signal there is. The seed is still excluded from candidates.
            query_vec, replay_res = _dj_events_query(
                conn, body.events, user_id, seed_vec, model_version, dim)
            if query_vec is None:
                # No usable event AND a seed with no vector: nothing to
                # point a query at. Same fallback as a seed without a vector.
                resp = _similar_vibe(track_key, limit, user_id,
                                     display_name=display_name, explain=explain)
                resp["mode_used"] = "vibe_fallback_no_signal"
                resp["variant_used"] = None
                return resp
        else:
            # Legacy weighted path, kept byte-for-byte for clients that still
            # send positive_ids / negative_ids.
            # Resolve positive/negative track_keys to internal ids. The seed
            # track_key in the URL is used only for exclusion — it does NOT
            # contribute to the query vector. Recommendations are driven purely
            # by the user's session (completions, skips, queue-adds).
            # Strongest weights win when a client sends more than we'll accept.
            def _cap_pairs(pairs):
                if len(pairs) <= _DJ_MAX_WEIGHTED_IDS:
                    return pairs
                return sorted(pairs, key=lambda kw: kw[1], reverse=True)[:_DJ_MAX_WEIGHTED_IDS]

            pos_pairs = _cap_pairs(_parse_id_weight_list(body.positive_ids))
            neg_pairs = _cap_pairs(_parse_id_weight_list(body.negative_ids))

            def _resolve_pairs(pairs):
                """Batch-resolve (key, weight) pairs to (internal_id, weight).

                Splits int/int-string entries (already resolved) from spotify_id
                strings (need lookup), then issues a single SQL for the strings.
                Preserves weights via a key→weight map so we don't lose data on
                the batch round-trip.
                """
                if not pairs:
                    return []
                resolved: list = []
                str_keys: list = []
                str_weight: dict = {}
                for k, w in pairs:
                    if isinstance(k, int):
                        resolved.append((k, w))
                        continue
                    s = str(k)
                    if s.isdigit():
                        try:
                            resolved.append((int(s), w))
                            continue
                        except ValueError:
                            pass
                    str_keys.append(s)
                    # If the same spotify_id appears twice we keep the last
                    # weight — callers don't emit duplicates today.
                    str_weight[s] = w
                if str_keys:
                    placeholders = ",".join("?" * len(str_keys))
                    rows = conn.execute(
                        f"SELECT id, spotify_id FROM tracks "
                        f"WHERE spotify_id IN ({placeholders})",
                        str_keys,
                    ).fetchall()
                    for r in rows:
                        tid = int(r["id"])
                        w = str_weight.get(r["spotify_id"], 1.0)
                        resolved.append((tid, w))
                return resolved

            pos_id_w = [(tid, w) for (tid, w) in _resolve_pairs(pos_pairs) if tid != anchor_id]
            neg_id_w = [(tid, w) for (tid, w) in _resolve_pairs(neg_pairs) if tid != anchor_id]

            needed_ids = list({tid for tid, _ in pos_id_w + neg_id_w})
            ctx_vecs = _load_mert_vecs_bulk(conn, needed_ids, model_version, dim)

            def _combine(pairs):
                acc = np.zeros(dim, dtype=np.float32)
                for tid, w in pairs:
                    v = ctx_vecs.get(tid)
                    if v is None:
                        continue
                    acc = acc + (w * _l2_normalize(v))
                return acc

            pos_vec = _combine(pos_id_w)
            neg_vec = _combine(neg_id_w)

            pos_norm = float(np.linalg.norm(pos_vec))
            taste_present = pos_norm >= 0.1

            query_vec = None
            if taste_present:
                # Pure session taste. Answers "what's next for me?"
                query_vec = _l2_normalize(pos_vec - 0.4 * neg_vec)
            # Cold start (no session signal): query_vec stays None; we'll pick
            # a random slice of the candidate pool below.

        # Build exclude set: request excludes + seed itself.
        # Truncated, not rejected: excludes are a nicety (avoid replaying
        # something recent), so dropping the tail degrades gracefully.
        exclude_keys = [str(x) for x in (body.exclude_ids or []) if x is not None]
        exclude_keys = exclude_keys[:_DJ_MAX_EXCLUDE_IDS]
        exclude_ids = set(_resolve_ids_to_track_ids(conn, exclude_keys))
        exclude_ids.add(anchor_id)

        # Load candidate track_ids from user's library (done + has the
        # requested embedding variant, not excluded). Option A layout:
        # one row per track, each variant in its own typed column.
        emb_col = _embedding_column_for(model_version) or "fused_embedding"
        mode_used = f"dj_replay_{variant_used}" if use_events else f"dj_{variant_used}"
        if replay_res is not None and replay_res.from_seed:
            mode_used += "_from_seed"

        # ------------------------------------------------------------------
        # Ranking backend: on Turso we push the cosine computation into the
        # DB (vector_distance_cos on the typed column) so we only stream
        # top-K + metadata back — ~10 KB per request instead of 4.7 MB of
        # embedding blobs. On sqlite we fall back to the in-process numpy
        # brute-force since vanilla sqlite has no vector functions.
        # ------------------------------------------------------------------
        _use_turso_ranking = (
            (os.environ.get("DB_BACKEND") or "").strip().lower() in ("turso", "libsql")
            and query_vec is not None
        )

        top_ids: list[int] = []
        top_scores: dict[int, float] = {}
        out_tracks: list[dict] = []

        if _use_turso_ranking:
            # Serialize query vector for Turso's vector32('[…]') builder.
            qv_str = "[" + ",".join(f"{float(x):.7f}" for x in query_vec.tolist()) + "]"
            # Exclude filter — inlined as literal ints since sqlite param
            # counts have limits and this list is bounded (~50-100).
            excl_sql = ""
            if exclude_ids:
                excl_sql = " AND t.id NOT IN ("
                excl_sql += ",".join(str(int(x)) for x in exclude_ids)
                excl_sql += ") "
            select = ", ".join(f"t.{c}" for c in TRACK_COLUMNS)
            # Zero extra round trips: the stats arrive on a LEFT JOIN added to
            # the query we were already issuing. With the kill switch set,
            # neither the join nor the wider LIMIT is emitted at all.
            stats_select = (", " + _UTS_SELECT.replace("\n", " ")) if rerank else ""
            stats_join = (_UTS_JOIN + " ") if rerank else ""
            sql = (
                f"SELECT {select}, "
                f"       vector_distance_cos(te.{emb_col}, vector32(?)) AS distance"
                f"{stats_select} "
                f"FROM tracks t "
                f"JOIN user_tracks ut ON ut.track_id = t.id "
                f"JOIN track_embeddings te ON te.track_id = t.id "
                f"{stats_join}"
                f"WHERE ut.user_id = ? "
                f"  AND t.ingestion_status = 'done' "
                f"  AND te.{emb_col} IS NOT NULL "
                f"  {excl_sql} "
                f"ORDER BY distance ASC "
                f"LIMIT ?"
            )
            try:
                rows = conn.execute(sql, (qv_str, user_id, pool)).fetchall()
            except Exception as e:
                log.warning("[dj] turso vector_distance_cos path failed: %s; "
                            "falling back to numpy", e)
                rows = None
            if rows is not None:
                # vector_distance_cos returns (1 - cos_sim), so cosine
                # similarity = 1 - distance. Frontend expects similarity.
                def _sim(r):
                    dist = float(r["distance"]) if r["distance"] is not None else 1.0
                    return 1.0 - dist

                if not rerank:
                    for r in rows[:limit]:
                        d = _row_to_dict(r)
                        d["score"] = _sim(r)
                        out_tracks.append(d)
                else:
                    cands = [
                        _RecencyCandidate(
                            score=_sim(r),
                            payload=r,
                            last_played=r["uts_last_played"],
                            last_skipped_at=r["uts_last_skipped_at"],
                            skip_count=r["uts_skip_count"],
                            skip_pos_sum=r["uts_skip_pos_sum"],
                            duration_ms=r["duration_ms"],
                        )
                        for r in rows
                    ]
                    for c, final, block in _rerank_by_recency(
                            cands, limit, explain=explain,
                            halflife_h=h_user):
                        d = _row_to_dict(c.payload)
                        d["score"] = c.score
                        d["final_score"] = final
                        if block is not None:
                            d["explain"] = block
                        out_tracks.append(d)
                anchor_out = {
                    "spotify_id": anchor["spotify_id"],
                    "apple_id": anchor["apple_id"],
                    "mood": a_mood,
                }
                return {
                    "anchor": anchor_out,
                    "tracks": out_tracks,
                    "mode_used": mode_used + "_turso",
                    "variant_used": variant_used,
                }
            # else: fall through to numpy path

        # -------- numpy fallback (local dev / cold-start / turso error) --------
        cand_rows = conn.execute(
            "SELECT t.id, t.duration_ms FROM tracks t "
            "JOIN user_tracks ut ON ut.track_id = t.id "
            "JOIN track_embeddings te ON te.track_id = t.id "
            "WHERE ut.user_id = ? "
            "  AND t.ingestion_status = 'done' "
            f" AND te.{emb_col} IS NOT NULL",
            (user_id,),
        ).fetchall()
        # duration_ms rides along on the query we were already issuing; it is
        # the denominator of the skip-depth term and the full track rows are
        # not fetched until after re-ranking on this path.
        cand_duration = {int(r["id"]): r["duration_ms"] for r in cand_rows}
        cand_ids = [int(r["id"]) for r in cand_rows if int(r["id"]) not in exclude_ids]
        if not cand_ids:
            anchor_out = {
                "spotify_id": anchor["spotify_id"],
                "apple_id": anchor["apple_id"],
                "mood": a_mood,
            }
            return {"anchor": anchor_out, "tracks": [],
                    "mode_used": mode_used, "variant_used": variant_used}

        cand_vecs = _load_mert_vecs_bulk(conn, cand_ids, model_version, dim)
        if not cand_vecs:
            anchor_out = {
                "spotify_id": anchor["spotify_id"],
                "apple_id": anchor["apple_id"],
                "mood": a_mood,
            }
            return {"anchor": anchor_out, "tracks": [],
                    "mode_used": mode_used, "variant_used": variant_used}

        ids_arr = np.array(list(cand_vecs.keys()), dtype=np.int64)

        # One extra query on this path, and only on this path -- there is no
        # candidate query here to hang the LEFT JOIN off. Bounded by "tracks
        # this user touched in 30 days", not by library size.
        stats = _fetch_recency_stats(conn, user_id) if rerank else {}

        def _mk_cand(tid, score):
            st = stats.get(tid)
            return _RecencyCandidate(
                score=score, payload=tid,
                last_played=st[0] if st else None,
                last_skipped_at=st[1] if st else None,
                skip_count=st[2] if st else 0,
                skip_pos_sum=st[3] if st else 0,
                duration_ms=cand_duration.get(tid),
            )

        explains: dict = {}
        finals: dict = {}
        if query_vec is None:
            # Cold start: no similarity signal to trade against, so there is
            # no lambda to calibrate and no window to measure. Keep it random
            # -- cold start must not be deterministic -- but weight the draw
            # by 1/(1+P), which makes a track played an hour ago about half as
            # likely as one never heard. Still one-sided: P only ever lowers
            # a track's weight.
            rng = np.random.default_rng()
            take = min(limit, len(ids_arr))
            if rerank and stats:
                now = datetime.now(timezone.utc)
                w = np.empty(len(ids_arr), dtype=np.float64)
                for i, tid in enumerate(ids_arr):
                    c = _mk_cand(int(tid), 0.0)
                    p, _ex = _recency_penalty(
                        now, c.last_played, c.last_skipped_at,
                        c.skip_count, c.skip_pos_sum, c.duration_ms,
                        halflife_h=h_user)
                    w[i] = 1.0 / (1.0 + p)
                total = float(w.sum())
                probs = (w / total) if total > 0 else None
                pick = rng.choice(len(ids_arr), size=take, replace=False, p=probs)
            else:
                pick = rng.permutation(len(ids_arr))[:take]
            top_ids = [int(ids_arr[i]) for i in pick]
            top_scores = {tid: 0.0 for tid in top_ids}
        else:
            mat = np.stack([cand_vecs[int(i)] for i in ids_arr]).astype(np.float32)
            # Row-normalize.
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms < 1e-12] = 1.0
            mat_n = mat / norms
            sims = mat_n @ query_vec.astype(np.float32)
            if not rerank:
                order = np.argsort(-sims)[:limit]
                top_ids = [int(ids_arr[i]) for i in order]
                top_scores = {int(ids_arr[i]): float(sims[i]) for i in order}
            else:
                # Pool, not the whole library, so this path displaces ranks by
                # the same amount the Turso path does -- otherwise local
                # behaviour would not predict production's.
                order = np.argsort(-sims)[:pool]
                cands = [_mk_cand(int(ids_arr[i]), float(sims[i])) for i in order]
                top_ids = []
                top_scores = {}
                for c, final, block in _rerank_by_recency(
                        cands, limit, explain=explain,
                        halflife_h=h_user):
                    tid = int(c.payload)
                    top_ids.append(tid)
                    top_scores[tid] = c.score
                    finals[tid] = final
                    if block is not None:
                        explains[tid] = block

        # Fetch full track rows for the top ids, preserve order.
        select = ", ".join(f"t.{c}" for c in TRACK_COLUMNS)
        placeholders = ",".join("?" for _ in top_ids)
        rows = conn.execute(
            f"SELECT {select} FROM tracks t WHERE t.id IN ({placeholders})",
            tuple(top_ids),
        ).fetchall()
        row_by_id = {int(r["id"]): r for r in rows}
        out_tracks = []
        for tid in top_ids:
            r = row_by_id.get(tid)
            if not r:
                continue
            d = _row_to_dict(r)
            d["score"] = top_scores.get(tid, 0.0)
            if tid in finals:
                d["final_score"] = finals[tid]
            if tid in explains:
                d["explain"] = explains[tid]
            out_tracks.append(d)

        anchor_out = {
            "spotify_id": anchor["spotify_id"],
            "apple_id": anchor["apple_id"],
            "mood": a_mood,
        }
        return {"anchor": anchor_out, "tracks": out_tracks,
                "mode_used": mode_used, "variant_used": variant_used}
    finally:
        conn.close()


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

    track_key can be a spotify_id (string) or a numeric internal tracks.id.
    """
    return _similar_vibe(track_key, limit, sess["user_id"],
                         display_name=sess.get("display_name"))


@app.post("/api/tracks/{track_key}/similar")
def similar_tracks_post(
    track_key: str,
    body: Optional[SimilarBody] = None,
    sess: dict = Depends(require_user),
):
    """POST variant: supports DJ mode (fused MERT cosine) via JSON body.

    Body (all optional):
      mode: "dj" | "vibe" (default vibe)
      positive_ids: [{id, weight}, ...] — id may be internal tracks.id (int)
        or spotify_id (str). Ints skip resolution; strings batch-resolve.
      negative_ids: [{id, weight}, ...] — same shape as positive_ids
      exclude_ids: [id, ...] — mixed list of internal tracks.id ints and/or
        spotify_id strings. Ints skip resolution.
      limit: int (1..25, default 8)
      explain: bool (default false) — attach a per-track `explain` block with
        the recency penalty breakdown and rank_before / rank_after. Opt-in;
        off by default so no existing client sees a change.

    Each track carries `score` (cosine similarity on the DJ path, absent on
    the vibe path — unchanged meaning) and, when recency re-ranking is
    active, `final_score` = score - lambda * P.
    """
    if body is None:
        body = SimilarBody()
    limit = body.limit if body.limit else 8
    limit = max(1, min(int(limit), 25))
    mode = (body.mode or "vibe").lower()
    if mode == "dj":
        return _similar_dj(track_key, body, sess["user_id"],
                           display_name=sess.get("display_name"))
    return _similar_vibe(track_key, limit, sess["user_id"],
                         display_name=sess.get("display_name"),
                         explain=bool(body.explain))


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
      // Return to whichever app started the flow. `state` is an opaque marker
      // chosen by the initiator ('vs_next' from the React app, 'vs_landing'
      // from the legacy page). Mapping known markers to FIXED paths — rather
      // than treating state as a URL — means a forged value can only ever
      // land on the default. No open redirect.
      // Only one app now, so every flow returns to /. The state marker is
      // still read rather than ignored: if a second client is ever added,
      // this is where it routes back, and treating state as a URL here
      // would be an open redirect.
      var RETURN_PATHS = { vs_next: '/' };
      var dest = RETURN_PATHS[state] || '/';
      window.location.replace(dest + (qs ? ('?' + qs) : ''));
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
    keyed by spotify_id (string) or numeric internal tracks.id. Per-user.
    """
    conn = get_conn()
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


# --------------------------------------------------------------- job state
#
# Restored. 1be1c59 ("drop the synchronous ingest path") deleted these three
# along with the sync ingest worker, but left 45 call sites behind — so every
# ingest job raised NameError on its first _update_job call, the thread died,
# and the job dict sat at its initial zeros forever. The status endpoint kept
# answering 200 with those zeros, which is why the UI polled indefinitely and
# showed no progress: there was nothing to show and nothing to stop it, since
# the error handler called _update_job too and raised again.
#
# All three take JOBS_LOCK because a job is written by its worker thread and
# read by request handlers.


def _update_job(job_id: str, **fields):
    """Merge fields into a job. Silently ignores a job that has been reaped."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)


def _bump(job_id: str, key: str, amount: int = 1):
    """Increment one counter on a job."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job[key] = int(job.get(key, 0)) + amount


def _is_cancelled(job_id: str) -> bool:
    """Whether the caller asked this job to stop. Polled between tracks."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))



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
        # No max_items: get_playlists owns the cap (500). Pinning it here is
        # how the earlier raise from 200 silently had no effect — the default
        # moved and the only caller kept overriding it.
        playlists_raw = splib.get_playlists(token)
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


# _itunes_search_track() lived here for the synchronous worker. Preview
# resolution is now ingest_pipeline/preview_providers.py, which owns the
# rate limiting and retry policy.


def _process_track(conn, track: dict, job_id: str, user_id: int, source: str = "manual") -> str:
    """
    Online fast path used by all /api/ingest/* endpoints. Writes metadata
    only — the preview cascade, ML scoring, and Whisper language detection
    all happen later, offline, in scripts/run_ingest_worker.py.

    Returns one of the sync-modal bucket names:
      - added_to_library     — track was already fully ingested globally,
                               user_tracks link created (playable now)
      - already_in_library   — user already had this track linked (no-op)
      - queued_for_analysis  — new metadata row (or existing pending row)
                               linked to the user, will appear in library
                               once the offline worker finishes it
      - skip:no_id           — track missing spotify_id
    """
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

    # 1) User already has this track linked → no-op.
    existing_link = conn.execute(
        "SELECT 1 FROM tracks t "
        "JOIN user_tracks ut ON ut.track_id = t.id "
        "WHERE t.spotify_id = ? AND ut.user_id = ?",
        (spotify_id, user_id),
    ).fetchone()
    if existing_link:
        return "already_in_library"

    # 2) Track exists globally → reuse and just add the user_tracks link.
    existing_global = conn.execute(
        "SELECT id, ingestion_status FROM tracks WHERE spotify_id = ?",
        (spotify_id,),
    ).fetchone()
    if existing_global:
        conn.execute(
            "INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) "
            "VALUES (?, ?, ?)",
            (user_id, int(existing_global["id"]), source),
        )
        conn.commit()
        status = (existing_global["ingestion_status"] or "done")
        return "added_to_library" if status == "done" else "queued_for_analysis"

    # 3) Brand new to the whole DB: insert metadata-only row with
    #    ingestion_status='pending'. The offline worker will fill in the
    #    preview URL cascade + activation/valence/mood + ML + language later.
    # vibe_score is NOT NULL in the current schema (legacy from the
    # pre-split-ingest era). Insert 0.0 as a placeholder; the offline
    # pipeline's classify stage overwrites it with the real score.
    cur = conn.execute(
        "INSERT INTO tracks "
        "(spotify_id, isrc, title, artist, album, artwork_url, preview_url, "
        " duration_ms, vibe_score, ingestion_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0, 'pending')",
        (spotify_id, isrc, name, artist_name, album_name,
         artwork_url, preview_url, duration_ms),
    )
    track_id = int(cur.lastrowid)
    conn.execute(
        "INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) "
        "VALUES (?, ?, ?)",
        (user_id, track_id, source),
    )
    conn.commit()
    log.info("[ingest] user=%s queued %r - %r (spotify_id=%s)",
             user_id, name, artist_name, spotify_id)
    return "queued_for_analysis"


# _upsert() and the synchronous ingest worker that used to live here are
# gone. Ingestion is async: _process_track writes a metadata row with
# ingestion_status='pending' and returns; ingest_pipeline/ does the rest
# out of band and owns every write to those columns.


def _iter_tracks(token: str, sources: IngestSources) -> Iterator[tuple[dict, str]]:
    """Yield (track, source) AS PAGES ARRIVE rather than after the whole fetch.

    Collecting everything first meant the job reported nothing at all until
    the last Spotify page landed — `total` is only known at the end, so the UI
    sat at 0/0 for the length of the fetch. On a large Liked Songs library
    that is 40 sequential round trips and ten-plus seconds of apparent
    deadlock, and it delayed the first playable track by exactly that long.

    Liked and top tracks stream, because those are the big ones. Playlists
    still arrive as a list: they are one or two pages each, and their fetcher
    carries dedup and diagnostic logging worth more than the second it saves.

    Dedup is global across sources, as before — a track in both Liked Songs
    and a playlist is yielded once.
    """
    seen: set[str] = set()

    def _fresh(t):
        tid = t.get("id") if isinstance(t, dict) else None
        if not tid or tid in seen:
            return False
        seen.add(tid)
        return True

    if sources.liked:
        for t in splib.iter_liked(token):
            if _fresh(t):
                yield t, "liked_songs"
    if sources.top_tracks:
        for t in splib.iter_top_tracks(token):
            if _fresh(t):
                yield t, "top_tracks"
    for pid in sources.playlist_ids or []:
        if not pid:
            continue
        for t in splib.fetch_playlist_tracks(pid, token):
            if _fresh(t):
                yield t, f"playlist:{pid}"


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


def _playable_first(conn, tracks, job_id):
    """Reorder a sync so the tracks that are INSTANTLY playable go first.

    A track already sitting in the catalogue at ingestion_status='done' costs
    one INSERT into user_tracks and is playable the moment it lands. A track
    we have never seen costs a metadata insert and then has to wait for the
    offline pipeline before it can be played at all.

    Processed in playlist order the two are interleaved, so a user importing
    500 tracks waits for the whole job before their library is usable — even
    though most of it was ready in the first second. Partitioning front-loads
    every instant win, which is what lets the UI offer "start listening"
    almost immediately instead of at 100%.

    This is pure ordering. No track is skipped, dropped or processed
    differently, and the four result buckets are unchanged — so a failure here
    must never break the sync. One query decides it; if that query fails for
    any reason we fall back to the original order and simply lose the
    optimisation.

    Stable within each half, so playlist order still shows through.
    """
    try:
        ids = [t.get("id") for t, _ in tracks if t.get("id")]
        if not ids:
            return tracks

        ready = set()
        # Chunked to stay under SQLite's variable limit on large libraries.
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" * len(chunk))
            ready.update(
                r[0] for r in conn.execute(
                    f"SELECT spotify_id FROM tracks "
                    f"WHERE ingestion_status = 'done' AND spotify_id IN ({ph})",
                    chunk,
                )
            )

        if not ready or len(ready) == len(ids):
            return tracks  # nothing to gain from reordering

        head = [p for p in tracks if p[0].get("id") in ready]
        tail = [p for p in tracks if p[0].get("id") not in ready]
        log.info(
            "[ingest job=%s] playable-first: %d instant, %d need analysis",
            job_id, len(head), len(tail),
        )
        return head + tail
    except Exception as e:
        log.warning("[ingest job=%s] playable-first reorder skipped: %s", job_id, e)
        return tracks


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

        conn = get_conn()
        by_src: dict[str, int] = {}
        try:
            """
            Fetch and process are INTERLEAVED.

            The old shape was collect-everything then process-everything, so
            `total` was unknown until the final Spotify page landed and the
            job reported 0/0 for the whole fetch. Now each page is processed
            as it arrives: the first tracks are written within about a second,
            which is what lets the player start and the sync window get out of
            the way almost immediately.

            `total` grows as pages arrive, so it is a running discovery count
            rather than a target until collection finishes. The job carries
            `collecting` so the UI can say "found 1,250 so far" instead of
            showing a percentage of a number that is still moving.
            """
            _update_job(job_id, collecting=True)
            batch: list[tuple[dict, str]] = []
            seen_total = 0
            cancelled = False

            def _drain(batch):
                """Process one page. Partitioned so instant wins land first."""
                for track, src in _playable_first(conn, batch, job_id):
                    if _is_cancelled(job_id):
                        return True
                    try:
                        result = _process_track(conn, track, job_id, user_id, source=src)
                        if result in ("added_to_library", "already_in_library",
                                      "queued_for_analysis"):
                            _bump(job_id, result, 1)
                        else:
                            _bump(job_id, "skipped", 1)
                    except splib.SpotifyAuthError:
                        raise
                    except Exception as e:
                        log.exception("track ingest failed: %s", e)
                        _bump(job_id, "skipped", 1)
                    _bump(job_id, "processed", 1)
                return False

            try:
                for pair in _iter_tracks(token, sources):
                    by_src[pair[1]] = by_src.get(pair[1], 0) + 1
                    batch.append(pair)
                    seen_total += 1
                    # One Spotify page. Draining per page is what makes the
                    # first tracks playable while later pages are still in
                    # flight.
                    if len(batch) >= 50:
                        _update_job(job_id, total=seen_total)
                        if _drain(batch):
                            cancelled = True
                            break
                        batch = []
                if batch and not cancelled:
                    _update_job(job_id, total=seen_total)
                    if _drain(batch):
                        cancelled = True
            except splib.SpotifyAuthError:
                log.warning("[ingest job=%s] SpotifyAuthError mid-stream", job_id)
                _update_job(job_id, status="error", error_message="spotify_token_expired")
                return
            except splib.SpotifyAPIError as e:
                _update_job(job_id, status="error", error_message=f"spotify_api_error: {e}")
                return

            _update_job(job_id, total=seen_total, collecting=False)
            log.info("[ingest job=%s] streamed total=%d by_source=%s cancelled=%s",
                     job_id, seen_total, by_src, cancelled)
        finally:
            conn.close()

        # Library-wide z-score recompute (per-user). Only rebalances rows
        # that actually have a score, so newly queued 'pending' rows are
        # naturally excluded — they'll fold in on the next sync after the
        # worker finishes them.
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
            # True while Spotify pages are still arriving. `total` is a
            # running discovery count until this flips, so the UI should show
            # "found N so far" rather than a percentage of a moving target.
            "collecting": True,
            # Four mutually exclusive buckets (sum == processed) matching
            # the new two-phase ingest flow. See _process_track docstring.
            "added_to_library": 0,     # global row already done, just linked
            "already_in_library": 0,   # user already had this exact link
            "queued_for_analysis": 0,  # new/pending row, worker will finish it
            "skipped": 0,              # skip:no_id or exceptions
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
            # Four mutually exclusive buckets (sum == processed) matching
            # the new two-phase ingest flow. See _process_track docstring.
            "added_to_library": 0,     # global row already done, just linked
            "already_in_library": 0,   # user already had this exact link
            "queued_for_analysis": 0,  # new/pending row, worker will finish it
            "skipped": 0,              # skip:no_id or exceptions
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

        # collecting=False matters as much as the total here.
        #
        # This path fetches the whole playlist up front, so the count is exact
        # the moment it lands — there is no discovery phase to report. The job
        # dict still starts at collecting=True to match the streaming library
        # job's shape, and nothing here ever cleared it: the progress bar
        # stayed indeterminate for the entire import and remained so after the
        # status went 'complete'.
        _update_job(job_id, total=len(unique_tracks), collecting=False)

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
                    # Same 4-bucket scheme as _run_ingest_job.
                    if result == "added_to_library":
                        _bump(job_id, "added_to_library", 1)
                    elif result == "already_in_library":
                        _bump(job_id, "already_in_library", 1)
                    elif result == "queued_for_analysis":
                        _bump(job_id, "queued_for_analysis", 1)
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
            # True while Spotify pages are still arriving. `total` is a
            # running discovery count until this flips, so the UI should show
            # "found N so far" rather than a percentage of a moving target.
            "collecting": True,
            # Four mutually exclusive buckets (sum == processed) matching
            # the new two-phase ingest flow. See _process_track docstring.
            "added_to_library": 0,     # global row already done, just linked
            "already_in_library": 0,   # user already had this exact link
            "queued_for_analysis": 0,  # new/pending row, worker will finish it
            "skipped": 0,              # skip:no_id or exceptions
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



# ---------------- Listening events (telemetry) ----------------
#
# POST /api/events is the only writer of track_events and the only writer of
# user_track_stats. Design constraints, all of them deliberate:
#
#   * INLINE ONLY. No background thread, no queue, no worker. Cloud Run runs
#     --min-instances 0 --max-instances 3 with CPU throttling, so an
#     in-process worker only advances while a request happens to be in flight
#     and dies when the instance is reclaimed (backlog 1.5). The handler
#     resolves, inserts, upserts and returns.
#   * NEVER 4xx/5xx FOR DATA. Telemetry must not be able to break playback or
#     surface an error in the client. A malformed body, a malformed event, an
#     unknown track, an over-long batch, even a DB failure all come back 202
#     with the event counted in `rejected`. The only non-202 outcome is 401
#     from require_user, which is an auth failure, not a data failure.
#   * EVENTS ARE TRUTH, STATS ARE A CACHE. track_events is append-only: one
#     INSERT per event, no UPDATE, no DELETE. user_track_stats is a
#     materialised aggregate of those same events, written in the same
#     request so /similar can read per-user behaviour without scanning the
#     log per candidate. Every column in it is a pure function of
#     track_events, so when the two disagree the events win and
#     scripts/rebuild_user_track_stats.py recomputes the aggregate from
#     scratch. That is what makes this safe despite Turso having no
#     transactions (backlog 2.2) — the insert and the upsert are two
#     independent statements and the cache can drift between them.
#   * NOTHING ELSE MAY WRITE user_track_stats. The moment a second writer
#     with its own idea of what a "play" is touches these columns they stop
#     being rebuildable, and this becomes backlog 1.2 again.
#
# A batch is written as multi-row INSERT ... VALUES chunks: still one row per
# event, but on Turso each execute() is a separate HTTPS round trip
# (backend/db_client.py), so a 50-event batch would otherwise be 50 of them.

_EVENT_BATCH_CAP = 50          # events per request; the overflow is rejected, not the request
_EVENT_INSERT_CHUNK = 25       # track_events rows per statement (25 x 12 = 300 bound params)
_STATS_UPSERT_CHUNK = 20       # user_track_stats rows per statement (20 x 28 = 560 params)
_EVENT_TYPES = ("play_start", "play_end")
_VIBE_SOURCES = ("user", "system")


def _ev_int(value, lo=None, hi=None):
    """Coerce a JSON value to int, or None if it is not a number or falls
    outside [lo, hi]. Out of range is nulled rather than clamped, so a client
    bug stays visible as missing data instead of masquerading as a real
    value."""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if lo is not None and n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n


def _ev_text(value, limit=32):
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s[:limit] if s else None


def _ev_bool(value):
    """Strict tri-state: True/False (or the strings/ints that unambiguously
    mean them) → 1/0; anything else → None. No default — see _normalize_event
    on why a guessed label is worse than a null."""
    if value is True or value is False:
        return 1 if value else 0
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return 1
        if v in ("false", "0", "no"):
            return 0
    return None


def _normalize_event(raw):
    """Validate one client event. Returns a dict ready for insertion (with the
    track still unresolved — an internal id under `_tid` or a spotify_id under
    `_key`) or None if the event is unusable.

    Only two things are structurally required: a known `type` and some track
    identity. Everything else is best-effort — `reason` and `source` are
    stored verbatim after trimming rather than validated against a vocabulary,
    so an unexpected client value shows up in the data instead of being
    silently nulled. The aggregate counters do validate `reason`, because a
    counter has to pick a bucket.

    `vibe_source` and `dj_mode` are expected on every event but are NEVER
    defaulted when missing or unrecognised — they are recorded as NULL
    ("unknown") and the event is still accepted. A guessed label is worse
    than a null here: mislabelling the system's own slider echo as the user's
    stated preference is how the recommender ends up training on its own
    output.
    """
    if not isinstance(raw, dict):
        return None
    etype = _ev_text(raw.get("type"), 16)
    if etype not in _EVENT_TYPES:
        return None

    tid = _ev_int(raw.get("track_id"), lo=1)
    key = None
    if tid is None:
        key = _ev_text(raw.get("spotify_id"), 64)
        if not key:
            return None

    vsrc = (_ev_text(raw.get("vibe_source"), 16) or "").lower()
    if vsrc not in _VIBE_SOURCES:
        vsrc = None

    return {
        "_tid": tid,
        "_key": key,
        "type": etype,
        "reason": _ev_text(raw.get("reason"), 32) if etype == "play_end" else None,
        "position_ms": _ev_int(raw.get("position_ms"), lo=0),
        "duration_ms": _ev_int(raw.get("duration_ms"), lo=0),
        "vibe": _ev_int(raw.get("vibe"), lo=0, hi=100),
        "vibe_source": vsrc,
        "dj_mode": _ev_bool(raw.get("dj_mode")),
        "source": _ev_text(raw.get("source"), 32),
        "client_ts": _ev_int(raw.get("client_ts"), lo=0),
    }


# Every additive counter in user_track_stats, in one place. The insert, the
# ON CONFLICT arithmetic and scripts/rebuild_user_track_stats.py all derive
# from this tuple, so adding a counter means touching exactly one list here
# and one CASE expression in the rebuild script.
#
# The u_ (vibe_source='user') and s_ (vibe_source='system') families are
# deliberately NOT merged — see the comment on user_track_stats in schema.sql.
# u_ is stated preference; s_ is the DJ's own output coming back as a reward
# signal. Averaging them trains the recommender on itself.
_STATS_TOTALS = ("play_count", "end_count", "total_played_ms", "dj_play_count")
_STATS_PER_LABEL = (
    "play_count", "end_count", "complete_count", "skip_count", "replace_count",
    "skip_position_ms_sum", "vibe_count", "vibe_sum", "vibe_sum_sq",
)
_STATS_PREFIX = {"user": "u_", "system": "s_"}
_STATS_COUNTERS = (
    _STATS_TOTALS
    + tuple(f"u_{c}" for c in _STATS_PER_LABEL)
    + tuple(f"s_{c}" for c in _STATS_PER_LABEL)
)


def _accumulate_stats(acc: dict, ev: dict) -> None:
    """Fold one accepted event into the per-track aggregate delta.

    This is the single definition of what each counter means. The rebuild
    script implements the same arithmetic in SQL; if the two ever disagree,
    this one is the spec.

    An event with no vibe_source label lands in the label-agnostic totals
    only, so u_* + s_* can be less than play_count / end_count. That is
    correct and intended: an unlabelled event is evidence that a play
    happened, but not evidence of whose intent set the vibe.
    """
    # `p` is the column prefix of the population this event belongs to, or
    # None when the client did not tell us who moved the slider.
    p = _STATS_PREFIX.get(ev["vibe_source"])

    if ev["type"] == "play_start":
        acc["play_count"] += 1
        if ev["dj_mode"] == 1:
            acc["dj_play_count"] += 1
        if p:
            acc[p + "play_count"] += 1
            # vibe is sampled on play_start only: one sample per play, so a
            # play_start + play_end pair for the same listen cannot count the
            # same slider position twice.
            if ev["vibe"] is not None:
                acc[p + "vibe_count"] += 1
                acc[p + "vibe_sum"] += ev["vibe"]
                acc[p + "vibe_sum_sq"] += ev["vibe"] * ev["vibe"]
        return

    # play_end
    pos = ev["position_ms"] or 0
    acc["end_count"] += 1
    acc["total_played_ms"] += pos
    reason = (ev["reason"] or "").lower()
    if reason == "skipped":
        acc["_skipped"] = True
    # A play "qualifies" (updates last_played) only on play_end with either a
    # completed reason or at least DJ_QUALIFIED_PLAY_MS of playback. play_start
    # NEVER qualifies — a 2 s skim is not a listen. See _upsert_track_stats.
    if reason == "completed" or pos >= _DJ_QUALIFIED_PLAY_MS:
        acc["_qualified"] = True
    if not p:
        return
    acc[p + "end_count"] += 1
    if reason == "completed":
        acc[p + "complete_count"] += 1
    elif reason == "skipped":
        acc[p + "skip_count"] += 1
        acc[p + "skip_position_ms_sum"] += pos
    elif reason == "replaced":
        acc[p + "replace_count"] += 1
    # An unrecognised reason still counts in end_count and total_played_ms and
    # is preserved verbatim in track_events, so a client vocabulary change is
    # recoverable by rebuilding.


_STATS_COLS = (
    ("user_id", "track_id") + _STATS_COUNTERS
    + ("first_played_at", "last_played", "last_skipped_at", "updated_at")
)


def _upsert_track_stats(conn, user_id: int, groups: dict, now: str) -> None:
    """Fold a batch of per-track deltas into user_track_stats.

    One multi-row INSERT ... ON CONFLICT DO UPDATE per chunk. Keys are unique
    within a statement (the caller groups by track_id), which is required —
    SQLite cannot apply two conflicting updates from a single statement.
    """
    rows = []
    for tid, acc in groups.items():
        rows.append(
            (user_id, tid)
            + tuple(acc[c] for c in _STATS_COUNTERS)
            + (
                now,                                       # first_played_at (COALESCEd on conflict)
                now if acc.get("_qualified") else None,    # last_played (only on qualified listens)
                now if acc.get("_skipped") else None,      # last_skipped_at
                now,                                       # updated_at
            )
        )

    sums = ",\n              ".join(
        f"{c} = user_track_stats.{c} + excluded.{c}" for c in _STATS_COUNTERS
    )
    sql_tail = f"""
        ON CONFLICT(user_id, track_id) DO UPDATE SET
              {sums},
              first_played_at = COALESCE(user_track_stats.first_played_at, excluded.first_played_at),
              last_played     = NULLIF(MAX(COALESCE(user_track_stats.last_played, ''),
                                           COALESCE(excluded.last_played, '')), ''),
              last_skipped_at = NULLIF(MAX(COALESCE(user_track_stats.last_skipped_at, ''),
                                           COALESCE(excluded.last_skipped_at, '')), ''),
              updated_at      = excluded.updated_at
    """
    cols = ", ".join(_STATS_COLS)
    width = len(_STATS_COLS)
    for i in range(0, len(rows), _STATS_UPSERT_CHUNK):
        chunk = rows[i:i + _STATS_UPSERT_CHUNK]
        values = ",".join(["(" + ",".join("?" * width) + ")"] * len(chunk))
        conn.execute(
            f"INSERT INTO user_track_stats ({cols}) VALUES {values}" + sql_tail,
            tuple(v for row in chunk for v in row),
        )


# EMA parameters for user_stats.ema_interval_h. alpha=0.1 means the current
# estimate is 90% history / 10% newest interval -- stable enough that one
# unusually long gap (holiday) does not blow the half-life out.
_USER_STATS_EMA_ALPHA = 0.1
# Clamp bounds on a single observed inter-play interval, in hours. 0.01 h
# (36 s) catches double-fires and clock skew; 720 h (30 d) stops a comeback
# after months of silence from permanently anchoring the EMA at a huge value.
_USER_STATS_INTERVAL_MIN_H = 0.01
_USER_STATS_INTERVAL_MAX_H = 720.0


def _upsert_user_stats(conn, user_id: int, play_count_delta: int, now: str) -> None:
    """Fold play_start events for one user into user_stats.

    Reads the current row (if any), computes the new EMA from the interval
    between the previous last_play_at and `now`, writes it back. Caller must
    guarantee play_count_delta > 0 -- this function is skipped entirely for
    batches with no plays, which is the common skip-only case.

    Two separate statements (SELECT then INSERT/UPDATE) on top of the event
    INSERT and user_track_stats upsert that already happened. On Turso each
    is its own Hrana stream so there is no transactional envelope -- a crash
    between any of them leaves user_stats behind the log, which is exactly
    the same drift mode as user_track_stats and recoverable the same way
    (scripts/rebuild_user_stats.py). The read path treats a missing row as
    "cold start, use the env default", so drift degrades to the pre-feature
    behaviour rather than to a wrong half-life.
    """
    if play_count_delta <= 0:
        return
    row = conn.execute(
        "SELECT ema_interval_h, play_count, last_play_at "
        "FROM user_stats WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        # First play ever. Seed with play_count = delta so a multi-play batch
        # still arms the cold-start gate correctly.
        conn.execute(
            "INSERT INTO user_stats "
            "(user_id, plays_30d, ema_interval_h, play_count, last_play_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (user_id, play_count_delta, play_count_delta, now, now),
        )
        return
    last_ts = _parse_sql_ts(row["last_play_at"])
    now_dt = _parse_sql_ts(now)
    new_ema = row["ema_interval_h"]
    if last_ts is not None and now_dt is not None:
        gap = max(_USER_STATS_INTERVAL_MIN_H,
                  min(_USER_STATS_INTERVAL_MAX_H,
                      (now_dt - last_ts).total_seconds() / 3600.0))
        if new_ema is None:
            new_ema = gap
        else:
            new_ema = _USER_STATS_EMA_ALPHA * gap + (1.0 - _USER_STATS_EMA_ALPHA) * float(new_ema)
    conn.execute(
        "UPDATE user_stats SET "
        "  ema_interval_h = ?, "
        "  play_count = play_count + ?, "
        "  last_play_at = ?, "
        "  updated_at = ? "
        "WHERE user_id = ?",
        (new_ema, play_count_delta, now, now, user_id),
    )


def _record_track_events(user_id: int, items: list, display_name: str = "") -> tuple:
    """Resolve, log and aggregate a batch of already-capped client events.

    Returns (accepted, rejected). Runs inline on a worker thread; cost is
    2 SELECTs (batch-resolve by spotify_id and by id) + ceil(n/25) INSERTs
    into track_events + ceil(distinct_tracks/20) upserts into
    user_track_stats.

    `accepted` means "durably written to track_events". The aggregate upsert
    is attempted only for accepted events, and its failure is logged but does
    not change the counts — the aggregate is rebuildable, the log is not.

    An event whose track is not in the catalogue is rejected, never inserted
    with a NULL track_id.
    """
    parsed = []
    rejected = 0
    for raw in items:
        ev = _normalize_event(raw)
        if ev is None:
            rejected += 1
        else:
            parsed.append(ev)
    if not parsed:
        return 0, rejected

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        want_sids = sorted({e["_key"] for e in parsed if e["_key"]})
        want_ids = sorted({e["_tid"] for e in parsed if e["_tid"] is not None})

        sid_to_id = {}
        if want_sids:
            qs = ",".join("?" * len(want_sids))
            for r in conn.execute(
                f"SELECT id, spotify_id FROM tracks WHERE spotify_id IN ({qs})",
                tuple(want_sids),
            ).fetchall():
                sid_to_id[r["spotify_id"]] = int(r["id"])

        known_ids = set()
        if want_ids:
            qs = ",".join("?" * len(want_ids))
            for r in conn.execute(
                f"SELECT id FROM tracks WHERE id IN ({qs})",
                tuple(want_ids),
            ).fetchall():
                known_ids.add(int(r["id"]))

        resolved = []   # [(track_id, event), ...]
        for e in parsed:
            tid = e["_tid"] if e["_tid"] in known_ids else sid_to_id.get(e["_key"])
            if tid is None:
                rejected += 1
                continue
            resolved.append((tid, e))
        if not resolved:
            return 0, rejected

        accepted = 0
        logged = []
        for i in range(0, len(resolved), _EVENT_INSERT_CHUNK):
            chunk = resolved[i:i + _EVENT_INSERT_CHUNK]
            values = ",".join(["(?,?,?,?,?,?,?,?,?,?,?,?)"] * len(chunk))
            params = []
            for tid, e in chunk:
                params.extend((
                    user_id, tid, e["type"], e["reason"], e["position_ms"],
                    e["duration_ms"], e["vibe"], e["vibe_source"], e["dj_mode"],
                    e["source"], e["client_ts"], now,
                ))
            try:
                conn.execute(
                    "INSERT INTO track_events "
                    "(user_id, track_id, type, reason, position_ms, duration_ms, "
                    " vibe, vibe_source, dj_mode, source, client_ts, server_ts) "
                    "VALUES " + values,
                    tuple(params),
                )
                accepted += len(chunk)
                logged.extend(chunk)
            except Exception:
                # Includes "no such table: track_events" on a Turso instance
                # that has not had scripts/_turso_create_event_tables.py run
                # against it. Loud in the log, invisible to the client.
                log.exception("[events] track_events insert failed for %d event(s), user=%s",
                              len(chunk), user_id)
                rejected += len(chunk)

        if logged:
            groups = {}
            for tid, e in logged:
                acc = groups.get(tid)
                if acc is None:
                    acc = groups[tid] = {c: 0 for c in _STATS_COUNTERS}
                _accumulate_stats(acc, e)
            try:
                _upsert_track_stats(conn, user_id, groups, now)
            except Exception:
                # The events are already logged, so this is recoverable:
                # scripts/rebuild_user_track_stats.py rebuilds from them.
                log.exception(
                    "[events] user_track_stats upsert failed for user=%s, %d track(s); "
                    "aggregate has drifted — run scripts/rebuild_user_track_stats.py",
                    user_id, len(groups),
                )
            # user_stats feeds the per-user DJ half-life. Only play_start
            # events advance it -- we are modelling play cadence, not
            # interaction cadence -- and the shared Guest row is skipped
            # outright rather than conflating many visitors' cadences into one.
            if (display_name or "") != "Guest":
                plays = sum(1 for _tid, e in logged if e["type"] == "play_start")
                if plays:
                    try:
                        _upsert_user_stats(conn, user_id, plays, now)
                    except Exception:
                        # Same recovery story as above: rebuild from events.
                        log.exception(
                            "[events] user_stats upsert failed for user=%s; "
                            "aggregate has drifted -- run scripts/rebuild_user_stats.py",
                            user_id,
                        )
            conn.commit()
        return accepted, rejected
    finally:
        conn.close()


@app.post("/api/events", status_code=202)
async def post_track_events(request: Request, sess: dict = Depends(require_user)):
    """Append listening events to track_events and fold them into
    user_track_stats. Always 202.

    Body: {"events": [ {...}, ... ]} — at most _EVENT_BATCH_CAP (50) events;
    anything past the cap is counted in `rejected` rather than failing the
    request. Per-event shape:

        type         "play_start" | "play_end"             (required)
        spotify_id   22-char Spotify id                    (required unless track_id)
        track_id     internal tracks.id                    (alternative key)
        position_ms  play_end: where playback stopped
        duration_ms  play_end: track length, when known
        reason       play_end: "completed" | "skipped" | "replaced"
        vibe         slider value 0-100 at the time
        vibe_source  "user" | "system" — who last set that slider value.
                     Expected on every event; an absent or unrecognised value
                     is stored as NULL and the event is still accepted.
        dj_mode      true | false — was DJ mode active. Same NULL-not-guessed
                     treatment as vibe_source.
        source       "queue" | "dj" | "search" | "autoplay"
        client_ts    epoch ms, client clock (untrusted; server_ts is authoritative)

    Response: {"accepted": int, "rejected": int}, always HTTP 202. The two
    counts sum to the number of events in the submitted array.

    The skip is the point: `type=play_end, reason=skipped` is the only
    explicit negative signal the system gets, which is why this is an event
    log and not a play counter.
    """
    n_seen = 0
    try:
        payload = await request.json()
        items = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            log.warning("[events] user=%s posted a body with no events array",
                        sess.get("user_id"))
            return {"accepted": 0, "rejected": 0}
        n_seen = len(items)
        over = 0
        if n_seen > _EVENT_BATCH_CAP:
            over = n_seen - _EVENT_BATCH_CAP
            items = items[:_EVENT_BATCH_CAP]
        accepted, rejected = await run_in_threadpool(
            _record_track_events, int(sess["user_id"]), items,
            sess.get("display_name") or "",
        )
        return {"accepted": accepted, "rejected": rejected + over}
    except Exception:
        # Telemetry is never allowed to produce a client-visible error.
        log.exception("[events] POST /api/events failed for user=%s", sess.get("user_id"))
        return {"accepted": 0, "rejected": n_seen}


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
                "is_admin": int(r["id"]) == ADMIN_USER_ID,
                "is_guest": r["display_name"] == "Guest",
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


NEXT_DIR = Path(__file__).resolve().parent.parent / "frontend-next" / "dist"

# ---------------------------------------------------------------- routing ---
# The React app (frontend-next) is the only UI. The legacy vanilla app that
# used to live under /legacy has been removed now that React is at parity.
#
# ORDER MATTERS: the catch-all at the bottom must stay last, after every API
# route, or it swallows them.



@app.get("/next", include_in_schema=False)
@app.get("/next/{_path:path}", include_in_schema=False)
def serve_next_alias(_path: str = ""):
    """Historical prefix from the strangler phase, when React lived at /next
    to avoid colliding with the legacy app's root-level files. Kept as a
    redirect so old links and bookmarks still resolve."""
    return RedirectResponse(url="/", status_code=308)


@app.get("/{path:path}", include_in_schema=False)
def serve_react(path: str = ""):
    """
    Serve the built React SPA.

    A real file (hashed asset, manifest.json, sw.js, icons/*) is served
    directly; anything else falls back to index.html so client-side routes
    resolve on a hard refresh.

    This is a catch-all and MUST remain the last route registered.
    """
    if not NEXT_DIR.is_dir():
        raise HTTPException(
            status_code=503,
            detail={"error": "frontend_not_built",
                    "hint": "cd frontend-next && npm install && npm run build"},
        )
    candidate = (NEXT_DIR / path).resolve()
    # Containment check — never serve outside the dist directory.
    if path and candidate.is_file() and NEXT_DIR.resolve() in candidate.parents:
        return FileResponse(str(candidate))
    return FileResponse(str(NEXT_DIR / "index.html"))
