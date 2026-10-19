"""
End-to-end evaluation of the DJ through its real HTTP endpoint.

    # local: start uvicorn on a prepared copy (setup_copy.py), run, stop it
    python scripts/dj_eval/run_eval.py --db data/dj_eval/eval.db

    # a quick pass: two families, a few groups each
    python scripts/dj_eval/run_eval.py --db data/dj_eval/eval.db --families lang,album --max-groups 5

    # a server that is already running; --catalog-db must be a copy of ITS
    # database, because events and exclude_ids carry internal tracks.id values
    python scripts/dj_eval/run_eval.py --base-url http://127.0.0.1:8000 \
        --token <session token> --catalog-db data/dj_eval/eval.db

Writes <out>/dj_eval_<stamp>.json (every request and result) and .md (the
per-group table and three traces per group). See groups.py for the groups.
A full run is ~330 groups and about 1.5 h with 4 workers; each finished
group also goes to <out>/dj_eval_partial.jsonl, and --resume carries on
from it.

SCENARIOS, per group G (n members, library N):
  single    a search-and-play of one member, then the top `limit`. Up to
            --seed-cap members, sampled with a fixed seed. Precision is the
            share of the recommendations in G; base rate (n-1)/(N-1), the
            precision of a random pick; lift = precision / base.
  session   4 members completed, then the top `limit` (seed = the 4th,
            as the app's autoplay fetches on the ending track). Base
            (n-4)/(N-4).
  skip      the same 4 completions, then 3 members of another group B of
            the same family skipped at 10%. Reports how many of the next
            `limit` are from B, before the skips and after.
  listener  search one member and finish it, then --steps times: fetch,
            count how many of the `limit` are in G (acceptance), play the
            top pick as autoplay does, finish it if it is in G, skip it at
            10% otherwise. Reports acceptance per step and the first step
            with >= 8 of 10 accepted.
  leakage   for every group, the languages of single-seed recommendations
            that are not G's dominant language.

ISOLATION. Plays go to POST /api/events as in the app, so they reach
user_track_stats and the recency re-rank. Each scenario therefore starts
from a clean history: on a local copy the user's track_events, user_events,
user_track_stats and user_stats rows are deleted first (the copy only;
--db is required for that). Against --base-url there is no reset, so
telemetry is off there unless --events is passed.
"""
from __future__ import annotations

import argparse
import json
import queue
import random
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/

from dj_eval import groups as groups_mod  # noqa: E402
from dj_eval.client import DjClient, Session, login  # noqa: E402
from dj_eval.setup_copy import load_meta, reset_user  # noqa: E402
from dj_eval.server import LocalServer  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = _REPO / "data" / "dj_eval" / "reports"
FAMILIES = ("lang", "lang_band", "album", "artist")
SKIP_RATIO = 0.1
ACCEPT_TARGET = 0.8


# ---------------------------------------------------------------- scenarios
class Ctx:
    def __init__(self, client, catalog, user_id, args, db_path):
        self.client, self.catalog, self.user_id = client, catalog, user_id
        self.args, self.db_path = args, db_path

    def session(self, events: bool) -> Session:
        if events and self.db_path is not None:
            reset_user(self.db_path, self.user_id)
        return Session(self.client, self.catalog, limit=self.args.limit, post_events=events)


def _rec_view(recs, members):
    return [{"id": r.id, "spotify_id": r.spotify_id, "title": r.title, "artist": r.artist,
             "score": r.score, "final_score": r.final_score, "in_group": r.spotify_id in members}
            for r in recs]


def _invariants(s: Session, recs) -> list[str]:
    """What must hold for every response: nothing seen this session (seed,
    played, skipped) comes back, and nothing comes back twice."""
    bad = []
    ids = [r.id for r in recs]
    if len(ids) != len(set(ids)):
        bad.append("duplicate")
    seen = set(s.seen)
    if any(i in seen for i in ids):
        bad.append("returned_seen")
    return bad


