"""ML backend dispatcher: runs MERT + Whisper either locally (dev/GPU) or via Modal (prod).

Mode is chosen by env var VIBESCAPE_ML_MODE:
    modal   -> always use Modal (errors out gracefully if tokens/deps missing)
    local   -> always run models in-process (needs torch + ckpt + optionally CUDA)
    none    -> skip ML entirely (caller falls back to librosa)
    auto    -> default. Try Modal (if MODAL_TOKEN_ID set), else local, else none.

Both public entrypoints below have the SAME signature and return value shape
regardless of which backend served the call, so callers (backend/app.py ingest,
ml/src/backfill_languages.py) don't need to care.

Local mode env knobs:
    VIBESCAPE_MERT_CKPT       (default: <repo>/ml/models/mert_v1.ckpt)
    VIBESCAPE_WHISPER_MODEL   (default: small)
    VIBESCAPE_TORCH_DEVICE    (default: cuda if available else cpu)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("vibescape.ml_backend")

_MODAL_APP_NAME = os.environ.get("MODAL_APP_NAME", "vibescape-ml")
_MODAL_FUNCTION_NAME = os.environ.get("MODAL_FUNCTION_NAME", "predict_from_url")
_MODAL_LANG_FUNCTION_NAME = os.environ.get("MODAL_LANG_FUNCTION_NAME", "predict_language_from_url")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CKPT = _REPO_ROOT / "ml" / "models" / "mert_v1.ckpt"

# Cached at module level so we don't re-resolve or re-load per track.
_mode_cache: Optional[str] = None
_modal_fn = None
_modal_lang_fn = None
_modal_lookup_attempted = False
_modal_lang_lookup_attempted = False
_local_predictor = None
_local_whisper_models: dict[str, object] = {}


# ============================================================================
# Mode selection
# ============================================================================

def _resolve_mode() -> str:
    """Return one of: 'modal', 'local', 'none'. Cached after first call."""
    global _mode_cache
    if _mode_cache is not None:
        return _mode_cache

    requested = (os.environ.get("VIBESCAPE_ML_MODE") or "auto").strip().lower()

    if requested == "none":
        _mode_cache = "none"
    elif requested == "modal":
        _mode_cache = "modal" if _modal_available() else "none"
        if _mode_cache == "none":
            log.warning("[ml_backend] VIBESCAPE_ML_MODE=modal but Modal not available")
    elif requested == "local":
        _mode_cache = "local" if _local_available() else "none"
        if _mode_cache == "none":
            log.warning("[ml_backend] VIBESCAPE_ML_MODE=local but local deps missing")
    else:  # auto
        if _modal_available():
            _mode_cache = "modal"
        elif _local_available():
            _mode_cache = "local"
        else:
            _mode_cache = "none"

    log.info("[ml_backend] mode=%s (requested=%s)", _mode_cache, requested)
    return _mode_cache


def _modal_available() -> bool:
    if not os.environ.get("MODAL_TOKEN_ID"):
        return False
    try:
        import modal  # noqa: F401
        return True
    except ImportError:
        return False


def _local_available() -> bool:
    """Local mode needs torch + a MERT checkpoint. Whisper is checked lazily
    (only needed if language prediction is called)."""
    ckpt = Path(os.environ.get("VIBESCAPE_MERT_CKPT") or _DEFAULT_CKPT)
    if not ckpt.exists():
        return False
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def is_available() -> bool:
    """True if any ML backend (modal or local) is usable."""
    return _resolve_mode() != "none"


def current_mode() -> str:
    """Return the active mode string. Useful for logs / debug endpoints."""
    return _resolve_mode()


# ============================================================================
# Modal-backed implementations
# ============================================================================

def _get_modal_fn():
    global _modal_fn, _modal_lookup_attempted
    if _modal_fn is not None:
        return _modal_fn
    if _modal_lookup_attempted:
        return None
    _modal_lookup_attempted = True
    try:
        import modal
        _modal_fn = modal.Function.from_name(_MODAL_APP_NAME, _MODAL_FUNCTION_NAME)
        log.info("[ml_backend] connected to modal %s/%s", _MODAL_APP_NAME, _MODAL_FUNCTION_NAME)
        return _modal_fn
    except Exception as e:
        log.warning("[ml_backend] modal MERT lookup failed: %s", e)
        return None


def _get_modal_lang_fn():
    global _modal_lang_fn, _modal_lang_lookup_attempted
    if _modal_lang_fn is not None:
        return _modal_lang_fn
    if _modal_lang_lookup_attempted:
        return None
    _modal_lang_lookup_attempted = True
    try:
        import modal
        _modal_lang_fn = modal.Function.from_name(_MODAL_APP_NAME, _MODAL_LANG_FUNCTION_NAME)
        log.info("[ml_backend] connected to modal %s/%s", _MODAL_APP_NAME, _MODAL_LANG_FUNCTION_NAME)
        return _modal_lang_fn
    except Exception as e:
        log.warning("[ml_backend] modal language lookup failed: %s", e)
        return None


def _modal_predict_from_url(preview_url: str) -> Optional[dict]:
    fn = _get_modal_fn()
    if fn is None:
        return None
    try:
        return fn.remote(preview_url)
    except Exception as e:
        log.warning("[ml_backend] modal MERT call failed for %s: %s", preview_url[:80], e)
        return None


def _modal_predict_language_from_url(preview_url: str, model_size: str) -> Optional[dict]:
    fn = _get_modal_lang_fn()
    if fn is None:
        return None
    try:
        return fn.remote(preview_url, model_size)
    except Exception as e:
        log.warning("[ml_backend] modal language call failed for %s: %s", preview_url[:80], e)
        return None


# ============================================================================
# Local implementations (in-process, uses local GPU if available)
# ============================================================================

def _download_to_tempfile(url: str, suffix: str = ".mp3") -> Optional[str]:
    """Fetch preview URL to a temp file. Returns path, or None on failure."""
    import tempfile
    import requests
    try:
        r = requests.get(url, timeout=30, stream=True)
        r.raise_for_status()
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return path
    except Exception as e:
        log.warning("[ml_backend] local download failed for %s: %s", url[:80], e)
        return None


def _local_torch_device() -> str:
    override = os.environ.get("VIBESCAPE_TORCH_DEVICE")
    if override:
        return override
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _get_local_predictor():
    """Load and cache the MERT Predictor from ml/src/predict.py."""
    global _local_predictor
    if _local_predictor is not None:
        return _local_predictor
    ckpt = Path(os.environ.get("VIBESCAPE_MERT_CKPT") or _DEFAULT_CKPT)
    if not ckpt.exists():
        log.warning("[ml_backend] local MERT ckpt missing: %s", ckpt)
        return None
    import sys
    ml_src = str(_REPO_ROOT / "ml" / "src")
    if ml_src not in sys.path:
        sys.path.insert(0, ml_src)
    try:
        from predict import Predictor
        device = _local_torch_device()
        _local_predictor = Predictor(str(ckpt), device=device)
        log.info("[ml_backend] local MERT loaded on %s from %s", device, ckpt.name)
        return _local_predictor
    except Exception as e:
        log.warning("[ml_backend] local MERT load failed: %s", e)
        return None


def _local_predict_from_url(preview_url: str) -> Optional[dict]:
    predictor = _get_local_predictor()
    if predictor is None:
        return None
    tmp = _download_to_tempfile(preview_url, ".mp3")
    if tmp is None:
        return None
    try:
        preds = predictor.predict(tmp)
        preds["model_version"] = "mert_v1"
        return preds
    except Exception as e:
        log.warning("[ml_backend] local MERT predict failed: %s", e)
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _ensure_ffmpeg_on_path():
    """Whisper shells out to `ffmpeg`. If not on PATH, prepend the binary
    bundled with imageio_ffmpeg (already a dep via ml/src/predict.py)."""
    from shutil import which
    if which("ffmpeg"):
        return
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_dir = os.path.dirname(ffmpeg_bin)
        current = os.environ.get("PATH", "")
        if ffmpeg_dir not in current:
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + current
            log.info("[ml_backend] prepended imageio_ffmpeg to PATH: %s", ffmpeg_dir)
        # Whisper's subprocess call uses the plain name 'ffmpeg'. On Windows the
        # bundled binary is named 'ffmpeg-win-x86_64-v7.1.exe' — create a copy
        # or symlink named 'ffmpeg.exe' so subprocess resolves it.
        base = os.path.basename(ffmpeg_bin)
        if base.lower() != "ffmpeg.exe" and base.lower() != "ffmpeg":
            target = os.path.join(ffmpeg_dir, "ffmpeg.exe" if base.lower().endswith(".exe") else "ffmpeg")
            if not os.path.exists(target):
                try:
                    import shutil as _shutil
                    _shutil.copyfile(ffmpeg_bin, target)
                    log.info("[ml_backend] created ffmpeg alias at %s", target)
                except OSError as e:
                    log.warning("[ml_backend] couldn't alias ffmpeg: %s", e)
    except ImportError:
        log.warning("[ml_backend] ffmpeg not on PATH and imageio_ffmpeg unavailable")


def _get_local_whisper(model_size: str):
    """Load and cache Whisper model by size."""
    if model_size in _local_whisper_models:
        return _local_whisper_models[model_size]
    try:
        import whisper
    except ImportError:
        log.warning("[ml_backend] openai-whisper not installed; language detection disabled locally")
        return None
    _ensure_ffmpeg_on_path()
    try:
        device = _local_torch_device()
        model = whisper.load_model(model_size, device=device)
        _local_whisper_models[model_size] = model
        log.info("[ml_backend] local Whisper '%s' loaded on %s", model_size, device)
        return model
    except Exception as e:
        log.warning("[ml_backend] local Whisper load failed: %s", e)
        return None


def _local_predict_language_from_url(preview_url: str, model_size: str) -> Optional[dict]:
    model = _get_local_whisper(model_size)
    if model is None:
        return None
    try:
        import whisper
    except ImportError:
        return None
    tmp = _download_to_tempfile(preview_url, ".mp3")
    if tmp is None:
        return None
    try:
        audio = whisper.load_audio(tmp)
        audio = whisper.pad_or_trim(audio)
        n_mels = getattr(model, "dims", None)
        n_mels = n_mels.n_mels if n_mels is not None else 80
        mel = whisper.log_mel_spectrogram(audio, n_mels=n_mels).to(model.device)
        _, probs = model.detect_language(mel)
        top3 = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:3]
        top3 = list(top3) + [("", 0.0)] * (3 - len(top3))
        return {
            "top1_lang": top3[0][0], "top1_prob": float(top3[0][1]),
            "top2_lang": top3[1][0], "top2_prob": float(top3[1][1]),
            "top3_lang": top3[2][0], "top3_prob": float(top3[2][1]),
            "model_version": f"whisper_{model_size}",
        }
    except Exception as e:
        log.warning("[ml_backend] local Whisper predict failed: %s", e)
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ============================================================================
# Public entrypoints — dispatcher
# ============================================================================

def predict_from_url(preview_url: str, timeout: float = 120.0) -> Optional[dict]:
    """Return {energy, danceability, valence, vibe_score, model_version} or None.

    Backend chosen by _resolve_mode(). timeout is honored only for Modal.
    """
    if not preview_url:
        return None
    mode = _resolve_mode()
    if mode == "modal":
        return _modal_predict_from_url(preview_url)
    if mode == "local":
        return _local_predict_from_url(preview_url)
    return None


def predict_language_from_url(preview_url: str, model_size: str = "small") -> Optional[dict]:
    """Return {top1_lang, top1_prob, ..., model_version} or None.

    Backend chosen by _resolve_mode(). model_size defaults to 'small' — override
    per-call for cheaper backfills or higher-accuracy runs.
    """
    if not preview_url:
        return None
    mode = _resolve_mode()
    if mode == "modal":
        return _modal_predict_language_from_url(preview_url, model_size)
    if mode == "local":
        return _local_predict_language_from_url(preview_url, model_size)
    return None
