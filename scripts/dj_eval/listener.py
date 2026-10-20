"""
A listener's remote control for the DJ, for an LLM agent playing a user.

    python scripts/dj_eval/listener.py start --persona kannada-mass
    python scripts/dj_eval/listener.py search --session S "kantara"
    python scripts/dj_eval/listener.py play --session S t-3f9a
    python scripts/dj_eval/listener.py finish --session S t-3f9a
    python scripts/dj_eval/listener.py next --session S
    python scripts/dj_eval/listener.py skip --session S t-81c0 --played 0.2
    python scripts/dj_eval/listener.py feedback --session S t-81c0 --fit 2 --reason "..."
    python scripts/dj_eval/listener.py review --session S "..."

Every call goes to the real local server (serve.py) as the frontend would
send it: `search` is GET /api/tracks/search; `play` logs `searched` (a
search result) or `picked` (one off the up-next list); `next` is the app's
DJ autoplay (QueueState.jsx djPickNext): the ended track is the seed, one
POST /api/tracks/{key}/similar with the event log, the top pick plays and
the rest is the up-next list; plays go to POST /api/events. The DJ asks for
8, as the app does (useRecs.js RECS_LIMIT). client.Session does the work.

WHAT IT PRINTS. Only what a user sees: an opaque handle, title, artist and
album or film. Never language, vibe, mood, tempo, scores or real ids, and
never a server error body. `_show` is the only formatter of a track, and
tests/dj_real/test_listener_cli.py checks the output for hidden fields.

STATE. One JSON file per session under data/dj_eval/listener/ (gitignored),
which report.py reads. It holds handles -> spotify ids and the session token
but no language or vibe (Session.to_state). `start` signs the persona's own
user in (setup_copy.py made one per persona) and wipes its play history on
the copy, so each run starts as a fresh listener; two concurrent runs of the
same persona would share that history.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/

from dj_eval.client import ApiError, DjClient, Session, Track, login  # noqa: E402
from dj_eval.setup_copy import DEFAULT_OUT, PERSONAS, load_meta, reset_user  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[2]
STATE_DIR = _REPO / "data" / "dj_eval" / "listener"
DEFAULT_BASE_URL = "http://127.0.0.1:8765"    # serve.py --port default
RECS_LIMIT = 8                                 # useRecs.js
SEARCH_LIMIT = 10


class UserError(Exception):
    """Something the listener did wrong; printed as is, exit 2."""


# ------------------------------------------------------------------ state
def _path(sid: str) -> Path:
    if not sid or any(c in sid for c in "/\\.:"):
        raise UserError(f"unknown session {sid!r}")
    return STATE_DIR / f"{sid}.json"


def _load(sid: str) -> dict:
    p = _path(sid)
    if not p.exists():
        raise UserError(f"unknown session {sid!r}; run `start` first")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(st["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _session(st: dict) -> Session:
    return Session.from_state(DjClient(st["base_url"], st["token"]), st["session"])


def _handle(st: dict, spotify_id: str) -> str:
    for h, s in st["handles"].items():
        if s == spotify_id:
            return h
    while True:
        h = "t-" + secrets.token_hex(2)
        if h not in st["handles"]:
            st["handles"][h] = spotify_id
            return h


def _show(h: str, t: Track) -> str:
    """The one place a track becomes text: handle, title, artist, album."""
    line = f"{h}  {t.title or '?'} — {t.artist or '?'}"
    return line + (f"  [{t.album}]" if t.album else "")


def _log(st: dict, kind: str, **fields) -> None:
    st["history"].append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "kind": kind, **fields})


def _resolve(st: dict, s: Session, handle: str) -> Track:
    sid = st["handles"].get(handle)
    if sid is None:
        raise UserError(f"unknown handle {handle!r}")
    return s.track(sid)


def _timed(st: dict, s: Session) -> None:
    st["timings"].extend(s.timings)
    s.timings.clear()


def _play_entry(st: dict, handle: str, how: str, t: Track) -> None:
    st["plays"].append({"step": len(st["plays"]) + 1, "handle": handle, "spotify_id": t.spotify_id,
                        "title": t.title, "artist": t.artist, "album": t.album, "how": how,
                        "outcome": None, "played": None, "fit": None, "reason": None,
                        "sounds_like": None})


def _current_play(st: dict, handle: str) -> dict:
    p = st["plays"][-1] if st["plays"] else None
    if not p or p["handle"] != handle or p["outcome"] is not None:
        raise UserError(f"{handle} is not playing")
    return p


def _up_next(st: dict, s: Session, recs) -> list[str]:
    st["up_next"] = []
    lines = []
    for r in recs:
        if not r.spotify_id:
            continue
        h = _handle(st, r.spotify_id)
        st["up_next"].append(h)
        lines.append("  " + _show(h, s.track(r.spotify_id)))
    return lines


# --------------------------------------------------------------- commands
def cmd_start(a) -> list[str]:
    personas = {p["name"]: p for p in json.loads(PERSONAS.read_text(encoding="utf-8"))}
    if a.persona not in personas:
        raise UserError(f"unknown persona {a.persona!r}; one of: {', '.join(personas)}")
    db = Path(a.db)
    user = load_meta(db)["listeners"][a.persona]
    reset_user(db, user["user_id"])
    token = login(a.base_url, user["email"], user["password"])
    s = Session(DjClient(a.base_url, token), {}, limit=RECS_LIMIT)
    sid = f"{a.persona}-{secrets.token_hex(3)}"
    st = {"id": sid, "persona": a.persona, "base_url": a.base_url, "token": token,
          "db": str(db.resolve()), "user_id": user["user_id"],
          "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "handles": {}, "results": [], "up_next": [], "plays": [], "history": [],
          "timings": [], "review": None, "session": s.to_state()}
    _log(st, "start")
    _save(st)
    return [f"session {sid}", f"you: {personas[a.persona]['brief']}"]


def cmd_search(a, st, s) -> list[str]:
    data, ms = s.client.search(a.query, limit=SEARCH_LIMIT)
    st["timings"].append(["search", ms])
    st["results"] = []
    lines = []
    for d in data.get("tracks") or []:
        t = Track.from_api(d)
        if not t.spotify_id:
            continue
        s.catalog.setdefault(t.spotify_id, t)
        h = _handle(st, t.spotify_id)
        st["results"].append(h)
        lines.append(_show(h, s.catalog[t.spotify_id]))
    _log(st, "search", query=a.query, results=list(st["results"]))
    return lines or ["no results"]


def cmd_play(a, st, s) -> list[str]:
    if s.current is not None:
        raise UserError("something is playing: finish or skip it first")
    t = _resolve(st, s, a.handle)
    if a.handle in st["results"]:
        s.search(t.spotify_id)          # 'searched', source 'search'
        how = "search"
    elif a.handle in st["up_next"]:
        s.pick(t.spotify_id)            # 'picked', source 'pick'
        how = "picked"
    else:
        raise UserError(f"{a.handle} is not in your search results or up next")
    _play_entry(st, a.handle, how, t)
    _log(st, "play", handle=a.handle, how=how)
    return ["now playing: " + _show(a.handle, t)]


def cmd_next(a, st, s) -> list[str]:
    if s.current is not None:
        raise UserError("something is playing: finish or skip it first")
    if s._ended is None:
        raise UserError("nothing has played yet: search and play something first")
    recs = s.recommend()
    if not recs:
        _log(st, "next", got=0)
        return ["the DJ has nothing more to play"]
    top, rest = recs[0], recs[1:]
    t = s.play(top.spotify_id)          # source 'autoplay'
    h = _handle(st, top.spotify_id)
    _play_entry(st, h, "dj", t)
    lines = ["now playing: " + _show(h, t), "up next:"] + _up_next(st, s, rest)
    _log(st, "next", handle=h, up_next=list(st["up_next"]))
    return lines


def _end(a, st, s, ratio: float, outcome: str) -> list[str]:
    p = _current_play(st, a.handle)
    if s.current is None or s.current.spotify_id != p["spotify_id"]:
        raise UserError(f"{a.handle} is not playing")
    s.end(ratio)
    p["outcome"], p["played"] = outcome, ratio
    _log(st, outcome, handle=a.handle, played=ratio)
    return [f"{outcome}: {a.handle}"]


def cmd_finish(a, st, s) -> list[str]:
    return _end(a, st, s, 1.0, "finished")


def cmd_skip(a, st, s) -> list[str]:
    if not 0.0 <= a.played < 1.0:
        raise UserError("--played is the share of the song heard, 0 to under 1")
    return _end(a, st, s, a.played, "skipped")


def cmd_feedback(a, st, s) -> list[str]:
    p = next((p for p in reversed(st["plays"]) if p["handle"] == a.handle), None)
    if p is None:
        raise UserError(f"you have not played {a.handle}")
    p["fit"], p["reason"] = a.fit, a.reason
    if a.vibe is not None:
        p["sounds_like"] = a.vibe
    _log(st, "feedback", handle=a.handle, fit=a.fit, reason=a.reason, sounds_like=a.vibe)
    return [f"noted: {a.handle} fit {a.fit}"]


def cmd_review(a, st, s) -> list[str]:
    st["review"] = a.text
    st["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _log(st, "review")
    return ["review saved"]


COMMANDS = {"search": cmd_search, "play": cmd_play, "next": cmd_next, "finish": cmd_finish,
            "skip": cmd_skip, "feedback": cmd_feedback, "review": cmd_review}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="listener.py", description="Listen to the VibeScape DJ.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start", help="begin a listening session")
    p.add_argument("--persona", required=True)
    p.add_argument("--db", default=str(DEFAULT_OUT), help=argparse.SUPPRESS)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)

    def with_session(name, help_):
        q = sub.add_parser(name, help=help_)
        q.add_argument("--session", required=True)
        return q

    with_session("search", "search your library").add_argument("query")
    with_session("play", "play a song from search results or up next").add_argument("handle")
    with_session("next", "let the DJ choose and play the next song")
    with_session("finish", "listen to the playing song to the end").add_argument("handle")
    q = with_session("skip", "skip the playing song")
    q.add_argument("handle")
    q.add_argument("--played", type=float, default=0.2, help="share heard before skipping")
    q = with_session("feedback", "say how well a song fit what you wanted")
    q.add_argument("handle")
    q.add_argument("--fit", type=int, required=True, choices=range(1, 6), metavar="1-5")
    q.add_argument("--reason", required=True)
    # The listener's own call, for score_dj.py's steer tests: which of the
    # two vibes it was told about this song belongs to. Stored, never printed.
    q.add_argument("--vibe", choices=("A", "B", "other"),
                   help="which of the two vibes a steer test named this song belongs to")
    with_session("review", "your overall review of the session").add_argument("text")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        if a.cmd == "start":
            lines = cmd_start(a)
        else:
            st = _load(a.session)
            s = _session(st)
            lines = COMMANDS[a.cmd](a, st, s)
            _timed(st, s)
            st["session"] = s.to_state()
            _save(st)
    except UserError as e:
        print(f"error: {e}")
        return 2
    except ApiError as e:
        # The status only: an error body could carry anything.
        print(f"error: the app answered HTTP {e.status}")
        return 1
    except requests.RequestException:
        print("error: the app is not reachable; is the server running?")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
