"""
Check the fused vector's language slots (fused_vector.LANG_CODES) against a
catalogue. Every tagged language should have a slot; a code the table lacks
falls into the 'other' slot until it is APPENDED to LANG_CODES (never
inserted, never reordered). Read-only.

    python scripts/language_slots.py --db data/dj_eval/eval.db

Prints the per-language track counts, the slot of each, the codes missing
from the table and the slots left; exits 1 if a code is missing. After
appending one, scripts/refuse_vectors.py rewrites only that language's
tracks. Takes a SQLite file, never production: run it on a copy of a backup
(scripts/dj_eval/setup_copy.py makes one).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from ingest_pipeline.fused_vector import LANG_CODES, OTHER_SLOT  # noqa: E402


def missing(counts: dict, table=LANG_CODES) -> list:
    """Tagged codes in `counts` ({language or None: tracks}) with no slot,
    most tracks first, ties by code: the order to append them in."""
    tagged = {}
    for k, n in counts.items():
        code = str(k or "").strip().lower()
        if code:
            tagged[code] = tagged.get(code, 0) + int(n)
    return [c for c, _ in sorted(tagged.items(), key=lambda kv: (-kv[1], kv[0]))
            if c not in table]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, required=True, help="a SQLite copy")
    a = ap.parse_args(argv)
    db = a.db.resolve()
    if (_REPO / "data" / "backups").resolve() in db.parents:
        raise SystemExit("refusing to open a file under data/backups: use a copy")
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        counts = dict(conn.execute(
            "SELECT language, COUNT(*) FROM tracks GROUP BY language").fetchall())
    finally:
        conn.close()
    for k, n in sorted(counts.items(), key=lambda kn: -kn[1]):
        code = str(k or "").strip().lower()
        slot = "-" if not code else (LANG_CODES.index(code) if code in LANG_CODES
                                     else f"{OTHER_SLOT} (other)")
        print(f"  {k or '(untagged)':<12} {n:>6}   slot {slot}")
    miss = missing(counts)
    free = OTHER_SLOT - len(LANG_CODES)
    print(f"\n{len(LANG_CODES)} codes in the table, {free} slots free")
    if miss:
        print(f"MISSING (append, in this order, then re-fuse): {miss}")
        if len(miss) > free:
            print("the block is full: the rest stay in 'other' until a wider block")
        return 1
    print("every tagged language has a slot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
