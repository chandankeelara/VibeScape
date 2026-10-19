"""
Deterministic replay benchmark for the DJ: no server, no LLM, same answer
every run. Judges a change to the vector space or the ranking before any
listener agent is spent on it.

    python scripts/dj_eval/replay_bench.py --db data/dj_eval/eval.db \
        --listeners ../vibescape/data/dj_eval/listener

It reads the copy read-only (never a file under data/backups) and rebuilds
every fused vector in memory from the stored MERT vector, the row's scalars
and its language, once per SPACE. The ranking is the endpoint's own code:
dj_replay.replay for the query, the cosine pool the endpoint fetches
(_dj_recency_pool), then dj_language.rerank. The recency re-rank is off
everywhere: user_track_stats holds the final state, which would leak the
future into a replay, and the seen-set already removes what was played.

THREE SOURCES OF MOMENTS
  listener  the recorded dj-listener sessions (state files from
            scripts/dj_eval/listener.py). Every DJ-served play is a moment:
            the session log up to that play, seed = the outgoing track,
            excluded = everything played before. Their logs carry this
            copy's tracks.id values.
  real      the real users' track_events in the copy, rebuilt into the
            client's log as dj.js keeps it; every autoplay / DJ start is a
            moment, labelled by how far it was played. Seen-set = started in
            the last 4 h. Library = the user's own analysed tracks.
  groups    the eval's 'single' scenario offline: search one member of a
            language (or language x energy) group, take the top 10.

METRICS
  served AUC   P(a finished track ranks above a skipped one), over the
               tracks actually served at each moment (pct rank in the
               eligible set; 0.5 = coin flip).
  future AUC   the same at each listener moment over every track the
               listener went on to finish or skip later, averaged.
  lang@10      share of the top 10 whose language is one the listener
               finished something in, this session ('' = untagged counts
               as a language).
  untagged@10  share of the top 10 that is untagged, in sessions where the
               listener finished no untagged track.
  top8         share of the served listener tracks this config ranks in
               its top 8. The listener was served the server's top 8, so
               the baseline must score ~1.0: the fidelity check.
  habitual@10  real users: share of the top 10 in a language that is at
               least 5% of the user's library but not accepted yet this
               session (the multilingual listener's other languages).
  dup@10       moments whose top 10 holds a near-duplicate (same cleaned
               title and primary artist) of a track already skipped this
               session / already seen at all. Duplicates are out of scope
               for the language work; reported so a change that worsens
               them shows.
  p@10         group precision, as run_eval.py's 'single' scenario.

Writes data/dj_eval/reports/replay_bench_<stamp>.json and prints tables.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

import dj_language  # noqa: E402
from dj_replay import MAX_EVENTS, library_mean, parse_events, replay  # noqa: E402
from dj_eval.client import classify  # noqa: E402
from dj_eval.groups import (  # noqa: E402
    _BRACKETS, _SUFFIX, BAND_LANG_MIN, BANDS, LANG_MIN, _terciles, primary_artist)
from ingest_pipeline import fused_vector as fv  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKUPS = _REPO / "data" / "backups"
DEFAULT_OUT = _REPO / "data" / "dj_eval" / "reports"
POOL = 150          # backend/app.py _dj_recency_pool(10): max(10, min(300, max(150, 120)))
K = 10
SEEN_WINDOW_MS = 4 * 3_600_000
MIN_LIBRARY = 50    # backend/app.py _DJ_MEAN_MIN_TRACKS
GROUP_SEEDS = 40    # run_eval.py --seed-cap
V1_LANGS = ["en", "kn", "te", "hi", "pa", "sa", "ta", "ur", "km", "pt"]


# ------------------------------------------------------------------ spaces
def _v1_onehot(language):
    """fused_v1: NULL shared the 'other' slot with every rare language."""
    v = np.zeros(len(V1_LANGS) + 1, dtype=np.float32)
    code = str(language or "").lower()
    v[V1_LANGS.index(code) if code in V1_LANGS else -1] = 1.0
    return v


def build_space(rows, mert, langs, weights, v1=False) -> np.ndarray:
    out = np.empty((len(rows), fv.MERT_DIM + len(fv.SCALAR_COLS) + len(langs) + 1))
    for i, r in enumerate(rows):
        if v1:
            sc = np.array([fv._norm_scalar(c, r.get(c)) for c in fv.SCALAR_COLS], dtype=np.float32)
            out[i] = fv._l2(np.concatenate([fv.W_MERT * fv._l2(mert[i]),
                                            fv.W_SCALAR * fv._l2(sc),
                                            fv.W_LANG * _v1_onehot(r.get("language"))]))
        else:
            out[i] = fv._build_fused(r, mert[i], langs=langs, weights=weights)
    return out


# --------------------------------------------------------------- the data
def load(db: Path):
    db = db.resolve()
    if BACKUPS.resolve() in db.parents:
        raise SystemExit("refusing to open a file under data/backups: use a copy")
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f"t.{c}" for c in fv.SCALAR_COLS)
    rows, mert, stored = [], [], []
    for r in conn.execute(
            f"SELECT t.id, t.title, t.artist, t.album, t.language, t.duration_ms, t.vibe_score_ml, {cols}, "
            f"       te.mert_embedding AS m, te.fused_embedding AS f "
            f"FROM tracks t JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE t.ingestion_status = 'done' AND te.fused_embedding IS NOT NULL "
            f"  AND te.mert_embedding IS NOT NULL ORDER BY t.id"):
        m = np.frombuffer(r["m"], dtype=np.float32)
        f = np.frombuffer(r["f"], dtype=np.float32)
        if m.size != fv.MERT_DIM or f.size != 788:
            continue
        rows.append({k: r[k] for k in r.keys() if k not in ("m", "f")})
        mert.append(m.copy())
        stored.append(f.astype(np.float64))
    return conn, rows, np.stack(mert), np.stack(stored)


def _clean_title(t: str) -> str:
    t = _BRACKETS.sub("", t or "")
    t = _SUFFIX.sub("", t)
    return re.sub(r"[^\w]+", " ", t.lower()).strip()


# ---------------------------------------------------------------- moments
def listener_moments(paths):
    out = []
    for p in sorted(paths):
        st = json.loads(Path(p).read_text(encoding="utf-8"))
        if not st.get("finished_at") or not st.get("plays"):
            continue
        log = st["session"]["log"]
        ends = [i for i, e in enumerate(log) if e["action"] in ("completed", "next", "skipped")]
        plays = st["plays"]
        if len(ends) != len(plays):
            raise SystemExit(f"{p}: {len(plays)} plays but {len(ends)} endings in the log")
        ids = [log[i]["id"] for i in ends]
        outcome = {}
        for tid, pl in zip(ids, plays):
            outcome.setdefault(tid, pl["outcome"])
        accepted = {tid for tid, pl in zip(ids, plays) if pl["outcome"] == "finished"}
        for k, pl in enumerate(plays):
            if pl["how"] != "dj" or k == 0:
                continue
            out.append({
                "src": "listener", "session": st["id"], "step": k,
                "events": log[:ends[k]], "seed": ids[k - 1],
                "exclude": set(ids[:k]), "served": ids[k],
                "label": "completed" if pl["outcome"] == "finished" else "skipped",
                "future": {t: ("completed" if outcome[t] == "finished" else "skipped")
                           for t in ids[k:] if t not in set(ids[:k])},
                "accepted": accepted, "skipped": set(ids[:k]) - accepted,
                "user": None,
            })
    return out


def real_moments(conn, dur_of):
    """The client's log rebuilt from track_events, as the old offline harness
    did (.claude/tmp/dj-compare/harness.py): play_end -> completed / next /
    skipped by played ratio; a search / queue / pick start -> its event."""
    out = []
    users = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT user_id FROM track_events ORDER BY user_id")]
    for uid in users:
        rows = conn.execute("SELECT * FROM track_events WHERE user_id = ? "
                            "AND type IN ('play_start', 'play_end') ORDER BY id", (uid,)).fetchall()
        log, starts, current, open_m = [], [], None, None
        for r in rows:
            tid, ts = int(r["track_id"]), int(r["client_ts"])
            if r["type"] == "play_end":
                if r["reason"] == "completed":
                    ratio = 1.0
                else:
                    d = r["duration_ms"] or dur_of.get(tid)
                    if r["position_ms"] is None or not d:
                        continue
                    ratio = max(0.0, min(1.0, r["position_ms"] / d))
                action = classify(ratio)
                log.append({"id": tid, "action": action, "played_ratio": ratio})
                if open_m is not None and open_m["served"] == tid:
                    open_m["label"] = action
                open_m = None
                continue
            src = r["source"]
            m = None
            if src in ("dj", "autoplay") and current is not None:
                m = {"src": "real", "session": f"user{uid}", "user": uid,
                     "events": list(log), "seed": current, "served": tid,
                     "exclude": {t for s, t in starts if ts - s <= SEEN_WINDOW_MS and t != tid},
                     "label": None}
                out.append(m)
            open_m = m
            act = {"search": "searched", "queue": "queued", "pick": "picked"}.get(src)
            if act:
                log.append({"id": tid, "action": act, "played_ratio": None})
            starts.append((ts, tid))
            current = tid
    return [m for m in out if m["label"] in ("completed", "skipped")]


def group_moments(rows):
    """groups.py's 'lang' (>= LANG_MIN) and 'lang_band' families: language,
    and language x vibe_score_ml tercile within the language. The bands are
    the guard: a re-rank that bought language by giving up energy fit would
    show as lang p@10 up and band p@10 down."""
    rng = np.random.default_rng(20261018)
    by_lang = defaultdict(list)
    for r in rows:
        if r["language"]:
            by_lang[r["language"]].append(r["id"])
    vibe = {r["id"]: r["vibe_score_ml"] for r in rows}
    groups = []
    for lang, ids in sorted(by_lang.items()):
        if len(ids) >= LANG_MIN:
            groups.append(("lang", lang, ids))
        scored = [t for t in ids if vibe[t] is not None]
        if len(scored) >= BAND_LANG_MIN:
            lo, hi = _terciles([vibe[t] for t in scored])
            bands = defaultdict(list)
            for t in scored:
                v = vibe[t]
                bands["low" if v < lo else "mid" if v < hi else "high"].append(t)
            for b in BANDS:
                groups.append(("band", f"{lang}:{b}", bands[b]))
    out = []
    for fam, name, ids in groups:
        seeds = sorted(rng.choice(ids, size=min(GROUP_SEEDS, len(ids)), replace=False).tolist())
        for s in seeds:
            out.append({"src": "group", "family": fam, "group": name, "members": set(ids),
                        "seed": s, "exclude": {s}, "user": None,
                        "events": [{"id": s, "action": "searched", "played_ratio": None}]})
    return out


# ------------------------------------------------------------- the ranker
class Ranker:
    """One space, one library: the cosine pool for a moment, cached, so the
    re-rank grid costs no matrix work."""

    def __init__(self, ids, X, lang_of, prior):
        self.ids = np.asarray(ids)
        self.X = X
        self.pos = {int(t): i for i, t in enumerate(ids)}
        self.mu = library_mean(X)
        self.lang_of = lang_of
        self.prior = prior
        self.vecs = {int(t): X[i] for i, t in enumerate(ids)}

    def pool(self, m):
        events = parse_events(m["events"])
        seed = self.vecs.get(m["seed"])
        res = replay(events, self.vecs, self.mu, seed)
        if res.query is None:
            return None
        sims = self.X @ res.query
        elig = np.ones(len(self.ids), dtype=bool)
        for t in m["exclude"] | {m["seed"]}:
            i = self.pos.get(t)
            if i is not None:
                elig[i] = False
        cand = np.flatnonzero(elig)
        order = cand[np.argsort(-sims[cand], kind="stable")]
        rank = np.empty(len(self.ids), dtype=np.int64)
        rank[order] = np.arange(len(order))
        top = order[:POOL]
        return {"events": events, "order": order, "rank": rank, "n": len(order),
                "pool": [(float(sims[i]), self.lang_of[int(self.ids[i])]) for i in top],
                "pool_ids": [int(self.ids[i]) for i in top]}

    def final(self, m, P, rr):
        """Final order: (list of top-K ids, rank function)."""
        if rr is None:
            perm = list(range(len(P["pool_ids"])))
        else:
            counts = dj_language.session_languages(P["events"], self.lang_of)
            mix = dj_language.language_mix(counts, self.prior(m), rr["prior_weight"])
            perm = [i for i, _ in dj_language.rerank(P["pool"], mix, K, rr["weight"], rr["close"])]
        new_pool = [P["pool_ids"][i] for i in perm]
        pool_rank = {t: j for j, t in enumerate(new_pool)}

        def rank_of(t):
            if t in pool_rank:
                return pool_rank[t]
            i = self.pos.get(t)
            return None if i is None else int(P["rank"][i])
        return new_pool[:K], rank_of


def auc(pairs):
    c = np.array([p for p, l in pairs if l == "completed"])
    s = np.array([p for p, l in pairs if l == "skipped"])
    if not len(c) or not len(s):
        return float("nan")
    return float((c[:, None] < s[None, :]).mean() + 0.5 * (c[:, None] == s[None, :]).mean())


def evaluate(rankers, moments, pools, rr, meta):
    lang_of, dup_key = meta["lang_of"], meta["dup_key"]
    served = defaultdict(list)
    fut, lang_hit, untag, dup_skip, dup_seen, top8, offered = [], [], [], [], [], [], []
    grp = defaultdict(list)
    for m, P in zip(moments, pools):
        if P is None:
            continue
        rk = rankers[m["user"]]
        top, rank_of = rk.final(m, P, rr)
        if m["src"] == "group":
            key = m["group"] if m["family"] == "lang" else "band:" + m["group"]
            grp[key].append(sum(t in m["members"] for t in top) / K)
            continue
        r = rank_of(m["served"])
        if r is not None:
            served[m["src"]].append((r / P["n"], m["label"]))
            if m["src"] == "listener":
                top8.append(r < 8)
        if m["src"] == "real":
            # Languages the user keeps in their library (>= 5%) that this
            # session has not accepted yet: is the DJ offering them at all?
            acc = dj_language.session_languages(P["events"], lang_of)
            lib = dj_language.shares(rk.prior(m))
            other = {k for k, v in lib.items() if v >= 0.05 and k and k not in acc}
            if acc and other:
                offered.append(sum(dj_language.lang_key(lang_of[t]) in other for t in top) / K)
        if m["src"] != "listener":
            continue
        pairs = [(rank_of(t) / P["n"], lab) for t, lab in m["future"].items()
                 if rank_of(t) is not None]
        a = auc(pairs)
        if not math.isnan(a):
            fut.append(a)
        acc_langs = {dj_language.lang_key(lang_of[t]) for t in m["accepted"] if t in lang_of}
        lang_hit.append(sum(dj_language.lang_key(lang_of[t]) in acc_langs for t in top) / K)
        if "" not in acc_langs:
            untag.append(sum(not lang_of[t] for t in top) / K)
        sk = {dup_key[t] for t in m["skipped"] if t in dup_key}
        seen = {dup_key[t] for t in m["exclude"] | {m["seed"]} if t in dup_key}
        dup_skip.append(any(dup_key.get(t) in sk for t in top))
        dup_seen.append(any(dup_key.get(t) in seen for t in top))
    mean = lambda v: float(np.mean(v)) if len(v) else float("nan")  # noqa: E731
    return {
        "served_auc_listener": auc(served["listener"]),
        "served_auc_real": auc(served["real"]),
        # Fidelity: the listener was served the server's top 8, so in the
        # baseline space with no re-rank this must be ~1.0.
        "served_in_top8_listener": mean(top8),
        "future_auc_listener": mean(fut),
        "lang10_listener": mean(lang_hit),
        "untagged10_listener": mean(untag),
        "other_habitual10_real": mean(offered),
        "dup_skipped10": mean(dup_skip),
        "dup_seen10": mean(dup_seen),
        "groups": {g: mean(v) for g, v in sorted(grp.items())},
        "n": {"listener": len(served["listener"]), "real": len(served["real"])},
    }


# ------------------------------------------------------------------- main
SPACES = {
    # name: (langs, weights, v1)
    "v1_stored": None,
    "v1_rebuilt": (V1_LANGS, None, True),
    "A": (fv.TOP_LANGS, (fv.W_MERT, fv.W_SCALAR, fv.W_LANG), False),
    "C_lang0.10": (fv.TOP_LANGS, (fv.W_MERT, fv.W_SCALAR, 0.10), False),
    "C_lang0": (fv.TOP_LANGS, (fv.W_MERT, fv.W_SCALAR, 0.0), False),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True, help="a COPY (setup_copy.py)")
    ap.add_argument("--listeners", type=Path, required=True,
                    help="directory of listener.py state files")
    ap.add_argument("--spaces", default=",".join(SPACES))
    ap.add_argument("--weights", default="0.5,1,2,4", help="re-rank WEIGHT grid (0 = off)")
    ap.add_argument("--prior-weights", default="1,2,4")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)

    t0 = time.time()
    conn, rows, mert, stored = load(a.db)
    ids = [r["id"] for r in rows]
    lang_of = {r["id"]: r["language"] for r in rows}
    dur_of = {r["id"]: r["duration_ms"] for r in rows}
    dup_key = {r["id"]: (_clean_title(r["title"]), primary_artist(r["artist"])) for r in rows}
    pos_of = {t: i for i, t in enumerate(ids)}
    libs = {None: ids}
    for uid in [int(r[0]) for r in conn.execute("SELECT DISTINCT user_id FROM track_events")]:
        own = {int(r[0]) for r in conn.execute(
            "SELECT track_id FROM user_tracks WHERE user_id = ?", (uid,))}
        libs[uid] = [t for t in ids if t in own]
    moments = (listener_moments(a.listeners.glob("*.json"))
               + real_moments(conn, dur_of) + group_moments(rows))
    conn.close()
    print(f"{len(rows)} tracks; moments: "
          + ", ".join(f"{k} {v}" for k, v in Counter(m['src'] for m in moments).items()))

    def prior_for(uid):
        lib = libs[uid]
        return Counter(dj_language.lang_key(lang_of[t]) for t in lib)
    priors = {uid: prior_for(uid) for uid in libs}

    grid = [None] + [{"weight": w, "prior_weight": pw, "close": c}
                     for w in [float(x) for x in a.weights.split(",") if float(x) > 0]
                     for pw in [float(x) for x in a.prior_weights.split(",")]
                     for c in (dj_language.CLOSE, {})]
    meta = {"lang_of": lang_of, "dup_key": dup_key}
    results = []
    for name in a.spaces.split(","):
        spec = SPACES[name]
        X = stored if spec is None else build_space(rows, mert, spec[0], spec[1], spec[2])
        X = X / np.linalg.norm(X, axis=1, keepdims=True)
        rankers = {}
        for uid, lib in libs.items():
            idx = [pos_of[t] for t in lib]
            rk = Ranker(lib, X[idx], lang_of, lambda m, u=uid: priors[u])
            if uid is not None and len(lib) < MIN_LIBRARY:
                rk.mu = library_mean(X)  # the global mean, as _dj_library_mean does
            rankers[uid] = rk
        pools = [rankers[m["user"]].pool(m) for m in moments]
        for rr in grid:
            r = evaluate(rankers, moments, pools, rr, meta)
            r["space"] = name
            r["rerank"] = None if rr is None else {
                "weight": rr["weight"], "prior_weight": rr["prior_weight"],
                "close": bool(rr["close"])}
            results.append(r)
        print(f"  space {name} done ({time.time() - t0:.0f} s)", flush=True)

    _print(results)
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = a.out / f"replay_bench_{stamp}.json"
    path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nwrote {path}")
    return 0


RARE = ("ml", "es", "fr", "ko", "ar")
SLOTTED = ("en", "hi", "kn", "te", "ta", "pa")


def _label(r):
    rr = r["rerank"]
    if rr is None:
        return f"{r['space']}"
    return f"{r['space']} +rr w={rr['weight']:g} pw={rr['prior_weight']:g}{' close' if rr['close'] else ''}"


def _print(results):
    print("\n| config | top8 | served AUC listener | served AUC real | future AUC | lang@10 "
          "| untagged@10 | habitual@10 real | dup(skipped)@10 | dup(seen)@10 | p@10 rare | p@10 slotted "
          "| p@10 all langs | p@10 lang x energy |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        g = r["groups"]
        rare = np.mean([g[x] for x in RARE if x in g])
        slot = np.mean([g[x] for x in SLOTTED if x in g])
        allg = np.mean([v for k, v in g.items() if not k.startswith("band:")])
        band = np.mean([v for k, v in g.items() if k.startswith("band:")])
        print(f"| {_label(r)} | {r['served_in_top8_listener']:.2f} | {r['served_auc_listener']:.3f} | {r['served_auc_real']:.3f} "
              f"| {r['future_auc_listener']:.3f} | {r['lang10_listener']:.3f} "
              f"| {r['untagged10_listener']:.3f} | {r['other_habitual10_real']:.3f} | {r['dup_skipped10']:.3f} | {r['dup_seen10']:.3f} "
              f"| {rare:.3f} | {slot:.3f} | {allg:.3f} | {band:.3f} |")


if __name__ == "__main__":
    sys.exit(main())
