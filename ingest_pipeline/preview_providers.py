"""
Preview URL providers + resolution chain.

Each provider takes a track dict (title, artist, isrc, existing preview_url)
and returns a PreviewHit or None. The chain walks providers in order and
returns the first hit. Providers are pure functions of the track dict +
external HTTP; no DB access.

Add a new provider = write a new subclass + prepend/append to DEFAULT_CHAIN.
"""
from __future__ import annotations

import random
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class PreviewHit:
    """Result returned by a provider when a preview URL is found."""
    url: str
    source: str                # short identifier: 'spotify' | 'itunes' | 'deezer_isrc' | 'deezer_search'
    apple_id: int | None = None
    genre: str | None = None
    track_view_url: str | None = None
    artwork_url: str | None = None
    album: str | None = None
    duration_ms: int | None = None


class PreviewProvider(ABC):
    """Resolves a playable preview URL for a track. One shot, no retries."""
    name: str = "provider"

    @abstractmethod
    def fetch(self, track: dict) -> PreviewHit | None:
        """Return a PreviewHit if this provider can serve a preview, else None."""


class SpotifyPreview(PreviewProvider):
    """
    Trivial 'provider' that just accepts whatever preview_url Spotify's
    catalog gave us at metadata-sync time. Spotify deprecated 30 s previews
    for third parties in 2024, so this is null for most modern tracks —
    but when present, it's the fastest and highest-fidelity source.
    """
    name = "spotify"

    def fetch(self, track: dict) -> PreviewHit | None:
        url = (track.get("preview_url") or "").strip()
        if not url:
            return None
        return PreviewHit(url=url, source="spotify")


class ItunesPreview(PreviewProvider):
    """
    iTunes Search API — free, no auth, but Apple aggressively 403s bursts.
    Empirically ~20 req/min ceiling from a single IP before a rolling
    rate limit kicks in. We serialize behind a class-level lock + a
    minimum inter-request gap, and retry with exponential backoff on 403.
    """
    name = "itunes"

    # All ItunesPreview instances share a single request slot. The lock
    # guards the "last request time" so worker threads take turns.
    _lock = threading.Lock()
    _last_request_ts = 0.0
    _MIN_GAP_S = 0.5      # >= 500 ms between requests
    _MAX_RETRIES = 4
    _BASE_BACKOFF_S = 2.0 # 2, 4, 8, 16 seconds on successive 403s

    def __init__(self, itunes_client, log=None):
        self._client = itunes_client
        self._log = log

    def _throttle(self):
        with ItunesPreview._lock:
            now = time.monotonic()
            wait = ItunesPreview._MIN_GAP_S - (now - ItunesPreview._last_request_ts)
            if wait > 0:
                time.sleep(wait)
            ItunesPreview._last_request_ts = time.monotonic()

    def _search_with_retry(self, term: str) -> list | None:
        for attempt in range(ItunesPreview._MAX_RETRIES + 1):
            self._throttle()
            try:
                return self._client.search(term, limit=5)
            except Exception as e:
                is_403 = "403" in str(e)
                if not is_403 or attempt == ItunesPreview._MAX_RETRIES:
                    if self._log:
                        self._log.warning("itunes search failed for %r: %s", term, e)
                    return None
                backoff = ItunesPreview._BASE_BACKOFF_S * (2 ** attempt) + random.uniform(0, 1)
                if self._log:
                    self._log.warning("itunes 403 for %r; backing off %.1fs (attempt %d/%d)",
                                      term, backoff, attempt + 1, ItunesPreview._MAX_RETRIES)
                time.sleep(backoff)
        return None

    def fetch(self, track: dict) -> PreviewHit | None:
        title = (track.get("title") or "").strip()
        artist = (track.get("artist") or "").strip()
        if not title or not artist:
            return None
        term = f"{title} {artist}"
        results = self._search_with_retry(term)
        if results is None:
            return None
        r = self._pick_best(results, title, artist)
        if not r:
            return None
        return PreviewHit(
            url=r["previewUrl"],
            source="itunes",
            apple_id=r.get("trackId"),
            genre=r.get("primaryGenreName"),
            track_view_url=r.get("trackViewUrl"),
            artwork_url=r.get("artworkUrl100"),
            album=r.get("collectionName"),
            duration_ms=r.get("trackTimeMillis"),
        )

    @staticmethod
    def _pick_best(results, title: str, artist: str) -> dict | None:
        if not results:
            return None
        title_l, artist_l = title.lower(), artist.lower()
        # 1: exact title AND artist match
        for r in results:
            if not isinstance(r, dict) or not r.get("previewUrl"):
                continue
            if (r.get("trackName") or "").lower() == title_l and \
               (r.get("artistName") or "").lower() == artist_l:
                return r
        # 2: substring match on both
        for r in results:
            if not isinstance(r, dict) or not r.get("previewUrl"):
                continue
            if title_l in (r.get("trackName") or "").lower() and \
               artist_l in (r.get("artistName") or "").lower():
                return r
        # 3: first with a previewUrl
        for r in results:
            if isinstance(r, dict) and r.get("previewUrl"):
                return r
        return None


