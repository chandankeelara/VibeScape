from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import numpy as np
import torch
import yaml
import pytorch_lightning as pl
from pytorch_lightning.callbacks import EarlyStopping, ModelCheckpoint, LearningRateMonitor
from pytorch_lightning.loggers import MLFlowLogger
from sklearn.model_selection import GroupShuffleSplit
from torch.utils.data import DataLoader

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import VibeDataset, build_dataframe, collate_skip_none, TARGET_COLS  # noqa
from model import MERTVibeRegressor  # noqa


ROOT = Path(__file__).resolve().parents[2]


def resolve(p: str) -> str:
    pp = Path(p)
    if not pp.is_absolute():
        pp = ROOT / pp
    return str(pp)


def set_seed(seed: int, deterministic: bool):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    else:
        torch.backends.cudnn.benchmark = True


def make_splits(df, train_frac: float, val_frac: float, test_frac: float, group_col: str, seed: int):
    assert abs(train_frac + val_frac + test_frac - 1.0) < 1e-6
    groups = df[group_col].fillna("__unknown__").astype(str).values
    gss1 = GroupShuffleSplit(n_splits=1, test_size=test_frac, random_state=seed)
    trainval_idx, test_idx = next(gss1.split(df, groups=groups))
    df_tv = df.iloc[trainval_idx].reset_index(drop=True)
    df_test = df.iloc[test_idx].reset_index(drop=True)
    val_rel = val_frac / (train_frac + val_frac)
    groups_tv = df_tv[group_col].fillna("__unknown__").astype(str).values
    gss2 = GroupShuffleSplit(n_splits=1, test_size=val_rel, random_state=seed)
    tr_idx, val_idx = next(gss2.split(df_tv, groups=groups_tv))
    df_train = df_tv.iloc[tr_idx].reset_index(drop=True)
    df_val = df_tv.iloc[val_idx].reset_index(drop=True)
    return df_train, df_val, df_test


