"""Rebuild user_stats from track_events.

user_stats is a materialised per-user aggregate of track_events (play_start
rows only -- we are modelling play cadence, not interaction cadence). POST
/api/events writes it inline alongside the event log, but Turso has no real
transactions (backend/db_client.py opens and closes a fresh Hrana stream per
statement), so a failure between the event INSERT, the user_track_stats
upsert and the user_stats upsert leaves the cache behind the log. This
script is what makes that an annoyance rather than data loss: it recomputes
every row from the events alone.

The Guest row (users.display_name='Guest') is deliberately skipped. The
write path in backend/app.py short-circuits on it so that many visitors do
not get conflated into one half-life; the rebuild must agree, otherwise it
would silently re-materialise the drift the write path refuses to create.

THE EMA ARITHMETIC BELOW MUST MATCH backend/app.py's _upsert_user_stats.
That function is the spec (it is what runs on the hot path); this is its
replay-from-events restatement. If they disagree, running this script will
silently "repair" correct rows into wrong ones -- change them together.

Dry-run by default, like every other operational script in scripts/:

    python scripts/rebuild_user_stats.py                 # local sqlite, report only
    python scripts/rebuild_user_stats.py --apply         # local sqlite, repair
    python scripts/rebuild_user_stats.py --turso         # prod, report only
    python scripts/rebuild_user_stats.py --turso --apply # prod, repair
    python scripts/rebuild_user_stats.py --user 1 --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"

# Must agree with backend/app.py's _USER_STATS_* constants.
_ALPHA = 0.1
_MIN_H = 0.01
_MAX_H = 720.0


def _connect(use_turso: bool):
    if use_turso:
        text = _PS1.read_text(encoding="utf-8")
        os.environ["TURSO_DATABASE_URL"] = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
        os.environ["TURSO_AUTH_TOKEN"] = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
        os.environ["DB_BACKEND"] = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    import db_client  # noqa: E402
    return db_client.create_connection()


def _parse_ts(s):
    if s is None:
        return None
    s = str(s).strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1].strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if "+" in s:
        s = s.split("+", 1)[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _expected(conn, user_id):
    """{user_id: (ema_interval_h, play_count, last_play_at_str)} for every
    user with at least one play_start event, excluding the Guest row.

    Walks events ordered by (user_id, server_ts, id) so the EMA is replayed
    in exactly the order the write path would have seen them.
    """
    where = "WHERE te.type = 'play_start' AND u.display_name != 'Guest'"
    params = ()
    if user_id is not None:
        where += " AND te.user_id = ?"
        params = (user_id,)
    rows = conn.execute(
        "SELECT te.user_id, te.server_ts "
        "FROM track_events te JOIN users u ON u.id = te.user_id "
        f"{where} "
        "ORDER BY te.user_id, te.server_ts, te.id",
        params,
    ).fetchall()

    out = {}
    for r in rows:
        uid = int(r["user_id"])
        ts_raw = r["server_ts"]
        ts_dt = _parse_ts(ts_raw)
        if ts_dt is None:
            continue
        cur = out.get(uid)
        if cur is None:
            out[uid] = {"ema": None, "n": 1, "last_dt": ts_dt, "last_str": str(ts_raw)}
            continue
        gap = max(_MIN_H, min(_MAX_H, (ts_dt - cur["last_dt"]).total_seconds() / 3600.0))
        if cur["ema"] is None:
            cur["ema"] = gap
        else:
            cur["ema"] = _ALPHA * gap + (1.0 - _ALPHA) * cur["ema"]
        cur["n"] += 1
        cur["last_dt"] = ts_dt
        cur["last_str"] = str(ts_raw)
    return out


def _existing(conn, user_id):
    where, params = "", ()
    if user_id is not None:
        where, params = "WHERE user_id = ?", (user_id,)
    try:
        rows = conn.execute(
            f"SELECT user_id, ema_interval_h, play_count, last_play_at "
            f"FROM user_stats {where}",
            params,
        ).fetchall()
    except Exception as e:
        print(f"  (user_stats unreadable: {e}; treating as empty)")
        return {}
    return {int(r["user_id"]): r for r in rows}


def _close(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the recomputed rows (default: report only)")
    ap.add_argument("--turso", action="store_true",
                    help="run against production Turso instead of the local sqlite DB")
    ap.add_argument("--user", type=int, default=None, help="limit to one user_id")
    args = ap.parse_args()

    conn = _connect(args.turso)
    target = "turso" if args.turso else "sqlite"
    scope = f"user {args.user}" if args.user is not None else "all users (excl. Guest)"
    print(f"rebuilding user_stats from track_events ({target}, {scope})")

    want = _expected(conn, args.user)
    have = _existing(conn, args.user)
    print(f"  users with play_start events: {len(want)}")
    print(f"  user_stats rows             : {len(have)}")

    missing, differing, stale = [], [], []
    for uid, w in want.items():
        cur = have.get(uid)
        if cur is None:
            missing.append(uid)
            continue
        if (not _close(cur["ema_interval_h"], w["ema"]) or
                int(cur["play_count"] or 0) != w["n"] or
                str(cur["last_play_at"] or "") != w["last_str"]):
            differing.append(uid)
    for uid in have:
        if uid not in want:
            stale.append(uid)

    print(f"  missing  : {len(missing)}")
    print(f"  differing: {len(differing)}")
    print(f"  stale (rows with no backing play events): {len(stale)}")
    for uid in (missing + differing)[:10]:
        w = want[uid]
        print(f"    user={uid} ema={w['ema']} play_count={w['n']} last={w['last_str']}")

    if not args.apply:
        print("dry-run -- nothing was modified. Re-run with --apply to repair.")
        conn.close()
        return 0

    now = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    written = 0
    for uid in missing + differing:
        w = want[uid]
        conn.execute(
            "INSERT INTO user_stats "
            "(user_id, plays_30d, ema_interval_h, play_count, last_play_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  ema_interval_h = excluded.ema_interval_h, "
            "  play_count     = excluded.play_count, "
            "  last_play_at   = excluded.last_play_at, "
            "  updated_at     = excluded.updated_at",
            (uid, w["n"], w["ema"], w["n"], w["last_str"], now),
        )
        written += 1
    for uid in stale:
        conn.execute("DELETE FROM user_stats WHERE user_id = ?", (uid,))
    conn.commit()
    print(f"applied: {written} row(s) written, {len(stale)} stale row(s) deleted.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
