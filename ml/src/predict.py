from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import MERTVibeRegressor  # noqa

import librosa


VIBE_ENERGY_W = 0.55
VIBE_DANCE_W = 0.45


def _load_audio(path: str, sr: int) -> np.ndarray:
    try:
        y, _ = librosa.load(path, sr=sr, mono=True)
        return y.astype(np.float32)
    except Exception:
        return _load_audio_ffmpeg(path, sr)


def _load_audio_ffmpeg(path: str, sr: int) -> np.ndarray:
    """Fallback loader using ffmpeg. Handles m4a/aac/opus containers that
    libsndfile can't open natively (imageio-ffmpeg ships the binary)."""
    import subprocess
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(sr),
           "-loglevel", "error", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16)
    return (pcm.astype(np.float32) / 32768.0)


class Predictor:
    def __init__(self, ckpt_path: str, device: str = None, crop_duration_s: float = 10.0):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = MERTVibeRegressor.load_from_checkpoint(ckpt_path, map_location=self.device)
        self.model.eval().to(self.device)
        self.sample_rate = int(self.model.hparams.sample_rate)
        self.crop_samples = int(crop_duration_s * self.sample_rate)
        self.target_names = list(self.model.target_names)

    def predict(self, audio_path: str) -> Dict[str, float]:
        preds, _ = self.predict_with_embedding(audio_path, want_embedding=False)
        return preds

    def predict_with_embedding(self, audio_path: str, want_embedding: bool = True):
        """
        One encoder forward pass, two outputs: the regression scalars and
        the mean-pooled hidden state.

        The head consumes cat([mean_pool, max_pool]) -> 2*hidden_size, but
        the encoder itself emits hidden_size (768) per frame either way.
        The 768-d mean_pool half IS the track embedding, so computing it
        here removes a second, independent MERT pass over the same audio
        (ingest_pipeline/stage_embedding.py used to do exactly that with
        the BASE checkpoint).

        Returns (preds_dict, mean_pooled_np_or_None).
        """
        y = _load_audio(audio_path, self.sample_rate)
        if len(y) < self.sample_rate:
            y = np.pad(y, (0, self.sample_rate - len(y)))
        if len(y) > self.crop_samples:
            start = max(0, (len(y) - self.crop_samples) // 2)
            y = y[start : start + self.crop_samples]
        else:
            y = np.pad(y, (0, self.crop_samples - len(y)))
        peak = float(np.max(np.abs(y))) if len(y) else 0.0
        if peak > 1.0:
            y = y / peak
        audio_t = torch.from_numpy(y).unsqueeze(0).to(self.device)
        with torch.no_grad():
            # Inlines MERTVibeRegressor.forward so the pooled halves are
            # reachable. Must stay in step with it — see ml/src/model.py.
            x = self.model._preprocess(audio_t)
            hs = self.model.encoder(x, output_hidden_states=False).last_hidden_state
            mean_p = hs.mean(dim=1)
            max_p = hs.max(dim=1).values
            pooled = torch.cat([mean_p, max_p], dim=-1)
            preds = torch.stack(
                [self.model.heads[n](pooled) for n in self.target_names], dim=-1
            ).cpu().numpy()[0]
            embedding = (
                mean_p.squeeze(0).float().cpu().numpy().astype(np.float32)
                if want_embedding else None
            )
        out = {name: float(preds[i]) for i, name in enumerate(self.target_names)}
        dance = float(out.get("danceability", 0.0))
        energy = float(out.get("energy", 0.0))
        out["vibe_score"] = VIBE_ENERGY_W * energy + VIBE_DANCE_W * dance
        return out, embedding


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--audio", required=True)
    args = ap.parse_args()
    pred = Predictor(args.ckpt).predict(args.audio)
    print(json.dumps(pred, indent=2))


if __name__ == "__main__":
    main()
