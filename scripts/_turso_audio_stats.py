"""Check what fraction of tracks in Turso have audio_path (local file
reference) vs preview_url (streamable HTTPS URL) — determines whether
audio playback will 404 on Cloud Run.
"""
import os, sys
from pathlib import Path
os.environ["DB_BACKEND"] = "turso"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import db_client
conn = db_client.create_connection()

total = conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"]
has_audio_path = conn.execute(
    "SELECT COUNT(*) AS n FROM tracks WHERE audio_path IS NOT NULL AND audio_path != ''"
).fetchone()["n"]
has_preview_url = conn.execute(
    "SELECT COUNT(*) AS n FROM tracks WHERE preview_url IS NOT NULL AND preview_url != ''"
).fetchone()["n"]
has_youtube = conn.execute(
    "SELECT COUNT(*) AS n FROM tracks WHERE youtube_id IS NOT NULL AND youtube_id != ''"
).fetchone()["n"]
has_spotify_id = conn.execute(
    "SELECT COUNT(*) AS n FROM tracks WHERE spotify_id IS NOT NULL AND spotify_id != ''"
).fetchone()["n"]

# Sample of preview_url shapes
sample = conn.execute(
    "SELECT title, artist, audio_path, preview_url FROM tracks "
    "WHERE preview_url IS NOT NULL LIMIT 3"
).fetchall()

print(f"total tracks:                {total}")
print(f"tracks with audio_path:      {has_audio_path}  ({100*has_audio_path/total:.1f}%)")
print(f"tracks with preview_url:     {has_preview_url}  ({100*has_preview_url/total:.1f}%)")
print(f"tracks with youtube_id:      {has_youtube}  ({100*has_youtube/total:.1f}%)")
print(f"tracks with spotify_id:      {has_spotify_id}  ({100*has_spotify_id/total:.1f}%)")
print()
print("sample rows (title / preview_url head):")
for r in sample:
    pu = r["preview_url"] or ""
    print(f"  {r['title']!r} -- audio_path={r['audio_path']!r} preview_url={pu[:70]!r}")
conn.close()
