"""Import a public playlist from any supported service by its link.

    detect(url)              -> Source(kind, id, url, storefront)
    fetch(source, http)      -> Playlist(name, items, note)
    choose(item, candidates) -> (spotify_track, score) | (None, score)

Every service is reduced to the same `Item`, then matched to a Spotify
track, then handed to the normal ingest (`_process_track` in services/library_ingest.py), so a
song imported from Apple Music or YouTube lands exactly like one synced
from Spotify — a metadata row with ingestion_status='pending'.

How each service is read, and why (all checked live 2026-10-10):

  spotify  The Web API refuses playlist contents to this app's
           client-credentials token (403 on /items) and hides Spotify's own
           editorial playlists entirely (404). With the user's Spotify
           token the job uses the API (full playlist;
           services/ingest_jobs.py _run_playlist_url_job). Without one, the
           public EMBED page (open.spotify.com/embed/playlist/<id>) carries
           the track list in __NEXT_DATA__: exact track ids, titles,
           artists and durations — capped at 100 songs. The ids are used
           as-is, so a Spotify link costs no Spotify API calls per song.
  apple    The public playlist page includes schema.org MusicPlaylist
           JSON-LD (every song's id + duration). Apple's public iTunes
           Lookup API resolves up to ~200 ids per request into title,
           artist, album, duration. No key, no account.
  youtube  yt-dlp (already a production dependency) reads the playlist,
           paging past 100 on its own. Titles are video titles, so they
           are cleaned before matching. The video id rides along.
  deezer   Not supported in this version (product decision). The link is
           recognised so the user gets a clear message.
  amazon   Amazon has no public playlist API (its Music API is a closed
           beta). The public web player loads a playlist for a signed-out
           visitor with two calls — POST /config.json for an anonymous
           token, then POST .../api/showHome with that token — and this
           reads the same response. It is Amazon's private, undocumented
           API and their terms forbid using it this way: it can change or
           be closed at any time, so every failure maps to a polite
           "couldn't read this Amazon playlist". Product decision
           (2026-10-10), taken knowing that.

None of the supported sources give ISRCs, so matching is by title, artist
and duration — see choose().
"""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

KINDS = ("spotify", "apple", "youtube", "deezer", "amazon")
# Deezer was built and measured (public API, ISRCs) and then dropped from the
# first version on 2026-10-10 by product decision; detect() still recognises
# its links so users get "not supported yet" rather than "invalid link".
SUPPORTED = ("spotify", "apple", "youtube", "amazon")

EMBED_CAP = 100          # Spotify's embed page lists at most this many songs
ITUNES_BATCH = 150       # ids per iTunes Lookup request
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


