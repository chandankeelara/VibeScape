"""A session language re-rank for the DJ -- MEASURED AND NOT SHIPPED.

Kept so scripts/dj_eval/replay_bench.py can reproduce the comparison that
retired it. It was backend/dj_language.py in PR #21 until 2026-10-19: a pure
re-rank run after the candidate fetch on both ranking paths. The vector did
better without it: with the language block at 0.40 of the fused recipe and a
stronger skip term in dj_replay, real-user finished-vs-skipped AUC was 0.747
against this re-rank's 0.688 (+0.061, 95% CI +0.035..+0.084), listener
language@10 0.952 (this: 0.981), and three Kannada skips cleared Kannada
from the top 10 (this: 0.15 left). See the PR for the tables.

    s(l)        = sum over the session's tracks in language l of
                  value(track) * 0.5 ** (age / HALF_LIFE)
    value       = +1 finished / searched / queued / picked, +NEXT_CREDIT for
                  'next', -SKIP_PENALTY for a skip (a track's last verdict)
    age         = how many tracks the session has heard since (0 = latest)
    affinity(c) = max(-1, s(lang_c))                                if s(lang_c) < 0
                = min(1, share(lang_c) + sum CLOSE(lang_c, l) * share(l)) otherwise
                  where share = the positive scores as raw shares
    final(c)    = cos(c) + WEIGHT * window * affinity(c)    affinity >= 0
                = cos(c) + DEMOTE * window * affinity(c)    affinity <  0
    window      = cos(#1) - cos(#limit), in the cosine order

Only this session counts: the language follows what the listener wants
now, not their library. Recency is the decay, so a Kannada session that
switches to Hindi follows Hindi within a few songs. Skips count against
their language, and a language whose score goes negative is demoted below
every neutral one. The seed's language is used exactly as dj_replay uses
the seed vector: only when the log holds no positive event at all (no
search, queue, pick, finish or 'next'); then it counts as one accepted
song. Any positive event and the seed has no influence.
Untagged tracks are a language of their own ('' below): an instrumental
session asks for more instrumentals.

Why it is shaped like this, and the constants (measured 2026-10-19 on a
copy of prod_20261019T024426Z with scripts/dj_eval/replay_bench.py, in the
fused_v2 space; PR "DJ language" has the tables):

  * Raw shares, not centred ones. The DJ centres vectors on the library
    mean, which turns the vector's same-language bonus into roughly
    1 - 2p + sum p^2: a rare language pulls far harder than English, and a
    mixed session drifts toward its rarer language. A share is a share.
  * The bonus is relative to the window, like the recency re-rank's lambda,
    so it means the same on a centred replay query (cosines near 0.3) and a
    raw one (near 0.9).
  * Skips count. Real users skip by language: on 3,291 real DJ / autoplay
    starts from track_events, finished-vs-skipped AUC is 0.689 with skips
    counted and 0.608 without (+0.080, 95% block-bootstrap CI
    +0.058..+0.105). Dropping the library prior that PR #21 first had
    changed nothing measurable (+0.001, CI -0.003..+0.006).
  * HALF_LIFE = 3 songs, SKIP_PENALTY = 1.25, DEMOTE = 12, from a grid of
    half-life 1.5-5, penalty 0-3 and demotion 3-24. Real-user AUC is flat
    in the half-life (within 0.004 between 2 and 3 songs) and rises with
    the penalty and the demotion; the listener sessions, whose skips are
    mostly about mood inside one language, pay for both (language@10 at
    demotion 12: 0.992 at penalty 1, 0.981 at 1.25, 0.955 at 1.5). 1.25 is where two Kannada skips after
    three Kannada songs turn the language clearly negative while one stray
    skip after several accepts never does. On synthetic sessions: one skip
    keeps Kannada at 1.00 of the top 10, two cut it to 0.69, three to 0.15;
    after five Kannada songs, one Hindi song gives Hindi 0.37 of the top 10
    and two give 1.00.
  * The demotion only works if the pool holds something else: a skipped
    language usually fills the query's neighbourhood, so the endpoint
    fetches its deepest pool (DJ_RECENCY_POOL_MAX, 300) while demoting().
    Fully clearing Kannada after two skips needed a 600-track pool in the
    benchmark; that was not taken (Turso streams every pool row).
  * WEIGHT = 3 is unchanged from the first version (3, 4 and 6 tied on
    real users).
"""
from __future__ import annotations

WEIGHT = 3.0          # a full-affinity track gains three windows
HALF_LIFE = 3.0       # songs: a track heard 3 songs ago counts half
SKIP_PENALTY = 1.25   # a skip is worth this many accepted songs, negative
NEXT_CREDIT = 0.5     # 'next' (45-85% played) is a weak accept
DEMOTE = 12.0         # a fully skipped language loses this many windows

