"""Fuzzy search over one user's library: title, artist, album and language.

Pure — no DB, no clock — so the endpoint and the tests run the same code.

Every word of the query must match some word of the track (AND), each one
scored by its best field:

    exact word 1.0 > word prefix 0.9 > inside a word 0.75 > typo (difflib) ≤ 0.8

weighted title 1.0, artist 0.95, language 0.9, featured 0.85, album 0.8.
"(feat. X)" / "(with X)" is moved out of the title into `featured`, so a
search for X ranks X's own songs above the ones X only appears on. A whole-query hit
on the title or artist then outranks a scattered one. Languages are stored as
ISO codes ('kn') and searched by name ('kannada', 'kanada', 'kan').

Fast enough because typo matching runs once per distinct word in the library,
not once per track: the largest local library (2026-10-10) is ~4,100 tracks.
Past ~50k tracks per user, move this to SQLite FTS5 (trigram) instead.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# ISO 639-1 codes Whisper returns — same table as frontend-next's labels.js.
LANG_NAMES = {
    "en": "english", "es": "spanish", "fr": "french", "de": "german", "it": "italian",
    "pt": "portuguese", "ru": "russian", "ja": "japanese", "ko": "korean", "zh": "chinese",
    "ar": "arabic", "hi": "hindi", "bn": "bengali", "pa": "punjabi", "ta": "tamil",
    "te": "telugu", "ml": "malayalam", "kn": "kannada", "mr": "marathi", "gu": "gujarati",
    "ur": "urdu", "tr": "turkish", "vi": "vietnamese", "th": "thai", "id": "indonesian",
    "ms": "malay", "tl": "filipino", "nl": "dutch", "sv": "swedish", "no": "norwegian",
    "da": "danish", "fi": "finnish", "pl": "polish", "cs": "czech", "el": "greek",
    "he": "hebrew", "fa": "persian", "uk": "ukrainian", "ro": "romanian", "hu": "hungarian",
}

FIELD_WEIGHT = {"title": 1.0, "artist": 0.95, "language": 0.9, "featured": 0.85, "album": 0.8}
TYPO_MIN = 0.8           # difflib ratio below this is not a match; 0.75 let
                         # "blinding" match "lining"
TYPO_MIN_LEN = 3         # 'ab' matching 'ac' is noise, not a typo


def norm(s) -> str:
    """Lowercase, accents stripped, punctuation to spaces."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def word_score(q: str, w: str) -> float:
    """How well one query word matches one library word, 0..1."""
    if q == w:
        return 1.0
    if w.startswith(q):
        return 0.9
    if len(q) >= TYPO_MIN_LEN and q in w:
        return 0.75
    # Typos keep the first letter ("goshal"/"ghoshal", "arjit"/"arijit");
    # requiring it cuts most false friends ("blinding"/"binding" aside).
    if len(q) < TYPO_MIN_LEN or q[0] != w[0] or abs(len(q) - len(w)) > 2:
        return 0.0
    sm = SequenceMatcher(None, q, w)
    if sm.real_quick_ratio() < TYPO_MIN or sm.quick_ratio() < TYPO_MIN:
        return 0.0
    r = sm.ratio()
    return 0.8 * r if r >= TYPO_MIN else 0.0


_FEAT = re.compile(r"[(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^)\]]+)[)\]]", re.I)


def _fields(row) -> dict:
    lang = (row.get("language") or "").lower().strip()
    title = str(row.get("title") or "")
    featured = " ".join(_FEAT.findall(title))
    return {
        "title": norm(_FEAT.sub(" ", title)),
        "featured": norm(featured),
        "artist": norm(row.get("artist")),
        "album": norm(row.get("album")),
        "language": LANG_NAMES.get(lang, ""),
    }


def search(rows, query: str, limit: int = 15) -> list:
    """rows: dicts with id, title, artist, album, language. Returns the
    matching rows, best first, at most `limit`."""
    q = norm(query)
    qwords = q.split()
    if not qwords:
        return []

    prepared = []
    vocab = set()
    for row in rows:
        f = _fields(row)
        words = {k: v.split() for k, v in f.items()}
        for ws in words.values():
            vocab.update(ws)
        prepared.append((row, f, words))

    # Each query word against each distinct library word, once.
    scores = []
    for qw in qwords:
        hits = {}
        for w in vocab:
            s = word_score(qw, w)
            if s:
                hits[w] = s
        scores.append(hits)

    ranked = []
    for row, f, words in prepared:
        total = 0.0
        for hits in scores:
            best = 0.0
            for field, ws in words.items():
                wt = FIELD_WEIGHT[field]
                for w in ws:
                    s = hits.get(w, 0.0) * wt
                    if s > best:
                        best = s
            if best == 0.0:
                break                       # every query word must match
            total += best
        else:
            score = total / len(qwords)
            # A phrase hit beats the same words scattered across fields.
            if f["title"].startswith(q):
                score += 0.5
            elif q in f["title"]:
                score += 0.3
            elif f["artist"].startswith(q):
                score += 0.25
            elif q in f["artist"]:
                score += 0.15
            ranked.append((score, f["title"], row))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [r for _, _, r in ranked[:limit]]