class ImportError_(Exception):
    """A user-facing failure. `code` is what the API returns."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass
class Source:
    kind: str
    id: str
    url: str
    storefront: str = "us"     # Apple only


@dataclass
class Item:
    title: str
    artist: str = ""
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    isrc: Optional[str] = None
    spotify_id: Optional[str] = None       # set when the source already knows it
    preview_url: Optional[str] = None
    youtube_id: Optional[str] = None
    artwork_url: Optional[str] = None
    # Other plausible artists (YouTube: the '|' segments and the channel).
    alt_artists: list = field(default_factory=list)
    # Video durations include intros/outros, so they only loosely bound the
    # track length.
    loose_duration: bool = False
    # 'Song - Artist' titles exist too: the other reading, tried if the first fails.
    swapped: Optional[tuple] = None

    def label(self) -> str:
        return f"{self.title} — {self.artist}" if self.artist else self.title


@dataclass
class Playlist:
    name: Optional[str]
    items: list = field(default_factory=list)
    note: Optional[str] = None


# --------------------------------------------------------------------- detect

_SPOTIFY_ID = re.compile(r"(?:^|/|:)playlist(?::|/)([A-Za-z0-9]{22})(?:$|[/?#])")


def detect(raw: str) -> Optional[Source]:
    """Which service a pasted link belongs to, and the playlist id in it."""
    s = (raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z0-9]{22}", s):
        return Source("spotify", s, f"https://open.spotify.com/playlist/{s}")
    m = _SPOTIFY_ID.search(s)
    if m and ("spotify" in s.lower()):
        return Source("spotify", m.group(1), f"https://open.spotify.com/playlist/{m.group(1)}")
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    u = urlparse(s)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    q = parse_qs(u.query)

    if host.endswith("music.apple.com"):
        # /us/playlist/<slug>/pl.<id>  (user playlists are pl.u-<id>)
        pid = next((p for p in parts if p.startswith("pl.")), None)
        if pid:
            store = parts[0] if parts and len(parts[0]) == 2 else "us"
            return Source("apple", pid, s, storefront=store.lower())
        return None
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                "youtu.be", "www.youtu.be"):
        pid = (q.get("list") or [None])[0]
        if pid and re.fullmatch(r"[A-Za-z0-9_-]{10,64}", pid):
            return Source("youtube", pid, f"https://www.youtube.com/playlist?list={pid}")
        return None
    if host.endswith("deezer.com"):
        if "playlist" in parts:
            i = parts.index("playlist")
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                return Source("deezer", parts[i + 1], s)
        return None
    if host.endswith("deezer.page.link") or host == "link.deezer.com":
        # share short-link; resolved by fetch() following the redirect
        return Source("deezer", "", s)
    if host.startswith("music.amazon.") or ("amazon." in host and "music" in host):
        if len(parts) >= 2 and parts[-2] in ("playlists", "user-playlists"):
            return Source("amazon", parts[-1], s)
        return None
    return None


# ---------------------------------------------------------------- normalising

_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+.*$", re.I)
_NOISE_WORDS = re.compile(
    r"\b(official\s+(?:music\s+)?(?:video|audio|lyric(?:s)?\s+video|visuali[sz]er|mv)|"
    r"music\s+video|lyric(?:s)?\s+video|lyrics|audio|visuali[sz]er|full\s+(?:video|song)|"
    r"video\s+song|4k|hd|hq|m/?v)\b", re.I)
_BRACKET_NOISE = re.compile(
    r"official|video|audio|lyric|visuali[sz]er|\bmv\b|\bhd\b|\b4k\b|remaster|explicit|"
    r"from\s+|full\s+song|out\s+now|\bft\b|feat", re.I)


def norm(s: str) -> str:
    """Comparison form: lowercase, no accents, no brackets/feat, words only."""
    s = html.unescape(s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _BRACKETS.sub(" ", s)
    s = re.sub(r"\s+-\s+.*(remaster|version|edit|mix|live).*$", " ", s, flags=re.I)
    s = _FEAT.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return " ".join(s.split())


def clean_channel(channel: str) -> str:
    c = re.sub(r"\s*-\s*topic$", "", channel or "", flags=re.I)
    c = re.sub(r"vevo$", "", c, flags=re.I)
    c = re.sub(r"\b(official|music|records|tv)\b", " ", c, flags=re.I)
    return " ".join(c.split())


def parse_youtube_title(title: str, channel: str = "") -> tuple:
    """(artist, song, alt_artists, swapped) from a YouTube video title.

    `swapped` is the reverse reading ('Song - Artist - Label') as
    (song, artist), or None; resolve() tries it if the first reading fails.

    'Shakira, Burna Boy - Dai Dai (Official Video)' -> ('Shakira, Burna Boy', 'Dai Dai', [...])
    "KATSEYE (캣츠아이) 'Hootie Frutti' Official MV"   -> ('KATSEYE', 'Hootie Frutti', [...])
    'Banjaare | Barsaat | Singer A | Label'           -> (channel, 'Banjaare', ['Barsaat', 'Singer A', ...])
    Without a separator the channel stands in for the artist, and every other
    '|' segment is offered as an alternative (film songs put the film, singers
    and label there).
    """
    raw = html.unescape(title or "")
    segments = [x.strip() for x in raw.split("|")]
    t = segments[0]
    alts = [clean_channel(x) for x in segments[1:] if x.strip()][:4]
    if channel:
        alts.append(clean_channel(channel))
    # K-pop convention: ARTIST 'Song' Official MV — the quoted part is the song.
    q = re.search(r"['‘’\"“”]([^'‘’\"“”]{1,80})['‘’\"“”]", t)
    if q and not re.search(r"\s[-–—]\s", t):
        before = _BRACKETS.sub(" ", t[:q.start()]).strip(" -–—:")
        if before:
            return " ".join(before.split()), q.group(1).strip(), [a for a in alts if a], None
    t = re.sub(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]",
               lambda m: " " if _BRACKET_NOISE.search(m.group(1)) else m.group(0), t)
    t = _NOISE_WORDS.sub(" ", t)
    t = re.sub(r"['\"“”‘’]", "", t)
    t = " ".join(t.split()).strip(" -–—:")
    artist, song = "", t
    for sep in (" - ", " – ", " — ", " -- "):
        if sep in t:
            artist, song = (x.strip() for x in t.split(sep, 1))
            break
    # The reverse reading ('Song - Artist - Label'), offered to resolve().
    swapped = None
    if artist:
        other = re.split(r"\s[-–—]\s", song)[0].strip()
        if other:
            swapped = (_FEAT.sub("", artist).strip(), other)
    if not artist:
        artist = clean_channel(channel)
    song = _FEAT.sub("", song).strip()
    song = _BRACKETS.sub("", song).strip() or song
    return artist, song, [a for a in alts if a and a != artist], swapped


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:                    # 'the cure' vs 'the cure acoustic'
        return 0.9
    ta, tb = set(a.split()), set(b.split())
    jacc = len(ta & tb) / len(ta | tb)
    return max(jacc, SequenceMatcher(None, a, b).ratio())


def artist_similarity(item_artist: str, cand_artists: list) -> float:
    """Best match of the item's artist against any of the candidate's artists,
    also accepting a candidate artist that appears inside the item's string
    ('Shakira, Burna Boy' contains 'Shakira')."""
    ia = norm(item_artist)
    if not ia:
        return 0.5                           # unknown artist: neutral
    best = 0.0
    for ca in cand_artists:
        c = norm(ca)
        if not c:
            continue
        if c in ia or ia in c:
            best = max(best, 0.95)
        best = max(best, similarity(ia, c))
    return best


def score(item: Item, cand: dict) -> float:
    """0..1. Title and artist similarity, gated by duration when both known."""
    if item.isrc and (cand.get("external_ids") or {}).get("isrc") == item.isrc:
        return 1.0
    title_s = similarity(item.title, cand.get("name") or "")
    names = [a.get("name", "") for a in cand.get("artists") or []]
    artist_s = max(artist_similarity(a, names) for a in [item.artist, *item.alt_artists])
    s = 0.6 * title_s + 0.4 * artist_s
    d1, d2 = item.duration_ms, cand.get("duration_ms")
    if d1 and d2:
        diff = abs(d1 - d2) / 1000
        if item.loose_duration:              # video: intro/outro, but not a different song
            if diff > 90:
                s -= 0.2
            elif diff > 45:
                s -= 0.06
        elif diff > 30:
            s -= 0.35                        # different recording / edit
        elif diff > 10:
            s -= 0.12
        elif diff <= 3:
            s += 0.05
    return max(0.0, min(1.0, s))


MATCH_THRESHOLD = 0.72


def choose(item: Item, candidates: list) -> tuple:
    """Best candidate and its score; (None, best_score) below the threshold."""
    best, best_s = None, 0.0
    for c in candidates or []:
        if not c or not c.get("id"):
            continue
        s = score(item, c)
        if s > best_s:
            best, best_s = c, s
    return (best, best_s) if best_s >= MATCH_THRESHOLD else (None, best_s)


def search_queries(item: Item) -> list:
    """The Spotify search for one reading of a song: ONE query.

    A loose 'Title Artist' text search, top 5, judged by choose(), catches
    the variants a strict `track:"" artist:""` filter misses (feat. credits,
    accents, 'A & B') — so a ladder of stricter-then-looser queries only
    spent quota. An ISRC, when a source has one, is exact and goes first.
    """
    t = item.title.replace('"', "").strip()
    a = item.artist.replace('"', "").strip()
    first_artist = re.split(r",|&| x | and ", a)[0].strip() if a else ""
    if item.isrc:
        return [f"isrc:{item.isrc}"]
    if not t:
        return []
    return [f"{t} {first_artist}".strip()]


# --------------------------------------------------------------------- fetch

HttpGet = Callable[..., object]   # requests.get-compatible


def _json_ld(page: str) -> list:
    out = []
    for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            out.append(json.loads(block))
        except ValueError:
            continue
    return out


def fetch_spotify_embed(source: Source, get: HttpGet) -> Playlist:
    r = get(f"https://open.spotify.com/embed/playlist/{source.id}",
            headers={"User-Agent": UA, "Accept-Language": "en-US"}, timeout=30)
    if r.status_code == 404:
        raise ImportError_("playlist_not_found")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if r.status_code != 200 or not m:
        raise ImportError_("source_unreadable", "Spotify's public player page had no track list")
    entity = (json.loads(m.group(1)).get("props", {}).get("pageProps", {})
              .get("state", {}).get("data", {}).get("entity", {}))
    items = []
    for t in entity.get("trackList") or []:
        uri = t.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            continue                          # episodes / local files
        items.append(Item(
            title=t.get("title") or "?", artist=t.get("subtitle") or "",
            duration_ms=t.get("duration"), spotify_id=uri.rsplit(":", 1)[-1],
            preview_url=(t.get("audioPreview") or {}).get("url"),
        ))
    note = None
    if len(items) >= EMBED_CAP:
        note = (f"Spotify's public view shows the first {EMBED_CAP} songs. "
                "Sign in with Spotify to import the whole playlist.")
    return Playlist(entity.get("name"), items, note)


def fetch_apple(source: Source, get: HttpGet) -> Playlist:
    r = get(source.url, headers={"User-Agent": UA, "Accept-Language": "en-US"}, timeout=30)
    if r.status_code == 404:
        raise ImportError_("playlist_not_found")
    r.encoding = "utf-8"
    pl = next((b for b in _json_ld(r.text) if b.get("@type") == "MusicPlaylist"), None)
    if r.status_code != 200 or not pl:
        raise ImportError_("source_unreadable", "Apple Music page had no playlist data")
    ids, durations = [], {}
    for t in pl.get("track") or []:
        m = re.search(r"/(\d+)(?:\?|$)", t.get("url") or "")
        if m:
            ids.append(m.group(1))
            durations[m.group(1)] = _iso_duration_ms(t.get("duration"))
    found = {}
    for i in range(0, len(ids), ITUNES_BATCH):
        chunk = ids[i:i + ITUNES_BATCH]
        lk = get("https://itunes.apple.com/lookup",
                 params={"id": ",".join(chunk), "entity": "song", "country": source.storefront},
                 timeout=30)
        for x in (lk.json() if lk.status_code == 200 else {}).get("results", []):
            if x.get("wrapperType") == "track":
                found[str(x.get("trackId"))] = x
    items = []
    for i in ids:
        x = found.get(i)
        if not x:
            continue
        items.append(Item(
            title=x.get("trackName") or "?", artist=x.get("artistName") or "",
            album=x.get("collectionName"),
            duration_ms=x.get("trackTimeMillis") or durations.get(i),
            artwork_url=(x.get("artworkUrl100") or "").replace("100x100", "600x600") or None,
        ))
    missing = len(ids) - len(items)
    note = f"{missing} song(s) could not be looked up on Apple." if missing else None
    return Playlist(html.unescape(pl.get("name") or "") or None, items, note)


def _iso_duration_ms(v) -> Optional[int]:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v or "")
    if not m:
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return ((h * 60 + mi) * 60 + s) * 1000


def fetch_youtube(source: Source, ytdlp_extract=None) -> Playlist:
    if ytdlp_extract is None:
        import yt_dlp  # production dependency, imported lazily

        def ytdlp_extract(url):
            with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "quiet": True,
                                   "skip_download": True, "no_warnings": True}) as y:
                return y.extract_info(url, download=False)
    try:
        info = ytdlp_extract(source.url)
    except Exception as e:  # yt-dlp raises its own DownloadError
        msg = str(e).lower()
        if "does not exist" in msg or "unavailable" in msg or "private" in msg:
            raise ImportError_("playlist_not_found")
        raise ImportError_("source_unreadable", str(e)[:200])
    items = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        artist, song, alts, swapped = parse_youtube_title(
            e.get("title") or "", e.get("channel") or e.get("uploader") or "")
        if not song:
            continue
        items.append(Item(title=song, artist=artist, alt_artists=alts, loose_duration=True,
                          swapped=swapped,
                          duration_ms=int(e["duration"] * 1000) if e.get("duration") else None,
                          youtube_id=e["id"]))
    return Playlist(info.get("title"), items, None)


AMAZON_CONFIG_URL = "https://music.amazon.com/config.json?skipToken=false&clientApplication=skyfire"
AMAZON_PAGE_URL = "https://na.web.skill.music.a2z.com/api/showHome"
# The web player's own descriptors (2026-10-10). Only the tokens from
# config.json change per visit; these identify the client type.
AMAZON_APP_VERSION = "1.0.11529.0"


def _amazon_headers(cfg: dict, page_url: str) -> dict:
    """The x-amzn-* header set the web player sends, built from config.json."""
    csrf = cfg.get("csrf") or {}
    return {
        "x-amzn-authentication": json.dumps({
            "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
            "accessToken": cfg.get("accessToken") or ""}),
        "x-amzn-csrf": json.dumps({
            "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
            "token": csrf.get("token"), "timestamp": csrf.get("ts"), "rndNonce": csrf.get("rnd")}),
        "x-amzn-device-id": str(cfg.get("deviceId") or ""),
        "x-amzn-session-id": str(cfg.get("sessionId") or ""),
        "x-amzn-request-id": __import__("uuid").uuid4().hex,
        "x-amzn-timestamp": str(int(time.time() * 1000)),
        "x-amzn-application-version": AMAZON_APP_VERSION,
        "x-amzn-device-family": "WebPlayer",
        "x-amzn-device-model": "WEBPLAYER",
        "x-amzn-os-version": "1.0",
        "x-amzn-device-language": "en_US",
        "x-amzn-currency-of-preference": "USD",
        "x-amzn-device-width": "1920",
        "x-amzn-device-height": "1080",
        "x-amzn-device-time-zone": "America/New_York",
        "x-amzn-music-domain": "music.amazon.com",
        "x-amzn-page-url": page_url,
        "x-amzn-user-agent": UA,
        "x-amzn-affiliate-tags": "", "x-amzn-age-band": "", "x-amzn-feature-flags": "",
        "x-amzn-has-profile-id": "", "x-amzn-ref-marker": "", "x-amzn-referer": "",
        "x-amzn-video-player-token": "", "x-amzn-weblab-id-overrides": "",
    }


def _amazon_duration_ms(v) -> Optional[int]:
    """'03:01' or '1:02:03' -> ms."""
    parts = [p for p in str(v or "").strip().split(":")]
    if not parts or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total * 1000


def _amazon_text(v) -> str:
    v = v.get("text") if isinstance(v, dict) else v
    return html.unescape(str(v or "")).strip()


def parse_amazon_page(page: dict) -> Playlist:
    """Song rows and the playlist name out of a showHome response.

    A song row is any object with primaryText (title) + secondaryText1
    (artist); secondaryText2 is the album and secondaryText3 the length.
    """
    rows, name = [], None

    def walk(o):
        nonlocal name
        if isinstance(o, dict):
            if o.get("primaryText") and "secondaryText1" in o:
                rows.append(o)
            if name is None and o.get("headerText") and isinstance(o.get("headerText"), (str, dict)):
                name = _amazon_text(o["headerText"]) or None
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(page)
    items = []
    for r in rows:
        title = _amazon_text(r.get("primaryText"))
        if not title:
            continue
        items.append(Item(title=title, artist=_amazon_text(r.get("secondaryText1")),
                          album=_amazon_text(r.get("secondaryText2")) or None,
                          duration_ms=_amazon_duration_ms(_amazon_text(r.get("secondaryText3")))))
    return Playlist(name, items, None)


def fetch_amazon(source: Source, post) -> Playlist:
    """Read a public Amazon Music playlist the way the web player does.
    `post` is requests.post-compatible."""
    u = urlparse(source.url if "://" in source.url else "https://" + source.url)
    path = u.path or f"/playlists/{source.id}"
    page_url = f"https://music.amazon.com{path}"
    try:
        cfg = post(AMAZON_CONFIG_URL, headers={"Content-Type": "application/json",
                                               "Referer": "https://music.amazon.com/",
                                               "User-Agent": UA}, data="{}", timeout=30)
        cfg = cfg.json() if cfg.status_code == 200 else {}
        if not cfg.get("csrf"):
            raise ImportError_("source_unreadable", "Amazon Music did not issue a visitor token")
        deeplink = json.dumps({"interface": "DeeplinkInterface.v1_0.DeeplinkClientInformation",
                               "deeplink": path})
        r = post(AMAZON_PAGE_URL,
                 headers={"Content-Type": "text/plain;charset=UTF-8",
                          "Referer": "https://music.amazon.com/", "User-Agent": UA},
                 data=json.dumps({"deeplink": deeplink,
                                  "headers": json.dumps(_amazon_headers(cfg, page_url))}),
                 timeout=60)
        if r.status_code != 200:
            raise ImportError_("source_unreadable", f"Amazon Music answered {r.status_code}")
        pl = parse_amazon_page(r.json())
    except ImportError_:
        raise
    except Exception as e:  # network, JSON, a changed response shape
        raise ImportError_("source_unreadable", f"Amazon Music: {e.__class__.__name__}")
    if not pl.items:
        raise ImportError_("playlist_not_found", "No songs found — is the playlist public?")
    return pl


def fetch(source: Source, get: HttpGet, ytdlp_extract=None, post=None) -> Playlist:
    """Read the playlist. Spotify here means the public embed;
    services/ingest_jobs.py's _run_playlist_url_job tries the Web API first when the user is signed in with Spotify."""
    if source.kind not in SUPPORTED:
        raise ImportError_(f"{source.kind}_not_supported",
                           f"{source.kind.title()} playlists are not supported yet.")
    if source.kind == "spotify":
        return fetch_spotify_embed(source, get)
    if source.kind == "apple":
        return fetch_apple(source, get)
    if source.kind == "youtube":
        return fetch_youtube(source, ytdlp_extract)
    if source.kind == "amazon":
        if post is None:
            import requests
            post = requests.post
        return fetch_amazon(source, post)
    raise ImportError_("unsupported_source")


