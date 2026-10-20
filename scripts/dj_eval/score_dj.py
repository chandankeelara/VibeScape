"""
One number for a DJ version: how fast it catches a listener and how well it
holds them, over the catch-and-hold scenarios in catch_hold.json, kept on a
scoreboard (scoreboard.json, committed) with the best score so far.

    python scripts/dj_eval/score_dj.py --label v4 --commit HEAD \\
        --session kn-melody-on=kannada-late-night-84b7e7 --session kn-melody-on=... ...
    python scripts/dj_eval/score_dj.py --manifest round.json          # same, from a file
    python scripts/dj_eval/score_dj.py ... --dry-run                  # table only, no entry
    python scripts/dj_eval/score_dj.py --render-prompts               # rewrite the stored prompts

A manifest is {"label", "commit", "prod_revision" (optional), "sessions":
{scenario: [session id, ...]}}; command-line flags override its label,
commit and revision and add to its sessions. A scenario given two or more
sessions has that many runs. Session files are listener.py's state files,
read from --listener-dir (default: data/dj_eval/listener/ of the checkout
this script is in; the listener agents write to the main checkout's).

SCORE, per run, from the `steps` (15) DJ picks only, the start song aside:
  catch_step  the first DJ step s whose picks s, s+1 and s+2 all have fit >= 4;
              none (never caught) when there is no such run of three.
  catch       50 * (steps + 1 - catch_step) / steps: 50 at step 1, 10 at 13.
  hold        50 * (picks from catch_step on with fit >= 4) / (picks from
              catch_step on). The catching three count. 0 when never caught.
  lost        picks after the catch with fit <= 2. Reported, not scored.
  total       catch + hold, 0 to 100.
A scenario's score is the mean over its runs; 'on', 'off' and overall are
means over scenarios.

VALIDATION. A run counts only if it is exactly what the prompt asks: the
right account, one `search` play first, then exactly `steps` `dj` plays, no
up-next (`picked`) play, every play ended and rated 1-5. Anything else is
printed as EXCLUDED and left out; a scenario left with no run stops the
script unless --allow-missing.

BEST. n is small and an LLM listener is noisy, so an entry with fewer than
2 valid runs for any scenario (or a missing scenario) is provisional: it is
recorded and compared, but cannot become the best. The noise band of an
entry is the max-min range of its overall score across run indices (the
overall of every scenario's first run, of every second run, ...); a gap to
the best within the larger of the two entries' bands is called noise.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/

from dj_eval.listener import STATE_DIR  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[2]
SCENARIOS = Path(__file__).resolve().with_name("catch_hold.json")
SCOREBOARD = Path(__file__).resolve().with_name("scoreboard.json")
PERSONAS = Path(__file__).resolve().with_name("personas.json")

GOOD_FIT = 4          # a pick that fits: 4 or 5 of 5
LOST_FIT = 2          # a pick that loses the listener: 1 or 2
CATCH_RUN = 3         # consecutive good picks that make a catch
HALF = 50.0           # points for catch, and for hold
MIN_RUNS_FOR_BEST = 2


class ScoreError(Exception):
    """A usage or data error; printed as is, exit 2."""


# ------------------------------------------------------------- scenarios
def _song(sc: dict) -> str:
    s = f'"{sc["title"]}"'
    if sc.get("artist"):
        s += f" by {sc['artist']}"
    if sc.get("album"):
        s += f" ({sc['album']})"
    return s


def render_prompt(cfg: dict, sc: dict) -> str:
    return "\n".join(cfg["prompt_template"]).format(
        target=sc["target"], account=sc["account"], query=sc["query"], song=_song(sc),
        steps=cfg["steps"])


def check_scenarios(cfg: dict, personas: set[str] | None = None) -> None:
    ids = [sc["id"] for sc in cfg["scenarios"]]
    if len(set(ids)) != len(ids):
        raise ScoreError(f"duplicate scenario ids in {SCENARIOS.name}: {ids}")
    accounts = [sc["account"] for sc in cfg["scenarios"]]
    if len(set(accounts)) != len(accounts):
        raise ScoreError("two scenarios share an account; concurrent sessions on one account "
                         f"reset each other's history: {accounts}")
    for sc in cfg["scenarios"]:
        if sc["kind"] not in ("on", "off"):
            raise ScoreError(f"{sc['id']}: kind must be 'on' or 'off', not {sc['kind']!r}")
        if personas is not None and sc["account"] not in personas:
            raise ScoreError(f"{sc['id']}: account {sc['account']!r} is not in personas.json")


def load_scenarios(path: Path = SCENARIOS) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    personas = {p["name"] for p in json.loads(PERSONAS.read_text(encoding="utf-8"))}
    check_scenarios(cfg, personas)
    return cfg


# ----------------------------------------------------------------- score
def catch_hold(fits: list[int], steps: int) -> dict:
    """Score one run from its DJ picks' fits, step 1 first."""
    if len(fits) != steps:
        raise ValueError(f"{len(fits)} fits for {steps} steps")
    catch_step = next((s for s in range(1, steps - CATCH_RUN + 2)
                       if all(f >= GOOD_FIT for f in fits[s - 1:s - 1 + CATCH_RUN])), None)
    if catch_step is None:
        return {"catch_step": None, "catch": 0.0, "hold": 0.0, "lost": 0, "total": 0.0}
    after = fits[catch_step - 1:]
    catch = HALF * (steps + 1 - catch_step) / steps
    hold = HALF * sum(f >= GOOD_FIT for f in after) / len(after)
    lost = sum(f <= LOST_FIT for f in after)
    return {"catch_step": catch_step, "catch": catch, "hold": hold, "lost": lost,
            "total": catch + hold}


