import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
import threading
import time as _time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# First: loads .env, imports backend/db.py, then puts the repo root and
# ingest/ on sys.path, in that order (see its docstring).
import bootstrap
from bootstrap import AUDIO_DIR, log

import asyncio
import numpy as np
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel

# Through the module, never `from db import get_conn`: db.get_conn is the one
# seam tests patch (conftest.use_conn), and a copied name would not see it.
import db
# Auth dependencies. ADMIN_USER_ID is patched by tests, so it is read as
# deps.ADMIN_USER_ID and never copied here.
import deps
from deps import _extract_bearer, _issue_session, require_admin, require_user, require_user_stream
import same_song as _same_song
from dj_replay import library_mean as _dj_mean_of
from dj_replay import parse_events as _dj_parse_events
from dj_replay import replay as _dj_replay
import playlist_import as _pli
import library_search as _libsearch
import lyrics as _lyrics
from track_rows import _row_to_dict, _search_rows, _search_rows_cache, _track_select
from services.vectors import (FUSED_COLUMN, FUSED_V1_AVOID, FUSED_V1_MODEL_VERSION,
                              _DJ_VARIANT_DEFAULT, _embedding_column_for, _l2_normalize,
                              _load_mert_vec, _load_mert_vecs_bulk, _resolve_anchor,
                              _variant_spec)
from services.recency import (_RecencyCandidate, _UTS_JOIN, _UTS_JOIN_BY_USER, _UTS_SELECT,
                              _dj_recency_enabled, _dj_recency_pool, _fetch_recency_stats,
                              _fetch_user_halflife, _recency_penalty, _rerank_by_recency)
from services.telemetry import _EVENT_BATCH_CAP, _record_track_events
from services.jobs import JOBS, JOBS_LOCK, _bump, _is_cancelled, _update_job
from routers import register as register_routes
from routers import admin as admin_routes
from routers import meta as meta_routes

import spotify_guard  # noqa: E402  — every api.spotify.com call goes through it
import spotify_library as splib  # noqa: E402
import spotify_matcher  # noqa: E402

# `features` pulls in librosa/numpy — heavy deps not needed in the
# playback-only prod deployment. Import lazily at call sites that need it
# (only the ingest hot path, and only when Modal ML backend is unavailable).
feat = None

app = FastAPI(title="VibeScape API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers first, then the routes app.py still defines, then the SPA
# catch-all at the bottom, which must stay last or it swallows every API
# route. register(), not app.include_router: see routers/__init__.py.
register_routes(app, meta_routes.router)
register_routes(app, admin_routes.router)


@app.on_event("startup")
def _startup():
    db.ensure_db()
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

    conn = db.get_conn()
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
                (user_id, deps.ADMIN_USER_ID),
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
        "is_admin": user_id == deps.ADMIN_USER_ID,
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
    conn = db.get_conn()
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
        "is_admin": user_id == deps.ADMIN_USER_ID,
        "is_guest": False,
    }


@app.post("/api/auth/logout", status_code=204)
def auth_logout(sess: dict = Depends(require_user)):
    conn = db.get_conn()
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
        "is_admin": int(sess["user_id"]) == deps.ADMIN_USER_ID,
        "is_guest": sess.get("display_name") == "Guest",
    }


class SpotifyOAuthBody(BaseModel):
    code: str
    redirect_uri: Optional[str] = None


class SpotifyRefreshBody(BaseModel):
    refresh_token: str


def _spotify_token_exchange(code: str, redirect_uri: str) -> dict:
    client_id, client_secret, _ = bootstrap.spotify_creds()
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
    r = spotify_guard.get(
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
    redirect_uri = body.redirect_uri or bootstrap.spotify_creds()[2]
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

    conn = db.get_conn()
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
        "is_admin": user_id == deps.ADMIN_USER_ID,
        # Pass the raw Spotify access token back so the frontend can call
        # /v1/me, /v1/me/tracks, etc. without a second consent round-trip.
        "spotify_access_token": access_token,
        "spotify_refresh_token": tokens.get("refresh_token"),
        "spotify_expires_in": tokens.get("expires_in"),
        "spotify_scope": tokens.get("scope"),
    }


@app.post("/api/spotify/refresh")
def spotify_refresh(body: SpotifyRefreshBody, sess: dict = Depends(require_user)):
    """
    Requires a VibeScape session (2026-10-10): without one, anyone could use
    this server's client secret to refresh any Spotify token. Both callers
    send one — the web app through api.request, mobile through _authOptions.

    Exchange a Spotify refresh_token for a fresh access_token using the
    server-side client_secret. This lets the browser avoid an interactive
    re-consent (and PKCE dance) when its short-lived access_token expires.
    Refresh tokens minted via the Authorization Code flow require
    client_secret to refresh -- doing this on the server keeps the secret
    off the wire.
    """
    if not body.refresh_token:
        raise HTTPException(status_code=422, detail={"error": "refresh_token required"})
    client_id, client_secret, _ = bootstrap.spotify_creds()
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


