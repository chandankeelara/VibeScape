"""Backfill fused (MERT + scalar + language) embeddings into track_embeddings.

Reads existing raw MERT vectors under model_version='mert_v1_95m_fp32_30s',
joins each track to its scalar + language columns in `tracks`, builds the
fused vector using the exact recipe from
`scripts/_recommender_feasibility.py` (SCALAR_COLS / TOP_LANGS / weights),
and writes the result back under model_version='fused_v1_mert_scalar_lang'.

Idempotent via INSERT OR REPLACE on the composite PK (track_id,
model_version). Use --force to overwrite existing fused rows even when
they already exist for the current source MERT vector.

Storage layout (same table the MERT backfill uses):

    track_embeddings(track_id, model_version, dim, embedding BLOB, created_at)

Run:
    D:/Softwares/MiniConda/python.exe scripts/_backfill_fused_embeddings.py
    D:/Softwares/MiniConda/python.exe scripts/_backfill_fused_embeddings.py --dry-run
    D:/Softwares/MiniConda/python.exe scripts/_backfill_fused_embeddings.py --force
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np


# ---- DB connection (matches _backfill_mert_embeddings.py pattern) -----------
os.environ.setdefault("DB_BACKEND", "sqlite")
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
import db_client  # noqa: E402


# ---- Feature spec (duplicated verbatim from _recommender_feasibility.py) ----

SOURCE_MODEL_VERSION = "mert_v1_95m_fp32_30s"
FUSED_MODEL_VERSION = "fused_v1_mert_scalar_lang"

MERT_DIM = 768

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

W_MERT   = 0.55
W_SCALAR = 0.25
W_LANG   = 0.20

FUSED_DIM = MERT_DIM + len(SCALAR_COLS) + LANG_DIMS


# ---- Helpers (duplicated from _recommender_feasibility.py) ------------------


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


def _lang_onehot(language: Optional[str]) -> np.ndarray:
    """Hard one-hot over TOP_LANGS (+ 'other' bucket at the last slot).

    Mirrors the fallback branch of `_lang_dist` in the feasibility script,
    which is what `build_fused_vec` actually invokes there. The feasibility
    script's soft-distribution variant (`_lang_dist`) is not what
    build_fused_vec uses, so we duplicate only the one-hot path here.
    """
    v = np.zeros(LANG_DIMS, dtype=np.float32)
    if language:
        code = str(language).lower()
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


# ---- Logging ----------------------------------------------------------------


_LOG_PATH = _REPO / "data" / "_fused_backfill.log"


class _Tee:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._fh.write(f"\n----- run @ {time.strftime('%Y-%m-%d %H:%M:%S')} -----\n")

    def write(self, msg: str) -> None:
        print(msg, flush=True)
        self._fh.write(msg + "\n")

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


# ---- Main -------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing fused rows for tracks that already "
                         f"have model_version={FUSED_MODEL_VERSION!r}.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen without writing.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of tracks to process (smoke test).")
    args = ap.parse_args()

    log = _Tee(_LOG_PATH)
    log.write(f"[plan] source={SOURCE_MODEL_VERSION!r}  target={FUSED_MODEL_VERSION!r}  "
              f"FUSED_DIM={FUSED_DIM}  force={args.force}  dry_run={args.dry_run}")

    conn = db_client.create_connection()

    # Track rows with scalars + language, restricted to tracks that have a
    # source MERT embedding. Left-join lets us also count existing fused rows
    # so --force gets accurate skip counts.
    scalar_cols_sql = ", ".join("t." + c + " AS " + c for c in SCALAR_COLS)
    rows = conn.execute(
        f"""
        SELECT t.id AS id, t.language AS language, {scalar_cols_sql},
               s.dim AS src_dim, s.embedding AS src_embedding,
               f.track_id AS fused_track_id
          FROM tracks t
          JOIN track_embeddings s
            ON s.track_id = t.id AND s.model_version = ?
     LEFT JOIN track_embeddings f
            ON f.track_id = t.id AND f.model_version = ?
         WHERE t.ingestion_status = 'done'
         ORDER BY t.id
        """,
        (SOURCE_MODEL_VERSION, FUSED_MODEL_VERSION),
    ).fetchall()

    total_source = len(rows)
    already_fused = sum(1 for r in rows if r["fused_track_id"] is not None)
    log.write(f"[plan] tracks with source MERT: {total_source}  "
              f"(already have fused: {already_fused})")

    if not args.force:
        rows = [r for r in rows if r["fused_track_id"] is None]
    if args.limit is not None:
        rows = rows[: args.limit]
    log.write(f"[plan] processing {len(rows)} rows")

    if not rows:
        log.write("[done] nothing to do.")
        conn.close()
        log.close()
        return

    t0 = time.time()
    ok = skipped = failed = 0
    for i, r in enumerate(rows, 1):
        tid = int(r["id"])
        src_dim = int(r["src_dim"] or 0)
        blob = r["src_embedding"]
        if blob is None or src_dim <= 0:
            skipped += 1
            log.write(f"[{i:>4}/{len(rows)}] track {tid} - missing source MERT blob, skip")
            continue
        try:
            mert_vec = np.frombuffer(blob, dtype=np.float32, count=src_dim)
            if mert_vec.size != MERT_DIM:
                skipped += 1
                log.write(f"[{i:>4}/{len(rows)}] track {tid} - source dim {mert_vec.size} "
                          f"!= expected {MERT_DIM}, skip")
                continue
            fused = build_fused_vec(r, mert_vec.astype(np.float32, copy=True))
            if fused.shape[0] != FUSED_DIM:
                failed += 1
                log.write(f"[{i:>4}/{len(rows)}] track {tid} - fused dim {fused.shape[0]} "
                          f"!= expected {FUSED_DIM}, fail")
                continue
            if not args.dry_run:
                conn.execute(
                    "INSERT OR REPLACE INTO track_embeddings "
                    "  (track_id, model_version, dim, embedding, created_at) "
                    "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    (tid, FUSED_MODEL_VERSION, int(FUSED_DIM),
                     fused.astype(np.float32, copy=False).tobytes()),
                )
                if i % 50 == 0:
                    conn.commit()
            ok += 1
        except Exception as e:
            failed += 1
            log.write(f"[{i:>4}/{len(rows)}] track {tid} - fuse failed: {e}")
            continue

        if i % 100 == 0 or i == len(rows):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0.0
            log.write(f"[{i:>4}/{len(rows)}] ok={ok} skipped={skipped} failed={failed} "
                      f"| {rate:.1f} tracks/s")

    if not args.dry_run:
        conn.commit()

    # Final coverage query.
    cov = conn.execute(
        "SELECT COUNT(*) AS n FROM track_embeddings WHERE model_version = ?",
        (FUSED_MODEL_VERSION,),
    ).fetchone()
    coverage = int(cov["n"] if cov else 0)

    conn.close()
    elapsed = time.time() - t0
    log.write(f"\n[done] processed={len(rows)}  ok={ok}  skipped={skipped}  failed={failed}  "
              f"elapsed={elapsed:.1f}s  dry_run={args.dry_run}")
    log.write(f"[done] final fused coverage in DB: {coverage} rows "
              f"(source MERT rows: {total_source})")
    log.close()


if __name__ == "__main__":
    main()
