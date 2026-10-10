"""backend/dj_replay.py — the pure event-log -> query-vector replay."""
import numpy as np
import pytest

import dj_replay as R

DIM = 16


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


@pytest.fixture
def space():
    """Orthogonal-ish directions so 'moved toward x' is easy to measure."""
    rng = np.random.default_rng(3)
    vecs = {i: unit(rng.normal(size=DIM)) for i in range(1, 40)}
    mu = np.zeros(DIM)
    return vecs, mu


def ev(tid, action, ratio=None):
    return {"id": tid, "action": action, "played_ratio": ratio}


def cos(a, b):
    return float(unit(a) @ unit(b))


# ---------------------------------------------------------------- parse_events

def test_parse_events_drops_garbage_and_keeps_types():
    raw = [
        {"id": 5, "action": "completed", "played_ratio": 1},
        {"id": "5", "action": "skipped", "played_ratio": 0.2},
        {"id": "abc", "action": "searched"},
        {"id": None, "action": "completed"},       # no id
        {"id": 7, "action": "teleported"},         # unknown action
        "not a dict",
        {"id": 8, "action": "next", "played_ratio": 7},     # clamped to 1
        {"id": 9, "action": "next", "played_ratio": "x"},   # unparseable -> None
    ]
    out = R.parse_events(raw)
    assert [e["id"] for e in out] == [5, "5", "abc", 8, 9]
    assert isinstance(out[0]["id"], int) and isinstance(out[1]["id"], str)
    assert out[3]["played_ratio"] == 1.0
    assert out[4]["played_ratio"] is None


def test_parse_events_keeps_only_the_newest_max_events():
    raw = [{"id": i, "action": "completed", "played_ratio": 1} for i in range(100)]
    out = R.parse_events(raw)
    assert len(out) == R.MAX_EVENTS
    assert out[0]["id"] == 100 - R.MAX_EVENTS and out[-1]["id"] == 99


# ---------------------------------------------------------------------- replay

def test_no_events_and_no_seed_gives_no_query(space):
    vecs, mu = space
    res = R.replay([], vecs, mu, seed_vec=None)
    assert res.query is None


def test_no_positive_events_starts_from_the_seed(space):
    vecs, mu = space
    res = R.replay([], vecs, mu, seed_vec=vecs[1])
    assert res.from_seed
    assert cos(res.query, vecs[1]) > 0.999


def test_search_jumps_most_of_the_way(space):
    vecs, mu = space
    log = [ev(1, "completed", 1), ev(2, "completed", 1), ev(3, "searched")]
    q = R.replay(log, vecs, mu).query
    # 0.8 of the way to track 3: much closer to it than to the earlier vibe
    assert cos(q, vecs[3]) > 0.9
    assert cos(q, vecs[3]) > cos(q, vecs[1]) + 0.5


def test_finishing_a_similar_track_barely_moves_the_vibe(space):
    vecs, mu = space
    near = unit(vecs[1] + 0.05 * vecs[2])
    vecs = {**vecs, 99: near}
    before = R.replay([ev(1, "completed", 1)], vecs, mu).query
    after = R.replay([ev(1, "completed", 1), ev(99, "completed", 1)], vecs, mu).query
    assert cos(before, after) > 0.99


def test_skips_build_an_avoid_direction_not_the_vibe(space):
    vecs, mu = space
    base = R.replay([ev(1, "completed", 1)], vecs, mu)
    skipped = R.replay([ev(1, "completed", 1), ev(5, "skipped", 0.1)], vecs, mu)
    assert skipped.avoid > 0
    # the query moves AWAY from the skipped track, not toward it
    assert cos(skipped.query, vecs[5]) < cos(base.query, vecs[5])


def test_avoid_term_is_bounded_however_many_skips(space):
    vecs, mu = space
    log = [ev(1, "completed", 1)] + [ev(i, "skipped", 0.0) for i in range(2, 30)]
    res = R.replay(log, vecs, mu)
    assert res.avoid <= 1.0
    # the vibe still contributes: the query keeps a real component along S
    assert cos(res.query, vecs[1]) > 0.3


def test_a_skip_run_primes_the_next_finished_track(space):
    vecs, mu = space
    calm = [ev(1, "completed", 1), ev(9, "completed", 1)]
    after_skips = [ev(1, "completed", 1), ev(2, "skipped", 0.1), ev(3, "skipped", 0.1),
                   ev(4, "skipped", 0.1), ev(9, "completed", 1)]
    q_calm = R.replay(calm, vecs, mu).query
    q_primed = R.replay(after_skips, vecs, mu).query
    assert cos(q_primed, vecs[9]) > cos(q_calm, vecs[9])


def test_next_does_not_end_a_skip_streak(space):
    vecs, mu = space
    log = [ev(1, "completed", 1), ev(2, "skipped", 0.1), ev(3, "next", 0.6), ev(4, "skipped", 0.1)]
    assert R.replay(log, vecs, mu).streak == 2


def test_a_positive_ends_the_streak(space):
    vecs, mu = space
    log = [ev(2, "skipped", 0.1), ev(3, "skipped", 0.1), ev(1, "completed", 1)]
    assert R.replay(log, vecs, mu).streak == 0


def test_a_skip_without_a_vector_still_counts_toward_the_streak(space):
    vecs, mu = space
    log = [ev(1, "completed", 1), ev(12345, "skipped", 0.1)]   # 12345 has no vector
    res = R.replay(log, vecs, mu)
    assert res.streak == 1 and res.avoid == 0


def test_repeating_a_track_cannot_run_away(space):
    vecs, mu = space
    log = [ev(1, "completed", 1)] * 25
    q = R.replay(log, vecs, mu).query
    assert abs(np.linalg.norm(q) - 1) < 1e-9
    assert cos(q, vecs[1]) > 0.999


def test_centring_is_applied(space):
    vecs, _ = space
    shared = unit(np.ones(DIM))
    shifted = {k: unit(v + 3 * shared) for k, v in vecs.items()}
    mu = np.mean([unit(v) for v in shifted.values()], axis=0)
    q = R.replay([ev(1, "searched")], shifted, mu).query
    # in centred space the query is the track's own deviation from the mean,
    # which is far from the shared direction everything sits in
    assert abs(float(q @ shared)) < 0.6


def test_library_mean():
    m = R.library_mean([np.array([2.0, 0.0]), np.array([0.0, 5.0])])
    assert np.allclose(m, [0.5, 0.5])
    assert R.library_mean([]) is None
