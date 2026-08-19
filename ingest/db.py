import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "vibescape.db")
SCHEMA_PATH = os.path.join(ROOT, "schema.sql")


def _table_info(conn, table):
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _needs_rebuild(conn) -> bool:
    info = _table_info(conn, "tracks")
    if not info:
        return False
    cols = {row[1]: row for row in info}
    if "id" not in cols:
        return True
    apple = cols.get("apple_id")
    if apple and apple[5] == 1:
        return True
    return False


def _dedupe_spotify_ids(conn) -> None:
    conn.execute(
        """
        UPDATE tracks SET spotify_id = NULL
        WHERE spotify_id IS NOT NULL
          AND rowid NOT IN (
              SELECT MIN(rowid) FROM tracks
              WHERE spotify_id IS NOT NULL
              GROUP BY spotify_id
          )
        """
    )
    conn.commit()


def _rebuild_tracks(conn) -> None:
    _dedupe_spotify_ids(conn)
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE tracks RENAME TO tracks_old")
        conn.execute(
            """
            CREATE TABLE tracks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                apple_id      INTEGER UNIQUE,
                spotify_id    TEXT UNIQUE,
                isrc          TEXT,
                title         TEXT NOT NULL,
                artist        TEXT NOT NULL,
                album         TEXT,
                genre         TEXT,
                artwork_url   TEXT,
                preview_url   TEXT,
                track_view_url TEXT,
                duration_ms   INTEGER,
                tempo         REAL,
                energy        REAL,
                brightness    REAL,
                zcr           REAL,
                mfcc_json     TEXT,
                vibe_score    REAL NOT NULL,
                mood          TEXT,
                audio_path    TEXT,
                classification_source TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        old_cols = {row[1] for row in _table_info(conn, "tracks_old")}
        carry = [c for c in [
            "apple_id", "spotify_id", "isrc", "title", "artist", "album", "genre",
            "artwork_url", "preview_url", "track_view_url", "duration_ms",
            "tempo", "energy", "brightness", "zcr", "mfcc_json",
            "vibe_score", "mood", "audio_path", "classification_source", "created_at",
        ] if c in old_cols]
        col_list = ", ".join(carry)
        conn.execute(f"INSERT INTO tracks ({col_list}) SELECT {col_list} FROM tracks_old")
        conn.execute("DROP TABLE tracks_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_vibe ON tracks(vibe_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_apple_id ON tracks(apple_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate(conn: sqlite3.Connection) -> None:
    for stmt in (
        "ALTER TABLE tracks ADD COLUMN audio_path TEXT",
        "ALTER TABLE tracks ADD COLUMN spotify_id TEXT",
        "ALTER TABLE tracks ADD COLUMN classification_source TEXT",
    ):
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass
    if _needs_rebuild(conn):
        _rebuild_tracks(conn)


def get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    _migrate(conn)
    return conn


def upsert_track(conn: sqlite3.Connection, track_data: dict) -> None:
    cols = [
        "apple_id", "spotify_id", "isrc", "title", "artist", "album", "genre",
        "artwork_url", "preview_url", "track_view_url", "duration_ms",
        "tempo", "energy", "brightness", "zcr", "mfcc_json",
        "vibe_score", "mood", "audio_path", "classification_source",
    ]
    values = [track_data.get(c) for c in cols]

    spotify_id = track_data.get("spotify_id")
    apple_id = track_data.get("apple_id")

    existing_id = None
    if spotify_id:
        row = conn.execute("SELECT id FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
        if row:
            existing_id = row[0]
    if existing_id is None and apple_id:
        row = conn.execute("SELECT id FROM tracks WHERE apple_id = ?", (apple_id,)).fetchone()
        if row:
            existing_id = row[0]

    if existing_id is None:
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})"
        conn.execute(sql, values)
    else:
        set_clause = ", ".join([f"{c} = ?" for c in cols])
        sql = f"UPDATE tracks SET {set_clause} WHERE id = ?"
        conn.execute(sql, values + [existing_id])
    conn.commit()


def track_exists_by_spotify_id(conn: sqlite3.Connection, spotify_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
    return row is not None


def update_audio_path(conn: sqlite3.Connection, apple_id: int, audio_path: str) -> None:
    conn.execute("UPDATE tracks SET audio_path = ? WHERE apple_id = ?", (audio_path, apple_id))
    conn.commit()


def update_spotify_id(conn: sqlite3.Connection, apple_id: int, spotify_id: str) -> None:
    conn.execute("UPDATE tracks SET spotify_id = ? WHERE apple_id = ?", (spotify_id, apple_id))
    conn.commit()
