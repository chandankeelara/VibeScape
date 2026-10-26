"""Listening telemetry: event normalisation, the per-track and per-user
stats rollups, and _record_track_events, which POST /api/events (routers/events.py)
and tests/database/test_rollup_rebuild.py call.

Moved out of app.py unchanged (refactor PR 2, 2026-10-25), except that the
qualified-play threshold is read as recency._DJ_QUALIFIED_PLAY_MS rather
than a copied name. scripts/rebuild_user_track_stats.py re-implements
_accumulate_stats in SQL; the two must agree.
"""
import json
from datetime import datetime, timezone

from bootstrap import log

import db  # noqa: E402  — db.get_conn() at call time: the seam tests patch
from services import recency  # noqa: E402
from services.recency import _parse_sql_ts  # noqa: E402


# ---------------- Listening events (telemetry) ----------------
#
# POST /api/events is the only writer of track_events and the only writer of
# user_track_stats. Design constraints, all of them deliberate:
#
#   * INLINE ONLY. No background thread, no queue, no worker. Cloud Run runs
#     --min-instances 0 --max-instances 3 with CPU throttling, so an
#     in-process worker only advances while a request happens to be in flight
#     and dies when the instance is reclaimed (backlog 1.5). The handler
#     resolves, inserts, upserts and returns.
#   * NEVER 4xx/5xx FOR DATA. Telemetry must not be able to break playback or
#     surface an error in the client. A malformed body, a malformed event, an
#     unknown track, an over-long batch, even a DB failure all come back 202
#     with the event counted in `rejected`. The only non-202 outcome is 401
#     from require_user, which is an auth failure, not a data failure.
#   * EVENTS ARE TRUTH, STATS ARE A CACHE. track_events is append-only: one
#     INSERT per event, no UPDATE, no DELETE. user_track_stats is a
#     materialised aggregate of those same events, written in the same
#     request so /similar can read per-user behaviour without scanning the
#     log per candidate. Every column in it is a pure function of
#     track_events, so when the two disagree the events win and
#     scripts/rebuild_user_track_stats.py recomputes the aggregate from
#     scratch. That is what makes this safe despite Turso having no
#     transactions (backlog 2.2) — the insert and the upsert are two
#     independent statements and the cache can drift between them.
#   * NOTHING ELSE MAY WRITE user_track_stats. The moment a second writer
#     with its own idea of what a "play" is touches these columns they stop
#     being rebuildable, and this becomes backlog 1.2 again.
#
# A batch is written as multi-row INSERT ... VALUES chunks: still one row per
# event, but on Turso each execute() is a separate HTTPS round trip
# (backend/db_client.py), so a 50-event batch would otherwise be 50 of them.

_EVENT_BATCH_CAP = 50          # events per request; the overflow is rejected, not the request
_EVENT_INSERT_CHUNK = 25       # track_events rows per statement (25 x 18 = 450 bound params)
_STATS_UPSERT_CHUNK = 20       # user_track_stats rows per statement (20 x 28 = 560 params)
# Track-scoped events, stored in track_events. Only _PLAY_TYPES feed
# user_track_stats — pause / resume / seek / queue_add are behaviour around a
# play, not plays, and must never be counted as one (_accumulate_stats used to
# treat every non-play_start row as a play_end).
_EVENT_TYPES = ("play_start", "play_end", "pause", "resume", "seek", "queue_add")
_PLAY_TYPES = ("play_start", "play_end")
# App-level events with no track, stored in user_events (same envelope).
_APP_EVENT_TYPES = ("session_start", "session_end", "search", "vibe_change", "dj_toggle",
                    "discover_toggle")
_VIBE_SOURCES = ("user", "system")
_EVENT_DATA_MAX_CHARS = 2000   # serialised `data` object; longer is dropped, not truncated
_EVENT_DATA_MAX_KEYS = 12


