"""
Push local tracks + track_embeddings to Turso — DESTRUCTIVE to prod.

What it does:
  1. Backs up Turso to a local sqlite snapshot at
     data/_turso_snapshot_<ts>.db  (metadata + tracks only).
  2. On Turso:
       DROP TABLE track_embeddings (if exists);
       DROP TABLE tracks;
       CREATE TABLE tracks    (matching local schema — 62 cols)
       CREATE TABLE track_embeddings (Option A shape, F32_BLOB(768)/(788))
  3. Bulk INSERT every local tracks + track_embeddings row into Turso.

Explicitly NOT touched on Turso:
  - users, sessions, user_tracks — production identity/state; different
    id spaces from local, must survive.

Run:
    D:/Softwares/MiniConda/python.exe scripts/_push_local_to_turso.py            # dry-run
    D:/Softwares/MiniConda/python.exe scripts/_push_local_to_turso.py --apply

--apply is intentionally required. Even in --apply mode, the Turso backup
is written before any destructive statement runs.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_LOCAL_DB = _REPO / "data" / "vibescape.db"
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"


def _load_turso_creds() -> tuple[str, str]:
    text = _PS1.read_text(encoding="utf-8")
    url = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    tok = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    return url, tok


def _chunks(seq, n=100):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _backup_turso_tracks(tconn, out_path: Path) -> int:
    """Dump Turso tracks + track_embeddings (if present) into a local sqlite."""
    if out_path.exists():
        out_path.unlink()
    b = sqlite3.connect(str(out_path))
    # tracks
    row = tconn.execute("SELECT * FROM tracks LIMIT 1").fetchone()
    if row is None:
        b.close()
        return 0
    cols = list(row.keys())
    b.execute(
        f"CREATE TABLE tracks ({', '.join(f'{c}' for c in cols)})"
    )
    tracks = tconn.execute("SELECT * FROM tracks").fetchall()
    placeholders = ",".join("?" for _ in cols)
    b.executemany(
        f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})",
        [[r[c] for c in cols] for r in tracks],
    )
    # Try embeddings — may not exist
    try:
        emb = tconn.execute("SELECT * FROM track_embeddings").fetchall()
        if emb:
            ecols = list(emb[0].keys())
            b.execute(f"CREATE TABLE track_embeddings ({', '.join(ecols)})")
            ep = ",".join("?" for _ in ecols)
            b.executemany(
                f"INSERT INTO track_embeddings ({', '.join(ecols)}) VALUES ({ep})",
                [[r[c] for c in ecols] for r in emb],
            )
    except sqlite3.OperationalError:
        pass
    b.commit()
    b.close()
    return len(tracks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually perform the destructive push. Off by default.")
    args = ap.parse_args()

    if not _LOCAL_DB.exists():
        print(f"missing {_LOCAL_DB}"); return 1

    turso_url, turso_token = _load_turso_creds()
    os.environ["TURSO_DATABASE_URL"] = turso_url
    os.environ["TURSO_AUTH_TOKEN"]   = turso_token
    os.environ["DB_BACKEND"]         = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    sys.path.insert(0, str(_REPO / "ingest"))
    import db_client  # noqa: E402

    print("connecting to Turso ...")
    tconn = db_client.create_connection()

    # Discover local schema for `tracks`
    lconn = sqlite3.connect(str(_LOCAL_DB))
    lconn.row_factory = sqlite3.Row
    local_track_cols = [r[1] for r in lconn.execute("PRAGMA table_info(tracks)")]
    local_track_types = {r[1]: r[2] for r in lconn.execute("PRAGMA table_info(tracks)")}
    # local track_embeddings cols
    local_emb_cols = [r[1] for r in lconn.execute("PRAGMA table_info(track_embeddings)")]
    local_emb_types = {r[1]: r[2] for r in lconn.execute("PRAGMA table_info(track_embeddings)")}

    n_local_tracks = lconn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    n_local_emb = lconn.execute("SELECT COUNT(*) FROM track_embeddings").fetchone()[0]

    n_turso_tracks = tconn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    try:
        n_turso_emb = tconn.execute("SELECT COUNT(*) FROM track_embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        n_turso_emb = "(no table)"

    print()
    print("=== PLAN ===")
    print(f"Local tracks:            {n_local_tracks}")
    print(f"Local track_embeddings:  {n_local_emb}")
    print(f"Turso tracks (current):  {n_turso_tracks}")
    print(f"Turso embeddings (curr): {n_turso_emb}")
    print()
    print(f"Local tracks schema: {len(local_track_cols)} cols")
    print(f"Local track_embeddings schema: {len(local_emb_cols)} cols "
          f"({', '.join(local_emb_cols)})")

    if not args.apply:
        print()
        print("dry-run — pass --apply to actually push.")
        return 0

    # 1) Backup Turso first
    ts = time.strftime("%Y%m%dT%H%M%S")
    backup = _REPO / "data" / f"_turso_snapshot_{ts}.db"
    print(f"\nbacking up Turso -> {backup}")
    n_backed = _backup_turso_tracks(tconn, backup)
    print(f"  backed up {n_backed} track rows")

    # 2) Recreate schema on Turso
    print("\ndropping + recreating tables on Turso ...")
    # DROP order — respect FKs (track_embeddings references tracks)
    try:
        tconn.execute("DROP TABLE IF EXISTS track_embeddings")
    except sqlite3.OperationalError as e:
        print(f"  drop track_embeddings warning: {e}")
    try:
        tconn.execute("DROP TABLE IF EXISTS tracks")
    except sqlite3.OperationalError as e:
        print(f"  drop tracks warning: {e}")

    # Build CREATE TABLE tracks matching local schema. Preserve types
    # verbatim from local's PRAGMA output.
    col_defs = []
    for c in local_track_cols:
        t = local_track_types.get(c, "")
        if c == "id":
            col_defs.append("id INTEGER PRIMARY KEY AUTOINCREMENT")
        else:
            col_defs.append(f"{c} {t}".strip())
    create_tracks_sql = "CREATE TABLE tracks (\n  " + ",\n  ".join(col_defs) + "\n)"
    tconn.execute(create_tracks_sql)
    print("  created tracks")

    # Option A track_embeddings on Turso with typed vector columns.
    tconn.execute(
        """
        CREATE TABLE track_embeddings (
            track_id         INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
            mert_embedding   F32_BLOB(768),
            fused_embedding  F32_BLOB(788),
            model_version    TEXT,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    print("  created track_embeddings (F32_BLOB typed columns)")

    # 3) Bulk insert local tracks into Turso
    tracks = lconn.execute(f"SELECT {', '.join(local_track_cols)} FROM tracks").fetchall()
    placeholders = ",".join("?" for _ in local_track_cols)
    insert_sql = f"INSERT INTO tracks ({', '.join(local_track_cols)}) VALUES ({placeholders})"
    print(f"\ninserting {len(tracks)} tracks into Turso ...")
    n_ins = 0
    for chunk in _chunks(tracks, 50):
        # Turso HTTP has statement size limits — small chunks are safer.
        # executemany fallback: individual inserts per row.
        for r in chunk:
            try:
                tconn.execute(insert_sql, [r[c] for c in local_track_cols])
                n_ins += 1
            except Exception as e:
                print(f"  insert failed sid={r['spotify_id']}: {e}")
        if n_ins % 200 == 0 and n_ins > 0:
            print(f"  ... {n_ins} / {len(tracks)}")
    print(f"  inserted {n_ins} tracks")

    # 4) Bulk insert embeddings
    emb_rows = lconn.execute(
        f"SELECT {', '.join(local_emb_cols)} FROM track_embeddings"
    ).fetchall()
    ep = ",".join("?" for _ in local_emb_cols)
    emb_insert_sql = (
        f"INSERT INTO track_embeddings ({', '.join(local_emb_cols)}) VALUES ({ep})"
    )
    print(f"\ninserting {len(emb_rows)} track_embeddings into Turso ...")
    n_emb_ins = 0
    for r in emb_rows:
        try:
            tconn.execute(emb_insert_sql, [r[c] for c in local_emb_cols])
            n_emb_ins += 1
        except Exception as e:
            print(f"  emb insert failed track_id={r['track_id']}: {e}")
        if n_emb_ins % 200 == 0 and n_emb_ins > 0:
            print(f"  ... {n_emb_ins} / {len(emb_rows)}")
    print(f"  inserted {n_emb_ins} embeddings")

    # 5) Verify
    print("\nverifying ...")
    n_t2 = tconn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    n_e2 = tconn.execute("SELECT COUNT(*) FROM track_embeddings").fetchone()[0]
    print(f"Turso after push: tracks={n_t2}  track_embeddings={n_e2}")
    print(f"backup: {backup}")

    tconn.close()
    lconn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