# ------------------------------------------------------------------ resolving
#
# Spotify API budget. Measured 2026-10-10: ~650 calls in ~30 minutes from
# repeated test imports got this app a 429 with Retry-After: 70320 (19.5 h),
# for every caller of the app — production included. So:
#   * a Spotify-link song costs ZERO calls (the embed gives its exact id),
#   * a song our own catalogue already has costs ZERO calls (catalog hook),
#   * anything else gets one search (two for an ambiguous YouTube title),
#   * calls are spaced at least MIN_CALL_INTERVAL_S apart,
#   * a Retry-After longer than RATE_LIMIT_GIVE_UP_S stops the whole import
#     (SpotifyRateLimited) instead of retrying into a longer block.

# One search per reading of a song; the second is only ever used by YouTube
# titles that also read as 'Song - Artist' (Item.swapped).
MAX_SEARCHES_PER_SONG = 2
MIN_CALL_INTERVAL_S = 0.35
RATE_LIMIT_GIVE_UP_S = 30


class SpotifyRateLimited(Exception):
    """Spotify asked us to back off for longer than an import can wait."""

    def __init__(self, retry_after_s: float):
        super().__init__(f"Spotify rate limit, retry after {retry_after_s:.0f}s")
        self.retry_after_s = retry_after_s


