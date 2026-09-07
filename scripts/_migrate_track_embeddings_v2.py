"""
One-time local migration: transpose track_embeddings from the legacy
(track_id, model_version, embedding) shape into the Option A row-per-track
shape (track_id, mert_embedding, fused_embedding, model_version, updated_at).

Bytes are preserved — F32_BLOB and BLOB share physical layout, so existing
np.float32().tobytes() blobs slot in unchanged.

The two source variants join on track_id:
    mert_v1_95m_fp32_30s   (768-D) -> mert_embedding
    fused_v1_mert_scalar_lang (788-D) -> fused_embedding

Rows that only have one variant end up with NULL in the missing column;
the embedding stage will re-generate the missing one on the next pass
(fetch_pending is unchanged — it looks at embedding_status='pending').

Local-only. Turso is untouched by this script — for Turso, the plan is
to DROP + CREATE the new-shape table there separately and push a fresh
snapshot from this local table (see scripts/_turso_*.py helpers).

Run:
    D:/Softwares/MiniConda/python.exe scripts/_migrate_track_embeddings_v2.py            # dry-run
    D:/Softwares/MiniConda/python.exe scripts/_migrate_track_embeddings_v2.py --apply
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
DB_PATH = _REPO / "data" / "vibescape.db"

MERT_MV  = "mert_v1_95m_fp32_30s"
FUSED_MV = "fused_v1_mert_scalar_lang"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _detect_shape(conn: sqlite3.Connection) -> str:
    """Return 'legacy' | 'v2' | 'missing'."""
    if not _table_exists(conn, "track_embeddings"):
        return "missing"
    cols = _columns(conn, "track_embeddings")
    if "mert_embedding" in cols and "fused_embedding" in cols:
        return "v2"
    if "model_version" in cols and "embedding" in cols:
        return "legacy"
    return "unknown"


def _report_legacy(conn: sqlite3.Connection) -> dict:
    counts = {}
    for mv in (MERT_MV, FUSED_MV):
        n = conn.execute(
            "SELECT COUNT(*) FROM track_embeddings WHERE model_version = ?", (mv,),
        ).fetchone()[0]
        counts[mv] = n
    both = conn.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT track_id FROM track_embeddings
             WHERE model_version IN (?, ?)
             GROUP BY track_id
             HAVING COUNT(DISTINCT model_version) = 2
        )
    """, (MERT_MV, FUSED_MV)).fetchone()[0]
    counts["both"] = both
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually perform the migration (default is dry-run).")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERR: {DB_PATH} does not exist"); return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        shape = _detect_shape(conn)
        print(f"current track_embeddings shape: {shape}")
        if shape == "v2":
            print("already migrated; nothing to do.")
            return 0
        if shape == "missing":
            print("no existing track_embeddings table; will just create the v2 shape.")
        if shape == "legacy":
            counts = _report_legacy(conn)
            print(f"legacy row counts:")
            print(f"  {MERT_MV}:  {counts[MERT_MV]}")
            print(f"  {FUSED_MV}: {counts[FUSED_MV]}")
            print(f"  tracks with BOTH variants: {counts['both']}")

        if not args.apply:
            print("\ndry-run — pass --apply to perform the migration.")
            return 0

        print("\napplying migration...")
        conn.execute("BEGIN")
        # 1. New table with Option A shape
        conn.execute("""
            CREATE TABLE track_embeddings_new (
                track_id         INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
                mert_embedding   F32_BLOB(768),
                fused_embedding  F32_BLOB(788),
                model_version    TEXT,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Transpose from legacy if it exists — one row per distinct track_id
        if shape == "legacy":
            conn.execute("""
                INSERT INTO track_embeddings_new
                    (track_id, mert_embedding, fused_embedding, model_version, updated_at)
                SELECT
                    t.track_id,
                    m.embedding                AS mert_embedding,
                    f.embedding                AS fused_embedding,
                    COALESCE(f.model_version, m.model_version)  AS model_version,
                    COALESCE(f.created_at, m.created_at, CURRENT_TIMESTAMP) AS updated_at
                FROM (SELECT DISTINCT track_id FROM track_embeddings) t
                LEFT JOIN track_embeddings m ON m.track_id = t.track_id AND m.model_version = ?
                LEFT JOIN track_embeddings f ON f.track_id = t.track_id AND f.model_version = ?
            """, (MERT_MV, FUSED_MV))
            n_new = conn.execute("SELECT COUNT(*) FROM track_embeddings_new").fetchone()[0]
            print(f"  transposed {n_new} rows into track_embeddings_new")
            conn.execute("DROP TABLE track_embeddings")

        conn.execute("ALTER TABLE track_embeddings_new RENAME TO track_embeddings")
        conn.execute("COMMIT")

        # 3. Verify
        cols = _columns(conn, "track_embeddings")
        n = conn.execute("SELECT COUNT(*) FROM track_embeddings").fetchone()[0]
        n_mert = conn.execute("SELECT COUNT(*) FROM track_embeddings WHERE mert_embedding IS NOT NULL").fetchone()[0]
        n_fused = conn.execute("SELECT COUNT(*) FROM track_embeddings WHERE fused_embedding IS NOT NULL").fetchone()[0]
        print(f"\nverified new shape:")
        print(f"  columns: {cols}")
        print(f"  total rows: {n}")
        print(f"  rows with mert_embedding NOT NULL:  {n_mert}")
        print(f"  rows with fused_embedding NOT NULL: {n_fused}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
