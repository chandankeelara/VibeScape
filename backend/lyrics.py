"""Lyrics from LRCLIB (https://lrclib.net) for GET /api/tracks/{key}/lyrics.

No DB here: routers/tracks.py's get_track_lyrics owns the `track_lyrics`
cache and calls `lookup()` on a miss, `response()` to shape what it returns
and `is_fresh()` to decide.

Which LRCLIB entry. A song usually has several, at different durations (a
radio edit, the album cut, a fan upload), and synced timestamps only line up
with the recording they were made against. So:

  1. /api/get with title, primary artist, album and our duration. If it
     answers with the same song within 3 s, that is the answer.
  2. Otherwise /api/search, and of the entries naming the same song: the
     synced one closest to our duration, within 3 s; else an instrumental
     one within 3 s; else the plain text of the closest one at any duration
     (plain text carries no timing, so another cut's words are still right).
  3. Nothing: 'none'.

"The same song" is title AND artist, checked on every entry including
/api/get's: never lyrics for a different song. The rules follow
ingest_pipeline/preview_providers.py's iTunes matcher, which cannot be
imported here (ingest_pipeline/ is not in the image).

LRCLIB needs no key but asks for a User-Agent. A spot check of 15 of our
songs on 2026-10-18 got two transient 503s, each fixed by one retry, and a
probe while this was written got a third ({"name":"ServerOverloaded"}). A
5xx or timeout that survives the retry is an outage: lookup() returns
status 'none' with error 'upstream', which is_fresh() keeps only for
UPSTREAM_RETRY. /api/get with no album_name works (probed the same day).
"""
from __future__ import annotations

import difflib
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("vibescape")

API = "https://lrclib.net/api"
USER_AGENT = "VibeScape (https://github.com/chandankeelara/VibeScape)"
TIMEOUT_S = 5.0
RETRY_PAUSE_S = 0.5
SYNC_TOLERANCE_MS = 3000          # synced timestamps drift past this
NONE_TTL = timedelta(days=30)     # a cached miss is asked again after this
UPSTREAM_RETRY = timedelta(minutes=10)   # an outage is asked again after this
CREDIT = "Lyrics from LRCLIB"
SOURCE = "lrclib"


class Upstream(Exception):
    """LRCLIB did not answer: a 5xx or a timeout, twice."""


# ---------------------------------------------------------------------------
# LRC
# ---------------------------------------------------------------------------

_TAG = re.compile(r"\[([^\]]*)\]")
_TIME = re.compile(r"^(\d+):(\d{1,2})(?:[.:](\d{1,3}))?$")
_OFFSET = re.compile(r"^offset:\s*([+-]?\d+)$", re.IGNORECASE)
_WORD_TIME = re.compile(r"<\d+:\d{1,2}(?:[.:]\d{1,3})?>")   # enhanced LRC, per word


def parse_lrc(text: str) -> list:
    """LRC text -> [{"t_ms", "text"}], sorted by time.

    `[mm:ss.xx]` and `[mm:ss.xxx]` (and `[mm:ss]`); a line with several
    timestamps becomes one entry per timestamp. A timestamp with no text is
    kept as "" (an instrumental gap). Metadata tags (`[ar:...]`) and lines
    with no timestamp are dropped, except `[offset:+/-ms]`: a positive
    offset makes lyrics appear sooner, so it is subtracted (floored at 0).
    Per-word `<mm:ss.xx>` stamps are stripped from the text.
    """
    offset = 0
    stamped = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        times = []
        pos = 0
        while True:
            m = _TAG.match(line, pos)
            if not m:
                break
            body = m.group(1).strip()
            t = _TIME.match(body)
            if t:
                frac = t.group(3) or ""
                ms = int(frac.ljust(3, "0")) if frac else 0
                times.append((int(t.group(1)) * 60 + int(t.group(2))) * 1000 + ms)
            else:
                o = _OFFSET.match(body)
                if o:
                    offset = int(o.group(1))
            pos = m.end()
        if not times:
            continue
        words = re.sub(r"\s+", " ", _WORD_TIME.sub("", line[pos:])).strip()
        stamped.extend((t, words) for t in times)
    # The offset tag may come after the lines it applies to; stable sort keeps
    # same-time lines in file order.
    stamped.sort(key=lambda x: x[0])
    return [{"t_ms": max(0, t - offset), "text": w} for t, w in stamped]