class _Pacer:
    """Process-wide spacing between Spotify calls (one app quota)."""

    def __init__(self, interval: float, clock=time.monotonic, sleep=time.sleep):
        import threading
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = self.clock()
            if now < self._next:
                self.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


PACER = _Pacer(MIN_CALL_INTERVAL_S)


def spotify_search(token: str, query: str, get: HttpGet, limit: int = 5, pacer: _Pacer = None) -> list:
    """Spotify /v1/search tracks. One short 429 is waited out once; a long
    one raises SpotifyRateLimited so the caller stops importing."""
    pacer = pacer or PACER
    for attempt in range(2):
        pacer.wait()
        r = get("https://api.spotify.com/v1/search",
                params={"q": query, "type": "track", "limit": limit},
                headers={"Authorization": f"Bearer {token}"}, timeout=20)
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After") or 5)
            except (TypeError, ValueError):
                wait = 5.0
            if wait > RATE_LIMIT_GIVE_UP_S or attempt:
                raise SpotifyRateLimited(wait)
            pacer.sleep(wait)
            continue
        if r.status_code != 200:
            return []
        return (r.json().get("tracks") or {}).get("items") or []
    return []


def item_as_track(item: Item) -> dict:
    """Spotify-shaped track dict for _process_track, built from what the
    source already told us. The embed's preview_url is deliberately NOT
    passed on: ingest_pipeline's preview stage only fills artwork, album and
    genre while preview_url is still empty (stage_preview.process_row), so
    keeping the slot empty is what gets the cover and album filled in."""
    return {
        "id": item.spotify_id, "name": item.title,
        "artists": [{"name": item.artist or "?"}],
        "album": {"name": item.album, "images": [{"url": item.artwork_url}] if item.artwork_url else []},
        "duration_ms": item.duration_ms, "external_ids": {"isrc": item.isrc} if item.isrc else {},
        "preview_url": None,
    }


