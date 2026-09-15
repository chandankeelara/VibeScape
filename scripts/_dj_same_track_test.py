"""
DJ endpoint sanity test:
  Send /similar with the SAME positive track repeated 10x and observe the
  top results. Ideally the top hit is that same track (cos ~ 1.0) when it's
  not the seed, or its nearest neighbor otherwise.

Local dev only. Talks to http://localhost:8000 and writes a session row
directly into data/vibescape.db to skip the login flow.
"""
import json
import secrets
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "vibescape.db"
BASE = "http://localhost:8000"
USER_ID = 20  # chandan
FUSED_MV = "fused_v1_mert_scalar_lang"
POSITIVE_REPEATS = 10
POSITIVE_WEIGHT = 1.2  # matches the queued weight after our recent change


def _issue_token(conn: sqlite3.Connection, user_id: int) -> str:
    tok = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (tok, user_id))
    conn.commit()
    return tok


def _pick_two_tracks(conn: sqlite3.Connection):
    rows = conn.execute(
        """
        SELECT t.id, t.title, t.artist, t.spotify_id, t.apple_id
        FROM tracks t
        JOIN user_tracks ut ON ut.track_id = t.id
        JOIN track_embeddings te ON te.track_id = t.id
        WHERE ut.user_id = ?
          AND t.ingestion_status = 'done'
          AND te.model_version = ?
        ORDER BY t.id
        LIMIT 2
        """,
        (USER_ID, FUSED_MV),
    ).fetchall()
    if len(rows) < 2:
        raise SystemExit("need at least 2 embedded tracks in chandan's library")
    return rows


def _track_key(row) -> str:
    # backend accepts spotify_id, apple_id, or numeric track id as the seed key
    return row["spotify_id"] or (str(row["apple_id"]) if row["apple_id"] else str(row["id"]))


def _post_similar(token: str, seed_key: str, positive_track_key: str):
    body = {
        "mode": "dj",
        "variant": "fused",
        "positive_ids": [
            {"id": positive_track_key, "weight": POSITIVE_WEIGHT}
            for _ in range(POSITIVE_REPEATS)
        ],
        "negative_ids": [],
        "exclude_ids": [],
        "limit": 10,
    }
    url = f"{BASE}/api/tracks/{urllib.request.quote(seed_key, safe='')}/similar"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _print_run(label: str, resp: dict, expect_key: str):
    print(f"\n=== {label} ===")
    print(f"mode_used   : {resp.get('mode_used')}")
    print(f"variant_used: {resp.get('variant_used')}")
    tracks = resp.get("tracks", [])
    print(f"top {len(tracks)}:")
    for i, t in enumerate(tracks, 1):
        key = t.get("spotify_id") or t.get("apple_id") or t.get("id")
        star = "  <-- POSITIVE TRACK" if str(key) == str(expect_key) else ""
        print(f"  {i:2d}. score={t.get('score'):+.4f}  {t.get('title')} - {t.get('artist')}{star}")


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    token = _issue_token(conn, USER_ID)
    a, b = _pick_two_tracks(conn)
    a_key, b_key = _track_key(a), _track_key(b)
    conn.close()

    print(f"positive track (repeated {POSITIVE_REPEATS}x, weight {POSITIVE_WEIGHT} each):")
    print(f"  A [{a_key}]  {a['title']} - {a['artist']}")
    print(f"other track used as seed:")
    print(f"  B [{b_key}]  {b['title']} - {b['artist']}")

    # Case 1: seed = B, positive = A × 10  ->  A itself should top the list
    r1 = _post_similar(token, seed_key=b_key, positive_track_key=a_key)
    _print_run("seed=B, positive=A x10 (expect A at rank #1 with score ~1.0)", r1, a_key)

    # Case 2: seed = A, positive = A × 10  ->  A is excluded; expect A's neighbor
    r2 = _post_similar(token, seed_key=a_key, positive_track_key=a_key)
    _print_run("seed=A, positive=A x10 (A is excluded; expect nearest neighbor of A)", r2, a_key)


if __name__ == "__main__":
    main()