def run_single(ctx: Ctx, g, members, rng):
    out = []
    picks = rng.sample(g.members, min(ctx.args.seed_cap, g.size))
    for sid in picks:
        s = ctx.session(events=False)
        s.search(sid)
        recs = s.recommend()
        out.append({"seed": sid, "recs": _rec_view(recs, members), "ms": s.steps[-1].ms,
                    "mode_used": s.steps[-1].mode_used, "violations": _invariants(s, recs)})
    return out


def _complete_four(s: Session, g, rng):
    four = rng.sample(g.members, 4)
    for sid in four:
        s.complete(sid)
    return four


def run_session(ctx: Ctx, g, members, rng):
    out = []
    for _ in range(ctx.args.sessions if g.size >= 5 else 0):
        s = ctx.session(events=ctx.args.events)
        played = _complete_four(s, g, rng)
        recs = s.recommend()
        out.append({"played": played, "recs": _rec_view(recs, members), "ms": s.steps[-1].ms,
                    "violations": _invariants(s, recs)})
    return out


def run_skip(ctx: Ctx, g, members, rng, pool):
    out = []
    others = [b for b in pool if b.name != g.name and b.size >= 3 and not (set(b.members) & members)]
    if g.size < 5 or not others:
        return out
    for _ in range(ctx.args.skip_runs):
        b = rng.choice(others)
        bm = set(b.members)
        s = ctx.session(events=ctx.args.events)
        _complete_four(s, g, rng)
        before = s.recommend()
        viol = _invariants(s, before)   # now: the skips below may pick one of these
        skipped = rng.sample(b.members, 3)
        for sid in skipped:
            s.skip(sid, SKIP_RATIO)
        after = s.recommend()
        out.append({
            "other": b.name, "skipped": skipped,
            "before_in_other": sum(r.spotify_id in bm for r in before),
            "after_in_other": sum(r.spotify_id in bm for r in after),
            "before_in_group": sum(r.spotify_id in members for r in before),
            "after_in_group": sum(r.spotify_id in members for r in after),
            "ms": [s.steps[-2].ms, s.steps[-1].ms],
            "violations": viol + _invariants(s, after),
            "after": _rec_view(after, members),
        })
    return out


def run_listener(ctx: Ctx, g, members, rng):
    out = []
    for _ in range(ctx.args.listener_runs):
        s = ctx.session(events=ctx.args.events)
        start = rng.choice(g.members)
        s.search(start)
        s.end(1.0)
        acc, played, viol, ms = [], [], [], []
        for _step in range(ctx.args.steps):
            recs = s.recommend()
            ms.append(s.steps[-1].ms)
            viol += _invariants(s, recs)
            if not recs:
                acc.append(0.0)
                break
            acc.append(sum(r.spotify_id in members for r in recs) / len(recs))
            top = recs[0]
            ok = top.spotify_id in members
            s.play(top.spotify_id)
            s.end(1.0 if ok else SKIP_RATIO)
            played.append({"spotify_id": top.spotify_id, "title": top.title,
                           "artist": top.artist, "accepted": ok})
        hit = next((i + 1 for i, a in enumerate(acc) if a >= ACCEPT_TARGET), None)
        out.append({"start": start, "acceptance": acc, "steps_to_target": hit,
                    "played": played, "ms": ms, "violations": viol})
    return out


def run_group(ctx: Ctx, g, pool, seed: int) -> dict:
    members = set(g.members)
    rng = random.Random(f"{seed}:{g.name}")
    res = {"group": g.name, "family": g.family, "size": g.size}
    res["single"] = run_single(ctx, g, members, rng)
    res["session"] = run_session(ctx, g, members, rng)
    res["skip"] = run_skip(ctx, g, members, rng, pool)
    res["listener"] = run_listener(ctx, g, members, rng)
    return res


# ---------------------------------------------------------------- summary
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _prec(recs):
    return sum(r["in_group"] for r in recs) / len(recs) if recs else 0.0


