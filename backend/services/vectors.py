"""Track embeddings: the MERT and fused vector variants, which of them the
database can serve (_variant_spec), and the loaders that read and decode
them.

Moved out of app.py unchanged (refactor PR 2, 2026-10-25). The fused
constants must match ingest_pipeline/fused_vector.py
(tests/backend/test_dj_paths.py checks). _fused_v2_state / _fused_v3_state
are mutated in place, never rebound.
"""
import os
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from bootstrap import log

MERT_MODEL_VERSION = "mert_v1_95m_fp32_30s"
MERT_DIM = 768

# Fused variant, fused_v3: MERT (768) + scalar features (9) + a 64-slot
# language block (one slot per tagged language, untagged all zeros) + a
# 24-slot genre block (one slot per genre_taxonomy genre, untagged all
# zeros). The recipe is ingest_pipeline/fused_vector.py, and these must match
# it (tests/backend/test_dj_paths.py checks). Each width lives in its own
# column; fused_v1's 788-wide vectors stay in 'fused_embedding' for a
# rollback until a cleanup drops them. The version also keys the
# user_embedding_mean cache, so bumping it is what stops a mean of old-space
# vectors being reused.
FUSED_MODEL_VERSION = "fused_v3_mert_scalar_lang64_genre24"
FUSED_COLUMN = "fused_v3_embedding"
FUSED_SCALAR_DIMS = 9
FUSED_LANG_DIMS = 64
FUSED_GENRE_DIMS = 24
FUSED_DIM = MERT_DIM + FUSED_SCALAR_DIMS + FUSED_LANG_DIMS + FUSED_GENRE_DIMS  # 865

# fused_v2, ranked while FUSED_COLUMN is missing or holds no vector yet
# (_fused_v3_ready): a deploy that lands before scripts/refuse_vectors.py has
# filled fused_v3 serves exactly what the previous revision did. Same avoid
# constants as v3 (dj_replay protects the same language block in both).
FUSED_V2_MODEL_VERSION = "fused_v2_mert_scalar_lang64"
FUSED_V2_COLUMN = "fused_v2_embedding"
FUSED_V2_DIM = MERT_DIM + FUSED_SCALAR_DIMS + FUSED_LANG_DIMS  # 841

# fused_v1, ranked only while fused_v2's column does not exist yet either.
# The whole request then stays in one version -- its column, width,
# mean-cache key and, for v1, the avoid constants it was tuned with -- so a
# query is never ranked against vectors of another width.
FUSED_V1_MODEL_VERSION = "fused_v1_mert_scalar_lang"
FUSED_V1_COLUMN = "fused_embedding"
FUSED_V1_DIM = MERT_DIM + FUSED_SCALAR_DIMS + 11  # 788
FUSED_V1_AVOID = {"neg_weight": 0.6, "m_step": 0.25, "m_max": 1.0,
                  "avoid_perp": False, "avoid_cap": None}
_FUSED_V2_RECHECK_S = 60.0
_fused_v2_state = {"ready": None, "checked_at": 0.0, "warned": False}
_fused_v3_state = {"ready": None, "checked_at": 0.0, "warned": False}


def _column_ready(conn, st: dict, col: str, needs_rows: bool, fallback: str) -> bool:
    """Is `col` usable for ranking? Once it is, that is cached for the life
    of the process; a no is re-checked every _FUSED_V2_RECHECK_S, so a
    migration run while instances are up is picked up without a restart.
    Without `needs_rows` the column only has to exist (a zero-row SELECT, not
    PRAGMA: it fails the same way on SQLite and over Hrana); with it, one row
    must hold a vector. That second query stops at its first hit, so it is a
    scan only while the column exists and is empty -- on Turso, the minutes
    between refuse_vectors.py adding the column and writing it, or a
    database where it was added and never filled."""
    now = datetime.now(timezone.utc).timestamp()
    if st["ready"] or (st["ready"] is False and now - st["checked_at"] < _FUSED_V2_RECHECK_S):
        return bool(st["ready"])
    try:
        if needs_rows:
            ready = conn.execute(f"SELECT 1 FROM track_embeddings WHERE {col} IS NOT NULL "
                                 f"LIMIT 1").fetchone() is not None
            why = "no vectors"
        else:
            conn.execute(f"SELECT {col} FROM track_embeddings LIMIT 0").fetchall()
            ready, why = True, ""
    except Exception as e:
        ready, why = False, str(e)
    if not ready and not st["warned"]:
        st["warned"] = True
        log.warning("[dj] %s not ready (%s): ranking %s until "
                    "scripts/refuse_vectors.py has run", col, why, fallback)
    st["ready"], st["checked_at"] = ready, now
    return ready


