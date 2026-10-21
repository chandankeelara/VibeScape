"""The same song in another release: album vs single, remix vs original, a
live take, a "(From "Film")" re-issue. Pure, so the DJ endpoint, the replay
and scripts/dj_eval/replay_bench.py share one definition.

    key = (base title, primary artist, language)

  base title      lower-cased; every (...) and [...] part removed, and
                  everything after " - " (Remastered, Live Version, Remix,
                  From "X", Single...); then only letters and digits are
                  kept, Latin and the Indic scripts (U+0900-U+0DFF:
                  Devanagari to Sinhala), runs of anything else become one
                  space
  primary artist  the first credit before ",", "&", " feat", " ft."
  language        must match, so a film song's Kannada and Hindi versions
                  (dubbed: same title, same composer) stay two songs

A track with no usable title or artist (the junk rows titled "?") has no
key and is never treated as a repeat: an empty key would make all of them
one song.

Validated by the team lead on the eval copy (2026-10-20): catches Belakina
Kavithe (album then single, served back to back), Mask Off and Old Town
Road (remix then original), Preetiya Hesare Neenu (live), Gudugudiya Sedi
Nodo, Salaam Rocky Bhai, Guruvara, Please Be Naked, Yaavudo Ee Bombe; keeps
Tabaahi kn and hi apart.
"""
from __future__ import annotations

import re
from typing import Optional

_SUFFIX = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]|\s+-\s+.*$")
_NON_WORD = re.compile(r"[^a-z0-9ऀ-෿]+")
_ARTIST_SPLIT = re.compile(r",|&| feat| ft\.")


def base_title(title) -> str:
    t = _SUFFIX.sub("", str(title or "").lower())
    return _NON_WORD.sub(" ", t).strip()


def primary_artist(artist) -> str:
    return _ARTIST_SPLIT.split(str(artist or "").lower())[0].strip()


def song_key(title, artist, language) -> Optional[tuple]:
    """(base title, primary artist, language), or None when the track has
    no usable title or artist."""
    b, a = base_title(title), primary_artist(artist)
    if not b or not a or a == "?":
        return None
    return (b, a, str(language or "").strip().lower())
