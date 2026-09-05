"""Quick smoke test for the HTTP-based Turso adapter in db_client.py."""
import os
import sys
from pathlib import Path

os.environ["DB_BACKEND"] = "turso"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import db_client  # noqa: E402

conn = db_client.create_connection()
print("connected")

r = conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()
print(f"tracks count = {r['n']}")

r = conn.execute("SELECT id, display_name FROM users LIMIT 3").fetchall()
print(f"users sample: {[(row['id'], row['display_name']) for row in r]}")

r = conn.execute("SELECT id, title, artist FROM tracks WHERE id = ?", (1,)).fetchone()
if r:
    print(f"track id=1: {r['title']!r} - {r['artist']!r}")
else:
    print("track id=1: not found")

r = conn.execute(
    "SELECT COUNT(*) AS n FROM tracks WHERE mood = ? AND vibe_score > ?",
    ("hype", 50.0),
)
row = r.fetchone()
print(f"hype tracks with vibe>50: {row['n']}")

conn.close()
print("OK")
