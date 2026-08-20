import importlib.util
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "vibescape.db")

# Load backend/db.py by path so we don't shadow (or collide with) our own
# `db` module name once ingest/ is on sys.path alongside backend/.
_BACKEND_DB_PATH = os.path.join(ROOT, "backend", "db.py")
_spec = importlib.util.spec_from_file_location("_vibescape_backend_db", _BACKEND_DB_PATH)
_backend_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backend_db)


def get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    _backend_db.ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_TRACK_COLS = [
    "apple_id", "spotify_id", "isrc", "title", "artist", "album", "genre",
    "artwork_url", "preview_url", "track_view_url", "duration_ms",
    "tempo", "energy", "brightness", "zcr", "mfcc_json",
    "vibe_score", "mood", "audio_path", "classification_source",
]


def upsert_track(conn: sqlite3.Connection, track_data: dict, user_id: int | None = None,
                 source: str = "manual") -> int:
    """
    Upsert a row into the global tracks table (dedup by spotify_id then
    apple_id). If user_id is provided, also link the caller into
    user_tracks. Returns the tracks.id.
    """
    values = [track_data.get(c) for c in _TRACK_COLS]
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
        placeholders = ", ".join(["?"] * len(_TRACK_COLS))
        cur = conn.execute(
            f"INSERT INTO tracks ({', '.join(_TRACK_COLS)}) VALUES ({placeholders})",
            values,
        )
        track_id = int(cur.lastrowid)
    else:
        set_clause = ", ".join([f"{c} = ?" for c in _TRACK_COLS])
        conn.execute(f"UPDATE tracks SET {set_clause} WHERE id = ?", values + [existing_id])
        track_id = int(existing_id)

    if user_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO user_tracks (user_id, track_id, source) VALUES (?, ?, ?)",
            (user_id, track_id, source),
        )
    conn.commit()
    return track_id


def track_exists_by_spotify_id(conn: sqlite3.Connection, spotify_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
    return row is not None


def update_audio_path(conn: sqlite3.Connection, apple_id: int, audio_path: str) -> None:
    conn.execute("UPDATE tracks SET audio_path = ? WHERE apple_id = ?", (audio_path, apple_id))
    conn.commit()


def update_spotify_id(conn: sqlite3.Connection, apple_id: int, spotify_id: str) -> None:
    conn.execute("UPDATE tracks SET spotify_id = ? WHERE apple_id = ?", (spotify_id, apple_id))
    conn.commit()
