from __future__ import annotations

import math
from typing import List

import torch
import torch.nn as nn
import torch.nn.functional as F
import pytorch_lightning as pl
from transformers import AutoModel, Wav2Vec2FeatureExtractor


class RegressionHead(nn.Module):
    def __init__(self, in_dim: int, hidden: int, dropout: float):
        super().__init__()
        self.norm = nn.LayerNorm(in_dim)
        self.fc1 = nn.Linear(in_dim, hidden)
        self.act = nn.GELU()
        self.drop = nn.Dropout(dropout)
        self.fc2 = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.norm(x)
        x = self.fc1(x)
        x = self.act(x)
        x = self.drop(x)
        x = self.fc2(x)
        return torch.sigmoid(x).squeeze(-1)


class MERTVibeRegressor(pl.LightningModule):
    def __init__(
        self,
        pretrained_name: str = "m-a-p/MERT-v1-95M",
        target_names: List[str] = None,
        head_hidden: int = 256,
        dropout: float = 0.2,
        lr_head: float = 1e-4,
        lr_encoder: float = 1e-5,
        weight_decay: float = 1e-2,
        warmup_steps: int = 500,
        total_steps: int = 10_000,
        freeze_encoder_epochs: int = 1,
        sample_rate: int = 24000,
    ):
        super().__init__()
        self.save_hyperparameters()
        self.target_names = target_names or ["danceability", "energy", "valence"]
        self.encoder = AutoModel.from_pretrained(pretrained_name, trust_remote_code=True)
        self.feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(
            pretrained_name, trust_remote_code=True
        )
        hidden_size = getattr(self.encoder.config, "hidden_size", 768)
        pooled_dim = hidden_size * 2
        self.heads = nn.ModuleDict(
            {name: RegressionHead(pooled_dim, head_hidden, dropout) for name in self.target_names}
        )
        self._encoder_frozen = False

    def freeze_encoder(self, freeze: bool = True):
        for p in self.encoder.parameters():
            p.requires_grad = not freeze
        self._encoder_frozen = freeze

    def setup(self, stage: str = None):
        if self.hparams.freeze_encoder_epochs > 0:
            self.freeze_encoder(True)

    def on_train_epoch_start(self):
        if self.current_epoch >= self.hparams.freeze_encoder_epochs and self._encoder_frozen:
            self.freeze_encoder(False)

    def _preprocess(self, audio: torch.Tensor) -> torch.Tensor:
        arrs = [a.detach().cpu().numpy() for a in audio]
        inputs = self.feature_extractor(
            arrs,
            sampling_rate=self.hparams.sample_rate,
            return_tensors="pt",
            padding=True,
        )
        return inputs["input_values"].to(audio.device)

    def forward(self, audio: torch.Tensor) -> torch.Tensor:
        x = self._preprocess(audio)
        if self._encoder_frozen:
            with torch.no_grad():
                out = self.encoder(x, output_hidden_states=False)
        else:
            out = self.encoder(x, output_hidden_states=False)
        hs = out.last_hidden_state
        mean_p = hs.mean(dim=1)
        max_p = hs.max(dim=1).values
        pooled = torch.cat([mean_p, max_p], dim=-1)
        preds = torch.stack([self.heads[n](pooled) for n in self.target_names], dim=-1)
        return preds

    def _step(self, batch, stage: str) -> torch.Tensor:
        if batch is None:
            return None
        preds = self(batch["audio"])
        targets = batch["targets"]
        losses = []
        total = 0.0
        for i, name in enumerate(self.target_names):
            li = F.mse_loss(preds[:, i], targets[:, i])
            losses.append(li)
            total = total + li
            self.log(f"{stage}_mse_{name}", li, prog_bar=False, on_step=False, on_epoch=True, batch_size=targets.size(0))
        self.log(f"{stage}_loss", total, prog_bar=True, on_step=(stage == "train"), on_epoch=True, batch_size=targets.size(0))
        return total

    def training_step(self, batch, batch_idx):
        return self._step(batch, "train")

    def validation_step(self, batch, batch_idx):
        return self._step(batch, "val")

    def test_step(self, batch, batch_idx):
        return self._step(batch, "test")

    def configure_optimizers(self):
        head_params = list(self.heads.parameters())
        enc_params = list(self.encoder.parameters())
        optim = torch.optim.AdamW(
            [
                {"params": enc_params, "lr": self.hparams.lr_encoder},
                {"params": head_params, "lr": self.hparams.lr_head},
            ],
            weight_decay=self.hparams.weight_decay,
        )
        warmup = max(1, int(self.hparams.warmup_steps))
        total = max(warmup + 1, int(self.hparams.total_steps))

        def lr_lambda(step: int):
            if step < warmup:
                return float(step) / float(warmup)
            progress = (step - warmup) / float(max(1, total - warmup))
            return 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress)))

        sched = torch.optim.lr_scheduler.LambdaLR(optim, lr_lambda)
        return {
            "optimizer": optim,
            "lr_scheduler": {"scheduler": sched, "interval": "step", "frequency": 1},
        }
