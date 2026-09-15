"""One-shot: add users.spotify_email TEXT on the Turso prod DB.

Reads TURSO_DATABASE_URL / TURSO_AUTH_TOKEN from the environment. Idempotent —
checks PRAGMA table_info first and skips if the column already exists.
"""
import os
import sys
import json
import urllib.request


def hrana_execute(url: str, token: str, stmt: str) -> dict:
    # libsql/turso HTTP v2 pipeline endpoint.
    base = url.replace("libsql://", "https://").rstrip("/")
    req = urllib.request.Request(
        base + "/v2/pipeline",
        data=json.dumps({
            "requests": [
                {"type": "execute", "stmt": {"sql": stmt}},
                {"type": "close"},
            ]
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url or not token:
        print("missing TURSO_DATABASE_URL or TURSO_AUTH_TOKEN in env", file=sys.stderr)
        return 2

    info = hrana_execute(url, token, "PRAGMA table_info(users)")
    cols = []
    try:
        result = info["results"][0]["response"]["result"]
        name_idx = [c["name"] for c in result["cols"]].index("name")
        for row in result["rows"]:
            cell = row[name_idx]
            cols.append(cell.get("value") if isinstance(cell, dict) else cell)
    except (KeyError, IndexError, ValueError) as e:
        print("could not parse PRAGMA response:", e, file=sys.stderr)
        print(json.dumps(info)[:2000], file=sys.stderr)
        return 3

    print("current users columns:", cols)
    if "spotify_email" in cols:
        print("spotify_email already exists — nothing to do")
        return 0

    print("adding spotify_email TEXT ...")
    resp = hrana_execute(url, token, "ALTER TABLE users ADD COLUMN spotify_email TEXT")
    first = resp.get("results", [{}])[0]
    if first.get("type") == "error":
        print("ALTER failed:", json.dumps(first)[:2000], file=sys.stderr)
        return 4
    print("ok")

    verify = hrana_execute(url, token, "PRAGMA table_info(users)")
    result = verify["results"][0]["response"]["result"]
    name_idx = [c["name"] for c in result["cols"]].index("name")
    new_cols = []
    for row in result["rows"]:
        cell = row[name_idx]
        new_cols.append(cell.get("value") if isinstance(cell, dict) else cell)
    print("post-migration columns:", new_cols)
    return 0 if "spotify_email" in new_cols else 5


if __name__ == "__main__":
    sys.exit(main())
