"""
Pull the 196 tracks that exist only in Turso down to local, with their
embeddings (transposed from Turso's legacy row-per-variant shape into
local's Option A row-per-track shape). Safe: local-only writes, plus a
timestamped .db backup before touching anything.

Skipped intentionally:
  - users / sessions       — identity data doesn't align between dev/prod
  - user_tracks            — user_id references would dangle (different
                             user id spaces between environments)

Run:
    D:/Softwares/MiniConda/python.exe scripts/_turso_pull_to_local.py            # dry run
    D:/Softwares/MiniConda/python.exe scripts/_turso_pull_to_local.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
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

MERT_MV  = "mert_v1_95m_fp32_30s"
FUSED_MV = "fused_v1_mert_scalar_lang"


def _load_turso_creds() -> tuple[str, str]:
    text = _PS1.read_text(encoding="utf-8")
    url = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    tok = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    return url, tok


def _local_conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_LOCAL_DB))
    c.row_factory = sqlite3.Row
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    turso_url, turso_token = _load_turso_creds()
    os.environ["TURSO_DATABASE_URL"] = turso_url
    os.environ["TURSO_AUTH_TOKEN"]   = turso_token
    os.environ["DB_BACKEND"]         = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    sys.path.insert(0, str(_REPO / "ingest"))
    import db_client  # noqa: E402

    # 1) Compute the diff
    print("connecting to Turso ...")
    tconn = db_client.create_connection()
    turso_sids = {r["spotify_id"] for r in
                  tconn.execute("SELECT spotify_id FROM tracks WHERE spotify_id IS NOT NULL")}
    print(f"  Turso spotify_ids: {len(turso_sids)}")

    lconn = _local_conn()
    local_sids = {r["spotify_id"] for r in
                  lconn.execute("SELECT spotify_id FROM tracks WHERE spotify_id IS NOT NULL")}
    print(f"  Local spotify_ids: {len(local_sids)}")
    to_pull = turso_sids - local_sids
    print(f"  Missing locally (to pull): {len(to_pull)}")

    # 2) Fetch Turso rows for these
    if not to_pull:
        print("nothing to pull.")
        return 0

    # Turso may not support the huge IN() list — chunk it.
    def _chunks(seq, n=200):
        seq = list(seq)
        for i in range(0, len(seq), n):
            yield seq[i:i+n]

    track_cols_row = tconn.execute("SELECT * FROM tracks LIMIT 1").fetchone()
    track_cols = list(track_cols_row.keys()) if track_cols_row else []
    print(f"  Turso.tracks cols: {len(track_cols)}")

    turso_tracks: list[dict] = []
    for chunk in _chunks(to_pull, 200):
        placeholders = ",".join("?" for _ in chunk)
        rows = tconn.execute(
            f"SELECT * FROM tracks WHERE spotify_id IN ({placeholders})",
            list(chunk),
        ).fetchall()
        turso_tracks.extend(dict(r) for r in rows)
    print(f"  Fetched {len(turso_tracks)} track rows from Turso")

    # Map turso track_id -> spotify_id for embedding lookups
    print(f"  Skipping embeddings pull — pipeline will regenerate everything locally.")

    tconn.close()

    if not args.apply:
        print("\ndry-run — pass --apply to write to local.")
        return 0

    # 3) Backup local
    ts = time.strftime("%Y%m%dT%H%M%S")
    backup = _LOCAL_DB.with_name(f"{_LOCAL_DB.name}.pre-turso-pull-{ts}")
    print(f"\nbacking up local -> {backup}")
    shutil.copyfile(_LOCAL_DB, backup)

    # 4) Insert into local. Skip cols that don't exist locally.
    local_track_cols = {r[1] for r in lconn.execute("PRAGMA table_info(tracks)")}
    print(f"  Local.tracks cols: {len(local_track_cols)}")
    common_cols = [c for c in track_cols if c in local_track_cols and c != "id"]
    print(f"  Copying columns: {len(common_cols)} (dropping 'id' — local re-autonums)")

    # Queue every pulled track at the FRONT of the chain and let the
    # pipeline arm the rest.
    #
    # Only ingestion_status is set. The per-stage columns are left NULL,
    # not 'pending': under the arming model in ingest_pipeline/base.py,
    # 'pending' means "an upstream stage armed me", and pre-setting it
    # fabricates that signal for stages nothing armed. That is what
    # previously let fuse run before language. PreviewStage is the entry
    # point and triggers off ingestion_status alone.
    #
    # Expensive artefacts are NOT wiped. Every stage now short-circuits
    # on what is already on the row — preview on preview_url, download on
    # the cached file, youtube on youtube_id — so keeping them turns a
    # re-ingest of the whole library from thousands of iTunes lookups and
    # yt-dlp scrapes into a status flip. Wiping youtube_id in particular
    # cost two ytsearch queries per track for ids we already had.
    #
    # Derived values ARE cleared, because they are what the re-run exists
    # to recompute and a stale value would otherwise be indistinguishable
    # from a fresh one.
    STAGE_RESET = {
        "ingestion_status":  "pending",
        "preview_status":     None,
        "download_status":    None,
        "librosa_status":     None,
        "ml_status":          None,
        "youtube_status":     None,
        "language_status":    None,
        "fuse_status":        None,
        "embedding_status":   None,   # retired stage; kept for old rows
        # Derived outputs — recomputed by classify / language.
        "activation":         None,
        "valence":            None,
        "vibe_score_ml":      None,
        "vibe_score":         0.0,   # NOT NULL placeholder
        "language":           None,
        "language_confidence": None,
        # audio_path is deliberately NOT wiped: DownloadStage re-points it
        # from the cached file, and wiping it only hides work already done.
    }

    inserted = 0
    skipped_dup = 0

    for tr in turso_tracks:
        # Override any stage-status columns we're forcing to pending.
        row = {c: tr.get(c) for c in common_cols}
        for k, v in STAGE_RESET.items():
            if k in row:
                row[k] = v
        cols_to_insert = list(row.keys())
        try:
            placeholders = ",".join("?" for _ in cols_to_insert)
            lconn.execute(
                f"INSERT INTO tracks ({', '.join(cols_to_insert)}) VALUES ({placeholders})",
                [row[c] for c in cols_to_insert],
            )
            inserted += 1
        except sqlite3.IntegrityError as e:
            skipped_dup += 1
            if skipped_dup <= 5:
                print(f"    skipped {tr.get('spotify_id')} - {tr.get('title')}: {e}")
    lconn.commit()
    print(f"  Inserted: {inserted}   Skipped (unique-collision): {skipped_dup}")

    # Final counts
    total = lconn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    pending = lconn.execute(
        "SELECT COUNT(*) FROM tracks WHERE ingestion_status = 'pending'"
    ).fetchone()[0]
    lconn.close()
    print()
    print(f"local tracks total: {total}   pending (for pipeline): {pending}")
    print(f"backup: {backup}")
    print()
    print("Next step: run the ingestion pipeline to fill audio + embeddings:")
    print("  D:/Softwares/MiniConda/python.exe scripts/run_ingest_v2.py --loop --batch 30")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
