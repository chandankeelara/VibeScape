"""
Write every track's fused vectors in the current recipes
(ingest_pipeline/fused_vector.py) -- fused_v3 and fused_v2, each into its
own column -- adding a column first if the database lacks it, and drop every
cached library mean. Dry-run by default.

    python scripts/refuse_vectors.py                      # local SQLite, report only
    python scripts/refuse_vectors.py --apply              # local SQLite, migrate + write
    python scripts/refuse_vectors.py --target prod        # production, report only
    python scripts/refuse_vectors.py --target prod --apply

`--target local` (the default) is the SQLite file VIBESCAPE_DB_PATH names,
else data/vibescape.db. `--target` is scripts/_target.py: prod credentials
come from .env.prod, never from scripts/_load_gcp_secrets.ps1. Back
production up first (scripts/backup_prod_db.py); this script does not do it
for you.

WHY NEW COLUMNS. fused_v3 is 865 wide (a 24-slot genre block after
fused_v2's 841), fused_v2 is 841 (a 64-slot language block) and fused_v1 is
788. libSQL types a vector column by width (F32_BLOB(788)) and SQLite
cannot change a column's type in place, so each recipe has its own column:
'fused_v3_embedding' F32_BLOB(865) and 'fused_v2_embedding' F32_BLOB(841),
added with ALTER TABLE ... ADD COLUMN -- the one table change SQLite and
libSQL both do in place, with no rewrite of existing rows. Both are kept
current: the app ranks fused_v3 once that column holds vectors and fused_v2
until then (backend/services/vectors.py _fused_v3_ready), so a rollback to a revision
that reads fused_v2 needs no data step. 'fused_embedding' (fused_v1) is
left exactly as it is, for the same reason, until a cleanup drops it. Prod
had no vector index on any of these columns on prod_20261019T024426Z (no
libsql_vector_idx shadow table in the backup, libsql_vector_meta_shadow
empty), so nothing else depends on the widths.

WHEN. Once per new recipe, and again after any change to one: a new
language appended to fused_vector.LANG_CODES or a genre to
genre_taxonomy.GENRES (only those tracks change, and only they are
written), a weight (W_GENRE above all), SCALAR_COLS. A change of width
needs a new column and a FUSED_COLUMN of its own.

WHAT. Every track_embeddings row that has a fused vector in any column and
a MERT vector from the CURRENT encoder (stage_fuse.CURRENT_MERT_VERSION,
the gate FuseStage uses). Each vector is built from that MERT vector and
the row's current scalars, language and genre scores by the functions
FuseStage calls (fused_vector._build_fused, _build_fused_v2), so a written
row equals what the pipeline would write. A row whose MERT vector is from
another encoder is reported and left alone, as FuseStage refuses it too.
A database without the genre columns (scripts/_turso_add_genre_columns.py
adds them on Turso, db.py locally) has no tags, so every genre block is
written neutral, which is right; the column-adding and tagging can come
later, since a tag on a fused row re-arms fuse.

A row an out-of-date pipeline fused after the migration has a stale or
missing vector in a newer column; it counts as fused and is rewritten on
the next run.

Rows are read in id order, BATCH at a time (keyset on tracks.id). Per row,
only the columns whose vector is missing or differs are written, and rows
where none does are skipped, BATCH rows per execute_batch (one transaction
each, on both backends). Every statement is an absolute UPDATE, so a run
that stops halfway is finished by running it again, and a second run writes
nothing. The end checks, by the database's own LENGTH() rather than by
decoding blobs in Python, that every fused row with a current MERT vector
has a vector of the right width in every recipe's column.

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
    FUSED_COLUMN, FUSED_DIM, FUSED_MODEL_VERSION, FUSED_V2_COLUMN, FUSED_V2_DIM,
    FUSED_V2_MODEL_VERSION, MERT_DIM, SCALAR_COLS, _build_fused, _build_fused_v2)
from ingest_pipeline.base import iso_now  # noqa: E402
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION  # noqa: E402

BATCH = 200
TOL = 1e-6
OLD_COLUMN = "fused_embedding"
# (name, column, width, builder), oldest first: the order columns are added
# and reported in.
RECIPES = (
    ("fused_v2", FUSED_V2_COLUMN, FUSED_V2_DIM, _build_fused_v2),
    ("fused_v3", FUSED_COLUMN, FUSED_DIM, _build_fused),
)
COLUMN_DDL = {col: f"{col} F32_BLOB({dim})" for _n, col, dim, _b in RECIPES}


def present_columns(conn) -> set:
    """Which recipes' columns track_embeddings has."""
    rows = conn.execute("SELECT name FROM pragma_table_info('track_embeddings')").fetchall()
    names = {r["name"] for r in rows}
    return {col for _n, col, _d, _b in RECIPES if col in names}


GENRE_COLS = ("genre_scores", "genre_status")


def genre_columns_present(conn) -> bool:
    """Does `tracks` have the genre columns? Without them no track can be
    tagged, so every genre block is neutral, which is what reading NULL
    gives: the vectors are right, and a tag written once the columns
    exist re-arms fuse (genre_tagging.tag_many)."""
    rows = conn.execute("SELECT name FROM pragma_table_info('tracks')").fetchall()
    return set(GENRE_COLS) <= {r["name"] for r in rows}


def has_column(conn) -> bool:
    """Does track_embeddings have the current recipe's column?"""
    return FUSED_COLUMN in present_columns(conn)


def _vec(blob, dim):
    if not blob:
        return None
    v = np.frombuffer(bytes(blob), dtype=np.float32)
    return v if v.size == dim else None


