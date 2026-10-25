"""
The fused-vector recipe: dimensions, weights and construction.

Extracted from the retired stage_embedding.py so the recipe has a home of
its own. Shared by stage_fuse.py (builds the vector during ingest) and
scripts/_backfill_fused_embeddings.py (historical backfills). It must stay
in step with backend/services/vectors.py's FUSED_DIM / MERT_DIM, which /similar uses to
validate the vectors it loads — a mismatch there makes every lookup return
nothing, silently.

Layout (865 dims, fused_v3, 2026-10-23):
    [0:768]    MERT     mean-pooled hidden state, L2'd, x W_MERT
    [768:777]  scalars  9 columns, min-max normed, L2'd, x W_SCALAR
    [777:841]  language one-hot over LANG_BLOCK = 64 slots, x W_LANG
    [841:865]  genre    scores / 100 over GENRE_BLOCK = 24 slots, L2'd, x W_GENRE
then the whole vector is L2-normalised, so cosine is the metric. It is
stored in track_embeddings.FUSED_COLUMN ('fused_v3_embedding',
F32_BLOB(865)). fused_v2 is the same without the genre block and with
W_MERT_V2 in place of W_MERT: 841 dims in 'fused_v2_embedding'
(_build_fused_v2). The fuse stage and scripts/refuse_vectors.py write both,
so a database whose app still serves fused_v2 (backend/services/vectors.py falls back to
it while fused_v3 is absent or empty) stays correct. fused_v1's
'fused_embedding' (F32_BLOB(788)) is left untouched, for a rollback, until a
cleanup drops it.

The genre block (fused_v3, 2026-10-23). Slot i is
genre_taxonomy.GENRES[i]: 17 genres, then 7 spare slots for appends, which
is why the taxonomy is APPEND-ONLY too. A track's slots are its
genre_scores (50 or 100) / 100, and the block is L2-normalised on its own,
so a one-genre track and a three-genre track weigh the same: W_GENRE says
how much genre counts, the scores only say which genres. An untagged track
(genre_status not 'done', or no scores) has an ALL-ZERO block, neutral to
every genre, as an untagged language is. A slug the code does not know
(scored against a newer taxonomy than this checkout) is ignored.

24 slots: 17 genres on 2026-10-23, and a taxonomy is designed, not
discovered, so it should grow far slower than the language table, which
needed 28 codes by 2026-10-19. Seven spare slots are 40% headroom for 96 bytes a vector
(+2.9%); a width change is the expensive thing -- a new column, a full
re-fuse and a deploy. There is no 'other' slot of the block's own: the
taxonomy has 'other'.

W_GENRE (default 0.10) is taken from MERT alone: W_MERT = W_MERT_V2 -
W_GENRE, with scalars and language unchanged (W_LANG stays 0.40). So
W_GENRE = 0 is fused_v2 exactly, padded with 24 zeros
(tests/ingest/test_fused_vector.py), and replay_bench scores it the same
as fused_v2 on every number.

0.10 was chosen on 2026-10-25 from the grid below: it is the largest
weight that leaves real-user served AUC unchanged (0.711) while genre@10
and listener AUC rise. Listener runs (backlog 15.4) may still move it.
replay_bench --w-genre
(2026-10-23, a copy of prod's 2026-10-20 backup, all 5,270 fused tracks
genre-tagged that day by one tagging session, the tags not reviewed),
shipped dj_replay and same-song filter, W_GENRE 0 / 0.10 / 0.15 / 0.20 /
0.25:
    genre@10 listener      0.500 0.594 0.611 0.625 0.642
    genre@10 real users    0.350 0.378 0.389 0.399 0.415
    served AUC real users  0.711 0.711 0.706 0.696 0.687
    served AUC listener    0.613 0.702 0.728 0.748 0.749
    p@10 language x energy 0.694 0.677 0.646 0.624 0.610
    film3: drone@10        0.22  0.08  0.01  0.00  0.00
lang@10 stayed 0.998-1.000 and the five regression sessions held their
language (film BGM 0.93-0.98 instrumental). Genre holds better with
weight; real-user AUC and energy fit fall, the same tension as dj_replay's
avoid term (docs/backend-todo.md 12.6). The listener sessions were
recorded on another copy (the bench's top8 fidelity is 0.71 here, not
~1.0), so their AUC is the weaker evidence. Choose with the DJ score's
listener runs (scripts/dj_eval/README.md) before shipping;
docs/backend-todo.md 15.

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

W_LANG is 0.40 (fused_v1: 0.20), the rest rescaled in fused_v2 to keep
MERT : scalars at 0.55 : 0.25 (W_MERT_V2, W_SCALAR). Language comes from
the vector alone: a separate session language re-rank was built, measured
and dropped (scripts/dj_eval/language_rerank.py). On the replay benchmark (scripts/dj_eval/
replay_bench.py, 2026-10-19), with dj_replay's stronger skip term, 0.20 left
listener language@10 at 0.868 and 0.40 brought it to 0.952; 0.30 and 0.50
scored within 0.02 of 0.40 on language and the same on real-user AUC. The
block's width does not enter this: a one-hot of any width has norm 1.

Changing SCALAR_COLS, the weights, LANG_BLOCK, GENRE_BLOCK or the order of
LANG_CODES or of genre_taxonomy.GENRES changes the vector SPACE (appending
to either is not a space change: only the tracks that use the new entry
move): every stored vector must be rebuilt, and cosine
against un-rebuilt rows is meaningless. Rebuilding is cheap -- no GPU, since
the MERT half is already stored -- but it is not optional: bump
FUSED_MODEL_VERSION (and backend/services/vectors.py's copy, which keys the
user_embedding_mean cache on it), then run scripts/refuse_vectors.py, which
rewrites every fused vector and drops every cached mean. A change of width
also needs a new column (see scripts/refuse_vectors.py).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import numpy as np

from .genre_taxonomy import GENRES

log = logging.getLogger("vibescape.ingest.fuse")


MERT_MODEL_NAME = "m-a-p/MERT-v1-95M"
SAMPLE_RATE = 24_000
MAX_DURATION_S = 30
MERT_DIM = 768
MERT_MODEL_VERSION = "mert_v1_95m_fp32_30s"
# The recipe the DJ ranks by (backend/services/vectors.py keeps a copy of all three).
FUSED_MODEL_VERSION = "fused_v3_mert_scalar_lang64_genre24"
FUSED_COLUMN = "fused_v3_embedding"
# fused_v2: still written beside fused_v3, served while fused_v3 is not.
FUSED_V2_MODEL_VERSION = "fused_v2_mert_scalar_lang64"
FUSED_V2_COLUMN = "fused_v2_embedding"

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
    "zxx",                                                        # 28: instrumental (2026-10-20)
]
# ISO 639-2 'zxx', "no linguistic content": the tagging verdict that a track
# has no language (language_tagging.tag(clear=True) writes language NULL with
# language_status 'done'), which is an instrumental in almost every case. It
# has its own slot so instrumental sessions hold together; a track that is
# merely untagged (no verdict yet, or a legacy 'no_match') keeps the
# language-neutral all-zero block.
INSTRUMENTAL = "zxx"
# The instrumental slot is set to this, not 1: instrumentals should hold
# together against vocal songs, but a full slot glues drones to film score.
# replay_bench --final2, 2026-10-20 (drones in the top 10 after three
# film-score songs / instrumentals in the film-BGM regression session's
# top 10): weight 1.0: 0.37 / 1.00; 0.5: 0.22 / 0.91; 0.25: 0.18 / 0.82;
# no slot (revision 00028): 0.16 / 0.46. Drone sessions stay drones at every
# weight (0.94-1.00): it is MERT and the scalars (energy, brightness) that
# keep a drone apart from a score, the slot only decides how much "no
# vocals" outweighs them.
INSTRUMENTAL_WEIGHT = 0.5
assert len(LANG_CODES) == len(set(LANG_CODES)) <= OTHER_SLOT
LANG_DIMS = LANG_BLOCK
# Slot i is genre_taxonomy.GENRES[i]; the rest are spare (see "The genre
# block" above).
GENRE_BLOCK = 24
assert len(GENRES) <= GENRE_BLOCK
GENRE_DIMS = GENRE_BLOCK
# fused_v2: MERT and scalars keep fused_v1's 0.55 : 0.25 ratio and share
# what the language block leaves (see "The language block" above for 0.40).
W_MERT_V2, W_SCALAR, W_LANG = 0.4125, 0.1875, 0.40
# fused_v3: the genre block's weight comes out of MERT's. The one knob: a
# starting value, the grid and what is left are under "The genre block".
W_GENRE = 0.10
W_MERT = W_MERT_V2 - W_GENRE
FUSED_V2_DIM = MERT_DIM + len(SCALAR_COLS) + LANG_DIMS
FUSED_DIM = FUSED_V2_DIM + GENRE_DIMS


def v3_weights(w_genre: float = W_GENRE) -> tuple:
    """(W_MERT, W_SCALAR, W_LANG, W_GENRE) for a genre weight: what MERT
    gives up, genre gets. replay_bench's --w-genre grid calls this."""
    return (W_MERT_V2 - w_genre, W_SCALAR, W_LANG, w_genre)


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
        v[langs.index(code) if code in langs else block - 1] = (
            INSTRUMENTAL_WEIGHT if code == INSTRUMENTAL else 1.0)
    return v


