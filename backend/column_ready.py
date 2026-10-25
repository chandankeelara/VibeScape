"""The per-process cache behind the app's "is this optional column there
yet?" checks: track_rows._genre_scores_ready (tracks.genre_scores) and
services/vectors.py's _fused_v2_ready / _fused_v3_ready. Production's
schema can lag a deploy (a migration script adds the column), so a route
that would name a missing column asks first, and must not ask the database
on every request.

A yes is kept for the life of the process. A no is kept for `recheck_s`
from the time it was asked, then asked again, so a migration run while
instances are up is picked up without a restart. The first no is logged
once per process. Pinned by tests/backend/test_column_ready.py.

No imports from the app: track_rows.py and services/vectors.py both use it.
"""
import time


def cached_ready(st: dict, recheck_s: float, probe, warn) -> bool:
    """Answer from `st` ({"ready", "checked_at", "warned"}, mutated in
    place, never rebound: tests reset it) or ask `probe()`, which returns
    (ready, why); an exception from it is a no, its text the why. `warn(why)`
    runs on the first no only."""
    now = time.time()
    if st["ready"] or (st["ready"] is False and now - st["checked_at"] < recheck_s):
        return bool(st["ready"])
    try:
        ready, why = probe()
    except Exception as e:
        ready, why = False, str(e)
    if not ready and not st["warned"]:
        st["warned"] = True
        warn(why)
    st["ready"], st["checked_at"] = ready, now
    return ready