class DeezerIsrcPreview(PreviewProvider):
    """Deezer ISRC lookup — deterministic when the ISRC is present."""
    name = "deezer_isrc"

    def __init__(self, deezer_client):
        self._client = deezer_client

    def fetch(self, track: dict) -> PreviewHit | None:
        isrc = (track.get("isrc") or "").strip()
        if not isrc:
            return None
        r = self._client.lookup_by_isrc(isrc)
        if not r or not r.get("previewUrl"):
            return None
        return PreviewHit(
            url=r["previewUrl"],
            source="deezer_isrc",
            genre=r.get("primaryGenreName"),
            track_view_url=r.get("trackViewUrl"),
            artwork_url=r.get("artworkUrl100"),
            album=r.get("collectionName"),
            duration_ms=r.get("trackTimeMillis"),
        )


class DeezerSearchPreview(PreviewProvider):
    """Deezer term search — last-resort catch for tracks missing ISRC."""
    name = "deezer_search"

    def __init__(self, deezer_client):
        self._client = deezer_client

    def fetch(self, track: dict) -> PreviewHit | None:
        title = (track.get("title") or "").strip()
        artist = (track.get("artist") or "").strip()
        if not title or not artist:
            return None
        r = self._client.search_track(title, artist)
        if not r or not r.get("previewUrl"):
            return None
        return PreviewHit(
            url=r["previewUrl"],
            source="deezer_search",
            genre=r.get("primaryGenreName"),
            track_view_url=r.get("trackViewUrl"),
            artwork_url=r.get("artworkUrl100"),
            album=r.get("collectionName"),
            duration_ms=r.get("trackTimeMillis"),
        )


class PreviewChain:
    """
    Walks providers in order, returns the first hit. Providers should be
    ordered fastest → slowest and cheapest → most-expensive so the common
    case exits early.
    """

    def __init__(self, providers: list[PreviewProvider]):
        self._providers = providers

    def resolve(self, track: dict) -> PreviewHit | None:
        for p in self._providers:
            hit = p.fetch(track)
            if hit and hit.url:
                return hit
        return None


def default_chain(log=None) -> PreviewChain:
    """
    Standard provider order for the app. iTunes only — Deezer previews
    have different audio properties (bitrate, EQ, sample rate) that
    shift MERT embeddings enough to matter for recommendations, so we
    keep provenance consistent by sourcing every preview from iTunes.
    Deezer providers are retained in this module for opt-in use but
    aren't wired into the default chain.

    Constructed lazily so importing this module doesn't drag in backend
    deps unless we actually run.
    """
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parents[1]
    for _sub in ("backend", "ingest"):
        p = _root / _sub
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import itunes_client  # type: ignore

    return PreviewChain([
        SpotifyPreview(),
        ItunesPreview(itunes_client, log=log),
    ])
