"""
Preview URL providers + resolution chain.

Each provider takes a track dict (title, artist, isrc, existing preview_url)
and returns a PreviewHit or None. The chain walks providers in order and
returns the first hit. Providers are pure functions of the track dict +
external HTTP; no DB access.

Add a new provider = write a new subclass + prepend/append to DEFAULT_CHAIN.
"""
from __future__ import annotations

import difflib
import os
import random
import re
import unicodedata
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


# ---------------------------------------------------------------------------
# Identity checks for a search result. Strict on purpose: see
# ItunesPreview._pick_best for what loose matching did.
# ---------------------------------------------------------------------------

# A result with one of these and a track without it (or the reverse) is a
# different recording — an 8D or slowed upload under the original's audio
# was the second kind of collision found on 2026-10-11. Radio edits and
# remasters are deliberately absent: they sound the same.
_VERSION_MARKERS = (
    "remix", "8d", "slowed", "reverb", "sped up", "nightcore", "acoustic",
    "unplugged", "live", "reprise", "instrumental", "karaoke", "lofi",
    "lo fi", "mashup", "cover", "background score", "bgm",
)
_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_FEAT = re.compile(r"\s(?:feat|ft|featuring)\.?\s.*$", re.IGNORECASE)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|/|;|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\band\b|\bx\b)\s*",
                           re.IGNORECASE)


def _fold(s: str) -> str:
    """Lowercase, strip accents (CLúB == CLUB), keep letters/digits of any
    script, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip()


def _markers(title: str) -> frozenset:
    t = f" {_fold(title)} "
    return frozenset(m for m in _VERSION_MARKERS if f" {m} " in t)


def _title_keys(title: str) -> tuple[str, list[str]]:
    """(whole title, its ' - ' segments), brackets and feat. removed."""
    t = _FEAT.sub("", _BRACKETS.sub(" ", title or ""))
    segs = [_fold(p) for p in re.split(r"\s+[-–—]\s+", t)]
    segs = [p for p in segs if p]
    return " ".join(segs), segs


def _same_text(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # Spelling drift ("Secret Of Succes"), never on short titles, where one
    # letter is a different song.
    return min(len(a), len(b)) >= 8 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def titles_match(want: str, got: str) -> bool:
    """True when `got` names the same recording as `want`.

    'Hukum - Thalaivar Alappara' matches 'Hukum (From "Jailer")': when only
    one side carries ' - ' segments, any segment may equal the other side.
    When both do, the whole titles must agree — otherwise a shared film
    name ('Dhurandhar The Revenge - Aari Aari' vs '... - Phir Se') would
    match two different songs.
    """
    if _markers(want) != _markers(got):
        return False
    w_all, w_segs = _title_keys(want)
    g_all, g_segs = _title_keys(got)
    if _same_text(w_all, g_all):
        return True
    if len(w_segs) > 1 and len(g_segs) == 1:
        return any(_same_text(s, g_all) for s in w_segs)
    if len(g_segs) > 1 and len(w_segs) == 1:
        return any(_same_text(s, w_all) for s in g_segs)
    return False


def _artist_set(s: str) -> set:
    return {_fold(p).replace(" ", "") for p in _ARTIST_SPLIT.split(s or "") if _fold(p)}


def artists_match(want: str, got: str) -> bool:
    """At least one credited artist in common. Spaces and dots are dropped
    so 'A.R. Rahman' == 'A. R. Rahman'."""
    w, g = _artist_set(want), _artist_set(got)
    return bool(w & g)


def durations_match(want_ms, got_ms) -> bool:
    """Unknown on either side passes. Otherwise within max(15 s, 10 %)."""
    try:
        w, g = int(want_ms), int(got_ms)
    except (TypeError, ValueError):
        return True
    if w <= 0 or g <= 0:
        return True
    return abs(w - g) <= max(15_000, 0.10 * w)


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
    # Tunable from the environment. The defaults suit a steady-state run;
    # draining a BACKLOG needs far more patience, because Apple's rolling
    # window stays shut longer than 2+4+8+16 = 30s once tripped. A retry
    # pass over known-false no_match rows should raise all three, e.g.
    #   VIBESCAPE_ITUNES_MIN_GAP=2 VIBESCAPE_ITUNES_RETRIES=7
    #   VIBESCAPE_ITUNES_BACKOFF=5
    _MIN_GAP_S = float(os.environ.get("VIBESCAPE_ITUNES_MIN_GAP") or 0.5)
    _MAX_RETRIES = int(os.environ.get("VIBESCAPE_ITUNES_RETRIES") or 4)
    _BASE_BACKOFF_S = float(os.environ.get("VIBESCAPE_ITUNES_BACKOFF") or 2.0)

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
                # 10, not 5: with no "closest result" fallback any more, the
                # real track has to be in the page. Same single request.
                return self._client.search(term, limit=10, raise_on_rate_limit=True)
            except Exception as e:
                # Apple signals a rolling rate limit with either 403 or 429
                # depending on which edge/endpoint answers. Both are transient
                # and must be retried — treating them as "no result" silently
                # writes false no_match rows.
                msg = str(e)
                is_rate_limited = "403" in msg or "429" in msg
                if not is_rate_limited or attempt == ItunesPreview._MAX_RETRIES:
                    if self._log:
                        self._log.warning("itunes search failed for %r: %s", term, e)
                    return None
                backoff = ItunesPreview._BASE_BACKOFF_S * (2 ** attempt) + random.uniform(0, 1)
                if self._log:
                    self._log.warning("itunes rate-limited for %r; backing off %.1fs (attempt %d/%d)",
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
        r = self._pick_best(results, title, artist, track.get("duration_ms"))
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
    def _pick_best(results, title: str, artist: str,
                   duration_ms: int | None = None) -> dict | None:
        """The result that IS this track, or None. Never "the closest one".

        There used to be a third tier, "first result with a previewUrl".
        Searching "FINA Bad Bunny" returns the album, so that tier handed one
        song's preview to every track on it: 50 groups / 111 tracks with
        byte-identical MERT vectors under different titles (EoO / FINA /
        EL CLúB / Después de la Playa; four Frank Ocean songs; ...), found
        2026-10-11 in the local DB and the 2026-10-10 prod snapshot. The DJ
        served them to each other as cosine 1.000. A wrong preview is worse
        than none: no_preview is visible, a wrong vector is not.
        """
        if not results:
            return None
        cands = [r for r in results if isinstance(r, dict) and r.get("previewUrl")]
        ok = [r for r in cands
              if titles_match(title, r.get("trackName") or "")
              and artists_match(artist, r.get("artistName") or "")
              and durations_match(duration_ms, r.get("trackTimeMillis"))]
        if not ok:
            return None
        # Prefer the literal title when several pass (e.g. a song and its
        # "(From ...)" release are both in the results).
        exact = [r for r in ok if (r.get("trackName") or "").strip().lower()
                 == title.strip().lower()]
        return (exact or ok)[0]


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
