from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import yaml
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import VibeDataset, build_dataframe, collate_skip_none, TARGET_COLS  # noqa
from model import MERTVibeRegressor  # noqa
from train import make_splits, resolve, set_seed  # noqa

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def gather_predictions(model, loader, device):
    model.eval()
    model.to(device)
    ys, ps = [], []
    with torch.no_grad():
        for batch in loader:
            if batch is None:
                continue
            audio = batch["audio"].to(device)
            targets = batch["targets"].numpy()
            preds = model(audio).detach().cpu().numpy()
            ps.append(preds)
            ys.append(targets)
    return np.concatenate(ys, 0), np.concatenate(ps, 0)


def compute_metrics(y_true, y_pred, names):
    rows = []
    for i, name in enumerate(names):
        rmse = float(np.sqrt(mean_squared_error(y_true[:, i], y_pred[:, i])))
        mae = float(mean_absolute_error(y_true[:, i], y_pred[:, i]))
        r2 = float(r2_score(y_true[:, i], y_pred[:, i]))
        try:
            spr = float(spearmanr(y_true[:, i], y_pred[:, i]).correlation)
        except Exception:
            spr = float("nan")
        try:
            prs = float(pearsonr(y_true[:, i], y_pred[:, i])[0])
        except Exception:
            prs = float("nan")
        rows.append({"axis": name, "rmse": rmse, "mae": mae, "r2": r2, "spearman": spr, "pearson": prs})
    return pd.DataFrame(rows)


def baseline_metrics(y_train, y_test, names):
    means = y_train.mean(axis=0)
    y_pred = np.tile(means, (y_test.shape[0], 1))
    rows = []
    for i, name in enumerate(names):
        rmse = float(np.sqrt(mean_squared_error(y_test[:, i], y_pred[:, i])))
        mae = float(mean_absolute_error(y_test[:, i], y_pred[:, i]))
        rows.append({"axis": name, "baseline_rmse": rmse, "baseline_mae": mae, "train_mean": float(means[i])})
    return pd.DataFrame(rows)


def plot_axis(y_true, y_pred, name, out_dir: Path):
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(y_true, y_pred, s=6, alpha=0.4)
    ax.plot([0, 1], [0, 1], "r--", linewidth=1)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_xlabel(f"actual {name}"); ax.set_ylabel(f"predicted {name}")
    ax.set_title(f"pred vs actual: {name}")
    fig.tight_layout()
    fig.savefig(out_dir / f"scatter_{name}.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(5, 3))
    ax.hist(y_pred - y_true, bins=50)
    ax.set_xlabel(f"residual (pred - actual) {name}")
    ax.set_title(f"residuals: {name}")
    fig.tight_layout()
    fig.savefig(out_dir / f"residuals_{name}.png", dpi=120)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--limit-tracks", type=int, default=None)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    set_seed(cfg["seed"], cfg.get("deterministic", False))

    dcfg = cfg["data"]
    csv_path = resolve(dcfg["csv_path"])
    manifest_path = resolve(dcfg["manifest_path"])
    previews_dir = resolve(dcfg["previews_dir"])

    df = build_dataframe(csv_path, manifest_path, previews_dir)
    if args.limit_tracks:
        df = df.sample(n=min(args.limit_tracks, len(df)), random_state=cfg["seed"]).reset_index(drop=True)

    df_train, df_val, df_test = make_splits(
        df, dcfg["train_frac"], dcfg["val_frac"], dcfg["test_frac"], dcfg["group_col"], cfg["seed"]
    )

    from torch.utils.data import DataLoader
    ds_test = VibeDataset(
        df_test, split="test", augment=False,
        sample_rate=dcfg["sample_rate"], max_duration_s=dcfg["max_duration_s"],
        crop_duration_s=dcfg["crop_duration_s"], gain_db=cfg["augment"]["gain_db"],
    )
    dl_test = DataLoader(
        ds_test, batch_size=cfg["trainer"]["batch_size"], shuffle=False,
        num_workers=dcfg.get("num_workers", 0), collate_fn=collate_skip_none,
    )

    model = MERTVibeRegressor.load_from_checkpoint(args.ckpt, map_location="cpu")
    device = "cuda" if torch.cuda.is_available() else "cpu"

    y_true, y_pred = gather_predictions(model, dl_test, device)
    y_train = df_train[TARGET_COLS].values.astype(np.float32)

    m = compute_metrics(y_true, y_pred, TARGET_COLS)
    b = baseline_metrics(y_train, y_true, TARGET_COLS)
    merged = m.merge(b, on="axis")
    merged["rmse_delta_vs_baseline"] = merged["baseline_rmse"] - merged["rmse"]

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(resolve(cfg["paths"]["experiments_dir"])) / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_dir / "metrics.csv", index=False)

    for i, name in enumerate(TARGET_COLS):
        plot_axis(y_true[:, i], y_pred[:, i], name, out_dir)

    report = ["# VibeScape MERT evaluation", "", f"ckpt: `{args.ckpt}`  |  test rows: {len(y_true)}", "", "## Per-axis metrics", "", merged.to_markdown(index=False)]
    (out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"[eval] wrote {out_dir}")
    print(merged.to_string(index=False))


if __name__ == "__main__":
    main()
