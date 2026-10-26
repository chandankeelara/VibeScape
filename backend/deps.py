"""Session and auth dependencies shared by every router, and the request
helper they parse the Authorization header with.

  require_user         Authorization: Bearer <session token>, else 401.
  require_admin        require_user, and the caller is ADMIN_USER_ID, else 403.
  require_user_stream  require_user, or ?token= for /api/stream/* only.

FastAPI resolves Depends(fn) by identity, so every route imports these
functions from here; a dependency_overrides entry must key on these objects.

ADMIN_USER_ID is patched by tests, so it is read as `deps.ADMIN_USER_ID` at
call time everywhere, never copied with `from deps import ADMIN_USER_ID`.
"""
import os
import secrets
from typing import Optional

# First: loads .env (ADMIN_USER_ID is read from it below) and imports
# backend/db.py before ingest/ goes on sys.path (see its docstring).
import bootstrap  # noqa: F401

from fastapi import Depends, Header, HTTPException, Request

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db


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


def _require_session(request: Request, token: Optional[str]) -> dict:
    """The body require_user and require_user_stream share once each has
    picked its token: 401 missing_authorization without one, 401
    invalid_session when no session has it, else the session dict, with
    user_id and the token attached to request.state for logging."""
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_authorization"})
    conn = db.get_conn()
    try:
        sess = _lookup_session(conn, token)
    finally:
        conn.close()
    if not sess:
        raise HTTPException(status_code=401, detail={"error": "invalid_session"})
    request.state.user_id = sess["user_id"]
    request.state.session_token = token
    return sess


def require_user(request: Request, authorization: Optional[str] = Header(None)) -> dict:
    """
    FastAPI dependency. Returns the auth session dict {user_id, display_name, ...}
    or raises 401. Attaches user_id onto request.state for logging.
    """
    return _require_session(request, _extract_bearer(authorization))


# The user_id of the single admin is stored in an env var so it's
# configurable per environment. Default = 1 (chandan on prod as of
# 2026-08-23; verified against the downloaded prod DB).
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "1"))


def require_admin(sess: dict = Depends(require_user)) -> dict:
    if int(sess["user_id"]) != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail={"error": "admin_only"})
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
    # The query token only when the header holds no bearer: a bearer that
    # fails is a 401, not a fallback (tests/backend/test_stream_routes.py).
    token = _extract_bearer(authorization)
    if not token:
        token = (request.query_params.get("token") or "").strip() or None
    return _require_session(request, token)
