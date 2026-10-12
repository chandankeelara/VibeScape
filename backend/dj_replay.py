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

  query = normalize(S - NEG_WEIGHT * m * N)

Everything happens after subtracting the library mean `mu`. Fused vectors
are strongly anisotropic — measured 2026-10-09 on the local DB (3,783
tracks): |mean of unit vectors| = 0.88, random-pair cosine median 0.78.
Raw `1 - cos` barely moves, and an unnormalised negative sum swamped S
7-11x in an offline replay. Centred, random pairs sit at cosine ~0.

The centred query works with the existing cosine SQL unchanged: stored
vectors are unit length (verified, same date), so ranking by
cos(candidate, q) equals ranking by the centred dot product.

Offline evidence for the constants (.claude/tmp/dj-compare/harness.py,
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
NEG_WEIGHT = 0.6         # |avoid term| can never exceed 0.6 of |S|

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
M_STEP = 0.25            # confidence gained per skip: 0.25 * depth * amp
M_FADE = 0.7             # confidence kept after each positive

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
           seed_vec: Optional[np.ndarray] = None) -> ReplayResult:
    """Replay `events` (already resolved: e["id"] is an internal int) over
    `vecs` {track_id: raw vector}. Tracks with no vector still count toward
    the skip streak but contribute no direction."""
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
            m = min(1.0, m + M_STEP * (1.0 - (ratio or 0.0)) * amp)
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
    q = S if N is None else _unit(S - NEG_WEIGHT * m * N)
    return ReplayResult(q, from_seed, k, m)


def library_mean(vectors) -> Optional[np.ndarray]:
    """Mean of unit vectors. `vectors` is any iterable of raw vectors."""
    acc, n = None, 0
    for v in vectors:
        u = _unit(np.asarray(v, dtype=np.float64))
        acc = u if acc is None else acc + u
        n += 1
    return None if acc is None else acc / n