@app.post("/api/auth/guest")
def auth_guest():
    """
    Zero-friction "just listen" mode. Reuses a shared 'Guest' user
    (creating one on first call) and issues a session token. All guests
    share the same library — treat this as a demo profile, not a real
    account.
    """
    conn = db.get_conn()
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
    conn = db.get_conn()
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


@app.get("/api/tracks/search")
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


# Server-side caps on client-supplied id lists. The frontend already trims
# (DJ_MAX_SENT_IDS / DJ_MAX_EXCLUDES in frontend-next/src/features/queue/dj.js),
# but a request body is user input: without these a large list means one
# embedding row loaded per positive/negative id, and an arbitrarily long
# inlined NOT IN (...) clause for excludes.
_DJ_MAX_WEIGHTED_IDS = 50
_DJ_MAX_EXCLUDE_IDS = 200


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
    # Discovery mode: rank the whole catalogue, not only the caller's
    # library; each track then says whether it is in the library and
    # whether it is a discovery (_dj_tag_library). Off: unchanged.
    # [frontend contract] new optional request field.
    discover: Optional[bool] = False


def _similar_vibe(track_key: str, limit: int, user_id,
                  display_name=None, explain: bool = False, discover: bool = False):
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
    conn = db.get_conn()
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
        select = _track_select(conn)
        rerank = _dj_recency_enabled(display_name)
        # Kill switch / guest opt-out: emit exactly the pre-feature SQL.
        stats_select = (",\n" + _UTS_SELECT) if rerank else ""
        stats_join = ("              " + (_UTS_JOIN_BY_USER if discover else _UTS_JOIN)
                      + "\n") if rerank else ""
        lib_join = "" if discover else "            JOIN user_tracks ut ON ut.track_id = t.id\n"
        lib_where = "" if discover else "ut.user_id = ?\n              AND "
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
{lib_join}{stats_join}            WHERE {lib_where}t.ingestion_status = 'done'
              AND t.id != ?
            ORDER BY distance ASC, t.title COLLATE NOCASE
            LIMIT ?
        """
        # The user id binds once: in the WHERE, or (discovery, re-ranked) in
        # the stats join; discovery without the re-rank has neither.
        uid_args = () if discover and not rerank else (user_id,)
        rows = conn.execute(sql, (a_mood, *uid_args, anchor["id"], fetch)).fetchall()
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


def _dj_events_query(conn, raw_events, user_id, seed_vec, model_version: str, dim: int,
                     seed_id=None):
    """Resolve the client's event log and replay it. Returns (query, result,
    session song keys): the same_song keys of every track in the log, and of
    the seed, which _dj_drop_repeats keeps out of the response."""
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
    song_keys = _dj_song_keys(conn, {e["id"] for e in resolved} | ({seed_id} if seed_id else set()))
    avoid = FUSED_V1_AVOID if model_version == FUSED_V1_MODEL_VERSION else {}
    res = _dj_replay(resolved, vecs, mu, seed_vec, song_keys=song_keys, **avoid)
    q = res.query.astype(np.float32) if res.query is not None else None
    return q, res, {k for k in song_keys.values() if k is not None}


def _dj_song_keys(conn, ids) -> dict:
    """{track_id: same_song.song_key} for `ids`, one query."""
    ids = sorted(int(i) for i in ids)
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    return {int(r["id"]): _same_song.song_key(r["title"], r["artist"], r["language"])
            for r in conn.execute(f"SELECT id, title, artist, language FROM tracks "
                                  f"WHERE id IN ({ph})", ids).fetchall()}


def _dj_drop_repeats(items, key_of, session_keys):
    """`items` (best first) without another release of a song the session
    already had, nor a second release of a song kept earlier in the list:
    the DJ served Belakina Kavithe's album and single releases back to back,
    and Mask Off right after its remix. Both ranking paths call this on
    their candidate pool, before the recency re-rank and the cut to
    `limit`, so the response still has `limit` tracks when the pool has."""
    seen, out = set(session_keys), []
    for it in items:
        k = key_of(it)
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        out.append(it)
    return out


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
    # Discovery ranks the whole catalogue with the same query, re-ranks and
    # pool; only the user_tracks restriction goes. The query is still
    # centred on the caller's library mean. A track the caller never played
    # carries no recency penalty, which leans the top K slightly toward
    # discoveries; nothing compensates for that, on purpose.
    discover = bool(getattr(body, "discover", False))
    pool = _dj_recency_pool(limit) if rerank else limit

    requested_variant = (body.variant or _DJ_VARIANT_DEFAULT).strip().lower()
    if requested_variant not in ("fused", "mert"):
        requested_variant = _DJ_VARIANT_DEFAULT

    conn = db.get_conn()
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
    model_version, dim = _variant_spec(requested_variant, conn)
    seed_vec = _load_mert_vec(conn, anchor_id, model_version, dim)
    variant_used = requested_variant

    if seed_vec is None:
        fallback_variant = "mert" if requested_variant == "fused" else "fused"
        fb_model_version, fb_dim = _variant_spec(fallback_variant, conn)
        seed_vec = _load_mert_vec(conn, anchor_id, fb_model_version, fb_dim)
        if seed_vec is not None:
            model_version, dim = fb_model_version, fb_dim
            variant_used = fallback_variant

    # With an event log the seed only matters for cold start, so a seed with
    # no vector (pending ingest — e.g. a just-searched track) no longer
    # throws the whole session away.
    use_events = body.events is not None
    replay_res = None
    session_keys = None

    if seed_vec is None and not use_events:
        conn.close()
        resp = _similar_vibe(track_key, limit, user_id,
                             display_name=display_name, explain=explain, discover=discover)
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
            query_vec, replay_res, session_keys = _dj_events_query(
                conn, body.events, user_id, seed_vec, model_version, dim, seed_id=anchor_id)
            # Repeats are filtered out of a pool, so the pool must be deeper
            # than `limit` even with the recency re-rank off.
            pool = max(pool, _dj_recency_pool(limit))
            if query_vec is None:
                # No usable event AND a seed with no vector: nothing to
                # point a query at. Same fallback as a seed without a vector.
                resp = _similar_vibe(track_key, limit, user_id,
                                     display_name=display_name, explain=explain,
                                     discover=discover)
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
        emb_col = _embedding_column_for(model_version) or FUSED_COLUMN
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
            select = _track_select(conn)
            # Zero extra round trips: the stats arrive on a LEFT JOIN added to
            # the query we were already issuing. With the kill switch set,
            # neither the join nor the wider LIMIT is emitted at all.
            stats_select = (", " + _UTS_SELECT.replace("\n", " ")) if rerank else ""
            stats_join = ((_UTS_JOIN_BY_USER if discover else _UTS_JOIN) + " ") if rerank else ""
            lib_join = "" if discover else "JOIN user_tracks ut ON ut.track_id = t.id "
            lib_where = "" if discover else "ut.user_id = ? AND "
            sql = (
                f"SELECT {select}, "
                f"       vector_distance_cos(te.{emb_col}, vector32(?)) AS distance"
                f"{stats_select} "
                f"FROM tracks t "
                f"{lib_join}"
                f"JOIN track_embeddings te ON te.track_id = t.id "
                f"{stats_join}"
                f"WHERE {lib_where}"
                f"  t.ingestion_status = 'done' "
                f"  AND te.{emb_col} IS NOT NULL "
                f"  {excl_sql} "
                f"ORDER BY distance ASC "
                f"LIMIT ?"
            )
            try:
                # The user id binds once, after the SELECT's vector: in the
                # WHERE, or (discovery, re-ranked) in the stats join.
                uid_args = () if discover and not rerank else (user_id,)
                rows = conn.execute(sql, (qv_str, *uid_args, pool)).fetchall()
            except Exception as e:
                log.warning("[dj] turso vector_distance_cos path failed: %s; "
                            "falling back to numpy", e)
                rows = None
            if rows is not None and session_keys is not None:
                rows = _dj_drop_repeats(
                    rows, lambda r: _same_song.song_key(r["title"], r["artist"], r["language"]),
                    session_keys)
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
        if discover:
            cand_rows = conn.execute(
                "SELECT t.id, t.duration_ms, t.title, t.artist, t.language FROM tracks t "
                "JOIN track_embeddings te ON te.track_id = t.id "
                "WHERE t.ingestion_status = 'done' "
                f" AND te.{emb_col} IS NOT NULL",
            ).fetchall()
        else:
            cand_rows = conn.execute(
                "SELECT t.id, t.duration_ms, t.title, t.artist, t.language FROM tracks t "
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
        cand_key = ({int(r["id"]): _same_song.song_key(r["title"], r["artist"], r["language"])
                     for r in cand_rows} if session_keys is not None else {})
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
            ranked = None
            if session_keys is not None:
                ranked = _dj_drop_repeats([int(i) for i in np.argsort(-sims)[:pool]],
                                          lambda i: cand_key.get(int(ids_arr[i])), session_keys)
            if not rerank:
                order = ranked[:limit] if ranked is not None else np.argsort(-sims)[:limit]
                top_ids = [int(ids_arr[i]) for i in order]
                top_scores = {int(ids_arr[i]): float(sims[i]) for i in order}
            else:
                # Pool, not the whole library, so this path displaces ranks by
                # the same amount the Turso path does -- otherwise local
                # behaviour would not predict production's.
                order = ranked if ranked is not None else np.argsort(-sims)[:pool]
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
        select = _track_select(conn)
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
    discover: bool = False,
    sess: dict = Depends(require_user),
):
    """
    Return N tracks from the caller's library most similar to the given
    track. Similarity = weighted L1 distance across the four ML feature
    dimensions (vibe_score_ml, energy_pred, danceability_pred,
    valence_pred), with a small bonus for matching mood. The current
    track is excluded from results.

    track_key can be a spotify_id (string) or a numeric internal tracks.id.
    `discover=1` ranks the whole catalogue instead (see SimilarBody).
    """
    resp = _similar_vibe(track_key, limit, sess["user_id"],
                         display_name=sess.get("display_name"), discover=discover)
    return _discover_finish(resp, sess["user_id"]) if discover else resp


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
      discover: bool (default false) — rank the whole catalogue, not only the
        caller's library. Each track's `in_library` and `discover` are then
        real, and `mode_used` ends in `_discover`.

    Each track carries `score` (cosine similarity on the DJ path, absent on
    the vibe path — unchanged meaning) and, when recency re-ranking is
    active, `final_score` = score - lambda * P.
    """
    if body is None:
        body = SimilarBody()
    limit = body.limit if body.limit else 8
    limit = max(1, min(int(limit), 25))
    mode = (body.mode or "vibe").lower()
    discover = bool(body.discover)
    if mode == "dj":
        resp = _similar_dj(track_key, body, sess["user_id"],
                           display_name=sess.get("display_name"))
    else:
        resp = _similar_vibe(track_key, limit, sess["user_id"],
                             display_name=sess.get("display_name"),
                             explain=bool(body.explain), discover=discover)
    return _discover_finish(resp, sess["user_id"]) if discover else resp


