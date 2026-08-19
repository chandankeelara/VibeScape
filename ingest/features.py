import os
import subprocess
import tempfile

import numpy as np
import requests
import librosa

try:
    import imageio_ffmpeg
    _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    _FFMPEG = None


# Krumhansl-Kessler major/minor key profiles (empirical listener ratings).
_KK_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    dtype=np.float64,
)
_KK_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    dtype=np.float64,
)


def download_preview(preview_url: str) -> str:
    if not preview_url:
        raise ValueError("preview_url is empty")
    r = requests.get(preview_url, timeout=30, stream=True)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".m4a")
    with open(fd, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return path


def _to_wav(src_path: str) -> str:
    if _FFMPEG is None:
        raise RuntimeError("ffmpeg not available; install imageio-ffmpeg or system ffmpeg")
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    subprocess.run(
        [_FFMPEG, "-y", "-loglevel", "error", "-i", src_path,
         "-ac", "1", "-ar", "22050", wav_path],
        check=True,
    )
    return wav_path


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)


def krumhansl_major_minor(chroma: np.ndarray) -> float:
    """
    Correlate the mean chroma vector against all 12 rotations of the
    Krumhansl-Kessler major and minor templates. Return
        max_major_corr - max_minor_corr, clipped to [-1, +1].
    Positive -> more major-like (brighter/happier); negative -> more minor.
    """
    if chroma is None:
        return 0.0
    if chroma.ndim == 2:
        vec = chroma.mean(axis=1)
    else:
        vec = np.asarray(chroma, dtype=np.float64)
    if vec.shape[0] != 12:
        return 0.0
    vec = vec.astype(np.float64)
    best_major = -1.0
    best_minor = -1.0
    for i in range(12):
        rot_maj = np.roll(_KK_MAJOR, i)
        rot_min = np.roll(_KK_MINOR, i)
        c_maj = _pearson(vec, rot_maj)
        c_min = _pearson(vec, rot_min)
        if c_maj > best_major:
            best_major = c_maj
        if c_min > best_minor:
            best_minor = c_min
    return float(np.clip(best_major - best_minor, -1.0, 1.0))


def extract_full(y: np.ndarray, sr: int) -> dict:
    """
    Full librosa feature set. Returns scalar aggregates + a few short vectors
    for downstream scoring, similarity search, etc.
    """
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo_val = float(np.asarray(tempo).flatten()[0])

    try:
        plp = librosa.beat.plp(y=y, sr=sr)
    except Exception:
        plp = np.array([0.0])
    try:
        onsets = librosa.onset.onset_strength(y=y, sr=sr)
    except Exception:
        onsets = np.array([0.0])

    rms = librosa.feature.rms(y=y)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
    flatness = librosa.feature.spectral_flatness(y=y)
    zcr = librosa.feature.zero_crossing_rate(y)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)

    try:
        chroma = librosa.feature.chroma_cens(y=y, sr=sr)
    except Exception:
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    try:
        tonnetz = librosa.feature.tonnetz(y=y, sr=sr)
    except Exception:
        tonnetz = np.zeros((6, 1), dtype=np.float64)

    try:
        y_h, y_p = librosa.effects.hpss(y)
        h_energy = float(np.mean(librosa.feature.rms(y=y_h)))
        p_energy = float(np.mean(librosa.feature.rms(y=y_p)))
    except Exception:
        h_energy = float(np.mean(rms))
        p_energy = float(np.mean(rms))

    valence_mode = krumhansl_major_minor(chroma)

    return {
        "tempo": tempo_val,
        "tempo_stability": float(1.0 / (float(np.std(plp)) + 1e-6)),
        "onset_rate": float(np.mean(onsets)),
        "energy_mean": float(np.mean(rms)),
        "energy_std": float(np.std(rms)),
        "brightness": float(np.mean(centroid)),
        "bandwidth": float(np.mean(bandwidth)),
        "rolloff": float(np.mean(rolloff)),
        "spectral_contrast": float(np.mean(contrast)),
        "flatness": float(np.mean(flatness)),
        "zcr": float(np.mean(zcr)),
        "mfcc_mean": [float(x) for x in mfcc.mean(axis=1).tolist()],
        "timbre_variability": float(mfcc.std(axis=1).mean()),
        "chroma_mean": [float(x) for x in chroma.mean(axis=1).tolist()],
        "valence_mode": float(valence_mode),
        "tonnetz_std": float(np.std(tonnetz)),
        "acousticness": float(h_energy / (h_energy + p_energy + 1e-6)),
    }


def extract(audio_path: str) -> dict:
    """
    Backwards-compatible entry point. Extracts the full feature set from
    an audio file on disk (any format ffmpeg can decode) and also returns
    the legacy-named 'energy'/'mfcc' aliases so old callers keep working.
    """
    wav_path = _to_wav(audio_path)
    try:
        y, sr = librosa.load(wav_path, sr=22050, mono=True)
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass

    f = extract_full(y, sr)
    # legacy aliases
    f["energy"] = f["energy_mean"]
    f["mfcc"] = f["mfcc_mean"]
    return f
