"""Idempotent: sync prod Turso users table to schema.sql.

Adds any missing columns from the expected list and creates the two
partial unique indexes. Safe to re-run.
"""
import os, sys, json, urllib.request


EXPECTED_COLUMNS = [
    ("email",                "TEXT"),
    ("password_hash",        "TEXT"),
    ("spotify_country",      "TEXT"),
    ("spotify_product",      "TEXT"),
    ("spotify_avatar_url",   "TEXT"),
    ("spotify_profile_url",  "TEXT"),
    ("last_login_at",        "TIMESTAMP"),
]

INDEXES = [
    ("idx_users_spotify_uid",
     "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_spotify_uid "
     "ON users(spotify_user_id) WHERE spotify_user_id IS NOT NULL"),
    ("idx_users_email",
     "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
     "ON users(email) WHERE email IS NOT NULL AND email != ''"),
]


def hrana(url, token, sql):
    base = url.replace("libsql://", "https://").rstrip("/")
    req = urllib.request.Request(
        base + "/v2/pipeline",
        data=json.dumps({"requests": [
            {"type": "execute", "stmt": {"sql": sql}},
            {"type": "close"},
        ]}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def rows(resp):
    result = resp["results"][0]["response"]["result"]
    cols = [c["name"] for c in result["cols"]]
    out = []
    for row in result["rows"]:
        d = {}
        for i, cell in enumerate(row):
            v = cell.get("value") if isinstance(cell, dict) else cell
            d[cols[i]] = v
        out.append(d)
    return out


def main():
    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url or not token:
        print("missing TURSO_DATABASE_URL / TURSO_AUTH_TOKEN", file=sys.stderr)
        return 2

    info = hrana(url, token, "PRAGMA table_info(users)")
    current = {r["name"] for r in rows(info)}
    print("current users columns:", sorted(current))

    for name, typ in EXPECTED_COLUMNS:
        if name in current:
            print(f"  = {name}: already present, skip")
            continue
        sql = f"ALTER TABLE users ADD COLUMN {name} {typ}"
        print(f"  + {name}: adding ...")
        resp = hrana(url, token, sql)
        first = resp.get("results", [{}])[0]
        if first.get("type") == "error":
            print(f"    FAILED: {json.dumps(first)[:400]}", file=sys.stderr)
            return 3

    for name, sql in INDEXES:
        print(f"  ~ index {name}: ensuring ...")
        resp = hrana(url, token, sql)
        first = resp.get("results", [{}])[0]
        if first.get("type") == "error":
            print(f"    FAILED: {json.dumps(first)[:400]}", file=sys.stderr)
            return 4

    verify = hrana(url, token, "PRAGMA table_info(users)")
    final = sorted(r["name"] for r in rows(verify))
    print("post-migration columns:", final)

    idx = hrana(url, token, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='users'")
    print("users indexes:", sorted(r["name"] for r in rows(idx)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