def _dj_tag_library(conn, user_id, tracks) -> None:
    """Set `in_library` and `discover` on discovery-mode tracks, in place.

    in_library is this row's own link (what the heart's PUT / DELETE acts
    on): one query over the <= 25 returned ids, never a per-candidate
    EXISTS in the ranking scan. discover is "not in the library, and no
    library song shares its same_song key", so another release of a song
    the caller owns is neither: no tag, and an empty heart. The keys come
    from _search_rows, cached 60 s and dropped by the heart, so they cost
    no read on a warm cache. That cache holds analysed ('done') rows only:
    a library song still in the pipeline does not hide its other releases.
    """
    ids = sorted({int(t["id"]) for t in tracks})
    if not ids:
        return
    ph = ",".join("?" * len(ids))
    linked = {int(r[0]) for r in conn.execute(
        f"SELECT track_id FROM user_tracks WHERE user_id = ? AND track_id IN ({ph})",
        (user_id, *ids)).fetchall()}
    owned = {_same_song.song_key(r["title"], r["artist"], r["language"])
             for r in _search_rows(conn, user_id)}
    owned.discard(None)
    for t in tracks:
        t["in_library"] = int(t["id"]) in linked
        t["discover"] = (not t["in_library"]
                         and _same_song.song_key(t.get("title"), t.get("artist"),
                                                 t.get("language")) not in owned)


