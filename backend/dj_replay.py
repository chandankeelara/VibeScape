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

  query = normalize(S - A)
  A     = min(NEG_WEIGHT * m, AVOID_CAP) * N', shrunk if need be so that the
          query stays within atan(AVOID_CAP) of S (56 degrees at 1.5)
  N'    = N with its language block's component along S's language block
          removed, renormalised

The avoid term may push away from anything the rejected tracks are --
their sound, their energy, a language the taste does not hold -- except
the language the taste itself holds, and it can never turn the query
further from the taste than the bound: see "The skip overshoot" below.

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

The skip overshoot (2026-10-20). With N subtracted whole and up to twice
|S|, five listener sessions on the deployed DJ (revision 00028) went wrong
once they skipped several times: two or three mood skips (Kannada love
songs in a Kannada mass session) threw two Kannada sessions out of Kannada,
a workout session that skipped 14 of 26 wandered through four languages,
film BGM drowned in vocal songs. Two changes. The avoid term is bounded by
an angle (AVOID_CAP): the query keeps cos >= 0.55 with S however many
skips. And the language the taste holds is not avoided (AVOID_PERP
"lang"). A first version removed ALL of N's component along S; that froze
a session whose one finished song was the wrong vibe -- the skips were the
taste, so every skip cancelled (indian-heartbreak-9c8502: Badtameez Dil,
then fifteen party skips and the DJ never moved). Protecting only the
language block keeps the skip working against the vibe.

On scripts/dj_eval/replay_bench.py --final3 (a copy of
prod_20261019T024426Z, with fused_vector's instrumental slot): the five
sessions at 0.94-1.00 of the persona's language (film BGM: 0.94
instrumentals, from 0.46); three mood skips inside Kannada keep Kannada
(0.00 -> 1.00); six mixed English skips keep English (0.00 -> 1.00); a
session that moved to Hindi still drops Kannada on two Kannada skips; the
off-target seed above leaves party after three skips (party 1.00 -> 0.00
of the top 10) and stays Hindi (1.00). Real-user AUC 0.746 -> 0.707 (95%
block bootstrap -0.064..-0.014); the all-of-S version cost twice that
(0.642). Real sessions are short, and a run of skips there mostly does mean
"not this"; the listener sessions say that reading is wrong once a
listener skips for mood. Cap 2.0 gained 0.006 AUC and lost film BGM (0.86);
1.0 lost 0.013. PR #21's "three Kannada skips after three Kannada songs
clear Kannada" no longer holds, and cannot together with "three mood skips
keep the language".

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
# What the avoid term may not push against. "lang": N's language block loses
# its component along S's language block (the language the taste and the
# skips share stays), the audio and scalar blocks are untouched. "full": the
# whole of N loses its component along S (PR #23's first version; it froze a
# session whose only accepted song was the wrong vibe). False: neither.
AVOID_PERP = "lang"
AVOID_PERP_RENORM = True # ... renormalised to unit length
# The language block, ingest_pipeline/fused_vector.py: MERT (768) + scalars
# (9), then 64 slots. It sits at the same place in fused_v2 (841 wide) and
# fused_v3 (865: a genre block follows it), and "lang" applies to vectors of
# those widths only (tests/backend/test_dj_replay.py checks both against the
# recipe). Only the language block is protected: fused_v3's genre block is
# left to the avoid term by decision (2026-10-23), so skips push against the
# skipped songs' genres even when the taste holds them, as they push against
# their sound. Measured only as part of replay_bench --w-genre, not on its
# own (backlog 15.4).
LANG_BLOCK = (777, 841)
LANG_BLOCK_WIDTHS = (841, 865)
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
           avoid_perp: Optional[bool] = None, avoid_cap=...,
           song_keys: Optional[dict] = None) -> ReplayResult:
    """Replay `events` (already resolved: e["id"] is an internal int) over
    `vecs` {track_id: raw vector}. Tracks with no vector still count toward
    the skip streak but contribute no direction. neg_weight / m_step / m_max
    / avoid_perp / avoid_cap default to the module constants; the app passes
    the pre-fused_v2 values when it has to rank fused_v1 vectors
    (services/vectors.py _fused_v2_ready, services/dj.py _similar_dj). avoid_cap=None means no cap.

    song_keys {track_id: same_song.song_key or None}: a skip of a track that
    is another release of a song an EARLIER event already had (album, then
    single) is left out entirely -- no avoid term, no skip streak. It says
    "not again", not "not this vibe": in the Kannada vibe tests the skip of
    a repeated Belakina Kavithe started a four-skip slide into mass songs."""
    neg_weight = NEG_WEIGHT if neg_weight is None else neg_weight
    m_step = M_STEP if m_step is None else m_step
    m_max = M_MAX if m_max is None else m_max
    S = N = None
    m, k, primed = 0.0, 0, 0.0
    first_of: dict = {}                    # song key -> the first track id seen with it
    for e in events:
        a = e["action"]
        if song_keys:
            sk = song_keys.get(e["id"])
            if sk is not None:
                first = first_of.setdefault(sk, e["id"])
                if a == "skipped" and first != e["id"]:
                    continue
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


def _within(S: np.ndarray, A: np.ndarray, cap) -> np.ndarray:
    """A, shrunk if need be so that S - A stays within atan(cap) of S: the
    largest t <= 1 with cos(S - t*A, S) >= 1 / sqrt(1 + cap^2). With an A
    orthogonal to S and |A| <= cap that is A itself; with an A that leans
    along S (perp "lang": the audio of a skipped vibe is also the taste's)
    a magnitude cap alone could reverse the taste."""
    if cap is None:
        return A
    floor = 1.0 / np.sqrt(1.0 + cap * cap)

    def cos(t):
        v = S - t * A
        n = float(np.linalg.norm(v))
        return float(v @ S) / n if n > 1e-12 else -1.0
    if cos(1.0) >= floor:
        return A
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cos(mid) >= floor else (lo, mid)
    return lo * A


def _avoid(S: np.ndarray, N: np.ndarray, coef: float, perp, cap) -> np.ndarray:
    """The vector subtracted from S: coef * N, with coef capped at `cap`
    unless that is None, and N first stripped of what it shares with S --
    its language-block component along S's language block (perp "lang"),
    or its whole component along S (perp "full" / True)."""
    return _within(S, _avoid_raw(S, N, coef, perp, cap), cap)


def _avoid_raw(S, N, coef, perp, cap):
    if cap is not None:
        coef = min(coef, cap)
    if perp == "lang":
        a, b = LANG_BLOCK
        if S.shape[0] not in LANG_BLOCK_WIDTHS:
            return coef * N
        sl = S[a:b]
        n_sl = float(np.linalg.norm(sl))
        if n_sl < 1e-9:
            return coef * N
        P = N.copy()
        P[a:b] = N[a:b] - float(N[a:b] @ sl) / n_sl ** 2 * sl
        n = float(np.linalg.norm(P))
        if n < 1e-9:
            return np.zeros_like(S)
        return coef * (P / n if AVOID_PERP_RENORM else P)
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
