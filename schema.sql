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


-- ---------------------------------------------------------------------------
-- track_embeddings — one row per track with both embedding variants inline.
--
-- Columns use libSQL typed vector affinities (F32_BLOB(dim)) so Turso can
-- build an ANN index over fused_embedding via libsql_vector_idx(). On
-- vanilla SQLite the types collapse to BLOB affinity — behaviour is
-- identical for the numpy path (np.frombuffer(row['...'], dtype=float32)).
--
-- mert_embedding:  768-D  raw MERT-v1-95M mean-pooled last_hidden_state
--                         over 30 s of audio.
-- fused_embedding: 788-D  concat of (0.55·L2(MERT768) ⊕ 0.25·L2(9 scalars)
--                         ⊕ 0.20·language_one_hot_11), then L2-normalized.
--                         This is what DJ mode ranks by.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS track_embeddings (
    track_id         INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    mert_embedding   F32_BLOB(768),
    fused_embedding  F32_BLOB(788),
    model_version    TEXT,          -- provenance: which regressor produced the scalars
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ANN index for DJ mode's cosine similarity queries. libSQL only — a no-op
-- syntax error on vanilla SQLite (guarded by IF NOT EXISTS + being wrapped
-- in a try/except at bootstrap on the sqlite backend).
-- CREATE INDEX IF NOT EXISTS track_embeddings_fused_ann
--     ON track_embeddings(libsql_vector_idx(fused_embedding));


-- ---------------------------------------------------------------------------
-- track_events — append-only listening telemetry. One row per client event.
--
-- NO UPDATES, NO DELETES, EVER. This table is the source of truth for
-- per-user listening behaviour and the archaeology layer: questions nobody
-- has thought of yet get answered from here. user_track_stats below is a
-- materialised cache of exactly these rows and nothing else, and can always
-- be rebuilt from them (scripts/rebuild_user_track_stats.py).
--
-- type:    'play_start' | 'play_end'            (validated server-side)
-- reason:  'completed' | 'skipped' | 'replaced' (play_end only; stored
--          verbatim after trimming, so an unexpected client vocabulary shows
--          up in the data instead of being silently nulled)
-- vibe:    slider position 0-100 at the moment of the event, NULL if absent
--          or out of range
-- vibe_source: 'user' | 'system' | NULL — WHO put that number on the slider.
--          In DJ mode the app moves the slider itself to follow the track it
--          just loaded (frontend-next/src/state/PlayerContext.jsx:178,
--          setVibeFromTrack, gated by syncVibe) as opposed to the user
--          dragging it (:150, setVibe / shiftVibe). Those mean opposite
--          things: one is stated preference, the other is the recommender's
--          own output echoed back. Unlabelled is NULL, never a guessed
--          default — a wrong label would train the recommender on itself.
-- dj_mode: 1 | 0 | NULL — was DJ mode active when the event happened.
-- client_ts: epoch ms from the client clock — untrusted, never used for
--          ordering. server_ts and id are the trustworthy ordering keys.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS track_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    type        TEXT    NOT NULL,
    reason      TEXT,
    position_ms INTEGER,
    duration_ms INTEGER,
    vibe        INTEGER,
    vibe_source TEXT,
    dj_mode     INTEGER,
    source      TEXT,
    client_ts   INTEGER,
    server_ts   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- "events for this user, recent first". server_ts has 1-second granularity,
-- so id (monotonic, insert order) is the tiebreaker and the real sort key.
CREATE INDEX IF NOT EXISTS idx_track_events_user_recent ON track_events(user_id, id DESC);
-- "events for this user and track" — the per-(user, track) aggregation scan.
CREATE INDEX IF NOT EXISTS idx_track_events_user_track  ON track_events(user_id, track_id, id DESC);


-- ---------------------------------------------------------------------------
-- user_track_stats — materialised per-(user, track) aggregate of track_events.
--
-- This is what /similar reads at query time; it cannot scan a raw event log
-- per candidate on every recommendation. Two rules make the duplication safe:
--
--   1. Every column here is a pure function of track_events. POST /api/events
--      is the ONLY writer, and it writes both in the same request. Nothing
--      else may write these columns — not the ingest path, not a script, not
--      a migration.
--   2. Because Turso has no real transactions (the event INSERT and this
--      upsert are two independent statements), a failure between them lets
--      this table drift. That is an annoyance, not data loss:
--      scripts/rebuild_user_track_stats.py recomputes every row from
--      track_events alone.
--
-- It lives here rather than on user_tracks on purpose: user_tracks is the
-- library-membership table written by the ingest path, and a DJ / autoplay /
-- search play of a catalogue track the user does not own must be countable
-- without fabricating a library membership for it.
--
-- THE u_ / s_ SPLIT IS LOAD-BEARING — DO NOT "SIMPLIFY" IT INTO ONE SET.
--   u_* = events whose vibe_source was 'user'   → PREFERENCE. What the person
--         actually reaches for, and at what slider position. This is the
--         signal /similar personalises with.
--   s_* = events whose vibe_source was 'system' → REWARD. DJ mode picked both
--         the vibe and the track; whether the user let it finish or killed it
--         early is a verdict on the recommender's own decision. This is how
--         DJ mode gets evaluated.
-- Averaging the two together trains the recommender on its own output. The
-- columns are deliberately prefixed so there is no unqualified `vibe_sum` to
-- grab by accident: a query has to state which population it means.
-- Events that arrived without a vibe_source label are counted ONLY in the
-- label-agnostic totals below, so u_* + s_* can legitimately be less than
-- play_count / end_count.
--
-- Counters are primitives; ratios are derived on read (per population):
--   completion_rate = u_complete_count / u_end_count
--   skip_rate       = u_skip_count     / u_end_count
--   mean_bail_ms    = u_skip_position_ms_sum / u_skip_count  (how early they bail)
--   mean_vibe       = u_vibe_sum / u_vibe_count    (the mood they reach for it in)
--   vibe_variance   = u_vibe_sum_sq / u_vibe_count - mean_vibe^2   (how tightly)
-- vibe_* samples come from play_start events only — one sample per play, so a
-- play_start + play_end pair does not count the same listen twice.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_track_stats (
    user_id                INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    track_id               INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,

    -- Label-agnostic volume. Safe to read without caring who set the slider:
    -- these answer "has this person played this, how often, for how long".
    play_count             INTEGER NOT NULL DEFAULT 0,  -- play_start events
    end_count              INTEGER NOT NULL DEFAULT 0,  -- play_end events, any reason
    total_played_ms        INTEGER NOT NULL DEFAULT 0,  -- sum(position_ms) over play_end
    dj_play_count          INTEGER NOT NULL DEFAULT 0,  -- play_start with dj_mode = 1

    -- PREFERENCE population (vibe_source = 'user').
    u_play_count           INTEGER NOT NULL DEFAULT 0,
    u_end_count            INTEGER NOT NULL DEFAULT 0,
    u_complete_count       INTEGER NOT NULL DEFAULT 0,
    u_skip_count           INTEGER NOT NULL DEFAULT 0,
    u_replace_count        INTEGER NOT NULL DEFAULT 0,
    u_skip_position_ms_sum INTEGER NOT NULL DEFAULT 0,
    u_vibe_count           INTEGER NOT NULL DEFAULT 0,
    u_vibe_sum             INTEGER NOT NULL DEFAULT 0,
    u_vibe_sum_sq          INTEGER NOT NULL DEFAULT 0,

    -- REWARD population (vibe_source = 'system'). Same arithmetic, different
    -- meaning: this is the DJ grading its own homework.
    s_play_count           INTEGER NOT NULL DEFAULT 0,
    s_end_count            INTEGER NOT NULL DEFAULT 0,
    s_complete_count       INTEGER NOT NULL DEFAULT 0,
    s_skip_count           INTEGER NOT NULL DEFAULT 0,
    s_replace_count        INTEGER NOT NULL DEFAULT 0,
    s_skip_position_ms_sum INTEGER NOT NULL DEFAULT 0,
    s_vibe_count           INTEGER NOT NULL DEFAULT 0,
    s_vibe_sum             INTEGER NOT NULL DEFAULT 0,
    s_vibe_sum_sq          INTEGER NOT NULL DEFAULT 0,

    first_played_at        TIMESTAMP,
    last_played            TIMESTAMP,
    last_skipped_at        TIMESTAMP,
    updated_at             TIMESTAMP,

    PRIMARY KEY (user_id, track_id)
);

-- "what has this user played lately" — recency is a first-class recommender
-- input and the PK cannot serve this ordering.
CREATE INDEX IF NOT EXISTS idx_user_track_stats_recent ON user_track_stats(user_id, last_played DESC);
