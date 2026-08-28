#!/usr/bin/env python3
"""
One-time data migration: copy a local SQLite VibeScape database into a
Turso/libSQL instance.

Usage:
    python scripts/migrate_to_turso.py \
        --source data/vibescape.db \
        --target-url  $TURSO_DATABASE_URL \
        --target-token $TURSO_AUTH_TOKEN

Steps:
  1. Load schema.sql and apply it to the target (idempotent —
     CREATE ... IF NOT EXISTS everywhere).
  2. For each table in dependency order (users -> sessions -> tracks ->
     user_tracks), stream rows from source and INSERT OR REPLACE them
     into the target, using positional params.
  3. Print row counts on both sides before/after each table.

Note: this script is NOT executed by the migration plan; run it
manually once you're ready to cut over.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema.sql"

TABLES_IN_ORDER = ["users", "sessions", "tracks", "user_tracks"]

BATCH_SIZE = 500


def _load_libsql():
    try:
        import libsql_client  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "This script requires `libsql-client`. "
            "Install with `pip install libsql-client>=0.3.1`."
        ) from e
    return libsql_client


def _count(target_client, table: str) -> int:
    res = target_client.execute(f"SELECT COUNT(*) FROM {table}")
    return int(res.rows[0][0]) if res.rows else 0


def _apply_schema(target_client, schema_sql: str) -> None:
    # Turso libsql-client-py has no executescript equivalent; split on `;`
    # and run one statement at a time. schema.sql has no embedded `;` in
    # string literals or trigger bodies, so this is safe.
    for stmt in schema_sql.split(";"):
        s = stmt.strip()
        if s:
            target_client.execute(s)


def _copy_table(src: sqlite3.Connection, target_client, table: str) -> tuple[int, int]:
    cursor = src.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cursor.description]
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(cols)
    insert_sql = (
        f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    )

    copied = 0
    while True:
        chunk = cursor.fetchmany(BATCH_SIZE)
        if not chunk:
            break
        for row in chunk:
            target_client.execute(insert_sql, list(row))
            copied += 1

    after = _count(target_client, table)
    return copied, after


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="Path to local SQLite DB")
    ap.add_argument("--target-url", required=True, help="libsql://... URL")
    ap.add_argument("--target-token", required=True, help="Turso auth token")
    args = ap.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.exists():
        print(f"[migrate] source not found: {source_path}", file=sys.stderr)
        return 2

    libsql_client = _load_libsql()
    target_client = libsql_client.create_client_sync(
        url=args.target_url, auth_token=args.target_token
    )

    print(f"[migrate] source: {source_path}")
    print(f"[migrate] target: {args.target_url}")

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    print("[migrate] applying schema...")
    _apply_schema(target_client, schema_sql)

    src = sqlite3.connect(str(source_path))
    src.row_factory = sqlite3.Row

    grand_total = 0
    try:
        for table in TABLES_IN_ORDER:
            src_count_row = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            src_count = int(src_count_row[0]) if src_count_row else 0
            before = _count(target_client, table)
            print(
                f"[migrate] {table}: source={src_count} target(before)={before} ..."
            )
            copied, after = _copy_table(src, target_client, table)
            print(
                f"[migrate] {table}: copied={copied} target(after)={after}"
            )
            grand_total += copied
    finally:
        src.close()
        try:
            target_client.close()
        except Exception:
            pass

    print(f"[migrate] done. total rows copied: {grand_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
