from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

try:
    import soundfile as sf
except Exception:
    sf = None

import librosa


TARGET_COLS = ["danceability", "energy", "valence"]


def _load_audio_mono(path: str, target_sr: int) -> np.ndarray:
    if sf is not None:
        try:
            y, sr = sf.read(path, dtype="float32", always_2d=False)
            if y.ndim > 1:
                y = y.mean(axis=1)
            if sr != target_sr:
                y = librosa.resample(y.astype(np.float32), orig_sr=sr, target_sr=target_sr)
            return y.astype(np.float32)
        except Exception:
            pass
    y, _ = librosa.load(path, sr=target_sr, mono=True)
    return y.astype(np.float32)


def build_dataframe(csv_path: str, manifest_path: str, previews_dir: str) -> pd.DataFrame:
    tracks = pd.read_csv(csv_path)
    if tracks.columns[0].startswith("Unnamed") or tracks.columns[0] == "":
        tracks = tracks.drop(columns=tracks.columns[0])
    manifest = pd.read_csv(manifest_path)
    manifest = manifest[manifest["status"] == "ok"][["track_id"]]
    df = tracks.merge(manifest, on="track_id", how="inner")
    prev_dir = Path(previews_dir)
    df["audio_path"] = df["track_id"].map(lambda t: str(prev_dir / f"{t}.mp3"))
    exists_mask = df["audio_path"].map(lambda p: Path(p).exists() and Path(p).stat().st_size > 10_000)
    df = df[exists_mask].reset_index(drop=True)
    for c in TARGET_COLS:
        df = df[df[c].notna()]
    df = df.reset_index(drop=True)
    return df


class VibeDataset(Dataset):
    def __init__(
        self,
        df: pd.DataFrame,
        sample_rate: int = 24000,
        max_duration_s: int = 30,
        crop_duration_s: int = 10,
        split: str = "train",
        augment: bool = True,
        gain_db: float = 3.0,
    ):
        self.df = df.reset_index(drop=True)
        self.sample_rate = sample_rate
        self.max_samples = int(max_duration_s * sample_rate)
        self.crop_samples = int(crop_duration_s * sample_rate)
        self.split = split
        self.augment = augment and split == "train"
        self.gain_db = gain_db

    def __len__(self) -> int:
        return len(self.df)

    def _crop(self, y: np.ndarray) -> np.ndarray:
        n = len(y)
        if n <= self.crop_samples:
            pad = self.crop_samples - n
            return np.pad(y, (0, pad), mode="constant")
        if self.split == "train":
            start = random.randint(0, n - self.crop_samples)
        else:
            start = max(0, (n - self.crop_samples) // 2)
        return y[start : start + self.crop_samples]

    def _apply_gain(self, y: np.ndarray) -> np.ndarray:
        db = random.uniform(-self.gain_db, self.gain_db)
        factor = 10.0 ** (db / 20.0)
        return (y * factor).astype(np.float32)

    def __getitem__(self, idx: int):
        row = self.df.iloc[idx]
        path = row["audio_path"]
        try:
            y = _load_audio_mono(path, self.sample_rate)
        except Exception:
            return None
        if y is None or len(y) < self.sample_rate:
            return None
        if len(y) > self.max_samples:
            y = y[: self.max_samples]
        y = self._crop(y)
        if self.augment:
            y = self._apply_gain(y)
        peak = float(np.max(np.abs(y))) if len(y) else 0.0
        if peak > 1.0:
            y = y / peak
        targets = np.array([row[c] for c in TARGET_COLS], dtype=np.float32)
        return {
            "audio": torch.from_numpy(y.astype(np.float32)),
            "targets": torch.from_numpy(targets),
        }


def collate_skip_none(batch):
    batch = [b for b in batch if b is not None]
    if not batch:
        return None
    audio = torch.stack([b["audio"] for b in batch], dim=0)
    targets = torch.stack([b["targets"] for b in batch], dim=0)
    return {"audio": audio, "targets": targets}
