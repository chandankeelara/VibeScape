import re
import time
from typing import Iterator, Optional

import requests

BASE = "https://api.spotify.com/v1"


class SpotifyAuthError(Exception):
    pass


class SpotifyAPIError(Exception):
    pass


class PlaylistNotFoundError(Exception):
    pass


class PlaylistPrivateError(Exception):
    pass


_PLAYLIST_ID_RE = re.compile(r"[A-Za-z0-9]{22}")
_PLAYLIST_URL_PATTERNS = (
    re.compile(r"open\.spotify\.com/(?:embed/)?playlist/([A-Za-z0-9]{22})"),
    re.compile(r"spotify:playlist:([A-Za-z0-9]{22})"),
    re.compile(r"^([A-Za-z0-9]{22})$"),
)


def parse_playlist_id(url_or_id: str) -> Optional[str]:
    """
    Extract a Spotify playlist ID from any of these shapes:
      * https://open.spotify.com/playlist/{ID}
      * https://open.spotify.com/playlist/{ID}?si=xxx
      * https://open.spotify.com/embed/playlist/{ID}
      * https://open.spotify.com/intl-en/playlist/{ID}   (locale-prefixed share links)
      * spotify:playlist:{ID}
      * raw 22-char base62 ID
    Returns the 22-char ID or None if nothing matches.
    """
    if not url_or_id:
        return None
    s = url_or_id.strip()
    for pat in _PLAYLIST_URL_PATTERNS:
        m = pat.search(s)
        if m:
            return m.group(1)
    # Fallback: any 22-char base62 substring; guard against false positives by
    # requiring the string to have looked at least URL-ish or to be exactly 22 chars.
    if "spotify" in s.lower() or len(s) == 22:
        m = _PLAYLIST_ID_RE.search(s)
        if m:
            return m.group(0)
    return None


import logging as _logging
_log = _logging.getLogger("vibescape.spotify")


def _extract_track(playlist_item: dict) -> Optional[dict]:
    """
    Extract the track object from a single element in a /playlists/{id}/items
    response. Spotify silently renamed the nested field from 'track' to
    'item' — the outer array is still called 'items' but each element's
    nested object is now 'item' rather than 'track'. Prefer the legacy
    'track' shape when present so behaviour stays correct if Spotify
    reverts. /me/tracks and /me/top/tracks still use the old 'track' key,
    which this helper also handles for free.
    """
    if not isinstance(playlist_item, dict):
        return None
    t = playlist_item.get("track")
    if isinstance(t, dict):
        return t
    t = playlist_item.get("item")
    if isinstance(t, dict):
        return t
    return None


def _normalize_token(token: str) -> str:
    """
    Defensive cleanup: strip whitespace/quotes, and remove a leading
    'Bearer ' prefix if the caller accidentally included it. Spotify
    rejects any token with extraneous content.
    """
    if not token:
        return ""
    t = token.strip().strip('"').strip("'").strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    return t


def _get(url: str, token: str, params: Optional[dict] = None) -> dict:
    token = _normalize_token(token)
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(3):
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 401:
            _log.warning("spotify 401 on GET %s (token head=%s… len=%d) body=%s",
                         url, token[:8] if token else "", len(token or ""),
                         (r.text or "")[:200])
            raise SpotifyAuthError("spotify_token_expired")
        if r.status_code == 429:
            retry = int(r.headers.get("Retry-After", "1"))
            time.sleep(min(retry, 10))
            continue
        if r.status_code >= 500:
            time.sleep(1 + attempt)
            continue
        if r.status_code != 200:
            _log.warning("spotify %d on GET %s body=%s", r.status_code, url, (r.text or "")[:200])
            raise SpotifyAPIError(f"{r.status_code}: {r.text[:200]}")
        return r.json()
    raise SpotifyAPIError("exhausted retries")


