"""
SUPERSEDED 2026-10-10. Language is no longer detected by Whisper and no
longer passes through a file-based export/apply pair; the database is the
queue and ingest_pipeline/language_tagging.py is the write path. This
script selects on language_status='whisper_done', which has no producer
any more. See ingest_pipeline/README.md, section Tagging languages.
Kept only for a batch that was already in flight; delete after that.

Export tracks awaiting LLM language verification to a review queue.

Reads rows with language_status='whisper_done' from local sqlite and
dumps them to data/_llm_verify_queue.jsonl (one row per line, JSONL for
easy line-based review). The Claude Code / LLM reviewer opens this file,
makes calls per track, and writes a corrections JSON that
_llm_verify_apply.py consumes.

Run:
    D:/Softwares/MiniConda/python.exe scripts/_llm_verify_export.py
    D:/Softwares/MiniConda/python.exe scripts/_llm_verify_export.py --limit 200
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_LOCAL_DB = _REPO / "data" / "vibescape.db"
_QUEUE = _REPO / "data" / "_llm_verify_queue.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of rows exported (for chunked review).")
    ap.add_argument("--offset", type=int, default=0,
                    help="Skip this many rows first. With --limit, carves a "
                         "disjoint slice so parallel reviewers don't overlap.")
    ap.add_argument("--out", default=None,
                    help="Write to this path instead of the shared queue file. "
                         "Required when running reviewers in parallel, or they "
                         "clobber each other.")
    args = ap.parse_args()

    out_path = Path(args.out) if args.out else _QUEUE

    conn = sqlite3.connect(str(_LOCAL_DB)); conn.row_factory = sqlite3.Row
    sql = (
        "SELECT id, spotify_id, title, artist, album, language, "
        "       language_confidence, language_top3_json "
        "FROM tracks "
        "WHERE language_status = 'whisper_done' "
        "ORDER BY id"
    )
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
        if args.offset:
            sql += f" OFFSET {int(args.offset)}"
    elif args.offset:
        # SQLite requires LIMIT before OFFSET; -1 means "no limit".
        sql += f" LIMIT -1 OFFSET {int(args.offset)}"
    rows = conn.execute(sql).fetchall()
    conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            top3 = None
            if r["language_top3_json"]:
                try:
                    top3 = json.loads(r["language_top3_json"])
                except Exception:
                    top3 = None
            f.write(json.dumps({
                "id":                   r["id"],
                "spotify_id":           r["spotify_id"],
                "title":                r["title"],
                "artist":               r["artist"],
                "album":                r["album"],
                "whisper_lang":         r["language"],
                "whisper_confidence":   r["language_confidence"],
                "whisper_top3":         top3,
            }, ensure_ascii=False) + "\n")

    print(f"exported {len(rows)} rows to {out_path}")
    print(f"reviewer writes corrections to data/_llm_verify_corrections.json")
    print(f"then run:  scripts/_llm_verify_apply.py --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