def lang_code(row_dict: dict) -> Optional[str]:
    """The code a row's language block is built from: its language, else
    INSTRUMENTAL when tagging gave the 'no language' verdict, else None."""
    lang = str(row_dict.get("language") or "").strip()
    if lang:
        return lang
    return INSTRUMENTAL if row_dict.get("language_status") == "done" else None


def genre_scores(row_dict: dict) -> dict:
    """{slug: score} a row's genre block is built from: its genre_scores
    when genre_status is 'done', else {} (untagged). Accepts the stored JSON
    text or an already-decoded dict. Unparseable text is logged and treated
    as untagged: genre_tagging.tag_many validates every write, so it can
    only come from a hand edit, and a neutral block is the safe reading."""
    if row_dict.get("genre_status") != "done":
        return {}
    raw = row_dict.get("genre_scores")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        scores = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("track %s: unreadable genre_scores %r; genre block left neutral",
                    row_dict.get("id"), raw)
        return {}
    return scores if isinstance(scores, dict) else {}


def _genre_block(row_dict: dict) -> np.ndarray:
    """Scores / 100 in the slots of genre_taxonomy.GENRES, L2'd within the
    block; all zeros for an untagged row. Slugs this checkout's taxonomy
    does not have are ignored."""
    v = np.zeros(GENRE_BLOCK, dtype=np.float32)
    for slug, score in genre_scores(row_dict).items():
        if slug in GENRES:
            try:
                v[GENRES.index(slug)] = float(score) / 100.0
            except (TypeError, ValueError):
                continue
    return _l2(v)


