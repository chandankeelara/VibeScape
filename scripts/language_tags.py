"""
See what is waiting for a language tag, and write tags back.

The database is the queue -- there is no export file and no apply step.
This script is a convenience over the two SQL statements documented in
ingest_pipeline/README.md ("Tagging languages"); a Claude Code session can
use either. The reason to prefer the write path here is that a tag is
three columns plus a conditional `fuse_status='pending'` cascade, and
forgetting the cascade leaves a stale 788-d vector that nothing flags.

    # How many are waiting, and for how long?
    python scripts/language_tags.py --status

    # The rows themselves, as JSON lines, oldest first
    python scripts/language_tags.py --waiting --limit 50

    # Write verdicts. 'set' takes an ISO 639-1 code; 'clear' means the
    # track has NO language (instrumental) -- a verdict, not a failure.
    python scripts/language_tags.py --set 4u1aBcD=kn --set 2Uyj6K6=hi --apply
    python scripts/language_tags.py --clear 49d0AMz --apply

Writes are off by default, like the rest of scripts/. It talks to whatever
backend/db.get_conn() points at -- local sqlite unless DB_BACKEND=turso is
set in the environment. It does not read scripts/_load_gcp_secrets.ps1 and
will not touch production unless you deliberately configure it to.

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
from ingest_pipeline import language_tagging as lt  # noqa: E402


def _key(s: str) -> dict:
    """A track reference: a spotify_id, or an integer local track id."""
    return {"track_id": int(s)} if s.isdigit() else {"spotify_id": s}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true",
                    help="counts and wait ages (default)")
    ap.add_argument("--waiting", action="store_true",
                    help="print the waiting rows as JSON lines")
    ap.add_argument("--limit", type=int, default=50, help="rows for --waiting")
    ap.add_argument("--set", action="append", default=[], metavar="REF=CODE",
                    help="tag a track: <spotify_id|id>=<iso639-1>")
    ap.add_argument("--clear", action="append", default=[], metavar="REF",
                    help="record 'no language' (instrumental/uncallable)")
    ap.add_argument("--apply", action="store_true", help="actually write")
    ap.add_argument("--json", action="store_true", help="--status as JSON")
    args = ap.parse_args(argv)

    writes = bool(args.set or args.clear)
    conn = get_conn()
    try:
        if args.waiting:
            for r in lt.fetch_waiting(conn, args.limit):
                print(json.dumps({k: r[k] for k in r.keys()}, ensure_ascii=False))
            return 0

        for spec in args.set:
            ref, _, code = spec.partition("=")
            if not code:
                print(f"bad --set {spec!r}: expected REF=CODE")
                return 2
            if args.apply:
                res = lt.tag(conn, **_key(ref), language=code)
                print(f"set   {ref} {res['old']!r} -> {res['new']!r}"
                      f"{'  [cascade: fuse_status=pending]' if res['cascaded'] else ''}")
            else:
                print(f"set   {ref} -> {code}")
        for ref in args.clear:
            if args.apply:
                res = lt.tag(conn, **_key(ref), clear=True)
                print(f"clear {ref} {res['old']!r} -> NULL"
                      f"{'  [cascade: fuse_status=pending]' if res['cascaded'] else ''}")
            else:
                print(f"clear {ref} -> NULL")
        if writes and not args.apply:
            print("\ndry-run -- nothing was modified. Pass --apply to write.")

        if args.json:
            print(json.dumps(lt.report(conn), indent=2))
            return 0

        if args.status or not writes:
            rep = lt.report(conn)
            print("\nlanguage_status across tracks:")
            for k in sorted(rep["by_status"], key=lambda x: -rep["by_status"][x]):
                print(f"    {k:<14} {rep['by_status'][k]}")
            print()
            print(f"waiting for a tag ({lt.WAITING_PREDICATE}) : {rep['waiting']}")
            print(f"  oldest / median wait, days              : "
                  f"{rep['oldest_wait_days']} / {rep['median_wait_days']}")
            print(f"blocked on language (encoded, unfused)    : "
                  f"{rep['blocked_on_language']}")
            print("    encoded by MERT but not fused, so NOT in the DJ pool.")
            if rep["waiting"] and not writes:
                print("\nWAITING: run a tagging session -- see "
                      "ingest_pipeline/README.md 'Tagging languages'.")
                return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