def _fused_v2_ready(conn) -> bool:
    """Does track_embeddings have fused_v2's column?"""
    return _column_ready(conn, _fused_v2_state, FUSED_V2_COLUMN, False, FUSED_V1_COLUMN)


def _fused_v3_ready(conn) -> bool:
    """Does track_embeddings have fused_v3's column, with vectors in it?
    Empty counts as not ready: db.py adds the column to every local file
    and refuse_vectors.py adds it before it writes, and an empty column
    ranked would leave the DJ nothing to serve."""
    return _column_ready(conn, _fused_v3_state, FUSED_COLUMN, True, FUSED_V2_COLUMN)

# Default DJ variant; override per-request with SimilarBody.variant.
_DJ_VARIANT_DEFAULT = (os.environ.get("DJ_EMBEDDING_VARIANT") or "fused").strip().lower()
if _DJ_VARIANT_DEFAULT not in ("fused", "mert"):
    _DJ_VARIANT_DEFAULT = "fused"


def _variant_spec(variant: str, conn=None):
    """Return (model_version, dim) for the requested variant. 'fused' is
    fused_v3 once the database holds it (_fused_v3_ready), else fused_v2,
    else fused_v1 while there is no fused_v2 column (_fused_v2_ready)."""
    v = (variant or "").strip().lower()
    if v == "mert":
        return MERT_MODEL_VERSION, MERT_DIM
    if conn is not None and not _fused_v3_ready(conn):
        if not _fused_v2_ready(conn):
            return FUSED_V1_MODEL_VERSION, FUSED_V1_DIM
        return FUSED_V2_MODEL_VERSION, FUSED_V2_DIM
    return FUSED_MODEL_VERSION, FUSED_DIM


_ANCHOR_COLS = (
    "id, apple_id, spotify_id, mood, "
    "vibe_score, vibe_score_ml, energy_pred, "
    "danceability_pred, valence_pred"
)


def _resolve_anchor(conn, track_key):
    """Resolve a track_key to a tracks row.

    Accepts either a spotify_id string (canonical external key) or a numeric
    value matching the internal tracks.id PK. The legacy apple_id lookup path
    has been retired — every row in the DB has a spotify_id (verified in
    audit), and internal callers now pass either spotify_id or tracks.id.
    """
    # Numeric input → treat as internal PK. Ints on the wire come from the
    # frontend's hot path (dj excludes/pos/neg use t.id directly to avoid
    # an N-round-trip resolution loop).
    if isinstance(track_key, int):
        return conn.execute(
            f"SELECT {_ANCHOR_COLS} FROM tracks WHERE id = ?",
            (track_key,),
        ).fetchone()
    # String input → try spotify_id first (fast path), then fall back to
    # int-parseable string as internal id.
    row = conn.execute(
        f"SELECT {_ANCHOR_COLS} FROM tracks WHERE spotify_id = ?",
        (str(track_key),),
    ).fetchone()
    if row:
        return row
    try:
        tid_int = int(track_key)
    except (TypeError, ValueError):
        return None
    return conn.execute(
        f"SELECT {_ANCHOR_COLS} FROM tracks WHERE id = ?",
        (tid_int,),
    ).fetchone()


