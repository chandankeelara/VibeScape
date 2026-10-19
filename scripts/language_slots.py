"""
Derive the fused vector's language slots (fused_vector.TOP_LANGS) from a
catalogue: every tagged language with at least LANG_SLOT_MIN_TRACKS finished
tracks, most first (ties by code). Read-only; prints the counts, the list it
derives, and whether that matches the frozen one.

    python scripts/language_slots.py --db data/dj_eval/eval.db

Takes a SQLite file, never production: run it on a copy of a backup
(scripts/dj_eval/setup_copy.py makes one). Changing TOP_LANGS changes the
vector space -- see fused_vector.py's docstring for what has to follow.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from ingest_pipeline.fused_vector import LANG_SLOT_MIN_TRACKS, TOP_LANGS  # noqa: E402


def derive(counts: dict, min_tracks: int = LANG_SLOT_MIN_TRACKS) -> list:
    """{language or None: tracks} -> the slot list."""
    tagged = [(str(k).strip().lower(), int(n)) for k, n in counts.items()
              if k and str(k).strip() and int(n) >= min_tracks]
    return [k for k, _ in sorted(tagged, key=lambda kn: (-kn[1], kn[0]))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, required=True, help="a SQLite copy")
    ap.add_argument("--min", type=int, default=LANG_SLOT_MIN_TRACKS)
    a = ap.parse_args(argv)
    db = a.db.resolve()
    if (_REPO / "data" / "backups").resolve() in db.parents:
        raise SystemExit("refusing to open a file under data/backups: use a copy")
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        counts = dict(conn.execute(
            "SELECT language, COUNT(*) FROM tracks WHERE ingestion_status = 'done' "
            "GROUP BY language").fetchall())
    finally:
        conn.close()
    for k, n in sorted(counts.items(), key=lambda kn: -kn[1]):
        print(f"  {k or '(untagged)':<12} {n}")
    got = derive(counts, a.min)
    print(f"\nderived (>= {a.min}): {got}")
    print(f"frozen:              {TOP_LANGS}")
    print("MATCH" if got == TOP_LANGS else "DIFFERENT: changing it changes the space")
    return 0 if got == TOP_LANGS else 1


if __name__ == "__main__":
    raise SystemExit(main())
