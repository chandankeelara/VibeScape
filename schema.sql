-- Identity: one row per human. spotify_user_id is the canonical identity
-- key when the user signs in with Spotify; the special row with
-- display_name='Guest' is a shared demo profile for zero-friction "just
-- listen" access. PINs / local-only accounts were removed in the unified-
-- identity refactor -- Spotify is the identity provider from here on.
CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name          TEXT NOT NULL UNIQUE,
    email                 TEXT,     -- for native email/password sign-in
    password_hash         TEXT,     -- scrypt hash for email/password auth
    spotify_user_id       TEXT,
    spotify_display_name  TEXT,
    spotify_email         TEXT,
    spotify_country       TEXT,
    spotify_product       TEXT,     -- 'premium' | 'free' | 'open'
    spotify_avatar_url    TEXT,
    spotify_profile_url   TEXT,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at         TIMESTAMP
);

-- One VibeScape user per Spotify account. Partial index so the Guest
-- user (spotify_user_id NULL) doesn't collide.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_spotify_uid
    ON users(spotify_user_id) WHERE spotify_user_id IS NOT NULL;

-- One VibeScape user per email address (native email/password sign-in).
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
    ON users(email) WHERE email IS NOT NULL AND email != '';

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

    -- language detection (filled by Whisper via modal_app.predict_language_from_url)
    language              TEXT,
    language_confidence   REAL,
    language_top3_json    TEXT,
    language_model_version TEXT,
    language_predicted_at  TIMESTAMP,

    features_extracted_at TIMESTAMP,
    ml_predicted_at       TIMESTAMP,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Two-phase ingestion. Sync (online) inserts metadata with status='pending';
    -- scripts/run_ingest_worker.py (offline) does the preview cascade + ML
    -- scoring + language detection and flips to 'done' (or 'no_preview' /
    -- 'failed'). The library / mood-grid queries filter to 'done'.
    ingestion_status       TEXT DEFAULT 'pending',
    ingestion_error        TEXT,
    ingestion_attempted_at TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id) WHERE spotify_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_apple_id   ON tracks(apple_id)   WHERE apple_id   IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tracks_vibe ON tracks(vibe_score);
CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood);
CREATE INDEX IF NOT EXISTS idx_tracks_activation     ON tracks(activation);
CREATE INDEX IF NOT EXISTS idx_tracks_activation_rel ON tracks(activation_relative);
CREATE INDEX IF NOT EXISTS idx_tracks_language       ON tracks(language);
CREATE INDEX IF NOT EXISTS idx_tracks_ingestion_status ON tracks(ingestion_status);

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
