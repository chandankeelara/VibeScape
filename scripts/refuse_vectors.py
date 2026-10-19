"""
Rebuild every stored fused vector in the current recipe
(ingest_pipeline/fused_vector.py) and drop every cached library mean.
Dry-run by default.

    python scripts/refuse_vectors.py                      # local SQLite, report only
    python scripts/refuse_vectors.py --apply              # local SQLite, rewrite
    python scripts/refuse_vectors.py --target prod        # production, report only
    python scripts/refuse_vectors.py --target prod --apply

`--target` is scripts/_target.py: prod credentials come from .env.prod,
never from scripts/_load_gcp_secrets.ps1. Back production up first
(scripts/backup_prod_db.py); this script does not do it for you.

WHEN. After a change to the fused recipe (TOP_LANGS, SCALAR_COLS, the
weights) and its FUSED_MODEL_VERSION bump. Cosine between a vector in the
old space and one in the new is meaningless, so every row is rewritten,
not only new ones. Written for fused_v2 (2026-10-19): untagged tracks
became language-neutral and the language slots were re-derived.

WHAT. Every track_embeddings row with a fused vector and a MERT vector from
the CURRENT encoder (stage_fuse.CURRENT_MERT_VERSION, the gate FuseStage
uses). The new vector is built from that MERT vector and the row's current
scalars and language by fused_vector._build_fused, the function FuseStage
calls, so a re-fused row equals what the pipeline would write. A row whose
MERT vector is from another encoder is reported and left alone: fusing it
would mint a vector from stale audio features, which FuseStage refuses too.

The scalar block moves as well as the language block wherever a row's
scalars changed after it was fused (backlog 1.2: the score recompute
rewrites vibe_score / activation / valence without re-arming fuse).
That is the vector the pipeline would build today; it is reported
separately so the language change and the drift can be told apart.

Rows are read in id order, BATCH at a time (keyset on tracks.id, so no
scan restarts), and only rows whose vector actually changes are written,
BATCH per execute_batch (one transaction each, on both backends). Every
statement is an absolute UPDATE, so a run that stops halfway is finished
by running it again, and a second run writes nothing.

user_embedding_mean is a cache keyed (user_id, model_version) and the
means are means of fused vectors, so after the rewrite every row of it is
wrong: all of them are deleted (one statement, last, after every vector is
written) and the app rebuilds each on its next DJ request. A running
instance also holds means in process memory for up to 10 minutes
(_DJ_MEAN_RECHECK_S); a deploy restarts it.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))
# Explicit, not left to the script directory being sys.path[0]: that entry
# is missing under `python -P` / PYTHONSAFEPATH.
sys.path.insert(0, str(_REPO / "scripts"))

import _target  # noqa: E402
from ingest_pipeline.fused_vector import (  # noqa: E402
    FUSED_DIM, FUSED_MODEL_VERSION, MERT_DIM, SCALAR_COLS, _build_fused)
from ingest_pipeline.base import iso_now  # noqa: E402
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION  # noqa: E402

BATCH = 200
TOL = 1e-6
_COLS = ", ".join(["t.id", "t.language"] + [f"t.{c}" for c in SCALAR_COLS]
                  + ["te.model_version AS mert_version",
                     "te.mert_embedding AS mert_blob", "te.fused_embedding AS fused_blob"])


def _vec(blob, dim):
    if not blob:
        return None
    v = np.frombuffer(bytes(blob), dtype=np.float32)
    return v if v.size == dim else None


def plan(conn, batch: int = BATCH):
    """Walk every fused row. Returns (writes, stats): writes is
    [(track_id, new blob)] for the rows whose vector changes."""
    writes, st = [], Counter()
    last = 0
    while True:
        rows = conn.execute(
            f"SELECT {_COLS} FROM tracks t JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE te.fused_embedding IS NOT NULL AND t.id > ? ORDER BY t.id LIMIT ?",
            (last, batch)).fetchall()
        if not rows:
            return writes, st
        last = int(rows[-1]["id"])
        for r in rows:
            st["fused"] += 1
            if r["mert_version"] != CURRENT_MERT_VERSION:
                st["stale_mert_skipped"] += 1
                continue
            mert = _vec(r["mert_blob"], MERT_DIM)
            if mert is None:
                st["no_mert_skipped"] += 1
                continue
            row = {k: r[k] for k in r.keys()}
            new = _build_fused(row, mert.copy()).astype(np.float32)
            old = _vec(r["fused_blob"], FUSED_DIM)
            if old is not None and float(np.max(np.abs(old - new))) <= TOL:
                st["current"] += 1
                continue
            st["rewrite"] += 1
            if old is not None:
                # Which block moved: the language block is the tail, but the
                # whole vector is renormalised, so compare blocks unscaled.
                n_old, n_new = old / np.linalg.norm(old), new / np.linalg.norm(new)
                lang_moved = not np.allclose(n_old[MERT_DIM + len(SCALAR_COLS):],
                                             n_new[MERT_DIM + len(SCALAR_COLS):], atol=1e-4)
                st["rewrite_lang_block" if lang_moved else "rewrite_other_blocks_only"] += 1
            writes.append((int(r["id"]), new.tobytes()))


def apply(conn, writes, batch: int = BATCH) -> int:
    sql = "UPDATE track_embeddings SET fused_embedding = ?, updated_at = ? WHERE track_id = ?"
    done = 0
    for i in range(0, len(writes), batch):
        chunk = writes[i:i + batch]
        now = iso_now()
        conn.execute_batch([(sql, (blob, now, tid)) for tid, blob in chunk])
        done += len(chunk)
        print(f"  wrote {done}/{len(writes)}", flush=True)
    return done


def drop_means(conn) -> None:
    conn.execute_batch([("DELETE FROM user_embedding_mean", ())])


def count_means(conn):
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM user_embedding_mean").fetchone()["n"])
    except Exception as e:  # the table is optional on Turso (backlog 7.1)
        print(f"user_embedding_mean unreadable ({e}); nothing to invalidate")
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    _target.add_argument(ap)
    ap.add_argument("--apply", action="store_true", help="rewrite (default: report only)")
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print(_target.apply(args.target))   # exits before connecting if prod is not set up
    import db_client
    conn = db_client.create_connection()
    try:
        print(f"recipe {FUSED_MODEL_VERSION}, {FUSED_DIM} dims, encoder {CURRENT_MERT_VERSION}")
        writes, st = plan(conn, args.batch)
        for k in ("fused", "current", "rewrite", "rewrite_lang_block",
                  "rewrite_other_blocks_only", "stale_mert_skipped", "no_mert_skipped"):
            print(f"  {k:<26} {st[k]}")
        n_means = count_means(conn)
        if n_means is not None:
            print(f"  {'cached means':<26} {n_means}")
        if not args.apply:
            print("dry-run: nothing was modified. --apply to rewrite.")
            return 0
        if writes:
            apply(conn, writes, args.batch)
        if n_means:
            drop_means(conn)
            print(f"deleted {n_means} cached means")
        left, st2 = plan(conn, args.batch)
        print(f"verified: {st2['current']} rows current, {len(left)} still to rewrite")
        return 0 if not left else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