def validate(st: dict, sc: dict, steps: int) -> tuple[list[str], list[str]]:
    """(problems that exclude the run, warnings that do not)."""
    bad, warn = [], []
    if st.get("persona") != sc["account"]:
        bad.append(f"account {st.get('persona')!r}, scenario wants {sc['account']!r}")
    plays = st.get("plays") or []
    hows = [p.get("how") for p in plays]
    n_search, n_dj, n_pick = hows.count("search"), hows.count("dj"), hows.count("picked")
    if n_search != 1:
        bad.append(f"{n_search} search plays, want 1")
    elif hows[0] != "search":
        bad.append("the search play is not the first play")
    if n_dj != steps:
        bad.append(f"{n_dj} DJ plays, want {steps}")
    if n_pick:
        bad.append(f"{n_pick} up-next plays, want 0")
    other = len(hows) - n_search - n_dj - n_pick
    if other:
        bad.append(f"{other} plays of another kind")
    for p in plays:
        if p.get("outcome") not in ("finished", "skipped"):
            bad.append(f"step {p.get('step')} has no outcome")
        if p.get("fit") not in (1, 2, 3, 4, 5):
            bad.append(f"step {p.get('step')} has no fit 1-5")
    if plays and plays[0].get("how") == "search":
        start = plays[0]
        if sc["title"].casefold() not in (start.get("title") or "").casefold():
            warn.append(f"start song is {start.get('title')!r}, scenario wants {sc['title']!r}")
        if isinstance(start.get("fit"), int) and abs(start["fit"] - sc["start_fit"]) > 1:
            warn.append(f"start song rated {start['fit']}, scenario expects {sc['start_fit']}")
    return bad, warn


def _overall(by_scenario: dict, kinds: dict, kind: str | None = None) -> float | None:
    xs = [v for k, v in by_scenario.items() if kind is None or kinds[k] == kind]
    return statistics.fmean(xs) if xs else None


