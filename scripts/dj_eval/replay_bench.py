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

from dj_eval import language_rerank as dj_language  # noqa: E402  (measured, not shipped)
import dj_replay  # noqa: E402
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
POOL_MAX = 300      # backend/app.py _DJ_RECENCY_POOL_MAX: the pool while a language is demoted
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


def build_space(rows, mert, langs, weights, v1=False, instrumental=False,
                instrumental_weight=None) -> np.ndarray:
    saved = fv.INSTRUMENTAL_WEIGHT
    if instrumental_weight is not None:
        fv.INSTRUMENTAL_WEIGHT = instrumental_weight
    try:
        return _build_space(rows, mert, langs, weights, v1, instrumental)
    finally:
        fv.INSTRUMENTAL_WEIGHT = saved


def _build_space(rows, mert, langs, weights, v1=False, instrumental=False) -> np.ndarray:
    """langs None: the shipped slot table (fused_vector.LANG_CODES, 64 wide)."""
    width = fv.LANG_BLOCK if langs is None else len(langs) + 1
    out = np.empty((len(rows), fv.MERT_DIM + len(fv.SCALAR_COLS) + width))
    for i, r in enumerate(rows):
        if v1:
            sc = np.array([fv._norm_scalar(c, r.get(c)) for c in fv.SCALAR_COLS], dtype=np.float32)
            out[i] = fv._l2(np.concatenate([0.55 * fv._l2(mert[i]), 0.25 * fv._l2(sc),
                                            0.20 * _v1_onehot(r.get("language"))]))
        else:
            # Without `instrumental` a 'no language' verdict stays all zeros, as
            # in every recipe before the zxx slot (2026-10-20).
            row = r if instrumental else {**r, "language_status": None}
            out[i] = fv._build_fused(row, mert[i], langs=langs, weights=weights)
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
            f"SELECT t.id, t.title, t.artist, t.album, t.language, t.language_status, "
            f"       t.duration_ms, t.vibe_score_ml, {cols}, "
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
# The listener sessions recorded on 2026-10-19 against the fused_v2 DJ as
# deployed (revision 00028), which exposed the skip overshoot: mood skips
# threw two Kannada sessions out of Kannada, a workout session wandered
# through four languages, and film BGM drowned in vocal songs.
REGRESSION = ("english-chill-aef5e7", "film-bgm-29e1d1", "kannada-mass-d40162",
              "kannada-late-night-982645", "english-workout-cff981")
# What each persona asked for (personas.json); film BGM is scored on
# instrumentals instead.
PERSONA_LANG = {"english-chill": "en", "english-workout": "en", "kannada-mass": "kn",
                "kannada-late-night": "kn"}


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
"SELECT DISTINCT e.user_id FROM track_events e JOIN users u ON u.id = e.user_id "
        "WHERE u.display_name NOT LIKE 'dj-%' ORDER BY e.user_id")]
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


