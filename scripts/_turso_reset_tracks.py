"""Drop the corrupted tracks + user_tracks tables in Turso so the next
migration run can recreate them from schema.sql (modern global shape).
Preserves users + sessions rows.
"""
import os, sys
from pathlib import Path
os.environ["DB_BACKEND"] = "turso"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import db_client
conn = db_client.create_connection()

for tbl in ["user_tracks", "tracks"]:
    print(f"DROP TABLE IF EXISTS {tbl}")
    conn.execute(f"DROP TABLE IF EXISTS {tbl}")

print("verifying...")
existing = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()
print(f"remaining tables: {[r['name'] for r in existing]}")
conn.close()
