"""DJ language re-rank: which languages this session wants, and how much.

Pure (no DB, no clock, no numpy), like dj_replay.py, so the endpoint and the
offline benchmark (scripts/dj_eval/replay_bench.py) run the same code. It runs
after the candidate fetch and before the recency re-rank, on BOTH ranking
paths: the numpy path (SQLite) and the vector_distance_cos path (Turso) hand
it the same (cosine, language) pool, so the order cannot depend on which
database answered.

    mix(l)      = (n_session(l) + PRIOR_WEIGHT * prior(l)) / (n_session + PRIOR_WEIGHT)
    affinity(c) = min(1, mix(lang_c) + sum over l != lang_c of CLOSE(lang_c, l) * mix(l))
    final(c)    = cos(c) + WEIGHT * window * affinity(c)
    window      = cos(#1) - cos(#limit), in the cosine order

n_session counts the distinct tracks the replayed log accepted (searched,
queued, picked, completed or 'next'); a skip says nothing about language, so
it is not counted. prior is the user's library by language. Untagged tracks
are a language of their own here ('' below): an instrumental session asks
for more instrumentals, a Kannada one gives them no credit.

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
  * WEIGHT = 3. On 3,291 real DJ / autoplay starts from track_events,
    finished-vs-skipped AUC went 0.598 (no re-rank) -> 0.606, +0.008, 95%
    block-bootstrap CI +0.002..+0.015; WEIGHT 4 and 6 scored the same
    (0.607, 0.609), so 3 is the smallest weight that gets the gain. On
    single-seed language groups precision@10 went 0.77 -> 0.93 and on
    language x energy groups 0.656 -> 0.688, so the language it buys is not
    paid for in energy fit.
  * PRIOR_WEIGHT = 1. The prior is how a multilingual listener is offered a
    language not played this session. 0.5, 1 and 2 scored the same on real
    users (AUC within 0.001); the eval users' "library" is the whole
    catalogue, so their groups favour a small prior for no real reason.
    Measured, the prior hardly reaches the top 10 at any weight: languages
    making up >= 5% of a real user's library but not yet accepted in the
    session were 1.6% of the top 10 without the re-rank and 0.3% with it.
    Real users finished same-language picks more, and the re-rank follows.
    Library rather than user_track_stats: positive terms may read only the
    u_ columns (backend/CLAUDE.md), and those hold 0-5 completions for three
    of the four real users.
  * CLOSE made no measurable difference (every metric within 0.002). It is
    kept because it only ever adds credit between languages a listener of
    one plausibly wants, and the data cannot see it either way yet.
"""
from __future__ import annotations

WEIGHT = 3.0          # a full-affinity track gains three windows
PRIOR_WEIGHT = 1.0    # the library counts as one session track
POSITIVE = frozenset({"searched", "queued", "picked", "completed", "next"})

# Partial credit between languages a listener of one plausibly also wants:
# Hindustani (Hindi, Urdu, Punjabi) and South Indian film music. Optional;
# pass close={} to turn it off.
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


def session_languages(events, lang_of) -> dict:
    """{language: accepted tracks} over a parsed, resolved event log.
    A track counts once, however many positive events it has."""
    seen, out = set(), {}
    for e in events or []:
        tid = e.get("id")
        if e.get("action") not in POSITIVE or tid in seen or tid not in lang_of:
            continue
        seen.add(tid)
        k = lang_key(lang_of[tid])
        out[k] = out.get(k, 0) + 1
    return out


def shares(counts: dict) -> dict:
    total = float(sum(v for v in counts.values() if v > 0))
    if total <= 0:
        return {}
    return {lang_key(k): v / total for k, v in counts.items() if v > 0}


def language_mix(session_counts: dict, prior: dict,
                 prior_weight: float = PRIOR_WEIGHT) -> dict:
    """Blend the session's accepted languages with the prior shares."""
    n = float(sum(session_counts.values()))
    p = shares(prior)
    w = prior_weight if p else 0.0
    if n + w <= 0:
        return {}
    out: dict = {}
    for k, v in session_counts.items():
        out[lang_key(k)] = out.get(lang_key(k), 0.0) + v
    for k, v in p.items():
        out[k] = out.get(k, 0.0) + w * v
    return {k: v / (n + w) for k, v in out.items()}


def affinity(language, mix: dict, close: dict = CLOSE) -> float:
    k = lang_key(language)
    a = mix.get(k, 0.0)
    if k:
        for other, share in mix.items():
            if other != k:
                a += close.get((k, other), 0.0) * share
    return min(1.0, a)


def rerank(cands, mix: dict, limit: int, weight: float = WEIGHT,
           close: dict = CLOSE) -> list:
    """Re-order a pool by cosine plus the language term.

    `cands` is [(cosine, language), ...] sorted best-first. Returns
    [(index into cands, adjusted score), ...] for the WHOLE pool, best first,
    so the recency re-rank can still pull from below `limit`. The input
    order comes back untouched (adjusted = cosine) when there is nothing to
    act on: no mix, no weight, fewer than two candidates, or a flat window.
    Ties keep their cosine order.
    """
    n = len(cands)
    ident = [(i, float(c[0])) for i, c in enumerate(cands)]
    if n < 2 or weight <= 0 or not mix:
        return ident
    window = float(cands[0][0]) - float(cands[min(limit, n) - 1][0])
    if window <= 0:
        return ident
    lam = weight * window
    scored = [(i, float(s) + lam * affinity(lang, mix, close))
              for i, (s, lang) in enumerate(cands)]
    scored.sort(key=lambda e: -e[1])
    return scored
