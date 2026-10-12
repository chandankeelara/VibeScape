"""One gate for every Spotify Web API call (api.spotify.com).

Spotify rate-limits per APP (client id), across every token the app holds —
the server's client-credentials token and every user's login token alike.
On 2026-10-10 test imports earned a 429 with Retry-After: 70320 (19.5 h),
and every feature kept discovering the block for itself, one call at a time.

So the first 429 anywhere records "blocked until T", and until T every call
made through request() is answered HERE with the same 429 — no network.
Existing 429 handling then runs unchanged (an import stops, a sync fails
cleanly), it just runs without spending more of the quota.

Retry loops must not sleep through a long block: use should_retry().
accounts.spotify.com (token exchange / refresh) is a different service and
does not go through this.

In-process only: each server process learns of a block from its own first
429 (one call), then stays quiet until the block ends.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time

import requests

log = logging.getLogger("vibescape.spotify_guard")

# A 429 asking for longer than this is a block to wait out, not a hiccup to
# retry through.
LONG_WAIT_S = 30
_DEFAULT_RETRY_S = 5

_lock = threading.Lock()
_blocked_until = 0.0
_clock = time.time            # replaced in tests


def blocked_for() -> float:
    """Seconds left on the current block, 0 when calls may go out."""
    with _lock:
        return max(0.0, _blocked_until - _clock())


def _retry_after_s(resp) -> float:
    try:
        return max(0.0, float(resp.headers.get("Retry-After")))
    except (TypeError, ValueError):
        return float(_DEFAULT_RETRY_S)


def note(resp) -> None:
    """Record a real 429 so later calls are skipped until it expires."""
    global _blocked_until
    if getattr(resp, "status_code", None) != 429 or getattr(resp, "_vibescape_guard", False):
        return
    secs = _retry_after_s(resp)
    with _lock:
        until = _clock() + secs
        if until > _blocked_until:
            _blocked_until = until
            log.warning("[spotify] 429 from %s — skipping all Spotify API calls for %.0f s",
                        getattr(resp, "url", "?"), secs)


def _local_429(url: str, secs: float):
    r = requests.Response()
    r.status_code = 429
    r.reason = "Too Many Requests (held locally by spotify_guard)"
    r.url = url
    r.headers["Retry-After"] = str(math.ceil(secs))
    r.headers["Content-Type"] = "application/json"
    r._content = json.dumps({"error": {"status": 429, "message": "rate_limited_locally"}}).encode()
    r._vibescape_guard = True
    return r


def request(method: str, url: str, **kwargs):
    """requests.request for api.spotify.com, gated by the current block."""
    left = blocked_for()
    if left > 0:
        return _local_429(url, left)
    resp = requests.request(method, url, **kwargs)
    note(resp)
    return resp


def get(url, **kw):
    return request("GET", url, **kw)


def put(url, **kw):
    return request("PUT", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)


def should_retry(resp) -> bool:
    """For retry loops: True only for a short 429 worth waiting out."""
    return resp.status_code == 429 and _retry_after_s(resp) <= LONG_WAIT_S


def status() -> dict:
    left = blocked_for()
    return {"blocked": left > 0, "retry_after_s": math.ceil(left)}


def _reset_for_tests() -> None:
    global _blocked_until
    with _lock:
        _blocked_until = 0.0
