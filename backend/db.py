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


def _has_global_track_uniqueness(conn: sqlite3.Connection) -> bool:
    """
    Detect legacy schema where apple_id / spotify_id are globally UNIQUE
    (rather than composite-unique per user). Returns True if we need to
    rebuild tracks to swap the constraint. Uses PRAGMA index_list +
    index_info because ALTER TABLE can't drop column-level UNIQUEs.
    """
    try:
        idxs = conn.execute("PRAGMA index_list(tracks)").fetchall()
    except sqlite3.OperationalError:
        return False
    for idx in idxs:
        name = idx[1]
        is_unique = int(idx[2]) == 1
        if not is_unique:
            continue
        try:
            cols = conn.execute(f"PRAGMA index_info({name})").fetchall()
        except sqlite3.OperationalError:
            continue
        col_names = [c[2] for c in cols]
        # Legacy: single-column unique on apple_id or spotify_id.
        if col_names in (["apple_id"], ["spotify_id"]):
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
    """
    Rebuild the tracks table into the multi-user shape:
      - adds/keeps `user_id` column
      - drops legacy global UNIQUE(apple_id) / UNIQUE(spotify_id)
      - adds composite UNIQUE(user_id, spotify_id) / (user_id, apple_id)
    Preserves all existing rows and columns.
    """
    _dedupe_spotify_ids(conn)
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE tracks RENAME TO tracks_old")
        conn.execute(
            """
            CREATE TABLE tracks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
                apple_id      INTEGER,
                spotify_id    TEXT,
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
                tempo_stability     REAL,
                onset_rate          REAL,
                energy_mean         REAL,
                energy_std          REAL,
                bandwidth           REAL,
                rolloff             REAL,
                spectral_contrast   REAL,
                flatness            REAL,
                timbre_variability  REAL,
                valence_mode        REAL,
                tonnetz_std         REAL,
                acousticness        REAL,
                chroma_mean_json    TEXT,
                activation          REAL,
                valence             REAL,
                activation_relative REAL,
                vibe_score    REAL NOT NULL,
                mood          TEXT,
                audio_path    TEXT,
                classification_source TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, spotify_id),
                UNIQUE (user_id, apple_id)
            )
            """
        )
        old_cols = {row[1] for row in _table_info(conn, "tracks_old")}
        carry = [c for c in [
            "user_id", "apple_id", "spotify_id", "isrc", "title", "artist",
            "album", "genre", "artwork_url", "preview_url", "track_view_url",
            "duration_ms",
            "tempo", "energy", "brightness", "zcr", "mfcc_json",
            "tempo_stability", "onset_rate", "energy_mean", "energy_std",
            "bandwidth", "rolloff", "spectral_contrast", "flatness",
            "timbre_variability", "valence_mode", "tonnetz_std", "acousticness",
            "chroma_mean_json",
            "activation", "valence", "activation_relative",
            "vibe_score", "mood", "audio_path", "classification_source",
            "created_at",
        ] if c in old_cols]
        col_list = ", ".join(carry)
        conn.execute(f"INSERT INTO tracks ({col_list}) SELECT {col_list} FROM tracks_old")
        conn.execute("DROP TABLE tracks_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_vibe ON tracks(vibe_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_apple_id ON tracks(apple_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_user ON tracks(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_activation ON tracks(activation)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_activation_rel ON tracks(activation_relative)")
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


DEFAULT_USER_NAME = "chandan"


def _seed_default_user(conn: sqlite3.Connection) -> int:
    """Ensure at least one user row exists. Returns default user id."""
    row = conn.execute("SELECT id FROM users WHERE display_name = ?", (DEFAULT_USER_NAME,)).fetchone()
    if row:
        return int(row[0])
    row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO users (display_name, pin_hash) VALUES (?, NULL)",
        (DEFAULT_USER_NAME,),
    )
    conn.commit()
    return int(cur.lastrowid)


def _migrate(conn: sqlite3.Connection) -> None:
    stmts = [
        "ALTER TABLE tracks ADD COLUMN audio_path TEXT",
        "ALTER TABLE tracks ADD COLUMN spotify_id TEXT",
        "ALTER TABLE tracks ADD COLUMN classification_source TEXT",
        "ALTER TABLE tracks ADD COLUMN chroma_mean_json TEXT",
        "ALTER TABLE tracks ADD COLUMN user_id INTEGER",
    ]
    for c in _EXTENDED_COLUMNS:
        stmts.append(f"ALTER TABLE tracks ADD COLUMN {c} REAL")
    for stmt in stmts:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    # ensure legacy users table columns exist (spotify_display_name added later)
    for stmt in (
        "ALTER TABLE users ADD COLUMN spotify_user_id TEXT",
        "ALTER TABLE users ADD COLUMN spotify_display_name TEXT",
    ):
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    for idx in (
        "CREATE INDEX IF NOT EXISTS idx_tracks_activation ON tracks(activation)",
        "CREATE INDEX IF NOT EXISTS idx_tracks_activation_rel ON tracks(activation_relative)",
        "CREATE INDEX IF NOT EXISTS idx_tracks_user ON tracks(user_id)",
    ):
        try:
            conn.execute(idx)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    # Seed the default user so we have a target for the tracks backfill.
    default_uid = _seed_default_user(conn)

    # Assign existing tracks with no user_id to the default user.
    conn.execute(
        "UPDATE tracks SET user_id = ? WHERE user_id IS NULL",
        (default_uid,),
    )
    conn.commit()

    # If the legacy table still has global UNIQUE(apple_id) / UNIQUE(spotify_id),
    # rebuild to the composite-unique multi-user shape.
    if _needs_rebuild(conn) or _has_global_track_uniqueness(conn):
        _rebuild_tracks(conn)
        # rebuild preserves rows; reapply default user assignment in case
        # the rebuild path was taken before the UPDATE (safe idempotent).
        conn.execute(
            "UPDATE tracks SET user_id = ? WHERE user_id IS NULL",
            (default_uid,),
        )
        conn.commit()

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
