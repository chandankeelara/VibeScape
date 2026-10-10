[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

<a id="readme-top"></a>

<div align="center">
  <h3 align="center">🎧 VibeScape — Audio-ML Music Player</h3>
  <p align="center">
    <strong>VibeScape</strong> turns your Spotify library into a <strong>dynamically playable pool</strong>. Every song is fingerprinted along <em>mood, acoustic texture, and language</em>, so instead of building playlists you either scrub a <strong>vibe slider</strong> to steer the pool by feel, or let autoplay pick the next track in real time from your <strong>live listening state</strong> — what you queue, complete, and skip this session.
    <br/><br/>
    <a href="https://vibescape-241988497106.us-central1.run.app"><strong>🌐 Live demo →</strong></a>
    <br/><br/>
    <a href="#getting-started">Quick Start</a>
    ·
    <a href="#ml-pipeline">ML Pipeline</a>
    ·
    <a href="#architecture">Architecture</a>
    ·
    <a href="#recommendation-system">Recommender</a>
    ·
    <a href="https://github.com/chandankeelara/VibeScape/issues">Report Bug</a>
  </p>
</div>

## 📋 Table of Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [ML Pipeline](#ml-pipeline)
  - [Model — MERT Regressor](#model--mert-regressor)
  - [Data & Splits](#data--splits)
  - [Training Recipe](#training-recipe)
  - [Whisper Language Head](#whisper-language-head)
  - [Librosa Feature Bank (Baseline / Fallback)](#librosa-feature-bank-baseline--fallback)
  - [Vibe Scoring](#vibe-scoring)
- [Ingestion Pipeline](#ingestion-pipeline)
  - [Phase 1 — online, synchronous](#phase-1--online-synchronous-backendapppy)
  - [Phase 2 — offline, batch](#phase-2--offline-batch-ingest_pipeline--scriptsrun_ingest_v2py)
  - [Why async, not synchronous](#why-async-not-synchronous)
  - [The Seven Stages](#the-seven-stages)
  - [Status vocabulary + promotion cascade](#status-vocabulary--promotion-cascade)
  - [Preview Provider Chain](#preview-provider-chain)
  - [Local Audio Cache](#local-audio-cache)
  - [Orchestrator](#orchestrator)
- [Recommendation System](#recommendation-system)
- [Engineering decisions worth calling out](#engineering-decisions-worth-calling-out)
- [Deployment](#deployment)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Training Your Own Model](#training-your-own-model)
- [Roadmap](#roadmap)
- [Contact](#contact)

## What it does

- **Fingerprints every song in your library into a 788-D vector.** A fine-tuned **MERT** transformer contributes a **768-D learned acoustic-texture embedding** (timbre, instrumentation, mix density, vocal character — self-supervised on ~160k hrs of music). On top of that sit **9 explicit music-theoretic scalars** — `danceability`, `energy`, `valence`, `vibe_score`, `activation`, `valence`, `acousticness`, `tempo`, `brightness` — regressed from the same audio by the fine-tuned head. **Whisper** adds an **11-D language one-hot**. Fused, L2-normalized, and weighted, this vector is the coordinate system everything else steers over.
- **Two ways to steer the pool.** Scrub a **vertical vibe slider** (activation, 0–100, library-wide z-scored so distribution is percentile-flat) to browse by feel — the library partitions into five buckets along that axis (*sleep / chill / steady / hype / beast*). Valence is a real dimension in the fused embedding and shapes DJ-mode ranking, but it isn't surfaced as a second slider yet. Or hand it to **DJ mode**, which picks the next track in real time from the last 10 events in your session (queue-adds and completions pull toward that sound, skips push away) ranked by cosine similarity over the fused embedding.
- **Plays the whole library through YouTube.** Each Spotify track is resolved to a `youtube_id` at ingest via `yt-dlp ytsearch1`; the player runs off the YouTube IFrame API. Playback state hooks into the Media Session API for Bluetooth / OS / lock-screen transport controls, and the session buffer emitting events into DJ mode's taste vector runs off the same play/next/skip transitions.

### How the vibe inference actually works

No sensor, no "how are you feeling?" prompt, no biometric anything. VibeScape infers your current listening state from **implicit feedback on the tracks it's already played you**:

1. **Every playback event becomes a signed weight on the fused embedding of the track that triggered it.** A queue-add is +1.2 (explicit, forward-looking choice), a natural completion is +0.8 (passive assent), a mid-track skip is −0.4 to −0.8 (rejection scaled by how quickly you bailed).
2. **The taste vector is a weighted, L2-normalized sum of the last 10 event embeddings** — positives pull toward what you're keeping, negatives push away from what you're bouncing off. Because each contributing track is L2-normalized *before* weighting, magnitude cancels out — only the *direction* of your accumulated taste matters. That direction is your current vibe expressed as a 788-D coordinate.
3. **Because that coordinate lives in the same space every song in your library is embedded in, "play what fits my vibe" is one lookup**: `vector_distance_cos(fused_embedding, taste_vector)` server-side on Turso, top-K by ascending distance, exclude the last 50 played + the current queue, return.

The 9 music-theoretic scalars (energy, valence, tempo, brightness…) in the fingerprint mean the taste vector drifts through **mood-space** in real time as your session evolves — this is the closest the system comes to "detecting mood." The 768-D MERT dimensions mean it *also* drifts through **texture-space** (production style, instrumentation, mix character), and the 11-D language one-hot keeps recommendations inside languages you're actually listening to. Skip a track, and within one request the coordinate has moved away from *whatever combination of mood, texture, and language* that track represented — and the next pick is whichever library track is now closest to the new coordinate.

**No age decay inside the 10-slot buffer.** The buffer *is* the recency window; older events roll off naturally. Decay was measured and dropped — at buffer size 10 it just penalized the median-age event by ~40% for no useful discrimination.

**Cold start / edge cases.** Empty buffer (fresh session) → query vector undefined → backend falls back to random pick from the candidate pool with `score=0`. Seed track has no MERT embedding (recently ingested, embedding stage hasn't run) → falls back to vibe-mode (weighted L1 over the scalar block) with `mode_used='vibe_fallback_no_seed_embedding'` so the client can tell.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Architecture

Four independently deployed components. Two client-facing paths — the online **request handler** and the offline **ingest worker** — fan into the same shared backends: a **GPU inference tier** and a **persistence tier** (relational + vector, same physical libSQL instance in two logical roles).

**Request / DJ path** (user scrubs the vibe slider or plays a track → next-track recommendation):

```
   ┌────────────────────────────┐
   │   Browser  (PWA / mobile)  │
   │   vibe slider · playback · │
   │   session-event buffer     │
   └─────────────┬──────────────┘
                 │  REST / JSON
                 ▼
   ┌────────────────────────────┐
   │   Stateless API tier       │
   │   FastAPI + UI + /similar  │
   │   (vibe mode + DJ mode)    │
   │   torch-free, scale-to-0   │
   └───┬─────────────────────┬──┘
       │                     │
       │  RPC (predict from  │  SQL + vector_distance_cos
       │   audio URL)        │   over HTTP
       ▼                     ▼
   ┌─────────────────┐   ┌───────────────────────────────────────────┐
   │  GPU inference  │   │  Persistence tier                         │
   │   MERT regressor│   │  (single hosted libSQL — two roles below) │
   │   MERT-95M enc. │   │  ┌─────────────────┐ ┌─────────────────┐  │
   │   Whisper lang  │   │  │ Relational DB   │ │ Vector DB       │  │
   │   warm ctx +    │   │  │  tracks · users │ │  track_embeds   │  │
   │   weight cache  │   │  │  · sessions ·   │ │   F32_BLOB(768) │  │
   │   volumes       │   │  │  user_tracks ·  │ │   F32_BLOB(788) │  │
   └─────────────────┘   │  │  per-stage      │ │  vector_dist_   │  │
                         │  │  status cols =  │ │  cos server-    │  │
                         │  │  ingest queue   │ │  side ranking   │  │
                         │  └─────────────────┘ └─────────────────┘  │
                         └───────────────────────────────────────────┘
```

**Ingest path** (offline worker → same GPU + persistence tiers, no API tier in the loop):

```
   ┌────────────────────────────────┐
   │   Ingest worker  (offline)     │
   │   6-stage pipeline in waves:   │
   │    preview → download →        │
   │    classify → youtube →        │
   │    language → embedding        │
   │   Claims work directly from DB:│
   │    SELECT ... WHERE            │
   │    <stage>_status='pending'    │
   │    LIMIT N;  thread-pool per   │
   │    stage; promote() cascades   │
   │    terminal failures           │
   └───┬────────────────────────┬───┘
       │                        │
       │  RPC (predict from     │  SQL: writes scalars,
       │   audio URL); worker   │   embedding blobs, and
       │   also fetches audio   │   status-column updates
       │   bytes itself         │
       ▼                        ▼
     (same GPU tier)         (same persistence tier)
```

**Playback path** (out-of-band): the browser resolves `youtube_id` from the API, then streams directly via the YouTube IFrame API. Audio bytes never touch the API tier.

The load-bearing decisions:

- **The API tier never imports `torch` and never touches audio bytes.** It dispatches a URL to the GPU tier and stores the returned scalars/embeddings. This is what keeps the always-on container tiny and cheap (512 MB / 1 CPU / scale-to-zero); GPU cost is paid per inference; state lives entirely off-box so any API instance can serve any request.
- **The database is both storage and work queue.** Per-stage status columns on the `tracks` table (`preview_status`, `download_status`, `ml_status`, `youtube_status`, `language_status`, `embedding_status` — each one of `pending / done / no_match / failed`) let the ingest worker claim work via `SELECT ... WHERE <stage>_status='pending' LIMIT N`. No external queue (SQS/Celery/Redis), no separate broker to run.
- **Vector search runs where the vectors live.** `vector_distance_cos(fused_embedding, vector32(?))` is executed server-side by the vector DB and returns only the top-K rows + track metadata (~10 KB), instead of streaming every candidate embedding to the API tier for numpy cosine (~4.7 MB). ~500× less egress per DJ request — see [Query performance](#query-performance).
- **Relational + vector DBs are the same physical instance, two logical roles.** No dual-write bookkeeping to keep track metadata and its embedding consistent — they're in the same transaction. Same `DB_BACKEND` switch (`sqlite | turso`) flips both.

Under any reasonable definition of "distributed application" this qualifies — compute is disaggregated across three execution tiers, the database is both storage *and* work queue for the ingest pipeline, and every tier can scale independently.

**Known scale limits.** Within a tier there's no horizontal scaling yet: single ingest orchestrator, `max_workers=1` on GPU stages. Two workers running at once would race on `pending` rows because there's no `claim_lease` column. That's a ~20-line addition when demand justifies it. Vector search is server-side but currently full-scan; a DiskANN index swap is roadmapped.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## ML Pipeline

### Model — MERT Regressor

**Backbone**: [`m-a-p/MERT-v1-95M`](https://huggingface.co/m-a-p/MERT-v1-95M) — a HuBERT-style self-supervised encoder pre-trained on ~160k hours of music, 95M parameters, 24 kHz input, 768-dim hidden states.

**Head**: mean-pool + max-pool the last hidden state along time, concat to a **1536-D vector** (2 × 768), then **three independent regression heads** (one per target — `danceability`, `energy`, `valence`). Each head is a shallow MLP written explicitly in `ml/src/model.py::RegressionHead`:

```
in (1536) → LayerNorm → Linear(1536, 256) → GELU → Dropout(0.2)
          → Linear(256, 1) → sigmoid → out ∈ [0, 1]
```

Heads live in an `nn.ModuleDict` keyed by target name, so `preds[:, i]` in the loss step maps 1-to-1 back to the config's target list. Sigmoid on the output means we're not clipping — the network learns to compress into `[0, 1]` where the labels already live.

**Frozen-encoder forward is wrapped in `torch.no_grad()`** during the freeze epoch (`model.py:85-89`), so no gradients or activations are cached through the 95M-parameter backbone while the heads warm up on random init. Cuts encoder-epoch memory to what the heads need.

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

- **Labels**: audio-features CSV (`ml/data/spotify_tracks.csv`) with per-track `danceability / energy / valence` — the three targets the regressor learns to reproduce. Rows with any target NaN are dropped in `build_dataframe`.
- **Audio**: 30-second `.mp3` previews downloaded via `ml/src/download_previews.py` and validated against a manifest — only rows with `status == "ok"` in `manifest.csv` **and** an on-disk file ≥ 10 kB (`dataset.py:46`) survive. Loading uses `soundfile` when available and falls back to `librosa.load`, mono-mixed, resampled to the model's 24 kHz.
- **Splits**: `GroupShuffleSplit` grouped on `artists` (train.py:47-60) so **no artist crosses train/val/test**. Two nested splits with the same seed enforce artist disjointness across all three sets — one split peels off 10 % as test, the second splits the remaining 90 % into 80 %/10 % train/val.
- **Crop**: raw audio is first truncated to 30 s (`max_duration_s`), then a **random 10 s window at train, centre 10 s at val/test** (`dataset.py:76-85`). Signals shorter than 10 s are zero-padded on the right rather than dropped.
- **Augmentation**: ±3 dB random gain at train only. Conditional peak-normalize (`peak > 1.0` → divide by peak) runs after gain to catch clip-through cases (`dataset.py:106-108`) — not applied when the augmented signal is already in-range, so it doesn't quietly rescale everything.

### Training Recipe

| Knob | Value | Rationale |
|---|---|---|
| Pre-trained backbone | `m-a-p/MERT-v1-95M` | Music-domain SSL beats generic wav2vec for MIR tasks |
| Freeze schedule | encoder frozen epoch 0, unfrozen from epoch 1 (`freeze_encoder_epochs`) | Warm up heads on random init before touching encoder; frozen forward runs under `torch.no_grad()` so encoder activations aren't cached |
| Optimizer | AdamW with **two param groups** | Encoder LR = **1e-5**, head LR = **1e-4** (10× the encoder LR because heads start from random init) |
| Weight decay | **1e-2** on both groups | Standard AdamW default; applied uniformly |
| LR schedule | Linear warmup (500 steps) → cosine decay to 0 over `total_steps` | `total_steps = (len(dl_train) // grad_accum) × max_epochs` — step-based, not epoch-based, so it survives batch/accum changes |
| Precision | `16-mixed` | Fits ~4× more batch on T4 / consumer GPU |
| Batch × Accum | 4 × 8 = **32 effective** | Small physical batch, real batch via accumulation |
| Loss | Per-head MSE, summed | Three independent `[0, 1]` regressions; per-target `val_mse_{name}` also logged each epoch |
| Early stopping | monitor `val_loss`, patience 3 | Restores best weights via `ModelCheckpoint` (`save_top_k=1`) |
| Grad clip | 1.0 | |
| Tracking | MLflow (`ml/experiments/mlruns`, `experiment_name='vibescape-mert'`) | Loss curves, LR-per-step, per-target MSE all logged |
| Best-checkpoint handling | Hard-linked (fallback: copied) to `ml/models/mert_v1.ckpt` after training | Downstream inference code always loads a stable path, not the epoch-decorated filename |
| Post-training | `trainer.test(model, ckpt_path='best')` on the held-out test split | Final test metrics logged to the same MLflow run for a single-glance report |

Reproducibility: `seed=42`, `deterministic=True` (also flips `torch.backends.cudnn.deterministic` on and `benchmark` off), split RNG seeded independently. `--fast-dev-run` and `--limit-tracks N` CLI flags for smoke runs and small-catalog debugging.

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
- **`vibe_score`** = alias of `activation` (kept for backwards compatibility with the frontend slider column). Once the library-wide z-score pass runs, the persisted `vibe_score` is overwritten with `activation_relative` so slider-percentile filtering matches the actual library distribution.
- **`activation_relative`**: library-wide z-score of `activation` so the slider gives a percentile-flat view instead of clumping in the middle of the population.

A 2×5 **mood grid** is derived from these two axes:

|  | valence < 50 | valence ≥ 50 |
|---|---|---|
| activation < 20 | sleep | sleep |
| 20–40 | melancholy | chill |
| 40–60 | moody | steady |
| 60–80 | aggressive | hype |
| ≥ 80 | beast | beast |

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Ingestion Pipeline

Ingest is split into a **fast online metadata pass** (runs inside the FastAPI request that a user's Spotify sync fires) and an **offline pipeline of seven modular stages** (a separate worker process that drains queued work). The sync-modal returns in seconds — the library becomes visible immediately with metadata — while the heavy per-track work happens out of band.

### Why async, not synchronous

The first version did everything inline: the request that added a track also resolved its preview, downloaded the audio, ran MERT, ran Whisper and searched YouTube, then returned. That worked for one track and fell apart for a library.

| | synchronous | async |
|---|---|---|
| 500-track sync | ~500 × (network + GPU) — minutes to hours, holding an HTTP connection | a few hundred ms, 2 INSERTs per new track |
| One bad track | iTunes 403 or a dead CDN link fails the whole request | that row parks at `<stage>_stage_error`; every other track proceeds |
| Retry | re-run everything, including work that already succeeded | each stage short-circuits on what is already on the row |
| GPU | contends with request threads, needs a GPU wherever the API runs | one worker, serialized, on a machine that has one |
| Rate limits | per-request, unbatchable, no backoff budget | a global lock + tunable backoff across the whole run |

The deeper problem is that the two halves have **incompatible failure models**. An HTTP request must answer in seconds and either succeeds or doesn't. Ingest is long, partial and resumable: a track can have a preview but no embedding, or a language but no video, and that is a normal intermediate state rather than an error. Forcing it into a request meant every partial failure became a 500, and every retry redid work that had already landed.

Splitting them lets each do what it is good at. The request path writes one row and returns. The pipeline owns durability: per-stage status columns record exactly how far each track got, so a crash resumes instead of restarting, and `ingestion_status` reports the aggregate.

The synchronous worker (`_ingest_track_row`) is gone. `backend/app.py` no longer imports `ml_backend`, `features` or `deezer_client` at all — the API process carries no ML dependencies.

### Phase 1 — online, synchronous (`backend/app.py`)

`_process_track()` writes a metadata-only `tracks` row for anything genuinely new, with `ingestion_status='pending'`. The per-stage columns are left NULL — the pipeline arms them as it goes (see below). Three short-circuit outcomes:

| Condition | Bucket | Cost |
|---|---|---|
| `user_tracks` row already exists | `already_in_library` | 1 SELECT |
| `tracks` row exists globally, user not linked | `added_to_library` — reuse row, add `user_tracks` link | 1 SELECT + 1 INSERT |
| Brand new to the DB | `queued_for_analysis` — insert metadata + `user_tracks` link | 2 INSERTs |

A 500-track playlist re-sync where every track is already known completes in a few hundred milliseconds — no HTTP fetches beyond the Spotify pagination, no inference, no audio.

### Phase 2 — offline, batch (`ingest_pipeline/` + `scripts/run_ingest_v2.py`)

A background worker walks the seven stages. Each pass picks **one cohort** of tracks and carries it through every stage in order, so the same tracks advance together; stages still batch internally (I/O-bound → thread pool). Full design notes live in `ingest_pipeline/README.md`.

### The Seven Stages

Each stage lives in its own module under `ingest_pipeline/`, is gated by exactly one status column on `tracks`, and writes only its own domain columns + its own status column.

| # | Stage | File | Status column | Blocks on | Concurrency | What it does |
|---|---|---|---|---|---|---|
| 1 | **preview** | `stage_preview.py` | `preview_status` | `ingestion_status='pending'` | 2 | Provider chain resolves a `preview_url`; backfills `apple_id`, `genre`, `track_view_url`, `album`, `artwork_url`, `duration_ms`. Short-circuits when `preview_url` is already set. |
| 2 | **download** | `stage_download.py` | `download_status` | `preview_status='done'` | 8 | Fetches to `data/audio/<spotify_id>.<ext>` atomically (`.part` rename). Skips if already cached. |
| 3 | **librosa** | `stage_librosa.py` | `librosa_status` | `download_status='done'` | 3 | The DSP feature bank — 18 columns: `tempo`, `energy`, `brightness`, `bandwidth`, `rolloff`, `spectral_contrast`, `flatness`, `zcr`, `tonnetz_std`, `acousticness`, `mfcc_json`, `chroma_mean_json`, … |
| 4 | **classify** | `stage_classify.py` | `ml_status` | `librosa_status='done'` | 1 (GPU) | **One** MERT pass over the full 30 s preview with the fine-tuned checkpoint, yielding **both** the vibe scalars and the 768-d mean-pooled embedding. |
| 5 | **language** | `stage_language.py` | `language_status` | none — armed at ingest entry | 1 (no model) | **The pipeline stops here.** Classifies nothing: parks live rows at `language_status='pending'`, the single predicate a Claude Code session queries. The session reads title/artist/album, writes the tag and the `fuse_status='pending'` cascade (`ingest_pipeline/README.md` § Tagging languages). Whisper was removed 2026-10-10 — it mispredicted routinely on sung audio. |
| 6 | **fuse** | `stage_fuse.py` | `fuse_status` | `language_status` terminal | 4 | Builds the 788-d retrieval vector from the stored MERT vector + 9 scalars + language one-hot. Pure numpy — a corrected language tag rebuilds in ms, no GPU. |
| 7 | **youtube** | `stage_youtube.py` | `youtube_status` | all of the above | 6 | `yt-dlp ytsearch`, first hit, no embed/age check. Skips if `youtube_id` is set. **Finisher** — settles `ingestion_status='done'`. |

**Constraint-collision retry.** An UPDATE that hits the legacy `UNIQUE (user_id, apple_id)` index retries once with `apple_id / track_view_url / genre` stripped. The row's own status column always lands so the pipeline never loops on the same row.

### Arming, and the `done` invariant

A stage runs when **its own** status column reads `'pending'`. `preview` is the entry point and triggers off `ingestion_status='pending'` — the one column the app's INSERT writes literally. Every later stage is **armed** by the one before it: on success a stage sets the next stage's column to `'pending'`, in the same UPDATE as its own status.

This replaced a design that leaned on `ALTER TABLE ... DEFAULT 'pending'`. That default does not exist in Turso — its table was rebuilt from `PRAGMA table_info`'s `type` field alone, silently dropping every DEFAULT — so app-inserted rows landed NULL, `= 'pending'` matched nothing, and the pipeline idled on a full backlog while looking perfectly healthy.

Arming is not *proof* the upstream ran, though: a column also reaches `'pending'` from a migration default or a manual UPDATE. So every gate additionally spells out its real preconditions rather than trusting that it was armed.

Status vocabulary: `pending`, `done`, `no_match`, `failed`. (`whisper_done` was retired with the Whisper language stage on 2026-10-10; `fuse` and `youtube` now require `language_status='done'` exactly.) `no_match` is terminal but non-error — the stage ran and found nothing. On `failed` a stage **arms nothing** and sets `ingestion_status='<stage>_stage_error'`, so the chain stops where it broke and one `GROUP BY ingestion_status` says which stage is failing and how often.

There is no promote pass. Each stage settles `ingestion_status` itself:

```
preview  no_match  ->  'no_preview'
download no_match  ->  'no_preview'
youtube  done      ->  'done'        (the finisher)
```

**The invariant: `ingestion_status='done'` means every stage reached a successful terminal outcome.** `youtube`'s gate requires every upstream stage, spelled out rather than inferred, so nothing can be promoted past a stage that failed.

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
# One pass across all seven stages, up to 50 rows per stage per pass:
python scripts/run_ingest_v2.py --batch 50

# Loop forever with 30 s idle sleep between empty passes:
python scripts/run_ingest_v2.py --loop --batch 30 --interval 30

# Restrict to a subset of stages:
python scripts/run_ingest_v2.py --stages preview,download,classify
```

The orchestrator is a thin loop over `Stage.run_batch()` calls followed by `promote()`. Stages are stateless — swap in Modal-backed classify/language stages later by changing `ml_backend` mode without touching orchestration.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

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

- **Re-embedding** — set `ml_status='pending'` (full re-encode) or `fuse_status='pending'` (rebuild the fused vector only, no GPU) and re-run the pipeline. The stage gates handle the rest.
- **Rebuilding fused vectors** is a pipeline stage, not a script: set `fuse_status='pending'` and `stage_fuse.py` rebuilds from the stored MERT vector + current scalars + current language. No audio, no GPU — pure numpy over blobs already on hand. This is what runs after a language-tag correction.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

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
- **Language tagging.** Whisper hallucinated on musical audio (Kannada film songs often mis-tagged as `sa / km / nn`), so it was removed on 2026-10-10 and language is read from title/artist/album instead. The pipeline stops at the language stage with rows at `language_status='pending'`; a Claude Code session queries the database for them and writes the tag plus the `fuse_status='pending'` cascade, which rebuilds the language one-hot in milliseconds with no GPU because the MERT half is already stored. Interface: `ingest_pipeline/README.md` § Tagging languages. Health: `python scripts/language_tags.py --status`.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

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

**Data sync.** `scripts/_turso_pull_to_local.py` and `scripts/_sync_local_to_turso.py` are the reproducible workflow: build the catalog locally against `sqlite` (GPU work runs on a machine that has a GPU), then push metadata + embeddings to Turso when ready to ship.

The push matches on `spotify_id` and `UPDATE`s in place. It never `DROP`s, never `INSERT`s and never writes `tracks.id` — two reasons, both easy to get wrong:

- `user_tracks.track_id REFERENCES tracks(id) ON DELETE CASCADE`, and Turso runs with `foreign_keys=1`. Dropping `tracks` deletes every user's library, and a backup covering only `tracks` + `track_embeddings` cannot restore it.
- Local and prod ids have **diverged**: the pull discards `id` and lets SQLite re-autonumber, so ~58% of tracks carry a different id locally. Writing local ids into prod would silently re-point `user_tracks` at the wrong songs — worse than deletion, because nothing errors.

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

<p align="right">(<a href="#readme-top">back to top</a>)</p>

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
│   ├── README.md             # pipeline design notes: arming, invariant, cohorts
│   ├── base.py               # Stage ABC: arming, finalizes, failure handling, batching
│   ├── fused_vector.py       # the 788-d recipe: dims, weights, _build_fused
│   ├── preview_providers.py  # PreviewChain + iTunes rate limiting/backoff
│   ├── stage_preview.py      # 1. resolve preview_url
│   ├── stage_download.py     # 2. fetch preview → data/audio/<spotify_id>.<ext>
│   ├── stage_librosa.py      # 3. DSP feature bank (18 columns)
│   ├── stage_classify.py     # 4. one MERT pass → vibe scalars + 768-d embedding
│   ├── stage_language.py     # 5. Whisper language detection
│   ├── stage_fuse.py         # 6. build the 788-d retrieval vector
│   └── stage_youtube.py      # 7. ytsearch, first hit; finisher sets ingestion_status
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
│   ├── run_ingest_v2.py                    # orchestrator: one cohort, all 7 stages
│   ├── predict_ml.py                       # standalone predict wrapper
│   ├── prewarm_youtube.py                  # bulk-resolve YouTube IDs
│   ├── build_cookies_file.py               # yt-dlp cookies helper
│   ├── _turso_pull_to_local.py             # prod Turso → local sqlite
│   ├── _sync_local_to_turso.py             # local → prod; UPDATE on spotify_id, no DROP
│   ├── _dedupe_local_by_isrc.py            # merge same-recording duplicates (local)
│   ├── _dedupe_turso_by_isrc.py            # ...and on prod; re-points user_tracks first
│   ├── language_tags.py                    # what is waiting for a language tag; write tags
│   ├── _llm_verify_export.py               # SUPERSEDED (hand-driven whisper_done review)
│   ├── _llm_verify_apply.py                # SUPERSEDED (hand-driven language corrections)
│   ├── _turso_create_vector_index.py       # DiskANN index attempt (blocked)
│   ├── _turso_inspect.py                   # schema/row inspection
│   ├── _turso_verify.py                    # post-push count/schema verifier
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
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

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

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Training Your Own Model

```bash
# 1. Get labels + previews (audio-features CSV with danceability/energy/valence at ml/data/spotify_tracks.csv)
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

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Roadmap

### Shipped
- MERT-v1-95M fine-tune (danceability / energy / valence)
- Group-shuffle split by artist (no leakage)
- Modal remote-GPU dispatch + warm-container caching
- Local-vs-Modal-vs-librosa dispatcher (`ml_backend.py`)
- Whisper language detection with confidence tiers
- Vibe slider (activation) with `activation_relative` z-score normalization; 2×5 mood grid computed backend-side (activation × valence), only activation exposed as a user slider today
- Librosa feature bank with Krumhansl-Kessler valence
- Resume-safe batch jobs (append-only manifests)
- Cloud Run deployment (512 MB, torch-free, scale-to-zero)
- Turso/libSQL migration + `sqlite3`-compatible HTTP client shim
- Media Session API (Bluetooth transport controls); PWA install
- MERT-embedding-based recommender — 30 s embeddings, MERT + scalar fusion, brute-force cosine (79.2% top-1 agreement across 739 seeds)
- Client-side preview streaming with backend fallback (cuts Cloud Run egress)
- Floating draggable/resizable video panel with mini transport controls
- Two-phase ingest split — fast online sync + offline worker
- Modular ingest pipeline — seven stages, per-stage status columns, stages arm the next
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
- ~~Retire legacy monolithic `_ingest_track_row` / `run_ingest_worker.py`~~ — done; ingestion is async only
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

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contact

**Chandan Keelara**
📧 gowdakeelarashivan.c@northeastern.edu
🐙 [github.com/virtual457](https://github.com/virtual457)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- MARKDOWN LINKS -->
[contributors-shield]: https://img.shields.io/github/contributors/chandankeelara/VibeScape.svg?style=for-the-badge
[forks-shield]: https://img.shields.io/github/forks/chandankeelara/VibeScape.svg?style=for-the-badge
[stars-shield]: https://img.shields.io/github/stars/chandankeelara/VibeScape.svg?style=for-the-badge
[issues-shield]: https://img.shields.io/github/issues/chandankeelara/VibeScape.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/chandankeelara/VibeScape.svg?style=for-the-badge
[contributors-url]: https://github.com/chandankeelara/VibeScape/graphs/contributors
[forks-url]: https://github.com/chandankeelara/VibeScape/network/members
[stars-url]: https://github.com/chandankeelara/VibeScape/stargazers
[issues-url]: https://github.com/chandankeelara/VibeScape/issues
[license-url]: https://github.com/chandankeelara/VibeScape/blob/main/LICENSE
