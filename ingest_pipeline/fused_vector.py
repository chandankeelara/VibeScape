"""
The fused-vector recipe: dimensions, weights and construction.

Extracted from the retired stage_embedding.py so the recipe has a home of
its own. Shared by stage_fuse.py (builds the vector during ingest) and
scripts/_backfill_fused_embeddings.py (historical backfills). It must stay
in step with backend/app.py's FUSED_DIM / MERT_DIM, which /similar uses to
validate the vectors it loads — a mismatch there makes every lookup return
nothing, silently.

Layout (841 dims, fused_v2):
    [0:768]    MERT     mean-pooled hidden state, L2'd, x W_MERT
    [768:777]  scalars  9 columns, min-max normed, L2'd, x W_SCALAR
    [777:841]  language one-hot over LANG_BLOCK = 64 slots, x W_LANG
then the whole vector is L2-normalised, so cosine is the metric. It is
stored in track_embeddings.FUSED_COLUMN ('fused_v2_embedding',
F32_BLOB(841)); the fused_v1 vectors stay in 'fused_embedding'
(F32_BLOB(788)) untouched, for a rollback, until a cleanup drops them.

The language block (fused_v2, 2026-10-19). Every tagged language has its own
slot: LANG_CODES[i] is slot i. The table is APPEND-ONLY -- never reorder or
remove an entry, or every stored vector silently changes meaning. A language
new to the catalogue is appended (scripts/language_slots.py --db <copy>
lists the codes the table lacks); until then it, and any code not in the
table, gets the last slot, OTHER_SLOT. Appending changes only that
language's own tracks, which scripts/refuse_vectors.py rewrites (it skips
every vector that does not change); no version bump, no width change.

An untagged or instrumental track (language NULL) gets an ALL-ZERO block, so
it is language-neutral: no closer to any language than to another. In
fused_v1 NULL shared the 'other' slot with every rare language, and the DJ
eval on a copy of prod_20261019T024426Z (scripts/dj_eval) measured what that
did: Malayalam, Spanish, French, Korean and Arabic seeds scored precision@10
of 0.06-0.26, leaking 74-94% of their recommendations into untagged tracks
and into each other. 'km' (Khmer) is not in the table: it only ever came
from Whisper mis-tags, and no track carries it.

64 slots: the catalogue held 28 tagged languages on 2026-10-19 (5,270
analysed tracks), so 35 are free. Each slot costs 4 bytes a vector (64 vs
fused_v1's 11: +6.7%), and a width change is the expensive thing here -- a
new column, a full re-fuse and a deploy -- so the block is sized for years,
not for today. When it fills, new codes fall into OTHER_SLOT (still apart
from untagged and from every slotted language) and language_slots.py says
so; only then is a wider block worth another migration.

W_LANG is 0.40 (fused_v1: 0.20), the rest rescaled to keep MERT : scalars at
0.55 : 0.25. Language comes from the vector alone: a separate session
language re-rank was built, measured and dropped (scripts/dj_eval/
language_rerank.py). On the replay benchmark (scripts/dj_eval/
replay_bench.py, 2026-10-19), with dj_replay's stronger skip term, 0.20 left
listener language@10 at 0.868 and 0.40 brought it to 0.952; 0.30 and 0.50
scored within 0.02 of 0.40 on language and the same on real-user AUC. The
block's width does not enter this: a one-hot of any width has norm 1.

Changing SCALAR_COLS, the weights, LANG_BLOCK or the order of LANG_CODES
changes the vector SPACE: every stored vector must be rebuilt, and cosine
against un-rebuilt rows is meaningless. Rebuilding is cheap -- no GPU, since
the MERT half is already stored -- but it is not optional: bump
FUSED_MODEL_VERSION (and backend/app.py's copy, which keys the
user_embedding_mean cache on it), then run scripts/refuse_vectors.py, which
rewrites every fused vector and drops every cached mean. A change of width
also needs a new column (see scripts/refuse_vectors.py).
"""
from __future__ import annotations

from typing import Optional

import numpy as np


MERT_MODEL_NAME = "m-a-p/MERT-v1-95M"
SAMPLE_RATE = 24_000
MAX_DURATION_S = 30
MERT_DIM = 768
MERT_MODEL_VERSION = "mert_v1_95m_fp32_30s"
FUSED_MODEL_VERSION = "fused_v2_mert_scalar_lang64"
FUSED_COLUMN = "fused_v2_embedding"

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
LANG_BLOCK = 64
OTHER_SLOT = LANG_BLOCK - 1
# APPEND-ONLY: slot i is LANG_CODES[i] forever. 2026-10-19: every tagged code
# on prod_20261019T024426Z, the ten that already had slots first, then by
# track count (ties by code).
LANG_CODES = [
    "en", "hi", "kn", "te", "ta", "pa", "ml", "es", "fr", "pt",   # 0-9
    "ar", "sa", "ko", "ur", "ru", "bn", "ja", "mr", "yo", "de",   # 10-19
    "it", "nl", "bm", "ka", "tcy", "tr", "tw", "zh",              # 20-27
]
assert len(LANG_CODES) == len(set(LANG_CODES)) <= OTHER_SLOT
LANG_DIMS = LANG_BLOCK
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


def _lang_onehot(language: Optional[str], langs=None, block=None) -> np.ndarray:
    """One-hot over the slot table, the last slot for a code not in it, all
    zeros for an untagged track. `langs` / `block` default to LANG_CODES /
    LANG_BLOCK; the offline benchmark passes others (block defaults to
    len(langs) + 1 then: the fused_v1-style layout)."""
    if langs is None:
        langs, block = LANG_CODES, LANG_BLOCK
    elif block is None:
        block = len(langs) + 1
    v = np.zeros(block, dtype=np.float32)
    code = str(language or "").strip().lower()
    if code:
        v[langs.index(code) if code in langs else block - 1] = 1.0
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