def _discover_finish(resp: dict, user_id) -> dict:
    """Discovery mode's response: real library tags on every track, and a
    `_discover` suffix on mode_used. At the endpoint, so the vibe fallbacks
    inside _similar_dj (which overwrite mode_used) get both too."""
    resp["mode_used"] = f"{resp.get('mode_used')}_discover"
    if resp.get("tracks"):
        conn = db.get_conn()
        try:
            _dj_tag_library(conn, user_id, resp["tracks"])
        finally:
            conn.close()
    return resp


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


@app.get("/api/spotify/status")
def spotify_status():
    """Is this server holding off Spotify after a 429? Answered from memory —
    checking never calls Spotify. {blocked, retry_after_s}. See
    ingest/spotify_guard.py. Per process: another Cloud Run instance learns
    of a block from its own first 429."""
    return spotify_guard.status()


@app.get("/api/track/{apple_id}/spotify")
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
        p = bootstrap.PROJECT_ROOT / p
    try:
        p = p.resolve()
    except OSError:
        return None
    try:
        p.relative_to(bootstrap.PROJECT_ROOT.resolve())
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
    conn = db.get_conn()
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
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT audio_path FROM tracks WHERE spotify_id = ?",
            (track_key,),
        ).fetchone()
    finally:
        conn.close()
    return _stream_audio_row(row, request)


@app.get("/api/tracks/{track_key}/features")
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