def resolve(item: Item, token: str, get: HttpGet, catalog: Callable = None,
            pacer: _Pacer = None) -> tuple:
    """(Spotify track dict, None) or (None, reason).

    Order, cheapest first: the source's own Spotify id (0 calls), our
    catalogue via `catalog(item)` (0 calls), then Spotify search — at most
    MAX_SEARCHES_PER_SONG queries across both readings of a YouTube title.
    Raises SpotifyRateLimited if Spotify says to back off for long.
    """
    if item.spotify_id:
        return item_as_track(item), None
    if catalog is not None:
        hit = catalog(item)
        if hit:
            return hit, None
    readings = [item]
    if item.swapped:
        t, a = item.swapped
        readings.append(Item(title=t, artist=a, alt_artists=item.alt_artists,
                             duration_ms=item.duration_ms, loose_duration=item.loose_duration))
    queries = []
    for reading in readings:
        queries += [(reading, q) for q in search_queries(reading)]
    # Interleave so the swapped reading still gets a turn inside the budget.
    if len(readings) > 1:
        a = [x for x in queries if x[0] is readings[0]]
        b = [x for x in queries if x[0] is readings[1]]
        queries = [q for pair in zip(a, b) for q in pair] + a[len(b):] + b[len(a):]
    best_s = 0.0
    for reading, q in queries[:MAX_SEARCHES_PER_SONG]:
        cand, s = choose(reading, spotify_search(token, q, get, pacer=pacer))
        best_s = max(best_s, s)
        if cand:
            return cand, None
    return None, f"no confident match (best {best_s:.2f})"