def scenario_moments(rows, mert=None, dup_key=None):
    """Synthetic sessions on real tracks, 30 draws each. Every moment says
    which language's share of the top 10 it measures, and which library it
    runs in ('kn70': all Kannada tracks plus 30% Hindi; None: everything).

      lib70    library 70% kn / 30% hi, three Hindi songs finished: hi@10
      skip<j>  three Kannada songs finished, then j Kannada skips: kn@10
      switch<k> five Kannada finished, then k Hindi finished: hi@10
      mixed    one track searched, then three of its neighbours that sound
               least alike (by MERT) skipped: the share of the top 10 in
               those three tracks' own 15 nearest neighbours, without the
               skips ("before") and with them ("after")
      twin     a track with another release (same cleaned title and primary
               artist) is searched: is the other release in the top 10, and
               is it still there once the searched one is skipped
    """
    rng = np.random.default_rng(20261019)
    kn = [r["id"] for r in rows if r["language"] == "kn"]
    hi = [r["id"] for r in rows if r["language"] == "hi"]
    hi70 = sorted(rng.choice(hi, size=round(len(kn) * 3 / 7), replace=False).tolist())

    def done(t):
        return {"id": t, "action": "completed", "played_ratio": 1.0}

    def skp(t):
        return {"id": t, "action": "skipped", "played_ratio": 0.1}

    en = [r["id"] for r in rows if r["language"] == "en"]
    kv = sorted((r["vibe_score_ml"], r["id"]) for r in rows
                if r["language"] == "kn" and r["vibe_score_ml"] is not None)
    third = len(kv) // 3
    kn_band = {"low": [t for _, t in kv[:third]], "high": [t for _, t in kv[-third:]]}

    def moment(name, events, lang, lib):
        ids = [e["id"] for e in events]
        return {"src": "scenario", "scenario": name, "lang": lang, "lib": lib,
                "events": events, "seed": ids[-1], "exclude": set(ids), "user": lib}
    out = []
    for _ in range(30):
        h = rng.choice(hi70, 3, replace=False).tolist()
        out.append(moment("lib70: hi@10", [done(t) for t in h], "hi", "kn70"))
        k = rng.choice(kn, 6, replace=False).tolist()
        for j in (1, 2, 3):
            ev = [done(t) for t in k[:3]] + [
                {"id": t, "action": "skipped", "played_ratio": 0.1} for t in k[3:3 + j]]
            out.append(moment(f"skip{j}: kn@10", ev, "kn", None))
        k5 = rng.choice(kn, 5, replace=False).tolist()
        h4 = rng.choice(hi, 4, replace=False).tolist()
        for j in range(5):
            out.append(moment(f"switch{j}: hi@10", [done(t) for t in k5 + h4[:j]], "hi", None))
        # Skip-heavy (2026-10-20). Mood: three high-energy Kannada songs
        # finished, then three low-energy Kannada songs skipped -- the
        # language must stay. Wander: three English songs finished, then six
        # English songs of every energy skipped -- the top 10 must not leave
        # English. Moved: three Kannada, then three Hindi finished, then two
        # Kannada skipped -- Kannada must be gone.
        kh = rng.choice(kn_band["high"], 3, replace=False).tolist()
        kl = rng.choice(kn_band["low"], 3, replace=False).tolist()
        out.append(moment("mood3: kn@10", [done(t) for t in kh] + [skp(t) for t in kl], "kn", None))
        e3 = rng.choice(en, 9, replace=False).tolist()
        out.append(moment("wander6: en@10", [done(t) for t in e3[:3]] + [skp(t) for t in e3[3:]],
                          "en", None))
        k3, h3 = rng.choice(kn, 5, replace=False).tolist(), rng.choice(hi, 3, replace=False).tolist()
        out.append(moment("moved: kn@10", [done(t) for t in k3[:3] + h3] + [skp(t) for t in k3[3:]],
                          "kn", None))
    if mert is not None:
        out += _type_moments(rows, mert, dup_key, rng)
    return out, set(kn) | set(hi70)


_DRONE = re.compile(r"\bhz\b|\d+(\.\d+)?\s*hz|delta|theta|binaural|solfeggio|sleep", re.I)
_FILM = re.compile(r"\(from |theme|bgm|motion picture|soundtrack|score", re.I)