def _ev_int(value, lo=None, hi=None):
    """Coerce a JSON value to int, or None if it is not a number or falls
    outside [lo, hi]. Out of range is nulled rather than clamped, so a client
    bug stays visible as missing data instead of masquerading as a real
    value."""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if lo is not None and n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n


def _ev_text(value, limit=32):
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s[:limit] if s else None


def _ev_bool(value):
    """Strict tri-state: True/False (or the strings/ints that unambiguously
    mean them) → 1/0; anything else → None. No default — see _normalize_event
    on why a guessed label is worse than a null."""
    if value is True or value is False:
        return 1 if value else 0
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return 1
        if v in ("false", "0", "no"):
            return 0
    return None


def _ev_data(value):
    """`data` must be a flat JSON object of scalars. Returns its JSON text, or
    None when it is absent, not an object, too big, or nests. Strings are
    capped at 300 chars (a search query is the longest legitimate value)."""
    if not isinstance(value, dict) or not value or len(value) > _EVENT_DATA_MAX_KEYS:
        return None
    clean = {}
    for k, v in value.items():
        if not isinstance(k, str) or not k or len(k) > 32:
            return None
        if isinstance(v, str):
            v = v[:300]
        elif v is not None and not isinstance(v, (bool, int, float)):
            return None
        clean[k] = v
    text = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= _EVENT_DATA_MAX_CHARS else None


def _normalize_event(raw):
    """Validate one client event. Returns a dict ready for insertion (with the
    track still unresolved — an internal id under `_tid` or a spotify_id under
    `_key`) or None if the event is unusable.

    Only two things are structurally required: a known `type` and some track
    identity. Everything else is best-effort — `reason` and `source` are
    stored verbatim after trimming rather than validated against a vocabulary,
    so an unexpected client value shows up in the data instead of being
    silently nulled. The aggregate counters do validate `reason`, because a
    counter has to pick a bucket.

    `vibe_source` and `dj_mode` are expected on every event but are NEVER
    defaulted when missing or unrecognised — they are recorded as NULL
    ("unknown") and the event is still accepted. A guessed label is worse
    than a null here: mislabelling the system's own slider echo as the user's
    stated preference is how the recommender ends up training on its own
    output.
    """
    if not isinstance(raw, dict):
        return None
    etype = _ev_text(raw.get("type"), 16)
    is_app = etype in _APP_EVENT_TYPES
    if etype not in _EVENT_TYPES and not is_app:
        return None

    tid = key = None
    if not is_app:
        tid = _ev_int(raw.get("track_id"), lo=1)
        if tid is None:
            key = _ev_text(raw.get("spotify_id"), 64)
            if not key:
                return None

    vsrc = (_ev_text(raw.get("vibe_source"), 16) or "").lower()
    if vsrc not in _VIBE_SOURCES:
        vsrc = None

    is_end = etype == "play_end"
    return {
        "_app": is_app,
        "_tid": tid,
        "_key": key,
        "type": etype,
        "reason": _ev_text(raw.get("reason"), 32) if is_end else None,
        "position_ms": _ev_int(raw.get("position_ms"), lo=0),
        "duration_ms": _ev_int(raw.get("duration_ms"), lo=0),
        "vibe": _ev_int(raw.get("vibe"), lo=0, hi=100),
        "vibe_source": vsrc,
        "dj_mode": _ev_bool(raw.get("dj_mode")),
        "source": _ev_text(raw.get("source"), 32),
        "client_ts": _ev_int(raw.get("client_ts"), lo=0),
        # telemetry v2 — all optional, NULL when absent, never guessed
        "listened_ms": _ev_int(raw.get("listened_ms"), lo=0) if is_end else None,
        "end_trigger": _ev_text(raw.get("trigger"), 32) if is_end else None,
        "playback": _ev_text(raw.get("playback"), 16),
        "session_id": _ev_text(raw.get("session_id"), 64),
        "tz_offset_min": _ev_int(raw.get("tz_offset_min"), lo=-900, hi=900),
        "data": _ev_data(raw.get("data")),
    }