def build_loaders(cfg, df_train, df_val, df_test):
    dcfg = cfg["data"]
    tcfg = cfg["trainer"]
    acfg = cfg["augment"]
    common = dict(
        sample_rate=dcfg["sample_rate"],
        max_duration_s=dcfg["max_duration_s"],
        crop_duration_s=dcfg["crop_duration_s"],
        gain_db=acfg["gain_db"],
    )
    ds_train = VibeDataset(df_train, split="train", augment=True, **common)
    ds_val = VibeDataset(df_val, split="val", augment=False, **common)
    ds_test = VibeDataset(df_test, split="test", augment=False, **common)

    nw = int(dcfg.get("num_workers", 0))
    persistent = bool(dcfg.get("persistent_workers", False)) and nw > 0
    pin = bool(dcfg.get("pin_memory", True))

    dl_train = DataLoader(
        ds_train, batch_size=tcfg["batch_size"], shuffle=True, num_workers=nw,
        pin_memory=pin, persistent_workers=persistent, collate_fn=collate_skip_none, drop_last=True,
    )
    dl_val = DataLoader(
        ds_val, batch_size=tcfg["batch_size"], shuffle=False, num_workers=nw,
        pin_memory=pin, persistent_workers=persistent, collate_fn=collate_skip_none,
    )
    dl_test = DataLoader(
        ds_test, batch_size=tcfg["batch_size"], shuffle=False, num_workers=nw,
        pin_memory=pin, persistent_workers=persistent, collate_fn=collate_skip_none,
    )
    return dl_train, dl_val, dl_test


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--fast-dev-run", action="store_true")
    ap.add_argument("--limit-tracks", type=int, default=None)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    set_seed(cfg["seed"], cfg.get("deterministic", False))

    dcfg = cfg["data"]
    csv_path = resolve(dcfg["csv_path"])
    manifest_path = resolve(dcfg["manifest_path"])
    previews_dir = resolve(dcfg["previews_dir"])

    print(f"[data] building dataframe from {csv_path}")
    df = build_dataframe(csv_path, manifest_path, previews_dir)
    print(f"[data] {len(df):,} rows with audio on disk")
    if args.limit_tracks is not None:
        df = df.sample(n=min(args.limit_tracks, len(df)), random_state=cfg["seed"]).reset_index(drop=True)
        print(f"[data] limited to {len(df):,} rows via --limit-tracks")

    if len(df) < 10:
        print("[data] not enough data to split; aborting")
        sys.exit(2)

    df_train, df_val, df_test = make_splits(
        df, dcfg["train_frac"], dcfg["val_frac"], dcfg["test_frac"], dcfg["group_col"], cfg["seed"]
    )
    print(f"[data] train={len(df_train):,}  val={len(df_val):,}  test={len(df_test):,}")

    dl_train, dl_val, dl_test = build_loaders(cfg, df_train, df_val, df_test)

    tcfg = cfg["trainer"]
    ocfg = cfg["optim"]
    mcfg = cfg["model"]

    steps_per_epoch = max(1, len(dl_train) // max(1, tcfg["grad_accum"]))
    total_steps = steps_per_epoch * tcfg["max_epochs"]

    model = MERTVibeRegressor(
        pretrained_name=mcfg["pretrained_name"],
        target_names=mcfg["targets"],
        head_hidden=mcfg["head_hidden"],
        dropout=mcfg["dropout"],
        lr_head=ocfg["lr_head"],
        lr_encoder=ocfg["lr_encoder"],
        weight_decay=ocfg["weight_decay"],
        warmup_steps=ocfg["warmup_steps"],
        total_steps=total_steps,
        freeze_encoder_epochs=tcfg["freeze_encoder_epochs"],
        sample_rate=dcfg["sample_rate"],
    )

    paths = cfg["paths"]
    mlflow_dir = Path(resolve(paths["mlflow_dir"]))
    mlflow_dir.mkdir(parents=True, exist_ok=True)
    models_dir = Path(resolve(paths["models_dir"]))
    models_dir.mkdir(parents=True, exist_ok=True)

    logger = MLFlowLogger(
        experiment_name="vibescape-mert",
        tracking_uri=f"file:{mlflow_dir.as_posix()}",
    )

    ckpt_cb = ModelCheckpoint(
        dirpath=str(models_dir),
        filename="mert_v1-{epoch:02d}-{val_loss:.4f}",
        monitor="val_loss",
        mode="min",
        save_top_k=1,
        save_last=False,
    )
    es_cb = EarlyStopping(monitor="val_loss", mode="min", patience=tcfg["patience"])
    lr_cb = LearningRateMonitor(logging_interval="step")

    trainer = pl.Trainer(
        max_epochs=tcfg["max_epochs"],
        accelerator="auto",
        devices=1,
        precision=tcfg.get("precision", "16-mixed"),
        accumulate_grad_batches=tcfg["grad_accum"],
        gradient_clip_val=tcfg.get("gradient_clip_val", 1.0),
        logger=logger,
        callbacks=[ckpt_cb, es_cb, lr_cb],
        deterministic=bool(cfg.get("deterministic", False)),
        fast_dev_run=args.fast_dev_run,
        log_every_n_steps=10,
    )

    trainer.fit(model, train_dataloaders=dl_train, val_dataloaders=dl_val)

    if args.fast_dev_run:
        print("[done] fast-dev-run complete")
        return

    if ckpt_cb.best_model_path:
        best = Path(ckpt_cb.best_model_path)
        target = Path(resolve(paths["best_ckpt"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        try:
            os.link(best, target)
        except Exception:
            import shutil
            shutil.copy2(best, target)
        print(f"[ckpt] best -> {target}")

    print("[test] evaluating on test split")
    trainer.test(model, dataloaders=dl_test, ckpt_path="best")


if __name__ == "__main__":
    main()
