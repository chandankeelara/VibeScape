"""Sign-up, login and sessions: email + password, Spotify OAuth, the shared
guest, linking a Spotify identity, refreshing a Spotify token with the
server's client secret, and the /callback page Spotify redirects to.

Sessions and the auth dependencies are deps.py; the Spotify calls behind
OAuth login are services/spotify_auth.py. The admin is deps.ADMIN_USER_ID,
read at call time because tests patch it."""
import hashlib
import hmac
import re
import secrets
import sqlite3
from typing import Optional

import bootstrap
from bootstrap import log

import requests
from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
import deps
from deps import _issue_session, require_user
from routers import Router
from services.spotify_auth import _spotify_me, _spotify_token_exchange

router = Router()


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


@router.post("/api/auth/signup")
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


@router.post("/api/auth/login")
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


@router.post("/api/auth/logout", status_code=204)
def auth_logout(sess: dict = Depends(require_user)):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (sess["token"],))
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


@router.get("/api/auth/me")
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


@router.post("/api/auth/spotify-oauth")
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


@router.post("/api/spotify/refresh")
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


@router.post("/api/auth/guest")
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


@router.post("/api/auth/spotify-link")
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


@router.get("/callback")
def spotify_callback():
    return HTMLResponse(_CALLBACK_HTML)
