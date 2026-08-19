import logging
import requests

log = logging.getLogger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"


def search(term: str, limit: int = 25, genre: str | None = None) -> list[dict]:
    params = {
        "term": term,
        "media": "music",
        "entity": "song",
        "limit": limit,
    }
    if genre:
        params["genreId"] = genre
    try:
        r = requests.get(SEARCH_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("results", [])
    except requests.RequestException as e:
        log.warning("iTunes search failed for %r: %s", term, e)
        return []


def lookup_by_isrc(isrc: str) -> dict | None:
    try:
        r = requests.get(LOOKUP_URL, params={"isrc": isrc}, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0] if results else None
    except requests.RequestException as e:
        log.warning("iTunes ISRC lookup failed for %r: %s", isrc, e)
        return None
