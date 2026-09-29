"""
Sync locally-computed pipeline results to Turso — matched on spotify_id.

NON-DESTRUCTIVE. Replaces scripts/_push_local_to_turso.py, which does
DROP TABLE tracks. Two reasons that script is unsafe:

  1. user_tracks.track_id REFERENCES tracks(id) ON DELETE CASCADE, and
     Turso has PRAGMA foreign_keys=1. Dropping tracks deletes every
     user_tracks row — 6008 of them across 3 real users — and its backup
     covers only tracks + track_embeddings, so there is no way back.

  2. Local and prod ids have diverged: the pull discards `id` and lets
     SQLite re-autonumber, so 2439 of 4230 tracks (58%) carry a different
     id locally. Writing local ids into prod would silently re-point
     every surviving user_tracks row at a DIFFERENT SONG — worse than
     deletion, because nothing errors.

This script never drops, never inserts, and never writes `id`. It looks
up each prod track by spotify_id — the one key both environments agree
on — and UPDATEs that row in place. Prod ids never move, so user_tracks
stays correct by construction.

Only pipeline-produced columns are written. Identity (id, spotify_id,
isrc), catalogue metadata (title, artist) and prod-owned state (users,
sessions, user_tracks) are never touched.

Run:
    python scripts/_sync_local_to_turso.py            # dry-run
    python scripts/_sync_local_to_turso.py --apply
    python scripts/_sync_local_to_turso.py --apply --limit 50
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

# Columns the ingest pipeline produces, grouped by the stage that owns them.
SYNC_COLS = [
    # preview
    "preview_url", "preview_source", "apple_id", "genre", "track_view_url",
    "artwork_url", "album", "duration_ms",
    # download — a local cache pointer. Prod streams preview_url and never
    # reads this, but keeping it consistent avoids a confusing NULL sitting
    # next to download_status='done'.
    "audio_path",
    # librosa feature bank
    "tempo", "tempo_stability", "onset_rate", "energy_mean", "energy",
    "energy_std", "brightness", "bandwidth", "rolloff", "spectral_contrast",
    "flatness", "zcr", "timbre_variability", "valence_mode", "tonnetz_std",
    "acousticness", "mfcc_json", "chroma_mean_json",
    # classify (ML vibe)
    "activation", "valence", "activation_relative", "vibe_score", "mood",
    "energy_pred", "danceability_pred", "valence_pred", "vibe_score_ml",
    "model_version", "classification_source", "ml_predicted_at",
    # language
    "language", "language_confidence", "language_top3_json",
    "language_model_version", "language_predicted_at",
    # youtube
    "youtube_id", "youtube_queried_at",
    # stage bookkeeping
    "ingestion_status", "ingestion_error", "ingestion_attempted_at",
    "preview_status", "download_status", "librosa_status", "ml_status",
    "language_status", "fuse_status", "youtube_status", "embedding_status",
]

# Columns prod may lack (added locally by backend/db.py migrations).
# No DEFAULT: under the arming model 'pending' means "an upstream stage
# armed me", and a default would fabricate that for every existing row.
MAYBE_MISSING = {"librosa_status": "TEXT", "fuse_status": "TEXT"}


def _turso():
    text = _PS1.read_text(encoding="utf-8")
    os.environ["TURSO_DATABASE_URL"] = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    os.environ["TURSO_AUTH_TOKEN"] = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    os.environ["DB_BACKEND"] = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    import db_client
    return db_client.create_connection()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    tconn = _turso()
    lconn = sqlite3.connect(f"file:{_LOCAL_DB}?mode=ro", uri=True)
    lconn.row_factory = sqlite3.Row

    prod_cols = {r[1] for r in tconn.execute("PRAGMA table_info(tracks)")}
    missing = [c for c in MAYBE_MISSING if c not in prod_cols]
    if missing:
        print(f"prod is missing columns: {missing}")
        if args.apply:
            for c in missing:
                tconn.execute(f"ALTER TABLE tracks ADD COLUMN {c} {MAYBE_MISSING[c]}")
                print(f"  added {c}")
            prod_cols |= set(missing)
        else:
            print("  (dry-run: would ALTER TABLE to add them)")

    cols = [c for c in SYNC_COLS if c in prod_cols]
    absent = [c for c in SYNC_COLS if c not in prod_cols]
    if absent:
        print(f"skipping columns absent from prod: {absent}")

    # prod's own id, keyed by the identifier both sides agree on.
    prod_ids = {r[1]: r[0] for r in tconn.execute(
        "SELECT id, spotify_id FROM tracks WHERE spotify_id IS NOT NULL")}
    print(f"\nprod tracks with a spotify_id: {len(prod_ids)}")

    sql = f"SELECT id, spotify_id, {', '.join(cols)} FROM tracks WHERE spotify_id IS NOT NULL"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    local = lconn.execute(sql).fetchall()
    matched = [r for r in local if r["spotify_id"] in prod_ids]
    unmatched = [r for r in local if r["spotify_id"] not in prod_ids]
    print(f"local tracks: {len(local)}   matched to prod: {len(matched)}   "
          f"not in prod (skipped, never inserted): {len(unmatched)}")

    emb = {r["track_id"]: r for r in lconn.execute(
        "SELECT track_id, mert_embedding, fused_embedding, model_version "
        "FROM track_embeddings")}
    emb_matched = sum(1 for r in matched if r["id"] in emb)
    print(f"local embeddings: {len(emb)}   pushable for matched tracks: {emb_matched}")

    if not args.apply:
        print("\ndry-run — pass --apply to write. Nothing was modified.")
        print("NOTE: no DROP, no INSERT, no id writes — UPDATE ... WHERE id = <prod id> only.")
        return 0

    set_clause = ", ".join(f"{c} = ?" for c in cols)
    upd = f"UPDATE tracks SET {set_clause} WHERE id = ?"
    print(f"\nupdating {len(matched)} prod tracks ...")
    n = fail = 0
    t0 = time.time()
    for r in matched:
        pid = prod_ids[r["spotify_id"]]
        try:
            tconn.execute(upd, [r[c] for c in cols] + [pid])
            n += 1
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  update failed sid={r['spotify_id']}: {e}")
        if n and n % 250 == 0:
            print(f"  ... {n}/{len(matched)}  ({time.time() - t0:.0f}s)")
    print(f"  updated {n}   failed {fail}")

    print(f"\nupserting {emb_matched} embeddings ...")
    ups = ("INSERT INTO track_embeddings (track_id, mert_embedding, fused_embedding, "
           "  model_version, updated_at) VALUES (?, ?, ?, ?, ?) "
           "ON CONFLICT(track_id) DO UPDATE SET "
           "  mert_embedding  = excluded.mert_embedding, "
           "  fused_embedding = excluded.fused_embedding, "
           "  model_version   = excluded.model_version, "
           "  updated_at      = excluded.updated_at")
    m = efail = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for r in matched:
        e = emb.get(r["id"])
        if not e:
            continue
        pid = prod_ids[r["spotify_id"]]
        try:
            tconn.execute(ups, (pid, e["mert_embedding"], e["fused_embedding"],
                                e["model_version"], now))
            m += 1
        except Exception as ex:
            efail += 1
            if efail <= 5:
                print(f"  emb failed sid={r['spotify_id']}: {ex}")
        if m and m % 250 == 0:
            print(f"  ... {m}/{emb_matched}  ({time.time() - t0:.0f}s)")
    print(f"  upserted {m}   failed {efail}")

    print("\nverifying ...")
    print("  prod tracks:", tconn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
    print("  prod track_embeddings:",
          tconn.execute("SELECT COUNT(*) FROM track_embeddings").fetchone()[0])
    print("  prod user_tracks (MUST be unchanged at 6008):",
          tconn.execute("SELECT COUNT(*) FROM user_tracks").fetchone()[0])
    print("  prod ingestion_status:",
          {str(r[0]): r[1] for r in tconn.execute(
              "SELECT ingestion_status, COUNT(*) FROM tracks GROUP BY 1")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
