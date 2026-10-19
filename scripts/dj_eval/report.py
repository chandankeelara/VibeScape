"""
Report on listener sessions (listener.py), per persona and side by side.

    python scripts/dj_eval/report.py                    # newest session per persona
    python scripts/dj_eval/report.py --all              # every session
    python scripts/dj_eval/report.py --session kannada-mass-1a2b3c ...

Writes data/dj_eval/reports/listeners_<stamp>.md and .json. For each
session: the transcript (song, how it was reached, finished or skipped and
at what point, the listener's fit 1-5 and reason), acceptance by DJ step and
the review. Acceptance counts only what the DJ chose (`next`): a song is
accepted when the listener finished it, and separately when they rated its
fit 4 or 5.

The language and ML vibe of each song come from the session's database
copy, read here and never shown to the listener; they are in the report so
a reader can see what the DJ served without looking it up.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/

from dj_eval.listener import STATE_DIR  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = _REPO / "data" / "dj_eval" / "reports"


def load_sessions(names=None, everything=False) -> list[dict]:
    files = sorted(STATE_DIR.glob("*.json"))
    sts = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    if names:
        sts = [s for s in sts if s["id"] in set(names)]
    elif not everything:
        newest = {}
        for s in sts:
            if s["persona"] not in newest or s["started_at"] > newest[s["persona"]]["started_at"]:
                newest[s["persona"]] = s
        sts = list(newest.values())
    return sorted(sts, key=lambda s: (s["persona"], s["started_at"]))


def _facts(db: str, sids: list[str]) -> dict:
    """spotify_id -> (language, vibe_score_ml) from the copy, read-only."""
    if not sids or not Path(db).exists():
        return {}
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        ph = ",".join("?" * len(sids))
        return {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT spotify_id, language, vibe_score_ml FROM tracks WHERE spotify_id IN ({ph})",
            sids)}
    finally:
        conn.close()


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def summarise(st: dict) -> dict:
    facts = _facts(st["db"], [p["spotify_id"] for p in st["plays"]])
    plays = []
    for p in st["plays"]:
        lang, vibe = facts.get(p["spotify_id"], (None, None))
        plays.append({**p, "language": lang, "vibe_ml": vibe})
    dj = [p for p in plays if p["how"] == "dj"]
    acc = [p["outcome"] == "finished" for p in dj if p["outcome"]]   # not one left playing
    good = [p["fit"] is not None and p["fit"] >= 4 for p in dj]
    ms = sorted(ms for kind, ms in st["timings"] if kind == "similar")
    return {
        "id": st["id"], "persona": st["persona"], "started_at": st["started_at"],
        "finished": bool(st.get("review")), "review": st.get("review"),
        "plays": plays,
        "seeds": [p for p in plays if p["how"] != "dj"],
        "dj_steps": len(dj),
        "accepted_by_step": acc, "fit_by_step": [p["fit"] for p in dj],
        "finish_rate": _mean([float(x) for x in acc]),
        "fit_rate": _mean([float(x) for x in good]),
        "mean_fit": _mean([p["fit"] for p in dj]),
        "first5": _mean([float(x) for x in acc[:5]]),
        "last5": _mean([float(x) for x in acc[-5:]]) if len(acc) > 5 else None,
        "unrated": sum(p["fit"] is None for p in plays),
        "dj_languages": Counter(p["language"] or "?" for p in dj).most_common(),
        "similar_ms": {"n": len(ms), "p50": ms[len(ms) // 2] if ms else None,
                       "max": ms[-1] if ms else None},
    }


def _f(x, d=2):
    return "-" if x is None else f"{x:.{d}f}"


def _pct(x):
    return "-" if x is None else f"{100 * x:.0f}%"


def _cell(s: str) -> str:
    return (s or "").replace("|", "/").replace("\n", " ")


def write_markdown(path: Path, sums: list[dict]) -> None:
    L = [f"# DJ listener sessions, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n",
         "Acceptance counts DJ-chosen songs only. finished = the listener let it play out; "
         "fit 4+ = rated 4 or 5 of 5. Language and vibe (ML, 0-1) come from the database, "
         "not from the listener, who never saw them.\n",
         "## Side by side\n",
         "| persona | DJ songs | finished | fit 4+ | mean fit | finished, first 5 | last 5 | "
         "languages served | /similar p50 ms |",
         "|---|---|---|---|---|---|---|---|---|"]
    for s in sums:
        langs = ", ".join(f"{k}:{v}" for k, v in s["dj_languages"][:4])
        L.append(f"| {s['persona']} | {s['dj_steps']} | {_pct(s['finish_rate'])} | "
                 f"{_pct(s['fit_rate'])} | {_f(s['mean_fit'], 1)} | {_pct(s['first5'])} | "
                 f"{_pct(s['last5'])} | {langs} | {_f(s['similar_ms']['p50'], 0)} |")
    steps = max((s["dj_steps"] for s in sums), default=0)
    if steps:
        L += ["\n## By DJ step\n",
              "F = finished, S = skipped, then the fit given.\n",
              "| step | " + " | ".join(s["persona"] for s in sums) + " |",
              "|---|" + "---|" * len(sums)]
        for i in range(steps):
            row = []
            for s in sums:
                if i < len(s["accepted_by_step"]):
                    f = s["fit_by_step"][i]
                    row.append(("F" if s["accepted_by_step"][i] else "S") + (str(f) if f else ""))
                else:
                    row.append("")
            L.append(f"| {i + 1} | " + " | ".join(row) + " |")
    for s in sums:
        L += [f"\n## {s['persona']} (`{s['id']}`)\n",
              f"Started {s['started_at']}; {'reviewed' if s['finished'] else 'NOT finished (no review)'}; "
              f"{s['unrated']} plays without feedback.\n",
              "| # | via | song | language | vibe | outcome | fit | reason |",
              "|---|---|---|---|---|---|---|---|"]
        for p in s["plays"]:
            song = f"{p['title']} — {p['artist']}" + (f" [{p['album']}]" if p["album"] else "")
            out = p["outcome"] or "playing"
            if p["outcome"] == "skipped":
                out += f" at {_pct(p['played'])}"
            L.append(f"| {p['step']} | {p['how']} | {_cell(song)} | {p['language'] or '?'} | "
                     f"{_f(p['vibe_ml'])} | {out} | {p['fit'] or '-'} | {_cell(p['reason'])} |")
        L.append(f"\n**Review.** {s['review'] or '(none)'}")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="append", help="a session id (repeatable)")
    ap.add_argument("--all", action="store_true", help="every session, not the newest per persona")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    sts = load_sessions(args.session, args.all)
    if not sts:
        print(f"no sessions in {STATE_DIR}")
        return 1
    sums = [summarise(s) for s in sts]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.out.mkdir(parents=True, exist_ok=True)
    md, js = args.out / f"listeners_{stamp}.md", args.out / f"listeners_{stamp}.json"
    write_markdown(md, sums)
    js.write_text(json.dumps(sums, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(sums)} sessions\n-> {md}\n-> {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