def _type_moments(rows, mert, dup_key, rng):
    ids = np.array([r["id"] for r in rows])
    M = mert / np.linalg.norm(mert, axis=1, keepdims=True)
    M = M - M.mean(axis=0)
    M /= np.linalg.norm(M, axis=1, keepdims=True)

    def near(i, n, skip=()):
        sims = M @ M[i]
        out = []
        for j in np.argsort(-sims):
            if j != i and j not in skip and dup_key[ids[j]] != dup_key[ids[i]]:
                out.append(j)
            if len(out) >= n:
                break
        return out

    def ev(t, a):
        return {"id": int(t), "action": a, "played_ratio": 1.0 if a == "completed" else
                (0.1 if a == "skipped" else None)}
    out = []
    en = [i for i, r in enumerate(rows) if r["language"] == "en"]
    for _ in range(30):
        x = int(rng.choice(en))
        cand = near(x, 60)
        picks = [int(rng.choice(cand))]
        for _k in range(2):
            rest = [c for c in cand if c not in picks]
            picks.append(min(rest, key=lambda c: max(M[c] @ M[p] for p in picks)))
        members = set()
        for pk in picks:
            members |= {int(ids[j]) for j in near(pk, 15, skip={x})}
        members -= {int(ids[x])} | {int(ids[p]) for p in picks}
        base = [ev(ids[x], "searched")]
        skips = [ev(ids[p], "skipped") for p in picks]
        excl = {int(ids[x])} | {int(ids[p]) for p in picks}
        for name, events in (("mixed3: before", base), ("mixed3: after", base + skips)):
            out.append({"src": "scenario", "scenario": name, "members": members,
                        "metric": "share", "events": events, "seed": int(ids[x]),
                        "exclude": excl, "user": None})
    # Instrumentals: three film-score tracks (or three drones) finished; the
    # share of drones in the top 10. Titles decide which is which.
    instr = [r for r in rows if not r["language"] and r["language_status"] == "done"]
    drones = {r["id"] for r in instr if _DRONE.search(f"{r['title']} {r['album']}")}
    film = {r["id"] for r in instr if _FILM.search(f"{r['title']} {r['album']}")} - drones
    for name, pool in (("film3: drone@10", sorted(film)), ("drone3: drone@10", sorted(drones))):
        if len(pool) < 3:
            continue
        for _ in range(30):
            acc = [int(t) for t in rng.choice(pool, 3, replace=False)]
            out.append({"src": "scenario", "scenario": name, "members": drones, "metric": "share",
                        "events": [ev(t, "completed") for t in acc], "seed": acc[-1],
                        "exclude": set(acc), "user": None})
    by_key = defaultdict(list)
    for i, r in enumerate(rows):
        by_key[dup_key[r["id"]]].append(i)
    pairs = [v[:2] for k, v in sorted(by_key.items()) if len(v) >= 2 and k[0]]
    for a_, b_ in [pairs[i] for i in rng.permutation(len(pairs))[:40]]:
        excl = {int(ids[a_])}
        base = [ev(ids[a_], "searched")]
        for name, events in (("twin: before", base),
                             ("twin: after skip", base + [ev(ids[a_], "skipped")])):
            out.append({"src": "scenario", "scenario": name, "members": {int(ids[b_])},
                        "metric": "present", "events": events, "seed": events[-1]["id"],
                        "exclude": excl, "user": None})
    return out


