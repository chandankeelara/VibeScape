"""The in-process ingest job registry: JOBS (job_id -> job dict), its lock,
_new_job (the dict a route creates) and the three helpers every ingest
worker calls.

Moved out of app.py unchanged (refactor PR 2, 2026-10-25). JOBS is mutated
in place, never rebound, so `from services.jobs import JOBS` sees the same
dict. A job dict is created by the route that starts the worker; every
worker must write a terminal status on every exit path (backend/CLAUDE.md,
"Background work").
"""
import threading
from typing import Optional

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------- job state
#
# Restored. 1be1c59 ("drop the synchronous ingest path") deleted these three
# along with the sync ingest worker, but left 45 call sites behind — so every
# ingest job raised NameError on its first _update_job call, the thread died,
# and the job dict sat at its initial zeros forever. The status endpoint kept
# answering 200 with those zeros, which is why the UI polled indefinitely and
# showed no progress: there was nothing to show and nothing to stop it, since
# the error handler called _update_job too and raised again.
#
# All three take JOBS_LOCK because a job is written by its worker thread and
# read by request handlers.


def _new_job(user_id: int, head: Optional[dict] = None, extra_counts: Optional[dict] = None) -> dict:
    """A job dict as a route creates it, before its worker starts.

    The keys come out in the order /api/ingest/status returns them, which
    the three job types share only in part, so the caller places its own:
    status, user_id, current_track, then `head` (what kind of job), then the
    progress counters, then `extra_counts` (the playlist-link job's
    unmatched songs), then cancel_requested and error_message. Pinned per
    job type by tests/backend/test_ingest_jobs.py ("exact dict")."""
    job = {"status": "pending", "user_id": user_id, "current_track": None}
    job.update(head or {})
    job.update({
        "total": 0,
        "processed": 0,
        # True while Spotify pages are still arriving. `total` is a running
        # discovery count until this flips, so the UI should show "found N
        # so far" rather than a percentage of a moving target.
        "collecting": True,
        # Four mutually exclusive buckets (sum == processed) matching the
        # two-phase ingest flow. See _process_track's docstring.
        "added_to_library": 0,     # global row already done, just linked
        "already_in_library": 0,   # user already had this exact link
        "queued_for_analysis": 0,  # new/pending row, the offline pipeline finishes it
        "skipped": 0,              # skip:no_id or exceptions
    })
    job.update(extra_counts or {})
    job.update({"cancel_requested": False, "error_message": None})
    return job


def _update_job(job_id: str, **fields):
    """Merge fields into a job. Silently ignores a job that has been reaped."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)


def _bump(job_id: str, key: str, amount: int = 1):
    """Increment one counter on a job."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job[key] = int(job.get(key, 0)) + amount


def _is_cancelled(job_id: str) -> bool:
    """Whether the caller asked this job to stop. Polled between tracks."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))