def score_round(cfg: dict, runs: dict[str, list[dict]]) -> dict:
    """runs: scenario id -> session states. Returns the scored round: per
    scenario, on / off / overall, the noise band, exclusions, provisional."""
    steps = cfg["steps"]
    scs = {sc["id"]: sc for sc in cfg["scenarios"]}
    unknown = set(runs) - set(scs)
    if unknown:
        raise ScoreError(f"unknown scenario(s): {', '.join(sorted(unknown))}; "
                         f"one of {', '.join(scs)}")
    seen: dict[str, str] = {}
    for sid_scen, sts in runs.items():
        for st in sts:
            if st["id"] in seen:
                raise ScoreError(f"session {st['id']} is given twice "
                                 f"({seen[st['id']]} and {sid_scen})")
            seen[st["id"]] = sid_scen
    kinds = {k: sc["kind"] for k, sc in scs.items()}
    out, excluded, warnings, provisional = {}, [], [], []
    for sid, sc in scs.items():
        scored = []
        for st in runs.get(sid, []):
            bad, warn = validate(st, sc, steps)
            warnings += [f"{sid} {st['id']}: {w}" for w in warn]
            if bad:
                excluded.append({"scenario": sid, "session": st["id"], "reasons": bad})
                continue
            fits = [p["fit"] for p in st["plays"] if p["how"] == "dj"]
            scored.append({"session": st["id"], **catch_hold(fits, steps)})
        if not scored:
            provisional.append(f"{sid}: no valid run")
            continue
        if len(scored) < MIN_RUNS_FOR_BEST:
            provisional.append(f"{sid}: {len(scored)} valid run, want {MIN_RUNS_FOR_BEST}")
        totals = [r["total"] for r in scored]
        out[sid] = {"kind": sc["kind"], "runs": scored,
                    "catch_step": [r["catch_step"] for r in scored],
                    "catch": statistics.fmean(r["catch"] for r in scored),
                    "hold": statistics.fmean(r["hold"] for r in scored),
                    "lost": statistics.fmean(r["lost"] for r in scored),
                    "total": statistics.fmean(totals),
                    "spread": [min(totals), max(totals)]}
    means = {k: v["total"] for k, v in out.items()}
    # The noise band: the overall of every scenario's i-th run, for each i
    # every present scenario has.
    depth = min((len(v["runs"]) for v in out.values()), default=0)
    per_index = [statistics.fmean(v["runs"][i]["total"] for v in out.values())
                 for i in range(depth)]
    return {"scenarios": out,
            "on": _overall(means, kinds, "on"), "off": _overall(means, kinds, "off"),
            "overall": _overall(means, kinds),
            "overall_band": (max(per_index) - min(per_index)) if len(per_index) >= 2 else None,
            "min_runs": min((len(out[k]["runs"]) if k in out else 0 for k in scs), default=0),
            "missing": [k for k in scs if k not in out],
            "provisional": provisional, "excluded": excluded, "warnings": warnings}


# ------------------------------------------------------------ scoreboard
def _round(x, d=1):
    return None if x is None else round(x, d)


def make_entry(cfg: dict, result: dict, label: str, commit: str, prod_revision: str | None,
               sessions: dict[str, list[str]], date: str) -> dict:
    return {
        "label": label, "date": date, "commit": commit, "prod_revision": prod_revision,
        "scenarios_version": cfg["version"], "steps": cfg["steps"],
        "provisional": bool(result["provisional"]), "provisional_why": result["provisional"],
        "min_runs": result["min_runs"],
        "on": _round(result["on"]), "off": _round(result["off"]),
        "overall": _round(result["overall"]), "overall_band": _round(result["overall_band"]),
        "scenarios": {k: {"kind": v["kind"], "runs": len(v["runs"]),
                          "catch_step": v["catch_step"], "catch": _round(v["catch"]),
                          "hold": _round(v["hold"]), "lost": _round(v["lost"]),
                          "total": _round(v["total"]),
                          "spread": [_round(x) for x in v["spread"]]}
                      for k, v in result["scenarios"].items()},
        "sessions": sessions,
        "excluded": result["excluded"],
    }


def best_of(entries: list[dict], version: int) -> dict | None:
    """The highest overall among non-provisional entries of this scenario
    version; the earlier entry wins a tie."""
    best = None
    for e in entries:
        if e["provisional"] or e["scenarios_version"] != version or e["overall"] is None:
            continue
        if best is None or e["overall"] > best["overall"]:
            best = e
    return best


def _best_ref(e: dict | None) -> dict | None:
    if e is None:
        return None
    return {"label": e["label"], "overall": e["overall"], "date": e["date"],
            "commit": e["commit"], "scenarios_version": e["scenarios_version"]}


