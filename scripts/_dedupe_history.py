"""
Listening history for the ISRC dedupe scripts (_dedupe_local_by_isrc.py,
_dedupe_turso_by_isrc.py). Shared so the two cannot drift.

WHY THIS EXISTS
---------------
The dedupe scripts were written when user_tracks was the only table
pointing at tracks(id). Since then track_events (the append-only listening
log, the source of truth) and user_track_stats (its materialised cache)
both declare track_id REFERENCES tracks(id) ON DELETE CASCADE. The scripts
re-pointed user_tracks only, so deleting a duplicate silently deleted the
listening history on it — 12 events and 6 stats rows on the 2026-10-12 prod
backup, and their own checks (orphaned user_tracks, lost recordings) could
not see it.

THE ORDER
---------
1. history_snapshot()  — before anything moves
2. repoint_events()    — events onto the survivor, BEFORE any delete.
                         Events have their own id, so a move cannot collide.
3. drop_loser_stats()  — the cache rows on losers. Not merged by hand: the
                         survivor's row is rebuilt from the moved events.
4. (delete losers)
5. rebuild_stats()     — scripts/rebuild_user_track_stats.py --user N --apply
                         for every affected user. That script is the
                         arithmetic spec (it mirrors backend/app.py's
                         _accumulate_stats), so the result is exact rather
                         than a second, hand-written merge that could disagree.
6. verify_history()    — events unchanged per (user, recording), no orphans,
                         and the cache equals a fresh rebuild.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_REBUILD = _REPO / "scripts" / "rebuild_user_track_stats.py"


def _in(ids):
    return ",".join(str(int(i)) for i in ids)


def history_snapshot(conn) -> tuple[int, Counter]:
    """(total events, {(user_id, recording): n}).

    A recording is the track's ISRC, or 'id:<n>' when it has none, so a
    move between two rows of the same ISRC leaves the key unchanged while
    any loss shows up as a lower count.
    """
    total = conn.execute("SELECT COUNT(*) FROM track_events").fetchone()[0]
    per = Counter()
    for r in conn.execute(
            "SELECT te.user_id, COALESCE(NULLIF(t.isrc, ''), 'id:' || te.track_id) AS rec, "
            "       COUNT(*) AS n "
            "FROM track_events te LEFT JOIN tracks t ON t.id = te.track_id "
            "GROUP BY 1, 2"):
        per[(int(r[0]), r[1])] = int(r[2])
    return int(total), per


def affected_users(conn, plan) -> list[int]:
    """Users with events or stats on any track in a duplicate group."""
    ids = [i for sid, losers, *_ in plan for i in [sid, *losers]]
    users = set()
    for i in range(0, len(ids), 400):
        chunk = _in(ids[i:i + 400])
        for t in ("track_events", "user_track_stats"):
            users |= {int(r[0]) for r in conn.execute(
                f"SELECT DISTINCT user_id FROM {t} WHERE track_id IN ({chunk})")}
    return sorted(users)


def count_on(conn, table: str, losers) -> int:
    n = 0
    for i in range(0, len(losers), 400):
        n += conn.execute(f"SELECT COUNT(*) FROM {table} "
                          f"WHERE track_id IN ({_in(losers[i:i + 400])})").fetchone()[0]
    return int(n)


def repoint_events(conn, plan) -> int:
    n = 0
    for sid, losers, *_ in plan:
        if not losers:
            continue
        moved = count_on(conn, "track_events", losers)
        if moved:
            conn.execute(f"UPDATE track_events SET track_id = ? "
                         f"WHERE track_id IN ({_in(losers)})", (int(sid),))
            n += moved
    return n


def drop_loser_stats(conn, losers) -> int:
    n = count_on(conn, "user_track_stats", losers)
    for i in range(0, len(losers), 400):
        conn.execute(f"DELETE FROM user_track_stats "
                     f"WHERE track_id IN ({_in(losers[i:i + 400])})")
    return n


def rebuild_stats(users, *, db_path: Path | None = None, turso: bool = False) -> None:
    """Run the rebuild script per user. Raises if any run fails."""
    env = dict(os.environ)
    if db_path is not None:
        env["VIBESCAPE_DB_PATH"] = str(db_path)
        env["DB_BACKEND"] = "sqlite"
    for uid in users:
        cmd = [sys.executable, str(_REBUILD), "--user", str(uid), "--apply"]
        if turso:
            cmd.append("--turso")
        out = subprocess.run(cmd, env=env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        last = (out.stdout.strip().splitlines() or ["(no output)"])[-1]
        print(f"    user {uid}: {last}")
        if out.returncode != 0:
            raise RuntimeError(f"rebuild failed for user {uid}: {out.stderr[-500:]}")


def stats_drift(users, *, db_path: Path | None = None, turso: bool = False) -> int:
    """Rows a dry-run rebuild would still change, summed over `users`."""
    env = dict(os.environ)
    if db_path is not None:
        env["VIBESCAPE_DB_PATH"] = str(db_path)
        env["DB_BACKEND"] = "sqlite"
    drift = 0
    for uid in users:
        cmd = [sys.executable, str(_REBUILD), "--user", str(uid)]
        if turso:
            cmd.append("--turso")
        out = subprocess.run(cmd, env=env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        for line in out.stdout.splitlines():
            s = line.strip()
            for key in ("missing", "differing", "stale"):
                if s.startswith(key):
                    try:
                        drift += int(s.split(":", 1)[1].split()[0])
                    except (IndexError, ValueError):
                        pass
    return drift


def verify_history(conn, before, users, **where) -> bool:
    """Print the history checks. True when every one passes."""
    total0, per0 = before
    total1, per1 = history_snapshot(conn)
    lost = {k: (per0[k], per1.get(k, 0)) for k in per0 if per1.get(k, 0) < per0[k]}
    orphan_ev = conn.execute(
        "SELECT COUNT(*) FROM track_events te LEFT JOIN tracks t ON t.id = te.track_id "
        "WHERE t.id IS NULL").fetchone()[0]
    orphan_st = conn.execute(
        "SELECT COUNT(*) FROM user_track_stats s LEFT JOIN tracks t ON t.id = s.track_id "
        "WHERE t.id IS NULL").fetchone()[0]
    drift = stats_drift(users, **where)
    print(f"  track_events: {total0} -> {total1} (MUST be equal)")
    print(f"  (user, recording) event counts that fell (MUST be none): {lost or 'none'}")
    print(f"  orphaned track_events (MUST be 0): {orphan_ev}")
    print(f"  orphaned user_track_stats (MUST be 0): {orphan_st}")
    print(f"  user_track_stats vs a fresh rebuild, rows off (MUST be 0): {drift}")
    return total0 == total1 and not lost and orphan_ev == 0 and orphan_st == 0 and drift == 0
