CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name          TEXT NOT NULL UNIQUE,
    pin_hash              TEXT,
    spotify_user_id       TEXT,
    spotify_display_name  TEXT,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    token         TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS tracks (
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

    -- legacy / core scalars
    tempo         REAL,
    energy        REAL,
    brightness    REAL,
    zcr           REAL,
    mfcc_json     TEXT,

    -- extended librosa scalars
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

    -- extended librosa vectors
    chroma_mean_json    TEXT,

    -- derived multi-axis scores
    activation          REAL,
    valence             REAL,
    activation_relative REAL,

    vibe_score    REAL NOT NULL,
    mood          TEXT,

    audio_path    TEXT,
    classification_source TEXT,

    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- (spotify_id, apple_id) are unique per-user, not globally, so two
    -- users can independently ingest the same track without collision.
    UNIQUE (user_id, spotify_id),
    UNIQUE (user_id, apple_id)
);

CREATE INDEX IF NOT EXISTS idx_tracks_vibe ON tracks(vibe_score);
CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood);
CREATE INDEX IF NOT EXISTS idx_tracks_apple_id ON tracks(apple_id);
CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id);
-- Indexes on columns added by later migrations (user_id, activation,
-- activation_relative) are created inside db.py::_migrate() after the
-- ADD COLUMN statements run — executescript here would try to index
-- columns that don't yet exist on legacy DBs.
