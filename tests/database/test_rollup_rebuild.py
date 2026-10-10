"""user_track_stats is a cache of track_events: the inline rollup written by
POST /api/events must equal what scripts/rebuild_user_track_stats.py
recomputes from the events alone. If these ever disagree, the rebuild script
silently "repairs" correct rows into wrong ones (its own docstring)."""
import importlib
import random

import pytest

rebuild = importlib.import_module("rebuild_user_track_stats")

REASONS = ["completed", "skipped", "replaced", "abandoned", None, "weird-new-reason"]
TRIGGERS = ["next_button", "media_key", "search", "pick", "queue_jump", None]
SOURCES = ["user", "system", None]


def random_session(rng, tracks):
    evs = []
    for _ in range(120):
        t = rng.choice(tracks)
        vs = rng.choice(SOURCES)
        base = {"track_id": t, "vibe": rng.randint(0, 100), "vibe_source": vs,
                "dj_mode": rng.random() < 0.5, "session_id": "s", "tz_offset_min": 60}
        kind = rng.random()
        if kind < 0.35:
            evs.append({**base, "type": "play_start", "source": rng.choice(["dj", "pick", "search"])})
        elif kind < 0.75:
            listened = rng.choice([None, rng.randint(0, 240_000), 89_999, 90_000])
            ev = {**base, "type": "play_end", "reason": rng.choice(REASONS),
                  "trigger": rng.choice(TRIGGERS), "position_ms": rng.randint(0, 240_000),
                  "duration_ms": 240_000}
            if listened is not None:
                ev["listened_ms"] = listened
            evs.append(ev)
        else:
            evs.append({**base, "type": rng.choice(["pause", "resume", "seek", "queue_add"]),
                        "position_ms": rng.randint(0, 1000)})
    return evs


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_inline_rollup_equals_rebuild(app_module, make_user, make_tracks, db, seed):
    rng = random.Random(seed)
    uid, _ = make_user()
    tracks = make_tracks(uid, [None] * 15)
    evs = random_session(rng, tracks[:12])      # 3 tracks never touched
    for i in range(0, len(evs), 50):
        app_module._record_track_events(uid, evs[i:i + 50], "tester")

    conn = rebuild._connect(False)
    want, have = rebuild._expected(conn, None), rebuild._existing(conn, None)
    conn.close()
    assert set(want) == set(have), "rebuild and inline disagree on which rows exist"
    for key, w in want.items():
        h = have[key]
        for col, _ in rebuild._ALL:
            if col in ("first_played_at", "last_played", "last_skipped_at"):
                # inline stamps the batch's receive time, rebuild the event's
                # server_ts: same second here, but compare presence to be safe
                assert (w[col] is None) == (h[col] is None), (key, col)
            else:
                assert (w[col] or 0) == (h[col] or 0), (key, col, w[col], h[col])
