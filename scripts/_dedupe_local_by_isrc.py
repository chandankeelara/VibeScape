"""
Deduplicate LOCAL tracks that are the same recording — matched on ISRC.

Local only. Never connects to Turso. Once the result is verified here,
the same end state reaches prod through _sync_local_to_turso.py, which is
non-destructive (UPDATE matched on spotify_id, no DROP, no DELETE).

Why ISRC and not spotify_id: spotify_id is UNIQUE, so grouping on it
finds nothing. The duplicates are one recording released under several
Spotify IDs — a single, its parent album, a compilation. ISRC identifies
a recording, so it merges those while keeping remixes and live versions
apart (those carry their own ISRC).

Why the ordering matters: user_tracks.track_id REFERENCES tracks(id)
ON DELETE CASCADE. Removing a loser row before re-pointing would silently
drop that song from a user's library. And where a user already owns BOTH
copies, a plain UPDATE would violate PRIMARY KEY (user_id, track_id) — so
those rows are merged (play_count summed, earliest added_at kept) rather
than moved.

Survivor: ingestion_status='done' > has an embedding > lowest id.

Run:
    python scripts/_dedupe_local_by_isrc.py            # dry-run
    python scripts/_dedupe_local_by_isrc.py --apply
"""
from __future__ import annotations

import argparse
import shutil
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
_DB = _REPO / "data" / "vibescape.db"


def snapshot_user_isrcs(conn) -> dict:
    """{user_id: {isrc, ...}} — the invariant dedup must not change.

    Merging may collapse two rows into one, so row COUNTS legitimately
    drop. What must never change is which recordings a user has.
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

    conn = sqlite3.connect(str(_DB))
    conn.row_factory = sqlite3.Row
    # Deliberately OFF. Local user_tracks still declares
    #   track_id ... REFERENCES "tracks_old"(id)
    # from an old table rebuild, and tracks_old no longer exists — so with
    # enforcement ON every UPDATE fails with "no such table: main.tracks_old".
    # (Prod's FK is correct and points at tracks; this is local-only drift.)
    #
    # Turning it off is safe HERE because this script never relies on the
    # cascade: it re-points every user_tracks row explicitly before deleting
    # anything, and deletes track_embeddings rows explicitly too. The
    # post-run check for orphaned user_tracks proves the result.
    conn.execute("PRAGMA foreign_keys = OFF")

    rows = conn.execute(
        "SELECT id, isrc, title, artist, ingestion_status FROM tracks "
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
    if losers:
        for i in range(0, len(losers), 400):
            chunk = losers[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT user_id, track_id, play_count, last_played, added_at "
                f"FROM user_tracks WHERE track_id IN ({ph})", chunk):
                ut_rows[r["track_id"]].append(r)

    survivors = [s for s, _, _, _ in plan]
    owned = set()
    for i in range(0, len(survivors), 400):
        chunk = survivors[i:i + 400]
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
    backup = _DB.with_name(f"vibescape.db.pre-dedupe-{ts}")
    shutil.copyfile(_DB, backup)
    print(f"\nbackup -> {backup.name}")

    before = snapshot_user_isrcs(conn)

    print("re-pointing user_tracks (before any delete) ...")
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
    print(f"  merged {n_merge}   re-pointed {n_move}")

    print("removing duplicate rows ...")
    n_del = 0
    for i in range(0, len(losers), 400):
        chunk = losers[i:i + 400]
        ph = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM track_embeddings WHERE track_id IN ({ph})", chunk)
        n_del += conn.execute(f"DELETE FROM tracks WHERE id IN ({ph})", chunk).rowcount
    conn.commit()
    print(f"  removed {n_del}")

    print("\nverifying ...")
    after = snapshot_user_isrcs(conn)
    lost = {u: before[u] - after.get(u, set()) for u in before}
    lost = {u: v for u, v in lost.items() if v}
    print(f"  tracks: {conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]}")
    print(f"  track_embeddings: {conn.execute('SELECT COUNT(*) FROM track_embeddings').fetchone()[0]}")
    print(f"  user_tracks: {conn.execute('SELECT COUNT(*) FROM user_tracks').fetchone()[0]}")
    print(f"  orphaned user_tracks (MUST be 0): "
          f"{conn.execute('SELECT COUNT(*) FROM user_tracks ut LEFT JOIN tracks t ON t.id=ut.track_id WHERE t.id IS NULL').fetchone()[0]}")
    dupes_left = conn.execute(
        "SELECT COUNT(*) FROM (SELECT isrc FROM tracks "
        "WHERE isrc IS NOT NULL AND isrc != '' "
        "GROUP BY isrc HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    print(f"  remaining duplicate ISRC groups (MUST be 0): {dupes_left}")
    print(f"  users who LOST a recording (MUST be empty): {lost or 'none'}")
    print(f"  integrity: {conn.execute('PRAGMA integrity_check').fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
