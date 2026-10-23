"""
genre@10 for the DJ eval: does the DJ keep the seed's genre?

A track's PRIMARY genre is the slug with its highest score
(ingest_pipeline/genre_tagging.py: 50 or 100 per genre, independently),
ties broken by taxonomy order (genre_taxonomy.GENRES), so a track scored
{"film_melody": 100, "folk": 100} is film_melody. An untagged track
(genre_status not 'done', or no scores) has none.

genre@10 at one moment is the share of the top 10 whose primary genre is
the seed's. It is defined only for a tagged seed: a moment whose seed has
no primary genre is left out (not scored 0), and an untagged candidate in
the top 10 counts as not sharing it. Read by replay_bench.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from ingest_pipeline.genre_taxonomy import GENRES  # noqa: E402


def primary_genre(scores, status: Optional[str] = "done") -> Optional[str]:
    """The highest-scored known slug, ties to the earlier taxonomy slot;
    None when untagged. `scores` is the stored JSON text or a dict."""
    if status != "done" or not scores:
        return None
    if isinstance(scores, str):
        try:
            scores = json.loads(scores)
        except ValueError:
            return None
    if not isinstance(scores, dict):
        return None
    best, best_score = None, 0.0
    for slug in GENRES:
        try:
            s = float(scores.get(slug) or 0)
        except (TypeError, ValueError):
            continue
        if s > best_score:
            best, best_score = slug, s
    return best


def genre_share(seed: int, top, primary_of: dict) -> Optional[float]:
    """The share of `top` whose primary genre is `seed`'s, or None when the
    seed has no primary genre (the moment is not scored)."""
    g = primary_of.get(seed)
    if g is None or not top:
        return None
    return sum(primary_of.get(t) == g for t in top) / len(top)