def summarise(res: dict, catalog: dict, n_lib: int) -> dict:
    n = res["size"]
    base1 = (n - 1) / (n_lib - 1)
    base4 = (n - 4) / (n_lib - 4) if n > 4 else None
    p1 = _mean([_prec(x["recs"]) for x in res["single"]])
    ps = _mean([_prec(x["recs"]) for x in res["session"]])
    langs = Counter(catalog[sid].language for sid in res["members"]) if "members" in res else Counter()
    home = langs.most_common(1)[0][0] if langs else None
    rec_langs = Counter()
    for x in res["single"]:
        for r in x["recs"]:
            t = catalog.get(r["spotify_id"])
            rec_langs[t.language if t else None] += 1
    total = sum(rec_langs.values())
    leaked = {k: v for k, v in rec_langs.items() if k != home}
    acc = [x["acceptance"] for x in res["listener"]]
    steps = max((len(a) for a in acc), default=0)
    per_step = [_mean([a[i] for a in acc if len(a) > i]) for i in range(steps)]
    reach = [x["steps_to_target"] for x in res["listener"]]
    return {
        "group": res["group"], "family": res["family"], "size": n,
        "base": base1, "p_single": p1, "lift_single": p1 / base1 if p1 is not None and base1 else None,
        "p_session": ps, "lift_session": ps / base4 if ps is not None and base4 else None,
        "skip_before": _mean([x["before_in_other"] for x in res["skip"]]),
        "skip_after": _mean([x["after_in_other"] for x in res["skip"]]),
        "listener_per_step": per_step,
        "listener_reached": sum(r is not None for r in reach), "listener_runs": len(reach),
        "listener_steps_to_target": _mean(reach),
        "home_language": home,
        "leak_share": (sum(leaked.values()) / total) if total else None,
        "leak_top": Counter(leaked).most_common(3),
        "n_single": len(res["single"]),
    }


def _f(x, d=2):
    return "-" if x is None else f"{x:.{d}f}"


