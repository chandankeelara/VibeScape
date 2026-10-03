"""Rebuild user_track_stats from track_events.

user_track_stats is a materialised aggregate of track_events and nothing
else. POST /api/events writes both in the same request, but Turso has no
real transactions (backend/db_client.py opens and closes a fresh Hrana
stream per statement), so a failure between the event INSERT and the
aggregate upsert leaves the cache behind the log. This script is what makes
that an annoyance rather than data loss: it recomputes every aggregate row
from the events alone and reports or repairs the difference.

It is also the migration path for any change to the counter set — add the
column to schema.sql, add the CASE expression below, run this with --apply.

Dry-run by default, like every other operational script in scripts/:

    python scripts/rebuild_user_track_stats.py                 # local sqlite, report only
    python scripts/rebuild_user_track_stats.py --apply         # local sqlite, repair
    python scripts/rebuild_user_track_stats.py --turso         # prod, report only
    python scripts/rebuild_user_track_stats.py --turso --apply # prod, repair
    python scripts/rebuild_user_track_stats.py --user 1 --apply

THE ARITHMETIC BELOW MUST MATCH backend/app.py's _accumulate_stats. That
function is the spec (it is what runs on the hot path); this is its SQL
restatement. If they disagree, running this script will silently "repair"
correct rows into wrong ones, so change them together.

The u_ (vibe_source='user') and s_ (vibe_source='system') families are kept
apart on purpose — u_ is the user's stated preference, s_ is DJ mode's own
output coming back as a reward signal. Averaging them trains the recommender
on itself. Events with no vibe_source label count only in the label-agnostic
totals, so u_* + s_* can be less than play_count / end_count.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"

# (column, SQL expression) in user_track_stats column order. Counters only —
# the timestamp columns are handled separately below.
_COUNTERS = [
    ("play_count",      "SUM(CASE WHEN type = 'play_start' THEN 1 ELSE 0 END)"),
    ("end_count",       "SUM(CASE WHEN type = 'play_end'   THEN 1 ELSE 0 END)"),
    ("total_played_ms", "SUM(CASE WHEN type = 'play_end' THEN COALESCE(position_ms, 0) ELSE 0 END)"),
    ("dj_play_count",   "SUM(CASE WHEN type = 'play_start' AND dj_mode = 1 THEN 1 ELSE 0 END)"),
]
for _p, _label in (("u", "user"), ("s", "system")):
    _is = f"vibe_source = '{_label}'"
    _COUNTERS += [
        (f"{_p}_play_count",
         f"SUM(CASE WHEN type = 'play_start' AND {_is} THEN 1 ELSE 0 END)"),
        (f"{_p}_end_count",
         f"SUM(CASE WHEN type = 'play_end' AND {_is} THEN 1 ELSE 0 END)"),
        (f"{_p}_complete_count",
         f"SUM(CASE WHEN type = 'play_end' AND {_is} AND LOWER(COALESCE(reason, '')) = 'completed' THEN 1 ELSE 0 END)"),
        (f"{_p}_skip_count",
         f"SUM(CASE WHEN type = 'play_end' AND {_is} AND LOWER(COALESCE(reason, '')) = 'skipped' THEN 1 ELSE 0 END)"),
        (f"{_p}_replace_count",
         f"SUM(CASE WHEN type = 'play_end' AND {_is} AND LOWER(COALESCE(reason, '')) = 'replaced' THEN 1 ELSE 0 END)"),
        (f"{_p}_skip_position_ms_sum",
         f"SUM(CASE WHEN type = 'play_end' AND {_is} AND LOWER(COALESCE(reason, '')) = 'skipped' THEN COALESCE(position_ms, 0) ELSE 0 END)"),
        (f"{_p}_vibe_count",
         f"SUM(CASE WHEN type = 'play_start' AND {_is} AND vibe IS NOT NULL THEN 1 ELSE 0 END)"),
        (f"{_p}_vibe_sum",
         f"SUM(CASE WHEN type = 'play_start' AND {_is} AND vibe IS NOT NULL THEN vibe ELSE 0 END)"),
        (f"{_p}_vibe_sum_sq",
         f"SUM(CASE WHEN type = 'play_start' AND {_is} AND vibe IS NOT NULL THEN vibe * vibe ELSE 0 END)"),
    ]

# last_skipped_at keys off any skip regardless of label, matching
# _accumulate_stats (which sets the flag before the label check).
_TIMESTAMPS = [
    ("first_played_at", "MIN(server_ts)"),
    ("last_played",     "MAX(server_ts)"),
    ("last_skipped_at",
     "MAX(CASE WHEN type = 'play_end' AND LOWER(COALESCE(reason, '')) = 'skipped' THEN server_ts END)"),
]

_ALL = _COUNTERS + _TIMESTAMPS
_COLS = ["user_id", "track_id"] + [c for c, _ in _ALL] + ["updated_at"]
_CHUNK = 20   # rows per INSERT (20 x 28 bound params)


def _connect(use_turso: bool):
    if use_turso:
        text = _PS1.read_text(encoding="utf-8")
        os.environ["TURSO_DATABASE_URL"] = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
        os.environ["TURSO_AUTH_TOKEN"] = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
        os.environ["DB_BACKEND"] = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    import db_client  # noqa: E402
    return db_client.create_connection()


def _expected(conn, user_id):
    sel = ", ".join(f"{expr} AS {col}" for col, expr in _ALL)
    where, params = "", ()
    if user_id is not None:
        where, params = "WHERE user_id = ?", (user_id,)
    rows = conn.execute(
        f"SELECT user_id, track_id, {sel} FROM track_events {where} "
        f"GROUP BY user_id, track_id",
        params,
    ).fetchall()
    return {(int(r["user_id"]), int(r["track_id"])): r for r in rows}


def _existing(conn, user_id):
    cols = ", ".join(c for c, _ in _ALL)
    where, params = "", ()
    if user_id is not None:
        where, params = "WHERE user_id = ?", (user_id,)
    rows = conn.execute(
        f"SELECT user_id, track_id, {cols} FROM user_track_stats {where}", params
    ).fetchall()
    return {(int(r["user_id"]), int(r["track_id"])): r for r in rows}


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
    scope = f"user {args.user}" if args.user is not None else "all users"
    print(f"rebuilding user_track_stats from track_events ({target}, {scope})")

    want = _expected(conn, args.user)
    have = _existing(conn, args.user)
    print(f"  track_events groups : {len(want)}")
    print(f"  user_track_stats rows: {len(have)}")

    missing, differing, stale = [], [], []
    for key, row in want.items():
        cur = have.get(key)
        if cur is None:
            missing.append(key)
            continue
        if any((cur[c] or 0) != (row[c] or 0) for c, _ in _COUNTERS) or \
           any((cur[c] or "") != (row[c] or "") for c, _ in _TIMESTAMPS):
            differing.append(key)
    for key in have:
        if key not in want:
            stale.append(key)

    print(f"  missing  : {len(missing)}")
    print(f"  differing: {len(differing)}")
    print(f"  stale (rows with no backing events): {len(stale)}")
    for key in (missing + differing)[:10]:
        print(f"    user={key[0]} track={key[1]}")

    if not args.apply:
        print("dry-run — nothing was modified. Re-run with --apply to repair.")
        conn.close()
        return 0

    # Absolute assignment, not += : this is a rebuild, the events are truth.
    sets = ",\n              ".join(
        f"{c} = excluded.{c}" for c in _COLS if c not in ("user_id", "track_id")
    )
    tail = f" ON CONFLICT(user_id, track_id) DO UPDATE SET\n              {sets}"
    cols = ", ".join(_COLS)
    width = len(_COLS)

    now = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    todo = [want[k] for k in missing + differing]
    written = 0
    for i in range(0, len(todo), _CHUNK):
        chunk = todo[i:i + _CHUNK]
        values = ",".join(["(" + ",".join("?" * width) + ")"] * len(chunk))
        params = []
        for r in chunk:
            params.extend([r["user_id"], r["track_id"]]
                          + [r[c] for c, _ in _ALL] + [now])
        conn.execute(
            f"INSERT INTO user_track_stats ({cols}) VALUES {values}{tail}",
            tuple(params),
        )
        written += len(chunk)

    for uid, tid in stale:
        conn.execute(
            "DELETE FROM user_track_stats WHERE user_id = ? AND track_id = ?",
            (uid, tid),
        )
    conn.commit()
    print(f"applied: {written} row(s) written, {len(stale)} stale row(s) deleted.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
