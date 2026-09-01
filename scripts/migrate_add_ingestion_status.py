"""
One-shot migration to add the two-phase ingest columns to the `tracks`
table on Turso (or a local sqlite DB) and backfill legacy rows.

The main `ensure_db()` path in backend/db.py short-circuits on Turso, so
we need a standalone script that talks through backend/db_client.py to
apply the same schema evolution against the remote libSQL instance.

Usage:
    # Point at Turso via env vars (see scripts/_load_gcp_secrets.ps1
    # for how the app pulls TURSO_DATABASE_URL / TURSO_AUTH_TOKEN in prod).
    $env:DB_BACKEND = "turso"
    $env:TURSO_DATABASE_URL = "https://vibescape-...turso.io"
    $env:TURSO_AUTH_TOKEN = "eyJ..."
    python scripts/migrate_add_ingestion_status.py

    # Or dry-run against local sqlite (uses data/vibescape.db).
    python scripts/migrate_add_ingestion_status.py

The script is idempotent — re-running is a no-op on a fully migrated DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

import db_client  # noqa: E402

ALTER_STMTS = [
    "ALTER TABLE tracks ADD COLUMN ingestion_status TEXT DEFAULT 'pending'",
    "ALTER TABLE tracks ADD COLUMN ingestion_error TEXT",
    "ALTER TABLE tracks ADD COLUMN ingestion_attempted_at TIMESTAMP",
]

INDEX_STMT = (
    "CREATE INDEX IF NOT EXISTS idx_tracks_ingestion_status "
    "ON tracks(ingestion_status)"
)

# Same shape as backend/db._backfill_ingestion_status. Rows that already
# have a score (activation / vibe_score / vibe_score_ml) are considered
# fully ingested and get flipped to 'done'. Fresh rows without any score
# stay 'pending' so scripts/run_ingest_worker.py can pick them up.
BACKFILL_STMT = (
    "UPDATE tracks SET ingestion_status = 'done' "
    "WHERE (ingestion_status IS NULL OR ingestion_status = 'pending') "
    "AND (vibe_score IS NOT NULL "
    "     OR vibe_score_ml IS NOT NULL "
    "     OR activation IS NOT NULL)"
)


def _has_column(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any((r[1] == col) for r in rows)


def main() -> int:
    import os
    backend = (os.environ.get("DB_BACKEND") or "sqlite").strip().lower()
    print(f"DB_BACKEND = {backend}")
    if backend in ("turso", "libsql"):
        url = os.environ.get("TURSO_DATABASE_URL") or ""
        # Print only host, not the token.
        print(f"  target: {url}")

    conn = db_client.create_connection()
    try:
        # Sanity: tracks table has to exist.
        info = conn.execute("PRAGMA table_info(tracks)").fetchall()
        if not info:
            print("ERROR: `tracks` table does not exist on this DB. Bail.")
            return 2
        existing_cols = {r[1] for r in info}
        print(f"  tracks columns before: {len(existing_cols)}")

        # 1) ALTER TABLE stmts (each ignored if column already exists).
        added = []
        for stmt in ALTER_STMTS:
            col = stmt.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
            if col in existing_cols:
                print(f"  skip: {col} already present")
                continue
            try:
                conn.execute(stmt)
                conn.commit()
                added.append(col)
                print(f"  added column: {col}")
            except Exception as e:
                # Turso / libsql may raise a different error type; treat
                # "duplicate column" as success so re-runs stay quiet.
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    print(f"  skip (race): {col} already present")
                else:
                    print(f"  FAILED to add {col}: {e}")
                    raise

        # 2) Index.
        try:
            conn.execute(INDEX_STMT)
            conn.commit()
            print("  index idx_tracks_ingestion_status: OK")
        except Exception as e:
            print(f"  WARN index create failed (may already exist): {e}")

        # 3) Backfill: mark scored rows as 'done'.
        before = conn.execute(
            "SELECT ingestion_status, COUNT(*) FROM tracks GROUP BY ingestion_status"
        ).fetchall()
        print(f"  status BEFORE backfill: {[(r[0], r[1]) for r in before]}")

        cur = conn.execute(BACKFILL_STMT)
        conn.commit()
        # rowcount is not always reliable on libSQL over HTTP; report via re-query.
        after = conn.execute(
            "SELECT ingestion_status, COUNT(*) FROM tracks GROUP BY ingestion_status"
        ).fetchall()
        print(f"  status AFTER backfill:  {[(r[0], r[1]) for r in after]}")

    finally:
        conn.close()

    print("migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