# Every additive counter in user_track_stats, in one place. The insert, the
# ON CONFLICT arithmetic and scripts/rebuild_user_track_stats.py all derive
# from this tuple, so adding a counter means touching exactly one list here
# and one CASE expression in the rebuild script.
#
# The u_ (vibe_source='user') and s_ (vibe_source='system') families are
# deliberately NOT merged — see the comment on user_track_stats in schema.sql.
# u_ is stated preference; s_ is the DJ's own output coming back as a reward
# signal. Averaging them trains the recommender on itself.
_STATS_TOTALS = ("play_count", "end_count", "total_played_ms", "dj_play_count", "total_listened_ms")
_STATS_PER_LABEL = (
    "play_count", "end_count", "complete_count", "skip_count", "replace_count",
    "skip_position_ms_sum", "vibe_count", "vibe_sum", "vibe_sum_sq",
)
_STATS_PREFIX = {"user": "u_", "system": "s_"}
_STATS_COUNTERS = (
    _STATS_TOTALS
    + tuple(f"u_{c}" for c in _STATS_PER_LABEL)
    + tuple(f"s_{c}" for c in _STATS_PER_LABEL)
)


def _accumulate_stats(acc: dict, ev: dict) -> None:
    """Fold one accepted event into the per-track aggregate delta.

    This is the single definition of what each counter means. The rebuild
    script implements the same arithmetic in SQL; if the two ever disagree,
    this one is the spec.

    An event with no vibe_source label lands in the label-agnostic totals
    only, so u_* + s_* can be less than play_count / end_count. That is
    correct and intended: an unlabelled event is evidence that a play
    happened, but not evidence of whose intent set the vibe.
    """
    # Only plays are plays. The caller already filters, this makes it a
    # property of the spec rather than of one call site.
    if ev["type"] not in _PLAY_TYPES:
        return

    # `p` is the column prefix of the population this event belongs to, or
    # None when the client did not tell us who moved the slider.
    p = _STATS_PREFIX.get(ev["vibe_source"])

    if ev["type"] == "play_start":
        acc["play_count"] += 1
        if ev["dj_mode"] == 1:
            acc["dj_play_count"] += 1
        if p:
            acc[p + "play_count"] += 1
            # vibe is sampled on play_start only: one sample per play, so a
            # play_start + play_end pair for the same listen cannot count the
            # same slider position twice.
            if ev["vibe"] is not None:
                acc[p + "vibe_count"] += 1
                acc[p + "vibe_sum"] += ev["vibe"]
                acc[p + "vibe_sum_sq"] += ev["vibe"] * ev["vibe"]
        return

    # play_end
    pos = ev["position_ms"] or 0
    # Time actually heard. Clients before telemetry v2 sent no listened_ms,
    # so their playhead is the best estimate there is — the same number
    # total_played_ms has always held.
    heard = ev.get("listened_ms")
    if heard is None:
        heard = pos
    acc["end_count"] += 1
    acc["total_played_ms"] += pos
    acc["total_listened_ms"] += heard
    reason = (ev["reason"] or "").lower()
    if reason == "skipped":
        acc["_skipped"] = True
    # A play "qualifies" (updates last_played) only on play_end with either a
    # completed reason or at least DJ_QUALIFIED_PLAY_MS of LISTENING — heard
    # time, so seeking to 2:00 and stopping is not a listen. play_start
    # NEVER qualifies — a 2 s skim is not a listen. See _upsert_track_stats.
    if reason == "completed" or heard >= recency._DJ_QUALIFIED_PLAY_MS:
        acc["_qualified"] = True
    if not p:
        return
    acc[p + "end_count"] += 1
    if reason == "completed":
        acc[p + "complete_count"] += 1
    elif reason == "skipped":
        acc[p + "skip_count"] += 1
        acc[p + "skip_position_ms_sum"] += pos
    elif reason == "replaced":
        acc[p + "replace_count"] += 1
    # An unrecognised reason still counts in end_count and total_played_ms and
    # is preserved verbatim in track_events, so a client vocabulary change is
    # recoverable by rebuilding.


