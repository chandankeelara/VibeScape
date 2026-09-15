"""Read-only: list tables and row counts on the Turso prod DB."""
import os, sys, json, urllib.request


def q(url, token, sql):
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
    url = os.environ["TURSO_DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    tables = rows(q(url, token, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
    print("tables:", [t["name"] for t in tables])
    for t in tables:
        name = t["name"]
        if name.startswith("sqlite_"):
            continue
        try:
            r = rows(q(url, token, f"SELECT COUNT(*) AS c FROM \"{name}\""))
            print(f"  {name}: {r[0]['c']}")
        except Exception as e:
            print(f"  {name}: <error {e}>")


if __name__ == "__main__":
    main()