# ------------------------------------------------------------- the ranker
class Ranker:
    """One space, one library: the cosine pool for a moment, cached, so the
    re-rank grid costs no matrix work."""

    def __init__(self, ids, X, lang_of):
        self.ids = np.asarray(ids)
        self.X = X
        self.pos = {int(t): i for i, t in enumerate(ids)}
        self.mu = library_mean(X)
        self.lang_of = lang_of
        # The library by language: only the habitual@10 metric and the
        # legacy 'b' re-rank read it; the shipped re-rank does not.
        self.library = Counter(dj_language.lang_key(lang_of[int(t)]) for t in ids)
        self.vecs = {int(t): X[i] for i, t in enumerate(ids)}

    def pool(self, m, center="full", replay_kw=None):
        """center 'nolang': the library mean without its language block, so
        the language dims stay raw (shares, not rarity-weighted). replay_kw
        overrides dj_replay's NEG_WEIGHT / M_STEP / M_MAX for this call."""
        events = parse_events(m["events"])
        seed = self.vecs.get(m["seed"])
        mu = self.mu.copy()
        if center == "nolang":
            mu[fv.MERT_DIM + len(fv.SCALAR_COLS):] = 0.0
        saved = {k: getattr(dj_replay, k) for k in (replay_kw or {})}
        try:
            for k, v in (replay_kw or {}).items():
                setattr(dj_replay, k, v)
            res = replay(events, self.vecs, mu, seed)
        finally:
            for k, v in saved.items():
                setattr(dj_replay, k, v)
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
        top = order[:POOL_MAX]
        return {"events": events, "order": order, "rank": rank, "n": len(order), "mu": mu,
                "pool": [(float(sims[i]), self.lang_of[int(self.ids[i])]) for i in top],
                "pool_ids": [int(self.ids[i]) for i in top], "pool_idx": top}

    def final(self, m, P, rr, nearest=None):
        """Final order: (list of top-K ids, rank function). `nearest`
        {"w", "h"}: subtract w * max over the session's skipped tracks of
        decay * centred cosine(skip, candidate), decay 0.5 ** (age / h)
        (h None: no decay). Pool 300 while there is a skip, else 150."""
        cands = P["pool"]
        n = POOL
        if nearest is not None:
            skips = _skipped(P["events"])
            if skips:
                n = POOL_MAX
                mu = P["mu"]
                S = []
                for tid, age in skips:
                    v = self.vecs.get(tid)
                    if v is None:
                        continue
                    sv = v / np.linalg.norm(v) - mu
                    d = 1.0 if nearest.get("h") is None else 0.5 ** (age / nearest["h"])
                    S.append(d * sv / np.linalg.norm(sv))
                if S:
                    Xp = self.X[P["pool_idx"][:n]] - mu
                    Xp /= np.linalg.norm(Xp, axis=1, keepdims=True)
                    pen = (Xp @ np.array(S).T).max(axis=1)
                    cands = [(c - nearest["w"] * float(p), lang)
                             for (c, lang), p in zip(cands[:n], pen)]
        idx = list(range(min(n, len(cands))))
        if nearest is not None:
            idx.sort(key=lambda i: -cands[i][0])
        if rr is None:
            perm = idx
        else:
            scores = language_scores(rr, P["events"], self.lang_of, m["seed"], self.library)
            # The endpoint fetches the deeper pool only while the session is
            # demoting a language (dj_language.demoting).
            if dj_language.demoting(scores):
                n = POOL_MAX
                if nearest is None:
                    idx = list(range(min(n, len(cands))))
            sub = [cands[i] for i in idx]
            perm = [idx[i] for i, _ in dj_language.rerank(sub, scores, K, rr["weight"],
                                                          rr["close"], rr.get("demote", rr["weight"]))]
        new_pool = [P["pool_ids"][i] for i in perm]
        pool_rank = {t: j for j, t in enumerate(new_pool)}

        def rank_of(t):
            if t in pool_rank:
                return pool_rank[t]
            i = self.pos.get(t)
            return None if i is None else int(P["rank"][i])
        return new_pool[:K], rank_of


def _skipped(events):
    """[(track id, age in tracks)] for the tracks whose last verdict in the
    log is a skip; age 0 = the latest track."""
    last = {}
    for i, e in enumerate(events):
        if e["action"] in ("searched", "queued", "picked", "completed", "next", "skipped"):
            last[e["id"]] = (i, e["action"])
    order = sorted(last.items(), key=lambda kv: -kv[1][0])
    return [(tid, age) for age, (tid, (_i, a)) in enumerate(order) if a == "skipped"]


def language_scores(rr, events, lang_of, seed, library):
    """The shipped session scores, or mode 'b': PR #21's first version (raw
    shares of the accepted tracks, skips ignored, blended with the library
    at one track's weight), kept to compare against. Its mix is all
    positive, so dj_language.rerank ranks it exactly as that version did."""
    if rr.get("mode") != "b":
        return dj_language.session_scores(events, lang_of, seed=seed,
                                          half_life=rr["half_life"],
                                          skip_penalty=rr["skip_penalty"])
    seen, counts = set(), Counter()
    for e in events:
        tid = e["id"]
        if e["action"] in ("searched", "queued", "picked", "completed", "next") \
                and tid not in seen and tid in lang_of:
            seen.add(tid)
            counts[dj_language.lang_key(lang_of[tid])] += 1
    prior = dj_language.shares(library)
    n, w = float(sum(counts.values())), 1.0
    out = {k: v / (n + w) for k, v in counts.items()}
    for k, v in prior.items():
        out[k] = out.get(k, 0.0) + w * v / (n + w)
    return out


def auc(pairs):
    c = np.array([p for p, l in pairs if l == "completed"])
    s = np.array([p for p, l in pairs if l == "skipped"])
    if not len(c) or not len(s):
        return float("nan")
    return float((c[:, None] < s[None, :]).mean() + 0.5 * (c[:, None] == s[None, :]).mean())


