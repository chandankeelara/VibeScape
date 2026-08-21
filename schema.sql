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
    spotify_id    TEXT,
    apple_id      INTEGER,
    isrc          TEXT,
    title         TEXT NOT NULL,
    artist        TEXT NOT NULL,
    album         TEXT,
    genre         TEXT,
    duration_ms   INTEGER,
    artwork_url   TEXT,
    preview_url   TEXT,
    track_view_url TEXT,

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

    vibe_score    REAL,
    mood          TEXT,

    audio_path    TEXT,
    classification_source TEXT,

    youtube_id         TEXT,
    youtube_queried_at TIMESTAMP,

    -- future ML model outputs (nullable, filled by ml/ pipeline)
    energy_pred      REAL,
    danceability_pred REAL,
    valence_pred     REAL,
    vibe_score_ml    REAL,
    model_version    TEXT,

    features_extracted_at TIMESTAMP,
    ml_predicted_at       TIMESTAMP,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id) WHERE spotify_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_apple_id   ON tracks(apple_id)   WHERE apple_id   IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tracks_vibe ON tracks(vibe_score);
CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood);
CREATE INDEX IF NOT EXISTS idx_tracks_activation     ON tracks(activation);
CREATE INDEX IF NOT EXISTS idx_tracks_activation_rel ON tracks(activation_relative);

CREATE TABLE IF NOT EXISTS user_tracks (
    user_id     INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source      TEXT,
    play_count  INTEGER DEFAULT 0,
    last_played TIMESTAMP,
    PRIMARY KEY (user_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_user_tracks_user  ON user_tracks(user_id);
CREATE INDEX IF NOT EXISTS idx_user_tracks_track ON user_tracks(track_id);