_STATS_COLS = (
    ("user_id", "track_id") + _STATS_COUNTERS
    + ("first_played_at", "last_played", "last_skipped_at", "updated_at")
)


def _upsert_track_stats(conn, user_id: int, groups: dict, now: str) -> None:
    """Fold a batch of per-track deltas into user_track_stats.

    One multi-row INSERT ... ON CONFLICT DO UPDATE per chunk. Keys are unique
    within a statement (the caller groups by track_id), which is required —
    SQLite cannot apply two conflicting updates from a single statement.
    """
    rows = []
    for tid, acc in groups.items():
        rows.append(
            (user_id, tid)
            + tuple(acc[c] for c in _STATS_COUNTERS)
            + (
                now,                                       # first_played_at (COALESCEd on conflict)
                now if acc.get("_qualified") else None,    # last_played (only on qualified listens)
                now if acc.get("_skipped") else None,      # last_skipped_at
                now,                                       # updated_at
            )
        )

    sums = ",\n              ".join(
        f"{c} = user_track_stats.{c} + excluded.{c}" for c in _STATS_COUNTERS
    )
    sql_tail = f"""
        ON CONFLICT(user_id, track_id) DO UPDATE SET
              {sums},
              first_played_at = COALESCE(user_track_stats.first_played_at, excluded.first_played_at),
              last_played     = NULLIF(MAX(COALESCE(user_track_stats.last_played, ''),
                                           COALESCE(excluded.last_played, '')), ''),
              last_skipped_at = NULLIF(MAX(COALESCE(user_track_stats.last_skipped_at, ''),
                                           COALESCE(excluded.last_skipped_at, '')), ''),
              updated_at      = excluded.updated_at
    """
    cols = ", ".join(_STATS_COLS)
    width = len(_STATS_COLS)
    for i in range(0, len(rows), _STATS_UPSERT_CHUNK):
        chunk = rows[i:i + _STATS_UPSERT_CHUNK]
        values = ",".join(["(" + ",".join("?" * width) + ")"] * len(chunk))
        conn.execute(
            f"INSERT INTO user_track_stats ({cols}) VALUES {values}" + sql_tail,
            tuple(v for row in chunk for v in row),
        )


# EMA parameters for user_stats.ema_interval_h. alpha=0.1 means the current
# estimate is 90% history / 10% newest interval -- stable enough that one
# unusually long gap (holiday) does not blow the half-life out.
_USER_STATS_EMA_ALPHA = 0.1
# Clamp bounds on a single observed inter-play interval, in hours. 0.01 h
# (36 s) catches double-fires and clock skew; 720 h (30 d) stops a comeback
# after months of silence from permanently anchoring the EMA at a huge value.
_USER_STATS_INTERVAL_MIN_H = 0.01
_USER_STATS_INTERVAL_MAX_H = 720.0


