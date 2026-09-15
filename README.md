<a id="readme-top"></a>

<div align="center">
  <h3 align="center">🎧 VibeScape</h3>
  <p align="center">
    <strong>VibeScape</strong> turns your Spotify library into a <strong>dynamically playable pool</strong>. Every song is fingerprinted along <em>mood, acoustic texture, and language</em>, so instead of building playlists you either scrub a <strong>vibe grid</strong> to steer the pool by feel, or let autoplay pick the next track in real time from your <strong>live listening state</strong> — what you queue, complete, and skip this session.
    <br/><br/>
    <a href="https://vibescape-241988497106.us-central1.run.app"><strong>🌐 Live demo →</strong></a>
  </p>
</div>

## What it does

- **Fingerprints every song in your library into a 788-D vector.** A fine-tuned **MERT** transformer contributes a **768-D learned acoustic-texture embedding** (timbre, instrumentation, mix density, vocal character — self-supervised on ~160k hrs of music). On top of that sit **9 explicit music-theoretic scalars** — `danceability`, `energy`, `valence`, `vibe_score`, `activation`, `valence`, `acousticness`, `tempo`, `brightness` — regressed from the same audio by the fine-tuned head. **Whisper** adds an **11-D language one-hot**. Fused, L2-normalized, and weighted, this vector is the coordinate system everything else steers over.
- **Two ways to steer the pool.** Scrub a two-axis **vibe grid** (activation × valence) to browse the library by feel — buckets like *chill / hype / melancholy / beast* fall out naturally. Or hand it to **DJ mode**, which picks the next track in real time from the last 10 events in your session (queue-adds and completions pull toward that sound, skips push away) ranked by cosine similarity over the fused embedding.
- **Plays the whole library through YouTube.** Each Spotify track is resolved to a `youtube_id` at ingest; the player runs off the YouTube IFrame API with Bluetooth / OS / lock-screen controls via Media Session API. A floating draggable/resizable video panel keeps the visual accessible without eating the layout.
- **Zero-friction sign-in.** Your Spotify account *is* your VibeScape identity — one OAuth flow, no separate password, no PIN, no second profile to remember. Email/password (scrypt) is available as a secondary path, and a **Just listen** entry point drops you straight into a shared demo library with no account at all.
- **Installs as a PWA** — manifest + service worker + iOS home-screen icons, so it lives on your home screen like an app.
- **Companion clients + ops surface.** A **Flutter mobile client** in `mobile/` talks to the same FastAPI backend. A separate `admin.html` surface handles catalog/user/ingest operations.


## Why it exists

Spotify removed the `/audio-features` endpoint for third-party apps in late 2024 — the endpoint that exposed `danceability / energy / valence`, the signals mood-based playlist construction is built on. The ML task here is precisely **knowledge distillation from a black-box model that was switched off**: training labels are Spotify's own published audio-features CSVs, so the model reproduces the function Spotify used. Predictions drop into the existing UI without recalibration.

## Architecture

Three independently deployed tiers. None sits on the critical path of the others' release cycle.

```
      deploy.ps1                modal deploy               turso db create
           │                          │                          │
           ▼                          ▼                          ▼
    ┌─────────────┐            ┌──────────────┐          ┌────────────────┐
    │  Cloud Run  │ ── RPC ──▶ │   Modal T4   │          │  Turso/libSQL  │
    │  512 MB/1CPU│            │  GPU workers │          │    (hosted)    │
    │  FastAPI+UI │ ◀──────────┤ MERT+Whisper │          │                │
    │  no torch   │ ────────────── SQL over HTTP ───────▶│                │
    └──────┬──────┘            └──────────────┘          └────────────────┘
           │                          │
           │                          └── fetches preview audio itself
           ▼
     YouTube IFrame + CDN preview URLs  (playback in the browser)
```

The load-bearing decision: **Cloud Run never imports `torch` and never touches audio bytes.** It dispatches a URL to Modal and stores the returned scalars. The always-on container stays at 512 MB / 1 CPU / scale-to-zero; GPU cost is paid per inference; state lives entirely off-box in Turso so any instance can serve any request.

Under any reasonable definition of "distributed application" this qualifies — compute is disaggregated across three execution tiers, the database is both storage *and* work-queue for the ingest pipeline (via per-stage status columns), and every tier can scale independently.

**Known scale limits.** Within a tier there's no horizontal scaling yet: single ingest orchestrator, `max_workers=1` on GPU stages. Two workers running at once would race on `pending` rows because there's no `claim_lease` column. That's a ~20-line addition when demand justifies it. Vector search is server-side on Turso but currently full-scan (see [Query performance](#query-performance)); DiskANN swap is roadmapped.

## Ingestion Pipeline

