"""
A track's genre tags as the player shows them: `tracks.genre_scores` (JSON
object, slug -> 50 | 100, written by ingest_pipeline/genre_tagging.py) turned
into `genres`, a list of {slug, score} sorted by score, high first, then by
taxonomy order. Untagged, unreadable or empty is [].

GENRE_ORDER is a copy of ingest_pipeline.genre_taxonomy.GENRES because the
production image ships backend/ and ingest/ only (Dockerfile), not
ingest_pipeline/. tests/backend/test_track_genres.py fails when the two
differ, and when frontend-next's label map misses a slug. A slug this list
does not know is left out rather than shown raw.
"""
from __future__ import annotations

import json

GENRE_ORDER: tuple[str, ...] = (
    "pop",
    "hiphop_rap",
    "dance_edm",
    "ambient_chill",
    "rock_alt_metal",
    "rnb_soul",
    "film_melody",
    "film_mass",
    "folk",
    "devotional",
    "classical",
    "instrumental_score",
    "latin_afro",
    "reggae_dancehall",
    "country",
    "jazz_blues",
    "other",
)
_RANK = {slug: i for i, slug in enumerate(GENRE_ORDER)}
_SCORES = (50, 100)


def genres_list(raw) -> list[dict]:
    """`genre_scores` as stored -> [{slug, score}], best first."""
    if not raw:
        return []
    try:
        scores = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(scores, dict):
        return []
    out = [{"slug": slug, "score": score} for slug, score in scores.items()
           if slug in _RANK and type(score) is int and score in _SCORES]
    out.sort(key=lambda g: (-g["score"], _RANK[g["slug"]]))
    return out