def _upsert_user_stats(conn, user_id: int, play_count_delta: int, now: str) -> None:
    """Fold play_start events for one user into user_stats.

    Reads the current row (if any), computes the new EMA from the interval
    between the previous last_play_at and `now`, writes it back. Caller must
    guarantee play_count_delta > 0 -- this function is skipped entirely for
    batches with no plays, which is the common skip-only case.

    Two separate statements (SELECT then INSERT/UPDATE) on top of the event
    INSERT and user_track_stats upsert that already happened. On Turso each
    is its own Hrana stream so there is no transactional envelope -- a crash
    between any of them leaves user_stats behind the log, which is exactly
    the same drift mode as user_track_stats and recoverable the same way
    (scripts/rebuild_user_stats.py). The read path treats a missing row as
    "cold start, use the env default", so drift degrades to the pre-feature
    behaviour rather than to a wrong half-life.
    """
    if play_count_delta <= 0:
        return
    row = conn.execute(
        "SELECT ema_interval_h, play_count, last_play_at "
        "FROM user_stats WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        # First play ever. Seed with play_count = delta so a multi-play batch
        # still arms the cold-start gate correctly.
        conn.execute(
            "INSERT INTO user_stats "
            "(user_id, plays_30d, ema_interval_h, play_count, last_play_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (user_id, play_count_delta, play_count_delta, now, now),
        )
        return
    last_ts = _parse_sql_ts(row["last_play_at"])
    now_dt = _parse_sql_ts(now)
    new_ema = row["ema_interval_h"]
    if last_ts is not None and now_dt is not None:
        gap = max(_USER_STATS_INTERVAL_MIN_H,
                  min(_USER_STATS_INTERVAL_MAX_H,
                      (now_dt - last_ts).total_seconds() / 3600.0))
        if new_ema is None:
            new_ema = gap
        else:
            new_ema = _USER_STATS_EMA_ALPHA * gap + (1.0 - _USER_STATS_EMA_ALPHA) * float(new_ema)
    conn.execute(
        "UPDATE user_stats SET "
        "  ema_interval_h = ?, "
        "  play_count = play_count + ?, "
        "  last_play_at = ?, "
        "  updated_at = ? "
        "WHERE user_id = ?",
        (new_ema, play_count_delta, now, now, user_id),
    )


def _record_track_events(user_id: int, items: list, display_name: str = "") -> tuple:
    """Resolve, log and aggregate a batch of already-capped client events.

    Returns (accepted, rejected). Runs inline on a worker thread; cost is
    2 SELECTs (batch-resolve by spotify_id and by id) + ceil(n/25) INSERTs
    into track_events + ceil(distinct_tracks/20) upserts into
    user_track_stats.

    `accepted` means "durably written to track_events". The aggregate upsert
    is attempted only for accepted events, and its failure is logged but does
    not change the counts — the aggregate is rebuildable, the log is not.

    An event whose track is not in the catalogue is rejected, never inserted
    with a NULL track_id.
    """
    parsed = []
    rejected = 0
    app_events = []
    for raw in items:
        ev = _normalize_event(raw)
        if ev is None:
            rejected += 1
        elif ev["_app"]:
            app_events.append(ev)
        else:
            parsed.append(ev)
    if not parsed and not app_events:
        return 0, rejected

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    try:
        accepted = 0
        # App-level events have no track to resolve and feed no aggregate:
        # append them and move on. Same never-fail rule as track_events.
        for i in range(0, len(app_events), _EVENT_INSERT_CHUNK):
            chunk = app_events[i:i + _EVENT_INSERT_CHUNK]
            values = ",".join(["(?,?,?,?,?,?,?,?,?,?)"] * len(chunk))
            params = []
            for e in chunk:
                params.extend((
                    user_id, e["type"], e["session_id"], e["tz_offset_min"],
                    e["vibe"], e["vibe_source"], e["dj_mode"], e["data"],
                    e["client_ts"], now,
                ))
            try:
                conn.execute(
                    "INSERT INTO user_events "
                    "(user_id, type, session_id, tz_offset_min, vibe, vibe_source, "
                    " dj_mode, data, client_ts, server_ts) VALUES " + values,
                    tuple(params),
                )
                accepted += len(chunk)
            except Exception:
                # "no such table: user_events" on a Turso instance that has not
                # had scripts/_turso_migrate_telemetry_v2.py run against it.
                log.exception("[events] user_events insert failed for %d event(s), user=%s",
                              len(chunk), user_id)
                rejected += len(chunk)
        if not parsed:
            conn.commit()
            return accepted, rejected

        want_sids = sorted({e["_key"] for e in parsed if e["_key"]})
        want_ids = sorted({e["_tid"] for e in parsed if e["_tid"] is not None})

        sid_to_id = {}
        if want_sids:
            qs = ",".join("?" * len(want_sids))
            for r in conn.execute(
                f"SELECT id, spotify_id FROM tracks WHERE spotify_id IN ({qs})",
                tuple(want_sids),
            ).fetchall():
                sid_to_id[r["spotify_id"]] = int(r["id"])

        known_ids = set()
        if want_ids:
            qs = ",".join("?" * len(want_ids))
            for r in conn.execute(
                f"SELECT id FROM tracks WHERE id IN ({qs})",
                tuple(want_ids),
            ).fetchall():
                known_ids.add(int(r["id"]))

        resolved = []   # [(track_id, event), ...]
        for e in parsed:
            tid = e["_tid"] if e["_tid"] in known_ids else sid_to_id.get(e["_key"])
            if tid is None:
                rejected += 1
                continue
            resolved.append((tid, e))
        if not resolved:
            conn.commit()
            return accepted, rejected

        logged = []
        for i in range(0, len(resolved), _EVENT_INSERT_CHUNK):
            chunk = resolved[i:i + _EVENT_INSERT_CHUNK]
            values = ",".join(["(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"] * len(chunk))
            params = []
            for tid, e in chunk:
                params.extend((
                    user_id, tid, e["type"], e["reason"], e["position_ms"],
                    e["duration_ms"], e["vibe"], e["vibe_source"], e["dj_mode"],
                    e["source"], e["client_ts"], now,
                    e["listened_ms"], e["end_trigger"], e["playback"],
                    e["session_id"], e["tz_offset_min"], e["data"],
                ))
            try:
                conn.execute(
                    "INSERT INTO track_events "
                    "(user_id, track_id, type, reason, position_ms, duration_ms, "
                    " vibe, vibe_source, dj_mode, source, client_ts, server_ts, "
                    " listened_ms, end_trigger, playback, session_id, tz_offset_min, data) "
                    "VALUES " + values,
                    tuple(params),
                )
                accepted += len(chunk)
                logged.extend(chunk)
            except Exception:
                # Includes "no such table: track_events" on a Turso instance
                # that has not had scripts/_turso_create_event_tables.py run
                # against it. Loud in the log, invisible to the client.
                log.exception("[events] track_events insert failed for %d event(s), user=%s",
                              len(chunk), user_id)
                rejected += len(chunk)

        # Only plays reach the aggregate. A pause or a queue-add of a track
        # never played must not create a user_track_stats row (it would set
        # first_played_at for a track nobody played).
        logged = [(tid, e) for tid, e in logged if e["type"] in _PLAY_TYPES]
        if logged:
            groups = {}
            for tid, e in logged:
                acc = groups.get(tid)
                if acc is None:
                    acc = groups[tid] = {c: 0 for c in _STATS_COUNTERS}
                _accumulate_stats(acc, e)
            try:
                _upsert_track_stats(conn, user_id, groups, now)
            except Exception:
                # The events are already logged, so this is recoverable:
                # scripts/rebuild_user_track_stats.py rebuilds from them.
                log.exception(
                    "[events] user_track_stats upsert failed for user=%s, %d track(s); "
                    "aggregate has drifted — run scripts/rebuild_user_track_stats.py",
                    user_id, len(groups),
                )
            # user_stats feeds the per-user DJ half-life. Only play_start
            # events advance it -- we are modelling play cadence, not
            # interaction cadence -- and the shared Guest row is skipped
            # outright rather than conflating many visitors' cadences into one.
            if (display_name or "") != "Guest":
                plays = sum(1 for _tid, e in logged if e["type"] == "play_start")
                if plays:
                    try:
                        _upsert_user_stats(conn, user_id, plays, now)
                    except Exception:
                        # Same recovery story as above: rebuild from events.
                        log.exception(
                            "[events] user_stats upsert failed for user=%s; "
                            "aggregate has drifted -- run scripts/rebuild_user_stats.py",
                            user_id,
                        )
        # Always: a batch of only pauses / seeks has no aggregate to upsert
        # but its rows still have to land.
        conn.commit()
        return accepted, rejected
    finally:
        conn.close()
