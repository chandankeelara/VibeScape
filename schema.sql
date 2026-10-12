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

    -- language. Read from title/artist/album by a Claude Code session, not
    -- detected from audio (Whisper was removed 2026-10-10 -- it mispredicted
    -- routinely on sung audio). language_confidence is 1.0 for an asserted
    -- tag, NULL when the verdict is "no language" (instrumental).
    -- language_top3_json is a Whisper leftover with no writer.
    -- See ingest_pipeline/README.md, section Tagging languages.
    language              TEXT,
    language_confidence   REAL,
    language_top3_json    TEXT,
    language_model_version TEXT,
    language_predicted_at  TIMESTAMP,

    features_extracted_at TIMESTAMP,
    ml_predicted_at       TIMESTAMP,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Two-phase ingestion. Sync (online) inserts metadata with status='pending',
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
-- type:    'play_start' | 'play_end' | 'pause' | 'resume' | 'seek' |
--          'queue_add'  (validated server-side). Only play_start and play_end
--          feed user_track_stats. The rest are behaviour inside or around a
--          play. App-level events with no track go to user_events below.
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
    server_ts   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Telemetry v2 (2026-10-10). NULL on every older row.
    -- listened_ms: play_end only. Audible time, counted only while playing,
    --   so pauses and seek jumps do not inflate it (position_ms does).
    -- end_trigger: play_end only (wire field 'trigger'). How it was ended -
    --   next_button, media_key,
    --   prev, search, pick, queue_jump, vibe_change.
    -- playback: spotify | preview | youtube - what actually played.
    -- session_id: one per app session. tz_offset_min: client UTC offset.
    -- data: JSON object for type-specific extras (seek from/to, queue via).
    listened_ms   INTEGER,
    end_trigger   TEXT,
    playback      TEXT,
    session_id    TEXT,
    tz_offset_min INTEGER,
    data          TEXT
);

-- "events for this user, recent first". server_ts has 1-second granularity,
-- so id (monotonic, insert order) is the tiebreaker and the real sort key.
CREATE INDEX IF NOT EXISTS idx_track_events_user_recent ON track_events(user_id, id DESC);
-- "events for this user and track" — the per-(user, track) aggregation scan.
CREATE INDEX IF NOT EXISTS idx_track_events_user_track  ON track_events(user_id, track_id, id DESC);

-- ---------------------------------------------------------------------------
-- user_events - append-only, app-level events that are not about one track.
-- Same envelope as track_events (session_id, tz_offset_min, vibe context,
-- client_ts, server_ts), so the two read as one timeline when joined on
-- session_id. Kept separate because track_events.track_id is NOT NULL and
-- widening that needs a table rebuild, which Turso cannot do atomically.
--
-- type: 'session_start' | 'session_end' | 'search' | 'vibe_change' |
--       'dj_toggle'.
-- data: JSON object of type-specific fields, for example
--   search       {query, results_library, results_spotify, outcome}
--   vibe_change  {from, to, via}
--   dj_toggle    {on}
--   session_*    {platform} / {duration_ms}
-- NO UPDATES, NO DELETES, same as track_events.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type          TEXT    NOT NULL,
    session_id    TEXT,
    tz_offset_min INTEGER,
    vibe          INTEGER,
    vibe_source   TEXT,
    dj_mode       INTEGER,
    data          TEXT,
    client_ts     INTEGER,
    server_ts     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_events_user_recent ON user_events(user_id, id DESC);


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
    -- sum over play_end of listened_ms, falling back to position_ms on rows
    -- sent before listened_ms existed. This is "time listened".
    total_listened_ms      INTEGER NOT NULL DEFAULT 0,
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
    -- last_played: timestamp of last QUALIFIED listen only
    -- (play_end with reason='completed' OR position_ms >= DJ_QUALIFIED_PLAY_MS,
    -- default 90s). A play_start or short skim never updates this. The DJ
    -- recency penalty reads this directly. For "last time user saw this
    -- track" (library UI), read MAX(last_played, last_skipped_at).
    last_played            TIMESTAMP,
    -- last_skipped_at: any play_end with reason='skipped', any depth.
    -- Cross-reference last_played above.
    last_skipped_at        TIMESTAMP,
    updated_at             TIMESTAMP,

    PRIMARY KEY (user_id, track_id)
);

-- "what has this user played lately" — recency is a first-class recommender
-- input and the PK cannot serve this ordering.
CREATE INDEX IF NOT EXISTS idx_user_track_stats_recent ON user_track_stats(user_id, last_played DESC);


-- ---------------------------------------------------------------------------
-- user_stats — one row per user, derived from track_events (play_start only).
--
-- Feeds the DJ recency re-ranker's per-user half-life: a heavy listener's
-- "played recently" means hours, a light listener's means weeks. Rebuildable
-- by scripts/rebuild_user_stats.py from track_events alone, so this is a
-- cache like user_track_stats and the single-writer rule is the same —
-- POST /api/events is the only writer. Updated only for type='play_start'
-- (we are modelling play cadence, not interaction cadence) and skips do not
-- advance last_play_at here even though they do in user_track_stats.last_played.
--
-- ema_interval_h is the exponentially-weighted mean of hours between
-- consecutive plays (0.1 new / 0.9 old). Nullable until the second play
-- lands. play_count is the cold-start gate: the re-ranker falls back to the
-- fixed DJ_RECENCY_HALFLIFE_H env default until play_count >= 5.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_stats (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plays_30d       INTEGER NOT NULL DEFAULT 0,
    ema_interval_h  REAL,
    play_count      INTEGER NOT NULL DEFAULT 0,
    last_play_at    TEXT,
    updated_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- user_embedding_mean — mean of unit embedding vectors over a user's
-- analysed library, per embedding variant. DJ replay centres every vector
-- on it (backend/dj_replay.py). user_id 0 is the mean over every analysed
-- track, used for libraries under 50 analysed tracks. mean_json is a JSON
-- array rather than a BLOB because Turso BLOB reads can come back empty.
-- A cache: _dj_library_mean rebuilds a row when the live track count
-- drifts more than 5 percent from n_tracks, so the table is safe to drop.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_embedding_mean (
    user_id        INTEGER NOT NULL,
    model_version  TEXT    NOT NULL,
    n_tracks       INTEGER NOT NULL,
    mean_json      TEXT    NOT NULL,
    computed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, model_version)
);
