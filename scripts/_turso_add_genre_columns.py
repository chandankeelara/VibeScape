"""Add the genre-score columns to `tracks` where they are missing.

backend/db.py's ensure_db() returns at once on Turso, so neither schema.sql
nor _migrate's ALTERs reach production. Run this BEFORE the next pipeline
run against production: `genre` is in run_ingest_v2.py's default stages, so
until these columns exist every default-stage pass there stops at its first
query with "no such column: genre_status" (nothing is written; the run
just does not start). `--stages` without genre still works.

    python scripts/_turso_add_genre_columns.py                       # local, report
    python scripts/_turso_add_genre_columns.py --apply               # local, add
    python scripts/_turso_add_genre_columns.py --target prod         # prod, report
    python scripts/_turso_add_genre_columns.py --target prod --apply

Idempotent: adds only the columns PRAGMA table_info does not list, from
backend/db.py's TRACKS_GENRE_COLUMNS, the list the local migration uses, so
the two cannot differ. No column gets a DEFAULT (see that list's comment).
Back up production first (scripts/backup_prod_db.py). Credentials for
--target prod come from .env.prod (scripts/_target.py), never from
scripts/_load_gcp_secrets.ps1.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

import _target  # noqa: E402

TABLE = "tracks"


def missing_statements(conn) -> list[str]:
    """ALTER TABLE statements for the genre columns `tracks` lacks."""
    from db import TRACKS_GENRE_COLUMNS
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()}
    if not have:
        raise SystemExit(f"{TABLE} does not exist on this target")
    return [f"ALTER TABLE {TABLE} ADD COLUMN {col} {decl}"
            for col, decl in TRACKS_GENRE_COLUMNS if col not in have]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    _target.add_argument(ap)
    ap.add_argument("--apply", action="store_true", help="add them (default: report only)")
    args = ap.parse_args(argv)

    print(_target.apply(args.target))
    import db_client
    conn = db_client.create_connection()
    try:
        todo = missing_statements(conn)
        if not todo:
            print(f"  {TABLE} has every genre column; nothing to do.")
            return 0
        for stmt in todo:
            print(("  apply: " if args.apply else "  would: ") + stmt)
        if not args.apply:
            print("dry-run: nothing was changed. --apply to add them.")
            return 0
        # One by one: ADD COLUMN is DDL, and each lands on its own. A rerun
        # after a partial failure adds only what is still missing.
        for stmt in todo:
            conn.execute(stmt)
            conn.commit()
        print(f"added {len(todo)} column(s) to {TABLE}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
