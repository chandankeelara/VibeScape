"""
The fused-vector recipe: dimensions, weights and construction.

Extracted from the retired stage_embedding.py so the recipe has a home of
its own. Shared by stage_fuse.py (builds the vector during ingest) and
scripts/_backfill_fused_embeddings.py (historical backfills). It must stay
in step with backend/app.py's FUSED_DIM / MERT_DIM, which /similar uses to
validate the vectors it loads — a mismatch there makes every lookup return
nothing, silently.

Layout (788 dims):
    [0:768]    MERT     mean-pooled hidden state, L2'd, x W_MERT
    [768:777]  scalars  9 columns, min-max normed, L2'd, x W_SCALAR
    [777:788]  language one-hot over TOP_LANGS + 'other', x W_LANG
then the whole vector is L2-normalised, so cosine is the metric.

The language block (fused_v2, 2026-10-19). A tagged language in TOP_LANGS
gets its own slot; any other tagged language gets 'other'; an untagged or
instrumental track (language NULL) gets an ALL-ZERO block, so it is
language-neutral: no closer to any language than to another. In fused_v1
NULL shared the 'other' slot with every rare language, and the DJ eval on a
copy of prod_20261019T024426Z (scripts/dj_eval) measured what that did:
Malayalam, Spanish, French, Korean and Arabic seeds scored precision@10 of
0.06-0.26, leaking 74-94% of their recommendations into untagged tracks and
into each other, while slotted languages scored 0.97-1.00.

TOP_LANGS is every language with at least LANG_SLOT_MIN_TRACKS finished
tracks, most first, counted on that backup by
`python scripts/language_slots.py --db <copy>` (which prints the counts and
the list it derives). It is frozen here rather than derived at fuse time:
the list defines the space, so it changes only together with a version
bump and a re-fuse (scripts/refuse_vectors.py). On 2026-10-19 the cut gave
exactly ten languages (ar was next, at 24), so the block stayed 11 wide and
FUSED_DIM stayed 788 -- which keeps production's F32_BLOB(788) column. A
list of another length changes FUSED_DIM, backend/app.py's
FUSED_LANG_DIMS, and that column type. 'km' (Khmer) is gone: it only ever
came from Whisper mis-tags, and no track carries it today.

W_LANG is 0.40 (fused_v1: 0.20), the rest rescaled to keep MERT : scalars at
0.55 : 0.25. Language comes from the vector alone: a separate session
language re-rank was built, measured and dropped (scripts/dj_eval/
language_rerank.py). On the replay benchmark (scripts/dj_eval/
replay_bench.py, 2026-10-19), with dj_replay's stronger skip term, 0.20 left
listener language@10 at 0.868 and 0.40 brought it to 0.952; 0.30 and 0.50
scored within 0.02 of 0.40 on language and the same on real-user AUC.

Changing SCALAR_COLS, TOP_LANGS or the weights changes the vector SPACE:
every stored vector must be rebuilt, and cosine against un-rebuilt rows is
meaningless. Rebuilding is cheap — no GPU, since the MERT half is already
stored — but it is not optional: bump FUSED_MODEL_VERSION (and
backend/app.py's copy, which keys the user_embedding_mean cache on it), then
run scripts/refuse_vectors.py, which rewrites every fused vector and drops
every cached mean.
"""
from __future__ import annotations

from typing import Optional

import numpy as np


MERT_MODEL_NAME = "m-a-p/MERT-v1-95M"
SAMPLE_RATE = 24_000
MAX_DURATION_S = 30
MERT_DIM = 768
MERT_MODEL_VERSION = "mert_v1_95m_fp32_30s"
FUSED_MODEL_VERSION = "fused_v2_mert_scalar_lang"

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
LANG_SLOT_MIN_TRACKS = 25
TOP_LANGS = ["en", "hi", "kn", "te", "ta", "pa", "ml", "es", "fr", "pt"]
LANG_DIMS = len(TOP_LANGS) + 1
# MERT and scalars keep fused_v1's 0.55 : 0.25 ratio and share what the
# language block leaves (see "The language block" above for why 0.40).
W_MERT, W_SCALAR, W_LANG = 0.4125, 0.1875, 0.40
FUSED_DIM = MERT_DIM + len(SCALAR_COLS) + LANG_DIMS


# ---- Vector-builder helpers (duplicated from _backfill_fused_embeddings) ----


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n < 1e-8 else v / n


def _norm_scalar(name: str, val) -> float:
    if val is None:
        return 0.0
    lo, hi = SCALAR_RANGE[name]
    if hi <= lo:
        return 0.0
    x = (float(val) - lo) / (hi - lo)
    return max(0.0, min(1.0, x))


def _lang_onehot(language: Optional[str], langs=None) -> np.ndarray:
    """One-hot over `langs` (default TOP_LANGS) + 'other'; all zeros for an
    untagged track. `langs` exists for the offline benchmark."""
    langs = TOP_LANGS if langs is None else langs
    v = np.zeros(len(langs) + 1, dtype=np.float32)
    code = str(language or "").strip().lower()
    if code:
        v[langs.index(code) if code in langs else len(langs)] = 1.0
    return v


def _build_fused(row_dict: dict, mert_vec: np.ndarray,
                 langs=None, weights=None) -> np.ndarray:
    """The fused vector for one row. `langs` and `weights` (W_MERT,
    W_SCALAR, W_LANG) default to the current recipe; the offline benchmark
    (scripts/dj_eval/replay_bench.py) passes others to compare spaces."""
    w_mert, w_scalar, w_lang = (W_MERT, W_SCALAR, W_LANG) if weights is None else weights
    scalars = np.array(
        [_norm_scalar(c, row_dict.get(c)) for c in SCALAR_COLS],
        dtype=np.float32,
    )
    lang = _lang_onehot(row_dict.get("language"), langs)
    fused = np.concatenate([
        w_mert   * _l2(mert_vec),
        w_scalar * _l2(scalars),
        w_lang   * lang,
    ])
    return _l2(fused)
