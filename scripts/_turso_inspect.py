"""Inspect Turso's tracks table + count rows per column presence."""
import os, sys
from pathlib import Path
os.environ["DB_BACKEND"] = "turso"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import db_client
conn = db_client.create_connection()
cols = conn.execute("PRAGMA table_info(tracks)").fetchall()
print(f"tracks has {len(cols)} columns:")
for c in cols:
    print(f"  {c['cid']:>2}  {c['name']:<30}  {c['type']}")
print()
print(f"row count = {conn.execute('SELECT COUNT(*) AS n FROM tracks').fetchone()['n']}")
langs = conn.execute("SELECT COUNT(*) AS n FROM tracks WHERE language IS NOT NULL").fetchone() if any(c['name']=='language' for c in cols) else None
if langs:
    print(f"rows with language set = {langs['n']}")
conn.close()