Ingest is split into a **fast online metadata pass** (runs inside the FastAPI request that a user's Spotify sync fires) and an **offline v2 pipeline of six modular stages** (a separate worker process that drains queued work). The split means the sync-modal returns in seconds — the user's library becomes visible immediately with metadata + previously-cached audio — while heavy per-track work (audio download, MERT inference, embeddings, language, YouTube resolution) happens asynchronously with no bearing on request latency.

### Phase 1 — online, synchronous (`backend/app.py`)

`_process_track()` writes a metadata-only `tracks` row for anything genuinely new, with `ingestion_status='pending'` and six per-stage status columns each set to `'pending'`. Three short-circuit outcomes:

| Condition | Bucket | Cost |
|---|---|---|
| `user_tracks` row already exists | `already_in_library` | 1 SELECT |
| `tracks` row exists globally, user not linked | `added_to_library` — reuse row, add `user_tracks` link | 1 SELECT + 1 INSERT |
| Brand new to the DB | `queued_for_analysis` — insert metadata + `user_tracks` link | 2 INSERTs |

A 500-track playlist re-sync where every track is already known completes in a few hundred milliseconds — no HTTP fetches beyond the Spotify pagination, no inference, no audio.

### Phase 2 — offline, batch (`ingest_pipeline/` + `scripts/run_ingest_v2.py`)

A background worker walks the six stages of the v2 pipeline in waves. Each pass fetches all pending rows for stage *N*, dispatches them concurrently (I/O-bound → thread pool), advances to stage *N+1*, and finishes with `promote.py` to derive `ingestion_status` and cascade terminal failures.

Songs move through **stages in waves, not one-by-one across stages** — the whole batch clears preview before any of it starts classify. Simpler orchestration than a per-track state machine, and each stage sees a hot working set.

### The Six Stages

Each stage lives in its own module under `ingest_pipeline/`, is gated by exactly one status column on `tracks`, and writes only its own domain columns + its own status column.

| # | Stage | File | Status column | Blocks on | Concurrency | What it does |
|---|---|---|---|---|---|---|
| 1 | **preview** | `stage_preview.py` | `preview_status` | — | 2 workers | Runs the provider chain to resolve a `preview_url`. Also backfills `apple_id`, `genre`, `track_view_url`, `album`, `artwork_url`, `duration_ms` from the provider hit. Sets `preview_source` to `spotify` / `itunes` / `deezer_isrc` / `deezer_search`. |
| 2 | **download** | `stage_download.py` | `download_status` | `preview_status='done'` | 8 workers | Fetches `preview_url` and writes to `data/audio/<spotify_id>.<ext>` atomically (`.part` rename). Sets `audio_path`. |
| 3 | **classify** | `stage_classify.py` | `ml_status` | `download_status='done'` | 1 (GPU) | Runs `MERTVibeRegressor` (10 s crop) on the cached audio via `ml_backend.predict_from_path`. Writes `energy_pred`, `danceability_pred`, `valence_pred`, `vibe_score_ml`, `activation`, `valence`, `vibe_score`, `mood`, `classification_source='ml_mert'`. |
| 4 | **youtube** | `stage_youtube.py` | `youtube_status` | — (independent) | 6 workers | `yt-dlp ytsearch1` for `"{title} {artist}"`. Takes the first hit, no embed / age / availability check. Writes `youtube_id`, `youtube_queried_at`. |
| 5 | **language** | `stage_language.py` | `language_status` | `download_status='done'` | 1 (GPU) | Whisper `small` language detection on the cached audio. Writes `language`, `language_confidence`, `language_top3_json`, `language_model_version` when top-1 confidence ≥ 0.20; otherwise `language_status='no_match'`. |
| 6 | **embedding** | `stage_embedding.py` | `embedding_status` | `download_status='done'` AND `ml_status='done'` | 1 (GPU) | Runs raw MERT-v1-95M encoder (30 s window) → mean-pooled 768-D vector. Writes `mert_v1_95m_fp32_30s` blob to `track_embeddings`. Piggybacks librosa `tempo` / `brightness` / `acousticness` from the same waveform. Builds the fused vector using those scalars + `language` and writes `fused_v1_mert_scalar_lang` (788-D). |

**Constraint-collision retry.** An UPDATE that hits the legacy `UNIQUE (user_id, apple_id)` index retries once with `apple_id / track_view_url / genre` stripped. The row's own status column always lands so the pipeline never loops on the same row.

### Status vocabulary + promotion cascade

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

The **default chain is Spotify-then-iTunes-only**. Deezer providers are wired but kept out of `default_chain()` because Deezer previews have different audio properties (bitrate, EQ, sample rate) that shift MERT embeddings enough to matter for recommendations — keeping provenance consistent means the embedding space stays comparable across the catalog. Signed-URL expiry (`?hdnea=exp=<ts>`) is a secondary concern.

**iTunes rate-limit handling.** iTunes' Search API 403s bursts around ~20 req/min per IP. `ItunesPreview` serializes behind a class-level lock with a 500 ms inter-request gap and retries 403s with exponential backoff (2 s → 4 s → 8 s → 16 s + jitter, 4 retries max). `PreviewStage.max_workers=2` — more workers here would just spin on the lock without gaining throughput.

Adding a new provider = write a `PreviewProvider` subclass + prepend it to `default_chain()`.

### Local Audio Cache

`DownloadStage` writes every preview to `data/audio/<spotify_id>.<ext>` (`.m4a` for iTunes AAC, `.mp3` for MP3 sources). `resolve_audio_path()` is the single source of truth for cache lookups; every downstream stage prefers the local file:

- **One preview download per song, ever** — not per stage per pass.
- **Cache survives crashes / re-runs.** A backfill migration marks pre-existing files as `download_status='done'`.
- **Strict cache path.** Classify / language / embedding all `SELECT ... WHERE download_status='done' AND audio_path IS NOT NULL`. There is **no URL fallback** — if audio isn't cached, promote cascades the row to `no_match` and downstream stages skip it. Failures propagate cleanly instead of silently retrying.

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

The orchestrator is a thin loop over `Stage.run_batch()` calls followed by `promote()`. Stages are stateless — swap in Modal-backed classify/language stages later by changing `ml_backend` mode without touching orchestration.

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
- **Splits**: `GroupShuffleSplit` grouped on `artists` so **no artist crosses train/val/test**. Two nested splits enforce artist disjointness across all three sets.
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

Reproducibility: `seed=42`, `deterministic=True`, split RNG seeded independently.

### Whisper Language Head

Every preview also passes through **OpenAI Whisper** (small by default — configurable up to `large-v3`) using only the **language-detection head** on the first 30 s of audio. No transcription — just the softmax over Whisper's 99 language IDs.

Predictions are bucketed by top-1 probability:

| Bucket | Threshold | Meaning |
|---|---|---|
| `confident` | `p ≥ 0.5` | Written to DB |
| `uncertain` | `0.2 ≤ p < 0.5` | Written to DB with lower confidence |
| `unknown` | `p < 0.2` | Likely instrumental / non-speech; DB stays `NULL` |
| `failed` | — | Load/model error; resume-safe re-try via `--retry-failed` |

The manifest at `ml/data/language_manifest.csv` is append-only and compacted on every run — batch jobs are Ctrl-C safe.

### Librosa Feature Bank (Baseline / Fallback)

The system originally ran on ~15 hand-engineered features from `ingest/features.py`. This path is still the **fallback when Modal / local inference is unavailable**, and drives an interpretable second opinion:

- **Rhythm**: tempo (beat-track), tempo stability (PLP inverse-std), onset rate
- **Energy**: RMS mean/std, HPSS harmonic-percussive split → `acousticness = h_energy / (h + p)`
- **Spectral**: centroid (brightness), bandwidth, rolloff, contrast, flatness, ZCR
- **Timbre**: 13-dim MFCC mean + std
- **Tonal**: 12-dim CENS chroma, tonnetz std
- **Mode / valence**: **Krumhansl-Kessler major/minor template correlation** — correlate the mean chroma vector against all 12 rotations of KK major and minor profiles; report `max_major_corr − max_minor_corr` clipped to [−1, +1] as `valence_mode`.

### Vibe Scoring

Both paths converge on a two-axis representation:

- **activation** ∈ [0, 100]: `0.30·energy + 0.25·tempo + 0.20·dance + 0.10·onset + 0.10·brightness + 0.05·dynamic_range`
- **valence** ∈ [0, 100]: `0.50·mode + 0.20·(1−flatness) + 0.15·contrast + 0.15·(1 − 0.5·acousticness)`
- **`activation_relative`**: library-wide z-score so the frontend slider gives a percentile view instead of clumping in the middle.

A 2×5 **mood grid** is derived from these two axes:

|  | valence < 50 | valence ≥ 50 |
|---|---|---|
| activation < 20 | sleep | sleep |
| 20–40 | melancholy | chill |
| 40–60 | moody | steady |
| 60–80 | aggressive | hype |
| ≥ 80 | beast | beast |

## Recommendation System

Vibe-consistent autoplay picks the next track from your library using a **fused acoustic + emotional embedding**, ranked by cosine similarity. Two modes:

- **Vibe mode** (`GET /api/tracks/{id}/similar`) — anchored on the currently-playing seed, ranked by weighted L1 distance across the four ML feature dimensions + mood bonus. Returns the closest N tracks in the library.
- **DJ mode** (`POST /api/tracks/{seed}/similar`) — anchored on the user's **rolling session** rather than a single seed. The query vector is built from the user's last few playback events (completions, queue-adds, skips) weighted by action type.

### Fused Embedding

Every track with a downloadable preview gets a **768-D raw MERT embedding** (mean-pool of `last_hidden_state` over 30 s of audio) and a **788-D fused embedding** that folds in ML scalar predictions + a language one-hot. Both live in a dedicated `track_embeddings` table — one row per track, each variant in its own typed vector column, so the DB can build an ANN index over the fused column directly:

```sql
CREATE TABLE track_embeddings (
  track_id         INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
  mert_embedding   F32_BLOB(768),   -- raw MERT, DJ-independent
  fused_embedding  F32_BLOB(788),   -- MERT + scalars + language, what DJ ranks
  model_version    TEXT,
  updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

On libSQL/Turso `F32_BLOB(dim)` is a typed vector affinity. On vanilla SQLite it collapses to plain BLOB — physical bytes are identical, so `np.frombuffer(row['fused_embedding'], dtype=np.float32)` works on both. Only the DB-native `vector_distance_cos()` / `vector_top_k()` functions differ.

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

### Why fusion, not MERT alone?

Measured top-1 agreement between MERT-only and MERT+scalars across all 739 embedded tracks:

| Depth | Avg overlap | Exact match |
|---|---|---|
| top-1 | 79.2% (585/739) | 585 seeds |
| top-3 | 81.0% (2.43/3) | 353 seeds |
| top-5 | 82.4% (4.12/5) | 236 seeds |
| top-10 | 84.8% (8.48/10) | 100 seeds |

MERT alone captures ~80% of the correct nearest neighbours. The remaining 20% is where the two methods disagree — almost entirely at emotional extremes. MERT treats *"loud + energetic + happy"* and *"loud + energetic + angry"* as acoustically adjacent because they share texture; the scalar slice's valence dimension disambiguates them. The language one-hot prevents cross-language whiplash (a Tamil devotional set doesn't want Norwegian folk at the same valence/energy).

### Why the full 30 s embedding, not the 10 s crop?

The trained regressor uses a centre-cropped 10 s window because that was the training compute budget. For similarity search we want the *whole song's* acoustic fingerprint. Measured across all 739 embedded tracks:

- Correlation `centre-10s vibe` vs `full-30s vibe`: **0.960**
- Mean absolute vibe delta: **3.15 pts** on a 0-100 scale
- Systematic bias: full-30s scores **1.83 pts lower** on average

Ranking is largely preserved, but ~16% of tracks with lopsided intros/outros differ by 5–23 vibe points. The 30 s window is more representative for embedding purposes; the 10 s crop is left intact for scalar prediction so mood-grid coordinates stay stable. The split is queryable via `model_version` (`mert_v1` = 10 s scalar, `mert_v1_30s` = 30 s rescoring).

### Ranking backend

**Turso (prod).** Cosine ranking runs **server-side** via `vector_distance_cos(fused_embedding, vector32(?))` — query vector serializes as a JSON-array string, Turso computes distances against every candidate in a full-scan `ORDER BY distance ASC LIMIT K` and streams back only the top-K + track metadata. **Payload per DJ request: ~10 KB** (top-K rows) instead of **~4.7 MB** (every candidate's raw embedding streamed to Python) — a **~500× reduction in Turso→Cloud-Run egress**, which is what makes DJ mode viable on the 3 GB/month Turso free tier.

**Local sqlite (dev).** Vanilla SQLite has no vector functions, so `_load_mert_vecs_bulk` still pulls all candidate embeddings and cosine happens in numpy. Fast at library sizes < 100 K (< 5 ms for the whole scan).

Dispatch is a one-line check on `os.environ["DB_BACKEND"]` in `_similar_dj`; the numpy path is preserved as local-dev + graceful-degradation fallback.

### DJ Mode — Session-Weighted Recommendations

DJ mode replaces the single-seed similarity query with a **taste-vector query** built from the user's rolling session.

**Client-side taste buffer** (`frontend/app.js`, key `vibescape.sessionEvents` in localStorage):
- Ring buffer of the **last 10 playback events** — each entry `{track_id, action, played_ratio, ts}`.
- Populated on every queue-add, natural end, or transition. Actions: `completed`, `queued`, `next`, `skipped`.
- Cleared on logout, survives page reload.

**Event → weight table** (`djBuildWeights`):

| action | condition | weight | pile |
|---|---|---:|---|
| `queued` | user explicitly added to queue | **+1.2** | positive |
| `completed` | natural end or `played_ratio ≥ 0.85` | **+0.8** | positive |
| `next` | manual skip, `played_ratio > 0.5` | +0.3 | positive |
| `next` | manual skip, `played_ratio ≤ 0.5` | 0 | ignored |
| `skipped` | manual skip, `played_ratio < 0.15` | **−0.8** | negative |
| `skipped` | manual skip, `0.15 ≤ played_ratio < 0.45` | −0.4 | negative |
| `skipped` | manual skip, `0.45 ≤ played_ratio < 0.85` | 0 | ignored (ambiguous) |

Queued weight > completed weight because a queue-add is an *explicit, forward-looking* choice; a completion can be passive (music kept playing in a background tab).

**No age decay.** Every event in the 10-slot buffer counts at full base weight. The buffer *is* the recency window. Decay was tried and dropped: at buffer size 10 it just penalized the median-age event by ~40% for no useful discrimination.

**Query vector construction** (`backend/app.py::_similar_dj`):

```
pos_vec   = Σ (w_i · L2_normalize(fused_i))      for positive events
neg_vec   = Σ (w_i · L2_normalize(fused_i))      for negative events
query_vec = L2_normalize(pos_vec − 0.4 · neg_vec)
```

Each contributing track is L2-normalized *before* weighting, and the final query vector is L2-normalized again — **magnitude is irrelevant**, only direction of the accumulated taste matters. Piling on more `completed` events from the same vibe pulls the query toward the centroid of the liked cluster (tighter recs), not out of the cluster.

**Exclusion list** (`djExcludeIds`):
- All tracks currently in the queue.
- **Last 50 played tracks** (`state.recent`, in-memory, capped at `RECENT_MAX=50`, cleared on logout). ~2.5–3 h of listening — long enough to prevent obvious repeats, short enough the user can hear a track again in a long session.
- The URL seed itself.

**Cold start.** When the taste buffer is empty or contains only the seed, the query vector is undefined. Backend falls back to random selection from the candidate pool with `score=0`. Once one non-seed positive event lands, DJ mode engages properly.

**Fallback to vibe mode.** If the seed has no MERT embedding (recently-ingested track that hasn't finished the embedding stage), the endpoint returns vibe-mode results with `mode_used='vibe_fallback_no_seed_embedding'`.

### Query performance

**Turso path (prod).** `vector_distance_cos()` full-scan over 1489 candidates: **~300 ms** per DJ request end-to-end. Payload: ~10 KB. At ~50 K embeddings we'd hit ~10 s per request and want DiskANN — a `CREATE INDEX ... libsql_vector_idx(fused_embedding)` swap plus a `vector_top_k()` rewrite. Turso's ANN was flaky on our instance at build time; deferred until it cooperates or scale demands it.

**Numpy path (local dev).** ~5 ms per request at library size 1489.

**Egress economics** (Turso free tier is 3 GB/month):

| Backend | Bytes per DJ request | Free-tier ceiling |
|---|---:|---:|
| Old numpy path (every candidate blob streamed) | ~4.7 MB | ~640 requests/mo |
| Current Turso `vector_distance_cos` (top-K + metadata) | ~10 KB | ~300 K requests/mo |

### Populating embeddings

Embeddings are produced inline by the v2 pipeline's `EmbeddingStage` — no separate backfill needed. That stage reads local cached audio (from `DownloadStage`), runs raw MERT once, piggybacks a `librosa` pass on the same waveform to fill `acousticness / tempo / brightness`, then builds the fused vector and writes both blobs in one `INSERT ... ON CONFLICT DO UPDATE`.

Two ops helpers exist for offline maintenance:

- **`scripts/_backfill_mert_embeddings.py`** — rebuild MERT vectors from local audio files. Use after locally re-processing audio (e.g. bumped `MAX_DURATION_S`).
- **`scripts/_refuse_embeddings.py`** — rebuild only the *fused* vector for every track from its existing MERT + current scalar columns + current language. No audio, no GPU — pure numpy over blobs we already have. Runs against local and Turso in one invocation. This is what you run after a language-tag correction sweep.

## Engineering decisions worth calling out

Grouped by what they buy you:

- **Torch-free API container.** Cloud Run stays at 512 MB / 1 CPU / scale-to-zero because it never imports `torch`, `librosa`, or ffmpeg beyond ingest. Only 6 pip deps land in prod (`fastapi / uvicorn / requests / modal / python-dotenv / numpy`). Every GPU op goes through the `ml_backend.py` dispatcher to Modal.
- **DB-as-queue via per-stage status columns.** No external queue (SQS/Celery/Redis). The `tracks.<stage>_status` columns *are* the work queue — `SELECT ... WHERE status='pending' LIMIT N` is the claim protocol. Weaker semantics than SQS (no leases, single-worker-per-stage today), but correct for the actual workload and dependency-free.
- **Cascade rules keep pending counts meaningful.** `preview_status='no_match'` cascading to `download_status`, and `download_status='no_match'` cascading to `ml/language/embedding` prevents rows sitting `pending` forever waiting on audio that will never arrive. Without this the pipeline metrics lie.
- **Strict cache gate, no URL fallback.** Downstream stages require `download_status='done' AND audio_path IS NOT NULL`. Missing audio propagates as a `no_match` cascade instead of silently retrying — failures visible instead of hidden.
- **Backend-agnostic typed vector columns.** `F32_BLOB(768)` and `F32_BLOB(788)` are typed on libSQL, plain BLOB on SQLite — same bytes on disk. `np.frombuffer(..., dtype=np.float32)` reads both. Only the DB-native `vector_distance_cos()` differs, dispatched by a one-line check on `DB_BACKEND`.
- **Server-side cosine + query serialization.** `vector_distance_cos(fused_embedding, vector32('[...]'))` runs the full-scan on Turso's side and returns top-K + metadata (~10 KB) instead of all candidate blobs (~4.7 MB). ~500× less egress. Numpy path preserved for local dev.
- **`sqlite3`-compatible HTTP shim.** `backend/db_client.py` implements a `sqlite3` connection/cursor/row shim over Turso's raw Hrana HTTP pipeline. Every call site keeps its plain `sqlite3` API — `DB_BACKEND=sqlite|turso` switches the whole app between local file and remote DB.
- **F32_BLOB round-trip quirk.** Turso returns F32_BLOB values as base64-encoded blobs that the HTTP shim doesn't fully decode. Any path that needs the raw vectors (positives/negatives for query-vector construction) uses `vector_extract()` to get the text form and parses it. Wrapped in `_decode_embedding_cell()` — one place to update if libSQL changes the wire format.
- **Empirical crop-length audit.** Before committing to a 30 s embedding + 10 s scalar prediction split, we measured drift (`scripts/_predict_crop_length_test.py`, `_regressor_window_compare.py`). 0.960 correlation, ~3 pt MAE, systematic 1.83 pt bias — small enough to keep the split, large enough to justify the `model_version` column that makes it queryable.
- **Language-tag correction workflow.** Whisper hallucinates on musical audio (Kannada film songs often mis-tagged as `sa / km / nn`). `scripts/_fix_language_tags.py` applies an artist→language map + title-substring patterns for ~180 known-wrong tags; `_refuse_embeddings.py` rebuilds fused vectors so DJ mode reflects the corrected language one-hot. Runs against local + Turso in one invocation.

## Deployment

### Backend → Google Cloud Run

Secrets live in **Secret Manager** and mount as env vars at runtime, so they never touch the container image. Full runbook: [`deploy/cloud-run/README.md`](deploy/cloud-run/README.md).

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

`deploy.ps1` health-checks `/api/health` before pruning old revisions, images, and secret versions via `cleanup.ps1`.

### Database → Turso

```bash
turso db create vibescape
turso db show vibescape --url      # → TURSO_DATABASE_URL
turso db tokens create vibescape   # → TURSO_AUTH_TOKEN
```

With `DB_BACKEND=turso` the app talks to remote libSQL and `docker-entrypoint.sh` skips local SQLite seed. Left unset, it defaults to `sqlite` and seeds `data/vibescape.db` from the image on first boot — still used for local dev and VM hosts with persistent volumes.

**Data sync.** `scripts/_turso_vs_local_diff.py` and `scripts/_push_local_to_turso.py` are the reproducible seed workflow: build the catalog locally against `sqlite`, then bulk-push metadata + embeddings to Turso when ready to ship. The push is DROP + CREATE for `tracks` and `track_embeddings`; `users` / `sessions` / `user_tracks` are left alone to preserve prod identity.

### ML → Modal

```bash
modal token new                # one-time browser auth
modal deploy modal_app.py      # publishes vibescape-ml app
```

`modal_app.py` bundles the checkpoint into the image and mounts persistent volumes for the HuggingFace and Whisper caches so cold starts don't re-download weights.

## Tech Stack

### Machine Learning
- **PyTorch 2.6** + **torchaudio** — training + inference
- **PyTorch Lightning 2.6** — training loop, callbacks, checkpointing
- **Transformers 4.57** — MERT (`AutoModel` + `Wav2Vec2FeatureExtractor`)
- **openai-whisper** — language detection
- **MLflow** — experiment tracking
- **librosa 0.10** + **soundfile** — DSP + feature extraction

### Backend
- **FastAPI** + **Uvicorn** — REST API, OAuth callbacks, static file serving
- **Turso (libSQL)** — hosted track store, users, sessions, embeddings in prod; **SQLite** for local dev (`schema.sql`)
- **`backend/db_client.py`** — hand-rolled `sqlite3`-compatible shim over Turso's Hrana HTTP API
- **numpy** — query-vector construction + local-dev cosine fallback. Prod ranking is server-side on Turso via `vector_distance_cos()`.
- **yt-dlp** — resolve Spotify tracks → YouTube video IDs
- **requests** — Spotify Web API, iTunes/Deezer preview fallback

### Frontend
- Vanilla **JavaScript** + **HTML** + **CSS** — no framework, no build step
- **YouTube IFrame Player API** — playback
- **Media Session API** — Bluetooth / OS / lock-screen controls
- **PWA** — manifest + service worker + iOS home-screen icons
- **Sign-in:** Spotify OAuth as primary identity, native email/password (scrypt) as a secondary path, plus a *Just listen* guest flow (no account, shared demo library)
- Separate **admin console** at `admin.html`
- Companion **Flutter client** in `mobile/` against the same FastAPI backend

### Infra
- **Docker** + **Google Cloud Run** — 512 MB / 1 CPU, `us-central1`, scale-to-zero
- **Cloud Build** + **Artifact Registry** — source-based image builds
- **Secret Manager** — runtime-mounted credentials
- **Turso** — hosted libSQL for state (keeps state off Cloud Run's ephemeral FS)
- **Modal** — remote T4 GPU inference, warm containers, persistent-volume weight caches

## Project Structure

```
VibeScape/
├── backend/
│   ├── app.py                # FastAPI: OAuth, ingest hot path, playback, library API, /similar
│   ├── db.py                 # DB bootstrap + connection helpers
│   └── db_client.py          # sqlite3-compatible shim over Turso/libSQL HTTP
│
├── frontend/
│   ├── index.html            # single-page player UI
│   ├── app.js                # mood-slider, filter, YouTube playback, DJ session buffer
│   ├── style.css
│   ├── login.html/js/css     # Spotify OAuth + email-password + Just-listen guest
│   ├── admin.html/js/css     # catalog + ingest admin console
│   ├── manifest.json         # PWA install
│   ├── sw.js                 # service worker
│   └── icons/                # PWA + apple-touch icons
│
├── ingest/                   # low-level clients + ML dispatch (shared by both pipelines)
│   ├── spotify_library.py    # Spotify Web API client
│   ├── spotify_matcher.py    # Spotify ⇄ iTunes/Deezer matching
│   ├── deezer_client.py      # Deezer preview fallback
│   ├── itunes_client.py      # iTunes Search preview fallback
│   ├── features.py           # librosa feature bank + Krumhansl-Kessler
│   ├── scoring.py            # activation / valence / mood-grid logic
│   └── ml_backend.py         # Modal-vs-local-vs-none dispatcher
│
├── ingest_pipeline/          # v2 modular offline pipeline (6 stages)
│   ├── base.py               # Stage ABC + thread-pool run_batch + status vocab
│   ├── preview_providers.py  # PreviewChain + Spotify/iTunes/Deezer providers
│   ├── stage_preview.py      # 1. resolve preview_url (Spotify → iTunes chain)
│   ├── stage_download.py     # 2. fetch preview → data/audio/<spotify_id>.<ext>
│   ├── stage_classify.py     # 3. MERT + head → mood/scalars
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
│   ├── run_ingest_worker.py                # legacy single-pass worker
│   ├── predict_ml.py                       # standalone predict wrapper
│   ├── prewarm_youtube.py                  # bulk-resolve YouTube IDs
│   ├── build_cookies_file.py               # yt-dlp cookies helper
│   ├── _backfill_mert_embeddings.py        # rebuild MERT vectors from local audio
│   ├── _backfill_fused_embeddings.py       # legacy: build fused from existing MERT
│   ├── _refuse_embeddings.py               # rebuild fused only; runs on local + Turso
│   ├── _migrate_track_embeddings_v2.py     # local: transpose to Option A shape
│   ├── _rescore_regressor_30s.py           # re-run regressor on full 30 s window
│   ├── _regressor_window_compare.py        # 10 s vs 30 s: bias + correlation
│   ├── _recommender_feasibility.py         # top-K probe (MERT-only vs MERT+scalars)
│   ├── _dj_same_track_test.py              # DJ sanity: N-repeat returns itself at cos=1.0
│   ├── _predict_crop_length_test.py        # 10 s vs 30 s MAE per target
│   ├── _fix_language_tags.py               # artist→language map + patterns; local + Turso
│   ├── _turso_vs_local_diff.py             # spotify_id diff between prod Turso and local
│   ├── _turso_pull_to_local.py             # pull missing tracks Turso → local
│   ├── _push_local_to_turso.py             # DROP+CREATE + bulk INSERT local → Turso
│   ├── _turso_create_vector_index.py       # DiskANN index attempt (blocked)
│   ├── _turso_vector_probe.py              # smoke-test vector_distance_cos + vector_extract
│   ├── _turso_verify.py                    # post-push count/schema verifier
│   ├── _turso_*.py                         # other Turso ops (inventory, smoke, resets)
│   └── _load_gcp_secrets.ps1               # local: pull Secret Manager values into env
│
├── deploy/cloud-run/
│   ├── README.md             # Cloud Run runbook (secrets, IAM, deploy)
│   ├── deploy.ps1            # build + deploy + health-check + prune
│   └── cleanup.ps1           # prune revisions / images / secret versions
│
├── docs/
│   ├── architecture.md       # technical companion to this README
│   ├── dsp.md                # DSP / feature-extraction deep-dive
│   ├── mobile-api.md         # API reference for a mobile client
│   └── openapi.json          # generated OpenAPI snapshot
│
├── mobile/                   # Flutter client (iOS + Android) against the same backend
├── codemagic.yaml            # CI for the Flutter build
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

## Getting Started

### Prerequisites

- **Python 3.13**
- **ffmpeg** on PATH (or bundled via `imageio-ffmpeg`)
- A Spotify developer app (Client ID + Secret) — free at [developer.spotify.com](https://developer.spotify.com/)
- Optional: NVIDIA GPU + CUDA for local training / inference
- Optional: [Modal](https://modal.com/) account for remote GPU

### Install & configure

```bash
git clone https://github.com/virtual457/VibeScape.git
cd VibeScape

pip install -r requirements.txt              # backend + ingest
pip install -r ml/requirements.txt           # only if training / running inference locally

export SPOTIFY_CLIENT_ID=your_client_id
export SPOTIFY_CLIENT_SECRET=your_client_secret
export SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback

export VIBESCAPE_ML_MODE=auto                # auto | modal | local | none
```

### Run

```bash
cd backend
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://127.0.0.1:8000/`, log in, paste a Spotify playlist URL, watch it ingest.

To drain the offline pipeline in a separate terminal:

```bash
python scripts/run_ingest_v2.py --loop --batch 30 --interval 30
```

## Training Your Own Model

```bash
# 1. Get labels + previews (any Spotify audio-features CSV at ml/data/spotify_tracks.csv)
python ml/src/download_previews.py

# 2. Sanity check
python ml/src/train.py --config ml/configs/smoke.yaml --fast-dev-run

# 3. Full run (~10 epochs on a T4; best ckpt hard-linked to ml/models/mert_v1.ckpt)
python ml/src/train.py --config ml/configs/default.yaml

# Monitor:
mlflow ui --backend-store-uri file:ml/experiments/mlruns

# 4. Predict on a single file
python ml/src/predict.py --ckpt ml/models/mert_v1.ckpt --audio path/to/clip.mp3

# 5. Backfill Whisper language on your library
python ml/src/predict_language.py --model small
python ml/src/backfill_languages.py
```

## Roadmap

### Shipped
- MERT-v1-95M fine-tune (danceability / energy / valence)
- Group-shuffle split by artist (no leakage)
- Modal remote-GPU dispatch + warm-container caching
- Local-vs-Modal-vs-librosa dispatcher (`ml_backend.py`)
- Whisper language detection with confidence tiers
- Two-axis mood grid + `activation_relative` z-score normalization
- Librosa feature bank with Krumhansl-Kessler valence
- Resume-safe batch jobs (append-only manifests)
- Cloud Run deployment (512 MB, torch-free, scale-to-zero)
- Turso/libSQL migration + `sqlite3`-compatible HTTP client shim
- Media Session API (Bluetooth transport controls); PWA install
- MERT-embedding-based recommender — 30 s embeddings, MERT + scalar fusion, brute-force cosine (79.2% top-1 agreement across 739 seeds)
- Client-side preview streaming with backend fallback (cuts Cloud Run egress)
- Floating draggable/resizable video panel with mini transport controls
- Two-phase ingest split — fast online sync + offline worker
- v2 modular ingest pipeline — six stages, per-stage status columns, cascade rules
- Local audio cache — one download per song, strict cache gate
- DJ mode — session-weighted taste vector, no age decay, 50-track exclusion window
- `/api/tracks/{id}/similar` — `GET` (vibe, L1) and `POST` (DJ, cosine)
- Empirical crop-length sensitivity study (10 s vs 30 s MAE + bias)
- Option A `track_embeddings` layout — row-per-track typed `F32_BLOB(768)` + `F32_BLOB(788)`, byte-preserving migration
- Server-side cosine on Turso — ~500× egress reduction
- Language-tag correction workflow — artist→language map + title patterns; rebuilds fused vectors
- Prod pinned to Turso for both metadata and vectors; single DB shared with local dev via `DB_BACKEND`

### In progress
- Optuna sweeps (head-hidden / dropout / LR ratios)
- Multi-crop test-time averaging
- Genre auxiliary head (multi-task)
- Retire legacy monolithic `_ingest_track_row` / `run_ingest_worker.py`
- Modal-backed Classify / Language / Embedding stages (unblocks GPU concurrency)

### Planned
- Turso DiskANN index (`libsql_vector_idx(fused_embedding)`) + `vector_top_k()` rewrite — blocked on Turso server-side error; revisit at ~50 K embeddings
- Larger MERT (`MERT-v1-330M`) with LoRA adapters
- Discovery-slot mixing — always inject N global-catalog candidates alongside in-library recs
- Per-user preference learning on skip/replay signals (persistent per-user model)
- Whisper transcription for lyric-based mood cues
- Web-audio on-device inference (ONNX / WebGPU)
- Unified MERT encoder pass (needs head retrained on 30 s crops first)
- iOS Capacitor / native Spotify iOS SDK bridge (Web Playback SDK doesn't decode on iOS Safari)
- LrcLib-based ground-truth pass over remaining implausible language tags
- Distributed ingest workers — add a claim-lease column so multiple orchestrators can safely drain in parallel

## Contact

**Chandan Keelara**
📧 gowdakeelarashivan.c@northeastern.edu
🐙 [github.com/virtual457](https://github.com/virtual457)
