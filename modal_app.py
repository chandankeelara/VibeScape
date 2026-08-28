"""
Modal deployment: MERT inference on remote GPU.

Deployed once via `modal deploy modal_app.py`. Fly backend calls
`predict_from_url` on-demand during Spotify library sync.

Usage locally:
    python -m pip install modal
    modal token new              # one-time browser auth
    modal deploy modal_app.py    # deploys to modal.com

Then set these two env vars on Fly (from `modal token new` output):
    MODAL_TOKEN_ID     (starts with ak-)
    MODAL_TOKEN_SECRET (starts with as-)

Fly's ingest hot path will import the client and call this function
with a preview URL. Modal downloads, decodes, runs MERT on a T4 GPU,
returns predictions as JSON.
"""
from __future__ import annotations

import modal

app = modal.App("vibescape-ml")

# Image: PyTorch + transformers + pytorch-lightning + ffmpeg.
# No librosa — we use ffmpeg subprocess for audio decoding (already the
# fallback path in ml/src/predict.py). Saves ~600 MB and skips numba/llvm.
image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("ffmpeg")
    .pip_install(
        "torch==2.6.0",
        "torchaudio==2.6.0",
        "transformers==4.57.6",
        "pytorch-lightning==2.6.5",
        "requests>=2.31.0",
        "numpy>=1.24",
        "openai-whisper==20240930",
    )
    # Bundle the model definition + predictor. Copied at image build time.
    .add_local_dir("ml/src", "/app/ml_src")
    # Bundle the trained checkpoint (~380 MB).
    .add_local_file("ml/models/mert_v1.ckpt", "/app/mert_v1.ckpt")
)

# Whisper downloads its own weights to ~/.cache/whisper. Persist those too so
# language-detection cold starts don't re-download the model each time.
whisper_cache = modal.Volume.from_name("vibescape-whisper-cache", create_if_missing=True)

# Persistent volume so MERT's HuggingFace weights (~380 MB from HF Hub for
# m-a-p/MERT-v1-95M) are cached across cold starts.
hf_cache = modal.Volume.from_name("vibescape-hf-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu="T4",
    volumes={"/root/.cache/huggingface": hf_cache},
    timeout=180,
    # Keep container warm for 5 min after last request to avoid cold starts
    # on batched syncs. Costs nothing when idle.
    scaledown_window=300,
)
def predict_from_url(preview_url: str) -> dict:
    """
    Download a preview clip, run MERT, return predictions.

    Returns:
        {
          "energy": 0.0-1.0,
          "danceability": 0.0-1.0,
          "valence": 0.0-1.0,
          "vibe_score": 0.0-1.0,
          "model_version": "mert_v1"
        }
    """
    import os
    import subprocess
    import sys
    import tempfile

    import numpy as np
    import requests
    import torch

    sys.path.insert(0, "/app/ml_src")
    from model import MERTVibeRegressor  # noqa: E402

    # Load the model once per container (cached across warm invocations).
    global _MODEL, _SR, _CROP
    if "_MODEL" not in globals():
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _MODEL = MERTVibeRegressor.load_from_checkpoint("/app/mert_v1.ckpt", map_location=device)
        _MODEL.eval().to(device)
        _SR = int(_MODEL.hparams.sample_rate)
        _CROP = int(10.0 * _SR)

    # Download preview to tmp file.
    fd, tmp_in = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        r = requests.get(preview_url, timeout=30, stream=True)
        r.raise_for_status()
        with open(tmp_in, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Decode via ffmpeg — pipes raw PCM to stdout. Works for m4a/mp3/aac.
        proc = subprocess.run(
            ["ffmpeg", "-i", tmp_in, "-f", "s16le", "-ac", "1",
             "-ar", str(_SR), "-loglevel", "error", "-"],
            capture_output=True,
            check=True,
        )
        y = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        if os.path.exists(tmp_in):
            try:
                os.remove(tmp_in)
            except OSError:
                pass

    # Pad / crop to fixed 10s window (matches training).
    if len(y) < _SR:
        y = np.pad(y, (0, _SR - len(y)))
    if len(y) > _CROP:
        start = max(0, (len(y) - _CROP) // 2)
        y = y[start : start + _CROP]
    else:
        y = np.pad(y, (0, _CROP - len(y)))
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 1.0:
        y = y / peak

    device = next(_MODEL.parameters()).device
    audio_t = torch.from_numpy(y).unsqueeze(0).to(device)
    with torch.no_grad():
        preds = _MODEL(audio_t).cpu().numpy()[0]

    out = {name: float(preds[i]) for i, name in enumerate(_MODEL.target_names)}
    energy = float(out.get("energy", 0.0))
    dance = float(out.get("danceability", 0.0))
    out["vibe_score"] = 0.55 * energy + 0.45 * dance
    out["model_version"] = "mert_v1"
    return out


@app.function(
    image=image,
    gpu="T4",
    volumes={"/root/.cache/whisper": whisper_cache},
    timeout=180,
    scaledown_window=300,
)
def predict_language_from_url(preview_url: str, model_size: str = "small") -> dict:
    """
    Download a preview clip, run Whisper language detection, return top-3.

    Returns:
        {
          "top1_lang": "en", "top1_prob": 0.87,
          "top2_lang": "hi", "top2_prob": 0.06,
          "top3_lang": "es", "top3_prob": 0.03,
          "model_version": "whisper_small"
        }
    """
    import os
    import tempfile

    import requests
    import whisper

    # Load and cache the Whisper model per container. Cache key by size so
    # callers can request different models without cross-contamination.
    global _WHISPER_MODELS
    if "_WHISPER_MODELS" not in globals():
        _WHISPER_MODELS = {}
    if model_size not in _WHISPER_MODELS:
        _WHISPER_MODELS[model_size] = whisper.load_model(model_size)
    model = _WHISPER_MODELS[model_size]

    fd, tmp_in = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        r = requests.get(preview_url, timeout=30, stream=True)
        r.raise_for_status()
        with open(tmp_in, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        audio = whisper.load_audio(tmp_in)
        audio = whisper.pad_or_trim(audio)
        n_mels = getattr(model, "dims", None)
        n_mels = n_mels.n_mels if n_mels is not None else 80
        mel = whisper.log_mel_spectrogram(audio, n_mels=n_mels).to(model.device)
        _, probs = model.detect_language(mel)
    finally:
        if os.path.exists(tmp_in):
            try:
                os.remove(tmp_in)
            except OSError:
                pass

    top3 = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top3 = list(top3) + [("", 0.0)] * (3 - len(top3))

    return {
        "top1_lang": top3[0][0], "top1_prob": float(top3[0][1]),
        "top2_lang": top3[1][0], "top2_prob": float(top3[1][1]),
        "top3_lang": top3[2][0], "top3_prob": float(top3[2][1]),
        "model_version": f"whisper_{model_size}",
    }


@app.local_entrypoint()
def smoke():
    """Local test: `modal run modal_app.py` — hits a known preview URL."""
    url = "https://p.scdn.co/mp3-preview/1234abcd"  # replace with a real one
    result = predict_from_url.remote(url)
    print(result)
    lang = predict_language_from_url.remote(url)
    print(lang)
