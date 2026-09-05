"""Find and remove duplicate NULL-user_id rows blocking the local migration.

Composite unique indexes are (user_id, apple_id) and (user_id, spotify_id).
NULL user_ids currently coexist because SQLite treats NULL as distinct in
composite uniques. The migration sets them all to user_id=1, which then
collides among the duplicate rows. Keep the first (lowest id) per apple_id
and per spotify_id, drop the rest.
"""
import shutil
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "vibescape.db"
print(f"[db] {DB} exists={DB.exists()} size={DB.stat().st_size}")

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

null_rows = conn.execute("""
    SELECT id, apple_id, spotify_id, title, artist FROM tracks
    WHERE user_id IS NULL
    ORDER BY id
""").fetchall()
print(f"[null user_id rows] {len(null_rows)}")

# Group NULL-user_id rows by apple_id and by spotify_id. Keep the lowest
# id per group; mark the rest for deletion.
keep_ids = set()
delete_ids = set()

for keycol in ("apple_id", "spotify_id"):
    seen = {}
    for r in null_rows:
        v = r[keycol]
        if v is None or v == "":
            continue
        if v in seen:
            delete_ids.add(r["id"])
        else:
            seen[v] = r["id"]
            keep_ids.add(r["id"])

# Also detect NULL-user_id rows whose (apple_id or spotify_id) collides with
# an EXISTING user_id=1 row — those need to be dropped too, but we handle them
# after the NULL-vs-NULL dedup.
existing_apples = {
    r[0] for r in conn.execute(
        "SELECT apple_id FROM tracks WHERE user_id = 1 AND apple_id IS NOT NULL"
    ).fetchall()
}
existing_spotifys = {
    r[0] for r in conn.execute(
        "SELECT spotify_id FROM tracks WHERE user_id = 1 AND spotify_id IS NOT NULL"
    ).fetchall()
}
for r in null_rows:
    if r["id"] in delete_ids:
        continue
    if (r["apple_id"] and r["apple_id"] in existing_apples) or \
       (r["spotify_id"] and r["spotify_id"] in existing_spotifys):
        delete_ids.add(r["id"])

print(f"[delete plan] {len(delete_ids)} rows will be removed")

if not delete_ids:
    print("[note] nothing to delete — check migration code path")
    conn.close()
    raise SystemExit(0)

# Sample of victims
for i, tid in enumerate(sorted(delete_ids)[:8]):
    r = conn.execute(
        "SELECT id, apple_id, spotify_id, title, artist FROM tracks WHERE id = ?",
        (tid,),
    ).fetchone()
    print(f"  id={r['id']} apple_id={r['apple_id']} "
          f"spotify_id={(r['spotify_id'] or '')[:12]} "
          f"'{r['title']}' - '{r['artist']}'")
if len(delete_ids) > 8:
    print(f"  ... and {len(delete_ids) - 8} more")

# Reversible backup
backup = DB.with_suffix(".db.pre-conflict-fix")
if not backup.exists():
    shutil.copyfile(str(DB), str(backup))
    print(f"[backup] {backup.name}")

placeholders = ", ".join(["?"] * len(delete_ids))
conn.execute(f"DELETE FROM tracks WHERE id IN ({placeholders})", tuple(delete_ids))
conn.commit()
after = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
print(f"[deleted] {len(delete_ids)} rows. tracks count now: {after}")
conn.close()