def plain_from_lines(lines: list) -> str:
    return "\n".join(ln["text"] for ln in lines).strip("\n")


# ---------------------------------------------------------------------------
# Is this entry our song
# ---------------------------------------------------------------------------

# A different recording with the same name: an instrumental or karaoke
# entry has no words, a live or remix one has different ones.
_VERSION_MARKERS = (
    "remix", "8d", "slowed", "reverb", "sped up", "nightcore", "acoustic",
    "unplugged", "live", "reprise", "instrumental", "karaoke", "lofi",
    "lo fi", "mashup", "cover",
)
_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_FEAT = re.compile(r"\s(?:feat|ft|featuring)\.?\s.*$", re.IGNORECASE)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|/|;|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\band\b|\bx\b)\s*",
                           re.IGNORECASE)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip()


def _markers(title: str) -> frozenset:
    t = f" {_fold(title)} "
    return frozenset(m for m in _VERSION_MARKERS if f" {m} " in t)


def _title_keys(title: str):
    t = _FEAT.sub("", _BRACKETS.sub(" ", title or ""))
    segs = [p for p in (_fold(p) for p in re.split(r"\s+[-–—]\s+", t)) if p]
    return " ".join(segs), segs


def _same_text(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 8 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def titles_match(want: str, got: str) -> bool:
    """Same song name. 'Song - Remastered 2011' matches 'Song'; when both
    sides carry ' - ' segments the whole titles must agree."""
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
    """One credited artist in common. Whole-string first, so a name with a
    comma in it ('Tyler, The Creator') matches itself."""
    if _fold(want).replace(" ", "") and _fold(want).replace(" ", "") == _fold(got).replace(" ", ""):
        return True
    return bool(_artist_set(want) & _artist_set(got))


def same_song(entry: dict, title: str, artist: str) -> bool:
    return (titles_match(title, entry.get("trackName") or entry.get("name") or "")
            and artists_match(artist, entry.get("artistName") or ""))


# ---------------------------------------------------------------------------
# LRCLIB
# ---------------------------------------------------------------------------

def _http(url: str, params: dict):
    """The one way out to the network; tests/conftest.py replaces it."""
    return requests.get(url, params=params, headers={"User-Agent": USER_AGENT},
                        timeout=TIMEOUT_S)


def _sleep(s: float) -> None:
    time.sleep(s)


def _call(path: str, params: dict):
    """JSON of a 200, None for a 404. Raises Upstream after one retry on a
    5xx, a timeout or a dropped connection; anything else (a 4xx, a body
    that is not JSON) is treated as no answer."""
    for attempt in (0, 1):
        try:
            r = _http(f"{API}/{path}", params)
        except (requests.Timeout, requests.ConnectionError) as e:
            err = f"{type(e).__name__}"
        else:
            if r.status_code < 500:
                if r.status_code != 200:
                    if r.status_code != 404:
                        log.warning("[lyrics] lrclib %s answered %s", path, r.status_code)
                    return None
                try:
                    return r.json()
                except ValueError:
                    log.warning("[lyrics] lrclib %s returned a body that is not JSON", path)
                    return None
            err = f"HTTP {r.status_code}"
        if attempt == 0:
            _sleep(RETRY_PAUSE_S)
    log.warning("[lyrics] lrclib %s failed twice: %s", path, err)
    raise Upstream(err)


def _duration_ms(entry: dict):
    try:
        d = float(entry.get("duration"))
    except (TypeError, ValueError):
        return None
    return int(round(d * 1000)) if d > 0 else None


def _off_by(entry: dict, duration_ms):
    d = _duration_ms(entry)
    if d is None or not duration_ms:
        return None
    return abs(d - int(duration_ms))


def _within(entry: dict, duration_ms) -> bool:
    off = _off_by(entry, duration_ms)
    return off is not None and off <= SYNC_TOLERANCE_MS


def _outcome(entry: dict, status: str) -> dict:
    synced = entry.get("syncedLyrics") or None if status == "synced" else None
    plain = entry.get("plainLyrics") or None
    if status == "plain" and not plain:
        plain = plain_from_lines(parse_lrc(entry.get("syncedLyrics") or "")) or None
    if status == "instrumental":
        plain = None
    return {"status": status, "source": SOURCE, "synced_lrc": synced, "plain": plain,
            "matched_duration_ms": _duration_ms(entry),
            "source_id": str(entry["id"]) if entry.get("id") is not None else None,
            "error": None}


def _has_synced(e: dict) -> bool:
    return bool(parse_lrc(e.get("syncedLyrics") or ""))


def _has_words(e: dict) -> bool:
    return bool((e.get("plainLyrics") or "").strip()) or _has_synced(e)


def _status_of(e: dict) -> str | None:
    """What an entry already accepted on duration gives, best first."""
    if _has_synced(e):
        return "synced"
    if e.get("instrumental"):
        return "instrumental"
    if _has_words(e):
        return "plain"
    return None


def choose(entries: list, title: str, artist: str, duration_ms) -> dict | None:
    """The /api/search rule (module docstring, step 2) over `entries`."""
    ours = [e for e in entries if isinstance(e, dict) and same_song(e, title, artist)]
    close = sorted((e for e in ours if _within(e, duration_ms)),
                   key=lambda e: _off_by(e, duration_ms))
    for e in close:
        if _has_synced(e):
            return _outcome(e, "synced")
    for e in close:
        if e.get("instrumental"):
            return _outcome(e, "instrumental")
    worded = [e for e in ours if _has_words(e) and not e.get("instrumental")]
    if worded:
        far = 10 ** 12
        worded.sort(key=lambda e: _off_by(e, duration_ms) if _off_by(e, duration_ms) is not None else far)
        return _outcome(worded[0], "plain")
    return None


NONE = {"status": "none", "source": None, "synced_lrc": None, "plain": None,
        "matched_duration_ms": None, "source_id": None, "error": None}


def lookup(title: str, artist: str, album, duration_ms) -> dict:
    """Ask LRCLIB. Returns the row to cache: status, source, synced_lrc,
    plain, matched_duration_ms, source_id, error ('upstream' or None).
    Never raises for an LRCLIB failure."""
    try:
        if duration_ms:
            params = {"track_name": title, "artist_name": artist,
                      "duration": round(int(duration_ms) / 1000)}
            if album:
                params["album_name"] = album
            hit = _call("get", params)
            if isinstance(hit, dict) and same_song(hit, title, artist) and _within(hit, duration_ms):
                status = _status_of(hit)
                if status:
                    return _outcome(hit, status)
        found = _call("search", {"track_name": title, "artist_name": artist})
        picked = choose(found if isinstance(found, list) else [], title, artist, duration_ms)
        return picked or dict(NONE)
    except Upstream:
        return {**NONE, "error": "upstream"}


# ---------------------------------------------------------------------------
# Cache and response
# ---------------------------------------------------------------------------

def _parse_ts(v):
    if isinstance(v, datetime):
        dt = v
    else:
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_ts(now: datetime | None = None) -> str:
    """fetched_at as stored: UTC 'YYYY-MM-DD HH:MM:SS', CURRENT_TIMESTAMP's shape."""
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


def is_fresh(row, now: datetime | None = None) -> bool:
    """A cached row is served unless it is a miss past its TTL: 'none' after
    30 days, an upstream failure after 10 minutes. Found lyrics never expire."""
    if row["status"] != "none":
        return True
    at = _parse_ts(row["fetched_at"])
    if at is None:
        return False
    ttl = UPSTREAM_RETRY if row["error"] else NONE_TTL
    return (now or datetime.now(timezone.utc)) - at < ttl


def response(row, track_duration_ms) -> dict:
    """The endpoint's body (contract in routers/tracks.py's get_track_lyrics)."""
    status = row["status"]
    lines = parse_lrc(row["synced_lrc"] or "") if status == "synced" else []
    plain = plain_from_lines(lines) if lines else (row["plain"] or None)
    if status in ("none", "instrumental"):
        plain = None
    source = row["source"] if status != "none" else None
    out = {
        "status": status,
        "source": source,
        "lines": lines,
        "plain": plain,
        "matched_duration_ms": row["matched_duration_ms"] if status != "none" else None,
        "track_duration_ms": track_duration_ms,
        "credit": CREDIT if source == SOURCE else None,
    }
    if row["error"]:
        out["error"] = row["error"]
    return out