def evaluate(rankers, moments, pools, rr, meta, nearest=None):
    lang_of, dup_key = meta["lang_of"], meta["dup_key"]
    served = defaultdict(list)
    fut, lang_hit, untag, dup_skip, dup_seen, top8, offered = [], [], [], [], [], [], []
    grp = defaultdict(list)
    scen = defaultdict(list)
    reg = defaultdict(lambda: defaultdict(list))
    for m, P in zip(moments, pools):
        if P is None:
            continue
        rk = rankers[m["user"]]
        top, rank_of = rk.final(m, P, rr, nearest)
        if m["src"] == "scenario":
            if m.get("metric") == "present":
                scen[m["scenario"]].append(float(any(t in m["members"] for t in top)))
            elif m.get("metric") == "share":
                scen[m["scenario"]].append(sum(t in m["members"] for t in top) / K)
            else:
                scen[m["scenario"]].append(sum(lang_of[t] == m["lang"] for t in top) / K)
            continue
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
            acc = {k for k, v in dj_language.session_scores(P["events"], lang_of).items()
                   if v > 0}
            lib = dj_language.shares(rk.library)
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
        hit = sum(dj_language.lang_key(lang_of[t]) in acc_langs for t in top) / K
        lang_hit.append(hit)
        if m["session"] in REGRESSION:
            rg = reg[m["session"]]
            want = PERSONA_LANG.get(m["session"].rsplit("-", 1)[0])
            if want:
                rg["lang10"].append(sum(lang_of[t] == want for t in top) / K)
            rg["langs"].append(len({dj_language.lang_key(lang_of[t]) for t in top}))
            rg["instr10"].append(sum(t in meta["instrumental"] for t in top) / K)
            if not math.isnan(a):
                rg["fut"].append(a)
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
        "scenarios": {k: mean(v) for k, v in sorted(scen.items())},
        "regression": {sid: {k: mean(v) for k, v in d.items()} for sid, d in sorted(reg.items())},
        "n": {"listener": len(served["listener"]), "real": len(served["real"])},
    }


# ------------------------------------------------------------------- main
# fused_v2's first slot list: the ten languages with 25+ tracks, + 'other'.
TEN = ["en", "hi", "kn", "te", "ta", "pa", "ml", "es", "fr", "pt"]
SPACES = {
    # name: (langs, weights, v1)
    "v1_stored": None,
    "v1_rebuilt": (V1_LANGS, None, True),
    "A": (None, (fv.W_MERT, fv.W_SCALAR, fv.W_LANG), False),       # prod (2026-10-19): 64 slots
    "AZ": (None, (fv.W_MERT, fv.W_SCALAR, fv.W_LANG), False, True), # + the instrumental slot
    "AZ1": (None, (fv.W_MERT, fv.W_SCALAR, fv.W_LANG), False, True, 1.0),   # ... at full weight
    "AZ50": (None, (fv.W_MERT, fv.W_SCALAR, fv.W_LANG), False, True, 0.5),
    "A10": (TEN, (fv.W_MERT, fv.W_SCALAR, fv.W_LANG), False),      # ... with ten slots + other
    "W20": (TEN, (0.55, 0.25, 0.20), False),                       # fused_v2's first weights
    "C_lang0.10": (TEN, (0.55, 0.25, 0.10), False),
    "C_lang0": (TEN, (0.55, 0.25, 0.0), False),
}
# dj_replay's avoid constants before 2026-10-19, for the comparisons.
LEGACY_REPLAY = {"NEG_WEIGHT": 0.6, "M_STEP": 0.25, "M_MAX": 1.0,
                 "AVOID_PERP": False, "AVOID_CAP": None}
# dj_replay as deployed in revision 00028 (2026-10-19), before the overshoot fix.
PROD_00028 = {"NEG_WEIGHT": 1.0, "M_STEP": 0.5, "M_MAX": 2.0, "AVOID_PERP": False, "AVOID_CAP": None}
# Language block raised; MERT and scalars keep their 0.55 : 0.25 ratio and
# share what is left, so the three still sum to 1 (only ratios matter: the
# vector is L2-normalised).
for _w in (0.30, 0.40, 0.50):
    _k = (1.0 - _w) / (0.55 + 0.25)
    SPACES[f"W{int(_w * 100)}"] = (TEN, (0.55 * _k, 0.25 * _k, _w), False)