def _paginate(url: str, token: str, params: Optional[dict] = None, max_items: Optional[int] = None) -> Iterator[dict]:
    next_url = url
    next_params = dict(params or {})
    fetched = 0
    page = 0
    while next_url:
        page += 1
        data = _get(next_url, token, next_params)
        items = data.get("items") or []
        total = data.get("total")
        _log.info("[paginate] page=%d url=%s items=%d total=%s next=%s",
                  page, next_url.split("?")[0], len(items), total,
                  "yes" if data.get("next") else "no")
        for it in items:
            yield it
            fetched += 1
            if max_items is not None and fetched >= max_items:
                return
        next_url = data.get("next")
        next_params = None


def get_liked_count(token: str) -> int:
    data = _get(f"{BASE}/me/tracks", token, {"limit": 1})
    return int(data.get("total") or 0)


def get_top_tracks_count(token: str) -> int:
    # Spotify caps /me/top/tracks pagination at 50 items regardless of the
    # reported `total` (which is the size of the user's listening-history
    # pool). Cap the displayed count so the UI matches what will actually
    # be ingested.
    data = _get(f"{BASE}/me/top/tracks", token, {"limit": 1})
    return min(int(data.get("total") or 0), 50)


def get_playlists(token: str, max_items: int = 200) -> list[dict]:
    out: list[dict] = []
    for item in _paginate(f"{BASE}/me/playlists", token, {"limit": 50}, max_items=max_items):
        out.append(item)
    return out


def fetch_liked(token: str) -> list[dict]:
    tracks: list[dict] = []
    for item in _paginate(f"{BASE}/me/tracks", token, {"limit": 50}):
        t = _extract_track(item)
        if t and t.get("id"):
            tracks.append(t)
    return tracks


def fetch_top_tracks(token: str) -> list[dict]:
    tracks: list[dict] = []
    for item in _paginate(f"{BASE}/me/top/tracks", token, {"limit": 50, "time_range": "medium_term"}):
        if item and item.get("id"):
            tracks.append(item)
    return tracks


def fetch_playlist_tracks(playlist_id: str, token: str) -> list[dict]:
    # Spotify renamed the endpoint from /playlists/{id}/tracks (deprecated,
    # returns 403) to /playlists/{id}/items. The outer array key is still
    # 'items' but each element's nested track object is now 'item' rather
    # than 'track' — _extract_track handles both shapes.
    tracks: list[dict] = []
    seen_items = 0
    drop_no_track = 0
    drop_no_id = 0
    drop_local = 0
    seen_ids: set[str] = set()
    dup_ids = 0
    first_ids: list[str] = []
    url = f"{BASE}/playlists/{playlist_id}/items"
    for item in _paginate(url, token, {"limit": 50}):
        seen_items += 1
        t = _extract_track(item)
        if not t:
            drop_no_track += 1
            continue
        if not t.get("id"):
            drop_no_id += 1
            if drop_no_id <= 3:
                _log.info("[playlist=%s] dropped no-id item type=%s keys=%s sample=%r",
                          playlist_id, t.get("type"), list(t.keys())[:8],
                          {k: t.get(k) for k in ("id", "name", "type", "uri") if k in t})
            continue
        if t.get("is_local"):
            drop_local += 1
            continue
        tid = t.get("id")
        if tid in seen_ids:
            dup_ids += 1
        else:
            seen_ids.add(tid)
        if len(first_ids) < 20:
            first_ids.append(str(tid))
        tracks.append(t)
    _log.info("[playlist=%s] fetch summary: seen=%d kept=%d unique_ids=%d dup_ids=%d "
              "drop_no_track=%d drop_no_id=%d drop_local=%d",
              playlist_id, seen_items, len(tracks), len(seen_ids), dup_ids,
              drop_no_track, drop_no_id, drop_local)
    _log.info("[playlist=%s] first 20 spotify_ids: %s", playlist_id, first_ids)
    return tracks


# Spotify field projection for /playlists/{id}/items. Includes BOTH the
# legacy `track` and the new `item` nested key so we get track data
# regardless of which shape the API is serving. _extract_track picks the
# populated one at parse time.
_PUBLIC_PLAYLIST_FIELDS = (
    "items(track(id,name,artists(name),album(name,images),"
    "external_ids,duration_ms,preview_url,is_local),"
    "item(id,name,artists(name),album(name,images),"
    "external_ids,duration_ms,preview_url,is_local)),next,total"
)


