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

Changing SCALAR_COLS, TOP_LANGS or the weights changes the vector SPACE:
every stored vector must be rebuilt, and cosine against un-rebuilt rows is
meaningless. Rebuilding is cheap — set fuse_status='pending', no GPU
needed, since the MERT half is already stored — but it is not optional.
"""
from __future__ import annotations

from typing import Optional

import numpy as np


MERT_MODEL_NAME = "m-a-p/MERT-v1-95M"
SAMPLE_RATE = 24_000
MAX_DURATION_S = 30
MERT_DIM = 768
MERT_MODEL_VERSION = "mert_v1_95m_fp32_30s"
FUSED_MODEL_VERSION = "fused_v1_mert_scalar_lang"

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
LANG_DIMS = len(TOP_LANGS) + 1
W_MERT, W_SCALAR, W_LANG = 0.55, 0.25, 0.20
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


def _lang_onehot(language: Optional[str]) -> np.ndarray:
    v = np.zeros(LANG_DIMS, dtype=np.float32)
    if language:
        code = str(language).lower()
        v[TOP_LANGS.index(code) if code in TOP_LANGS else LANG_DIMS - 1] = 1.0
    else:
        v[-1] = 1.0
    return v


def _build_fused(row_dict: dict, mert_vec: np.ndarray) -> np.ndarray:
    scalars = np.array(
        [_norm_scalar(c, row_dict.get(c)) for c in SCALAR_COLS],
        dtype=np.float32,
    )
    lang = _lang_onehot(row_dict.get("language"))
    fused = np.concatenate([
        W_MERT   * _l2(mert_vec),
        W_SCALAR * _l2(scalars),
        W_LANG   * lang,
    ])
    return _l2(fused)