def add_entry(board: dict, entry: dict, replace: bool = False) -> tuple[dict, list[str]]:
    """Append (or with replace, swap in) an entry and recompute `best`.
    Returns the board and the lines comparing the entry with the previous best."""
    entries = list(board.get("entries", []))
    at = next((i for i, e in enumerate(entries) if e["label"] == entry["label"]), None)
    if at is not None and not replace:
        raise ScoreError(f"label {entry['label']!r} is already on the scoreboard; "
                         "pass --replace to overwrite it")
    others = [e for e in entries if e["label"] != entry["label"]]
    prev = best_of(others, entry["scenarios_version"])
    lines = compare(entry, prev)
    if at is None:
        entries.append(entry)
    else:
        entries[at] = entry
    return {**board, "entries": entries,
            "best": _best_ref(best_of(entries, entry["scenarios_version"]))}, lines


def compare(entry: dict, best: dict | None) -> list[str]:
    tag = " (provisional: fewer than 2 runs per scenario, cannot set the best)" \
        if entry["provisional"] else ""
    if entry["overall"] is None:
        return ["no score" + tag]
    if best is None:
        return [("NEW BEST" if not entry["provisional"] else "no best yet") + tag]
    if not entry["provisional"] and entry["overall"] > best["overall"]:
        return [f"NEW BEST: {entry['overall']:.1f}, +{entry['overall'] - best['overall']:.1f} "
                f"over {best['label']}{_noise(entry, best)}"]
    gap = best["overall"] - entry["overall"]
    sign = "−" if gap > 0 else "+"
    return [f"Δ vs best ({best['label']}, {best['overall']:.1f}): {sign}{abs(gap):.1f}"
            f"{_noise(entry, best)}{tag}"]


def _noise(entry: dict, best: dict) -> str:
    bands = [b for b in (entry.get("overall_band"), best.get("overall_band")) if b is not None]
    if not bands:
        return "; run-to-run spread unknown (no multi-run entry)"
    band, gap = max(bands), abs(entry["overall"] - best["overall"])
    return (f"; within the run-to-run spread ({band:.1f})" if gap <= band
            else f"; outside the run-to-run spread ({band:.1f})")