def final_table():
    """The rows the PR reports (2026-10-19). Every row names its avoid
    constants, since dj_replay's defaults changed with this work."""
    rr = {"weight": 3.0, "half_life": 3.0, "skip_penalty": 1.25, "demote": 12.0,
          "close": dj_language.CLOSE}
    return [
        {"space": "v1_stored", "replay": LEGACY_REPLAY},
        {"space": "W20", "replay": LEGACY_REPLAY},
        {"space": "W20", "replay": LEGACY_REPLAY, "rr": rr},
        {"space": "W20", "replay": LEGACY_REPLAY, "nearest": {"w": 0.4, "h": 3.0}},
        {"space": "W40", "replay": LEGACY_REPLAY, "nearest": {"w": 0.4, "h": 3.0}},
        {"space": "W20", "replay": {}},
        {"space": "A10", "replay": LEGACY_REPLAY},
        {"space": "A10", "replay": {}},
        {"space": "A", "replay": {}},
    ]


def skip_grid():
    """2026-10-20, the skip overshoot: the old DJ, prod (revision 00028), and
    the candidate avoid terms -- N without its component along S (perp),
    a cap on NEG_WEIGHT * m in units of |S| -- in prod's space (A) and with
    the instrumental slot (AZ)."""
    out = [{"space": "v1_stored", "replay": LEGACY_REPLAY}]
    for sp in ("A", "AZ"):
        out.append({"space": sp, "replay": {}})
        for cap in (None, 0.5, 0.75, 1.0):
            out.append({"space": sp, "replay": {"AVOID_PERP": True, "AVOID_CAP": cap}})
        for cap in (0.5, 0.75, 1.0):
            out.append({"space": sp, "replay": {"AVOID_CAP": cap}})
        for cap in (None, 0.75):
            out.append({"space": sp, "replay": {"AVOID_PERP": True, "AVOID_PERP_RENORM": False,
                                                "AVOID_CAP": cap}})
    return out


def final_table2():
    """The rows the skip-overshoot PR reports (2026-10-20)."""
    rr = {"weight": 3.0, "half_life": 3.0, "skip_penalty": 1.25, "demote": 12.0,
          "close": dj_language.CLOSE}
    fix = {"NEG_WEIGHT": 1.0, "M_STEP": 0.5, "M_MAX": 2.0, "AVOID_PERP": True}
    return [
        {"space": "v1_stored", "replay": LEGACY_REPLAY},
        {"space": "A", "replay": PROD_00028},
        {"space": "W20", "replay": LEGACY_REPLAY, "rr": rr},
        {"space": "AZ", "replay": PROD_00028},
        {"space": "A", "replay": {**fix, "AVOID_CAP": 1.5}},
        {"space": "AZ1", "replay": {**fix, "AVOID_CAP": 1.5}},
        {"space": "AZ50", "replay": {**fix, "AVOID_CAP": 1.5}},
        {"space": "AZ", "replay": {**fix, "AVOID_CAP": 1.0}},
        {"space": "AZ", "replay": {**fix, "AVOID_CAP": 2.0}},
        {"space": "AZ", "replay": {**fix, "AVOID_CAP": 1.5}},
    ]


def skip_grid2():
    """Round 2: between the configs that pass the skip-heavy checks and prod."""
    out = [{"space": "AZ", "replay": {}}]
    for cap in (1.25, 1.5, 2.0):
        out.append({"space": "AZ", "replay": {"AVOID_PERP": True, "AVOID_CAP": cap}})
    for cap in (1.0, 1.25, 1.5):
        out.append({"space": "AZ", "replay": {"AVOID_PERP": True, "AVOID_PERP_RENORM": False,
                                              "AVOID_CAP": cap}})
    return out


def vector_grid2():
    """Round 2: past the edge of round 1's best avoid setting."""
    out = []
    for sp in ("W20", "W40"):
        for nw, ms, mx in ((1.0, 0.5, 1.5), (1.0, 0.5, 2.0), (1.0, 0.75, 1.5),
                           (1.25, 0.5, 1.5), (1.5, 0.5, 2.0)):
            out.append({"space": sp, "center": "full",
                        "replay": {"NEG_WEIGHT": nw, "M_STEP": ms, "M_MAX": mx}})
    return out


