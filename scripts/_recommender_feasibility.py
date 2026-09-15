"""Recommender feasibility probe against real local SQLite data.

Read-only. Loads MERT embeddings from track_embeddings + scalar/language
features from tracks, builds a fused per-track vector, and runs a
simulated session for one user: picks their most-played track as the
seed, blends it with a user-taste vector and three different mood
targets, prints the top-N recommendations per scenario with a per-slice
similarity breakdown.

Answers: with real MERT vectors now in place, do the recommendations
under different mood-slider positions actually look sensibly different?

Run:
    D:/Softwares/MiniConda/python.exe scripts/_recommender_feasibility.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

# ---- DB (local sqlite) -----------------------------------------------------
os.environ.setdefault("DB_BACKEND", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import db_client  # noqa: E402


# ---- Feature spec ----------------------------------------------------------

EMBEDDING_VERSION = "mert_v1_95m_fp32_30s"   # matches backfill script
MERT_DIM = 768

# Scalar features from tracks (order defines the scalar slice).
SCALAR_COLS = [
    "energy_pred", "danceability_pred", "valence_pred",
    "vibe_score", "activation", "valence",
    "acousticness", "tempo", "brightness",
]
SCALAR_RANGE = {
    "energy_pred":       (0.0, 1.0),
    "danceability_pred": (0.0, 1.0),
    "valence_pred":      (0.0, 1.0),
    "vibe_score":        (0.0, 100.0),
    "activation":        (0.0, 100.0),
    "valence":           (0.0, 100.0),
    "acousticness":      (0.0, 1.0),
    "tempo":             (40.0, 220.0),
    "brightness":        (0.0, 8000.0),
}

TOP_LANGS = ["en", "kn", "te", "hi", "pa", "sa", "ta", "ur", "km", "pt"]
LANG_DIMS = len(TOP_LANGS) + 1   # +1 for "other"

# Slice weights. MERT dominates because it's the strongest content signal
# we have; scalars anchor the mood slider; language keeps recommendations
# roughly in-language.
W_MERT   = 0.55
W_SCALAR = 0.25
W_LANG   = 0.20

SLICES = {
    "mert":   (0, MERT_DIM),
    "scalar": (MERT_DIM, MERT_DIM + len(SCALAR_COLS)),
    "lang":   (MERT_DIM + len(SCALAR_COLS),
               MERT_DIM + len(SCALAR_COLS) + LANG_DIMS),
}
FUSED_DIM = MERT_DIM + len(SCALAR_COLS) + LANG_DIMS


# ---- Helpers ---------------------------------------------------------------


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v if n < 1e-8 else v / n


def _norm_scalar(name: str, val: Optional[float]) -> float:
    if val is None:
        return 0.0
    lo, hi = SCALAR_RANGE[name]
    if hi <= lo:
        return 0.0
    x = (float(val) - lo) / (hi - lo)
    return max(0.0, min(1.0, x))


def _lang_dist(top3_json: Optional[str], fallback_lang: Optional[str]) -> np.ndarray:
    """Soft language distribution from Whisper's top-3 predictions.

    Preferred over hard one-hot: bilingual/mixed-lyric tracks get partial
    mass in each language slot (e.g. Bollywood song with Hindi 0.72 +
    English 0.22 partially cosine-matches both). Falls back to a hard
    one-hot over `fallback_lang` if top3_json isn't populated (older ingest
    runs or tracks that skipped Whisper).
    """
    v = np.zeros(LANG_DIMS, dtype=np.float32)
    parsed = None
    if top3_json:
        try:
            parsed = json.loads(top3_json)
        except (ValueError, TypeError):
            parsed = None
    if isinstance(parsed, dict):
        for key in ("top1", "top2", "top3"):
            entry = parsed.get(key)
            if not (isinstance(entry, list) and len(entry) >= 2):
                continue
            lang_code, prob = entry[0], entry[1]
            if not lang_code or not isinstance(prob, (int, float)):
                continue
            code = str(lang_code).lower()
            slot = TOP_LANGS.index(code) if code in TOP_LANGS else LANG_DIMS - 1
            v[slot] += float(prob)
        if v.sum() > 0:
            return v
    # Fallback: hard one-hot over the (possibly stale) language column.
    if fallback_lang:
        code = fallback_lang.lower()
        v[TOP_LANGS.index(code) if code in TOP_LANGS else LANG_DIMS - 1] = 1.0
    else:
        v[-1] = 1.0
    return v


def _get(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def build_fused_vec(row, mert_vec: Optional[np.ndarray]) -> np.ndarray:
    mert = mert_vec if mert_vec is not None else np.zeros(MERT_DIM, dtype=np.float32)
    scalars = np.array(
        [_norm_scalar(c, _get(row, c)) for c in SCALAR_COLS],
        dtype=np.float32,
    )
    lang = _lang_onehot(_get(row, "language"))
    fused = np.concatenate([
        W_MERT   * _l2(mert),
        W_SCALAR * _l2(scalars),
        W_LANG   * lang,
    ])
    return _l2(fused)


def per_slice_cosine(a: np.ndarray, b: np.ndarray) -> dict:
    out = {}
    for name, (lo, hi) in SLICES.items():
        aa = _l2(a[lo:hi])
        bb = _l2(b[lo:hi])
        out[name] = float(np.dot(aa, bb))
    return out


# ---- Data loading ----------------------------------------------------------


def load_tracks(conn) -> list:
    scalar_cols_sql = ", ".join(SCALAR_COLS)
    return conn.execute(
        f"SELECT id, title, artist, album, language, mood, "
        f"       {scalar_cols_sql} "
        f"  FROM tracks WHERE ingestion_status='done'"
    ).fetchall()


def load_embeddings(conn) -> dict:
    """Return {track_id: np.ndarray(MERT_DIM)} for the current version."""
    rows = conn.execute(
        "SELECT track_id, dim, embedding FROM track_embeddings "
        "WHERE model_version = ?",
        (EMBEDDING_VERSION,),
    ).fetchall()
    out = {}
    for r in rows:
        vec = np.frombuffer(r["embedding"], dtype=np.float32)
        if vec.size == int(r["dim"]):
            out[int(r["track_id"])] = vec
    return out


def load_user_library(conn, user_id: int) -> list:
    scalar_cols_sql = ", ".join("t." + c + " AS " + c for c in SCALAR_COLS)
    return conn.execute(
        f"""
        SELECT t.id AS id, ut.play_count AS play_count, ut.last_played AS last_played,
               t.title AS title, t.artist AS artist, t.language AS language,
               t.mood AS mood, {scalar_cols_sql}
          FROM user_tracks ut
          JOIN tracks t ON t.id = ut.track_id
         WHERE ut.user_id = ? AND t.ingestion_status = 'done'
        """,
        (user_id,),
    ).fetchall()


# ---- Feasibility diagnostics -----------------------------------------------


def report_feasibility(tracks: list, embeds: dict) -> None:
    total = len(tracks)
    print(f"\n=== FEASIBILITY DIAGNOSTICS ===")
    print(f"tracks with ingestion_status='done': {total}")
    if not total:
        return
    cov = Counter()
    for r in tracks:
        for c in SCALAR_COLS:
            if _get(r, c) is not None:
                cov[c] += 1
        if _get(r, "language"):
            cov["language"] += 1
        if int(r["id"]) in embeds:
            cov["mert_embedding"] += 1

    print(f"\nfeature coverage (% of {total}):")
    for k in ["mert_embedding"] + SCALAR_COLS + ["language"]:
        pct = 100.0 * cov[k] / total
        bar = "#" * int(pct / 4)
        print(f"  {k:<22} {pct:5.1f}%  {bar}")

    langs = Counter(r["language"] for r in tracks if r["language"])
    print(f"\nlanguage distribution (top 10):")
    for lang, n in langs.most_common(10):
        print(f"  {lang:<8} {n:>4}  ({100*n/total:.1f}%)")
    other = total - sum(langs.values())
    print(f"  (none)   {other:>4}  ({100*other/total:.1f}%)")


# ---- Session / user / mood vectors -----------------------------------------


def build_user_vec(lib_rows: list, embeds: dict) -> Optional[np.ndarray]:
    if not lib_rows:
        return None
    vecs, weights = [], []
    for r in lib_rows:
        pc = _get(r, "play_count", 0) or 0
        w = np.log1p(pc) + 0.1
        mert = embeds.get(int(r["id"]))
        vecs.append(build_fused_vec(r, mert))
        weights.append(w)
    W = np.array(weights, dtype=np.float32)
    V = np.stack(vecs, axis=0)
    u = (V * W[:, None]).sum(axis=0) / W.sum()
    return _l2(u)


def build_mood_target(activation: float, valence: float,
                      dance: float = 0.5, energy: float = 0.5,
                      language: Optional[str] = None) -> np.ndarray:
    """The mood slider only speaks scalar+language. MERT slice is zeros."""
    scalars = np.zeros(len(SCALAR_COLS), dtype=np.float32)
    for i, c in enumerate(SCALAR_COLS):
        if c == "activation":            scalars[i] = _norm_scalar(c, activation)
        elif c == "valence":             scalars[i] = _norm_scalar(c, valence)
        elif c == "danceability_pred":   scalars[i] = dance
        elif c == "energy_pred":         scalars[i] = energy
        elif c == "valence_pred":        scalars[i] = valence / 100.0
        elif c == "vibe_score":          scalars[i] = _norm_scalar(c, activation)
    lang = _lang_onehot(language)
    fused = np.concatenate([
        W_MERT   * np.zeros(MERT_DIM, dtype=np.float32),
        W_SCALAR * _l2(scalars),
        W_LANG   * lang,
    ])
    return _l2(fused)


# ---- Ranking + pretty print ------------------------------------------------


def rank_and_print(candidates: list, cand_vecs: np.ndarray,
                   session_vec: np.ndarray, user_vec: Optional[np.ndarray],
                   mood_target: Optional[np.ndarray],
                   exclude_ids: set, top_n: int = 10,
                   w_session: float = 0.5, w_user: float = 0.2,
                   w_mood: float = 0.3) -> None:
    scores = w_session * (cand_vecs @ session_vec)
    if user_vec is not None:
        scores = scores + w_user * (cand_vecs @ user_vec)
    if mood_target is not None:
        scores = scores + w_mood * (cand_vecs @ mood_target)

    for i, r in enumerate(candidates):
        if r["id"] in exclude_ids:
            scores[i] = -1e9

    order = np.argsort(-scores)[:top_n]
    print(f"\n  weights: session={w_session}, user={w_user}, mood={w_mood}")
    print(f"  {'#':<3} {'score':>6}  {'title':<38} {'artist':<24} "
          f"{'lang':<5} {'mood':<10} {'vibe':>5}  "
          f"session:mert/scal/lang    mood:scal/lang")
    for rank, i in enumerate(order, 1):
        r = candidates[i]
        title = (r["title"] or "")[:36]
        artist = (r["artist"] or "")[:22]
        lang = (r["language"] or "-")[:4]
        mood = (r["mood"] or "-")[:9]
        vibe = _get(r, "vibe_score") or 0.0
        tv = cand_vecs[i]
        sc = per_slice_cosine(session_vec, tv)
        mc = per_slice_cosine(mood_target, tv) if mood_target is not None else {}
        s_line = f"{sc['mert']:+.2f}/{sc['scalar']:+.2f}/{sc['lang']:+.2f}"
        m_line = f"{mc['scalar']:+.2f}/{mc['lang']:+.2f}" if mc else "-"
        print(f"  {rank:<3} {scores[i]:>6.3f}  {title:<38} {artist:<24} "
              f"{lang:<5} {mood:<10} {vibe:>5.1f}  "
              f"{s_line}     {m_line}")


# ---- Main ------------------------------------------------------------------


import random


def _mert_only_vec(row, embeds: dict) -> Optional[np.ndarray]:
    v = embeds.get(int(row["id"]))
    return _l2(v) if v is not None else None


def _mert_scalar_vec(row, embeds: dict,
                     w_mert: float = 0.70, w_scalar: float = 0.30) -> Optional[np.ndarray]:
    """Fused MERT + scalar vector, no language slice. Returned L2-normed."""
    v = embeds.get(int(row["id"]))
    if v is None:
        return None
    mert = _l2(v)
    scalars = np.array(
        [_norm_scalar(c, _get(row, c)) for c in SCALAR_COLS],
        dtype=np.float32,
    )
    return _l2(np.concatenate([w_mert * mert, w_scalar * _l2(scalars)]))


def _session_vec_from(rows, vec_fn, embeds, played_weight: float = 1.5,
                      queue_weight: float = 1.0) -> Optional[np.ndarray]:
    """Weighted mean of per-track vectors. First 5 = played (higher weight),
    last 5 = queued (base weight)."""
    vecs, weights = [], []
    for i, r in enumerate(rows):
        v = vec_fn(r, embeds)
        if v is None:
            continue
        w = played_weight if i < 5 else queue_weight
        vecs.append(v)
        weights.append(w)
    if not vecs:
        return None
    W = np.array(weights, dtype=np.float32)
    V = np.stack(vecs, axis=0)
    return _l2((V * W[:, None]).sum(axis=0) / W.sum())


def _rank_topk(session_vec: np.ndarray, tracks: list, embeds: dict,
               vec_fn, exclude_ids: set, k: int = 3) -> list:
    """Return list of (score, track_row)."""
    scored = []
    for r in tracks:
        tid = int(r["id"])
        if tid in exclude_ids:
            continue
        v = vec_fn(r, embeds)
        if v is None:
            continue
        scored.append((float(np.dot(session_vec, v)), r))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def _fmt_track(r) -> str:
    """Rich per-track line so 'feel' is visible, not just the mood label."""
    title = (r["title"] or "")[:32]
    artist = (r["artist"] or "")[:20]
    lang = (_get(r, "language") or "-")[:4]
    mood = (_get(r, "mood") or "-")[:9]
    vibe = _get(r, "vibe_score") or 0.0
    e = _get(r, "energy_pred") or 0.0
    d = _get(r, "danceability_pred") or 0.0
    v = _get(r, "valence_pred") or 0.0
    return (f"{title:<32}  {artist:<20}  {lang:<4} {mood:<9} "
            f"vibe={vibe:>5.1f}  E={e:.2f} D={d:.2f} V={v:.2f}")


PATTERNS = {
    # Patterns chosen along the emotional 2D (energy × valence) axes rather
    # than the coarse 'mood' label bucket. Each returns 10 tracks close to
    # a specific corner of the plane — the recommender should pick more
    # tracks from the same corner if it's actually picking up the "feel"
    # and not just the mood tag.
    "happy_upbeat":   "high energy + high valence  (dance-pop, feel-good)",
    "sad_slow":       "low  energy + low  valence  (ballads, melancholic acoustic)",
    "dark_intense":   "high energy + low  valence  (aggressive/angry, dark bangers)",
    "chill_positive": "low  energy + high valence  (mellow but warm, lo-fi vibes)",
    "same_artist":    "10 tracks by the artist most-represented in the library",
    "random":         "10 random tracks (control — no pattern)",
}


def _closeness(row, e_target: float, v_target: float,
               d_target: Optional[float] = None) -> float:
    """L2 distance in (energy, valence[, dance]) — smaller = closer to corner."""
    e = _get(row, "energy_pred") or 0.5
    v = _get(row, "valence_pred") or 0.5
    dist = (e - e_target) ** 2 + (v - v_target) ** 2
    if d_target is not None:
        d = _get(row, "danceability_pred") or 0.5
        dist += (d - d_target) ** 2
    return dist


def _pick_pattern(name: str, eligible: list, seed: int) -> list:
    if name == "happy_upbeat":
        return sorted(eligible, key=lambda r: _closeness(r, 0.85, 0.85, 0.80))[:10]
    if name == "sad_slow":
        return sorted(eligible, key=lambda r: _closeness(r, 0.20, 0.20, 0.30))[:10]
    if name == "dark_intense":
        return sorted(eligible, key=lambda r: _closeness(r, 0.85, 0.15))[:10]
    if name == "chill_positive":
        return sorted(eligible, key=lambda r: _closeness(r, 0.25, 0.75))[:10]
    if name == "same_artist":
        # Pick the artist with the most tracks that have MERT embeddings.
        counts = Counter(_get(r, "artist") for r in eligible if _get(r, "artist"))
        if not counts:
            return []
        top_artist, _ = counts.most_common(1)[0]
        pool = [r for r in eligible if _get(r, "artist") == top_artist]
        return pool[:10] if len(pool) >= 10 else []
    if name == "random":
        return random.Random(seed).sample(eligible, 10)
    raise ValueError(f"unknown pattern: {name}")


def _seed_profile(sample: list) -> dict:
    """Aggregate stats over the seed set so we can compare against the picks."""
    vals = lambda k: [_get(r, k) for r in sample if _get(r, k) is not None]
    mood_counts = Counter(_get(r, "mood") for r in sample if _get(r, "mood"))
    lang_counts = Counter(_get(r, "language") for r in sample if _get(r, "language"))
    return {
        "vibe_mean":    np.mean(vals("vibe_score")) if vals("vibe_score") else 0,
        "energy_mean":  np.mean(vals("energy_pred")) if vals("energy_pred") else 0,
        "dance_mean":   np.mean(vals("danceability_pred")) if vals("danceability_pred") else 0,
        "valence_mean": np.mean(vals("valence_pred")) if vals("valence_pred") else 0,
        "moods":        dict(mood_counts.most_common(3)),
        "langs":        dict(lang_counts.most_common(3)),
    }


def _run_pattern(name: str, tracks: list, embeds: dict, top_k: int,
                 seed: int, show_full_picks: bool = False) -> None:
    print(f"\n{'='*100}")
    print(f"PATTERN: {name}  —  {PATTERNS[name]}")
    print(f"{'='*100}")

    eligible = [r for r in tracks if int(r["id"]) in embeds]
    sample = _pick_pattern(name, eligible, seed)
    if len(sample) < 10:
        print(f"[skip] only {len(sample)} tracks matched — need 10")
        return

    played = sample[:5]
    queued = sample[5:]
    seed_ids = {int(r["id"]) for r in sample}
    profile = _seed_profile(sample)
    print(f"  seed profile:  vibe={profile['vibe_mean']:>5.1f}  "
          f"E={profile['energy_mean']:.2f}  D={profile['dance_mean']:.2f}  "
          f"V={profile['valence_mean']:.2f}  moods={profile['moods']}  langs={profile['langs']}")

    print(f"\n  5 played:")
    for r in played:
        print(f"    [{r['id']:>4}] {_fmt_track(r)}")
    print(f"  5 queued:")
    for r in queued:
        print(f"    [{r['id']:>4}] {_fmt_track(r)}")

    sess_mert  = _session_vec_from(sample, _mert_only_vec,   embeds)
    sess_fused = _session_vec_from(sample, _mert_scalar_vec, embeds)
    top_mert   = _rank_topk(sess_mert,  tracks, embeds, _mert_only_vec,   seed_ids, top_k)
    top_fused  = _rank_topk(sess_fused, tracks, embeds, _mert_scalar_vec, seed_ids, top_k)

    if show_full_picks:
        print(f"\n  MERT-only top-{top_k}:")
        for i, (s, r) in enumerate(top_mert, 1):
            print(f"    {i:>2}. {s:.4f}  [{r['id']:>4}] {_fmt_track(r)}")
        print(f"\n  MERT+scalars top-{top_k}:")
        for i, (s, r) in enumerate(top_fused, 1):
            print(f"    {i:>2}. {s:.4f}  [{r['id']:>4}] {_fmt_track(r)}")
    else:
        print(f"\n  MERT-only top-5:")
        for i, (s, r) in enumerate(top_mert[:5], 1):
            print(f"    {i}. {s:.4f}  [{r['id']:>4}] {_fmt_track(r)}")
        print(f"  MERT+scalars top-5:")
        for i, (s, r) in enumerate(top_fused[:5], 1):
            print(f"    {i}. {s:.4f}  [{r['id']:>4}] {_fmt_track(r)}")

    # Pattern preservation: seed profile vs pick profile side by side.
    mert_profile  = _seed_profile([r for _, r in top_mert])
    fused_profile = _seed_profile([r for _, r in top_fused])
    print(f"\n  PATTERN PRESERVATION")
    print(f"                     vibe    E      D      V      moods                     langs")
    def _row(label, p):
        moods = ",".join(f"{k}={v}" for k, v in p["moods"].items())
        langs = ",".join(f"{k}={v}" for k, v in p["langs"].items())
        print(f"    {label:<16} {p['vibe_mean']:>5.1f}  {p['energy_mean']:>5.2f}  "
              f"{p['dance_mean']:>5.2f}  {p['valence_mean']:>5.2f}  "
              f"{moods:<24}  {langs}")
    _row("seed",          profile)
    _row("MERT top-K",    mert_profile)
    _row("fused top-K",   fused_profile)

    mert_ids = {int(r["id"]) for _, r in top_mert}
    fused_ids = {int(r["id"]) for _, r in top_fused}
    print(f"    overlap MERT vs fused: {len(mert_ids & fused_ids)}/{top_k}")


def _rank_topk_from_vec(session_vec: np.ndarray, tracks: list, embeds: dict,
                        vec_fn, exclude_ids: set, k: int) -> list:
    """Cosine similarity between seed_vec and every candidate. Brute-force
    nearest-neighbor over the whole library — O(N) per query, fine up to
    ~100K tracks on modern hardware; would swap in FAISS/HNSW at that
    scale."""
    scored = []
    for r in tracks:
        tid = int(r["id"])
        if tid in exclude_ids:
            continue
        v = vec_fn(r, embeds)
        if v is None:
            continue
        scored.append((float(np.dot(session_vec, v)), r))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def _run_single_seed(seed_row, tracks: list, embeds: dict, top_k: int) -> None:
    print(f"\n{'='*100}")
    print(f"SEED TRACK: [{seed_row['id']}] {_fmt_track(seed_row)}")
    print(f"{'='*100}")

    seed_id = int(seed_row["id"])

    # Same seed vector as the candidate builders — no mean over multiple
    # tracks, just this one song's fused vector.
    mert_vec  = _mert_only_vec(seed_row, embeds)
    fused_vec = _mert_scalar_vec(seed_row, embeds)
    if mert_vec is None or fused_vec is None:
        print("  [skip] seed track has no MERT embedding")
        return

    print(f"  library scanned: {sum(1 for r in tracks if int(r['id']) in embeds)} "
          f"tracks (brute-force cosine)\n")

    top_mert  = _rank_topk_from_vec(mert_vec,  tracks, embeds, _mert_only_vec,
                                    {seed_id}, top_k)
    top_fused = _rank_topk_from_vec(fused_vec, tracks, embeds, _mert_scalar_vec,
                                    {seed_id}, top_k)

    print(f"  MERT-only top-{top_k}:")
    for i, (s, r) in enumerate(top_mert, 1):
        print(f"    {i}. score={s:.4f}  [{r['id']:>4}] {_fmt_track(r)}")

    print(f"\n  MERT+scalars top-{top_k}:")
    for i, (s, r) in enumerate(top_fused, 1):
        print(f"    {i}. score={s:.4f}  [{r['id']:>4}] {_fmt_track(r)}")

    mert_ids  = {int(r["id"]) for _, r in top_mert}
    fused_ids = {int(r["id"]) for _, r in top_fused}
    print(f"\n  overlap: {len(mert_ids & fused_ids)}/{top_k}")


def _top1_agreement_all_seeds(tracks: list, embeds: dict) -> None:
    """For every embedded track, compute its top-1 nearest neighbor under
    both MERT-only and MERT+scalars. Report how often they agree.

    Vectorized: stack all fused vectors into a matrix, do one N×N cosine
    similarity matmul per method (trivial at 739×768)."""
    # Only look at tracks that both have MERT embeddings.
    embedded = [r for r in tracks if int(r["id"]) in embeds]
    n = len(embedded)
    print(f"\n=== TOP-1 AGREEMENT ACROSS ALL {n} EMBEDDED SEEDS ===")

    # Stack per-method vectors.
    mert_mat  = np.stack([_mert_only_vec(r, embeds)   for r in embedded], axis=0)  # (n, 768)
    fused_mat = np.stack([_mert_scalar_vec(r, embeds) for r in embedded], axis=0)  # (n, 768+9)

    # Similarity matrices (each row's top-1 excluding self on the diagonal).
    sim_mert  = mert_mat  @ mert_mat.T
    sim_fused = fused_mat @ fused_mat.T
    np.fill_diagonal(sim_mert,  -np.inf)
    np.fill_diagonal(sim_fused, -np.inf)

    top1_mert  = sim_mert.argmax(axis=1)
    top1_fused = sim_fused.argmax(axis=1)

    matches = int((top1_mert == top1_fused).sum())
    pct = 100.0 * matches / n
    print(f"  top-1 match:      {matches} / {n}   ({pct:.1f}%)")
    print(f"  top-1 disagree:   {n - matches} / {n}   ({100 - pct:.1f}%)")

    # Also report top-3 and top-5 overlap for context.
    for k in (3, 5, 10):
        # Get top-k indices excluding self for each row.
        # argpartition is faster than argsort at large N.
        idx_m = np.argpartition(-sim_mert,  kth=k, axis=1)[:, :k]
        idx_f = np.argpartition(-sim_fused, kth=k, axis=1)[:, :k]
        overlaps = [len(set(a) & set(b)) for a, b in zip(idx_m.tolist(), idx_f.tolist())]
        avg = float(np.mean(overlaps))
        exact = sum(1 for o in overlaps if o == k)
        print(f"  top-{k}: avg overlap = {avg:.2f}/{k}   "
              f"({100*avg/k:.1f}%)   exact match: {exact}/{n} ({100*exact/n:.1f}%)")

    # Show some examples where the two methods disagreed on top-1.
    disagreements = [(i, top1_mert[i], top1_fused[i]) for i in range(n)
                     if top1_mert[i] != top1_fused[i]]
    if disagreements:
        print(f"\n  first 10 disagreements:")
        print(f"  {'seed':<50}  {'MERT top-1':<50}  {'fused top-1':<50}")
        for i, mi, fi in disagreements[:10]:
            s_title  = (embedded[i]["title"] or "")[:24]
            s_artist = (embedded[i]["artist"] or "")[:20]
            m_title  = (embedded[mi]["title"] or "")[:24]
            m_artist = (embedded[mi]["artist"] or "")[:20]
            f_title  = (embedded[fi]["title"] or "")[:24]
            f_artist = (embedded[fi]["artist"] or "")[:20]
            print(f"    {s_title:<24} - {s_artist:<20} | "
                  f"{m_title:<24} - {m_artist:<20} | "
                  f"{f_title:<24} - {f_artist:<20}")


def main():
    conn = db_client.create_connection()
    tracks = load_tracks(conn)
    embeds = load_embeddings(conn)
    conn.close()

    if not tracks:
        print("no tracks."); return
    print(f"library={len(tracks)} tracks, {len(embeds)} with MERT embeddings")

    _top1_agreement_all_seeds(tracks, embeds)


if __name__ == "__main__":
    main()