def load_board(path: Path) -> dict:
    if not path.exists():
        return {"about": "", "best": None, "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- output
def _f(x, d=1):
    return "-" if x is None else f"{x:.{d}f}"


def markdown(entry: dict, comparison: list[str], missing: list[str]) -> str:
    """The table, from the entry as stored, so it shows the scoreboard's numbers."""
    L = [f"## DJ score: {entry['label']}\n",
         f"commit `{entry['commit']}`" + (f", prod revision `{entry['prod_revision']}`"
                                          if entry["prod_revision"] else "")
         + f", {entry['date']}, scenarios v{entry['scenarios_version']}, "
           f"{entry['steps']} DJ steps\n",
         "| scenario | kind | runs | catch step | catch | hold | lost | total | spread |",
         "|---|---|---|---|---|---|---|---|---|"]
    for k, v in entry["scenarios"].items():
        steps = ", ".join("never" if s is None else str(s) for s in v["catch_step"])
        spread = f"{v['spread'][0]:.1f}–{v['spread'][1]:.1f}" if v["runs"] > 1 else "-"
        L.append(f"| {k} | {v['kind']} | {v['runs']} | {steps} | {_f(v['catch'])} | "
                 f"{_f(v['hold'])} | {_f(v['lost'])} | {_f(v['total'])} | {spread} |")
    for k in missing:
        L.append(f"| {k} | | 0 | | | | | MISSING | |")
    L += ["", "| | score |", "|---|---|",
          f"| on | {_f(entry['on'])} |", f"| off | {_f(entry['off'])} |",
          f"| **overall** | **{_f(entry['overall'])}** |",
          f"| run-to-run spread | {_f(entry['overall_band'])} |", ""]
    if entry["provisional"]:
        L.append("PROVISIONAL: " + "; ".join(entry["provisional_why"]))
    L += comparison
    return "\n".join(L)


# ------------------------------------------------------------------ main
def _resolve_commit(ref: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(_REPO), "rev-parse", "--verify", "--short=12",
                            f"{ref}^{{commit}}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ref
    if r.returncode != 0:
        print(f"warning: git cannot resolve {ref!r}; recorded as given", file=sys.stderr)
        return ref
    return r.stdout.strip()


def _load_session(listener_dir: Path, sid: str) -> dict:
    if not sid or any(c in sid for c in "/\\:") or sid.startswith("."):
        raise ScoreError(f"bad session id {sid!r}")
    p = listener_dir / f"{sid}.json"
    if not p.exists():
        raise ScoreError(f"no session file {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def render_prompts(path: Path = SCENARIOS) -> int:
    cfg = load_scenarios(path)
    for sc in cfg["scenarios"]:
        sc["prompt"] = render_prompt(cfg, sc)
    save_json(path, cfg)
    print(f"rendered {len(cfg['scenarios'])} prompts into {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", help="this DJ version's name on the scoreboard")
    ap.add_argument("--commit", help="git commit (or ref) of the DJ code that served the round")
    ap.add_argument("--prod-revision", help="Cloud Run revision, if the code is deployed")
    ap.add_argument("--session", action="append", default=[], metavar="SCENARIO=SESSION_ID",
                    help="a run of a scenario (repeatable; repeat a scenario for more runs)")
    ap.add_argument("--manifest", type=Path, help="JSON: label, commit, prod_revision, sessions")
    ap.add_argument("--listener-dir", type=Path, default=STATE_DIR)
    ap.add_argument("--scoreboard", type=Path, default=SCOREBOARD)
    ap.add_argument("--scenarios", type=Path, default=SCENARIOS)
    ap.add_argument("--replace", action="store_true", help="overwrite an entry with this label")
    ap.add_argument("--allow-missing", action="store_true",
                    help="record even if a scenario has no valid run (provisional)")
    ap.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    ap.add_argument("--render-prompts", action="store_true",
                    help="rewrite every scenario's prompt from the template and exit")
    a = ap.parse_args(argv)
    try:
        if a.render_prompts:
            return render_prompts(a.scenarios)
        cfg = load_scenarios(a.scenarios)
        man = json.loads(a.manifest.read_text(encoding="utf-8")) if a.manifest else {}
        label = a.label or man.get("label")
        commit = a.commit or man.get("commit")
        if not label or not commit:
            raise ScoreError("--label and --commit are required (or in the manifest)")
        sessions: dict[str, list[str]] = {k: list(v) for k, v in man.get("sessions", {}).items()}
        for arg in a.session:
            scen, sep, sid = arg.partition("=")
            if not sep or not scen or not sid:
                raise ScoreError(f"--session wants SCENARIO=SESSION_ID, not {arg!r}")
            sessions.setdefault(scen, []).append(sid)
        if not sessions:
            raise ScoreError("no sessions: pass --session or --manifest")
        runs = {k: [_load_session(a.listener_dir, s) for s in v] for k, v in sessions.items()}
        result = score_round(cfg, runs)
        for w in result["warnings"]:
            print(f"warning: {w}", file=sys.stderr)
        if result["excluded"]:
            print("\n" + "!" * 72, file=sys.stderr)
            for x in result["excluded"]:
                print(f"EXCLUDED {x['scenario']} {x['session']}: {'; '.join(x['reasons'])}",
                      file=sys.stderr)
            print("!" * 72 + "\n", file=sys.stderr)
        if result["missing"] and not a.allow_missing:
            raise ScoreError(f"no valid run for {', '.join(result['missing'])}; nothing recorded "
                             "(--allow-missing records a provisional entry)")
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = make_entry(cfg, result, label, _resolve_commit(commit),
                           a.prod_revision or man.get("prod_revision"), sessions, date)
        board = load_board(a.scoreboard)
        new_board, comparison = add_entry(board, entry, replace=a.replace or a.dry_run)
        print(markdown(entry, comparison, result["missing"]))
        if a.dry_run:
            print("\n(dry run: scoreboard not written)")
        else:
            save_json(a.scoreboard, new_board)
            print(f"\n-> {a.scoreboard}")
    except ScoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
