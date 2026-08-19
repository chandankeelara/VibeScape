import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "vibescape.db"
SCHEMA_PATH = ROOT / "schema.sql"


def _table_info(conn: sqlite3.Connection, table: str):
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _needs_rebuild(conn: sqlite3.Connection) -> bool:
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


def _dedupe_spotify_ids(conn: sqlite3.Connection) -> None:
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


def _rebuild_tracks(conn: sqlite3.Connection) -> None:
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


_EXTENDED_COLUMNS = [
    "tempo_stability",
    "onset_rate",
    "energy_mean",
    "energy_std",
    "bandwidth",
    "rolloff",
    "spectral_contrast",
    "flatness",
    "timbre_variability",
    "valence_mode",
    "tonnetz_std",
    "acousticness",
    "activation",
    "valence",
    "activation_relative",
]


def _migrate(conn: sqlite3.Connection) -> None:
    stmts = [
        "ALTER TABLE tracks ADD COLUMN audio_path TEXT",
        "ALTER TABLE tracks ADD COLUMN spotify_id TEXT",
        "ALTER TABLE tracks ADD COLUMN classification_source TEXT",
        "ALTER TABLE tracks ADD COLUMN chroma_mean_json TEXT",
    ]
    for c in _EXTENDED_COLUMNS:
        stmts.append(f"ALTER TABLE tracks ADD COLUMN {c} REAL")
    for stmt in stmts:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass
    # ensure the new indexes exist even on already-created DBs
    for idx in (
        "CREATE INDEX IF NOT EXISTS idx_tracks_activation ON tracks(activation)",
        "CREATE INDEX IF NOT EXISTS idx_tracks_activation_rel ON tracks(activation_relative)",
    ):
        try:
            conn.execute(idx)
            conn.commit()
        except sqlite3.OperationalError:
            pass
    if _needs_rebuild(conn):
        _rebuild_tracks(conn)
    _backfill_classification_source(conn)


def _backfill_classification_source(conn: sqlite3.Connection) -> int:
    """Populate classification_source for existing rows that lack it.

    Heuristic based on preview_url hostname:
      - contains 'p.scdn.co'                          -> 'spotify_preview'
      - contains 'itunes-assets' / 'audio-ssl.itunes' -> 'itunes_term_search'
        (we can't retroactively distinguish ISRC-hit vs term-search hits;
         all current tracks were term-search since ISRC lookup has been dead)
      - preview_url missing/empty                     -> 'none'
      - anything else                                 -> 'unknown'
    Runs idempotently: only touches rows where classification_source is NULL/empty.
    """
    try:
        rows = conn.execute(
            "SELECT id, preview_url FROM tracks "
            "WHERE classification_source IS NULL OR classification_source = ''"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    updates = 0
    for row in rows:
        pid, url = row[0], (row[1] or "").lower()
        if "p.scdn.co" in url:
            src = "spotify_preview"
        elif "itunes-assets" in url or "audio-ssl.itunes" in url or "mzaf_" in url:
            src = "itunes_term_search"
        elif not url:
            src = "none"
        else:
            src = "unknown"
        conn.execute("UPDATE tracks SET classification_source = ? WHERE id = ?", (src, pid))
        updates += 1
    if updates:
        conn.commit()
    return updates


def ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(schema)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def get_conn():
    ensure_db()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
