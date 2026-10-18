"""
Merge duplicate `tracks` rows, so schema.sql's unique indexes can be built
on production (backlog 1.13). Dry-run by default.

    python scripts/_dedupe_tracks.py                      # local SQLite, report only
    python scripts/_dedupe_tracks.py --apply              # local SQLite, merge
    python scripts/_dedupe_tracks.py --target prod        # production, report only
    python scripts/_dedupe_tracks.py --target prod --apply

`--target` is scripts/_target.py: prod credentials come from .env.prod,
never from scripts/_load_gcp_secrets.ps1. Back production up first
(scripts/backup_prod_db.py); this script does not do it for you.

Replaces _dedupe_turso_by_isrc.py, _dedupe_local_by_isrc.py and
_dedupe_history.py (2026-10-18), which grouped on ISRC only, wrote every
statement as its own autocommit (a crash mid-group left a half-moved
group), rebuilt the stats cache by shelling out to
rebuild_user_track_stats.py --turso (which reads the ps1), and left the
apple_id collisions that block idx_tracks_apple_id.

WHAT IS A DUPLICATE
-------------------
Rows sharing a spotify_id or an ISRC are one recording: those are merged,
transitively (a shares an ISRC with b, b a spotify_id with c: one group).
On the 2026-10-18 backup every ISRC group had matching durations (all
within 5 s) and only spelling differences in titles, so ISRC is safe to
merge on. Every spotify_id duplicate (1 group) sat inside an ISRC group.

apple_id is NOT a merge key. It is the iTunes match the preview stage
found, and different recordings can share it: an album cut and its music
video edit (Save Your Tears, 215 s vs 249 s), a single and the film
release with its own ISRC. Where rows still share an apple_id after the
merge, the keeper keeps it and the others' apple_id is set to NULL, which
is what the pipeline itself does on a collision locally
(ingest_pipeline/base.py, _RETRY_DROPPABLE_FIELDS). Their audio and vector
are untouched.

THE KEEPER is ingestion_status='done' first, then the lowest id: the same
order backend/app.py's _TRACK_PICK uses to choose a row for a new add, so
the app and this script agree on which release stands for a recording.

WHAT POINTS AT tracks(id), all ON DELETE CASCADE (schema.sql):
  user_tracks       moved onto the keeper. A user with both rows keeps one:
                    earliest added_at, play_count summed, latest
                    last_played (the last two are dead columns, folded
                    anyway). A user with only a loser's row gets the
                    keeper's.
  track_events      moved onto the keeper. Append-only, own ids: no
                    collision possible.
  user_track_stats  a cache of track_events (backend/CLAUDE.md). The rows on
                    the group are deleted and the keeper's rebuilt from its
                    events with rebuild_user_track_stats.py's own
                    expressions, so the arithmetic cannot drift from it.
  track_embeddings  the losers' rows are deleted; the keeper's stays.

EACH GROUP IS ONE execute_batch: one transaction on both backends (see
backend/app.py _link_track's docstring for what that is on each), so a
group is merged entirely or not at all and a crash or a failure stops
between groups. Re-running carries on: merged groups no longer exist.
Every move happens before the delete in the same transaction, so the
cascade finds nothing to take.

COST ON PRODUCTION: `tracks` is read once. Per group, user_tracks is
reached through its track_id index, but track_events and user_track_stats
have no index leading with track_id, so each group scans them (about
8,000 and 2,300 rows on 2026-10-18): roughly 3.5M rows read for a full
run. Narrowing by user would use their indexes but could miss an event
written during the run, which the delete would then cascade away.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))
# Explicit, not left to the script directory being sys.path[0]: that entry
# is missing under `python -P` / PYTHONSAFEPATH.
sys.path.insert(0, str(_REPO / "scripts"))

import _target  # noqa: E402
import rebuild_user_track_stats as _rebuild  # noqa: E402  (no connection at import)

_REFS = ("user_tracks", "track_events", "user_track_stats", "track_embeddings")
_PLAYS = "type IN ('play_start', 'play_end')"


@dataclass
class Plan:
    groups: list = field(default_factory=list)       # [(keeper, [losers])]
    clear_apple: list = field(default_factory=list)  # ids whose apple_id goes NULL
    kinds: dict = field(default_factory=dict)        # kind -> (groups, extra rows)


def _rank(r):
    return (r["ingestion_status"] != "done", int(r["id"]))


def _dupes(rows, key):
    by = defaultdict(list)
    for r in rows:
        if r[key] not in (None, ""):
            by[r[key]].append(r)
    return [g for g in by.values() if len(g) > 1]


def plan(rows) -> Plan:
    """The merge for `rows` (id, spotify_id, apple_id, isrc, ingestion_status).

    Pure: no database. Groups are the connected components of "shares a
    spotify_id or an ISRC"; apple_id collisions left among the keepers and
    the rows outside any group are resolved by clearing it (module
    docstring).
    """
    rows = list(rows)
    p = Plan()
    for key in ("spotify_id", "isrc", "apple_id"):
        d = _dupes(rows, key)
        p.kinds[key] = (len(d), sum(len(g) - 1 for g in d))

    parent = {int(r["id"]): int(r["id"]) for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for key in ("spotify_id", "isrc"):
        for g in _dupes(rows, key):
            root = find(int(g[0]["id"]))
            for r in g[1:]:
                parent[find(int(r["id"]))] = root

    by_id = {int(r["id"]): r for r in rows}
    comps = defaultdict(list)
    for i in by_id:
        comps[find(i)].append(by_id[i])
    losers = set()
    for members in comps.values():
        if len(members) > 1:
            members.sort(key=_rank)
            p.groups.append((int(members[0]["id"]), sorted(int(m["id"]) for m in members[1:])))
            losers.update(int(m["id"]) for m in members[1:])
    p.groups.sort()

    for g in _dupes([r for r in rows if int(r["id"]) not in losers], "apple_id"):
        g.sort(key=_rank)
        p.clear_apple.extend(int(r["id"]) for r in g[1:])
    p.clear_apple.sort()
    return p


def _in(ids) -> str:
    return ",".join(str(int(i)) for i in ids)


def group_statements(keeper: int, losers) -> list:
    """The one transaction that folds `losers` into `keeper`. Order matters:
    every reference moves before the rows it pointed at are deleted."""
    lo = _in(losers)
    allg = _in([keeper, *losers])
    cols = ", ".join(_rebuild._COLS)
    exprs = ", ".join(expr for _, expr in _rebuild._ALL)
    return [
        # A user who holds the keeper and a loser: fold onto the keeper row.
        (f"UPDATE user_tracks SET "
         f"  added_at    = (SELECT MIN(added_at) FROM user_tracks l "
         f"                 WHERE l.user_id = user_tracks.user_id AND l.track_id IN ({allg})), "
         f"  play_count  = (SELECT SUM(COALESCE(play_count, 0)) FROM user_tracks l "
         f"                 WHERE l.user_id = user_tracks.user_id AND l.track_id IN ({allg})), "
         f"  last_played = (SELECT MAX(last_played) FROM user_tracks l "
         f"                 WHERE l.user_id = user_tracks.user_id AND l.track_id IN ({allg})) "
         f"WHERE track_id = ? AND user_id IN "
         f"  (SELECT user_id FROM user_tracks WHERE track_id IN ({lo}))", (keeper,)),
        # A user who holds only losers: a keeper row carrying theirs.
        (f"INSERT OR IGNORE INTO user_tracks "
         f"(user_id, track_id, added_at, source, play_count, last_played) "
         f"SELECT user_id, ?, MIN(added_at), MIN(source), SUM(COALESCE(play_count, 0)), "
         f"       MAX(last_played) "
         f"FROM user_tracks WHERE track_id IN ({lo}) GROUP BY user_id", (keeper,)),
        (f"DELETE FROM user_tracks WHERE track_id IN ({lo})", ()),
        (f"UPDATE track_events SET track_id = ? WHERE track_id IN ({lo})", (keeper,)),
        (f"DELETE FROM user_track_stats WHERE track_id IN ({allg})", ()),
        (f"INSERT INTO user_track_stats ({cols}) "
         f"SELECT user_id, track_id, {exprs}, CURRENT_TIMESTAMP FROM track_events "
         f"WHERE {_PLAYS} AND track_id = ? GROUP BY user_id, track_id", (keeper,)),
        (f"DELETE FROM track_embeddings WHERE track_id IN ({lo})", ()),
        (f"DELETE FROM tracks WHERE id IN ({lo})", ()),
    ]


def snapshot(conn) -> dict:
    """What the merge must preserve, keyed by recording rather than row id:
    a recording is a track's ISRC, else its spotify_id, else its id."""
    rec = ("COALESCE(NULLIF(t.isrc, ''), 's:' || t.spotify_id, 'id:' || t.id)")
    lib = defaultdict(set)
    for r in conn.execute(f"SELECT ut.user_id, {rec} FROM user_tracks ut "
                          f"JOIN tracks t ON t.id = ut.track_id"):
        lib[int(r[0])].add(r[1])
    ev = Counter()
    for r in conn.execute(f"SELECT te.user_id, {rec}, COUNT(*) FROM track_events te "
                          f"JOIN tracks t ON t.id = te.track_id GROUP BY 1, 2"):
        ev[(int(r[0]), r[1])] = int(r[2])
    return {"library": dict(lib), "events": ev,
            "n_events": conn.execute("SELECT COUNT(*) FROM track_events").fetchone()[0]}


