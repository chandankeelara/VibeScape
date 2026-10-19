"""
Evaluation groups for the DJ: sets of spotify_ids that "belong together".

Derived from the data (one read of the eval copy, or of any backup whose
tracks.id values match the server under test):

  lang        tracks.language, groups of at least LANG_MIN
  lang_band   language x energy band: vibe_score_ml terciles computed
              within the language, for languages with at least BAND_LANG_MIN
  album       same album or film, at least ALBUM_MIN tracks. Film
              soundtracks are keyed on the film: 'X (From "Film")' and
              'Film (Original Motion Picture Soundtrack)' fall together
  artist      same primary artist (or composer, who is credited first on
              most film songs here), at least ARTIST_MIN tracks

The library is the eval user's: ingestion_status='done' with a fused
embedding, the DJ's own candidate set. Everything is restricted to it, so
a group's base rate (its share of the library) is the precision a random
recommender would score.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from dj_eval.client import Track

LANG_MIN = 8
BAND_LANG_MIN = 30
ALBUM_MIN = 4
ARTIST_MIN = 6
BANDS = ("low", "mid", "high")


@dataclass
class Group:
    name: str
    family: str
    members: list            # spotify_ids, sorted

    @property
    def size(self) -> int:
        return len(self.members)


def load_catalog(db_path: Path) -> dict:
    """spotify_id -> Track for every track in the DJ's candidate set."""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT t.id, t.spotify_id, t.title, t.artist, t.album, t.language, "
            "       t.vibe_score_ml, t.duration_ms "
            "FROM tracks t JOIN track_embeddings te ON te.track_id = t.id "
            "WHERE t.ingestion_status = 'done' AND te.fused_embedding IS NOT NULL "
            "  AND t.spotify_id IS NOT NULL").fetchall()
    finally:
        conn.close()
    return {r[1]: Track(spotify_id=r[1], id=int(r[0]), title=r[2] or "", artist=r[3] or "",
                        album=r[4] or "", language=r[5], vibe=r[6], duration_ms=r[7])
            for r in rows}


# "Tyler, The Creator" is one artist; "Yuvan Shankar Raja,Sid Sriram" is two.
# Credits here join artists with a bare comma; "&" is part of names
# ("Earth, Wind & Fire"), so it is not a separator.
_ARTIST_SPLIT = re.compile(r",(?! )|;")


def primary_artist(artist: str) -> str:
    return (_ARTIST_SPLIT.split(artist or "")[0]).strip().lower()


_FROM_FILM = re.compile(r"""\(\s*from\s+["'“‘](.+?)["'”’]""", re.I)
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_SUFFIX = re.compile(r"\s+-\s+.*$")


def album_key(album: str) -> str:
    """Film name when the album names one, else the album without its
    bracketed edition / soundtrack / language qualifiers."""
    a = album or ""
    m = _FROM_FILM.search(a)
    if m:
        a = m.group(1)
    a = _BRACKETS.sub("", a)
    a = _SUFFIX.sub("", a)
    return re.sub(r"\s+", " ", a).strip().lower()


_FILM = re.compile(r"soundtrack|\bo\.?s\.?t\b|motion picture|\(\s*from\s", re.I)


def album_group_key(t: Track) -> str:
    """Films group on the film alone (their songs credit several composers
    and singers); any other album on album + primary artist, so two
    unrelated 'Greatest Hits' stay apart."""
    k = album_key(t.album)
    if not k or _FILM.search(t.album or ""):
        return k
    return f"{k} / {primary_artist(t.artist)}"


def _terciles(values: list) -> tuple:
    v = sorted(values)
    n = len(v)
    return v[n // 3], v[(2 * n) // 3]


def derive(catalog: dict) -> list[Group]:
    by_lang, by_album, by_artist = defaultdict(list), defaultdict(list), defaultdict(list)
    for sid, t in catalog.items():
        if t.language:
            by_lang[t.language].append(sid)
        if album_group_key(t):
            by_album[album_group_key(t)].append(sid)
        if primary_artist(t.artist):
            by_artist[primary_artist(t.artist)].append(sid)

    groups: list[Group] = []
    for lang, sids in sorted(by_lang.items()):
        if len(sids) >= LANG_MIN:
            groups.append(Group(f"lang:{lang}", "lang", sorted(sids)))
        scored = [s for s in sids if catalog[s].vibe is not None]
        if len(scored) >= BAND_LANG_MIN:
            lo, hi = _terciles([catalog[s].vibe for s in scored])
            bands = defaultdict(list)
            for s in scored:
                v = catalog[s].vibe
                bands["low" if v < lo else "mid" if v < hi else "high"].append(s)
            for b in BANDS:
                groups.append(Group(f"lang_band:{lang}:{b}", "lang_band", sorted(bands[b])))
    for key, sids in sorted(by_album.items()):
        if len(sids) >= ALBUM_MIN:
            groups.append(Group(f"album:{key}", "album", sorted(sids)))
    for key, sids in sorted(by_artist.items()):
        if len(sids) >= ARTIST_MIN:
            groups.append(Group(f"artist:{key}", "artist", sorted(sids)))
    return groups
