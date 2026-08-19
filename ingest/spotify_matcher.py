import time
from typing import Optional

import requests

SEARCH_URL = "https://api.spotify.com/v1/search"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def get_client_credentials_token(client_id: str, client_secret: str) -> str:
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _search(query: str, access_token: str) -> Optional[str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"q": query, "type": "track", "limit": 1}
    for attempt in range(2):
        r = requests.get(SEARCH_URL, headers=headers, params=params, timeout=15)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "1"))
            time.sleep(retry_after)
            continue
        if r.status_code != 200:
            return None
        data = r.json()
        items = (data.get("tracks") or {}).get("items") or []
        if not items:
            return None
        return items[0].get("id")
    return None


def match_by_isrc(isrc: str, access_token: str) -> Optional[str]:
    if not isrc:
        return None
    return _search(f"isrc:{isrc}", access_token)


def match_by_title_artist(title: str, artist: str, access_token: str) -> Optional[str]:
    if not title or not artist:
        return None
    return _search(f"track:{title} artist:{artist}", access_token)
