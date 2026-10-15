"""
Apply scripts/reset_shared_previews.py's reset to PRODUCTION (Turso).

    python scripts/backup_prod_db.py                                   # first, always
    python scripts/reset_shared_previews.py --db data/backups/prod_<ts>.db
    python scripts/_turso_reset_shared_previews.py data/_reingest_shared_previews_report_prod_<ts>.json
    python scripts/_turso_reset_shared_previews.py <same report> --apply

Takes the report a --db dry-run wrote against a prod backup, so the
selection is made on prod's own data — prod holds tracks the local DB does
not, and a collision can pair a local track with a prod-only one.

Credentials come from .env.prod through scripts/_target.py, like
run_ingest_v2.py --target prod: this script must not become another reader
of the secrets file (root CLAUDE.md). It used to borrow the connection of
_sync_local_to_turso.py, retired 2026-10-15.

Matches on spotify_id, never tracks.id (the local DB's ids diverged from
prod's). Never touches user_tracks. Two statements, each committed on its
own (UPDATE tracks, then DELETE the embeddings), and a re-run converges on
the same state.

After this the rows read ingestion_status='pending' in prod and drop out of
/similar and the vibe queues (both require 'done') until the pipeline
re-ingests them: run_ingest_v2.py --target prod.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "backend"))
import _target  # noqa: E402
import db_client  # noqa: E402
from reset_shared_previews import _DERIVED, _PREVIEW, _STAGES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rep = json.loads(args.report.read_text(encoding="utf-8"))
    sids = sorted({r["spotify_id"] for g in rep["reset"] for r in g if r.get("spotify_id")})
    if not sids:
        print("report has no tracks to reset")
        return 1
    ph = ",".join("?" * len(sids))

    print(_target.apply("prod"))
    t = db_client.create_connection()

    def state():
        tr = t.execute(f"SELECT ingestion_status, COUNT(*) FROM tracks WHERE spotify_id IN ({ph}) "
                       f"GROUP BY 1", sids).fetchall()
        emb = t.execute(f"SELECT COUNT(*) FROM track_embeddings WHERE track_id IN "
                        f"(SELECT id FROM tracks WHERE spotify_id IN ({ph}))", sids).fetchone()[0]
        ut = t.execute("SELECT COUNT(*) FROM user_tracks").fetchone()[0]
        return {str(r[0]): r[1] for r in tr}, emb, ut

    before = state()
    print(f"report: {args.report.name}  tracks: {len(sids)}")
    print(f"prod now: ingestion_status {before[0]}  embeddings {before[1]}  user_tracks {before[2]}")
    if not args.apply:
        print("dry-run — pass --apply to write. Nothing was modified.")
        return 0

    sets = ", ".join(f"{c} = 0.0" if c == "vibe_score" else f"{c} = NULL"
                     for c in _DERIVED + _PREVIEW + _STAGES)
    t.execute(f"UPDATE tracks SET {sets}, ingestion_status = 'pending', ingestion_error = NULL "
              f"WHERE spotify_id IN ({ph})", sids)
    t.execute(f"DELETE FROM track_embeddings WHERE track_id IN "
              f"(SELECT id FROM tracks WHERE spotify_id IN ({ph}))", sids)
    try:
        t.commit()
    except Exception:
        pass

    after = state()
    print(f"prod after: ingestion_status {after[0]}  embeddings {after[1]}  "
          f"user_tracks {after[2]} (must equal {before[2]})")
    return 0 if after[2] == before[2] and after[1] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
