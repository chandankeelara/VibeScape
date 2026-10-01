# DSP in VibeScape

This document describes the digital signal processing (DSP) that runs inside
VibeScape: what audio features we extract, why we extract them, and how they
flow into the ML models, mood axes, and the fused embedding used for DJ-mode
similarity search.

If you want the code, the two files that matter are:

- `ingest/features.py` — the full librosa feature extractor (legacy path,
  22.05 kHz, ~30 s previews from Deezer/iTunes).
- `ingest_pipeline/stage_embedding.py` — the newer pipeline stage that runs
  MERT at 24 kHz and *piggybacks* three librosa scalars off the same waveform
  so the fused vector is never fed zeros.
- `ingest/scoring.py` — how the librosa scalars collapse into the
  `activation` / `valence` mood axes.

---

## 1. Where DSP fits in the pipeline

```
                        Audio file (m4a / mp3 preview, 30 s)
                                       │
                                       ▼
                              ffmpeg → mono PCM
                                       │
                       ┌───────────────┴────────────────┐
                       ▼                                ▼
             librosa DSP pipeline             MERT-v1-95M transformer
             (ingest/features.py)             (stage_embedding.py)
                       │                                │
                       ▼                                ▼
         17 scalar/vector features             768-d audio embedding
         (tempo, MFCC, chroma, …)              (last_hidden_state.mean)
                       │                                │
                       ▼                                │
             ingest/scoring.py                          │
             activation + valence                       │
             (0..100 mood axes)                         │
                       │                                │
                       └────────────┬───────────────────┘
                                    ▼
                        _build_fused (stage_embedding.py)
                        [ 0.55·L2(MERT_768)
                        | 0.25·L2(9 scalars normalized)
                        | 0.20·onehot(top-10 languages + other) ]
                                    │
                                    ▼
                        788-d fused_embedding, L2-normalized
                                    │
                                    ▼
                        Turso: track_embeddings.fused_embedding
                                    │
                                    ▼
                        DJ-mode cosine similarity search
```

DSP is doing three jobs here:

1. **Direct listener-facing features** — tempo, brightness, energy, mode —
   surfaced on the frontend and used by rule-based filters.
2. **Input to the mood axes** — `activation` and `valence` (0..100) that
   drive the mood slider and mood grid (`sleep / chill / melancholy /
   steady / moody / hype / aggressive / beast`).
3. **Handcrafted dimensions of the fused embedding** — MERT gives us
   learned semantics, but a 768-d transformer embedding does not tell you
   "this is 128 BPM" or "this is major-key" in a linearly-separable way.
   The scalar tail of the fused vector re-injects those DSP-derived
   properties so cosine similarity in the fused space respects them.

---

## 2. Preprocessing (before any features are computed)

Every preview URL goes through the same normalization:

| Step               | Where                                | Reason |
|--------------------|--------------------------------------|--------|
| Download preview   | `download_preview` (`features.py:27`) | Providers stream m4a/mp3. |
| Transcode → WAV    | `_to_wav` via `imageio_ffmpeg`       | libsndfile can't open m4a/aac reliably. |
| Downmix to mono    | `-ac 1`                              | We do not model stereo; halves compute. |
| Resample to 22 050 Hz (legacy) / 24 000 Hz (MERT path) | ffmpeg `-ar`, or `librosa.resample` | librosa defaults + MERT's pretrained sample rate. |
| Clip to 30 s       | `MAX_DURATION_S = 30`                | Previews are 30 s anyway; caps GPU memory for MERT. |
| Peak-normalize     | `y = y / peak if peak > 1.0`         | Prevents ffmpeg int→float overshoot. |
| Reject < 1 s clips | `if len(y) < target_sr: return None` | Not worth embedding. |

Sampling-rate choice matters for MFCC/centroid frequency ranges. We use
22.05 kHz in the legacy path (Nyquist ≈ 11 kHz, covers the musically
important band and matches librosa docs) and 24 kHz on the MERT path
because MERT-v1-95M was pretrained at 24 kHz. The small drift is fine for
the three scalars we recompute (`tempo`, `brightness`, `acousticness`);
they are used as fused-vector fill-ins, not for training.

