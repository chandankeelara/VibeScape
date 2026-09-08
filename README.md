<a id="readme-top"></a>

<div align="center">
  <h3 align="center">🎧 VibeScape — Audio-ML Music Player</h3>
  <p align="center">
    Fine-tuned <strong>MERT</strong> transformer regresses Spotify's <em>danceability / energy / valence</em> from raw audio, Whisper adds language detection, and a librosa feature bank supplies a fully-interpretable fallback. Predictions drive a two-axis mood grid the user can scrub through in the browser — and a MERT-embedding-based similarity search powers vibe-consistent autoplay.
    <br/><br/>
    <a href="https://vibescape-241988497106.us-central1.run.app"><strong>🌐 Live demo →</strong></a>
    <br/><br/>
    <a href="#getting-started">Quick Start</a>
    ·
    <a href="#ml-pipeline">ML Pipeline</a>
    ·
    <a href="#recommendation-system">Recommender</a>
    ·
    <a href="#architecture">Architecture</a>
  </p>
</div>

## 📋 Table of Contents

- [About](#about-the-project)
- [Why This Exists](#why-this-exists)
- [Ingestion Pipeline](#ingestion-pipeline)
  - [Two-Phase Split: Fast Metadata Sync + Offline v2 Pipeline](#two-phase-split-fast-metadata-sync--offline-v2-pipeline)
  - [The Six Stages](#the-six-stages)
  - [Per-Stage Status Columns + Promotion Cascade](#per-stage-status-columns--promotion-cascade)
  - [Preview Provider Chain](#preview-provider-chain)
  - [Local Audio Cache](#local-audio-cache)
  - [Orchestrator](#orchestrator)
- [ML Pipeline](#ml-pipeline)
  - [Model — MERT Regressor](#model--mert-regressor)
  - [Data & Splits](#data--splits)
  - [Training Recipe](#training-recipe)
  - [Whisper Language Head](#whisper-language-head)
  - [Librosa Feature Bank (Baseline)](#librosa-feature-bank-baseline)
  - [Vibe Scoring](#vibe-scoring)
- [Recommendation System](#recommendation-system)
  - [Fused Embedding + Cosine Similarity](#fused-embedding--cosine-similarity)
  - [DJ Mode — Session-Weighted Recommendations](#dj-mode--session-weighted-recommendations)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Training Your Own Model](#training-your-own-model)
- [Deployment](#deployment)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)
- [Contact](#contact)

## About The Project

**VibeScape** is a personal music library that classifies every track along two perceptual axes — **activation** (how energetic it feels) and **valence** (how bright/happy it feels) — then lets you scrub a slider across the resulting mood grid to build vibe-consistent queues.

The interesting part is what's under the hood: audio understanding is done by a **fine-tuned MERT transformer** running on a remote GPU, with **Whisper** handling sung/spoken language detection and a hand-engineered **librosa** feature bank as an interpretable baseline / fallback. A single dispatcher (`ingest/ml_backend.py`) routes each track through Modal (prod), a local GPU (dev), or the librosa path (fully offline).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Why This Exists

Spotify used to expose an `/audio-features` endpoint with `danceability`, `energy`, and `valence` — the exact fields that made mood-based recommendations possible. Spotify deprecated the endpoint for third-party apps in late 2024.

VibeScape reproduces those signals **from the raw 30-second preview clip** using self-supervised music representation learning, so the app keeps working without depending on a private API.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Ingestion Pipeline

Ingest is split into a **fast online metadata pass** (runs inside the FastAPI request that a user's Spotify sync fires) and an **offline v2 pipeline of six modular stages** (a separate worker process that drains queued work). The split means the sync-modal returns in seconds — the user's library becomes visible immediately with metadata + previously-cached audio — while the heavy per-track work (audio download, MERT inference, embeddings, language, YouTube resolution) happens asynchronously with no bearing on request latency.

### Two-Phase Split: Fast Metadata Sync + Offline v2 Pipeline

**Phase 1 — online, synchronous (`backend/app.py`).**
`_process_track()` writes a metadata-only `tracks` row for anything genuinely new, with `ingestion_status='pending'` and six per-stage status columns each set to `'pending'`. Three short-circuit outcomes:

| Condition | Bucket | Cost |
|---|---|---|
| `user_tracks` row already exists | `already_in_library` | 1 SELECT |
| `tracks` row exists globally, user not linked | `added_to_library` — reuse row, add `user_tracks` link | 1 SELECT + 1 INSERT |
| Brand new to the DB | `queued_for_analysis` — insert metadata + `user_tracks` link | 2 INSERTs |

A 500-track playlist re-sync where every track is already known completes in a few hundred milliseconds — no HTTP fetches beyond the Spotify pagination, no inference, no audio.

**Phase 2 — offline, batch (`ingest_pipeline/` + `scripts/run_ingest_v2.py`).**
A background worker walks the six stages of the v2 pipeline in waves. Each pass:
1. Fetches all pending rows for stage 1, dispatches them concurrently (I/O-bound → thread pool).
2. Advances to stage 2, same pattern.
3. ... through all six stages.
4. Runs `promote.py` to derive `ingestion_status` and cascade any terminal failures.

Songs move through **stages in waves, not one-by-one across stages** — the whole batch clears preview before any of it starts classify. Simpler orchestration than a per-track state machine, and each stage sees a hot working set.

### The Six Stages

Each stage lives in its own module under `ingest_pipeline/`, is gated by exactly one status column on `tracks`, and writes only its own domain columns + its own status column.

| # | Stage | File | Status column | Blocks on | Concurrency | What it does |
|---|---|---|---|---|---|---|
| 1 | **preview** | `stage_preview.py` | `preview_status` | — | 2 workers | Runs the provider chain to resolve a `preview_url`. Also backfills `apple_id`, `genre`, `track_view_url`, `album`, `artwork_url`, `duration_ms` from the provider hit (only where the row lacks them). Sets `preview_source` to `spotify` / `itunes` / `deezer_isrc` / `deezer_search`. |
| 2 | **download** | `stage_download.py` | `download_status` | `preview_status='done'` | 8 workers | Fetches `preview_url` and writes to `data/audio/<spotify_id>.<ext>` atomically (`.part` rename). Sets `audio_path`. |
| 3 | **classify** | `stage_classify.py` | `ml_status` | `download_status='done'` | 1 (GPU) | Runs `MERTVibeRegressor` (10 s crop) on the cached audio via `ml_backend.predict_from_path`. Writes `energy_pred`, `danceability_pred`, `valence_pred`, `vibe_score_ml`, `activation`, `valence`, `vibe_score`, `mood`, `classification_source='ml_mert'`. |
| 4 | **youtube** | `stage_youtube.py` | `youtube_status` | — (independent) | 6 workers | `yt-dlp ytsearch1` for `"{title} {artist}"`. Takes the first hit, no embed / age / availability check. Writes `youtube_id`, `youtube_queried_at`. |
| 5 | **language** | `stage_language.py` | `language_status` | `download_status='done'` | 1 (GPU) | Whisper `small` language detection on the cached audio. Writes `language`, `language_confidence`, `language_top3_json`, `language_model_version` when top-1 confidence ≥ 0.20; otherwise sets `language_status='no_match'`. |
| 6 | **embedding** | `stage_embedding.py` | `embedding_status` | `download_status='done'` AND `ml_status='done'` | 1 (GPU) | Runs raw MERT-v1-95M encoder (30 s window) on the cached audio → mean-pooled 768-D vector. Writes `mert_v1_95m_fp32_30s` blob to `track_embeddings`. Piggybacks librosa `tempo` / `brightness` / `acousticness` from the same waveform and writes them to `tracks`. Builds the fused vector using those scalars + `language` and writes `fused_v1_mert_scalar_lang` (788-D) to `track_embeddings`. |

Constraint-collision retry: an UPDATE that hits the legacy `UNIQUE (user_id, apple_id)` index retries once with `apple_id / track_view_url / genre` stripped. The row's own status column always lands so the pipeline never loops on the same row.

### Per-Stage Status Columns + Promotion Cascade

Each stage-status column takes one of four values: `pending`, `done`, `no_match`, `failed`. `no_match` is terminal but non-error (e.g. iTunes had no hit; audio decoded but Whisper confidence was too low). `failed` is retryable next pass.

`ingest_pipeline/promote.py` derives the aggregate `ingestion_status`:

```
ingestion_status = 'done'        when preview + download + ml all 'done'
ingestion_status = 'no_preview'  when preview_status='no_match'
                                 OR download_status='no_match'
```

`youtube_status` and `language_status` are best-effort — never block promotion.

Promote also **cascades** audio-availability failures downstream so pending counts stay meaningful:

- `preview_status='no_match'` → `download_status='no_match'`
- `download_status='no_match'` → `ml_status`, `language_status`, `embedding_status` all → `'no_match'`

Without the cascade, rows would sit `pending` forever waiting on audio that will never arrive.

### Preview Provider Chain

`ingest_pipeline/preview_providers.py` defines `PreviewProvider` as an ABC and ships four implementations: `SpotifyPreview`, `ItunesPreview`, `DeezerIsrcPreview`, `DeezerSearchPreview`. `PreviewChain.resolve(track)` walks providers in order and returns the first `PreviewHit`.

The **default chain is Spotify-then-iTunes-only** — Deezer providers are wired but kept out of `default_chain()` because Deezer's signed URLs (`?hdnea=exp=<unix-ts>`) expire on a ~14-day rolling window. Using them in an offline worker guarantees a fraction of tracks will have dead URLs by download time.

**iTunes rate-limit handling:** iTunes' Search API 403s bursts around ~20 req/min per IP. `ItunesPreview` serializes behind a class-level lock with a 500 ms inter-request gap and retries 403s with exponential backoff (2 s → 4 s → 8 s → 16 s + jitter, 4 retries max). `PreviewStage.max_workers=2` — more workers here would just spin on the lock without gaining throughput.

Adding a new provider = write a `PreviewProvider` subclass + prepend it to `default_chain()`.

### Local Audio Cache

`DownloadStage` writes every preview to `data/audio/<spotify_id>.<ext>` (`.m4a` for iTunes AAC, `.mp3` for MP3 sources). `resolve_audio_path()` (exported from `stage_download.py`) is the single source of truth for cache lookups; every downstream stage prefers the local file. This means:

- **One preview download per song, ever** — not per stage per pass.
- **Cache survives crashes / re-runs** — the backfill migration marks pre-existing files (779 tracks in local dev) as `download_status='done'`.
- **Strict cache path** — classify / language / embedding all `SELECT ... WHERE download_status='done' AND audio_path IS NOT NULL`. There is **no URL fallback** — if audio isn't cached, promote cascades the row to `no_match` and downstream stages skip it.

The app never streams from `audio_path` — the frontend streams directly from `preview_url` (CDN) and the backend's `/api/stream/*` is only a fallback. The local cache is pipeline-internal state.

### Orchestrator

```bash
# One pass across all six stages, up to 50 rows per stage per pass:
python scripts/run_ingest_v2.py --batch 50

# Loop forever with 30 s idle sleep between empty passes:
python scripts/run_ingest_v2.py --loop --batch 30 --interval 30

# Restrict to a subset of stages:
python scripts/run_ingest_v2.py --stages preview,download,classify
```

The orchestrator (`scripts/run_ingest_v2.py`) is a thin loop over `Stage.run_batch()` calls followed by `promote()`. Stages are stateless — swap in Modal-backed classify/language stages later by changing the `ml_backend` mode without touching orchestration.

Because each stage is isolated, later scaling is trivial: one worker per stage, or one worker per shard of rows, or a task queue in front of any subset.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## ML Pipeline

### Model — MERT Regressor

**Backbone**: [`m-a-p/MERT-v1-95M`](https://huggingface.co/m-a-p/MERT-v1-95M) — a HuBERT-style self-supervised encoder pre-trained on ~160k hours of music, 95M parameters, 24 kHz input, 768-dim hidden states.

**Head**: mean-pool + max-pool the last hidden state along time, concat to a 1536-d vector, then **three independent regression heads** (one per target). Each head is `LayerNorm → Linear(1536→256) → GELU → Dropout(0.2) → Linear(256→1) → sigmoid`.

```
     ┌──────────────────────────────────────┐
     │ raw audio, 24 kHz mono, 10 s crop     │
     └──────────────────┬───────────────────┘
                        │
              Wav2Vec2FeatureExtractor
                        │
     ┌──────────────────▼───────────────────┐
     │  MERT-v1-95M  (12 transformer layers) │
     └──────────────────┬───────────────────┘
                        │ [B, T, 768]
              mean-pool ⨁ max-pool
                        │ [B, 1536]
           ┌────────────┼────────────┐
           ▼            ▼            ▼
      ┌───────┐    ┌───────┐    ┌───────┐
      │ dance │    │ energy│    │valence│
      │ head  │    │ head  │    │ head  │
      └───┬───┘    └───┬───┘    └───┬───┘
     σ(·) │      σ(·) │      σ(·) │
          ▼           ▼           ▼
        [0, 1]      [0, 1]      [0, 1]
```

See `ml/src/model.py` for the `MERTVibeRegressor` LightningModule.

### Data & Splits

- **Labels**: Spotify audio-features CSV (`ml/data/spotify_tracks.csv`) — public Kaggle-style dumps still contain the deprecated fields.
- **Audio**: 30-second `.mp3` previews downloaded via `ml/src/download_previews.py` and validated against a manifest (`status == "ok"` and file ≥ 10 kB survives).
- **Splits**: `GroupShuffleSplit` grouped on `artists` so **no artist crosses train/val/test**. Two nested splits (train+val vs. test, then train vs. val) enforce artist disjointness across all three sets.
- **Crop**: random 10 s window at train, centre 10 s at val/test. Peak-normalized to prevent clipping, augmented with ±3 dB random gain.

### Training Recipe

| Knob | Value | Rationale |
|---|---|---|
| Pre-trained backbone | `m-a-p/MERT-v1-95M` | Music-domain SSL beats generic wav2vec for MIR tasks |
| Freeze schedule | encoder frozen epoch 0, unfrozen from epoch 1 | Warm up heads on random init before touching encoder |
| Optimizer | AdamW, two param groups | Encoder LR = 1e-5, head LR = 1e-4 |
| LR schedule | Linear warmup (500 steps) → cosine decay | Standard transformer fine-tune curve |
| Precision | `16-mixed` | Fits ~4× more batch on T4 / consumer GPU |
| Batch × Accum | 4 × 8 = **32 effective** | Small physical batch, real batch via accumulation |
| Loss | Per-head MSE, summed | Three independent [0,1] regressions |
| Early stopping | `val_loss`, patience 3 | |
| Grad clip | 1.0 | |
| Tracking | MLflow (`ml/experiments/mlruns`) | Loss curves, LR, per-target MSE all logged |

Reproducibility: `seed=42`, `deterministic=True`, split RNG seeded independently. See `ml/configs/default.yaml` for the full recipe and `ml/configs/smoke.yaml` for a fast-dev-run config.

### Whisper Language Head

Every preview also passes through **OpenAI Whisper** (small by default — configurable up to `large-v3`) using only the **language-detection head** on the first 30 s of audio. No transcription, just the softmax over Whisper's 99 language IDs.

Predictions are bucketed by top-1 probability:

| Bucket | Threshold | Meaning |
|---|---|---|
| `confident` | `p ≥ 0.5` | Written to DB |
| `uncertain` | `0.2 ≤ p < 0.5` | Written to DB with lower confidence |
| `unknown` | `p < 0.2` | Likely instrumental / non-speech; DB stays `NULL` |
| `failed` | — | Load/model error; resume-safe re-try via `--retry-failed` |

The manifest at `ml/data/language_manifest.csv` is append-only and compacted on every run, so batch jobs are Ctrl-C safe.

### Librosa Feature Bank (Baseline)

Before MERT was trained, the system ran on ~15 hand-engineered features from `ingest/features.py`. This path is still the **fallback when Modal/local inference is unavailable**, and it drives an interpretable second opinion:

- **Rhythm**: tempo (beat-track), tempo stability (PLP inverse-std), onset rate
- **Energy**: RMS mean/std, HPSS harmonic-percussive split → `acousticness = h_energy / (h + p)`
- **Spectral**: centroid (brightness), bandwidth, rolloff, contrast, flatness, ZCR
- **Timbre**: 13-dim MFCC mean + std (timbre variability)
- **Tonal**: 12-dim CENS chroma, tonnetz std
- **Mode / valence**: **Krumhansl-Kessler major/minor template correlation**. Correlate the mean chroma vector against all 12 rotations of the KK major and minor profiles; report `max_major_corr − max_minor_corr` clipped to [−1, +1] as `valence_mode`. Positive → brighter/major-key, negative → darker/minor-key.

### Vibe Scoring

Both feature paths converge on a two-axis representation:

- **activation** ∈ [0, 100]: `0.30·energy + 0.25·tempo + 0.20·dance + 0.10·onset + 0.10·brightness + 0.05·dynamic_range`
- **valence** ∈ [0, 100]: `0.50·mode + 0.20·(1−flatness) + 0.15·contrast + 0.15·(1 − 0.5·acousticness)`
- **`activation_relative`**: library-wide z-score of activation, so the frontend slider gives a well-distributed percentile view instead of clumping in the middle.

A 2×5 **mood grid** is derived from these two axes:

|  | valence < 50 | valence ≥ 50 |
|---|---|---|
| activation < 20 | sleep | sleep |
| 20–40 | melancholy | chill |
| 40–60 | moody | steady |
| 60–80 | aggressive | hype |
| ≥ 80 | beast | beast |

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Recommendation System

Vibe-consistent autoplay picks the next track from your library using a **fused acoustic + emotional embedding**, ranked by cosine similarity. Two modes:

- **Vibe mode** (`GET /api/tracks/{id}/similar`) — anchored on the currently-playing seed, ranked by weighted L1 distance across the four ML feature dimensions + mood bonus. Returns the closest N tracks in the library.
- **DJ mode** (`POST /api/tracks/{seed}/similar`) — anchored on the user's **rolling session** rather than a single seed. The query vector is built from the user's last few playback events (completions, queue-adds, skips) weighted by action type. See [DJ Mode](#dj-mode--session-weighted-recommendations) below.

### Fused Embedding + Cosine Similarity

Every track with a downloadable preview gets a **768-D raw MERT embedding** (mean-pool of `last_hidden_state` over 30 s of audio) and a **788-D fused embedding** that folds in the ML scalar predictions + a language one-hot. Both live in a dedicated `track_embeddings` table — **one row per track**, each variant in its own typed vector column, so the DB can build an ANN index over the fused column directly:

```sql
CREATE TABLE track_embeddings (
  track_id         INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
  mert_embedding   F32_BLOB(768),   -- raw MERT, DJ-independent
  fused_embedding  F32_BLOB(788),   -- MERT + scalars + language, what DJ ranks
  model_version    TEXT,
  updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

On libSQL / Turso the `F32_BLOB(dim)` type is a typed vector affinity. On vanilla SQLite it collapses to plain BLOB affinity — physical bytes are identical, so the numpy code path (`np.frombuffer(row['fused_embedding'], dtype=np.float32)`) works on both. Only the DB-native `vector_distance_cos()` / `vector_top_k()` functions differ.

The fused vector is a weighted concat:

```
      768-D MERT              9-D scalars           11-D language one-hot
    ┌────────────────┐    ┌────────────────┐    ┌──────────────────┐
    │ raw MERT       │    │ energy_pred    │    │ en / kn / te /   │
    │ mean-pool of   │    │ dance_pred     │    │ hi / pa / sa /   │
    │ last_hidden    │    │ valence_pred   │    │ ta / ur / km /   │
    │ over 30 s      │    │ vibe_score     │    │ pt / other       │
    │                │    │ activation     │    │                  │
    │                │    │ valence        │    │                  │
    │                │    │ acousticness   │    │                  │
    │                │    │ tempo          │    │                  │
    │                │    │ brightness     │    │                  │
    └───────┬────────┘    └────────┬───────┘    └────────┬─────────┘
        L2-norm                 L2-norm            hard one-hot
        × 0.55                  × 0.25              × 0.20
              └────────────────┬──────────────────────┘
                        concat + L2-norm
                               │
                               ▼
                     fused ∈ ℝ^788
```

**Ranking backend** — depends on `DB_BACKEND`:

- **Turso (prod).** The cosine ranking runs **server-side** via `vector_distance_cos(fused_embedding, vector32(?))` — the query vector serializes as a JSON-array string, Turso computes distances against every candidate in a full-scan `ORDER BY distance ASC LIMIT K` and streams back only the top-K + track metadata. Payload per DJ request: **~10 KB** (top-K rows) instead of **~4.7 MB** (every candidate's raw embedding streamed to Python) — a **~500× reduction in Turso→Cloud-Run egress**, which is what makes DJ mode viable on the 3 GB / month Turso free tier.
- **Local sqlite (dev).** Vanilla SQLite has no vector functions, so `_load_mert_vecs_bulk` still pulls all candidate embeddings and cosine happens in numpy. Fast at library sizes < 100 K (< 5 ms for the whole scan).

The dispatch is a one-line check on `os.environ["DB_BACKEND"]` in `_similar_dj`; the numpy path is preserved as the local-dev + graceful-degradation fallback if a Turso call ever fails.

### Why the fusion, not MERT alone?

Measured top-1 agreement between MERT-only and MERT+scalars across all 739 embedded tracks:

| Depth | Avg overlap | Exact match |
|---|---|---|
| top-1 | 79.2% (585/739) | 585 seeds |
| top-3 | 81.0% (2.43/3) | 353 seeds |
| top-5 | 82.4% (4.12/5) | 236 seeds |
| top-10 | 84.8% (8.48/10) | 100 seeds |

MERT alone captures ~80% of the correct nearest neighbours. The remaining 20% is where the two methods disagree — almost entirely at emotional extremes. MERT treats *"loud + energetic + happy"* and *"loud + energetic + angry"* as acoustically adjacent because they share texture; the scalar slice's valence dimension disambiguates them.

### Why the full 30 s embedding, not the 10 s crop?

The trained regressor uses a centre-cropped 10 s window because that was the training compute budget. For similarity search we want the *whole song's* acoustic fingerprint. We measured the impact across all 739 embedded tracks:

- Correlation `centre-10s vibe` vs `full-30s vibe`: **0.960**
- Mean absolute vibe delta: **3.15 pts** on a 0-100 scale
- Systematic bias: full-30s scores **1.83 pts lower** on average

Ranking is largely preserved, but the ~16% of tracks with lopsided intros/outros can differ by 5-23 vibe points. The 30 s window is more representative for embedding purposes; the regressor's 10 s crop is left intact for the scalar-prediction path so downstream mood-grid coordinates stay stable. The two paths converge on the same track but "see" slightly different windows — captured explicitly via `model_version = 'mert_v1'` (10s scalar prediction) vs `model_version = 'mert_v1_30s'` (30s rescoring) so the split is queryable.

### Populating embeddings

Embeddings are produced inline by the v2 pipeline's [`EmbeddingStage`](#the-six-stages) — no separate backfill needed. That stage reads the local cached audio (from `DownloadStage`), runs raw MERT once, piggybacks a `librosa` pass on the same waveform to fill `acousticness / tempo / brightness`, then builds the fused vector via `_build_fused(row_dict, mert_vec)` and writes both blobs in one `INSERT ... ON CONFLICT(track_id) DO UPDATE ...`.

Two ops helpers exist for offline maintenance:

- **`scripts/_backfill_mert_embeddings.py`** — rebuild MERT vectors from local audio files. Useful when you've locally re-processed the audio (e.g. bumped `MAX_DURATION_S`) and want to regenerate everything without going through the full pipeline.
- **`scripts/_refuse_embeddings.py`** — rebuild only the *fused* vector for every track from its existing MERT + current scalar columns + current language. No audio, no GPU — pure numpy over blobs we already have. Runs against local and Turso in one invocation. This is what you run after a language-tag correction sweep, since the fused vector has a 20 % language component that goes stale when `tracks.language` changes.

### Query performance

**Turso path (prod).** `vector_distance_cos()` full-scan over 1489 candidates: **~300 ms** per DJ request end-to-end (includes HTTP round-trip). Payload: ~10 KB. At ~50 K embeddings we'd hit ~10 s per request and want the DiskANN index — a `CREATE INDEX ... libsql_vector_idx(fused_embedding)` swap plus a `vector_top_k()` rewrite. Turso's ANN was flaky on our instance at build time so we defer this until the index cooperates or scale demands it.

**Numpy path (local dev).** ~5 ms per request at library size 1489. Pure in-process cosine, no round-trip, no serialization.

**Egress economics** (Turso free tier is 3 GB/month):

| Backend | Bytes per DJ request | Free-tier ceiling |
|---|---:|---:|
| Old numpy path (every candidate blob streamed to app) | ~4.7 MB | ~640 requests/mo |
| Current Turso `vector_distance_cos` (top-K + metadata) | ~10 KB | ~300 K requests/mo |

### DJ Mode — Session-Weighted Recommendations

DJ mode replaces the single-seed similarity query with a **taste-vector query** built from the user's rolling session. The query vector is a weighted sum of the fused embeddings of tracks the user has interacted with recently.

**Client-side taste buffer** (`frontend/app.js`, key `vibescape.sessionEvents` in localStorage):
- Ring buffer of the **last 10 playback events** — each entry `{track_id, action, played_ratio, ts}`.
- Populated by `djPushEvent()` on every queue-add, natural end, or transition. Actions: `completed`, `queued`, `next`, `skipped`.
- Cleared on logout, survives page reload.

**Event → weight table** (`djBuildWeights`, `frontend/app.js`):

| action | condition | weight | pile |
|---|---|---:|---|
| `queued` | user explicitly added to queue | **+1.2** | positive |
| `completed` | natural end or `played_ratio ≥ 0.85` | **+0.8** | positive |
| `next` | manual skip, `played_ratio > 0.5` | +0.3 | positive |
| `next` | manual skip, `played_ratio ≤ 0.5` | 0 | ignored |
| `skipped` | manual skip, `played_ratio < 0.15` | **−0.8** | negative |
| `skipped` | manual skip, `0.15 ≤ played_ratio < 0.45` | −0.4 | negative |
| `skipped` | manual skip, `0.45 ≤ played_ratio < 0.85` | 0 | ignored (ambiguous) |

Queued weight > completed weight because a queue-add is an *explicit, forward-looking* choice, while a completion can be passive (music continued playing in a background tab, user didn't bother to skip).

**No age decay.** Every event in the 10-slot buffer counts at full base weight. The buffer *is* the recency window — older events roll off naturally as new ones come in. Decay was tried and dropped: at buffer size 10 it just penalized the median-age event by ~40% for no useful discrimination.

**Query vector construction** (backend `_similar_dj`, `backend/app.py`):

```
pos_vec   = Σ (w_i · L2_normalize(fused_i))      for positive events
neg_vec   = Σ (w_i · L2_normalize(fused_i))      for negative events
query_vec = L2_normalize(pos_vec − 0.4 · neg_vec)
```

Each contributing track is L2-normalized *before* weighting, and the final query vector is L2-normalized again. This means **magnitude is irrelevant** — only the direction of the accumulated taste matters. Piling on more `completed` events from the same vibe pulls the query toward the centroid of the liked cluster (tighter recs), not out of the cluster.

Cosine similarity is computed by whichever backend `DB_BACKEND` points at — Turso via `vector_distance_cos()` in prod, numpy fallback in dev (see [Query performance](#query-performance)). Response includes a `mode_used` field that ends in `_turso` when the fast path served the request, so client-side diagnostics can distinguish which backend answered.

**Exclusion list** (`djExcludeIds`, `frontend/app.js`):
- All tracks currently in the queue.
- **Last 50 played tracks** (`state.recent`, in-memory, capped at `RECENT_MAX=50`, cleared on logout).
- The URL seed itself.

50 tracks ≈ 2.5-3 hours of listening — long enough to prevent obvious repeats, short enough that the user can hear a track again later in a long session.

**Cold start.** When the taste buffer is empty (fresh session) or contains only the seed track itself, the query vector is undefined. Backend falls back to random selection from the candidate pool with `score=0`. Once one non-seed positive event lands, DJ mode engages properly.

**Fallback to vibe mode.** If the seed track has no MERT embedding in `track_embeddings` (recently-ingested tracks that haven't finished the embedding stage yet), the endpoint returns vibe-mode results with `mode_used='vibe_fallback_no_seed_embedding'`. The frontend can then choose whether to surface the fallback or wait for embeddings.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         BROWSER (frontend/)                      │
│  • two-axis mood slider          • YouTube playback              │
│  • Media Session API (Bluetooth) • library filter                │
└────────────────────────────┬────────────────────────────────────┘
                             │  REST / JSON
┌────────────────────────────▼────────────────────────────────────┐
│                   FastAPI backend (backend/app.py)               │
│  • Spotify OAuth + library sync   • track store (SQLite)         │
│  • ingest hot path                • YouTube ID resolution        │
└────┬──────────────────────────────────────────────────┬─────────┘
     │ preview_url                                       │ audio-features
     │                                                   │
     ▼                                                   ▼
┌───────────────────────┐                    ┌─────────────────────┐
│  ml_backend dispatcher│                    │  Spotify Web API     │
│  VIBESCAPE_ML_MODE=   │                    │  (metadata only —    │
│    auto | modal |     │                    │   /audio-features    │
│    local | none       │                    │   is gone)           │
└──────┬──────────┬─────┘                    └─────────────────────┘
       │          │
   Modal        Local
   (prod)       (dev / GPU box)
       │          │
       ▼          ▼
  ┌─────────────────────┐          ┌─────────────────────────────┐
  │  MERT-v1-95M ckpt   │          │  Whisper (small/med/large)  │
  │  (~380 MB, T4 GPU)  │          │  (language detection head)  │
  │  ml/models/*.ckpt   │          │                             │
  └──────────┬──────────┘          └──────────────┬──────────────┘
             │                                     │
             └─────────────┬───────────────────────┘
                           │
                           ▼
        {danceability, energy, valence, vibe_score,
         top1_lang, top1_prob, model_version}
                           │
                           ▼
         Turso / libSQL (prod) · SQLite (local dev)
                  — schema.sql, tracks table
```

**Two-tier deploy**: the playback API runs as a **512 MB Google Cloud Run** container with only FastAPI + requests + Modal client (no torch, no librosa, no ffmpeg beyond ingest). All heavy ML happens on **Modal T4 GPUs** with warm-container reuse (`scaledown_window=300`) and persistent volumes for the MERT and Whisper weight caches. Cold sync of a 1000-track playlist is dominated by preview download, not inference.

Because Cloud Run's filesystem is ephemeral, persistence moves off-box to **Turso** (hosted libSQL). `backend/db_client.py` implements a `sqlite3`-compatible connection/cursor/row shim over Turso's raw Hrana HTTP pipeline, so every call site keeps its plain `sqlite3` API and `DB_BACKEND=sqlite|turso` switches the whole app between local file and remote database.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Getting Started

### Prerequisites

- **Python 3.13**
- **ffmpeg** on PATH (or bundled via `imageio-ffmpeg`, already in requirements)
- A Spotify developer app (Client ID + Secret) — free at [developer.spotify.com](https://developer.spotify.com/)
- **Optional**: NVIDIA GPU + CUDA if you want to train or run inference locally
- **Optional**: [Modal](https://modal.com/) account for remote GPU inference (free tier is enough)

### Install

```bash
git clone https://github.com/virtual457/VibeScape.git
cd VibeScape

# Runtime deps (backend + ingest)
pip install -r requirements.txt

# ML deps (only needed to train / run inference locally)
pip install -r ml/requirements.txt
```

### Configure

Set Spotify credentials via env vars (or edit `config.py`):

```bash
export SPOTIFY_CLIENT_ID=your_client_id
export SPOTIFY_CLIENT_SECRET=your_client_secret
export SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback
```

Pick an ML backend mode:

```bash
# Auto (default): try Modal → local → librosa fallback
export VIBESCAPE_ML_MODE=auto

# Force local (needs torch + ckpt at ml/models/mert_v1.ckpt)
export VIBESCAPE_ML_MODE=local

# Force Modal (needs MODAL_TOKEN_ID / MODAL_TOKEN_SECRET)
export VIBESCAPE_ML_MODE=modal
```

### Run

```bash
cd backend
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://127.0.0.1:8000/`, log in, paste a Spotify playlist URL, and watch it ingest.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Training Your Own Model

### 1. Get labels + previews

```bash
# Any Spotify-audio-features CSV works — put it at ml/data/spotify_tracks.csv
# Must contain: track_id, danceability, energy, valence, artists

python ml/src/download_previews.py
# Writes to ml/data/previews/*.mp3 + ml/data/manifest.csv
```

### 2. Sanity-check with the smoke config (fast_dev_run)

```bash
python ml/src/train.py --config ml/configs/smoke.yaml --fast-dev-run
```

### 3. Full training run

```bash
python ml/src/train.py --config ml/configs/default.yaml
# ~10 epochs on a single T4; best ckpt hard-linked to ml/models/mert_v1.ckpt
```

Monitor with MLflow:

```bash
mlflow ui --backend-store-uri file:ml/experiments/mlruns
# http://127.0.0.1:5000
```

### 4. Predict on a single file

```bash
python ml/src/predict.py --ckpt ml/models/mert_v1.ckpt --audio path/to/clip.mp3
# {"danceability": 0.73, "energy": 0.81, "valence": 0.62, "vibe_score": 0.775}
```

### 5. Backfill Whisper language on your library

```bash
python ml/src/predict_language.py --model small
# Resume-safe. Progress prints ETA + top-5 languages so far.
python ml/src/backfill_languages.py    # push manifest → SQLite
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Deployment

### Backend → Google Cloud Run

Secrets live in **Secret Manager** and are mounted as env vars at runtime, so they never touch the container image. Full runbook (API enablement, secret creation, IAM bindings): [`deploy/cloud-run/README.md`](deploy/cloud-run/README.md).

```powershell
.\deploy\cloud-run\deploy.ps1                # build + deploy + health-check + prune
.\deploy\cloud-run\deploy.ps1 -SkipCleanup   # deploy only
```

Or the underlying command directly:

```bash
gcloud run deploy vibescape \
  --source . --region us-central1 --allow-unauthenticated \
  --port 8080 --memory 512Mi --cpu 1 \
  --min-instances 0 --max-instances 3 --timeout 300 \
  --set-env-vars "VIBESCAPE_ML_MODE=modal,DB_BACKEND=turso,SPOTIFY_REDIRECT_URI=https://<service-url>/callback" \
  --set-secrets "SPOTIFY_CLIENT_ID=SPOTIFY_CLIENT_ID:latest,SPOTIFY_CLIENT_SECRET=SPOTIFY_CLIENT_SECRET:latest,MODAL_TOKEN_ID=MODAL_TOKEN_ID:latest,MODAL_TOKEN_SECRET=MODAL_TOKEN_SECRET:latest,TURSO_DATABASE_URL=TURSO_DATABASE_URL:latest,TURSO_AUTH_TOKEN=TURSO_AUTH_TOKEN:latest"
```

The container is deliberately tiny (512 MB, 1 CPU, scale-to-zero) — it never runs torch or librosa. Only 6 pip deps land: `fastapi / uvicorn / requests / modal / python-dotenv / numpy` (numpy is required because `_similar_dj`'s local-dev fallback path uses it — even in prod we import it at module top). `deploy.ps1` health-checks `/api/health` before pruning old revisions, images, and secret versions via `cleanup.ps1`.

### Database → Turso

```bash
turso db create vibescape
turso db show vibescape --url      # → TURSO_DATABASE_URL
turso db tokens create vibescape   # → TURSO_AUTH_TOKEN
```

With `DB_BACKEND=turso` the app talks to the remote libSQL instance and `docker-entrypoint.sh` skips the local SQLite seed. Left unset, it defaults to `sqlite` and seeds `data/vibescape.db` from the image on first boot — the path still used for local dev and for VM hosts with a persistent volume.

**Vector-native queries** — when `DB_BACKEND=turso`, `_similar_dj` builds the session-weighted query vector in Python, serializes it as a JSON array, and hands it to Turso as `vector32('[...]')`. Turso runs `vector_distance_cos()` server-side over the `fused_embedding F32_BLOB(788)` column and streams back only the top-K + track metadata. See [Query performance](#query-performance) for the egress math. F32_BLOB values come back as base64-encoded blobs that our `db_client.py` shim doesn't fully decode, so any Python path that needs the raw vectors (positives / negatives for query-vector construction) uses `vector_extract()` to get the text form and parses it. All wrapped in `_decode_embedding_cell()` in `backend/app.py`.

**Data sync** — `scripts/_turso_vs_local_diff.py` and `scripts/_push_local_to_turso.py` are the reproducible seed workflow: build the catalog locally against `sqlite`, then bulk-push metadata + embeddings to Turso when you're ready to ship. The push is DROP + CREATE for `tracks` and `track_embeddings`; `users` / `sessions` / `user_tracks` are left alone to preserve prod identity.

### ML → Modal

```bash
modal token new                # one-time browser auth
modal deploy modal_app.py      # publishes vibescape-ml app
```

`modal_app.py` bundles the checkpoint into the image and mounts persistent volumes for the HuggingFace and Whisper caches so cold starts don't re-download weights.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Tech Stack

### Machine Learning
- **PyTorch 2.6** + **torchaudio** — training + inference
- **PyTorch Lightning 2.6** — training loop, callbacks, checkpointing
- **Transformers 4.57** — MERT (`AutoModel` + `Wav2Vec2FeatureExtractor`)
- **openai-whisper** — language detection
- **MLflow** — experiment tracking
- **Optuna** — hyperparameter search (planned)
- **librosa 0.10** + **soundfile** — DSP + feature extraction

### Backend
- **FastAPI** + **Uvicorn** — REST API, OAuth callbacks, static file serving
- **Turso (libSQL)** — hosted track store, users, sessions, embeddings in prod; **SQLite** for local dev (see `schema.sql`)
- **`backend/db_client.py`** — hand-rolled `sqlite3`-compatible shim over Turso's Hrana HTTP API
- **numpy** — query-vector construction (weighted L2-norm sums of positives/negatives) + local-dev cosine fallback. Prod ranking happens server-side on Turso via `vector_distance_cos()`.
- **yt-dlp** — resolve Spotify tracks → YouTube video IDs for playback
- **requests** — Spotify Web API, iTunes/Deezer preview fallback

### Frontend
- Vanilla **JavaScript** + **HTML** + **CSS** — no framework, no build step
- **YouTube IFrame Player API** — playback
- **Media Session API** — Bluetooth / OS transport controls

### Infra
- **Docker** + **Google Cloud Run** — 512 MB / 1 CPU, `us-central1`, scale-to-zero
- **Cloud Build** + **Artifact Registry** — source-based image builds
- **Secret Manager** — runtime-mounted credentials, never baked into the image
- **Turso** — hosted libSQL, keeps state off Cloud Run's ephemeral filesystem
- **Modal** — remote T4 GPU inference, warm containers, persistent volume caches

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Project Structure

```
VibeScape/
├── backend/
│   ├── app.py                # FastAPI: OAuth, ingest, playback, library API
│   ├── db.py                 # DB bootstrap + connection helpers
│   └── db_client.py          # sqlite3-compatible shim over Turso/libSQL HTTP
│
├── frontend/
│   ├── index.html            # single-page player UI
│   ├── app.js                # mood-slider, filter, YouTube playback
│   └── style.css
│
├── ingest/                   # low-level clients + ML dispatch (shared by both pipelines)
│   ├── spotify_library.py    # Spotify Web API client
│   ├── spotify_matcher.py    # Spotify ⇄ iTunes/Deezer matching
│   ├── deezer_client.py      # Deezer preview fallback (30 s clips)
│   ├── itunes_client.py      # iTunes Search preview fallback
│   ├── features.py           # librosa feature bank + Krumhansl-Kessler
│   ├── scoring.py            # activation / valence / mood-grid logic
│   └── ml_backend.py         # Modal-vs-local-vs-none dispatcher (+ predict_from_path variants)
│
├── ingest_pipeline/          # v2 modular offline pipeline (6 stages)
│   ├── base.py               # Stage ABC + thread-pool run_batch + status vocab
│   ├── preview_providers.py  # PreviewChain + Spotify/iTunes/Deezer providers
│   ├── stage_preview.py      # 1. resolve preview_url (iTunes-only by default)
│   ├── stage_download.py     # 2. fetch preview → data/audio/<spotify_id>.<ext>
│   ├── stage_classify.py     # 3. MERT + head → activation/valence/mood + ML preds
│   ├── stage_youtube.py      # 4. ytsearch1, first hit
│   ├── stage_language.py     # 5. Whisper language detection
│   ├── stage_embedding.py    # 6. raw MERT-95M + librosa piggyback → fused vector
│   └── promote.py            # derive ingestion_status + cascade audio-failure rules
│
├── ml/
│   ├── configs/
│   │   ├── default.yaml      # 10-epoch fine-tune recipe
│   │   └── smoke.yaml        # fast_dev_run config
│   ├── src/
│   │   ├── model.py          # MERTVibeRegressor (LightningModule)
│   │   ├── dataset.py        # VibeDataset + GroupShuffleSplit helpers
│   │   ├── train.py          # entry point: config-driven training
│   │   ├── predict.py        # single-file inference
│   │   ├── evaluate.py       # test-set metrics
│   │   ├── download_previews.py    # Spotify preview downloader
│   │   ├── predict_language.py     # Whisper batch language detection
│   │   └── backfill_languages.py   # manifest → SQLite writer
│   ├── models/               # trained checkpoints (.ckpt)
│   └── experiments/mlruns/   # MLflow tracking store
│
├── scripts/
│   ├── run_ingest_v2.py                    # v2 orchestrator (loop across the 6 stages)
│   ├── run_ingest_worker.py                # legacy single-pass worker (still runs the monolithic _ingest_track_row)
│   ├── predict_ml.py                       # standalone predict wrapper
│   ├── prewarm_youtube.py                  # bulk-resolve YouTube IDs
│   ├── build_cookies_file.py               # yt-dlp cookies helper
│   ├── _backfill_mert_embeddings.py        # rebuild MERT vectors from local audio files
│   ├── _backfill_fused_embeddings.py       # legacy: build fused from existing MERT (pre-Option A)
│   ├── _refuse_embeddings.py               # rebuild fused for every track using current scalars + language; runs on local + Turso
│   ├── _migrate_track_embeddings_v2.py     # local: transpose track_embeddings to Option A shape
│   ├── _rescore_regressor_30s.py           # re-run regressor on full 30 s window
│   ├── _regressor_window_compare.py        # 10 s crop vs 30 s: bias + correlation across catalog
│   ├── _recommender_feasibility.py         # top-K similarity probe (MERT-only vs MERT+scalars)
│   ├── _dj_same_track_test.py              # DJ sanity check: N-repeat positive returns itself at cos=1.0
│   ├── _predict_crop_length_test.py        # trained-head sensitivity to 10 s vs 30 s crop (MAE per target)
│   ├── _fix_language_tags.py               # artist→language map + title patterns; corrects Whisper mistags on local + Turso
│   ├── _turso_vs_local_diff.py             # spotify_id diff between prod Turso and local dev
│   ├── _turso_pull_to_local.py             # pull missing tracks from Turso → local, mark stages as pending
│   ├── _push_local_to_turso.py             # DROP+CREATE tracks/track_embeddings on Turso, bulk INSERT from local
│   ├── _turso_create_vector_index.py       # attempt DiskANN index create (blocked by Turso server-side error)
│   ├── _turso_vector_probe.py              # smoke-test vector_distance_cos + vector_extract against Turso
│   ├── _turso_verify.py                    # post-push count/schema verifier
│   ├── _turso_*.py                         # other Turso ops (inventory, smoke, audio stats, resets)
│   └── _load_gcp_secrets.ps1               # local dev: pull Secret Manager values into env for a shell
│
├── deploy/cloud-run/
│   ├── README.md             # Cloud Run runbook (secrets, IAM, deploy)
│   ├── deploy.ps1            # build + deploy + health-check + prune
│   └── cleanup.ps1           # prune revisions / images / secret versions
│
├── docs/
│   └── mobile-api.md         # API reference for a mobile client
│
├── modal_app.py              # Modal deployment (predict_from_url, predict_language_from_url)
├── config.py                 # Spotify credentials (env-backed)
├── schema.sql                # SQLite / libSQL schema
├── requirements.txt          # runtime deps (thin)
├── ml/requirements.txt       # training + inference deps (heavy)
├── Dockerfile                # container build (Cloud Run via Cloud Build)
├── docker-entrypoint.sh      # seeds local SQLite unless DB_BACKEND=turso
└── fly.toml                  # legacy Fly.io config (superseded by Cloud Run)
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Roadmap

### Completed ✅
- [x] MERT-v1-95M fine-tune (danceability / energy / valence)
- [x] Group-shuffle split by artist (no leakage)
- [x] Modal remote-GPU dispatch + warm-container caching
- [x] Local-vs-Modal-vs-librosa dispatcher (`ml_backend.py`)
- [x] Whisper language detection with confidence tiers
- [x] Two-axis mood grid + `activation_relative` z-score normalization
- [x] Librosa feature bank with Krumhansl-Kessler valence
- [x] Resume-safe batch jobs (append-only manifests)
- [x] Cloud Run deployment (512 MB, torch-free, scale-to-zero) — [live](https://vibescape-241988497106.us-central1.run.app)
- [x] Turso/libSQL migration + `sqlite3`-compatible HTTP client shim
- [x] Media Session API (Bluetooth transport controls)
- [x] **MERT-embedding-based recommender** — full-clip 30 s embeddings in a dedicated `track_embeddings` table, MERT + scalar fusion, brute-force cosine retrieval (79.2% top-1 agreement across 739 seeds — see [Recommendation System](#recommendation-system))
- [x] Client-side preview streaming with backend fallback (cuts Cloud Run egress on the happy path)
- [x] Floating draggable/resizable video panel with mini transport controls
- [x] **Two-phase ingest split** — fast online metadata sync + offline worker for heavy analysis. Sync-modal returns in seconds instead of minutes.
- [x] **v2 modular ingest pipeline** — six independent stages (preview / download / classify / youtube / language / embedding) with per-stage status columns, thread-pool concurrency within a stage, and derived `ingestion_status` with cascade rules for audio-availability failures
- [x] **Local audio cache** — one preview download per song, ever; all downstream stages prefer the on-disk file. Strict cache gate (no URL fallback) so failures propagate cleanly instead of silently retrying
- [x] **DJ mode** — session-weighted taste vector from the last 10 playback events (queued=1.2, completed=0.8, skipped up to −0.8), no age decay, exclude window of last 50 played tracks. See [DJ Mode](#dj-mode--session-weighted-recommendations)
- [x] `/api/tracks/{id}/similar` — both `GET` (vibe mode, weighted L1 distance) and `POST` (DJ mode, cosine similarity against session-weighted query vector)
- [x] Empirical crop-length sensitivity study — MERT head's 10 s vs 30 s crop drift measured across 34 tracks. MAE 0.03-0.04 on `energy` / `dance` / `vibe_score` (borderline), 0.06 on `valence` (retrain required if we ever unify encoder passes). See `scripts/_predict_crop_length_test.py`
- [x] **Option A `track_embeddings` layout** — migrated from row-per-variant `(track_id, model_version, embedding)` to row-per-track with `mert_embedding F32_BLOB(768)` + `fused_embedding F32_BLOB(788)` typed columns inline. Byte-preserving migration (F32_BLOB and BLOB share physical layout), so the numpy code path works unchanged on both libSQL/Turso and vanilla SQLite.
- [x] **Server-side cosine ranking on Turso** — `_similar_dj` uses `vector_distance_cos(fused_embedding, vector32(?))` when `DB_BACKEND=turso`. Payload per DJ request drops from ~4.7 MB (every candidate embedding streamed to numpy) to ~10 KB (top-K + metadata). ~500× reduction in Turso→Cloud Run egress. Numpy fallback preserved for local dev.
- [x] **Language-tag correction workflow** — Whisper's language head hallucinates on musical audio (Kannada film songs often misclassified as `sa` / `km` / `nn`). `scripts/_fix_language_tags.py` applies an artist→language map + title-substring patterns to fix ~180 known-wrong tags; `scripts/_refuse_embeddings.py` then rebuilds fused vectors so DJ mode reflects the corrected language one-hot component. Runs against local + Turso in a single invocation.
- [x] Prod deploy on Cloud Run pinned to Turso for both metadata and vectors — single DB shared with the local dev via `DB_BACKEND` switch.

### In Progress 🚧
- [ ] Optuna sweeps over head-hidden / dropout / LR ratios
- [ ] Multi-crop test-time averaging (currently single centre crop)
- [ ] Genre auxiliary head (multi-task learning)
- [ ] Retire the legacy monolithic `_ingest_track_row` / `run_ingest_worker.py` once v2 pipeline is production-verified
- [ ] Modal-backed Classify / Language / Embedding stages (unblocks GPU concurrency; currently `max_workers=1` on local GPU to avoid MERT OOM)

### Planned 📋
- [ ] Turso DiskANN index (`libsql_vector_idx(fused_embedding)`) + `vector_top_k()` rewrite — server-side sub-linear query. Blocked on Turso server-side error `"vector index: unable to update global metadata table"`; revisit when they fix it or we grow past ~50 K embeddings and full-scan becomes user-visible.
- [ ] Larger MERT (`MERT-v1-330M`) with LoRA adapters
- [ ] Per-user preference learning on skip/replay signals (extend DJ mode's session weights into a persistent per-user model)
- [ ] Whisper transcription for lyric-based mood cues
- [ ] Web-audio-based on-device inference (ONNX / WebGPU)
- [ ] Unified MERT encoder pass — one 30 s forward feeds both the trained scalar head and the raw embedding pipeline (needs head retrained on 30 s crops first — see the crop-length sensitivity study)
- [ ] iOS Capacitor wrapper + native Spotify iOS SDK bridge (Web Playback SDK doesn't decode on iOS Safari due to Widevine/EME missing)
- [ ] LrcLib-based ground-truth pass over the ~40 remaining implausible language tags (mostly true instrumentals; keep as `null` if no lyrics found)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contact

**Chandan Keelara**
📧 gowdakeelarashivan.c@northeastern.edu
🐙 [github.com/virtual457](https://github.com/virtual457)

<p align="right">(<a href="#readme-top">back to top</a>)</p>