def check(conn, before) -> list:
    """Every invariant that must hold after a run; the failures, as text."""
    bad = []
    for key in ("spotify_id", "isrc", "apple_id"):
        n = conn.execute(f"SELECT COUNT(*) FROM (SELECT {key} FROM tracks WHERE {key} IS NOT NULL "
                         f"AND {key} != '' GROUP BY {key} HAVING COUNT(*) > 1)").fetchone()[0]
        if n:
            bad.append(f"{n} {key} groups remain")
    for t in _REFS:
        n = conn.execute(f"SELECT COUNT(*) FROM {t} x LEFT JOIN tracks t ON t.id = x.track_id "
                         f"WHERE t.id IS NULL").fetchone()[0]
        if n:
            bad.append(f"{n} orphaned {t} rows")
    after = snapshot(conn)
    if after["n_events"] != before["n_events"]:
        bad.append(f"track_events {before['n_events']} -> {after['n_events']}")
    if after["events"] != before["events"]:
        bad.append("per-(user, recording) event counts changed")
    lost = {u: s - after["library"].get(u, set()) for u, s in before["library"].items()}
    lost = {u: s for u, s in lost.items() if s}
    if lost:
        bad.append(f"users lost recordings: {lost}")
    want, have = _rebuild._expected(conn, None), _rebuild._existing(conn, None)
    off = [k for k in set(want) | set(have)
           if k not in want or k not in have
           or any((want[k][c] or 0) != (have[k][c] or 0) for c, _ in _rebuild._COUNTERS)
           or any((want[k][c] or "") != (have[k][c] or "") for c, _ in _rebuild._TIMESTAMPS)]
    if off:
        bad.append(f"{len(off)} user_track_stats rows differ from a rebuild")
    return bad


