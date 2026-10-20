"""
One number for a DJ version, from LLM listeners on the fixed scenarios in
scenarios.json, kept on a scoreboard (scoreboard.json, committed) with the
best score so far.

    python scripts/dj_eval/score_dj.py --label v4 --commit HEAD \\
        --session vibe-kn-melody=kannada-late-night-84b7e7 --session vibe-kn-melody=... ...
    python scripts/dj_eval/score_dj.py --manifest round.json          # same, from a file
    python scripts/dj_eval/score_dj.py ... --dry-run                  # table only, no entry
    python scripts/dj_eval/score_dj.py --render-prompts               # rewrite the stored prompts

A manifest is {"label", "commit", "prod_revision" (optional), "sessions":
{scenario: [session id, ...]}}; command-line flags override its label,
commit and revision and add to its sessions. A scenario given two or more
sessions has that many runs. Session files are listener.py's state files,
read from --listener-dir (default: data/dj_eval/listener/ of the checkout
this script is in; the listener agents write to the main checkout's).

The DJ knows only what the listener does: searches, plays, finishes and
skips. So a test never asks it to guess a vibe it was not shown. Two kinds:

VIBE (one start song in the target vibe, then 15 DJ picks judged against it)
  hold           share of the 15 with fit >= 4
  skip_rate      share skipped
  skip_recovery  of the picks right after a skipped pick, the share finished;
                 None when no pick follows a skip
  early          share of fit >= 4 in picks 1-3
  score          VIBE_WEIGHTS: 50 hold + 25 (1 - skip_rate) + 15 skip_recovery
                 + 10 early. With no skip_recovery its 15 is left out and the
                 rest scaled to 100, so a run without skips is neither paid
                 nor fined for it.

STEER (song A, 5 DJ picks judged against vibe A; then a search for song B,
       10 DJ picks judged against vibe B)
  hold_a         share of the 5 A picks with fit >= 4
  switch_step    the first B pick s where B picks s, s+1, s+2 were all
                 finished; None if never
  switch         (11 - switch_step) / 10: 1 at step 1, 0.3 at 8, 0 if never
  hold_b         share of fit >= 4 among B picks from switch_step on; 0 if never
  leak           share of the 10 B picks the listener tagged `--vibe A`
  score          STEER_WEIGHTS: 15 hold_a + 35 switch + 40 hold_b + 10 (1 - leak)
  Leak is the listener's explicit tag, not fit <= 2: a B pick can be wrong
  without being A (another language, a third mood), and leak is meant to
  count the DJ holding on to A.

A scenario's score is the mean over its runs; 'vibe', 'steer' and the
overall DJ score are means over scenarios.

VALIDATION. A run counts only if its plays are exactly the scenario's
layout: per phase, one `search` play of that phase's song (one of the
song's `spotify_ids`), finished, then that phase's number of `dj` plays,
every play ended and rated 1-5, and in a steer test every B pick tagged A,
B or other. An up-next (`picked`) play, a search too early or too late, too
few plays, another song as a start song or the wrong account exclude the
run, printed as EXCLUDED. Plays after the last expected one (agents
overshoot) cannot change the ones before and are ignored with a warning. A
scenario left with no run stops the script unless --allow-missing.

BEST. n is small and an LLM listener is noisy, so an entry with fewer than
2 valid runs for any scenario (or a missing scenario) is provisional: it is
recorded and compared, but cannot become the best. The best is kept per
scenario version. The noise band of an entry is the max-min range of its
overall score across run indices (the overall of every scenario's first
run, of every second run, ...); a gap to the best within the larger of the
two entries' bands is called noise.
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
SCENARIOS = Path(__file__).resolve().with_name("scenarios.json")
SCOREBOARD = Path(__file__).resolve().with_name("scoreboard.json")
PERSONAS = Path(__file__).resolve().with_name("personas.json")

GOOD_FIT = 4          # a pick that fits: 4 or 5 of 5
RUN = 3               # finished picks in a row that make a switch; picks that make 'early'
VIBE_WEIGHTS = {"hold": 50, "no_skip": 25, "recovery": 15, "early": 10}
STEER_WEIGHTS = {"hold_a": 15, "switch": 35, "hold_b": 40, "no_leak": 10}
SOUNDS_LIKE = ("A", "B", "other")
MIN_RUNS_FOR_BEST = 2
PHASES = {"vibe": 1, "steer": 2}


class ScoreError(Exception):
    """A usage or data error; printed as is, exit 2."""


# ------------------------------------------------------------- scenarios
def _song(cfg: dict, key: str) -> str:
    s = cfg["songs"][key]
    return f'`{s["title"]} — {s["artist"]}` (as the search lists it)'


def render_prompt(cfg: dict, sc: dict) -> str:
    tpl = "\n".join(cfg["prompt_templates"][sc["kind"]])
    ph = sc["phases"]
    if sc["kind"] == "vibe":
        p = ph[0]
        return tpl.format(account=sc["account"], target=cfg["vibes"][p["vibe"]],
                          query=cfg["songs"][p["song"]]["query"], song=_song(cfg, p["song"]),
                          picks=p["picks"])
    a, b = ph
    return tpl.format(account=sc["account"],
                      target_a=cfg["vibes"][a["vibe"]], target_b=cfg["vibes"][b["vibe"]],
                      query_a=cfg["songs"][a["song"]]["query"], song_a=_song(cfg, a["song"]),
                      query_b=cfg["songs"][b["song"]]["query"], song_b=_song(cfg, b["song"]),
                      picks_a=a["picks"], picks_b=b["picks"])


def check_scenarios(cfg: dict, personas: set[str] | None = None) -> None:
    ids = [sc["id"] for sc in cfg["scenarios"]]
    if len(set(ids)) != len(ids):
        raise ScoreError(f"duplicate scenario ids: {ids}")
    accounts = [sc["account"] for sc in cfg["scenarios"]]
    if len(set(accounts)) != len(accounts):
        raise ScoreError("two scenarios share an account; concurrent sessions on one account "
                         f"reset each other's history: {accounts}")
    for sc in cfg["scenarios"]:
        if sc["kind"] not in PHASES:
            raise ScoreError(f"{sc['id']}: kind must be one of {list(PHASES)}, not {sc['kind']!r}")
        if len(sc["phases"]) != PHASES[sc["kind"]]:
            raise ScoreError(f"{sc['id']}: a {sc['kind']} test has {PHASES[sc['kind']]} phase(s)")
        for p in sc["phases"]:
            if p["vibe"] not in cfg["vibes"] or p["song"] not in cfg["songs"]:
                raise ScoreError(f"{sc['id']}: unknown vibe or song in {p}")
            if p["picks"] < RUN:
                raise ScoreError(f"{sc['id']}: a phase needs at least {RUN} picks")
        if personas is not None and sc["account"] not in personas:
            raise ScoreError(f"{sc['id']}: account {sc['account']!r} is not in personas.json")


def load_scenarios(path: Path = SCENARIOS) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    personas = {p["name"] for p in json.loads(PERSONAS.read_text(encoding="utf-8"))}
    check_scenarios(cfg, personas)
    return cfg


# ----------------------------------------------------------------- score
def weighted(parts: dict, weights: dict) -> float:
    """100 x the weighted mean of parts in 0..1. A part that is None is
    left out and the other weights scaled up, so it neither pays nor fines."""
    used = {k: w for k, w in weights.items() if parts[k] is not None}
    return 100 * sum(w * parts[k] for k, w in used.items()) / sum(used.values())


def _good(p: dict) -> bool:
    return p["fit"] >= GOOD_FIT


def _finished(p: dict) -> bool:
    return p["outcome"] == "finished"


def vibe_metrics(picks: list[dict]) -> dict:
    n = len(picks)
    skipped = [not _finished(p) for p in picks]
    after_skip = [_finished(picks[i + 1]) for i in range(n - 1) if skipped[i]]
    m = {"hold": sum(map(_good, picks)) / n,
         "skip_rate": sum(skipped) / n,
         "skip_recovery": sum(after_skip) / len(after_skip) if after_skip else None,
         "early": sum(map(_good, picks[:RUN])) / RUN}
    m["score"] = weighted({"hold": m["hold"], "no_skip": 1 - m["skip_rate"],
                           "recovery": m["skip_recovery"], "early": m["early"]}, VIBE_WEIGHTS)
    return m


def steer_metrics(a_picks: list[dict], b_picks: list[dict]) -> dict:
    nb = len(b_picks)
    fin = [_finished(p) for p in b_picks]
    step = next((s for s in range(1, nb - RUN + 2) if all(fin[s - 1:s - 1 + RUN])), None)
    after = b_picks[step - 1:] if step else []
    m = {"hold_a": sum(map(_good, a_picks)) / len(a_picks),
         "switch_step": step,
         "switch": (nb + 1 - step) / nb if step else 0.0,
         "hold_b": sum(map(_good, after)) / len(after) if after else 0.0,
         "leak": sum(p.get("sounds_like") == "A" for p in b_picks) / nb}
    m["score"] = weighted({"hold_a": m["hold_a"], "switch": m["switch"], "hold_b": m["hold_b"],
                           "no_leak": 1 - m["leak"]}, STEER_WEIGHTS)
    return m


def layout(sc: dict) -> list[tuple[int, str]]:
    """(phase index, how) for every expected play, in order."""
    out = []
    for i, p in enumerate(sc["phases"]):
        out += [(i, "search")] + [(i, "dj")] * p["picks"]
    return out


def phase_picks(st: dict, sc: dict) -> list[list[dict]]:
    """The DJ plays of each phase, from the expected window only."""
    plays = st.get("plays") or []
    out = [[] for _ in sc["phases"]]
    for p, (i, how) in zip(plays, layout(sc)):
        if how == "dj":
            out[i].append(p)
    return out


def validate(st: dict, sc: dict, cfg: dict) -> tuple[list[str], list[str]]:
    """(problems that exclude the run, warnings that do not)."""
    bad, warn = [], []
    if st.get("persona") != sc["account"]:
        bad.append(f"account {st.get('persona')!r}, scenario wants {sc['account']!r}")
    plays = st.get("plays") or []
    want = layout(sc)
    window, extra = plays[:len(want)], plays[len(want):]
    if len(window) < len(want):
        bad.append(f"{len(window)} plays, want {len(want)} "
                   f"({' + '.join(f'1 search + {p["picks"]} DJ' for p in sc['phases'])})")
    wrong = [(n, p.get("how"), how) for n, (p, (_, how)) in enumerate(zip(window, want), 1)
             if p.get("how") != how]
    if wrong:
        n, got, how = wrong[0]
        bad.append(f"play {n} is a {got!r} play, want {how!r}"
                   + (f" ({len(wrong)} plays out of place)" if len(wrong) > 1 else ""))
    for p, (i, how) in zip(window, want):
        if p.get("outcome") not in ("finished", "skipped"):
            bad.append(f"play {p.get('step')} has no outcome")
        if p.get("fit") not in (1, 2, 3, 4, 5):
            bad.append(f"play {p.get('step')} has no fit 1-5")
        if how == "search" and p.get("how") == "search":
            song = cfg["songs"][sc["phases"][i]["song"]]
            if p.get("spotify_id") not in song["spotify_ids"]:
                bad.append(f"play {p.get('step')} is {p.get('title')!r} by {p.get('artist')!r}, "
                           f"want {song['title']!r} by {song['artist']!r}")
            if p.get("outcome") == "skipped":
                bad.append(f"start song at play {p.get('step')} was skipped, want finished")
        if (sc["kind"] == "steer" and i == 1 and how == "dj"
                and p.get("sounds_like") not in SOUNDS_LIKE):
            bad.append(f"B pick at play {p.get('step')} has no --vibe A|B|other")
    if extra:
        hows = [p.get("how") for p in extra]
        kinds = ", ".join(f"{h} x{hows.count(h)}" for h in sorted(set(hows), key=str))
        warn.append(f"{len(extra)} plays after play {len(want)} ignored ({kinds})")
    return bad, warn


def score_run(st: dict, sc: dict) -> dict:
    picks = phase_picks(st, sc)
    return vibe_metrics(picks[0]) if sc["kind"] == "vibe" else steer_metrics(*picks)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


METRICS = {"vibe": ("hold", "skip_rate", "skip_recovery", "early"),
           "steer": ("hold_a", "switch", "hold_b", "leak")}


def score_round(cfg: dict, runs: dict[str, list[dict]]) -> dict:
    """runs: scenario id -> session states. Returns the scored round: per
    scenario, vibe / steer / overall, the noise band, exclusions, provisional."""
    scs = {sc["id"]: sc for sc in cfg["scenarios"]}
    unknown = set(runs) - set(scs)
    if unknown:
        raise ScoreError(f"unknown scenario(s): {', '.join(sorted(unknown))}; "
                         f"one of {', '.join(scs)}")
    seen: dict[str, str] = {}
    for scen, sts in runs.items():
        for st in sts:
            if st["id"] in seen:
                raise ScoreError(f"session {st['id']} is given twice "
                                 f"({seen[st['id']]} and {scen})")
            seen[st["id"]] = scen
    out, excluded, warnings, provisional = {}, [], [], []
    for sid, sc in scs.items():
        scored = []
        for st in runs.get(sid, []):
            bad, warn = validate(st, sc, cfg)
            warnings += [f"{sid} {st['id']}: {w}" for w in warn]
            if bad:
                excluded.append({"scenario": sid, "session": st["id"], "reasons": bad})
                continue
            scored.append({"session": st["id"], **score_run(st, sc)})
        if not scored:
            provisional.append(f"{sid}: no valid run")
            continue
        if len(scored) < MIN_RUNS_FOR_BEST:
            provisional.append(f"{sid}: {len(scored)} valid run, want {MIN_RUNS_FOR_BEST}")
        scores = [r["score"] for r in scored]
        v = {"kind": sc["kind"], "runs": scored, "score": statistics.fmean(scores),
             "spread": [min(scores), max(scores)]}
        v.update({k: _mean(r[k] for r in scored) for k in METRICS[sc["kind"]]})
        if sc["kind"] == "steer":
            v["switch_step"] = [r["switch_step"] for r in scored]
        out[sid] = v
    kinds = {k: v["kind"] for k, v in out.items()}
    means = {k: v["score"] for k, v in out.items()}
    # The noise band: the overall of every scenario's i-th run, for each i
    # every present scenario has.
    depth = min((len(v["runs"]) for v in out.values()), default=0)
    per_index = [statistics.fmean(v["runs"][i]["score"] for v in out.values())
                 for i in range(depth)]
    return {"scenarios": out,
            "vibe": _mean(m for k, m in means.items() if kinds[k] == "vibe"),
            "steer": _mean(m for k, m in means.items() if kinds[k] == "steer"),
            "overall": _mean(means.values()),
            "overall_band": (max(per_index) - min(per_index)) if len(per_index) >= 2 else None,
            "min_runs": min((len(out[k]["runs"]) if k in out else 0 for k in scs), default=0),
            "missing": [k for k in scs if k not in out],
            "provisional": provisional, "excluded": excluded, "warnings": warnings}


# ------------------------------------------------------------ scoreboard
def _round(x, d=1):
    return None if x is None else round(x, d)


def make_entry(cfg: dict, result: dict, label: str, commit: str, prod_revision: str | None,
               sessions: dict[str, list[str]], date: str) -> dict:
    scen = {}
    for k, v in result["scenarios"].items():
        e = {"kind": v["kind"], "runs": len(v["runs"]), "score": _round(v["score"]),
             "spread": [_round(x) for x in v["spread"]]}
        e.update({m: _round(v[m], 2) for m in METRICS[v["kind"]]})
        if "switch_step" in v:
            e["switch_step"] = v["switch_step"]
        scen[k] = e
    return {
        "label": label, "date": date, "commit": commit, "prod_revision": prod_revision,
        "scenarios_version": cfg["version"],
        "provisional": bool(result["provisional"]), "provisional_why": result["provisional"],
        "min_runs": result["min_runs"],
        "vibe": _round(result["vibe"]), "steer": _round(result["steer"]),
        "overall": _round(result["overall"]), "overall_band": _round(result["overall_band"]),
        "scenarios": scen, "sessions": sessions, "excluded": result["excluded"],
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
    lines = compare(entry, best_of(others, entry["scenarios_version"]))
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


def _spread(v: dict) -> str:
    return f"{v['spread'][0]:.1f}–{v['spread'][1]:.1f}" if v["runs"] > 1 else "-"


def markdown(entry: dict, comparison: list[str], missing: list[str]) -> str:
    """The tables, from the entry as stored, so they show the scoreboard's numbers."""
    sc = entry["scenarios"]
    L = [f"## DJ score: {entry['label']}\n",
         f"commit `{entry['commit']}`" + (f", prod revision `{entry['prod_revision']}`"
                                          if entry["prod_revision"] else "")
         + f", {entry['date']}, scenarios v{entry['scenarios_version']}\n",
         "### Vibe\n",
         "| scenario | runs | hold | skip rate | skip recovery | early | score | spread |",
         "|---|---|---|---|---|---|---|---|"]
    for k, v in sc.items():
        if v["kind"] == "vibe":
            L.append(f"| {k} | {v['runs']} | {_f(v['hold'], 2)} | {_f(v['skip_rate'], 2)} | "
                     f"{_f(v['skip_recovery'], 2)} | {_f(v['early'], 2)} | {_f(v['score'])} | "
                     f"{_spread(v)} |")
    L += ["", "### Steer\n",
          "| scenario | runs | hold A | switch step | switch | hold B | leak | score | spread |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k, v in sc.items():
        if v["kind"] == "steer":
            steps = ", ".join("never" if s is None else str(s) for s in v["switch_step"])
            L.append(f"| {k} | {v['runs']} | {_f(v['hold_a'], 2)} | {steps} | "
                     f"{_f(v['switch'], 2)} | {_f(v['hold_b'], 2)} | {_f(v['leak'], 2)} | "
                     f"{_f(v['score'])} | {_spread(v)} |")
    if missing:
        L += ["", "MISSING (no valid run): " + ", ".join(missing)]
    L += ["", "| | score |", "|---|---|",
          f"| vibe | {_f(entry['vibe'])} |", f"| steer | {_f(entry['steer'])} |",
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
                    help="rewrite every scenario's prompt from its template and exit")
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
