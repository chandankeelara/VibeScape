"""
See what is waiting for genre scores, and write scores back from a file.

The database is the queue (ingest_pipeline/README.md, "Tagging genres").
Scores come back in a JSON-lines file rather than as command-line arguments:
a tagging session scores 50 tracks at a time, and one file per batch is one
permission rule and no giant command line.

    # How many are waiting?
    python scripts/genre_tags.py --status

    # The rows themselves, as JSON lines, oldest first
    python scripts/genre_tags.py --waiting --limit 50

    # Check a file of scores, then write it. One object per line:
    #   {"id": 812, "spotify_id": "4u1aBcD...", "scores": {"film_melody": 100, "folk": 50}}
    # spotify_id is optional; when present the row is written only if it matches.
    python scripts/genre_tags.py --apply-file scores.jsonl
    python scripts/genre_tags.py --apply-file scores.jsonl --apply

    # The same against production Turso
    python scripts/genre_tags.py --target prod --status
    python scripts/genre_tags.py --target prod --apply-file scores.jsonl --apply

Writes are off by default, like the rest of scripts/. The whole file is
validated before anything is written: one bad line writes nothing. Then it
goes out in batches of 200 rows, each one transaction (one request on
Turso), with a count per batch. --target local (the default) is the local
SQLite file whatever DB_BACKEND says; --target prod reads production
credentials from the gitignored .env.prod (scripts/_target.py), never
scripts/_load_gcp_secrets.ps1. The target goes to stderr first, so
--waiting's JSON lines on stdout stay clean.

--status exits 1 when anything is waiting, so it works as a cron check.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
# For _target. Explicit, not left to the script directory being
# sys.path[0]: that entry is missing under `python -P` / PYTHONSAFEPATH.
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from db import get_conn  # noqa: E402
from ingest_pipeline import genre_tagging as gt  # noqa: E402
import _target  # noqa: E402

# Rows per write: one execute_batch, so one transaction and one Turso
# request each, the size the score recompute writes in.
BATCH = 200


def read_file(path: Path) -> list[tuple]:
    """The file's lines as tag_many items, every one validated. Raises
    ValueError naming the line on the first problem."""
    items, seen = [], set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {n}: not JSON ({e.msg})") from None
        if not isinstance(obj, dict) or set(obj) - {"id", "spotify_id", "scores"} \
                or "id" not in obj or "scores" not in obj:
            raise ValueError(f'line {n}: expected {{"id": <int>, "scores": {{...}}}} '
                             '(and optionally "spotify_id")')
        tid = obj["id"]
        if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0:
            raise ValueError(f"line {n}: id must be a positive integer, got {tid!r}")
        if tid in seen:
            raise ValueError(f"line {n}: track {tid} appears twice")
        seen.add(tid)
        try:
            gt.validate_scores(obj["scores"])
        except ValueError as e:
            raise ValueError(f"line {n} (track {tid}): {e}") from None
        sid = obj.get("spotify_id")
        items.append((tid, obj["scores"], sid) if sid else (tid, obj["scores"]))
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    _target.add_argument(ap)
    ap.add_argument("--status", action="store_true",
                    help="counts by genre_status (default)")
    ap.add_argument("--waiting", action="store_true",
                    help="print the waiting rows as JSON lines")
    ap.add_argument("--limit", type=int, default=50, help="rows for --waiting")
    ap.add_argument("--apply-file", type=Path, metavar="PATH",
                    help='JSON lines {"id": <int>, "scores": {...}}; checked, and '
                         "written with --apply")
    ap.add_argument("--apply", action="store_true", help="actually write")
    ap.add_argument("--json", action="store_true", help="--status as JSON")
    args = ap.parse_args(argv)

    items = None
    if args.apply_file:
        # Before connecting: a bad file needs no database to be refused.
        try:
            items = read_file(args.apply_file)
        except (OSError, ValueError) as e:
            print(f"{args.apply_file}: {e}\nnothing was written.")
            return 2

    print(_target.apply(args.target), file=sys.stderr)
    conn = get_conn()
    try:
        if args.waiting:
            for r in gt.fetch_waiting(conn, args.limit):
                print(json.dumps({k: r[k] for k in gt.WAITING_COLUMNS}, ensure_ascii=False))
            return 0

        if items is not None:
            missing_all = []
            for start in range(0, len(items), BATCH):
                chunk = items[start:start + BATCH]
                label = f"batch {start // BATCH + 1}: rows {start + 1}..{start + len(chunk)}"
                if not args.apply:
                    print(f"{label}: {len(chunk)} valid, would write")
                    continue
                res = gt.tag_many(conn, chunk)
                missing_all += res["missing"]
                print(f"{label}: {res['written']} written, {len(res['missing'])} missing"
                      + (f" {res['missing']}" if res["missing"] else ""))
            if not args.apply:
                print(f"\ndry-run -- {len(items)} row(s) checked, nothing was modified. "
                      "Pass --apply to write.")
                return 0
            if missing_all:
                print(f"\n{len(missing_all)} id(s) matched no row (or not their "
                      "spotify_id): wrong --target, or ids from another database?")
                return 1
            return 0

        rep = gt.report(conn)
        if args.json:
            print(json.dumps(rep, indent=2))
        else:
            print("\ngenre_status across tracks:")
            for k in sorted(rep["by_status"], key=lambda x: -rep["by_status"][x]):
                print(f"    {k:<10} {rep['by_status'][k]}")
            print(f"\nwaiting for scores ({gt.WAITING_PREDICATE}) : {rep['waiting']}")
            print("    genre blocks nothing: a waiting track moves through the "
                  "pipeline and the DJ as if it were scored.")
        if rep["waiting"]:
            if not args.json:
                print("\nWAITING: run a tagging session -- see "
                      "ingest_pipeline/README.md 'Tagging genres'.")
            return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
