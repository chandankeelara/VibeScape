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
    y, _ = librosa.load(path, sr=sr, mono=True)
    return y.astype(np.float32)


class Predictor:
    def __init__(self, ckpt_path: str, device: str = None, crop_duration_s: float = 10.0):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = MERTVibeRegressor.load_from_checkpoint(ckpt_path, map_location=self.device)
        self.model.eval().to(self.device)
        self.sample_rate = int(self.model.hparams.sample_rate)
        self.crop_samples = int(crop_duration_s * self.sample_rate)
        self.target_names = list(self.model.target_names)

    def predict(self, audio_path: str) -> Dict[str, float]:
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
            preds = self.model(audio_t).cpu().numpy()[0]
        out = {name: float(preds[i]) for i, name in enumerate(self.target_names)}
        dance = float(out.get("danceability", 0.0))
        energy = float(out.get("energy", 0.0))
        out["vibe_score"] = VIBE_ENERGY_W * energy + VIBE_DANCE_W * dance
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--audio", required=True)
    args = ap.parse_args()
    pred = Predictor(args.ckpt).predict(args.audio)
    print(json.dumps(pred, indent=2))


if __name__ == "__main__":
    main()
