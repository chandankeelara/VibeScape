"""The in-process ingest job registry: JOBS (job_id -> job dict), its lock,
and the three helpers every ingest worker calls.

Moved out of app.py unchanged (refactor PR 2, 2026-10-25). JOBS is mutated
in place, never rebound, so `from services.jobs import JOBS` sees the same
dict. A job dict is created by the route that starts the worker; every
worker must write a terminal status on every exit path (backend/CLAUDE.md,
"Background work").
"""
import threading

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
