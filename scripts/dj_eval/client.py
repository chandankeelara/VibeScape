"""
HTTP client for the DJ, sending what the frontend sends.

What the real app does, and where it is written down:

  * Auth: POST /api/auth/login {email, password} returns session_token
    (features/login/EmailForm.jsx), then `Authorization: Bearer <token>`
    on every call (frontend-next/src/lib/api.js; backend require_user).
    login() below; setup_copy.py made the accounts.
  * DJ fetch: POST /api/tracks/{key}/similar with
        {mode: 'dj', events: <newest 30 of the log>, exclude_ids: [...], limit}
    (frontend-next/src/features/queue/dj.js fetchDjPicks). There is no vibe,
    no seed field and no recency field in the body: the seed is the path
    segment, and recency is read server-side from user_track_stats. The app
    asks for limit=8 (useRecs.js RECS_LIMIT); the eval asks for 10 by
    default, which changes the candidate pool size only through
    _dj_recency_pool(limit) = max(limit, min(POOL_MAX, max(POOL, 12*limit))).
  * {key}: apiKey(t) -- the internal tracks.id when the track has one, else
    its spotify_id (lib/vibe.js). Event ids use the same rule.
  * The log: append-only {id, action, played_ratio, ts}, actions searched,
    queued, picked, completed, next, skipped. A track's ending is classified
    by played ratio: >= 0.85 completed, >= 0.45 next, else skipped (dj.js
    classifyTransition). The seed's own events count.
  * exclude_ids: the session seen-set -- every track put in front of the
    user, played or skipped -- as integer ids only (dj.js excludeIds drops
    anything non-numeric), capped at 200.
  * Autoplay (QueueState.jsx djPickNext): when a track ends, its ending is
    logged FIRST, then one fetch with that track still the seed, and the top
    pick plays with source 'autoplay'. recommend() after end() is that.
  * Telemetry: play_start when a track loads, play_end when it ends, to
    POST /api/events (lib/listenLog.js). Those are what feed
    user_track_stats, which the DJ's recency re-rank reads. The app batches
    them every 5 s; this sends one batch per track, its play_start and
    play_end together, as it ends and before the next DJ fetch. Only the
    just-ended track's own stats could differ from the app's timing, and it
    is in the seen-set, so excluded either way.

Every request is timed (wall clock, client side) into Session.timings.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Optional

import requests

MAX_SENT_EVENTS = 30       # dj.js DJ_MAX_SENT_EVENTS
MAX_EXCLUDES = 200         # dj.js DJ_MAX_EXCLUDES == PlayerContext SEEN_MAX
COMPLETED_AT = 0.85        # dj.js DJ_COMPLETED_THRESHOLD
NEXT_AT = 0.45


def classify(ratio: float) -> str:
    if ratio >= COMPLETED_AT:
        return "completed"
    if ratio >= NEXT_AT:
        return "next"
    return "skipped"


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"{path} -> HTTP {status}: {body[:300]}")
        self.status = status


def login(base_url: str, email: str, password: str, timeout: float = 30.0) -> str:
    """POST /api/auth/login, as EmailForm does. Returns the session token."""
    r = requests.post(base_url.rstrip("/") + "/api/auth/login",
                      json={"email": email, "password": password}, timeout=timeout)
    if r.status_code >= 300:
        raise ApiError(r.status_code, r.text, "/api/auth/login")
    return r.json()["session_token"]


class DjClient:
    """Authenticated transport. Knows nothing about sessions."""

    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    def _post(self, path: str, body: dict):
        t0 = time.perf_counter()
        r = self.http.post(self.base_url + path, json=body, timeout=self.timeout)
        ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code >= 300:
            raise ApiError(r.status_code, r.text, path)
        return r.json(), ms

    def search(self, query: str, limit: int = 10):
        """GET /api/tracks/search, the frontend's library search. (json, ms)."""
        t0 = time.perf_counter()
        r = self.http.get(self.base_url + "/api/tracks/search",
                          params={"q": query, "limit": limit}, timeout=self.timeout)
        ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code >= 300:
            raise ApiError(r.status_code, r.text, "/api/tracks/search")
        return r.json(), ms

    def similar(self, key, body: dict):
        """(response json, elapsed ms)."""
        return self._post(f"/api/tracks/{requests.utils.quote(str(key), safe='')}/similar", body)

    def post_events(self, events: list):
        return self._post("/api/events", {"events": events})


