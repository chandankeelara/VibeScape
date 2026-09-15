"""
Compare tracks between the local dev DB and the production Turso DB.

Reads Turso credentials from scripts/_load_gcp_secrets.ps1 (parsed via
regex, never printed) so nothing lands in shell history / transcripts.

Prints a diff by spotify_id:
  - tracks present in Turso but missing locally
  - tracks present locally but not in Turso
  - overlap count

Run:
    D:/Softwares/MiniConda/python.exe scripts/_turso_vs_local_diff.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

# Force UTF-8 stdout so non-ASCII track titles don't crash cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_PS1  = _REPO / "scripts" / "_load_gcp_secrets.ps1"
_LOCAL_DB = _REPO / "data" / "vibescape.db"


def _load_turso_creds_from_ps1() -> tuple[str, str]:
    """Parse TURSO_DATABASE_URL + TURSO_AUTH_TOKEN literals out of the
    PowerShell secret-loader script. Never prints them."""
    if not _PS1.exists():
        raise SystemExit(f"missing {_PS1}")
    text = _PS1.read_text(encoding="utf-8")
    url_m = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text)
    tok_m = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text)
    if not url_m or not tok_m:
        raise SystemExit("could not parse turso_url / turso_token from ps1")
    return url_m.group(1), tok_m.group(1)


def main() -> int:
    if not _LOCAL_DB.exists():
        print(f"local db missing: {_LOCAL_DB}"); return 1

    turso_url, turso_token = _load_turso_creds_from_ps1()
    os.environ["TURSO_DATABASE_URL"] = turso_url
    os.environ["TURSO_AUTH_TOKEN"]   = turso_token
    os.environ["DB_BACKEND"]         = "turso"

    sys.path.insert(0, str(_REPO / "backend"))
    sys.path.insert(0, str(_REPO / "ingest"))
    import db_client  # noqa: E402

    print(f"connecting to Turso: {turso_url}")
    tconn = db_client.create_connection()
    turso_rows = tconn.execute(
        "SELECT id, spotify_id, title, artist FROM tracks"
    ).fetchall()
    tconn.close()

    lconn = sqlite3.connect(str(_LOCAL_DB))
    lconn.row_factory = sqlite3.Row
    local_rows = lconn.execute(
        "SELECT id, spotify_id, title, artist FROM tracks"
    ).fetchall()
    lconn.close()

    turso_map = {r["spotify_id"]: (r["title"], r["artist"])
                 for r in turso_rows if r["spotify_id"]}
    local_map = {r["spotify_id"]: (r["title"], r["artist"])
                 for r in local_rows if r["spotify_id"]}
    turso_sids = set(turso_map)
    local_sids = set(local_map)

    common       = turso_sids & local_sids
    turso_only   = turso_sids - local_sids
    local_only   = local_sids - turso_sids
    turso_no_sid = sum(1 for r in turso_rows if not r["spotify_id"])
    local_no_sid = sum(1 for r in local_rows if not r["spotify_id"])

    print()
    print(f"Turso total rows: {len(turso_rows)}  ({turso_no_sid} without spotify_id)")
    print(f"Local total rows: {len(local_rows)}  ({local_no_sid} without spotify_id)")
    print()
    print(f"In both (by spotify_id): {len(common)}")
    print(f"ONLY in Turso (missing locally): {len(turso_only)}")
    print(f"ONLY in local (missing on Turso): {len(local_only)}")
    print()
    # Write full lists to files for easy scanning.
    out_dir = _REPO / "data"
    out_dir.mkdir(exist_ok=True)
    turso_only_path = out_dir / "_diff_only_in_turso.txt"
    local_only_path = out_dir / "_diff_only_in_local.txt"

    with turso_only_path.open("w", encoding="utf-8") as f:
        for sid in sorted(turso_only):
            t, a = turso_map[sid]
            f.write(f"{sid}\t{t or '?'}\t{a or '?'}\n")
    with local_only_path.open("w", encoding="utf-8") as f:
        for sid in sorted(local_only):
            t, a = local_map[sid]
            f.write(f"{sid}\t{t or '?'}\t{a or '?'}\n")

    print(f"full lists written:")
    print(f"  {turso_only_path}   ({len(turso_only)} rows)")
    print(f"  {local_only_path}   ({len(local_only)} rows)")

    if turso_only:
        print()
        print(f"=== ONLY in Turso (first 20 of {len(turso_only)}) ===")
        for sid in sorted(turso_only)[:20]:
            t, a = turso_map[sid]
            print(f"  [{sid}]  {t} - {a}")
    if local_only:
        print()
        print(f"=== ONLY in local (first 20 of {len(local_only)}) ===")
        for sid in sorted(local_only)[:20]:
            t, a = local_map[sid]
            print(f"  [{sid}]  {t} - {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