def _count_by_user(conn, table, ids):
    out = Counter()
    for i in range(0, len(ids), 400):
        for r in conn.execute(f"SELECT user_id, COUNT(*) FROM {table} "
                              f"WHERE track_id IN ({_in(ids[i:i + 400])}) GROUP BY 1"):
            out[int(r[0])] += int(r[1])
    return out


def report(conn, p: Plan) -> None:
    for key, (g, n) in p.kinds.items():
        print(f"  {key:10} groups {g:4}   rows beyond the first {n:4}")
    losers = [l for _, ls in p.groups for l in ls]
    print(f"  merge groups (spotify_id or ISRC, transitive): {len(p.groups)}, "
          f"rows to delete: {len(losers)}")
    print(f"  apple_id cleared (different recordings, one iTunes match): {len(p.clear_apple)}")
    owners = defaultdict(set)
    members = [i for k, ls in p.groups for i in (k, *ls)]
    for i in range(0, len(members), 400):
        for r in conn.execute(f"SELECT user_id, track_id FROM user_tracks "
                              f"WHERE track_id IN ({_in(members[i:i + 400])})"):
            owners[int(r[1])].add(int(r[0]))
    merged = moved = 0
    for k, ls in p.groups:
        held = set(owners[k])
        for l in ls:
            for u in owners[l]:
                if u in held:
                    merged += 1
                else:
                    moved += 1
                    held.add(u)
    print(f"  user_tracks on losers: {merged + moved}  (re-pointed {moved}, "
          f"folded into a row the user already had {merged})")
    for t in ("track_events", "user_track_stats", "track_embeddings"):
        n = sum(_count_by_user(conn, t, losers).values()) if t != "track_embeddings" else \
            sum(conn.execute(f"SELECT COUNT(*) FROM track_embeddings WHERE track_id IN "
                             f"({_in(losers[i:i + 400])})").fetchone()[0]
                for i in range(0, len(losers), 400))
        what = {"track_events": "moved to the keeper",
                "user_track_stats": "deleted, keeper's rebuilt from events",
                "track_embeddings": "deleted"}[t]
        print(f"  {t} on losers: {n}  ({what})")


def run(conn, p: Plan, log=print) -> int:
    """Apply `p`, one transaction per group. Returns groups merged."""
    done = 0
    for keeper, losers in p.groups:
        conn.execute_batch(group_statements(keeper, losers))
        done += 1
        if done % 25 == 0:
            log(f"  ... {done}/{len(p.groups)} groups")
    for i in range(0, len(p.clear_apple), 400):
        conn.execute_batch([(f"UPDATE tracks SET apple_id = NULL "
                             f"WHERE id IN ({_in(p.clear_apple[i:i + 400])})", ())])
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    _target.add_argument(ap)
    ap.add_argument("--apply", action="store_true", help="merge (default: report only)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print(_target.apply(args.target))   # exits before connecting if prod is not set up
    import db_client
    conn = db_client.create_connection()

    rows = conn.execute("SELECT id, spotify_id, apple_id, isrc, ingestion_status "
                        "FROM tracks").fetchall()
    p = plan(rows)
    print(f"tracks: {len(rows)}")
    report(conn, p)
    if not args.apply:
        print("dry-run: nothing was modified. --apply to merge.")
        return 0
    if not p.groups and not p.clear_apple:
        print("nothing to do.")
        return 0

    before = snapshot(conn)
    n = run(conn, p)
    print(f"merged {n} groups, cleared {len(p.clear_apple)} apple_id")
    bad = check(conn, before)
    for b in bad:
        print(f"  FAILED: {b}")
    print("verified: no duplicates, no orphans, no recording or event lost, "
          "stats equal a rebuild" if not bad else "verification FAILED")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