@app.get("/api/tracks/{track_key}/lyrics")
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
    conn = db.get_conn()
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
    conn = db.get_conn()
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
    sess: dict = Depends(require_admin),
):
    """
    Pin the YouTube video a track plays. Admin only: this writes the GLOBAL
    tracks.youtube_id, which every user (and the shared guest) streams from,
    so a per-user guard is not enough. Until 2026-10-13 it took require_user,
    meaning a guest minted by POST /api/auth/guest (no credentials) could
    repoint any track in the catalogue. Nothing in frontend-next calls this
    (lib/api.js:setTrackYoutube has no caller); it is a tooling endpoint.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "invalid_body"})
    vid = body.get("youtube_id")
    if not isinstance(vid, str) or not _YT_ID_RE.match(vid):
        raise HTTPException(status_code=400, detail={"error": "invalid_youtube_id"})

    conn = db.get_conn()
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
    the caller has already ingested that spotify_id, or another release of
    the same recording (same ISRC), which is what /api/ingest/single would
    link them to.
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
    isrcs = [_isrc_of(it) for it in items if isinstance(it, dict) and _isrc_of(it)]

    # For each Spotify id we recognise: pull vibe + mood so the frontend can
    # render "vibe NN" alongside the badge without a second round-trip.
    lib_meta: dict[str, dict] = {}
    lib_by_isrc: dict[str, dict] = {}
    if spotify_ids:
        placeholders = ",".join("?" * len(spotify_ids))
        isrc_ph = ",".join("?" * len(isrcs))
        conn = db.get_conn()
        try:
            rows = conn.execute(
                f"SELECT t.spotify_id, t.isrc, t.vibe_score, t.vibe_score_ml, "
                f"       t.mood, t.language, t.language_confidence "
                f"FROM tracks t "
                f"JOIN user_tracks ut ON ut.track_id = t.id "
                f"WHERE ut.user_id = ? AND (t.spotify_id IN ({placeholders})"
                f"{f' OR t.isrc IN ({isrc_ph})' if isrcs else ''})",
                [sess["user_id"], *spotify_ids, *isrcs],
            ).fetchall()
            for r in rows:
                meta = {
                    "vibe_score": r["vibe_score"],
                    "vibe_score_ml": r["vibe_score_ml"],
                    "mood": r["mood"],
                    "language": r["language"],
                    "language_confidence": r["language_confidence"],
                }
                if r["spotify_id"]:
                    lib_meta[r["spotify_id"]] = meta
                if r["isrc"]:
                    lib_by_isrc[r["isrc"]] = meta
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
        meta = lib_meta.get(sid) or lib_by_isrc.get(_isrc_of(it)) or {}
        out.append({
            "spotify_id": sid,
            "title": it.get("name") or "",
            "artist": artist_name,
            "album": album.get("name") or "",
            "artwork_url": artwork_url,
            "preview_url": it.get("preview_url"),
            "duration_ms": it.get("duration_ms"),
            "in_library": bool(meta),
            "vibe_score": meta.get("vibe_score"),
            "vibe_score_ml": meta.get("vibe_score_ml"),
            "mood": meta.get("mood"),
            "language": meta.get("language"),
            "language_confidence": meta.get("language_confidence"),
        })
    return {"tracks": out}


@app.post("/api/ingest/clear")
def ingest_clear(sess: dict = Depends(require_user)):
    """Remove the caller's library links (`user_tracks`) and nothing else.

    Catalogue rows are kept on purpose (2026-10-13). `tracks` is a global,
    song-level catalogue shared by every user, and on Turso, where foreign
    keys are enforced, `track_events`, `user_track_stats` and
    `track_embeddings` all reference `tracks(id) ON DELETE CASCADE`. This
    endpoint used to delete every track no other user still linked, plus its
    audio file, which permanently erased the caller's listening history
    (append-only, the source of truth) and the track's GPU-computed
    embeddings. Orphan catalogue rows, if they ever need cleaning, are an
    offline admin job, not a side effect of a user action.

    `tracks_pruned` and `audio_files_removed` are always 0 and kept only so
    existing clients that parse the body keep working.
    """
    conn = db.get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (sess["user_id"],)
        ).fetchone()[0]
        conn.execute("DELETE FROM user_tracks WHERE user_id = ?", (sess["user_id"],))
        conn.commit()
    finally:
        conn.close()

    log.info("ingest/clear user=%s removed %d user_tracks", sess["user_id"], n)
    return {"cleared": int(n), "tracks_pruned": 0, "audio_files_removed": 0}


def _library_track_id(conn, track_key: str) -> int:
    anchor = _resolve_anchor(conn, track_key)
    if not anchor:
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})
    return int(anchor["id"])


@app.put("/api/me/library/{track_key}")
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


@app.delete("/api/me/library/{track_key}")
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
        client_id, client_secret, _ = bootstrap.spotify_creds()
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


def _isrc_of(track: dict) -> Optional[str]:
    """A Spotify track's ISRC, None when it has none (or an empty one)."""
    return (track.get("external_ids") or {}).get("isrc") or None


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

        conn = db.get_conn()
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
    Links through _link_track, the same path as playlist/liked ingest, so
    the track may come back as another release of the same recording (same
    ISRC) with a different spotify_id.
    """
    spotify_id = (req.spotify_id or "").strip()
    if not spotify_id:
        raise HTTPException(status_code=422, detail={"error": "missing_spotify_id"})

    user_id = sess["user_id"]

    conn = db.get_conn()
    try:
        row = conn.execute(
            f"SELECT {_track_select(conn)} FROM tracks t "
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

    try:
        def _do_process() -> tuple[str, Optional[int]]:
            conn = db.get_conn()
            try:
                return _link_track(conn, track_obj, user_id, source="search")
            finally:
                conn.close()
        result, track_id = await run_in_threadpool(_do_process)
    except Exception as e:
        log.exception("single ingest failed for %s: %s", spotify_id, e)
        raise HTTPException(status_code=500, detail={"error": "ingest_failed", "message": str(e)})

    if result.startswith("skip:no_id"):
        raise HTTPException(status_code=422, detail={"error": "invalid_track", "result": result})

    # By id: the row may be another release of the same recording, whose
    # spotify_id is not the one asked for (_link_track).
    conn = db.get_conn()
    try:
        row = conn.execute(
            f"SELECT {_track_select(conn)} FROM tracks t "
            f"JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE t.id = ? AND ut.user_id = ?",
            (track_id, user_id),
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

        conn = db.get_conn()
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
        r_meta = spotify_guard.get(
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


# ---------------------------------------------------------------------------
# Import a public playlist from any supported service by link.
#
# Spotify, Apple Music, YouTube / YouTube Music and Amazon Music links are read by
# backend/playlist_import.py (its docstring says how and why for each), every
# song is matched to a Spotify track, and the match goes through the same
# _process_track as a library sync — so an imported song is a metadata row
# with ingestion_status='pending', or just a library link when the catalogue
# already has it. Songs with no confident match are reported, not guessed.
#
# Same job machinery as /api/ingest/spotify-public (thread + JOBS dict), so
# the same Cloud Run caveat applies (backlog 1.5); a ~100-song playlist takes
# about a minute, which the client's polling keeps alive.
# ---------------------------------------------------------------------------

class PlaylistUrlIngestRequest(BaseModel):
    url: str
    # The user's Spotify token, optional. Only used for Spotify links, where
    # it lifts the public embed's 100-song cap. Never persisted.
    access_token: Optional[str] = None


_UNMATCHED_REPORT_CAP = 200
_PLATFORM_NAMES = {"spotify": "Spotify", "apple": "Apple Music", "youtube": "YouTube",
                   "deezer": "Deezer", "amazon": "Amazon Music"}


def _catalog_match(conn, item):
    """A song the catalogue already has, as a Spotify-shaped track — so it
    costs no Spotify call. By ISRC when the source gave one, else by exact
    (case-insensitive) title, scored with the same matcher as search."""
    cols = "spotify_id, title, artist, album, artwork_url, duration_ms, isrc"
    rows = []
    if item.isrc:
        rows = conn.execute(f"SELECT {cols} FROM tracks WHERE isrc = ? AND spotify_id IS NOT NULL LIMIT 1",
                            (item.isrc,)).fetchall()
    if not rows and item.title:
        rows = conn.execute(f"SELECT {cols} FROM tracks WHERE LOWER(title) = LOWER(?) "
                            f"AND spotify_id IS NOT NULL LIMIT 10", (item.title,)).fetchall()
    cands = [{"id": r["spotify_id"], "name": r["title"], "artists": [{"name": r["artist"]}],
              "album": {"name": r["album"], "images": [{"url": r["artwork_url"]}] if r["artwork_url"] else []},
              "duration_ms": r["duration_ms"], "external_ids": {"isrc": r["isrc"]} if r["isrc"] else {},
              "preview_url": None} for r in rows]
    best, _score = _pli.choose(item, cands)
    return best


@app.post("/api/ingest/playlist-url", status_code=202)
def ingest_playlist_url(req: PlaylistUrlIngestRequest, sess: dict = Depends(require_user)):
    """Start importing a public playlist link. Returns {job_id, platform}.

    Errors (detail.error): invalid_playlist_url (400), and
    <service>_not_supported (400) for links we recognise but do not read yet
    (deezer). Everything that needs the network — not found,
    unreadable — is reported by the job, so this call stays fast.
    """
    src = _pli.detect(req.url or "")
    if not src:
        raise HTTPException(status_code=400, detail={"error": "invalid_playlist_url"})
    if src.kind not in _pli.SUPPORTED:
        raise HTTPException(status_code=400, detail={
            "error": f"{src.kind}_not_supported",
            "message": f"{_PLATFORM_NAMES.get(src.kind, src.kind)} playlists are not supported yet."})
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "user_id": sess["user_id"],
            "current_track": None,
            "source": f"playlist_url:{src.kind}",
            "platform": src.kind,
            "playlist_name": None,
            "note": None,
            "total": 0,
            "processed": 0,
            "collecting": True,
            "added_to_library": 0,
            "already_in_library": 0,
            "queued_for_analysis": 0,
            "skipped": 0,
            # Songs read from the source that matched nothing on Spotify.
            # A separate bucket from `skipped` (which is failures), with the
            # titles so the user can see exactly what did not come across.
            "not_matched": 0,
            "unmatched": [],
            "cancel_requested": False,
            "error_message": None,
        }
    user_token = (req.access_token or "").strip() or None
    threading.Thread(target=_run_playlist_url_job,
                     args=(job_id, src, sess["user_id"], user_token), daemon=True).start()
    return {"job_id": job_id, "platform": src.kind}


def _run_playlist_url_job(job_id: str, src, user_id: int, user_token: Optional[str]):
    try:
        _update_job(job_id, status="running", current_track=f"reading {src.kind} playlist…")

        # Spotify + the user's own token: the Web API returns the whole
        # playlist with full track objects. Anything else, or any failure
        # there, falls back to the public readers.
        direct = None
        if src.kind == "spotify" and user_token:
            try:
                direct = splib.fetch_public_playlist_tracks(src.id, user_token)
            except Exception as e:  # 403/404/expired: the public embed still works
                log.info("[playlist-url job=%s] Spotify API read failed (%s); using public embed",
                         job_id, e.__class__.__name__)
        if direct is not None:
            work = [(t, None) for t in direct if t and t.get("id")]
        else:
            try:
                pl = _pli.fetch(src, requests.get)
            except _pli.ImportError_ as e:
                _update_job(job_id, status="error", error_message=e.code)
                return
            _update_job(job_id, playlist_name=pl.name, note=pl.note)
            work = [(None, it) for it in pl.items]
        _update_job(job_id, total=len(work), collecting=False)

        conn = db.get_conn()
        cancelled = False
        rate_limited = None
        seen: set = set()
        src_label = f"{src.kind}:{src.id}"[:120]
        try:
            for track, item in work:
                if _is_cancelled(job_id):
                    cancelled = True
                    break
                counted = True               # every outcome counts as processed but a rate-limit stop
                try:
                    if track is None:
                        _update_job(job_id, current_track=f"matching {item.label()}")
                        track, why = _pli.resolve(item, _get_app_token(), spotify_guard.get,
                                                  catalog=lambda it: _catalog_match(conn, it))
                        if track is None:
                            _bump(job_id, "not_matched", 1)
                            with JOBS_LOCK:
                                lst = JOBS[job_id]["unmatched"]
                                if len(lst) < _UNMATCHED_REPORT_CAP:
                                    lst.append({"title": item.title, "artist": item.artist})
                            continue
                    sid = track.get("id")
                    if sid in seen:              # the same song twice in one playlist
                        _bump(job_id, "already_in_library", 1)
                        continue
                    seen.add(sid)
                    result = _process_track(conn, track, job_id, user_id, source=src_label)
                    bucket = result if result in ("added_to_library", "already_in_library",
                                                  "queued_for_analysis") else "skipped"
                    _bump(job_id, bucket, 1)
                except _pli.SpotifyRateLimited as e:
                    # Stop, don't retry: retrying into a long Retry-After is
                    # how the app earned a 19.5 h block on 2026-10-10. What
                    # already landed stays; the rest is never marked
                    # "not matched", because it was never tried.
                    rate_limited, counted = e, False
                    break
                except Exception as e:
                    log.exception("[playlist-url job=%s] track failed: %s", job_id, e)
                    _bump(job_id, "skipped", 1)
                finally:
                    if counted:
                        _bump(job_id, "processed", 1)
        finally:
            conn.close()

        if rate_limited is not None:
            hours = rate_limited.retry_after_s / 3600
            _update_job(job_id, status="error", current_track=None,
                        error_message="spotify_rate_limited",
                        note=(f"Spotify is limiting VibeScape right now (try again in about "
                              f"{hours:.0f} h). Songs added so far are kept."
                              if hours >= 1 else
                              "Spotify is limiting VibeScape right now — try again in a few "
                              "minutes. Songs added so far are kept."))
            log.warning("[playlist-url job=%s] stopped: Spotify rate limit, Retry-After %.0fs",
                        job_id, rate_limited.retry_after_s)
            return
        _update_job(job_id, status="cancelled" if cancelled else "complete", current_track=None)
    except Exception as e:
        log.exception("playlist-url job crashed: %s", e)
        _update_job(job_id, status="error", error_message=f"{e.__class__.__name__}: {e}")


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


@app.post("/api/events", status_code=202)
async def post_track_events(request: Request, sess: dict = Depends(require_user)):
    """Append listening events to track_events and fold them into
    user_track_stats. Always 202.

    Body: {"events": [ {...}, ... ]} — at most _EVENT_BATCH_CAP (50) events;
    anything past the cap is counted in `rejected` rather than failing the
    request. Per-event shape (one envelope for every type):

        type         track events -> track_events:          (required)
                       "play_start" | "play_end" | "pause" | "resume" |
                       "seek" | "queue_add"
                     app events -> user_events (no track):
                       "session_start" | "session_end" | "search" |
                       "vibe_change" | "dj_toggle" | "discover_toggle"
        spotify_id   22-char Spotify id                    (track events: required unless track_id)
        track_id     internal tracks.id                    (alternative key)
        position_ms  play_end / pause / resume: the playhead
        duration_ms  play_end: track length, when known
        reason       play_end: "completed" | "skipped" | "replaced" | "abandoned"
        listened_ms  play_end: audible time, pauses and seek jumps excluded
        trigger      play_end: how it was ended — "next_button" | "media_key" |
                     "prev" | "search" | "pick" | "queue_jump" | "vibe_change"
                     (stored as end_trigger)
        playback     "spotify" | "preview" | "youtube"
        session_id   one per app session
        tz_offset_min  client offset, minutes east of UTC
        data         flat JSON object of type-specific extras (seek from_ms /
                     to_ms, queue_add via, search query / counts / outcome,
                     vibe_change from / to, dj_toggle / discover_toggle on,
                     play_start discover (a DJ pick from discovery),
                     session platform / duration_ms). Nested or oversized objects are dropped.
        vibe         slider value 0-100 at the time
        vibe_source  "user" | "system" — who last set that slider value.
                     Expected on every event; an absent or unrecognised value
                     is stored as NULL and the event is still accepted.
        dj_mode      true | false — was DJ mode active. Same NULL-not-guessed
                     treatment as vibe_source.
        source       "queue" | "dj" | "search" | "pick" | "autoplay"
                     ("pick" since 2026-10-10 — older "search" rows include picks)
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


