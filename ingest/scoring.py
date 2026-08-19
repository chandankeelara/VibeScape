"""
Multi-axis vibe scoring.

Two axes computed from librosa features:
  * activation (0..100): how energetic/danceable/loud/fast a track feels
  * valence    (0..100): how bright/major-key/rich a track feels

vibe_score remains as a backwards-compat alias for activation (the slider
value the frontend currently drives). Once the library-wide z-score
normalization runs, the persisted `vibe_score` is set to activation_relative
so filtering the slider by percentile buckets works across the actual
library distribution.

Mood grid crosses activation buckets with valence:
    activation < 20            -> sleep
    20 <= activation < 40      -> chill / melancholy
    40 <= activation < 60      -> steady / moody
    60 <= activation < 80      -> hype / aggressive
    activation >= 80           -> beast
"""

from __future__ import annotations

MOODS = [
    "sleep",
    "chill",
    "melancholy",
    "steady",
    "moody",
    "hype",
    "aggressive",
    "beast",
]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _feat(f: dict, key: str, default: float = 0.0) -> float:
    v = f.get(key)
    if v is None:
        # tolerate legacy feature dicts that only stored 'energy' or 'mfcc'
        if key == "energy_mean":
            v = f.get("energy")
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_axes(f: dict) -> dict:
    """Compute activation / valence / vibe_score from a feature dict."""
    tempo = _feat(f, "tempo")
    energy_mean = _feat(f, "energy_mean")
    energy_std = _feat(f, "energy_std")
    brightness = _feat(f, "brightness")
    onset_rate = _feat(f, "onset_rate")
    tempo_stability = _feat(f, "tempo_stability")
    acousticness = _feat(f, "acousticness")
    valence_mode = _feat(f, "valence_mode")
    flatness = _feat(f, "flatness")
    spectral_contrast = _feat(f, "spectral_contrast")

    tempo_n = _clamp((tempo - 60.0) / 120.0)
    energy_n = _clamp(energy_mean / 0.15)
    dyn_n = _clamp(energy_std * 8.0)
    dance_n = _clamp(tempo_stability * onset_rate / 10.0)
    bright_n = _clamp((brightness - 500.0) / 3500.0)
    onset_n = _clamp(onset_rate / 3.0)
    acoustic_n = _clamp(acousticness)
    valence_n = _clamp((valence_mode + 1.0) / 2.0)
    flatness_n = _clamp(flatness * 10.0)
    contrast_n = _clamp(spectral_contrast / 30.0)

    activation = 100.0 * (
        0.30 * energy_n
        + 0.25 * tempo_n
        + 0.20 * dance_n
        + 0.10 * onset_n
        + 0.10 * bright_n
        + 0.05 * dyn_n
    )
    valence = 100.0 * (
        0.50 * valence_n
        + 0.20 * (1.0 - flatness_n)
        + 0.15 * contrast_n
        + 0.15 * (1.0 - acoustic_n * 0.5)
    )

    return {
        "activation": float(_clamp(activation, 0.0, 100.0)),
        "valence": float(_clamp(valence, 0.0, 100.0)),
        "vibe_score": float(_clamp(activation, 0.0, 100.0)),
    }


def mood_label(activation: float, valence: float | None = None) -> str:
    """
    Return a mood tag from activation (+ optional valence).

    Backwards-compat: if valence is None (old single-axis callers), fall back
    to the legacy 5-bucket labelling.
    """
    if valence is None:
        if activation < 20:
            return "sleep"
        if activation < 40:
            return "chill"
        if activation < 60:
            return "steady"
        if activation < 80:
            return "hype"
        return "beast"

    if activation < 20:
        return "sleep"
    if activation < 40:
        return "chill" if valence >= 50 else "melancholy"
    if activation < 60:
        return "steady" if valence >= 50 else "moody"
    if activation < 80:
        return "hype" if valence >= 50 else "aggressive"
    return "beast"


def vibe_score(features: dict) -> float:
    """Backwards-compat single-value entry point. Returns activation."""
    return compute_axes(features)["activation"]
