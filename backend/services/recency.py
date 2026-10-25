"""DJ recency re-ranking: the env knobs, the user_track_stats join, the pure
penalty and re-ranker, and the two per-request stats reads.

Moved out of app.py unchanged (refactor PR 2, 2026-10-25). The knobs are
module globals read at call time; tests patch them here
(`monkeypatch.setattr(recency, "_DJ_RECENCY_WEIGHT", 0.0)`), so nothing
outside this module may copy one with `from services.recency import ...`.
"""
import os
from datetime import datetime, timezone

from bootstrap import log


# ---------------------------------------------------------------------------
# DJ recency re-ranking.  Design + measurements: docs/dj-recency-plan.md.
#
#   final(c) = score(c) - lambda * P(c)
#   lambda   = W * (score[#1] - score[#limit])   in the UN-penalised order
#   P(c)     = P_play(c) + P_skip(c)             >= 0, always
#   P_play   = 2 ^ (-dt_played  / H_play)        0 if never played
#   P_skip   = B * 2 ^ (-dt_skipped / H_skip)    0 if never skipped
#   B        = 2.0 - 1.5 * bail_fraction         in [0.5, 2.0]; 1.0 if unknown
#
# Three properties this code must keep, in order of importance:
#
#   1. NO TERM MAY RAISE A SCORE.  P is a sum of non-negative terms and it is
#      subtracted.  A user with no user_track_stats rows gets P = 0 for every
#      candidate, so every score shifts by zero and the ordering is exactly
#      what it was before this feature existed.  The feature is inert until
#      there is listening history.  Do not add a term that can go negative.
#   2. lambda is RELATIVE to the width of the output window, never absolute.
#      The usable cosine signal across the top-8 is ~0.016 wide sitting at
#      ~0.95 (measured, 2026-10-02, 3,783 fused vectors).  An absolute lambda
#      is either inside the noise or annihilates the vector match, and it
#      silently becomes wrong on the 'mert' variant or on _similar_vibe, whose
#      "score" is a negated weighted-L1 distance on a completely different
#      scale.  CONSEQUENCE: lambda values are NOT comparable between the DJ
#      path and the vibe path.  A much larger lambda on the vibe path is
#      correct and is the whole point.
#   3. POSITIVE TERMS READ u_* ONLY; NEGATIVE TERMS MAY READ BOTH.  The
#      schema's u_/s_ prohibition is on averaging the recommender's own output
#      back in as stated preference -- that is a prohibition on a POSITIVE
#      feedback path.  Every term here is non-positive and every skip is a
#      human act regardless of who moved the slider, so summing u_ and s_ skip
#      counters is safe.  The rule is about the DIRECTION of the effect, not
#      about the column prefix.  If anyone ever adds a term that RAISES a
#      score, it must read u_* only.
#
# Columns read: last_played, last_skipped_at, (u_+s_)skip_count,
# (u_+s_)skip_position_ms_sum, tracks.duration_ms.  Nothing else.
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("[dj] %s=%r is not a number; using %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


# Defaults live in code so the behaviour is readable from source; the env
# override lets two Cloud Run revisions of the SAME image be compared without
# a rebuild.  Deliberately NOT request-body parameters: a client-settable
# ranking knob becomes a contract you can never change.
_DJ_RECENCY_HALFLIFE_H = _env_float("DJ_RECENCY_HALFLIFE_H", 72.0)
_DJ_SKIP_HALFLIFE_H = _env_float("DJ_SKIP_HALFLIFE_H", 168.0)
# A play counts as "qualified" (updates user_track_stats.last_played) iff
# play_end.reason == 'completed' OR position_ms >= DJ_QUALIFIED_PLAY_MS. 90 s
# is the common recommender-signal bar for a real listen (streaming services
# count a stream at ~30 s, but we want more than "it autoplayed and I didn't
# stop it"). A 2-minute skip still qualifies: having had the track playing
# for two minutes is a real listening event regardless of why the user moved
# on. See _accumulate_stats and scripts/rebuild_user_track_stats.py.
_DJ_QUALIFIED_PLAY_MS = _env_int("DJ_QUALIFIED_PLAY_MS", 90000)
# DJ_RECENCY_WEIGHT=0 is the complete kill switch. It short-circuits before
# the LEFT JOIN is added and before the pool is widened, so with it set the
# emitted SQL and the response are byte-for-byte what they were pre-feature.
_DJ_RECENCY_WEIGHT = _env_float("DJ_RECENCY_WEIGHT", 2.0)
# Candidate pool: POOL = min(POOL_MAX, max(POOL_BASE, 12 * limit)).
#
# THE POOL SIZE IS THE CEILING ON WHAT THIS FEATURE CAN EVER DO. The penalty
# only reorders WITHIN the retrieved pool -- a track that falls below the pool
# cut can never be promoted into the output, no matter how fresh it is and no
# matter how heavily everything above it is penalised. Widening the pool is
# the only way to raise that ceiling; raising W is not.
_DJ_RECENCY_POOL = _env_int("DJ_RECENCY_POOL", 150)
_DJ_RECENCY_POOL_MAX = _env_int("DJ_RECENCY_POOL_MAX", 300)
# The guest row is shared: auth_guest keys every guest on one users row with
# display_name='Guest', so all guests pool one listening history and one
# guest's skips suppress tracks for every other guest. Accepted by default --
# at demo volume the harm is bounded to "tracks any guest touched in the last
# ~2 weeks", and a less repetitive shared demo is arguably mildly good. Set
# DJ_RECENCY_SKIP_GUEST=1 to opt the guest row out. Watch for this if guest
# traffic ever reaches a few hundred plays a week: the symptom presents as
# "the demo recommends weird tracks" with no obvious cause.
_DJ_RECENCY_SKIP_GUEST = (
    (os.environ.get("DJ_RECENCY_SKIP_GUEST") or "").strip().lower()
    in ("1", "true", "yes", "on")
)

# Below this the output window is degenerate (every candidate identical, or
# fewer than two candidates) and dividing the penalty into it would
# manufacture an ordering out of nothing. Skip re-ranking instead.
_DJ_RECENCY_MIN_WINDOW = 1e-6

# The four stats columns, as they appear aliased on the candidate queries.
# Summing u_ and s_ is deliberate -- see property 3 above.
_UTS_SELECT = (
    "       s.last_played                             AS uts_last_played,\n"
    "       s.last_skipped_at                         AS uts_last_skipped_at,\n"
    "       COALESCE(s.u_skip_count, 0)\n"
    "     + COALESCE(s.s_skip_count, 0)               AS uts_skip_count,\n"
    "       COALESCE(s.u_skip_position_ms_sum, 0)\n"
    "     + COALESCE(s.s_skip_position_ms_sum, 0)     AS uts_skip_pos_sum"
)
# Joins on user_track_stats' primary key (user_id, track_id), so this is an
# index seek per candidate row inside the DB and adds ZERO round trips. A
# per-candidate stats query would be one Hrana POST each (backlog 2.2) --
# 150 HTTPS round trips per recommendation. That option is not on the table.
_UTS_JOIN = "LEFT JOIN user_track_stats s ON s.user_id = ut.user_id AND s.track_id = t.id"
# The same join for discovery mode, whose candidate query has no user_tracks
# alias to borrow the user from: the user id is a parameter, and it binds
# after anything in the SELECT list and before the WHERE's. With `_UTS_JOIN`
# there instead, the Turso SQL fails ("no such column: ut.user_id"), the DJ
# falls through to the numpy path, and on Turso that path reads every fused
# vector in the catalogue (~20 MB) on each request.
_UTS_JOIN_BY_USER = "LEFT JOIN user_track_stats s ON s.user_id = ? AND s.track_id = t.id"


def _dj_recency_enabled(display_name=None) -> bool:
    """The single gate. False => not one byte of this feature runs."""
    if _DJ_RECENCY_WEIGHT <= 0:
        return False
    if _DJ_RECENCY_SKIP_GUEST and (display_name or "") == "Guest":
        return False
    return True


def _dj_recency_pool(limit: int) -> int:
    return max(limit, min(_DJ_RECENCY_POOL_MAX, max(_DJ_RECENCY_POOL, 12 * limit)))


def _parse_sql_ts(value):
    """Parse a SQLite/libSQL timestamp into an aware UTC datetime, or None.

    Verified 2026-10-02: the only writer is _upsert_track_stats, which stores
    datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') -- directly
    comparable with datetime('now'), also UTC. The ISO/'Z' branches are
    defensive, for a future writer or a hand-edited row.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1].strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if "+" in s:
        s = s.split("+", 1)[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _recency_penalty(now, last_played, last_skipped_at,
                     skip_count=0, skip_pos_sum=0, duration_ms=None,
                     halflife_h=None):
    """Pure. Returns (P, explain) with P >= 0 ALWAYS. Takes no connection.

    `now` is an aware UTC datetime. The timestamps are whatever the DB handed
    back; they are parsed here.

    Never played means P_play = 0 -- maximally fresh. It is NOT imputed to a
    mid-range value and NOT imputed to "very old" (which happens to give the
    same answer today, for the wrong reason, and would stop being true the
    moment anyone adds a first_played_at term). That is what makes a user with
    no stats a no-op.

    dt is clamped at >= 0: a client clock running ahead of the server would
    otherwise give 2^(+k) and an unbounded SCORE-RAISING penalty. A future
    timestamp yields P = 1 (maximally recent), not P = 40.

    last_played and last_skipped_at are INDEPENDENT since the DJ_QUALIFIED_PLAY_MS
    change: a short skip updates only last_skipped_at, a qualifying listen
    (completed OR >=DJ_QUALIFIED_PLAY_MS, default 90s) updates only last_played,
    and a long-but-skipped play updates both. The two penalty terms are
    ADDITIVE rather than max(): a track skipped an hour ago after two minutes
    of play carries both and should be the most suppressed thing in the pool.
    """
    ex = {
        "p_play": 0.0,
        "p_skip": 0.0,
        "bail_fraction": None,
        "skip_amplitude": None,
        "last_played": last_played if isinstance(last_played, str) else (
            str(last_played) if last_played is not None else None),
        "last_skipped_at": last_skipped_at if isinstance(last_skipped_at, str) else (
            str(last_skipped_at) if last_skipped_at is not None else None),
    }

    def _hours_since(ts):
        parsed = _parse_sql_ts(ts)
        if parsed is None:
            return None
        return max(0.0, (now - parsed).total_seconds() / 3600.0)

    # Per-user half-life falls back to the env default so existing callers
    # (and users with no stats) see pre-feature behaviour byte-for-byte.
    h_half = halflife_h if (halflife_h is not None and halflife_h > 0) else _DJ_RECENCY_HALFLIFE_H
    h_play = _hours_since(last_played)
    if h_play is not None and h_half > 0:
        ex["p_play"] = float(2.0 ** (-h_play / h_half))

    h_skip = _hours_since(last_skipped_at)
    if h_skip is not None and _DJ_SKIP_HALFLIFE_H > 0:
        # Depth of the bail. Mean skip position, not the most recent one:
        # the aggregate stores a sum and a count, and going to track_events
        # for the last position would be a second query per candidate.
        # The mean is monotone in the right direction and that is enough.
        amp = 1.0
        try:
            sc = int(skip_count or 0)
            dur = int(duration_ms or 0)
        except (TypeError, ValueError):
            sc, dur = 0, 0
        if sc > 0 and dur > 0:
            frac = (float(skip_pos_sum or 0) / sc) / float(dur)
            frac = max(0.0, min(1.0, frac))
            amp = 2.0 - 1.5 * frac
            ex["bail_fraction"] = frac
        ex["skip_amplitude"] = amp
        ex["p_skip"] = float(amp * (2.0 ** (-h_skip / _DJ_SKIP_HALFLIFE_H)))

    # Belt and braces. P is non-negative by construction above; this makes it
    # non-negative by assertion too, so a future retune of the constants
    # cannot accidentally turn the penalty into a boost.
    p = max(0.0, ex["p_play"] + ex["p_skip"])
    ex["p"] = p
    return p, ex


class _RecencyCandidate:
    """One candidate on the way through the re-ranker.

    `score` is generic: HIGHER IS BETTER, whatever the path's scale. DJ passes
    cosine similarity; _similar_vibe passes -distance. Nothing below knows
    which.
    """
    __slots__ = ("score", "payload", "last_played", "last_skipped_at",
                 "skip_count", "skip_pos_sum", "duration_ms")

    def __init__(self, score, payload, last_played=None, last_skipped_at=None,
                 skip_count=0, skip_pos_sum=0, duration_ms=None):
        self.score = float(score)
        self.payload = payload
        self.last_played = last_played
        self.last_skipped_at = last_skipped_at
        self.skip_count = skip_count
        self.skip_pos_sum = skip_pos_sum
        self.duration_ms = duration_ms


def _rerank_by_recency(cands, limit, now=None, explain=False, halflife_h=None):
    """Re-rank `cands` (already sorted best-first) and return the first
    `limit`. Each returned item is (candidate, final_score, explain|None).

    Pure apart from the clock. Returns the input order untouched whenever the
    re-rank cannot be meaningful:
      * the gate is off (handled by the caller, which also skips the JOIN),
      * fewer than two candidates,
      * the output window is below _DJ_RECENCY_MIN_WINDOW.

    If every candidate carries the SAME P, every score shifts by the same
    constant and the order is unchanged -- which is why "small library where
    everything is recent" degrades to today's behaviour for free, and why P
    must NOT be normalised within the pool.
    """
    n = len(cands)
    if n == 0:
        return []
    now = now or datetime.now(timezone.utc)

    window_hi = cands[0].score
    window_lo = cands[min(limit, n) - 1].score
    window = window_hi - window_lo
    if n < 2 or window < _DJ_RECENCY_MIN_WINDOW:
        out = []
        for i, c in enumerate(cands[:limit]):
            ex = None
            if explain:
                ex = {"sim": c.score, "lambda": 0.0, "p_play": 0.0, "p_skip": 0.0,
                      "penalty": 0.0, "final": c.score,
                      "rank_before": i + 1, "rank_after": i + 1,
                      "degenerate_window": True,
                      "last_played": None, "last_skipped_at": None}
            out.append((c, c.score, ex))
        return out

    lam = _DJ_RECENCY_WEIGHT * window

    scored = []
    for i, c in enumerate(cands):
        p, ex = _recency_penalty(
            now, c.last_played, c.last_skipped_at,
            c.skip_count, c.skip_pos_sum, c.duration_ms,
            halflife_h=halflife_h,
        )
        penalty = lam * p
        final = c.score - penalty
        scored.append([c, final, i, ex, penalty, p])

    # Stable: equal finals keep their un-penalised relative order.
    scored.sort(key=lambda e: -e[1])

    out = []
    for rank_after, entry in enumerate(scored[:limit]):
        c, final, rank_before, ex, penalty, _p = entry
        block = None
        if explain:
            block = {
                "sim": c.score,
                "lambda": lam,
                "p_play": ex["p_play"],
                "p_skip": ex["p_skip"],
                "bail_fraction": ex["bail_fraction"],
                "penalty": penalty,
                "final": final,
                "rank_before": rank_before + 1,
                "rank_after": rank_after + 1,
                "last_played": ex["last_played"],
                "last_skipped_at": ex["last_skipped_at"],
            }
        out.append((c, final, block))
    return out


def _fetch_recency_stats(conn, user_id, now=None):
    """{track_id: (last_played, last_skipped_at, skip_count, skip_pos_sum)}
    for everything this user touched in the last 30 days.

    ONE query per request, used only on the numpy / cold-start paths where
    there is no candidate query to hang a LEFT JOIN off. The Turso ranking
    path never calls this -- it gets the same columns on the JOIN it was
    already issuing.

    The 30-day cutoff is safe: P_play(30 d) = 0.0009, below the float noise on
    the similarity scores. Served by idx_user_track_stats_recent
    (user_id, last_played DESC) for the first predicate.
    """
    out = {}
    try:
        rows = conn.execute(
            "SELECT track_id, last_played, last_skipped_at, "
            "       COALESCE(u_skip_count, 0) + COALESCE(s_skip_count, 0) AS skip_count, "
            "       COALESCE(u_skip_position_ms_sum, 0) "
            "     + COALESCE(s_skip_position_ms_sum, 0) AS skip_pos_sum "
            "FROM user_track_stats "
            "WHERE user_id = ? "
            "  AND (last_played     > datetime('now', '-30 day') "
            "    OR last_skipped_at > datetime('now', '-30 day'))",
            (user_id,),
        ).fetchall()
    except Exception as e:
        # No stats table (prod before _turso_create_event_tables.py has run),
        # or any other read failure. Degrade to "no history", which is the
        # pre-feature behaviour, rather than failing the recommendation.
        log.warning("[dj] recency stats unavailable: %s", e)
        return out
    for r in rows:
        out[int(r["track_id"])] = (
            r["last_played"], r["last_skipped_at"],
            r["skip_count"], r["skip_pos_sum"],
        )
    return out


# Cold-start gate for the per-user half-life: below this many plays, the
# ema is too noisy to beat the fixed env default. 5 is deliberately small --
# an EMA with alpha=0.1 that has seen 4 updates is still ~65% initialisation
# weight; by the fifth it is ~59%, but we have at least a signal.
_DJ_USER_HALFLIFE_MIN_PLAYS = 5
# Hard bounds on the derived half-life. 12 h keeps a very heavy listener from
# shrinking the "forget this track" window below one listening session; 336 h
# (two weeks) keeps a very light listener from suppressing a track they heard
# once a month ago. Both are generous relative to the shipping fixed default
# of 72 h, which lands inside the window at any realistic cadence.
_DJ_USER_HALFLIFE_MIN_H = 12.0
_DJ_USER_HALFLIFE_MAX_H = 336.0
# Mapping constant: half-life ~= 3 * typical interval between plays. A user
# who plays every 24 h gets 72 h (matches today's default), a 4-hourly heavy
# listener gets 12 h, a 100-hourly light listener gets 300 h.
_DJ_USER_HALFLIFE_MULT = 3.0


def _fetch_user_halflife(conn, user_id) -> float:
    """Return H_play for this user. Falls back to the env default on cold start,
    unknown user, missing table (prod before _turso_create_user_stats.py has
    run), or any read failure.

    One SELECT per request, keyed on the PK. Pure read -- no writes, no side
    effects on the recency re-rank fast path.
    """
    try:
        row = conn.execute(
            "SELECT ema_interval_h, play_count FROM user_stats WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    except Exception as e:
        log.warning("[dj] user_stats unavailable: %s", e)
        return _DJ_RECENCY_HALFLIFE_H
    if row is None:
        return _DJ_RECENCY_HALFLIFE_H
    try:
        pc = int(row["play_count"] or 0)
        ema = row["ema_interval_h"]
        ema_f = float(ema) if ema is not None else None
    except (TypeError, ValueError):
        return _DJ_RECENCY_HALFLIFE_H
    if pc < _DJ_USER_HALFLIFE_MIN_PLAYS or ema_f is None or ema_f <= 0:
        return _DJ_RECENCY_HALFLIFE_H
    h = _DJ_USER_HALFLIFE_MULT * ema_f
    return max(_DJ_USER_HALFLIFE_MIN_H, min(_DJ_USER_HALFLIFE_MAX_H, h))