def follow_playlist(playlist_id: str, user_token: str, public: bool = False) -> str:
    """
    PUT /playlists/{id}/followers with the user's OAuth token. Used as a
    fallback when the tracks endpoint returns 403 even for a public
    playlist owned by someone else — silently following unlocks the
    tracks endpoint for that user.

    Body defaults to {"public": false} — a *private* follow that does not
    show up on the follower's public Spotify profile.

    Returns one of:
      "ok"    — 200/204, playlist is now followed
      "scope" — 403 with "Insufficient client scope" (the user's OAuth
                token lacks the playlist-modify scope; frontend should
                re-auth with an upgraded scope)
      "other" — 403 without a scope hint, or any other terminal failure
                (playlist genuinely inaccessible)

    Raises:
      SpotifyAuthError  — 401 (token expired)
    """
    user_token = _normalize_token(user_token)
    url = f"{BASE}/playlists/{playlist_id}/followers"
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
    }
    body = {"public": bool(public)}
    for attempt in range(2):
        r = requests.put(url, headers=headers, json=body, timeout=15)
        if r.status_code in (200, 204):
            return "ok"
        if r.status_code == 429:
            retry = int(r.headers.get("Retry-After", "1"))
            time.sleep(min(retry, 10))
            continue
        if r.status_code == 401:
            raise SpotifyAuthError("spotify_token_expired")
        if r.status_code == 403:
            body_text = (r.text or "").lower()
            if "scope" in body_text:
                return "scope"
            return "other"
        return "other"
    return "other"


def probe_playlist_tracks(playlist_id: str, token: str) -> int:
    """
    Probe: GET /playlists/{id}/items?limit=1 to check whether this token
    can read the items endpoint at all. Returns the raw HTTP status
    code so the caller can distinguish 200 / 401 / 403 / 404 and pick
    the right follow-or-error path.

    (Formerly hit /tracks — Spotify deprecated that path; it now 403s.)
    """
    token = _normalize_token(token)
    url = f"{BASE}/playlists/{playlist_id}/items"
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 1, "fields": "total"},
        timeout=15,
    )
    return r.status_code


def fetch_public_playlist_tracks(playlist_id: str, app_token: str) -> list[dict]:
    """
    Fetch a public playlist's tracks using an app (client-credentials) token.
    Raises:
      PlaylistNotFoundError  on 404
      PlaylistPrivateError   on 401 or 403 (playlist is private/unavailable)
      SpotifyAuthError       on 401 that looks like a token expiry (rare with
                             client-creds, but treated distinctly for cleaner
                             upstream error messages)
      SpotifyAPIError        on anything else non-200
    """
    app_token = _normalize_token(app_token)
    tracks: list[dict] = []
    # Spotify deprecated /tracks; use /items (identical response shape).
    url: Optional[str] = f"{BASE}/playlists/{playlist_id}/items"
    params: Optional[dict] = {"limit": 50, "fields": _PUBLIC_PLAYLIST_FIELDS}
    headers = {"Authorization": f"Bearer {app_token}"}
    while url:
        for attempt in range(3):
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 404:
                raise PlaylistNotFoundError(f"playlist_not_found: {playlist_id}")
            if r.status_code in (401, 403):
                # Distinguish: a fresh client-credentials token that gets 401
                # on the playlist endpoint almost always means the resource is
                # private / geo-restricted, not that the token expired. Treat
                # both 401 and 403 as `playlist_private` for the caller.
                raise PlaylistPrivateError(f"playlist_private: {playlist_id}")
            if r.status_code == 429:
                retry = int(r.headers.get("Retry-After", "1"))
                time.sleep(min(retry, 10))
                continue
            if r.status_code >= 500:
                time.sleep(1 + attempt)
                continue
            if r.status_code != 200:
                raise SpotifyAPIError(f"{r.status_code}: {r.text[:200]}")
            break
        else:
            raise SpotifyAPIError("exhausted retries")

        data = r.json()
        for item in data.get("items") or []:
            t = _extract_track(item)
            if not t or not t.get("id"):
                continue
            if t.get("is_local"):
                continue
            tracks.append(t)
        url = data.get("next")
        params = None
    return tracks