---

## 3. Features we extract

All extraction happens in `extract_full(y, sr)` in `ingest/features.py`.
The returned dict has 17 keys, listed below with the DSP concept, the
librosa call, and what we use it for downstream.

### 3.1 Rhythm / temporal

| Feature | librosa call | What it captures | Used by |
|---|---|---|---|
| `tempo` | `librosa.beat.beat_track` | Estimated BPM from onset autocorrelation. | Mood axes (`activation`), fused vector, DJ tempo-match filter. |
| `tempo_stability` | `1 / (std(librosa.beat.plp) + eps)` | How steady the pulse is (PLP = predominant local pulse). High = metronomic (EDM), low = rubato (folk/vocal). | `dance_n` in activation. |
| `onset_rate` | `mean(librosa.onset.onset_strength)` | Density of note onsets. | Activation (fast rhythm feel). |
| `zcr` | `librosa.feature.zero_crossing_rate` | Zero-crossing rate — proxy for noisiness / high-frequency content, classic percussion vs. tone discriminator. | Persisted; available for retraining. |

### 3.2 Energy / dynamics

| Feature | librosa call | What it captures | Used by |
|---|---|---|---|
| `energy_mean` | `mean(librosa.feature.rms(y))` | Root-mean-square envelope — perceived loudness proxy. | Activation (30 % weight — the biggest single term). |
| `energy_std` | `std(librosa.feature.rms(y))` | Dynamic range — quiet-loud contrast (crescendos, drops). | Activation (`dyn_n`, 5 %). |

### 3.3 Spectral shape (from STFT)

librosa internally computes an STFT (Short-Time Fourier Transform, i.e.
overlapping FFT windows) and then aggregates the magnitude spectrogram
into these scalar descriptors:

| Feature | librosa call | What it captures | Used by |
|---|---|---|---|
| `brightness` | `mean(librosa.feature.spectral_centroid)` | Centroid of the spectrum, in Hz — "center of mass" of brightness. | Activation (`bright_n`), fused vector (`brightness`), UI "brightness" chip. |
| `bandwidth` | `mean(librosa.feature.spectral_bandwidth)` | Weighted spread around the centroid — how wide the spectrum is. | Persisted for future rerankers. |
| `rolloff` | `mean(librosa.feature.spectral_rolloff)` | Frequency below which N % (default 85 %) of energy lives — separates bright/dark tracks. | Persisted. |
| `spectral_contrast` | `mean(librosa.feature.spectral_contrast)` | Peak-to-valley ratio per octave band. High = tonal harmonic music, low = broadband noise. | Valence (15 %). |
| `flatness` | `mean(librosa.feature.spectral_flatness)` | Wiener entropy — 1.0 = white noise, 0.0 = pure tone. Distinguishes noisy vs. pitched content. | Valence (20 %, inverted). |

### 3.4 Timbre — MFCC

MFCCs (Mel-Frequency Cepstral Coefficients) are the workhorse timbre
descriptor. Pipeline inside `librosa.feature.mfcc`:

```
     y (waveform)
           │
           ▼
        STFT → power spectrogram
           │
           ▼
    Mel filterbank (128 bands, perceptual)
           │
           ▼
        log(·)                      ← log-power on the mel scale
           │
           ▼
        DCT-II                       ← decorrelates the log-mel bands
           │
           ▼
     take first 13 coefficients      ← n_mfcc=13
```

Coefficient 0 is roughly overall energy; higher coefficients capture
progressively finer spectral-envelope detail (formant structure,
"vowel" of the instrument).

VibeScape stores:

- `mfcc_mean` — length-13 vector, per-coefficient mean over the clip
  (persisted as JSON; useful for future rerankers and t-SNE plots).
- `timbre_variability` — `mfcc.std(axis=1).mean()` — how much the timbre
  moves during the clip. High = variety (rap flows, instrument
  switches), low = flat texture (drones, single-instrument loops).

### 3.5 Tonal / harmonic