def write_markdown(path: Path, meta: dict, summaries: list, results: list, catalog: dict) -> None:
    L = []
    L.append(f"# DJ end-to-end eval, {meta['finished_at']}\n")
    L.append(f"- target: `{meta['base_url']}`  catalog: `{meta['catalog_db']}`")
    L.append(f"- library: {meta['library']} tracks; groups: {len(summaries)}; "
             f"limit {meta['args']['limit']}; telemetry {'on' if meta['args']['events'] else 'off'}")
    lat = meta["latency_ms"]
    L.append(f"- POST /similar latency (client wall clock, {lat['n']} requests): "
             f"p50 {_f(lat['p50'], 0)} ms, p95 {_f(lat['p95'], 0)} ms, max {_f(lat['max'], 0)} ms")
    L.append(f"- invariant violations: {meta['violations'] or 'none'}\n")

    L.append("## By family (unweighted mean over groups)\n")
    L.append("| family | groups | base | p@10 single | lift | p@10 session | lift | "
             "skip: other before | after | listener step 1 | step last | reached 8/10 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for fam in FAMILIES:
        ss = [s for s in summaries if s["family"] == fam]
        if not ss:
            continue
        first = _mean([s["listener_per_step"][0] for s in ss if s["listener_per_step"]])
        last = _mean([s["listener_per_step"][-1] for s in ss if s["listener_per_step"]])
        reached = sum(s["listener_reached"] for s in ss)
        runs = sum(s["listener_runs"] for s in ss)
        L.append(f"| {fam} | {len(ss)} | {_f(_mean([s['base'] for s in ss]), 3)} | "
                 f"{_f(_mean([s['p_single'] for s in ss]))} | {_f(_mean([s['lift_single'] for s in ss]), 1)} | "
                 f"{_f(_mean([s['p_session'] for s in ss]))} | {_f(_mean([s['lift_session'] for s in ss]), 1)} | "
                 f"{_f(_mean([s['skip_before'] for s in ss]))} | {_f(_mean([s['skip_after'] for s in ss]))} | "
                 f"{_f(first)} | {_f(last)} | {reached}/{runs} |")

    L.append("\n## Per group\n")
    L.append("Base = the group's share of the library (a random pick's precision). "
             "Skip columns: recommendations (of 10) from the skipped group B, before and after "
             "3 skips. Listener: mean acceptance at step 1 and at the last step; runs reaching "
             "8 of 10, and the mean step they got there. Leak: share of single-seed "
             "recommendations outside the group's main language, and the top three.\n")
    L.append("| group | n | base | p@10 | lift | sess p@10 | lift | skip B before→after | "
             "listener 1→last | reached (step) | leak | leaked to |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        ps = s["listener_per_step"]
        L.append(
            f"| {s['group']} | {s['size']} | {_f(s['base'], 3)} | {_f(s['p_single'])} | "
            f"{_f(s['lift_single'], 1)} | {_f(s['p_session'])} | {_f(s['lift_session'], 1)} | "
            f"{_f(s['skip_before'], 1)}→{_f(s['skip_after'], 1)} | "
            f"{_f(ps[0] if ps else None)}→{_f(ps[-1] if ps else None)} | "
            f"{s['listener_reached']}/{s['listener_runs']} ({_f(s['listener_steps_to_target'], 1)}) | "
            f"{_f(s['leak_share'])} | "
            f"{', '.join(f'{k}:{v}' for k, v in s['leak_top']) or '-'} |")

    L.append("\n## Traces (single seed; [x] = in the group)\n")
    for res in results:
        L.append(f"### {res['group']} (n={res['size']})\n")
        for x in res["single"][:3]:
            t = catalog.get(x["seed"])
            L.append(f"- **{t.title}** — {t.artist} [{t.language}]")
            for r in x["recs"]:
                rt = catalog.get(r["spotify_id"])
                lang = rt.language if rt else "?"
                mark = "[x]" if r["in_group"] else "[ ]"
                L.append(f"  - {mark} {r['title']} — {r['artist']} [{lang}] "
                         f"{_f(r['score'], 3)}")
        L.append("")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# -------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, help="prepared copy: start a local server on it")
    ap.add_argument("--base-url", help="use a running server instead")
    ap.add_argument("--token", action="append",
                    help="session token for --base-url (repeat for more workers)")
    ap.add_argument("--catalog-db", type=Path,
                    help="DB to build groups from (default --db); must match the server's ids")
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--max-groups", type=int, default=30,
                    help="per family, largest first (default 30; 0 = all, ~1.5 h on the full catalogue)")
    ap.add_argument("--seed-cap", type=int, default=40)
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--skip-runs", type=int, default=5)
    ap.add_argument("--listener-runs", type=int, default=3)
    ap.add_argument("--steps", type=int, default=15)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--workers", type=int, default=None,
                    help="concurrent listeners, one test user each (default: all users)")
    ap.add_argument("--events", action=argparse.BooleanOptionalAction, default=None,
                    help="POST /api/events for plays (default: on with --db, off with --base-url)")
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--resume", action="store_true",
                    help="keep the groups already in <out>/dj_eval_partial.jsonl and run the rest")
    args = ap.parse_args(argv)

    if bool(args.db) == bool(args.base_url):
        ap.error("pass exactly one of --db or --base-url")
    if args.events is None:
        args.events = bool(args.db)

    if args.db:
        users = load_meta(args.db)["users"]
        db_path = args.db.resolve()
    else:
        if not args.token:
            ap.error("--base-url needs --token")
        users = [{"user_id": None, "token": t} for t in args.token]
        db_path = None
    catalog_db = args.catalog_db or args.db
    if catalog_db is None:
        ap.error("--base-url needs --catalog-db")
    if args.events and db_path is None:
        print("note: telemetry on with no reset; play history accumulates across scenarios")

    catalog = groups_mod.load_catalog(catalog_db)
    fams = [f.strip() for f in args.families.split(",") if f.strip()]
    gs = [g for g in groups_mod.derive(catalog) if g.family in fams]
    if args.max_groups:
        keep = []
        for f in fams:
            keep += sorted([g for g in gs if g.family == f], key=lambda g: -g.size)[:args.max_groups]
        gs = keep
    by_family = defaultdict(list)
    for g in gs:
        by_family[g.family].append(g)
    workers = min(args.workers or len(users), len(users))
    print(f"{len(catalog)} tracks, {len(gs)} groups "
          f"({', '.join(f'{f} {len(v)}' for f, v in by_family.items())}), {workers} workers")

    # Every finished group is appended here as it lands, so a run that dies
    # an hour in can --resume. Each group's sampling is seeded by its name
    # and each scenario starts from a wiped history, so a resumed group is
    # the group a single run would have produced.
    args.out.mkdir(parents=True, exist_ok=True)
    partial = args.out / "dj_eval_partial.jsonl"
    results, errors = [], []
    if args.resume and partial.exists():
        names = {g.name for g in gs}
        for line in partial.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["group"] in names:
                results.append(r)
        print(f"resuming: {len(results)} groups already done")
    else:
        partial.write_text("", encoding="utf-8")
    finished = {r["group"] for r in results}

    server = LocalServer(db_path) if db_path else None
    base_url = args.base_url
    if server:
        server.start()
        base_url = server.base_url
    started = time.time()
    try:
        todo: queue.Queue = queue.Queue()
        # Biggest groups first: they take longest, and the tail then fills in.
        for g in sorted(gs, key=lambda g: -min(g.size, args.seed_cap)):
            if g.name not in finished:
                todo.put(g)
        lock = threading.Lock()
        done = [0]

        # Signed in up front, in this thread: a login failing inside a
        # worker would end it silently and leave its groups unrun.
        for u in users[:workers]:
            u.setdefault("token", None)
            u["token"] = u["token"] or login(base_url, u["email"], u["password"])

        def work(user):
            client = DjClient(base_url, user["token"])
            ctx = Ctx(client, catalog, user["user_id"], args, db_path)
            while True:
                try:
                    g = todo.get_nowait()
                except queue.Empty:
                    return
                try:
                    r = run_group(ctx, g, by_family[g.family], args.seed)
                    r["members"] = g.members
                    with lock:
                        results.append(r)
                        with open(partial, "a", encoding="utf-8") as f:
                            f.write(json.dumps(r, default=str) + "\n")
                except Exception as e:  # report and carry on with the next group
                    with lock:
                        errors.append({"group": g.name, "error": repr(e)})
                with lock:
                    done[0] += 1
                    if done[0] % 10 == 0 or done[0] == len(gs) - len(finished):
                        print(f"  {done[0]}/{len(gs) - len(finished)} groups, {time.time() - started:.0f}s", flush=True)

        threads = [threading.Thread(target=work, args=(u,), daemon=True) for u in users[:workers]]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        if server:
            server.stop()

    order = {f: i for i, f in enumerate(FAMILIES)}
    results.sort(key=lambda r: (order.get(r["family"], 99), r["group"]))
    summaries = [summarise(r, catalog, len(catalog)) for r in results]

    ms = []
    viol = Counter()
    for r in results:
        for x in r["single"] + r["session"]:
            ms.append(x["ms"])
            viol.update(x["violations"])
        for x in r["skip"]:
            ms.extend(x["ms"])
            viol.update(x["violations"])
        for x in r["listener"]:
            ms.extend(x["ms"])
            viol.update(x["violations"])
    ms.sort()
    q = lambda p: ms[min(len(ms) - 1, int(p * len(ms)))] if ms else None  # noqa: E731
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = {
        "finished_at": stamp, "base_url": base_url, "catalog_db": str(catalog_db),
        "library": len(catalog), "args": {k: (str(v) if isinstance(v, Path) else v)
                                          for k, v in vars(args).items() if k != "token"},
        "elapsed_s": round(time.time() - started, 1),
        "latency_ms": {"n": len(ms), "p50": q(0.5), "p95": q(0.95), "max": ms[-1] if ms else None,
                       "mean": statistics.fmean(ms) if ms else None},
        "violations": dict(viol), "errors": errors,
    }
    jpath = args.out / f"dj_eval_{stamp}.json"
    mpath = args.out / f"dj_eval_{stamp}.md"
    jpath.write_text(json.dumps({"meta": meta, "summaries": summaries, "results": results},
                                indent=1, default=str), encoding="utf-8")
    write_markdown(mpath, meta, summaries, results, catalog)
    print(f"done in {meta['elapsed_s']}s; latency p50 {_f(meta['latency_ms']['p50'], 0)} ms "
          f"p95 {_f(meta['latency_ms']['p95'], 0)} ms; violations {dict(viol) or 'none'}; "
          f"errors {len(errors)}")
    print(f"-> {mpath}\n-> {jpath}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
