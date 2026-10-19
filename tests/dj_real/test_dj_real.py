"""The DJ through its real HTTP endpoint, on a copy of production data.

Skipped unless one of these is set, so a fresh clone stays green:

  DJ_EVAL_DB         a copy prepared by scripts/dj_eval/setup_copy.py; a
                     uvicorn is started on it for this module and stopped
  DJ_EVAL_BASE_URL   a server that is already running, with
  DJ_EVAL_TOKEN      a session token on it, and
  DJ_EVAL_CATALOG_DB a copy of that server's database (ids must match)

Only basics are asserted: every call answers 200, nothing comes back twice,
and nothing this session already put in front of the listener (the seed,
anything played or skipped) comes back. Quality floors wait for the first
reports of scripts/dj_eval/run_eval.py.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

DB = os.environ.get("DJ_EVAL_DB")
BASE_URL = os.environ.get("DJ_EVAL_BASE_URL")

pytestmark = pytest.mark.skipif(
    not DB and not BASE_URL,
    reason="set DJ_EVAL_DB (a setup_copy.py copy) or DJ_EVAL_BASE_URL + DJ_EVAL_TOKEN "
           "+ DJ_EVAL_CATALOG_DB to run against a real server")


@pytest.fixture(scope="module")
def target():
    from dj_eval import groups
    from dj_eval.client import DjClient, login
    from dj_eval.server import LocalServer
    from dj_eval.setup_copy import load_meta

    if DB:
        db = Path(DB)
        user = load_meta(db)["users"][0]
        catalog = groups.load_catalog(db)
        srv = LocalServer(db).start()
        try:
            token = login(srv.base_url, user["email"], user["password"])
            yield {"client": DjClient(srv.base_url, token), "catalog": catalog,
                   "db": db, "user_id": user["user_id"], "base_url": srv.base_url}
        finally:
            srv.stop()
    else:
        catalog = groups.load_catalog(Path(os.environ["DJ_EVAL_CATALOG_DB"]))
        yield {"client": DjClient(BASE_URL, os.environ["DJ_EVAL_TOKEN"]), "catalog": catalog,
               "db": None, "user_id": None, "base_url": BASE_URL}


def _session(target, events: bool):
    from dj_eval.client import Session
    from dj_eval.setup_copy import reset_user
    if events and target["db"] is not None:
        reset_user(target["db"], target["user_id"])
    return Session(target["client"], target["catalog"], limit=10,
                   post_events=events and target["db"] is not None)


def _check(s, recs):
    ids = [r.id for r in recs]
    assert recs, "no recommendations"
    assert len(ids) == len(set(ids)), f"duplicates in {ids}"
    returned_seen = set(ids) & set(s.seen)
    assert not returned_seen, f"returned tracks already played or skipped: {returned_seen}"


def test_single_seed(target):
    rng = random.Random(1)
    for sid in rng.sample(sorted(target["catalog"]), 5):
        s = _session(target, events=False)
        seed = s.search(sid)
        recs = s.recommend()
        _check(s, recs)
        assert seed.id not in {r.id for r in recs}
        assert s.steps[-1].mode_used.startswith("dj_replay_")


def test_session_with_skips(target):
    """Completions, then skips: neither the played nor the skipped tracks
    come back, at any step."""
    rng = random.Random(2)
    pool = rng.sample(sorted(target["catalog"]), 7)
    s = _session(target, events=True)
    for sid in pool[:4]:
        s.complete(sid)
        _check(s, s.recommend())
    for sid in pool[4:]:
        s.skip(sid, 0.1)
        _check(s, s.recommend())


def test_autoplay_loop(target):
    """Play the top pick ten times, alternating finish and skip, as the
    app's autoplay does: no pick is ever offered twice in the session."""
    s = _session(target, events=True)
    s.search(sorted(target["catalog"])[0])
    s.end(1.0)
    for i in range(10):
        recs = s.recommend()
        _check(s, recs)
        s.play(recs[0].spotify_id)
        s.end(1.0 if i % 2 else 0.1)
