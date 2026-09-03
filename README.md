<a id="readme-top"></a>

<div align="center">
  <h3 align="center">🎧 VibeScape — Audio-ML Music Player</h3>
  <p align="center">
    Fine-tuned <strong>MERT</strong> transformer regresses Spotify's <em>danceability / energy / valence</em> from raw audio, Whisper adds language detection, and a librosa feature bank supplies a fully-interpretable fallback. Predictions drive a two-axis mood grid the user can scrub through in the browser.
    <br/>
    <a href="#getting-started">Quick Start</a>
    ·
    <a href="#ml-pipeline">ML Pipeline</a>
    ·
    <a href="#architecture">Architecture</a>
  </p>
</div>

## 📋 Table of Contents 

- [About](#about-the-project)
- [Why This Exists](#why-this-exists)
- [Ingestion Pipeline](#ingestion-pipeline)
  - [Metadata Discovery](#1-metadata-discovery)
  - [Dedup & User-Track Linking](#2-dedup--user-track-linking)
  - [Preview-URL Cascade](#3-preview-url-cascade)
  - [Two-Path Scoring Handoff](#4-two-path-scoring-handoff)
  - [Persistence](#5-persistence)
  - [Batch / Backfill Modes](#batch--backfill-modes)
- [ML Pipeline](#ml-pipeline)
  - [Model — MERT Regressor](#model--mert-regressor)
  - [Data & Splits](#data--splits)
  - [Training Recipe](#training-recipe)
  - [Whisper Language Head](#whisper-language-head)
  - [Librosa Feature Bank (Baseline)](#librosa-feature-bank-baseline)
  - [Vibe Scoring](#vibe-scoring)
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

The ingest layer turns a Spotify playlist URL into a fully-scored, deduplicated set of rows in the `tracks` table. Every step is idempotent, resume-safe, and content-addressed so re-ingesting the same playlist is effectively free.

```
    Spotify playlist URL / OAuth
                │
                ▼
   ┌─────────────────────────────┐
   │ 1. spotify_library.py        │  metadata + preview_url + ISRC
   │    (paginate playlist items) │
   └─────────────┬───────────────┘
                 │  per track
                 ▼
   ┌─────────────────────────────┐
   │ 2. Dedup & user-track link   │  ─── skip:already_ingested
   │    (spotify_id lookup)       │  ─── ok:linked_existing (reuse row)
   └─────────────┬───────────────┘
                 │
                 ▼
   ┌─────────────────────────────┐
   │ 3. Preview-URL cascade       │
   │    spotify_preview           │
   │        │  (empty since 2024) │
   │        ▼                     │
   │    iTunes term-search        │
   │        │                     │
   │        ▼                     │
   │    Deezer  ISRC   lookup     │  regional catalog coverage
   │        │                     │
   │        ▼                     │
   │    Deezer  term-search       │
   │        │                     │
   │        ▼                     │
   │    skip:no_preview           │
   └─────────────┬───────────────┘
                 │  audio_url
                 ▼
   ┌─────────────────────────────┐
   │ 4. ml_backend.is_available() │
   │        yes → MERT + Whisper  │ (Modal / local GPU — remote fetch)
   │        no  → librosa fallback│ (download → extract_full → scoring)
   └─────────────┬───────────────┘
                 │
                 ▼
   ┌─────────────────────────────┐
   │ 5. _upsert(tracks)           │  keyed on spotify_id ⨁ apple_id
   │    + user_tracks(user,track) │
   └─────────────────────────────┘
```

### 1. Metadata Discovery

`ingest/spotify_library.py` handles both entry points:

- **Playlist URL** — accepts every shape Spotify emits: `open.spotify.com/playlist/{id}`, locale-prefixed variants (`intl-en/`), embed URLs, `spotify:playlist:` URIs, or a raw 22-char base62 ID. Regex-parsed by `parse_playlist_id()`.
- **OAuth login** — user's saved library / playlists via Authorization Code flow (backend handles token refresh).

For each track the API returns metadata (`name`, `artists[]`, `album`, `duration_ms`, `artwork_url`, `external_ids.isrc`, `preview_url`). The pagination loop yields tracks incrementally so the frontend can stream progress updates via a job ID.

### 2. Dedup & User-Track Linking

Tracks are **global**, not per-user. The `tracks` table is keyed on `spotify_id` (with `apple_id` as a secondary unique index); the `user_tracks` join table connects users to tracks with `(user_id, track_id, source, added_at)`.

`_process_track()` short-circuits three ways:

| Condition | Return | Cost |
|---|---|---|
| `user_tracks` row exists | `skip:already_ingested` | 1 SQL lookup |
| `tracks` row exists globally, user not linked | `ok:linked_existing` — reuse audio + features, add `user_tracks` row | 1 SQL lookup + 1 insert |
| Not seen before | Proceed to preview cascade | full path |

This means an already-analyzed 500-track playlist re-ingests in a few hundred milliseconds — no downloads, no inference.

### 3. Preview-URL Cascade

Spotify silently emptied `preview_url` for the majority of tracks in November 2024, so the ingest layer degrades through a four-source cascade. Every hop is logged with `hit=True/False` and the resulting **classification source** is persisted on the row so later analytics can attribute where each audio sample actually came from.

| Order | Source | Endpoint | When it helps | `classification_source` |
|---|---|---|---|---|
| 1 | Spotify | `track.preview_url` (from playlist item payload) | Rare, but free when present | `spotify_preview` |
| 2 | iTunes | `iTunes Search API` (term = `"{title} {artist}"`) | US/UK/major-label catalog | `itunes_term_search` |
| 3 | Deezer | `GET /track/isrc:{isrc}` | Precise, works when we have ISRC | `deezer_isrc` |
| 4 | Deezer | `GET /search?q={title} {artist}` | Broad — especially good for **Indian / Punjabi / Tamil / other regional catalogs** where iTunes' western-biased search misses | `deezer_search` |
| — | — | All misses → `skip:no_preview` | Track saved as metadata-only or skipped | `none` |

Notes:
- **iTunes ISRC lookup is intentionally skipped**. Apple's undocumented `/lookup?isrc=` endpoint returns 0 hits for essentially every ISRC now; probing it costs ~1s/track for nothing.
- **Deezer normalizes to iTunes shape**. `deezer_client._to_itunes_shape()` maps `preview/title/artist.name/album.cover_medium` → `previewUrl/trackName/artistName/artworkUrl100` so the downstream `_process_track()` merge slot doesn't need to branch on source.
- **Shared audio** — if the same `spotify_id` was ingested by another user earlier and the file still exists on disk, the local path is reused instead of re-downloading (`shared_row` lookup).

### 4. Two-Path Scoring Handoff

Once an `audio_url` is resolved, `ml_backend.is_available()` picks between two entirely disjoint scoring paths:

**ML path (Modal / local GPU)**
- `predict_from_url(audio_url)` — Modal downloads the preview server-side, runs MERT, returns `{energy, danceability, valence, vibe_score, model_version}`.
- `predict_language_from_url(audio_url)` — Whisper runs on the same clip; the top-1 language is persisted only if `prob ≥ 0.2` (below that Whisper is guessing on instrumental audio).
- **Prod (Fly VM) never touches audio bytes locally** — the 512 MB torch-free VM just hands the URL to Modal.

**Librosa path (dev / offline)**
- Downloads the audio to a tempfile, runs `features.extract()` for the 15-feature bundle (tempo, RMS, HPSS, MFCC, CENS chroma, Krumhansl-Kessler valence, etc.), then `scoring.compute_axes()` for activation/valence, then `scoring.mood_label()` for the mood grid label.
- Saves the audio locally at `data/audio/{spotify_id}.{mp3|m4a}` so subsequent recomputes are free.

Both paths converge on the same `activation ∈ [0, 100]`, `valence ∈ [0, 100]`, and `mood` string — the frontend has no idea which one produced them.

### 5. Persistence

`_upsert()` writes to `tracks` (INSERT or UPDATE on existing row, keyed on `spotify_id` first, `apple_id` second) with the full column set: metadata + all librosa scalars + chroma JSON + `activation`/`valence`/`activation_relative`/`vibe_score`/`mood` + ML prediction columns (`energy_pred`, `danceability_pred`, `valence_pred`, `vibe_score_ml`, `model_version`) + language columns (`language`, `language_confidence`, `language_top3_json`, `language_model_version`, `language_predicted_at`) + `classification_source` provenance.

The `user_tracks` join row is written last with `INSERT OR IGNORE`, so re-runs are idempotent.

### Batch / Backfill Modes

The ingest CLI at `ingest/ingest.py` exposes three offline modes for maintenance:

```bash
# 1. Backfill missing audio files for existing DB rows (dev only).
python ingest/ingest.py --backfill

# 2. Match tracks to Spotify IDs via ISRC → title/artist fallback.
python ingest/ingest.py --match-spotify

# 3. Re-run librosa extract_full on every locally cached audio file,
#    then library-wide z-score normalization for activation_relative.
python ingest/ingest.py --recompute-features
python ingest/ingest.py --recompute-features --limit 50 --force   # debug
```

`--recompute-features` is fully idempotent — it skips tracks whose v2 columns are already populated unless `--force` is passed. Progress is written to `data/recompute_progress.json` after every track so external pollers (or the frontend admin panel) can watch state without parsing stdout. After the per-track pass finishes it runs a **library-wide z-score normalization** on `activation`:

```
activation_relative = clamp(50 + ((activation − μ) / σ) × 15, 0, 100)
```

so `vibe_score` (which the frontend slider drives) is a well-distributed percentile view of the user's actual library instead of clumping in the middle of the raw scale.

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
              SQLite (schema.sql — tracks table)
```

**Two-tier deploy**: the playback API lives on a **512 MB Fly.io VM** with only FastAPI + requests + Modal client (no torch, no librosa, no ffmpeg beyond ingest). All heavy ML happens on **Modal T4 GPUs** with warm-container reuse (`scaledown_window=300`) and persistent volumes for the MERT and Whisper weight caches. Cold sync of a 1000-track playlist is dominated by preview download, not inference.

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

### Backend → Fly.io

```bash
fly launch      # first time only, uses fly.toml
fly deploy
fly secrets set SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... \
                MODAL_TOKEN_ID=ak-... MODAL_TOKEN_SECRET=as-... \
                VIBESCAPE_ML_MODE=modal
```

The Fly VM is deliberately tiny (512 MB, 1 shared CPU) — it never runs torch. The `Dockerfile` seeds a starter SQLite DB on first boot via `docker-entrypoint.sh`.

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
- **SQLite** — track store, users, sessions (see `schema.sql`)
- **yt-dlp** — resolve Spotify tracks → YouTube video IDs for playback
- **requests** — Spotify Web API, iTunes/Deezer preview fallback

### Frontend
- Vanilla **JavaScript** + **HTML** + **CSS** — no framework, no build step
- **YouTube IFrame Player API** — playback
- **Media Session API** — Bluetooth / OS transport controls

### Infra
- **Docker** + **Fly.io** — 512 MB shared VM, iad region
- **Modal** — remote T4 GPU inference, warm containers, persistent volume caches

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Project Structure

```
VibeScape/
├── backend/
│   ├── app.py                # FastAPI: OAuth, ingest, playback, library API
│   └── db.py                 # SQLite bootstrap + connection helpers
│
├── frontend/
│   ├── index.html            # single-page player UI
│   ├── app.js                # mood-slider, filter, YouTube playback
│   └── style.css
│
├── ingest/
│   ├── spotify_library.py    # Spotify Web API client
│   ├── spotify_matcher.py    # Spotify ⇄ iTunes/Deezer matching
│   ├── deezer_client.py      # Deezer preview fallback (30 s clips)
│   ├── itunes_client.py      # iTunes Search preview fallback
│   ├── features.py           # librosa feature bank + Krumhansl-Kessler
│   ├── scoring.py            # activation / valence / mood-grid logic
│   └── ml_backend.py         # Modal-vs-local-vs-none dispatcher
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
│   ├── predict_ml.py         # standalone predict wrapper
│   ├── prewarm_youtube.py    # bulk-resolve YouTube IDs
│   └── build_cookies_file.py # yt-dlp cookies helper
│
├── modal_app.py              # Modal deployment (predict_from_url, predict_language_from_url)
├── config.py                 # Spotify credentials (env-backed)
├── schema.sql                # SQLite schema
├── requirements.txt          # runtime deps (thin)
├── ml/requirements.txt       # training + inference deps (heavy)
├── Dockerfile                # Fly.io build
├── docker-entrypoint.sh      # DB seed on first boot
└── fly.toml                  # Fly.io app config
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
- [x] Fly.io deployment (512 MB, torch-free)
- [x] Media Session API (Bluetooth transport controls)

### In Progress 🚧
- [ ] Optuna sweeps over head-hidden / dropout / LR ratios
- [ ] Multi-crop test-time averaging (currently single centre crop)
- [ ] Genre auxiliary head (multi-task learning)

### Planned 📋
- [ ] Larger MERT (`MERT-v1-330M`) with LoRA adapters
- [ ] Contrastive similarity search (nearest-vibe recommendations)
- [ ] Per-user preference learning on skip/replay signals
- [ ] Whisper transcription for lyric-based mood cues
- [ ] Web-audio-based on-device inference (ONNX / WebGPU)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contact

**Chandan Gowda K S**
📧 gowdakeelarashivan.c@northeastern.edu
🐙 [github.com/virtual457](https://github.com/virtual457)

<p align="right">(<a href="#readme-top">back to top</a>)</p>
