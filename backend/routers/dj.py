"""The two /api/tracks/{track_key}/similar routes: GET ranks by the
weighted-L1 vibe features, POST also takes DJ mode (mode: "dj", with the
event log, ids, excludes and the embedding variant in SimilarBody). Either
can rank the whole catalogue with `discover`.

Moved out of app.py unchanged (refactor PR 5, 2026-10-25). The ranking is
services/dj.py; these routes clamp the limit, pick the path and finish a
discovery response. `score` keeps its meaning (cosine on the DJ path) and
`final_score` is reported beside it (backend/CLAUDE.md, "Contracts the
frontend cannot infer").
"""
from typing import Optional

import bootstrap  # noqa: F401  — first: .env, sys.path (see bootstrap.py)

from fastapi import Depends, Query

from deps import require_user
from routers import Router
from services.dj import SimilarBody, _discover_finish, _similar_dj, _similar_vibe

router = Router()


@router.get("/api/tracks/{track_key}/similar")
def similar_tracks(
    track_key: str,
    limit: int = Query(8, ge=1, le=25),
    discover: bool = False,
    sess: dict = Depends(require_user),
):
    """
    Return N tracks from the caller's library most similar to the given
    track. Similarity = weighted L1 distance across the four ML feature
    dimensions (vibe_score_ml, energy_pred, danceability_pred,
    valence_pred), with a small bonus for matching mood. The current
    track is excluded from results.

    track_key can be a spotify_id (string) or a numeric internal tracks.id.
    `discover=1` ranks the whole catalogue instead (see SimilarBody).
    """
    resp = _similar_vibe(track_key, limit, sess["user_id"],
                         display_name=sess.get("display_name"), discover=discover)
    return _discover_finish(resp, sess["user_id"]) if discover else resp


@router.post("/api/tracks/{track_key}/similar")
def similar_tracks_post(
    track_key: str,
    body: Optional[SimilarBody] = None,
    sess: dict = Depends(require_user),
):
    """POST variant: supports DJ mode (fused MERT cosine) via JSON body.

    Body (all optional):
      mode: "dj" | "vibe" (default vibe)
      positive_ids: [{id, weight}, ...] — id may be internal tracks.id (int)
        or spotify_id (str). Ints skip resolution; strings batch-resolve.
      negative_ids: [{id, weight}, ...] — same shape as positive_ids
      exclude_ids: [id, ...] — mixed list of internal tracks.id ints and/or
        spotify_id strings. Ints skip resolution.
      limit: int (1..25, default 8)
      explain: bool (default false) — attach a per-track `explain` block with
        the recency penalty breakdown and rank_before / rank_after. Opt-in;
        off by default so no existing client sees a change.
      discover: bool (default false) — rank the whole catalogue, not only the
        caller's library. Each track's `in_library` and `discover` are then
        real, and `mode_used` ends in `_discover`.

    Each track carries `score` (cosine similarity on the DJ path, absent on
    the vibe path — unchanged meaning) and, when recency re-ranking is
    active, `final_score` = score - lambda * P.
    """
    if body is None:
        body = SimilarBody()
    limit = body.limit if body.limit else 8
    limit = max(1, min(int(limit), 25))
    mode = (body.mode or "vibe").lower()
    discover = bool(body.discover)
    if mode == "dj":
        resp = _similar_dj(track_key, body, sess["user_id"],
                           display_name=sess.get("display_name"))
    else:
        resp = _similar_vibe(track_key, limit, sess["user_id"],
                             display_name=sess.get("display_name"),
                             explain=bool(body.explain), discover=discover)
    return _discover_finish(resp, sess["user_id"]) if discover else resp