def _embedding_column_for(model_version: str) -> Optional[str]:
    """Map a logical model_version to the physical column in the new
    Option-A track_embeddings layout (one row per track, both variants
    inline as separate typed vector columns)."""
    if model_version == MERT_MODEL_VERSION:
        return "mert_embedding"
    if model_version == FUSED_MODEL_VERSION:
        return FUSED_COLUMN
    if model_version == FUSED_V2_MODEL_VERSION:
        return FUSED_V2_COLUMN
    if model_version == FUSED_V1_MODEL_VERSION:
        return FUSED_V1_COLUMN
    return None


def _is_turso() -> bool:
    return (os.environ.get("DB_BACKEND") or "").strip().lower() in ("turso", "libsql")


def _decode_embedding_cell(val) -> Optional[np.ndarray]:
    """Decode an embedding cell in either form the loaders below select:
      - Real bytes (the raw column): np.frombuffer
      - Text form '[a,b,c,...]' (Turso via vector_extract()): parse
    Returns None if the cell is empty / invalid.

    The text form exists because db_client used to decode a raw F32_BLOB
    read from Turso to empty bytes whenever its length was not a multiple
    of 3, every 788-float fused vector included. Fixed 2026-10-15: the
    probe that day showed the padded decode db_client now does returns a
    fused blob's 3152 bytes. The Turso loaders still select
    vector_extract() on purpose until a production run exercises the raw
    read; then they can drop it."""
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray, memoryview)):
        if len(val) == 0:
            return None
        return np.frombuffer(val, dtype=np.float32).astype(np.float32, copy=True)
    if isinstance(val, str):
        s = val.strip()
        if not s or s == "[]":
            return None
        s = s.lstrip("[").rstrip("]")
        try:
            return np.array([float(x) for x in s.split(",")], dtype=np.float32)
        except ValueError:
            return None
    return None


def _load_mert_vec(conn, track_id: int,
                   model_version: str = MERT_MODEL_VERSION,
                   expected_dim: int = MERT_DIM) -> Optional[np.ndarray]:
    col = _embedding_column_for(model_version)
    if col is None:
        return None
    # On Turso, read the text form through vector_extract(). The raw blob
    # decodes correctly since 2026-10-15 (see _decode_embedding_cell); this
    # path is kept deliberately until a production run exercises that read.
    if _is_turso():
        row = conn.execute(
            f"SELECT vector_extract({col}) AS emb "
            f"FROM track_embeddings WHERE track_id = ?",
            (track_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT {col} AS emb FROM track_embeddings WHERE track_id = ?",
            (track_id,),
        ).fetchone()
    if not row:
        return None
    vec = _decode_embedding_cell(row["emb"])
    if vec is None or vec.shape[0] != expected_dim:
        return None
    return vec


def _load_mert_vecs_bulk(conn, track_ids: list,
                         model_version: str = MERT_MODEL_VERSION,
                         expected_dim: int = MERT_DIM) -> dict:
    """Return {track_id: np.ndarray} for track_ids that have an embedding
    for the requested variant. Rows with NULL in the target column are
    skipped (track has the other variant but not this one)."""
    out: dict = {}
    if not track_ids:
        return out
    col = _embedding_column_for(model_version)
    if col is None:
        return out
    # Chunk to keep SQL param counts sane.
    CHUNK = 400
    turso = _is_turso()
    # vector_extract() on Turso, kept deliberately: see _decode_embedding_cell.
    select_expr = f"vector_extract({col})" if turso else col
    for i in range(0, len(track_ids), CHUNK):
        chunk = track_ids[i:i + CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        sql = (
            f"SELECT track_id, {select_expr} AS emb FROM track_embeddings "
            f"WHERE track_id IN ({placeholders}) AND {col} IS NOT NULL"
        )
        rows = conn.execute(sql, tuple(chunk)).fetchall()
        for r in rows:
            v = _decode_embedding_cell(r["emb"])
            if v is None or v.shape[0] != expected_dim:
                continue
            out[int(r["track_id"])] = v
    return out


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return v
    return v / n