def plan(conn, present, batch: int = BATCH):
    """Walk every fused row. `present` is the set of recipe columns the
    table has (present_columns). Returns (writes, stats): writes is
    [(track_id, {column: new blob})] for the rows where at least one
    recipe's vector is missing or differs, holding only those columns."""
    present = set(present)
    fused = " OR ".join([f"te.{OLD_COLUMN} IS NOT NULL"]
                        + [f"te.{col} IS NOT NULL" for _n, col, _d, _b in RECIPES if col in present])
    has_genre = genre_columns_present(conn)
    genre = [f"t.{c}" if has_genre else f"NULL AS {c}" for c in GENRE_COLS]
    cols =", ".join(["t.id", "t.language", "t.language_status", *genre]
                     + [f"t.{c}" for c in SCALAR_COLS]
                     + ["te.model_version AS mert_version", "te.mert_embedding AS mert_blob"]
                     + [(f"te.{col}" if col in present else "NULL") + f" AS {name}_blob"
                        for name, col, _d, _b in RECIPES])
    writes, st = [], Counter()
    last = 0
    while True:
        rows = conn.execute(
            f"SELECT {cols} FROM tracks t JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE ({fused}) AND t.id > ? ORDER BY t.id LIMIT ?", (last, batch)).fetchall()
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
            changed = {}
            for name, col, dim, build in RECIPES:
                new = build(row, mert.copy()).astype(np.float32)
                have = _vec(r[f"{name}_blob"], dim)
                if have is not None and float(np.max(np.abs(have - new))) <= TOL:
                    st[f"{name} current"] += 1
                    continue
                st[f"{name} {'fill' if have is None else 'rewrite'}"] += 1
                changed[col] = new.tobytes()
            if changed:
                writes.append((int(r["id"]), changed))
            else:
                st["current"] += 1


def add_columns(conn, missing) -> None:
    """One execute_batch adding each missing recipe column, oldest first."""
    stmts = [(f"ALTER TABLE track_embeddings ADD COLUMN {COLUMN_DDL[col]}", ())
             for _n, col, _d, _b in RECIPES if col in set(missing)]
    if stmts:
        conn.execute_batch(stmts)


def apply(conn, writes, batch: int = BATCH) -> int:
    done = 0
    for i in range(0, len(writes), batch):
        chunk = writes[i:i + batch]
        now = iso_now()
        stmts = []
        for tid, changed in chunk:
            sets = ", ".join(f"{col} = ?" for col in changed)
            stmts.append((f"UPDATE track_embeddings SET {sets}, updated_at = ? WHERE track_id = ?",
                          (*changed.values(), now, tid)))
        conn.execute_batch(stmts)
        done += len(chunk)
        print(f"  wrote {done}/{len(writes)}", flush=True)
    return done


def wrong_width(conn) -> int:
    """Fused rows with a current MERT vector where some recipe's column is
    missing or not that recipe's width, counted by the database itself.
    Needs every recipe column to exist."""
    any_fused = " OR ".join([f"{OLD_COLUMN} IS NOT NULL"]
                            + [f"{col} IS NOT NULL" for _n, col, _d, _b in RECIPES])
    bad = " OR ".join(f"{col} IS NULL OR LENGTH({col}) != {dim * 4}"
                      for _n, col, dim, _b in RECIPES)
    return int(conn.execute(
        f"SELECT COUNT(*) AS n FROM track_embeddings "
        f"WHERE ({any_fused}) "
        f"  AND mert_embedding IS NOT NULL AND model_version = ? "
        f"  AND ({bad})",
        (CURRENT_MERT_VERSION,)).fetchone()["n"])


def drop_means(conn) -> None:
    conn.execute_batch([("DELETE FROM user_embedding_mean", ())])


def count_means(conn):
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM user_embedding_mean").fetchone()["n"])
    except Exception as e:  # the table is optional on Turso (backlog 7.1)
        print(f"user_embedding_mean unreadable ({e}); nothing to invalidate")
        return None


STAT_KEYS = (["fused", "current"]
             + [f"{n} {k}" for n, _c, _d, _b in RECIPES for k in ("current", "fill", "rewrite")]
             + ["stale_mert_skipped", "no_mert_skipped"])


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
        print(f"recipes {FUSED_MODEL_VERSION} ({FUSED_DIM} dims into {FUSED_COLUMN}) and "
              f"{FUSED_V2_MODEL_VERSION} ({FUSED_V2_DIM} into {FUSED_V2_COLUMN}), "
              f"encoder {CURRENT_MERT_VERSION}")
        present = present_columns(conn)
        missing = [col for _n, col, _d, _b in RECIPES if col not in present]
        for _n, col, _d, _b in RECIPES:
            print(f"  {'column ' + col:<26} {'present' if col in present else 'MISSING: will be added'}")
        writes, st = plan(conn, present, args.batch)
        for k in STAT_KEYS:
            print(f"  {k:<26} {st[k]}")
        print(f"  {'rows to write':<26} {len(writes)}")
        n_means = count_means(conn)
        if n_means is not None:
            print(f"  {'cached means':<26} {n_means}")
        if not args.apply:
            print("dry-run: nothing was modified. --apply to migrate and write.")
            return 0
        if missing:
            add_columns(conn, missing)
            print("added " + ", ".join(COLUMN_DDL[c] for c in missing))
        if writes:
            apply(conn, writes, args.batch)
        if n_means:
            drop_means(conn)
            print(f"deleted {n_means} cached means")
        left, st2 = plan(conn, present_columns(conn), args.batch)
        bad = wrong_width(conn)
        print(f"verified: {st2['current']} rows current, {len(left)} still to write, "
              f"{bad} without a vector of each recipe's width (LENGTH, server-side)")
        return 0 if not left and not bad else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
