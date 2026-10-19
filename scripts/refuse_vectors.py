"""
Write every track's fused vector in the current recipe
(ingest_pipeline/fused_vector.py) into the recipe's own column, adding the
column first if the database lacks it, and drop every cached library mean.
Dry-run by default.

    python scripts/refuse_vectors.py                      # local SQLite, report only
    python scripts/refuse_vectors.py --apply              # local SQLite, migrate + write
    python scripts/refuse_vectors.py --target prod        # production, report only
    python scripts/refuse_vectors.py --target prod --apply

`--target` is scripts/_target.py: prod credentials come from .env.prod,
never from scripts/_load_gcp_secrets.ps1. Back production up first
(scripts/backup_prod_db.py); this script does not do it for you.

WHY A NEW COLUMN. fused_v2 is 841 wide (a 64-slot language block) and
fused_v1 is 788. libSQL types a vector column by width (F32_BLOB(788)) and
SQLite cannot change a column's type in place, so fused_v2 goes into
FUSED_COLUMN ('fused_v2_embedding', F32_BLOB(841)), added with ALTER TABLE
... ADD COLUMN -- the one table change SQLite and libSQL both do in place,
with no rewrite of existing rows. 'fused_embedding' is left exactly as it
is: the app of the previous deploy still reads it, so rolling back is a
redeploy with no data step. Dropping it is a later cleanup, once prod has
run fused_v2 for a while. Prod had no vector index on either column on
prod_20261019T024426Z (no libsql_vector_idx shadow table in the backup,
libsql_vector_meta_shadow empty), so nothing else depends on the width.

WHEN. Once for fused_v2, and again after any change to the recipe: a new
language appended to fused_vector.LANG_CODES (only that language's tracks
change, and only they are written), a weight, SCALAR_COLS. A change of
width needs a new column and a FUSED_COLUMN of its own, as here.

WHAT. Every track_embeddings row that has a fused vector in either column
and a MERT vector from the CURRENT encoder (stage_fuse.CURRENT_MERT_VERSION,
the gate FuseStage uses). The vector is built from that MERT vector and the
row's current scalars and language by fused_vector._build_fused, the
function FuseStage calls, so a written row equals what the pipeline would
write. A row whose MERT vector is from another encoder is reported and left
alone, as FuseStage refuses it too. The scalar block moves as well as the
language block wherever a row's scalars changed after it was fused
(backlog 1.2).

A row an out-of-date pipeline fused after the migration has a vector only in
the old column; it counts as fused and gets its new one on the next run.

Rows are read in id order, BATCH at a time (keyset on tracks.id), and only
rows whose vector is missing or differs are written, BATCH per
execute_batch (one transaction each, on both backends). Every statement is
an absolute UPDATE, so a run that stops halfway is finished by running it
again, and a second run writes nothing. The end checks, by the database's
own LENGTH() rather than by decoding blobs in Python, that every fused row
with a current MERT vector has a FUSED_DIM-wide vector in the new column.

user_embedding_mean is a cache keyed (user_id, model_version) and the
means are means of fused vectors, so every row of it is deleted (one
statement, after every vector is written) and the app rebuilds each on its
next DJ request. A running instance also holds means in process memory for
up to 10 minutes (_DJ_MEAN_RECHECK_S); a deploy restarts it.
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
    FUSED_COLUMN, FUSED_DIM, FUSED_MODEL_VERSION, MERT_DIM, SCALAR_COLS, _build_fused)
from ingest_pipeline.base import iso_now  # noqa: E402
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION  # noqa: E402

BATCH = 200
TOL = 1e-6
OLD_COLUMN = "fused_embedding"
COLUMN_DDL = f"{FUSED_COLUMN} F32_BLOB({FUSED_DIM})"


def has_column(conn) -> bool:
    rows = conn.execute("SELECT name FROM pragma_table_info('track_embeddings')").fetchall()
    return FUSED_COLUMN in {r["name"] for r in rows}


def _vec(blob, dim):
    if not blob:
        return None
    v = np.frombuffer(bytes(blob), dtype=np.float32)
    return v if v.size == dim else None


def plan(conn, column_exists: bool, batch: int = BATCH):
    """Walk every fused row. Returns (writes, stats): writes is
    [(track_id, new blob)] for the rows whose new vector is missing or
    differs."""
    cur = f"te.{FUSED_COLUMN}" if column_exists else "NULL"
    fused = f"(te.{OLD_COLUMN} IS NOT NULL" + (f" OR {cur} IS NOT NULL)" if column_exists else ")")
    cols = ", ".join(["t.id", "t.language"] + [f"t.{c}" for c in SCALAR_COLS]
                     + ["te.model_version AS mert_version", "te.mert_embedding AS mert_blob",
                        f"{cur} AS new_blob"])
    writes, st = [], Counter()
    last = 0
    while True:
        rows = conn.execute(
            f"SELECT {cols} FROM tracks t JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE {fused} AND t.id > ? ORDER BY t.id LIMIT ?", (last, batch)).fetchall()
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
            new = _build_fused({k: r[k] for k in r.keys()}, mert.copy()).astype(np.float32)
            have = _vec(r["new_blob"], FUSED_DIM)
            if have is not None and float(np.max(np.abs(have - new))) <= TOL:
                st["current"] += 1
                continue
            st["fill" if have is None else "rewrite"] += 1
            writes.append((int(r["id"]), new.tobytes()))


def add_column(conn) -> None:
    conn.execute_batch([(f"ALTER TABLE track_embeddings ADD COLUMN {COLUMN_DDL}", ())])


def apply(conn, writes, batch: int = BATCH) -> int:
    sql = f"UPDATE track_embeddings SET {FUSED_COLUMN} = ?, updated_at = ? WHERE track_id = ?"
    done = 0
    for i in range(0, len(writes), batch):
        chunk = writes[i:i + batch]
        now = iso_now()
        conn.execute_batch([(sql, (blob, now, tid)) for tid, blob in chunk])
        done += len(chunk)
        print(f"  wrote {done}/{len(writes)}", flush=True)
    return done


def wrong_width(conn) -> int:
    """Fused rows with a current MERT vector whose new column is missing or
    not FUSED_DIM floats wide, counted by the database itself."""
    return int(conn.execute(
        f"SELECT COUNT(*) AS n FROM track_embeddings "
        f"WHERE ({OLD_COLUMN} IS NOT NULL OR {FUSED_COLUMN} IS NOT NULL) "
        f"  AND mert_embedding IS NOT NULL AND model_version = ? "
        f"  AND ({FUSED_COLUMN} IS NULL OR LENGTH({FUSED_COLUMN}) != ?)",
        (CURRENT_MERT_VERSION, FUSED_DIM * 4)).fetchone()["n"])


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
    ap.add_argument("--apply", action="store_true", help="migrate + write (default: report only)")
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
        print(f"recipe {FUSED_MODEL_VERSION}, {FUSED_DIM} dims into {FUSED_COLUMN}, "
              f"encoder {CURRENT_MERT_VERSION}")
        exists = has_column(conn)
        print(f"  {'column ' + FUSED_COLUMN:<26} {'present' if exists else 'MISSING: will be added'}")
        writes, st = plan(conn, exists, args.batch)
        for k in ("fused", "current", "fill", "rewrite", "stale_mert_skipped", "no_mert_skipped"):
            print(f"  {k:<26} {st[k]}")
        n_means = count_means(conn)
        if n_means is not None:
            print(f"  {'cached means':<26} {n_means}")
        if not args.apply:
            print("dry-run: nothing was modified. --apply to migrate and write.")
            return 0
        if not exists:
            add_column(conn)
            print(f"added {COLUMN_DDL}")
        if writes:
            apply(conn, writes, args.batch)
        if n_means:
            drop_means(conn)
            print(f"deleted {n_means} cached means")
        left, st2 = plan(conn, True, args.batch)
        bad = wrong_width(conn)
        print(f"verified: {st2['current']} rows current, {len(left)} still to write, "
              f"{bad} without a {FUSED_DIM}-wide vector (LENGTH, server-side)")
        return 0 if not left and not bad else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