| Feature | librosa call | What it captures | Used by |
|---|---|---|---|
| `chroma_mean` | `mean(librosa.feature.chroma_cens, axis=1)` (fallback: `chroma_stft`) | 12-bin pitch-class energy (C, C#, D, …, B), rotation-invariant to key. | Krumhansl-Kessler key estimation → `valence_mode`. |
| `valence_mode` | `krumhansl_major_minor(chroma)` (custom, `features.py:60`) | Correlate the mean chroma against all 12 rotations of the KK major and minor templates; return `max_major_corr − max_minor_corr`, clipped to [−1, +1]. Positive = major-like (brighter), negative = minor-like. | Valence (50 % — the dominant term). |
| `tonnetz_std` | `std(librosa.feature.tonnetz)` | Tonal centroid (fifth / minor-third / major-third circles). Standard deviation across the clip = how much harmony *moves*. | Persisted. |

The chroma → KK-template correlation is one of the few pieces of DSP in
this repo that is *not* a stock librosa call — it's an explicit
implementation of Krumhansl's 1990 music-cognition model. Templates
live at `features.py:17-23` and the rotation loop at `features.py:78-86`.

### 3.6 Source separation — HPSS

`librosa.effects.hpss` decomposes the spectrogram into two components:

- **Harmonic** — content that is stable along the time axis of the
  spectrogram (sustained pitched instruments, vocals).
- **Percussive** — content that is stable along the frequency axis
  (broadband transients — drums).

We take the RMS energy of each part and compute:

```
acousticness = h_energy / (h_energy + p_energy + eps)
```

Values near 1.0 mean the track is mostly harmonic (unplugged / vocal /
strings); near 0.0 means it is drum-driven. This is a rough proxy for
Spotify's "acousticness" attribute — good enough to be useful, cheap
enough to compute inline.

### 3.7 Feature summary

```
17 scalar/vector fields per track:
    tempo, tempo_stability, onset_rate,
    energy_mean, energy_std,
    brightness, bandwidth, rolloff, spectral_contrast, flatness, zcr,
    mfcc_mean (13-vec), timbre_variability,
    chroma_mean (12-vec), valence_mode, tonnetz_std,
    acousticness
```

All 17 are persisted on the `tracks` row so future ML iterations can
retrain without recomputing DSP.

---

## 4. Mood axes: DSP → `activation` / `valence`

`ingest/scoring.py::compute_axes` maps the raw features into two
perceptual 0..100 axes. Each raw feature is first squashed into [0, 1]
by a hand-picked linear range, then a weighted sum produces the axis.

### Activation (energy / danceability / drive)

```
activation = 100 · (
    0.30 · energy_n          # RMS mean, 0..0.15
  + 0.25 · tempo_n           # (BPM − 60) / 120
  + 0.20 · dance_n           # tempo_stability · onset_rate / 10
  + 0.10 · onset_n           # onset_rate / 3
  + 0.10 · bright_n          # (centroid − 500) / 3500
  + 0.05 · dyn_n             # energy_std · 8
)
```

Energy dominates, tempo and rhythm-steadiness (`dance_n`) follow.
Brightness is a small contributor — bright tracks tend to feel more
energetic even at similar BPM.

### Valence (brightness of mood / major-ness)

```
valence = 100 · (
    0.50 · valence_n         # (KK_major_minor + 1) / 2
  + 0.20 · (1 − flatness_n)  # more tonal = happier
  + 0.15 · contrast_n        # more spectral contrast = richer
  + 0.15 · (1 − 0.5·acoustic_n) # acoustic tracks slightly darker
)
```

Krumhansl-Kessler major/minor dominates. The other terms are
tie-breakers.

### Mood grid

`mood_label(activation, valence)` crosses activation buckets with a
valence gate (≥50 vs. <50):

```
activation < 20                 → sleep
20 ≤ activation < 40            → chill (val ≥ 50) | melancholy
40 ≤ activation < 60            → steady (val ≥ 50) | moody
60 ≤ activation < 80            → hype (val ≥ 50) | aggressive
activation ≥ 80                 → beast
```

This is what the frontend mood grid renders. Every one of those labels
is ultimately a function of librosa features run over 30 s of preview
audio.

---

## 5. DSP inside the fused embedding

The fused 788-d embedding (`FUSED_MODEL_VERSION = "fused_v1_mert_scalar_lang"`)
is:

```
fused = L2( [ 0.55 · L2(MERT_768)
            | 0.25 · L2(scalars_9)
            | 0.20 · lang_onehot_11 ] )
```

The scalar block is 9 dimensions, each normalized to [0, 1] via
`_norm_scalar` (`stage_embedding.py:82`):

```
SCALAR_COLS = [
    "energy_pred",        # ML head (0..1)
    "danceability_pred",  # ML head (0..1)
    "valence_pred",       # ML head (0..1)
    "vibe_score",         # activation (0..100)
    "activation",         # (0..100)
    "valence",            # (0..100)
    "acousticness",       # librosa HPSS (0..1)
    "tempo",              # librosa beat_track (40..220)
    "brightness",         # librosa spectral_centroid (0..8000 Hz)
]
```

Six of these nine dimensions are directly DSP-derived (`vibe_score`,
`activation`, `valence`, `acousticness`, `tempo`, `brightness`), and
the three `*_pred` heads are trained on top of DSP features too. So
roughly a quarter of the fused vector's energy comes from librosa DSP —
enough to make similarity in that space respect tempo, brightness, and
mode, but not enough to override MERT's learned semantics.

### The piggyback trick

`_extract_librosa_scalars` (`stage_embedding.py:119`) recomputes just
three scalars — `tempo`, `brightness`, `acousticness` — from the *same*
waveform MERT is embedding, at 24 kHz. Why:

- The old ingest path stored these on `tracks` alongside 14 other
  features. New pipeline may run on tracks that were embedded before
  those columns existed.
- Without values, `_norm_scalar` returns 0.0 and 3 dims of the fused
  vector are dead. Recomputing costs almost nothing (audio is already
  in memory) and keeps every fused embedding on the same distribution.
- Any failure is non-fatal (`try/except` around each call): we log a
  warning and fall through to zero, matching the behavior of the
  backfill script.

---

## 6. Design notes / gotchas

- **STFT frame settings** — everywhere we use librosa defaults
  (`n_fft=2048`, `hop_length=512` at 22.05 kHz ≈ 93 ms window, 23 ms
  hop). Fine for feature extraction; would matter more if we were doing
  onset detection for beat-grid alignment.
- **Chroma variant** — `chroma_cens` (Chroma Energy Normalized) is
  preferred over `chroma_stft` because CENS is smoothed over time and
  quantized, which makes the KK template correlation more stable on
  short 30-s clips. We fall back to `chroma_stft` if CENS raises.
- **Sample rate consistency** — 22.05 kHz in `features.py`, 24 kHz in
  `stage_embedding.py`. Only the three piggyback scalars are computed
  at 24 kHz; the *full* 17-feature dict is always 22.05 kHz. Do not
  compare `tempo` values across pipelines assuming exact equivalence —
  they are close but not identical.
- **No key detection is persisted** — we compute the KK correlation
  purely to derive `valence_mode` (a scalar). If we ever want to
  surface "Song is in E minor" on the UI, we already have the argmax
  rotation index inside `krumhansl_major_minor`; expose it there.
- **HPSS is expensive** — the harmonic/percussive median-filtering step
  is one of the slower calls in the pipeline. It's the reason ingest
  spends more time on `acousticness` than on any other single feature.

---

## 7. TL;DR

VibeScape's DSP layer is a librosa pipeline that turns each 30 s preview
into 17 interpretable features covering rhythm (tempo, onsets), energy
(RMS), spectral shape (centroid, bandwidth, rolloff, contrast, flatness),
timbre (13 MFCC + variability), tonal content (12 chroma bins + KK
major/minor + tonnetz), and harmonic-vs-percussive balance (HPSS →
acousticness). Those features feed three consumers:

1. The frontend, directly (tempo, brightness, mood labels).
2. `compute_axes` → the `activation` / `valence` mood-grid coordinates.
3. The 9-scalar tail of the 788-d fused embedding used by DJ mode.

MERT provides the learned semantics; librosa provides the interpretable
axes and the DSP-derived dimensions that keep tempo, brightness, and
mode legible inside the similarity space.
