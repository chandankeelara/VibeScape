# VibeScape — Architecture, ML, and Deployment

A technical companion to the README. This document covers three things in
depth: **how the system is deployed**, **how the ML model works**, and **how
audio actually reaches the listener**.

Everything here was written against the code as it stands on the
`split-ingest-metadata-sync` branch. File/line references are clickable in
most editors.

---

## Table of Contents

- [1. What the project is](#1-what-the-project-is)
- [2. System topology](#2-system-topology)
- [3. Deployment](#3-deployment)
  - [3.1 Cloud Run — the app](#31-cloud-run--the-app)
  - [3.2 Modal — the GPU workers](#32-modal--the-gpu-workers)
  - [3.3 Turso — the database](#33-turso--the-database)
  - [3.4 Configuration matrix](#34-configuration-matrix)
- [4. The ML model](#4-the-ml-model)
  - [4.1 Labels and dataset](#41-labels-and-dataset)
  - [4.2 Architecture](#42-architecture)
  - [4.3 Training recipe](#43-training-recipe)
  - [4.4 Splits and leakage control](#44-splits-and-leakage-control)
  - [4.5 Results](#45-results)
  - [4.6 Inference](#46-inference)
- [5. Audio delivery](#5-audio-delivery)
- [6. Known gaps](#6-known-gaps)

---

## 1. What the project is

Spotify removed the `/audio-features` endpoint for third-party apps in late
2024. That endpoint exposed `danceability`, `energy`, and `valence` — the
signals that make mood-based playlist construction possible.

VibeScape reconstructs those three values **from the raw 30-second preview
clip** using a fine-tuned self-supervised music transformer, then drives a
two-axis mood grid (activation × valence) that the user scrubs through to
build vibe-consistent queues.

Framed precisely, the ML task is **knowledge distillation from a black-box
model that was switched off**: the training labels are Spotify's own
published `audio_features` values, so the model learns to reproduce the
function Spotify used. That is why the predictions drop directly into the
existing UI without recalibration.

---

## 2. System topology

Three independently deployed components. None of them is on the critical
path of the others' release cycle.

```
   deploy.ps1              modal deploy modal_app.py        turso db create
        │                            │                             │
        ▼                            ▼                             ▼
  ┌───────────────┐           ┌──────────────┐            ┌────────────────┐
  │  Cloud Run    │  ──RPC──▶ │  Modal T4    │            │  Turso/libSQL  │
  │  512MB/1CPU   │           │  GPU workers │            │  (hosted)      │
  │  FastAPI + UI │  ◀────────┤  MERT+Whisper│            │                │
  │  no torch     │  ─────────────── SQL over HTTP ──────▶│                │
  └───────────────┘           └──────────────┘            └────────────────┘
         │                            │
         │                            └── fetches preview audio itself
         ▼
   YouTube IFrame API / CDN preview URLs  (playback in the browser)
```

The load-bearing decision is that **Cloud Run never imports `torch` and never
touches audio bytes**. It dispatches a URL to Modal and stores the returned
scalars. This keeps the always-on container at 512 MB and lets it scale to
zero, while GPU cost is paid only per inference.

---

## 3. Deployment

### 3.1 Cloud Run — the app

**What gets built.** [`Dockerfile`](../Dockerfile) deliberately ignores
`requirements.txt` and hardcodes a thin runtime:

```
fastapi · uvicorn[standard] · requests · modal · python-dotenv
```

It then copies `backend/`, `frontend/`, `ingest/`, `config.py`, and
`schema.sql`. The service is both API and web server — `app.py` mounts
`frontend/` as static files at `/`, with explicit routes for `login.html`,
`index.html`, and `admin.html`. There is no separate frontend deploy and no
build step.

**What is deliberately absent matters more than what is present.** No
`torch`, no `librosa`, no `yt-dlp`. Two consequences follow directly:

| Missing package | Consequence in prod |
|---|---|
| `torch` / `librosa` | `VIBESCAPE_ML_MODE=modal` is mandatory, not a preference — the librosa fallback path physically cannot run |
| `yt-dlp` | The YouTube resolver hits its `ImportError` guard and logs `[youtube] yt_dlp import failed`. Prod serves `youtube_id` **only from the DB cache**, which is why `scripts/prewarm_youtube.py` exists |

**How the deploy runs.** [`deploy/cloud-run/deploy.ps1`](../deploy/cloud-run/deploy.ps1)
is a four-phase wrapper:

1. **Build + deploy** — `gcloud run deploy vibescape --source .`. Source-based,
   so Cloud Build builds the image and pushes it to Artifact Registry; no
   local `docker build`. Flags: `us-central1`, 512Mi / 1 CPU,
   `--min-instances 0` (scale-to-zero), `--max-instances 3`, 300 s timeout,
   `--allow-unauthenticated`.
2. **Health check** — polls `/api/health` up to 8 times, 3 s apart.
3. **Cleanup** — on success only, delegates to
   [`cleanup.ps1`](../deploy/cloud-run/cleanup.ps1): prune to the 2 newest
   revisions, 2 newest images, and 1 version per secret.
4. Print the service URL.

The ordering is the safety property: a failed health check exits non-zero and
**skips cleanup**, so a broken deploy can never prune the previous good
revision.

**Upload filtering.** `.gcloudignore` governs what Cloud Build receives and is
intentionally *not* `.gitignore`. It **keeps** `config.py` and
`data/vibescape.db` (both `COPY`'d by the Dockerfile) while excluding `ml/`,
`data/audio/`, `.claude/`, scratch `_*` scripts, and `fly.toml`.

### 3.2 Modal — the GPU workers

```bash
modal deploy modal_app.py
```

Publishes the `vibescape-ml` app with two functions, both on **T4**,
`timeout=180`, `scaledown_window=300` for warm-container reuse:

| Function | Job |
|---|---|
| `predict_from_url(preview_url)` | MERT → `{danceability, energy, valence, vibe_score, model_version}` |
| `predict_language_from_url(preview_url, model_size)` | Whisper → top-1 language, persisted only when `prob ≥ 0.2` |

Two persistent volumes (`vibescape-hf-cache`, `vibescape-whisper-cache`) hold
the HuggingFace and Whisper weight caches so cold starts don't re-download
them. Modal fetches the audio **server-side** from the preview URL.

This deploys on its own cadence — retraining and republishing the model
requires no Cloud Run redeploy, and vice versa.

### 3.3 Turso — the database

```bash
turso db create vibescape
turso db show vibescape --url      # → TURSO_DATABASE_URL
turso db tokens create vibescape   # → TURSO_AUTH_TOKEN
```

Cloud Run's filesystem is ephemeral tmpfs, so state has to live off-box.
[`docker-entrypoint.sh`](../docker-entrypoint.sh) branches on `DB_BACKEND`:
on `turso`/`libsql` it skips the local SQLite seed entirely; otherwise it
seeds `data/vibescape.db` from the image on first boot (the local-dev path).

`backend/db_client.py` implements a `sqlite3`-compatible
connection/cursor/row shim over Turso's raw Hrana HTTP pipeline, written
because the official client was unreliable. Every call site keeps its plain
`sqlite3` API, and `DB_BACKEND` switches the whole application between a
local file and a remote database with no other code changes.

### 3.4 Configuration matrix

| Kind | Mechanism | Values |
|---|---|---|
| Non-secret | `--set-env-vars` | `VIBESCAPE_ML_MODE=modal`, `DB_BACKEND=turso`, `SPOTIFY_REDIRECT_URI` |
| Secret | `--set-secrets` → Secret Manager | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` |

Secrets are mounted as env vars at runtime and never enter an image layer.
The Cloud Run runtime service account needs `roles/secretmanager.secretAccessor`
on each; the loop is in [`deploy/cloud-run/README.md`](../deploy/cloud-run/README.md).

---

## 4. The ML model

### 4.1 Labels and dataset

Labels are Spotify's own `audio_features` values from a 114k-track,
125-genre dataset.

| Stage | Artifact | Count |
|---|---|---|
| Source labels | `ml/data/spotify_tracks.csv` | 114,000 |
| Preview download attempts | `ml/data/manifest.csv` | 89,741 |
| Previews on disk | `ml/data/previews/*.mp3` | 87,029 |
| After `status == ok` join + size/NaN filter | training frame | ~87,000 |

`build_dataframe()` in [`ml/src/dataset.py`](../ml/src/dataset.py) performs
the join: inner-merge tracks against manifest rows with `status == "ok"`, map
each `track_id` to `previews/{id}.mp3`, drop files missing or under 10 KB, then
drop rows with a NaN in any target.

Those previews were downloaded from `p.scdn.co`, Spotify's preview CDN,
before it went dark. **This corpus is not reproducible today** — which is
precisely why the runtime ingest path needs its iTunes/Deezer preview cascade.

### 4.2 Architecture

```
30s mp3 → mono, 24 kHz → 10s crop
              │
              ▼
   Wav2Vec2FeatureExtractor (MERT's own)
              │
              ▼
   MERT-v1-95M encoder  (m-a-p, trust_remote_code=True)
              │  last_hidden_state  [B, T, 768]
              ├── mean-pool over time  ┐
              ├── max-pool over time   ┴→ concat → [B, 1536]
              ▼
   3 independent RegressionHeads
   LayerNorm → Linear(1536→256) → GELU → Dropout(0.2) → Linear(256→1) → sigmoid
              │
              ▼
   {danceability, energy, valence} ∈ [0,1]
```

Defined in [`ml/src/model.py`](../ml/src/model.py). Three choices worth
naming:

- **Mean + max pooling, concatenated.** Mean captures average texture; max
  captures peak events — transients, drops. Doubles head input to 1536 for
  negligible cost.
- **Sigmoid output.** Matches Spotify's bounded 0–1 scale exactly. No
  clipping, no out-of-range predictions.
- **One head per target**, not a single `Linear(1536→3)`. Each target gets its
  own normalization and capacity, at ~3× head parameters — trivial beside a
  95M-parameter encoder.

Loss is the **unweighted sum of three per-target MSEs**.

### 4.3 Training recipe

From [`ml/configs/default.yaml`](../ml/configs/default.yaml) and
`configure_optimizers`:

| Knob | Value | Rationale |
|---|---|---|
| `lr_encoder` / `lr_head` | 1e-5 / 1e-4 | 10× discriminative LR — don't wreck pretrained features |
| `freeze_encoder_epochs` | 1 | Epoch 0 trains heads only, then unfreezes |
| Schedule | 500-step linear warmup → cosine decay | per-step `LambdaLR` |
| Optimizer | AdamW, `weight_decay=1e-2` | |
| Batch | 4 × `grad_accum` 8 | effective batch 32 on a consumer GPU |
| Precision | `16-mixed`, `gradient_clip_val=1.0` | |
| Epochs | max 10, early-stop patience 3 | |
| Determinism | `seed: 42`, `deterministic: true` | |

The freeze-then-unfreeze schedule is the detail most easily gotten wrong: a
randomly-initialized head emits large early gradients that would otherwise
scramble MERT's pretrained representations within the first few hundred steps.

**Augmentation** (train split only): random 10 s crop position and ±3 dB
random gain. Val, test, and inference all use the **center** crop, so
evaluation is deterministic.

**Robustness:** `collate_skip_none` drops unreadable files from a batch and
returns `None` if the whole batch fails; `_step` short-circuits on `None` so a
corrupt file can't kill a run.

### 4.4 Splits and leakage control

[`ml/src/train.py`](../ml/src/train.py) runs `GroupShuffleSplit` **twice**,
grouped on the `artists` column — first carving out test (10 %), then val from
the remainder. No artist appears in two splits.

This matters enormously in music tagging. Under a random split a model
memorizes an artist's production signature and the label distribution of
their catalog, and test metrics become fiction. Most portfolio audio projects
use a random split and silently inflate their numbers.

**Honest caveat:** `artists` is a comma-joined collaborator string, so
`"Drake"` and `"Drake, Future"` hash to different groups. Collaboration tracks
can still straddle splits. Grouping on the first artist, or on an exploded
artist set, would close that gap.

### 4.5 Results

Best MLflow run (8 epochs), held-out test split:

| Target | Test MSE | RMSE |
|---|---|---|
| danceability | 0.0081 | 0.090 |
| energy | 0.0103 | 0.102 |
| valence | 0.0240 | 0.155 |

Val loss 0.0439 vs test loss 0.0423 — closely tracked, no sign of overfit.
Valence being roughly 3× harder than danceability is the expected ordering:
perceived emotional brightness is genuinely more ambiguous than rhythmic
regularity.

**These numbers are not yet sufficient to claim the model is good.** On
bounded [0,1] targets with clustered distributions, a model predicting near
the label mean earns respectable MSE while explaining almost no variance.
**R² and Spearman correlation** against the label standard deviation are
required to establish that anything beyond the prior was learned.
`ml/src/evaluate.py` is where that belongs.

### 4.6 Inference

Two paths, one set of weights:

| Path | Entry point | Notes |
|---|---|---|
| Local / dev | `Predictor` in [`ml/src/predict.py`](../ml/src/predict.py) | Loads `ml/models/mert_v1.ckpt`, center-crops 10 s, single forward pass |
| Prod | `modal_app.predict_from_url` | Same code on a T4; weights on persistent Modal volumes |

`predict.py` also has an ffmpeg fallback loader (via `imageio-ffmpeg`) for
m4a/aac/opus containers that libsndfile cannot open natively.

Finally:

```python
vibe_score = 0.55 * energy + 0.45 * danceability
```

This is a **hand-set heuristic, not learned** — the one number in the pipeline
with no training signal behind it. Worth knowing when explaining the mood grid.

---

## 5. Audio delivery

Playback and ML use audio differently, and conflating them causes confusion.

| Purpose | Source | Consumer |
|---|---|---|
| ML scoring | 30 s preview URL (iTunes / Deezer cascade) | Modal, server-side fetch |
| Preview playback | `<audio id="player">` | Browser |
| Full-track playback | YouTube IFrame API, via cached `youtube_id` | Browser |
| Premium playback | Spotify Web Playback SDK | Browser |

**Current state of the `<audio>` path.** `/api/stream/*` serves bytes from
`audio_path` on local disk, with correct HTTP Range / 206 handling. But
`backend/app.py` sets `"audio_path": None` in the prod ingest branch with the
comment *"prod doesn't store audio locally"* — so on Cloud Run that endpoint
resolves to NULL and 404s. The download-and-serve path runs **only in local
dev**; prod playback falls through to YouTube or the Spotify SDK.

**Streaming preview URLs directly instead.** `preview_url` is already stored
per track and already shipped to the client in the `_FIELDS` allowlist, and
the verify/debug path in `frontend/app.js` already streams a CDN preview URL
directly. Switching the main player is a one-line change in three places.

Two gotchas govern whether that works:

1. **CORS, and it fails silently.** The art-glow analyser calls
   `createMediaElementSource(el.player)` and routes the result through
   `ctx.destination`. A cross-origin `src` without `crossOrigin='anonymous'`
   and matching CDN headers taints the node, which then outputs **silence** —
   not an error. All audio flows through that graph, so the failure is total.
   `crossOrigin` must be set **before** `src`, and `createMediaElementSource`
   may be called only once per element.
2. **URL staleness.** A local file never expires; a CDN URL captured at ingest
   time can rotate. A re-resolve endpoint that reruns the existing
   iTunes/Deezer lookup would handle this.

The payoff: no disk writes, no `data/audio/` in the image, zero audio egress
billed through Cloud Run, and CDN edge caching that beats proxying through a
scale-to-zero container in a single region.

---

## 6. Known gaps

Ordered by value-to-effort.

1. **No R² / MAE / Spearman.** MSE alone cannot distinguish a real model from
   a mean-predictor. Highest-value ML work remaining.
2. **No baseline run logged.** The librosa feature bank is the natural
   comparison and MLflow has no run for it. *"MERT beats hand-engineered
   features by X on held-out artists"* is the claim that carries weight, and
   it cannot currently be made.
3. **No tests, no CI.** The repo has one scratch file (`_yt_match_test.py`)
   and no `.github/workflows/`.
4. **Train/inference crop mismatch.** Training sees random crops; inference
   sees only the center 10 s of a 30 s clip. Averaging 3 overlapping crops
   would likely reduce variance at no training cost.
5. **Artist grouping is string-exact.** See [§4.4](#44-splits-and-leakage-control).
6. **Dead weight in the image.** The Dockerfile unconditionally runs
   `COPY data/vibescape.db /app/seed/vibescape.db`, but with `DB_BACKEND=turso`
   the entrypoint never reads it. Every prod image carries an unused DB
   snapshot, and — because `data/` is gitignored — a clean `git clone` cannot
   build the image at all. Making that `COPY` conditional fixes both.