def vector_grid():
    """The vector-only configurations (no dj_language re-rank), 2026-10-19:
    can language and skips come from the taste vector alone?"""
    out = []
    for sp in ("W20", "W30", "W40", "W50"):
        for c in ("full", "nolang"):
            out.append({"space": sp, "center": c, "replay": LEGACY_REPLAY})
    for sp in ("W30", "W40", "W50"):
        for c in ("full", "nolang"):
            for nw, ms, mx in ((0.8, 0.25, 1.0), (1.0, 0.25, 1.0), (1.0, 0.5, 1.0),
                               (1.0, 0.5, 1.5)):
                out.append({"space": sp, "center": c,
                            "replay": {"NEG_WEIGHT": nw, "M_STEP": ms, "M_MAX": mx}})
            for w in (0.1, 0.2, 0.4):
                for h in (None, 3.0):
                    out.append({"space": sp, "center": c, "replay": LEGACY_REPLAY,
                            "nearest": {"w": w, "h": h}})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True, help="a COPY (setup_copy.py)")
    ap.add_argument("--listeners", type=Path, required=True,
                    help="directory of listener.py state files")
    ap.add_argument("--spaces", default=",".join(SPACES))
    ap.add_argument("--weights", default="3", help="re-rank WEIGHT grid")
    ap.add_argument("--half-lives", default="3", help="HALF_LIFE grid, in songs")
    ap.add_argument("--skip-penalties", default="1", help="SKIP_PENALTY grid")
    ap.add_argument("--demotes", default="3", help="DEMOTE grid")
    ap.add_argument("--legacy-b", action="store_true",
                    help="also run PR #21's first re-rank (library prior, skips ignored)")
    ap.add_argument("--vector-grid", action="store_true",
                    help="also run vector_grid(): no re-rank, language and skips from the vector")
    ap.add_argument("--vector-grid2", action="store_true", help="also run vector_grid2()")
    ap.add_argument("--final", action="store_true", help="only the rows of final_table()")
    ap.add_argument("--skip-grid", action="store_true", help="only the rows of skip_grid()")
    ap.add_argument("--skip-grid2", action="store_true", help="only the rows of skip_grid2()")
    ap.add_argument("--final2", action="store_true", help="only the rows of final_table2()")
    ap.add_argument("--only-vector-grid", action="store_true",
                    help="run vector_grid() and the session re-rank on A as the reference")
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
    for uid in [int(r[0]) for r in conn.execute(
            "SELECT DISTINCT e.user_id FROM track_events e JOIN users u ON u.id = e.user_id "
        "WHERE u.display_name NOT LIKE 'dj-%' ORDER BY e.user_id")]:
        own = {int(r[0]) for r in conn.execute(
            "SELECT track_id FROM user_tracks WHERE user_id = ?", (uid,))}
        libs[uid] = [t for t in ids if t in own]
    scen, kn70 = scenario_moments(rows, mert, dup_key)
    libs["kn70"] = [t for t in ids if t in kn70]
    moments = (listener_moments(a.listeners.glob("*.json"))
               + real_moments(conn, dur_of) + group_moments(rows) + scen)
    conn.close()
    print(f"{len(rows)} tracks; moments: "
          + ", ".join(f"{k} {v}" for k, v in Counter(m['src'] for m in moments).items()))

    def floats(v):
        return [float(x) for x in v.split(",")]
    grid = [None] + [{"weight": w, "half_life": h, "skip_penalty": b, "demote": d,
                      "close": dj_language.CLOSE}
                     for w in floats(a.weights) for h in floats(a.half_lives)
                     for b in floats(a.skip_penalties) for d in floats(a.demotes)]
    if a.legacy_b:
        grid.append({"mode": "b", "weight": 3.0, "close": dj_language.CLOSE})
    cfgs = []
    if not a.only_vector_grid:
        cfgs += [{"space": sp, "rr": rr} for sp in a.spaces.split(",") for rr in grid]
    else:
        cfgs += [{"space": "W20", "replay": LEGACY_REPLAY, "rr": rr} for rr in grid]
    if a.vector_grid or a.only_vector_grid:
        cfgs += vector_grid()
    if a.vector_grid2:
        cfgs += vector_grid2()
    if a.final:
        cfgs = final_table()
    if a.skip_grid:
        cfgs = skip_grid()
    if a.skip_grid2:
        cfgs = skip_grid2()
    if a.final2:
        cfgs = final_table2()
    meta = {"lang_of": lang_of, "dup_key": dup_key,
            "instrumental": {r["id"] for r in rows
                             if not r["language"] and r["language_status"] == "done"}}
    results = []
    for name in dict.fromkeys(c["space"] for c in cfgs):
        spec = SPACES[name]
        X = stored if spec is None else build_space(rows, mert, *spec)
        X = X / np.linalg.norm(X, axis=1, keepdims=True)
        rankers = {}
        for uid, lib in libs.items():
            idx = [pos_of[t] for t in lib]
            rk = Ranker(lib, X[idx], lang_of)
            if uid not in (None, "kn70") and len(lib) < MIN_LIBRARY:
                rk.mu = library_mean(X)  # the global mean, as _dj_library_mean does
            rankers[uid] = rk
        mine = [c for c in cfgs if c["space"] == name]
        keys = dict.fromkeys((c.get("center", "full"), tuple(sorted((c.get("replay") or {}).items())))
                             for c in mine)
        for center, rkw in keys:
            pools = [rankers[m["user"]].pool(m, center, dict(rkw)) for m in moments]
            for c in mine:
                if (c.get("center", "full"), tuple(sorted((c.get("replay") or {}).items()))) \
                        != (center, rkw):
                    continue
                rr = c.get("rr")
                r = evaluate(rankers, moments, pools, rr, meta, c.get("nearest"))
                r["space"] = name
                r["center"] = center
                r["replay"] = dict(rkw)
                r["nearest"] = c.get("nearest")
                r["rerank"] = None if rr is None else {
                    k: (bool(v) if k == "close" else v) for k, v in rr.items()}
                results.append(r)
            print(f"  space {name} centre {center} {dict(rkw) or ''} done "
                  f"({time.time() - t0:.0f} s)", flush=True)

    _print(results)
    _print_regression(results)
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
    extra = ""
    if r.get("center", "full") != "full":
        extra += " centre=no-lang"
    if r.get("replay"):
        extra += " " + " ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in sorted(r["replay"].items()))
    if r.get("nearest"):
        nn = r["nearest"]
        extra += f" nearest-skip w={nn['w']:g}" + (f" h={nn['h']:g}" if nn.get("h") else "")
    if rr is None:
        return f"{r['space']}{extra}"
    if rr.get("mode") == "b":
        return f"{r['space']} +rr(b: library prior, no skips) w={rr['weight']:g}"
    return (f"{r['space']} +rr w={rr['weight']:g} half-life={rr['half_life']:g} "
            f"skip={rr['skip_penalty']:g} demote={rr['demote']:g}")


def _print_regression(results):
    sids = sorted({s for r in results for s in r.get("regression", {})})
    if not sids:
        return
    print("\n| config | " + " | ".join(
        f"{s.rsplit('-', 1)[0]} {'instr' if 'bgm' in s else 'lang'}@10 / langs / fut"
        for s in sids) + " |")
    print("|---" * (len(sids) + 1) + "|")
    for r in results:
        cells = []
        for s in sids:
            g = r["regression"].get(s, {})
            main = g.get("instr10" if "bgm" in s else "lang10", float("nan"))
            cells.append(f"{main:.2f} / {g.get('langs', float('nan')):.1f} / "
                         f"{g.get('fut', float('nan')):.2f}")
        print(f"| {_label(r)} | " + " | ".join(cells) + " |")


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
    names = sorted({k for r in results for k in r["scenarios"]})
    print("\n| config | " + " | ".join(names) + " |")
    print("|---" * (len(names) + 1) + "|")
    for r in results:
        print(f"| {_label(r)} | " + " | ".join(f"{r['scenarios'].get(n, float('nan')):.2f}"
                                              for n in names) + " |")


if __name__ == "__main__":
    sys.exit(main())
