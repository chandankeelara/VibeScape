import sqlite3
c = sqlite3.connect(r"D:\Git\virtual457-projects\VibeScape\data\vibescape_prod.db")
print("=== all tables ===")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
    print(r)
print()
print("=== user_tracks schema (has ON DELETE CASCADE?) ===")
for r in c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='user_tracks'").fetchall():
    print(r[0])
print()
print("=== spotify_tokens or sessions? ===")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%session%' OR name LIKE '%token%')").fetchall():
    print(r)
