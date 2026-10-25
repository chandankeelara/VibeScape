"""POST /api/events: listening telemetry in. The route is a thin wrapper;
normalisation and the stats rollups are services/telemetry.py."""
from bootstrap import log  # first: .env, sys.path (see bootstrap.py)

from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool

from deps import require_user
from routers import Router
from services.telemetry import _EVENT_BATCH_CAP, _record_track_events

router = Router()


@router.post("/api/events", status_code=202)
async def post_track_events(request: Request, sess: dict = Depends(require_user)):
    """Append listening events to track_events and fold them into
    user_track_stats. Always 202.

    Body: {"events": [ {...}, ... ]} — at most _EVENT_BATCH_CAP (50) events;
    anything past the cap is counted in `rejected` rather than failing the
    request. Per-event shape (one envelope for every type):

        type         track events -> track_events:          (required)
                       "play_start" | "play_end" | "pause" | "resume" |
                       "seek" | "queue_add"
                     app events -> user_events (no track):
                       "session_start" | "session_end" | "search" |
                       "vibe_change" | "dj_toggle" | "discover_toggle"
        spotify_id   22-char Spotify id                    (track events: required unless track_id)
        track_id     internal tracks.id                    (alternative key)
        position_ms  play_end / pause / resume: the playhead
        duration_ms  play_end: track length, when known
        reason       play_end: "completed" | "skipped" | "replaced" | "abandoned"
        listened_ms  play_end: audible time, pauses and seek jumps excluded
        trigger      play_end: how it was ended — "next_button" | "media_key" |
                     "prev" | "search" | "pick" | "queue_jump" | "vibe_change"
                     (stored as end_trigger)
        playback     "spotify" | "preview" | "youtube"
        session_id   one per app session
        tz_offset_min  client offset, minutes east of UTC
        data         flat JSON object of type-specific extras (seek from_ms /
                     to_ms, queue_add via, search query / counts / outcome,
                     vibe_change from / to, dj_toggle / discover_toggle on,
                     play_start discover (a DJ pick from discovery),
                     session platform / duration_ms). Nested or oversized objects are dropped.
        vibe         slider value 0-100 at the time
        vibe_source  "user" | "system" — who last set that slider value.
                     Expected on every event; an absent or unrecognised value
                     is stored as NULL and the event is still accepted.
        dj_mode      true | false — was DJ mode active. Same NULL-not-guessed
                     treatment as vibe_source.
        source       "queue" | "dj" | "search" | "pick" | "autoplay"
                     ("pick" since 2026-10-10 — older "search" rows include picks)
        client_ts    epoch ms, client clock (untrusted; server_ts is authoritative)

    Response: {"accepted": int, "rejected": int}, always HTTP 202. The two
    counts sum to the number of events in the submitted array.

    The skip is the point: `type=play_end, reason=skipped` is the only
    explicit negative signal the system gets, which is why this is an event
    log and not a play counter.
    """
    n_seen = 0
    try:
        payload = await request.json()
        items = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            log.warning("[events] user=%s posted a body with no events array",
                        sess.get("user_id"))
            return {"accepted": 0, "rejected": 0}
        n_seen = len(items)
        over = 0
        if n_seen > _EVENT_BATCH_CAP:
            over = n_seen - _EVENT_BATCH_CAP
            items = items[:_EVENT_BATCH_CAP]
        accepted, rejected = await run_in_threadpool(
            _record_track_events, int(sess["user_id"]), items,
            sess.get("display_name") or "",
        )
        return {"accepted": accepted, "rejected": rejected + over}
    except Exception:
        # Telemetry is never allowed to produce a client-visible error.
        log.exception("[events] POST /api/events failed for user=%s", sess.get("user_id"))
        return {"accepted": 0, "rejected": n_seen}
