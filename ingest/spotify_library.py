import time
from typing import Iterator, Optional

import requests

BASE = "https://api.spotify.com/v1"


class SpotifyAuthError(Exception):
    pass


class SpotifyAPIError(Exception):
    pass


def _get(url: str, token: str, params: Optional[dict] = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(3):
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 401:
            raise SpotifyAuthError("spotify_token_expired")
        if r.status_code == 429:
            retry = int(r.headers.get("Retry-After", "1"))
            time.sleep(min(retry, 10))
            continue
        if r.status_code >= 500:
            time.sleep(1 + attempt)
            continue
        if r.status_code != 200:
            raise SpotifyAPIError(f"{r.status_code}: {r.text[:200]}")
        return r.json()
    raise SpotifyAPIError("exhausted retries")


def _paginate(url: str, token: str, params: Optional[dict] = None, max_items: Optional[int] = None) -> Iterator[dict]:
    next_url = url
    next_params = dict(params or {})
    fetched = 0
    while next_url:
        data = _get(next_url, token, next_params)
        items = data.get("items") or []
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
        t = item.get("track")
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
    tracks: list[dict] = []
    url = f"{BASE}/playlists/{playlist_id}/tracks"
    for item in _paginate(url, token, {"limit": 50}):
        t = item.get("track") if isinstance(item, dict) else None
        if t and t.get("id") and not t.get("is_local"):
            tracks.append(t)
    return tracks