@dataclass
class Track:
    """What the harness knows about a track; `id` is the internal tracks.id."""
    spotify_id: str
    id: Optional[int] = None
    title: str = ""
    artist: str = ""
    album: str = ""
    language: Optional[str] = None
    vibe: Optional[float] = None
    duration_ms: Optional[int] = None

    @classmethod
    def from_api(cls, d: dict) -> "Track":
        """A full track row as /similar and /tracks/search return it."""
        return cls(spotify_id=d.get("spotify_id") or "", id=d.get("id"),
                   title=d.get("title") or "", artist=d.get("artist") or "",
                   album=d.get("album") or "", language=d.get("language"),
                   vibe=d.get("vibe_score_ml"), duration_ms=d.get("duration_ms"))


@dataclass
class Rec:
    id: Optional[int]
    spotify_id: Optional[str]
    title: str
    artist: str
    score: Optional[float]
    final_score: Optional[float]


@dataclass
class Step:
    seed: object
    recs: list
    ms: float
    mode_used: str


class Session:
    """One listening session, run the way the frontend runs it."""

    def __init__(self, client: DjClient, catalog: dict, limit: int = 10,
                 post_events: bool = True, vibe: Optional[float] = None,
                 vibe_source: str = "system"):
        self.client = client
        self.catalog = catalog            # spotify_id -> Track
        self.limit = limit
        self.post_events = post_events
        self.vibe = vibe
        self.vibe_source = vibe_source    # DJ mode sets the slider: 'system'
        self.session_id = uuid.uuid4().hex
        self.log: list[dict] = []
        self.seen: list[int] = []         # LRU, newest last
        self.current: Optional[Track] = None
        self._ended: Optional[Track] = None   # the seed between end() and play()
        self._pending: list[dict] = []        # the open play's play_start
        self.steps: list[Step] = []
        self.timings: list[tuple[str, float]] = []

    # --------------------------------------------------------------- state
    def to_state(self) -> dict:
        """Everything a later process needs to carry on (listener.py).
        Language and vibe are left out: the state file sits where a
        listener agent could read it, and those are what it must not see."""
        return {"session_id": self.session_id, "limit": self.limit, "log": self.log,
                "seen": self.seen, "pending": self._pending,
                "current": self.current.spotify_id if self.current else None,
                "ended": self._ended.spotify_id if self._ended else None,
                "catalog": {k: {**asdict(v), "language": None, "vibe": None}
                            for k, v in self.catalog.items()}}

    @classmethod
    def from_state(cls, client: "DjClient", st: dict, post_events: bool = True) -> "Session":
        catalog = {k: Track(**v) for k, v in st["catalog"].items()}
        s = cls(client, catalog, limit=st["limit"], post_events=post_events)
        s.session_id, s.log, s.seen, s._pending = st["session_id"], st["log"], st["seen"], st["pending"]
        s.current = catalog.get(st["current"]) if st["current"] else None
        s._ended = catalog.get(st["ended"]) if st["ended"] else None
        return s

    # ------------------------------------------------------------ identity
    def track(self, sid: str) -> Track:
        t = self.catalog.get(sid)
        return t if t is not None else Track(spotify_id=sid)

    @staticmethod
    def key(t: Track):
        return t.id if t.id is not None else t.spotify_id

    def _see(self, t: Track) -> None:
        if t.id is None:
            return
        if t.id in self.seen:
            self.seen.remove(t.id)
        self.seen.append(t.id)
        del self.seen[:-MAX_EXCLUDES]

    def _log(self, t: Track, action: str, ratio: Optional[float] = None) -> None:
        self.log.append({"id": self.key(t), "action": action,
                         "played_ratio": ratio, "ts": int(time.time() * 1000)})
        del self.log[:-50]                # dj.js DJ_MAX_EVENTS

    # ----------------------------------------------------------- telemetry
    def _envelope(self, t: Track, **fields) -> dict:
        ev = {"spotify_id": t.spotify_id} if t.spotify_id else {"track_id": t.id}
        ev.update(fields)
        ev.update(client_ts=int(time.time() * 1000), session_id=self.session_id,
                  tz_offset_min=0, dj_mode=True, vibe_source=self.vibe_source)
        if self.vibe is not None:
            ev["vibe"] = self.vibe
        return ev

    def _send(self, events: list) -> None:
        if not self.post_events or not events:
            return
        _, ms = self.client.post_events(events)
        self.timings.append(("events", ms))

    # ------------------------------------------------------------- actions
    def play(self, sid: str, source: str = "autoplay") -> Track:
        """Load a track: it becomes current and joins the seen-set."""
        if self.current is not None:
            raise RuntimeError("end the current track first")
        t = self.track(sid)
        self.current = t
        self._see(t)
        self._pending = [self._envelope(t, type="play_start", source=source, playback="spotify")]
        return t

    def end(self, ratio: float) -> str:
        """The current track ends at `ratio` of its length. It stays the
        seed until the next play(), as in the app's autoplay picker."""
        t = self.current
        action = classify(ratio)
        self._log(t, action, ratio)
        dur = t.duration_ms or 200_000
        pos = dur if action == "completed" else int(dur * ratio)
        reason = "completed" if action == "completed" else "skipped"
        fields = dict(type="play_end", position_ms=pos, duration_ms=dur, reason=reason,
                      listened_ms=pos, playback="spotify", source="dj")
        if reason != "completed":
            fields["trigger"] = "next_button"
        self._send(self._pending + [self._envelope(t, **fields)])
        self._pending = []
        self._ended = t
        self.current = None
        return action

    def search(self, sid: str) -> Track:
        t = self.track(sid)
        self._log(t, "searched")
        return self.play(sid, source="search")

    def pick(self, sid: str) -> Track:
        t = self.track(sid)
        self._log(t, "picked")
        return self.play(sid, source="pick")

    def complete(self, sid: str, source: str = "autoplay") -> None:
        self.play(sid, source)
        self.end(1.0)

    def skip(self, sid: str, ratio: float = 0.1, source: str = "autoplay") -> None:
        self.play(sid, source)
        self.end(ratio)

    # ------------------------------------------------------------------ DJ
    def seed(self) -> Track:
        if self.current is not None:
            return self.current
        if self._ended is None:
            raise RuntimeError("nothing has played: no seed")
        return self._ended

    def recommend(self) -> list[Rec]:
        seed = self.seed()
        body = {
            "mode": "dj",
            "events": self.log[-MAX_SENT_EVENTS:],
            "exclude_ids": self.seen[-MAX_EXCLUDES:],
            "limit": self.limit,
        }
        data, ms = self.client.similar(self.key(seed), body)
        self.timings.append(("similar", ms))
        recs = []
        for d in data.get("tracks") or []:
            sid = d.get("spotify_id")
            if sid and sid not in self.catalog:
                self.catalog[sid] = Track.from_api(d)
            elif sid and self.catalog[sid].id is None and d.get("id") is not None:
                self.catalog[sid].id = int(d["id"])
            recs.append(Rec(id=d.get("id"), spotify_id=sid, title=d.get("title") or "",
                            artist=d.get("artist") or "", score=d.get("score"),
                            final_score=d.get("final_score")))
        self.steps.append(Step(seed=self.key(seed), recs=recs, ms=ms,
                               mode_used=data.get("mode_used") or ""))
        return recs
