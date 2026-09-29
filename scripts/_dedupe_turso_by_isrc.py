"""
Deduplicate prod tracks that are the same recording — matched on ISRC.

Same logic as scripts/_dedupe_local_by_isrc.py, which was run against the
local DB first and verified: 237 rows removed, 0 orphaned user_tracks,
0 duplicate groups left, and no user lost a recording.

Why ISRC and not spotify_id: spotify_id is UNIQUE, so grouping on it finds
nothing (0 groups). The duplicates are one recording released under several
Spotify IDs — a single, its parent album, a compilation. ISRC identifies a
recording, so it merges those while keeping remixes and live versions apart
(those carry their own ISRC).

THE ORDERING IS THE SAFETY ARGUMENT.
  user_tracks.track_id REFERENCES tracks(id) ON DELETE CASCADE, prod has
  PRAGMA foreign_keys=1, and 456 duplicate rows sit in real user libraries.
  Deleting a loser before re-pointing would silently remove that song from
  the user's library. So every affected user_tracks row is moved onto the
  survivor FIRST; only then are the losers deleted, by which point nothing
  references them and the cascade has nothing to take.

  328 (user, isrc) pairs have the SAME user holding BOTH copies. For those a
  plain UPDATE would violate PRIMARY KEY (user_id, track_id), so the loser's
  listening history is folded into the survivor's row (play_count summed,
  latest last_played, earliest added_at) and the loser row is dropped.

Survivor: ingestion_status='done' > has an embedding > lowest id.

Never touches users or sessions. Never writes tracks.id. Never DROPs.

Run:
    python scripts/_dedupe_turso_by_isrc.py            # dry-run
    python scripts/_dedupe_turso_by_isrc.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"


def _turso():
    text = _PS1.read_text(encoding="utf-8")
    os.environ["TURSO_DATABASE_URL"] = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    os.environ["TURSO_AUTH_TOKEN"] = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    os.environ["DB_BACKEND"] = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    import db_client
    return db_client.create_connection()


def _backup(tconn, path: Path) -> None:
    """Snapshot tracks + user_tracks locally before mutating prod.

    track_embeddings is excluded on purpose: ~24MB of blobs, and fully
    reproducible from the local DB via _sync_local_to_turso.py. user_tracks
    is the one thing that cannot be reconstructed from anywhere.
    """
    if path.exists():
        path.unlink()
    b = sqlite3.connect(str(path))
    for table in ("tracks", "user_tracks"):
        cols = [r[1] for r in tconn.execute(f"PRAGMA table_info({table})")]
        b.execute(f"CREATE TABLE {table} ({', '.join(c + ' TEXT' for c in cols)})")
        rows = tconn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        b.executemany(
            f"INSERT INTO {table} VALUES ({','.join('?' * len(cols))})",
            [[r[c] for c in cols] for r in rows])
        print(f"  backed up {len(rows)} rows from {table}")
    b.commit()
    b.close()
    print(f"  -> {path.name}")


def snapshot_user_isrcs(conn) -> dict:
    """{user_id: {isrc, ...}} — the invariant dedup must not change.

    Row counts legitimately fall when two rows merge into one. What must
    never change is WHICH recordings each user holds.
    """
    out = defaultdict(set)
    for r in conn.execute(
        "SELECT ut.user_id, t.isrc FROM user_tracks ut "
        "JOIN tracks t ON t.id = ut.track_id "
        "WHERE t.isrc IS NOT NULL AND t.isrc != ''"
    ):
        out[r[0]].add(r[1])
    return dict(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = _turso()

    rows = conn.execute(
        "SELECT id, isrc, title, ingestion_status FROM tracks "
        "WHERE isrc IS NOT NULL AND isrc != ''"
    ).fetchall()
    by_isrc = defaultdict(list)
    for r in rows:
        by_isrc[r["isrc"]].append(r)
    groups = {k: v for k, v in by_isrc.items() if len(v) > 1}

    has_emb = {r[0] for r in conn.execute(
        "SELECT track_id FROM track_embeddings WHERE fused_embedding IS NOT NULL")}

    def rank(r):
        return (0 if r["ingestion_status"] == "done" else 1,
                0 if r["id"] in has_emb else 1,
                r["id"])

    plan = []
    for isrc, members in sorted(groups.items()):
        o = sorted(members, key=rank)
        plan.append((o[0]["id"], [m["id"] for m in o[1:]], isrc, o[0]["title"]))
    losers = [l for _, ls, _, _ in plan for l in ls]

    n_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    n_ut = conn.execute("SELECT COUNT(*) FROM user_tracks").fetchone()[0]
    print(f"duplicate ISRC groups : {len(plan)}")
    print(f"rows to remove        : {len(losers)}")
    print(f"tracks   : {n_tracks} -> {n_tracks - len(losers)}")

    ut_rows = defaultdict(list)
    for i in range(0, len(losers), 200):
        chunk = losers[i:i + 200]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT user_id, track_id, play_count, last_played, added_at "
            f"FROM user_tracks WHERE track_id IN ({ph})", chunk):
            ut_rows[r["track_id"]].append(r)

    survivors = [s for s, _, _, _ in plan]
    owned = set()
    for i in range(0, len(survivors), 200):
        chunk = survivors[i:i + 200]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT user_id, track_id FROM user_tracks WHERE track_id IN ({ph})", chunk):
            owned.add((r["user_id"], r["track_id"]))

    sim = set(owned)
    merges = repoints = 0
    for sid, ls, _, _ in plan:
        for lid in ls:
            for r in ut_rows.get(lid, []):
                if (r["user_id"], sid) in sim:
                    merges += 1
                else:
                    repoints += 1
                    sim.add((r["user_id"], sid))
    print(f"user_tracks rows on losers : {sum(len(v) for v in ut_rows.values())}")
    print(f"  merged into existing     : {merges}")
    print(f"  re-pointed               : {repoints}")
    print(f"user_tracks : {n_ut} -> {n_ut - merges}")
    print("\nsample:")
    for sid, ls, isrc, title in plan[:5]:
        print(f"  keep id={sid:5} drop {ls}  isrc={isrc}  {str(title)[:34]}")

    if not args.apply:
        print("\ndry-run — pass --apply to execute. Nothing was modified.")
        return 0

    ts = time.strftime("%Y%m%dT%H%M%S")
    print("\nbacking up prod ...")
    _backup(conn, _REPO / "data" / f"_turso_pre_dedupe_{ts}.db")

    before = snapshot_user_isrcs(conn)

    print("\nre-pointing user_tracks (BEFORE any delete) ...")
    n_merge = n_move = 0
    live = set(owned)
    for sid, ls, _, _ in plan:
        for lid in ls:
            for r in ut_rows.get(lid, []):
                uid = r["user_id"]
                if (uid, sid) in live:
                    conn.execute(
                        "UPDATE user_tracks SET "
                        "  play_count  = COALESCE(play_count,0) + ?, "
                        "  last_played = MAX(COALESCE(last_played,''), COALESCE(?,'')), "
                        "  added_at    = MIN(COALESCE(added_at,'9999'), COALESCE(?,'9999')) "
                        "WHERE user_id = ? AND track_id = ?",
                        (r["play_count"] or 0, r["last_played"], r["added_at"], uid, sid))
                    conn.execute(
                        "DELETE FROM user_tracks WHERE user_id = ? AND track_id = ?",
                        (uid, lid))
                    n_merge += 1
                else:
                    conn.execute(
                        "UPDATE user_tracks SET track_id = ? WHERE user_id = ? AND track_id = ?",
                        (sid, uid, lid))
                    live.add((uid, sid))
                    n_move += 1
                if (n_merge + n_move) % 100 == 0:
                    print(f"  ... {n_merge + n_move}")
    print(f"  merged {n_merge}   re-pointed {n_move}")

    print("\nremoving duplicate rows ...")
    n_del = 0
    for lid in losers:
        try:
            conn.execute("DELETE FROM track_embeddings WHERE track_id = ?", (lid,))
            conn.execute("DELETE FROM tracks WHERE id = ?", (lid,))
            n_del += 1
        except Exception as e:
            print(f"  delete failed id={lid}: {e}")
        if n_del and n_del % 50 == 0:
            print(f"  ... {n_del}/{len(losers)}")
    print(f"  removed {n_del}")

    print("\nverifying ...")
    after = snapshot_user_isrcs(conn)
    lost = {u: sorted(before[u] - after.get(u, set())) for u in before}
    lost = {u: v for u, v in lost.items() if v}
    print(f"  tracks: {conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]}")
    print(f"  track_embeddings: {conn.execute('SELECT COUNT(*) FROM track_embeddings').fetchone()[0]}")
    print(f"  user_tracks: {conn.execute('SELECT COUNT(*) FROM user_tracks').fetchone()[0]}")
    print("  orphaned user_tracks (MUST be 0): "
          f"{conn.execute('SELECT COUNT(*) FROM user_tracks ut LEFT JOIN tracks t ON t.id=ut.track_id WHERE t.id IS NULL').fetchone()[0]}")
    dupes_left = conn.execute(
        "SELECT COUNT(*) FROM (SELECT isrc FROM tracks "
        "WHERE isrc IS NOT NULL AND isrc != '' GROUP BY isrc HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    print(f"  remaining duplicate ISRC groups (MUST be 0): {dupes_left}")
    print(f"  users who LOST a recording (MUST be empty): {lost or 'none'}")
    for r in conn.execute("SELECT user_id, COUNT(*) n FROM user_tracks GROUP BY 1 ORDER BY n DESC"):
        print(f"    user {r[0]}: {r[1]} tracks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