def _head(row_dict: dict, mert_vec: np.ndarray, w_mert, w_scalar, w_lang, langs):
    scalars = np.array(
        [_norm_scalar(c, row_dict.get(c)) for c in SCALAR_COLS],
        dtype=np.float32,
    )
    lang = _lang_onehot(lang_code(row_dict), langs)
    return [
        w_mert   * _l2(mert_vec),
        w_scalar * _l2(scalars),
        w_lang   * lang,
    ]


def _build_fused_v2(row_dict: dict, mert_vec: np.ndarray,
                    langs=None, weights=None) -> np.ndarray:
    """The fused_v2 vector (841) for one row. `langs` and `weights`
    (W_MERT, W_SCALAR, W_LANG) default to fused_v2's; the offline benchmark
    (scripts/dj_eval/replay_bench.py) passes others to compare spaces."""
    w = (W_MERT_V2, W_SCALAR, W_LANG) if weights is None else weights
    return _l2(np.concatenate(_head(row_dict, mert_vec, *w, langs)))


def _build_fused(row_dict: dict, mert_vec: np.ndarray,
                 langs=None, weights=None) -> np.ndarray:
    """The fused vector (fused_v3, FUSED_DIM) for one row: fused_v2's
    blocks plus the genre block, which reads genre_scores and genre_status
    from the row. `weights` is (W_MERT, W_SCALAR, W_LANG, W_GENRE), default
    v3_weights(); the offline benchmark passes others."""
    w_mert, w_scalar, w_lang, w_genre = v3_weights() if weights is None else weights
    head = np.concatenate(_head(row_dict, mert_vec, w_mert, w_scalar, w_lang, langs))
    genre = w_genre * _genre_block(row_dict)
    # The norm block by block, the head's exactly as _l2 takes it: with an
    # all-zero genre block (W_GENRE 0, or an untagged row) the head is then
    # fused_v2's vector bit for bit, not to within a BLAS rounding.
    n_head = float(np.linalg.norm(head))
    n = float(np.sqrt(n_head * n_head + float(genre @ genre)))
    out = np.concatenate([head, genre])
    return out if n < 1e-8 else out / n
