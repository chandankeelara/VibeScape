"""DJ session replay: the client's recent event log -> one query vector.

Pure (numpy only, no DB, no clock), so the offline replay harness and the
endpoint run the same code.

The client sends raw facts, oldest first: {id, action, played_ratio, ts}.
All weighting lives here. Two pieces of state are rebuilt on every request:

  S  "where the vibe is now" — an EMA over positives whose learning rate
     adapts: a search jumps, a queue-add / pick moves strongly, a finished
     track moves in proportion to how unlike S it is, a run of skips primes
     the next positive to move further.
  N  "what is being rejected" — an EMA direction over skips, with a bounded
     confidence m in [0, 1] that grows with each skip (faster in a streak)
     and fades on every positive.

  query = normalize(S - min(NEG_WEIGHT * m, AVOID_CAP) * N_perp)
  N_perp = N without its component along S, renormalised

The avoid term only pushes away from what the rejected tracks have that the
taste does not, and it can never turn the query more than atan(AVOID_CAP)
away from S (56 degrees at 1.5): see "The skip overshoot" below.

Everything happens after subtracting the library mean `mu`. Fused vectors
are strongly anisotropic — measured 2026-10-09 on the local DB (3,783
tracks): |mean of unit vectors| = 0.88, random-pair cosine median 0.78.
Raw `1 - cos` barely moves, and an unnormalised negative sum swamped S
7-11x in an offline replay. Centred, random pairs sit at cosine ~0.

The centred query works with the existing cosine SQL unchanged: stored
vectors are unit length (verified, same date), so ranking by
cos(candidate, q) equals ranking by the centred dot product.

The skip term is the DJ's main lever, language included (2026-10-19,
scripts/dj_eval/replay_bench.py on a copy of prod_20261019T024426Z, fused_v2
with the language block at 0.40). NEG_WEIGHT 0.6 -> 1.0, M_STEP 0.25 -> 0.5
and the cap on m 1.0 -> 2.0 took real-user finished-vs-skipped AUC from 0.612
to 0.747 over 3,291 DJ / autoplay starts; the cap alone, 1.5 -> 2.0, was
+0.013 (block bootstrap 95% CI +0.005..+0.022). Three skips of one language
then clear it from the top 10, one stray skip after three accepts does not
move it, and a mixed run of skips (three tracks that sound unlike each
other) takes their neighbourhoods from 0.31 of the top 10 to under 0.01. The
cost: the avoid term may now outweigh S (up to twice), so a long skip run
steers mostly by what was rejected. Stronger still (NEG_WEIGHT 1.5) lost
language@10 (0.878) for no AUC. A nearest-skip penalty (max cosine to any
skipped track) was measured too and lost to this on real-user AUC
(0.645 at best).

The skip overshoot (2026-10-20). With that, N subtracted whole and up to
twice |S|, five listener sessions on the deployed DJ (revision 00028) went
wrong once they skipped several times: two or three mood skips (Kannada love
songs in a Kannada mass session) threw two Kannada sessions out of Kannada,
a workout session that skipped 14 of 26 wandered through four languages,
film BGM drowned in vocal songs. N is an average of the skipped tracks, and
what those share with the taste -- the language, mostly -- is the part that
must not be avoided. So N loses its component along S (AVOID_PERP) and the
avoid weight is capped (AVOID_CAP). On scripts/dj_eval/replay_bench.py
--final2 (a copy of prod_20261019T024426Z, with fused_vector's instrumental
slot): the five sessions back to 0.96-1.00 of the persona's language (film
BGM: 0.91 instrumentals, from 0.46), three mood skips inside Kannada keep
Kannada (0.00 -> 0.98), six mixed English skips keep English (0.00 -> 0.99),
and a session that moved to Hindi still drops Kannada on two Kannada skips
(0.00). The cost is real-user AUC: 0.748 -> 0.643 (95% block bootstrap of
the difference -0.142..-0.066), still above fused_v1's 0.595. Real sessions are short, and a
run of skips there mostly does mean "not this language"; the listener
sessions say that reading is wrong once a listener skips for mood. The old
"three Kannada skips after three Kannada songs clear Kannada" no longer
holds, and cannot together with "three mood skips keep the language": the
two sessions differ only in what the skipped songs share with the taste.
Cap 1.5 over 1.0: +0.013 real-user AUC (CI +0.008..+0.018) with every check
passing; at 2.0 the checks start to fail (mood 0.96, film BGM 0.95).

Offline evidence for the original constants (.claude/tmp/dj-compare/harness.py,
local track_events 2026-10-02..09, 241 DJ-served tracks): finished-vs-
skipped AUC 0.83 vs 0.64 for the previous weighted-sum query; after a
search, 5.5 of the top 10 sit in the searched track's neighbourhood vs
1.4. The constants were set before that replay and not tuned on it —
treat them as starting values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

MAX_EVENTS = 30          # server-side cap; the client keeps 50, sends 30
NEG_WEIGHT = 1.0         # |avoid term| can never exceed NEG_WEIGHT * M_MAX = 2 x |S|

ETA_SEARCHED = 0.8       # "I want this, now"
ETA_QUEUED = 0.6         # queue-add or a pick off a list
ETA_PICKED = 0.6
ETA_FLOOR = 0.15         # a finished track like the current vibe
ETA_CEIL = 0.6           # a finished track unlike it
SURPRISE_GAIN = 0.8      # eta = FLOOR + GAIN * (1 - cos(S, x)), clamped
ETA_NEXT = 0.15          # 'next' (45-85% played) scales this by the ratio

STREAK_AMP_STEP = 0.5    # skip k in a row counts 1 + 0.5 * (k - 1) ...
STREAK_AMP_MAX = 3.0     # ... up to 3x
PRIME_BASE = 0.3         # after k skips the next positive moves at least
PRIME_STEP = 0.15        # PRIME_BASE + PRIME_STEP * k ...
PRIME_MAX = 0.8          # ... up to 0.8
N_BETA_STEP = 0.3        # N's own EMA rate: 0.3 * amp, capped
N_BETA_MAX = 0.8
M_STEP = 0.5             # confidence gained per skip: 0.5 * depth * amp
M_MAX = 2.0              # ... capped here
M_FADE = 0.7             # confidence kept after each positive
AVOID_PERP = True        # avoid only N's part orthogonal to S
AVOID_PERP_RENORM = True # ... renormalised to unit length
AVOID_CAP = 1.5          # cap on NEG_WEIGHT * m, in units of |S| (None: no cap)

POSITIVE = frozenset({"searched", "queued", "picked", "completed"})
KNOWN = POSITIVE | {"next", "skipped"}


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def parse_events(raw) -> list[dict]:
    """Keep well-formed events, oldest first, last MAX_EVENTS.

    Unknown actions are dropped rather than rejected: an older or newer
    client must never turn a recommendation request into a 4xx.
    Ids keep their type (int = internal tracks.id, str = spotify id).
    """
    out = []
    for e in raw or []:
        if not isinstance(e, dict):
            continue
        tid, action = e.get("id"), e.get("action")
        if tid is None or tid == "" or action not in KNOWN:
            continue
        try:
            ratio = float(e.get("played_ratio")) if e.get("played_ratio") is not None else None
        except (TypeError, ValueError):
            ratio = None
        if ratio is not None:
            ratio = max(0.0, min(1.0, ratio))
        out.append({"id": tid if isinstance(tid, int) else str(tid),
                    "action": action, "played_ratio": ratio})
    return out[-MAX_EVENTS:]


@dataclass
class ReplayResult:
    query: Optional[np.ndarray]   # unit vector in centred space, or None
    from_seed: bool               # no positive signal: S started at the seed
    streak: int                   # skips in a row at the end of the log
    avoid: float                  # final confidence m in N

    def explain(self) -> dict:
        return {"from_seed": self.from_seed, "skip_streak": self.streak,
                "avoid_strength": round(self.avoid, 4)}


def replay(events: list[dict], vecs: dict, mu: np.ndarray,
           seed_vec: Optional[np.ndarray] = None, *, neg_weight: Optional[float] = None,
           m_step: Optional[float] = None, m_max: Optional[float] = None,
           avoid_perp: Optional[bool] = None, avoid_cap=...) -> ReplayResult:
    """Replay `events` (already resolved: e["id"] is an internal int) over
    `vecs` {track_id: raw vector}. Tracks with no vector still count toward
    the skip streak but contribute no direction. neg_weight / m_step / m_max
    / avoid_perp / avoid_cap default to the module constants; the app passes
    the pre-fused_v2 values when it has to rank fused_v1 vectors
    (backend/app.py _fused_v2_ready). avoid_cap=None means no cap."""
    neg_weight = NEG_WEIGHT if neg_weight is None else neg_weight
    m_step = M_STEP if m_step is None else m_step
    m_max = M_MAX if m_max is None else m_max
    S = N = None
    m, k, primed = 0.0, 0, 0.0
    for e in events:
        a = e["action"]
        v = vecs.get(e["id"])
        x = _unit(_unit(v) - mu) if v is not None else None
        ratio = e.get("played_ratio")

        if a == "skipped":
            k += 1
            primed = min(PRIME_MAX, PRIME_BASE + PRIME_STEP * k)
            if x is None:
                continue
            amp = min(STREAK_AMP_MAX, 1.0 + STREAK_AMP_STEP * (k - 1))
            beta = min(N_BETA_MAX, N_BETA_STEP * amp)
            N = x if N is None else _unit((1 - beta) * N + beta * x)
            m = min(m_max, m + m_step * (1.0 - (ratio or 0.0)) * amp)
            continue

        if a == "next":
            # Weak positive. Deliberately does not end a skip streak.
            if x is None:
                continue
            if S is None:
                S = x
            else:
                eta = ETA_NEXT * (ratio if ratio is not None else 0.5)
                S = _unit((1 - eta) * S + eta * x)
            continue

        # searched / queued / picked / completed
        if x is not None:
            if S is None:
                S = x
            else:
                if a == "searched":
                    eta = ETA_SEARCHED
                elif a == "queued":
                    eta = ETA_QUEUED
                elif a == "picked":
                    eta = ETA_PICKED
                else:
                    surprise = 1.0 - float(S @ x)
                    eta = min(max(ETA_FLOOR + SURPRISE_GAIN * surprise, ETA_FLOOR), ETA_CEIL)
                    eta = max(eta, primed)
                S = _unit((1 - eta) * S + eta * x)
        k, primed, m = 0, 0.0, m * M_FADE

    from_seed = False
    if S is None:
        if seed_vec is None:
            return ReplayResult(None, True, k, m)
        S, from_seed = _unit(_unit(seed_vec) - mu), True
    perp = AVOID_PERP if avoid_perp is None else avoid_perp
    cap = AVOID_CAP if avoid_cap is ... else avoid_cap
    q = S if N is None else _unit(S - _avoid(S, N, neg_weight * m, perp, cap))
    return ReplayResult(q, from_seed, k, m)


def _avoid(S: np.ndarray, N: np.ndarray, coef: float, perp: bool, cap) -> np.ndarray:
    """The vector subtracted from S: coef * N, without N's component along
    S when `perp`, with coef capped at `cap` unless that is None."""
    if cap is not None:
        coef = min(coef, cap)
    if perp:
        P = N - float(N @ S) * S
        n = float(np.linalg.norm(P))
        if n < 1e-9:
            return np.zeros_like(S)
        N = P / n if AVOID_PERP_RENORM else P
    return coef * N


def library_mean(vectors) -> Optional[np.ndarray]:
    """Mean of unit vectors. `vectors` is any iterable of raw vectors."""
    acc, n = None, 0
    for v in vectors:
        u = _unit(np.asarray(v, dtype=np.float64))
        acc = u if acc is None else acc + u
        n += 1
    return None if acc is None else acc / n
