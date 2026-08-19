CREATE TABLE IF NOT EXISTS tracks (
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

    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracks_vibe ON tracks(vibe_score);
CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood);
CREATE INDEX IF NOT EXISTS idx_tracks_apple_id ON tracks(apple_id);
CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id);
-- idx_tracks_activation and idx_tracks_activation_rel are created by
-- db.py::_migrate() after the ADD COLUMN migrations run, since executescript
-- here would try to index columns that don't exist yet on legacy DBs.
