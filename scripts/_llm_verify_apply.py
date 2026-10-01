"""
Apply LLM-produced language corrections from
data/_llm_verify_corrections.json to local + Turso.

Corrections JSON schema (one object per track):
  {
    "id": 12,                    # local track_id — required
    "action": "keep"             # OR "change" OR "no_match"
    "new_lang": "kn"             # only when action == "change"
    "note": "..."                # optional free-text reason
  }

Semantics:
  - keep     — Whisper's tag is correct. Set language_status='done'. No other change.
  - change   — Update tracks.language to new_lang. Set language_status='done'.
               CASCADE: set fuse_status='pending' so the next pipeline
               pass rebuilds fused_embedding (which has a 20% language
               one-hot component).
  - no_match — LLM says the track is instrumental / unknowable. Set
               language=NULL, language_status='done'. Also cascade
               fuse_status so the language slot in fused becomes the
               'other' bucket instead of the previous incorrect one.

Applies to both local sqlite AND Turso in a single invocation, matched
by spotify_id so track_id divergence between environments doesn't matter.

Run:
    D:/Softwares/MiniConda/python.exe scripts/_llm_verify_apply.py            # dry-run
    D:/Softwares/MiniConda/python.exe scripts/_llm_verify_apply.py --apply
    D:/Softwares/MiniConda/python.exe scripts/_llm_verify_apply.py --apply --local-only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_LOCAL_DB = _REPO / "data" / "vibescape.db"
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"
_CORRECTIONS = _REPO / "data" / "_llm_verify_corrections.json"


def _load_turso_creds() -> tuple[str, str]:
    text = _PS1.read_text(encoding="utf-8")
    url = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    tok = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    return url, tok


def _load_corrections() -> list[dict]:
    if not _CORRECTIONS.exists():
        raise SystemExit(f"corrections file not found: {_CORRECTIONS}")
    with _CORRECTIONS.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("corrections file must be a JSON array of objects")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--turso-only", action="store_true")
    args = ap.parse_args()

    corrections = _load_corrections()
    print(f"corrections in file: {len(corrections)}")

    # Resolve local id → spotify_id (needed for Turso matching)
    lconn = sqlite3.connect(str(_LOCAL_DB)); lconn.row_factory = sqlite3.Row
    ops: list[dict] = []   # planned ops with spotify_id resolved
    for c in corrections:
        tid = c.get("id")
        action = (c.get("action") or "").strip().lower()
        if action not in ("keep", "change", "no_match"):
            print(f"  skip id={tid}: invalid action {action!r}")
            continue
        row = lconn.execute(
            "SELECT spotify_id, language FROM tracks WHERE id = ?", (tid,),
        ).fetchone()
        if not row or not row["spotify_id"]:
            print(f"  skip id={tid}: not found or no spotify_id")
            continue
        new_lang = None
        if action == "change":
            new_lang = c.get("new_lang")
            if not new_lang:
                print(f"  skip id={tid}: change action needs new_lang")
                continue
        # detect if the semantic language will change (drives embedding cascade)
        old_lang = row["language"]
        if action == "keep":
            will_change = False
        elif action == "no_match":
            will_change = (old_lang is not None)
            new_lang = None
        else:  # change
            will_change = (old_lang != new_lang)
        ops.append({
            "id":         int(tid),
            "spotify_id": row["spotify_id"],
            "action":     action,
            "new_lang":   new_lang,
            "old_lang":   old_lang,
            "will_change": will_change,
        })

    print(f"ops resolved: {len(ops)}")
    from collections import Counter
    by_action = Counter(o["action"] for o in ops)
    by_change = sum(1 for o in ops if o["will_change"])
    print(f"  actions: {dict(by_action)}   language changes: {by_change}   cascade to fuse: {by_change}")

    if not args.apply:
        print("\ndry-run. Pass --apply to write.")
        return 0

    # ---- write local ----
    if not args.turso_only:
        print("\nlocal ...")
        for o in ops:
            sid = o["spotify_id"]
            if o["action"] == "keep":
                lconn.execute(
                    "UPDATE tracks SET language_status = 'done' "
                    "WHERE spotify_id = ?", (sid,),
                )
            elif o["action"] == "no_match":
                sql = "UPDATE tracks SET language = NULL, language_status = 'done'"
                sql += ", fuse_status = 'pending'" if o["will_change"] else ""
                sql += " WHERE spotify_id = ?"
                lconn.execute(sql, (sid,))
            else:  # change
                sql = ("UPDATE tracks SET language = ?, language_confidence = 1.0, "
                       "language_status = 'done'")
                sql += ", fuse_status = 'pending'" if o["will_change"] else ""
                sql += " WHERE spotify_id = ?"
                lconn.execute(sql, (o["new_lang"], sid))
        lconn.commit()
        print(f"  wrote {len(ops)} rows")

    # ---- write turso ----
    if not args.local_only:
        print("\nturso ...")
        url, tok = _load_turso_creds()
        os.environ["TURSO_DATABASE_URL"] = url
        os.environ["TURSO_AUTH_TOKEN"]   = tok
        os.environ["DB_BACKEND"]         = "turso"
        sys.path.insert(0, str(_REPO / "backend"))
        sys.path.insert(0, str(_REPO / "ingest"))
        import db_client  # noqa: E402
        tconn = db_client.create_connection()
        n = 0
        for o in ops:
            sid = o["spotify_id"]
            try:
                if o["action"] == "keep":
                    tconn.execute(
                        "UPDATE tracks SET language_status = 'done' "
                        "WHERE spotify_id = ?", (sid,),
                    )
                elif o["action"] == "no_match":
                    sql = "UPDATE tracks SET language = NULL, language_status = 'done'"
                    sql += ", fuse_status = 'pending'" if o["will_change"] else ""
                    sql += " WHERE spotify_id = ?"
                    tconn.execute(sql, (sid,))
                else:  # change
                    sql = ("UPDATE tracks SET language = ?, language_confidence = 1.0, "
                           "language_status = 'done'")
                    sql += ", fuse_status = 'pending'" if o["will_change"] else ""
                    sql += " WHERE spotify_id = ?"
                    tconn.execute(sql, (o["new_lang"], sid))
                n += 1
            except Exception as e:
                print(f"  turso failed sid={sid}: {e}")
        print(f"  wrote {n} rows")
        tconn.close()

    lconn.close()
    print(f"\ncascade summary: {by_change} track(s) will have their fused vector rebuilt "
          f"(fuse_status='pending').")
    print("run:  D:/Softwares/MiniConda/python.exe scripts/run_ingest_v2.py --stages fuse,youtube")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