_VALUE = {"completed": 1.0, "searched": 1.0, "queued": 1.0, "picked": 1.0,
          "next": NEXT_CREDIT}

# Partial credit between languages a listener of one plausibly also wants:
# Hindustani (Hindi, Urdu, Punjabi) and South Indian film music. Optional;
# pass close={} to turn it off. Positive only: a skip demotes its own
# language, never its neighbours.
CLOSE_GROUPS = ((("hi", "ur", "pa"), 0.5), (("kn", "te", "ta", "ml"), 0.3))


def _build_close(groups) -> dict:
    out: dict = {}
    for langs, credit in groups:
        for a in langs:
            for b in langs:
                if a != b:
                    out[(a, b)] = credit
    return out


CLOSE = _build_close(CLOSE_GROUPS)


def lang_key(language) -> str:
    """'' for an untagged track, else the lower-cased code."""
    return str(language or "").strip().lower()


def session_scores(events, lang_of, seed=None, half_life: float = HALF_LIFE,
                   skip_penalty: float = SKIP_PENALTY) -> dict:
    """{language: signed, decayed score} over a parsed, resolved event log
    (oldest first). Each track counts once, by its last verdict: a search
    then a skip is a skip, a search then a finish is one accept. Tracks
    missing from `lang_of` are ignored."""
    last: dict = {}                       # tid -> (index of last event, value)
    positive = False                      # dj_replay's "S is not None"
    for i, e in enumerate(events or []):
        tid, a = e.get("id"), e.get("action")
        if tid not in lang_of:
            continue
        positive = positive or a in _VALUE
        if a == "skipped":
            last[tid] = (i, -skip_penalty)
        elif a in _VALUE:
            prev = last.get(tid)
            # An intent (search / queue / pick) and its own finish are one
            # accept, not two; a later outcome replaces an earlier one.
            v = _VALUE[a] if a in ("completed", "next") or prev is None else max(prev[1], _VALUE[a])
            last[tid] = (i, v)
    out: dict = {}
    order = sorted(last.items(), key=lambda kv: -kv[1][0])
    for age, (tid, (_i, v)) in enumerate(order):
        k = lang_key(lang_of[tid])
        out[k] = out.get(k, 0.0) + v * 0.5 ** (age / half_life)
    if not positive and seed in lang_of:
        k = lang_key(lang_of[seed])
        out[k] = out.get(k, 0.0) + 1.0
    return out


def shares(scores: dict) -> dict:
    """The positive scores as fractions of their sum."""
    total = float(sum(v for v in scores.values() if v > 0))
    if total <= 0:
        return {}
    return {lang_key(k): v / total for k, v in scores.items() if v > 0}


def demoting(scores: dict) -> bool:
    """True while the session holds a language below zero. The endpoint
    then fetches its deepest pool: a skipped language usually fills the
    cosine neighbourhood of the query, and demoting it is only useful if
    there is something else in the pool to promote."""
    return any(v < 0 for v in scores.values())


def affinity(language, scores: dict, close: dict = CLOSE, _shares=None) -> float:
    """In [-1, 1]: negative only for a language the session is skipping."""
    k = lang_key(language)
    s = scores.get(k, 0.0)
    if s < 0:
        return max(-1.0, s)
    sh = shares(scores) if _shares is None else _shares
    a = sh.get(k, 0.0)
    if k:
        for other, share in sh.items():
            if other != k:
                a += close.get((k, other), 0.0) * share
    return min(1.0, a)


def rerank(cands, scores: dict, limit: int, weight: float = WEIGHT,
           close: dict = CLOSE, demote: float = DEMOTE) -> list:
    """Re-order a pool by cosine plus the language term.

    `cands` is [(cosine, language), ...] sorted best-first; `scores` is
    session_scores(). Returns [(index into cands, adjusted score), ...] for
    the WHOLE pool, best first, so the recency re-rank can still pull from
    below `limit`. The input order comes back untouched (adjusted = cosine)
    when there is nothing to act on: no scores, no weight, fewer than two
    candidates, or a flat window. Ties keep their cosine order.
    """
    n = len(cands)
    ident = [(i, float(c[0])) for i, c in enumerate(cands)]
    if n < 2 or weight <= 0 or not any(scores.values()):
        return ident
    window = float(cands[0][0]) - float(cands[min(limit, n) - 1][0])
    if window <= 0:
        return ident
    sh = shares(scores)
    scored = []
    for i, (s, lang) in enumerate(cands):
        a = affinity(lang, scores, close, sh)
        scored.append((i, float(s) + (weight if a >= 0 else demote) * window * a))
    scored.sort(key=lambda e: -e[1])
    return scored
