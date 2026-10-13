"""
Library-wide recompute of the DSP-derived score columns.

For every track with stored librosa features this recomputes activation and
valence with `scoring.compute_axes`, z-scores activation across the whole
catalogue into `activation_relative` (mean 50, one std = 15, clamped to
0..100), mirrors that into `vibe_score`, and derives `mood`.

The baseline is the GLOBAL catalogue — tracks are song-level truth shared by
every user — so there is no per-user variant. One call rewrites rows other
users see, which is why the HTTP endpoint is admin-only.

Where it runs: at the end of an offline pipeline pass that scored something
(`scripts/run_ingest_v2.py`; `--recompute-only` runs it by hand), and from
`POST /api/recompute-scores` for the admin. It used to run at the end of every
import job too; that added nothing, because an import only writes
metadata-only 'pending' rows with no features, so the baseline cannot move
until the offline pipeline has analysed them (moved offline 2026-10-13).

Pure apart from the connection it is handed: no FastAPI, no app import, so the
offline runner can use it without the app's import-time side effects.

`vibe_score = activation_relative` here, while ClassifyStage writes
`vibe_score = activation`; that disagreement is backlog 1.2 and is
deliberately NOT resolved in this module.
"""
from __future__ import annotations

import json
import statistics

import scoring

# Feature columns compute_axes reads. A row with all of these NULL has never
# been through the librosa stage and is left untouched.
FEATURE_SCALARS = [
    "tempo",
    "tempo_stability",
    "onset_rate",
    "energy_mean",
    "energy_std",
    "brightness",
    "bandwidth",
    "rolloff",
    "spectral_contrast",
    "flatness",
    "zcr",
    "timbre_variability",
    "valence_mode",
    "tonnetz_std",
    "acousticness",
]

# Stored floats that differ from the recomputed ones by less than this are
# treated as equal, so a re-run over an unchanged catalogue writes nothing.
FLOAT_TOLERANCE = 1e-6

_SELECT = (
    "SELECT id, tempo, energy, energy_mean, energy_std, brightness, "
    "tempo_stability, onset_rate, bandwidth, rolloff, "
    "spectral_contrast, flatness, zcr, timbre_variability, "
    "valence_mode, tonnetz_std, acousticness, mfcc_json, "
    "chroma_mean_json, activation, valence, activation_relative, "
    "vibe_score, mood FROM tracks"
)


def row_features(row) -> dict:
    """Rebuild a feature dict from a DB row, in the shape compute_axes takes."""
    f: dict = {}
    for k in FEATURE_SCALARS:
        try:
            f[k] = row[k]
        except (IndexError, KeyError):
            f[k] = None
    # Legacy alias support
    try:
        if f.get("energy_mean") is None:
            f["energy_mean"] = row["energy"]
    except (IndexError, KeyError):
        pass
    try:
        mfcc_json = row["mfcc_json"]
        f["mfcc_mean"] = json.loads(mfcc_json) if mfcc_json else []
    except (IndexError, KeyError, ValueError, TypeError):
        f["mfcc_mean"] = []
    try:
        chroma_json = row["chroma_mean_json"]
        f["chroma_mean"] = json.loads(chroma_json) if chroma_json else []
    except (IndexError, KeyError, ValueError, TypeError):
        f["chroma_mean"] = []
    return f


def _same_float(stored, new: float) -> bool:
    if stored is None:
        return False
    try:
        return abs(float(stored) - new) <= FLOAT_TOLERANCE
    except (TypeError, ValueError):
        return False


def recompute_axes_and_zscores(conn) -> dict:
    """
    Recompute and persist the score columns for the whole catalogue.

    Only rows whose stored activation, valence, activation_relative,
    vibe_score or mood actually differ are UPDATEd — on Turso each UPDATE is
    its own HTTPS round trip, and a re-run over an unchanged catalogue used to
    cost ~3,800 of them. Batching the remaining writes needs Hrana batch
    support in db_client (backlog 2.2).

    Returns {updated, written, unchanged, activation_stats, mood_distribution}:
    `updated` is the number of rows scored (its meaning before the skip
    existed), `written` how many UPDATEs were issued, `unchanged` the rest.
    """
    rows = conn.execute(_SELECT).fetchall()

    activations: list[float] = []
    per_row: list[tuple] = []
    for row in rows:
        f = row_features(row)
        # If no scalars at all, skip (leaves existing values intact).
        if all((f.get(k) is None) for k in FEATURE_SCALARS):
            continue
        axes = scoring.compute_axes(f)
        activation = axes["activation"]
        valence = axes["valence"]
        activations.append(activation)
        per_row.append((row, activation, valence))

    if not per_row:
        return {"updated": 0, "written": 0, "unchanged": 0,
                "activation_stats": None, "mood_distribution": {}}

    mean = float(statistics.fmean(activations))
    std = float(statistics.pstdev(activations)) if len(activations) > 1 else 0.0
    mn = float(min(activations))
    mx = float(max(activations))

    mood_counts: dict[str, int] = {}
    written = 0
    for row, activation, valence in per_row:
        if std > 1e-9:
            rel = 50.0 + ((activation - mean) / std) * 15.0
        else:
            rel = 50.0
        rel = scoring._clamp(rel, 0.0, 100.0)
        mood = scoring.mood_label(activation, valence)
        mood_counts[mood] = mood_counts.get(mood, 0) + 1
        if (_same_float(row["activation"], activation)
                and _same_float(row["valence"], valence)
                and _same_float(row["activation_relative"], rel)
                and _same_float(row["vibe_score"], rel)
                and row["mood"] == mood):
            continue
        conn.execute(
            "UPDATE tracks SET activation = ?, valence = ?, activation_relative = ?, "
            "vibe_score = ?, mood = ? WHERE id = ?",
            (activation, valence, rel, rel, mood, row["id"]),
        )
        written += 1
    conn.commit()

    return {
        "updated": len(per_row),
        "written": written,
        "unchanged": len(per_row) - written,
        "activation_stats": {
            "mean": mean,
            "std": std,
            "min": mn,
            "max": mx,
        },
        "mood_distribution": mood_counts,
    }
