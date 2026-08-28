"""Deezer public-API client used as a fallback when Spotify's preview_url
is empty and iTunes' term search misses.

Spotify killed preview_url for most tracks in Nov 2024, and iTunes'
undocumented /lookup?isrc= now returns 0 hits for every ISRC. Deezer's
public API still returns full-quality 30s MP3 preview URLs by ISRC or
term search — and importantly, it has good coverage for the
Indian/Punjabi/Tamil regional catalog that iTunes' western-biased search
routinely misses.

Two entry points mirror ingest/itunes_client.py shape so
backend/app.py:_process_track can call them without branching on source:

    lookup_by_isrc(isrc) -> dict | None
    search_track(title, artist) -> dict | None

Both return dicts keyed like the iTunes helpers:
    previewUrl, trackName, artistName, collectionName,
    artworkUrl100, trackTimeMillis

Both return None on error/miss — they never raise.
"""

import logging
from typing import Optional
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

_ISRC_URL_TEMPLATE = "https://api.deezer.com/track/isrc:{isrc}"
_SEARCH_URL = "https://api.deezer.com/search"
_TIMEOUT_S = 10

# Single session for connection reuse across the many calls a sync makes.
_session = requests.Session()


def _to_itunes_shape(track: dict) -> dict:
    """Normalize a Deezer track dict into the same key shape iTunes returns.

    Deezer track schema (relevant fields):
        id, title, preview, duration, isrc,
        artist: {name, ...},
        album: {title, cover_medium, cover_big, ...}
    """
    artist = track.get("artist") or {}
    album = track.get("album") or {}
    duration_s = track.get("duration")
    try:
        duration_ms = int(duration_s) * 1000 if duration_s is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    return {
        "previewUrl": track.get("preview") or None,
        "trackName": track.get("title") or None,
        "artistName": artist.get("name") or None,
        "collectionName": album.get("title") or None,
        "artworkUrl100": album.get("cover_medium") or album.get("cover_big") or None,
        "trackTimeMillis": duration_ms,
    }


def lookup_by_isrc(isrc: str) -> Optional[dict]:
    """GET /track/isrc/{isrc}. Returns None on miss/error."""
    if not isrc:
        return None
    url = _ISRC_URL_TEMPLATE.format(isrc=quote(isrc, safe=""))
    try:
        r = _session.get(url, timeout=_TIMEOUT_S)
        # 404 = no ISRC match; silence to keep logs quiet during large syncs.
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        log.warning("deezer ISRC lookup failed for %s: %s", isrc, e)
        return None
    except ValueError as e:
        log.warning("deezer ISRC lookup returned non-JSON for %s: %s", isrc, e)
        return None

    if not isinstance(data, dict):
        return None
    # Deezer signals "not found" with {"error": {...}} on a 200 response.
    if data.get("error"):
        return None
    if not data.get("preview"):
        return None
    return _to_itunes_shape(data)


def search_track(title: str, artist: str) -> Optional[dict]:
    """Term search fallback for tracks whose ISRC didn't hit.

    Deezer's advanced search syntax: q=artist:"X" track:"Y". Returns first
    result with a non-empty preview, using the same preference order as
    _itunes_search_track: exact case-insensitive title+artist -> substring
    match -> first result with a preview.
    """
    if not title or not artist:
        return None
    q = f'artist:"{artist}" track:"{title}"'
    try:
        r = _session.get(_SEARCH_URL, params={"q": q, "limit": 5}, timeout=_TIMEOUT_S)
        r.raise_for_status()
        payload = r.json()
    except requests.RequestException as e:
        log.warning("deezer search failed for %r/%r: %s", title, artist, e)
        return None
    except ValueError as e:
        log.warning("deezer search returned non-JSON for %r/%r: %s", title, artist, e)
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("error"):
        return None
    results = payload.get("data") or []
    if not results:
        return None

    title_l = title.lower()
    artist_l = artist.lower()

    # exact title+artist (case-insensitive)
    for t in results:
        if not isinstance(t, dict) or not t.get("preview"):
            continue
        t_title = (t.get("title") or "").lower()
        t_artist = ((t.get("artist") or {}).get("name") or "").lower()
        if t_title == title_l and t_artist == artist_l:
            return _to_itunes_shape(t)

    # substring match
    for t in results:
        if not isinstance(t, dict) or not t.get("preview"):
            continue
        t_title = (t.get("title") or "").lower()
        t_artist = ((t.get("artist") or {}).get("name") or "").lower()
        if title_l in t_title and artist_l in t_artist:
            return _to_itunes_shape(t)

    # first with preview
    for t in results:
        if isinstance(t, dict) and t.get("preview"):
            return _to_itunes_shape(t)

    return None