@app.get("/api/me/stats")
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


# Cache policy for the built SPA. Vite names everything under /assets/ by
# content hash, so those bytes never change under one URL and may be cached
# for a year. Every other file (index.html, sw.js, manifest.json, icons) keeps
# its name across builds and must be revalidated, or a tab keeps the previous
# build's index.html and asks for chunks the new dist/ no longer has.
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "no-cache"


def _not_modified(request: Request, headers) -> bool:
    """True when the client's validators match the file. FileResponse sets
    ETag and Last-Modified but never answers a conditional request itself
    (Starlette 1.6; only StaticFiles does), so without this a no-cache
    revalidation re-downloads index.html every time instead of getting a 304."""
    inm = request.headers.get("if-none-match")
    if inm is not None:
        tags = {t.strip().removeprefix("W/") for t in inm.split(",")}
        return "*" in tags or headers["etag"] in tags
    ims = request.headers.get("if-modified-since")
    return ims is not None and ims == headers["last-modified"]


def _static_file(request: Request, file: Path, cache_control: str) -> Response:
    resp = FileResponse(str(file), headers={"Cache-Control": cache_control},
                        stat_result=os.stat(file))
    if _not_modified(request, resp.headers):
        return Response(status_code=304, headers={
            k: resp.headers[k] for k in ("etag", "last-modified", "cache-control")})
    return resp


