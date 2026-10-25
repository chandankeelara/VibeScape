"""Server-side Spotify credentials in use: the OAuth code exchange and the
/v1/me read behind Spotify login, and the cached client-credentials (app)
token the ingest jobs fall back to.

_get_app_token and _APP_TOKEN_STATE are patched by tests, so callers read
them as `spotify_auth.<name>` at call time and never copy them with
`from services.spotify_auth import ...`."""
import threading

import bootstrap
from bootstrap import log

import requests
from fastapi import HTTPException

import spotify_guard  # ingest/: every api.spotify.com call goes through it
import spotify_matcher  # ingest/


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