@app.get("/{path:path}", include_in_schema=False)
def serve_react(request: Request, path: str = ""):
    """
    Serve the built React SPA.

    A real file (hashed asset, manifest.json, sw.js, icons/*) is served
    directly. A missing file is a 404 when the path is under /assets/ or has
    an extension: answering index.html there (200, text/html) is what turned a
    stale tab's lazy import of a chunk the last deploy deleted into "Failed to
    fetch dynamically imported module" plus a MIME error, which the client
    cannot tell from a real module (2026-10-21). Anything else is a client
    route and falls back to index.html so a hard refresh resolves.

    This is a catch-all and MUST remain the last route registered.
    """
    if not NEXT_DIR.is_dir():
        raise HTTPException(
            status_code=503,
            detail={"error": "frontend_not_built",
                    "hint": "cd frontend-next && npm install && npm run build"},
        )
    candidate = (NEXT_DIR / path).resolve()
    is_asset = path.startswith("assets/")
    # Containment check — never serve outside the dist directory.
    if path and candidate.is_file() and NEXT_DIR.resolve() in candidate.parents:
        return _static_file(request, candidate,
                            IMMUTABLE_CACHE if is_asset else REVALIDATE_CACHE)
    if is_asset or "." in path.rsplit("/", 1)[-1]:
        # no-store: a CDN must not keep this 404 for a name a newer build
        # may yet serve.
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return _static_file(request, NEXT_DIR / "index.html", REVALIDATE_CACHE)
