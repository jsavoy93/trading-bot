"""Descriptive analysis of the labeled dataset for OBS-003.

This module is intentionally simple and descriptive. It does not run
any statistical tests of significance and does not propose threshold
changes. It only summarizes what is in the labeled dataset.

It produces:
    * Per-gate-group returns (RSI pass/fail x SMA pass/fail).
    * Per-gate-group score-band returns.
    * Threshold-proximity bins.
    * Joint-pass subset analysis.
    * Score vs return bands.
    * Raw vs deduplicated side-by-side comparison.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


# Group definitions for the four-way gate analysis.
def gate_group_label(rsi_pass, sma_pass) -> str:
    if rsi_pass is True and sma_pass is True:
        return "A_both_pass"
    if rsi_pass is True and sma_pass is False:
        return "B_rsi_pass_sma_fail"
    if rsi_pass is False and sma_pass is True:
        return "C_rsi_fail_sma_pass"
    if rsi_pass is False and sma_pass is False:
        return "D_both_fail"
    return "Z_unknown"


def assign_gate_group(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["gate_group"] = [
        gate_group_label(r, s)
        for r, s in zip(df["feat_rsi_oversold_pass"], df["feat_sma_uptrend_pass"])
    ]
    return df


def horizon_stats(values: pd.Series) -> dict:
    """Return descriptive statistics for a non-empty numeric Series."""
    v = values.dropna()
    if v.empty:
        return {
            "n": 0, "mean": np.nan, "median": np.nan, "std": np.nan,
            "p25": np.nan, "p75": np.nan, "p10": np.nan, "p90": np.nan,
            "p_positive": np.nan, "min": np.nan, "max": np.nan,
        }
    return {
        "n": int(len(v)),
        "mean": float(v.mean()),
        "median": float(v.median()),
        "std": float(v.std()),
        "p25": float(v.quantile(0.25)),
        "p75": float(v.quantile(0.75)),
        "p10": float(v.quantile(0.10)),
        "p90": float(v.quantile(0.90)),
        "p_positive": float((v > 0).mean()),
        "min": float(v.min()),
        "max": float(v.max()),
    }


def gate_group_returns(
    df: pd.DataFrame,
    horizons: Iterable[str],
) -> pd.DataFrame:
    """For each gate group and each horizon, compute return stats."""
    if df.empty:
        return pd.DataFrame()
    df = assign_gate_group(df)
    rows = []
    for grp, sub in df.groupby("gate_group"):
        for h in horizons:
            ret = sub[f"{h}_return"]
            stats = horizon_stats(ret)
            stats.update({
                "view": _view_of(df),
                "gate_group": grp,
                "horizon": h,
            })
            rows.append(stats)
    return pd.DataFrame(rows)


def threshold_proximity_returns(
    df: pd.DataFrame,
    horizons: Iterable[str],
) -> pd.DataFrame:
    """Bin by proximity to threshold for the major rejected groups.

    For each rejected group and each horizon:
      - bin by ``rsi_distance_to_threshold`` (positive side; 0-2, 2-5, 5-10, 10+)
      - bin by ``sma_distance_to_threshold`` (positive side; 0-0.05, 0.05-0.25, 0.25-1, 1+)
    """
    if df.empty:
        return pd.DataFrame()

    rows = []

    # RSI proximity (for rsi-failing groups)
    rsi_bins = pd.cut(
        df["feat_rsi_distance_to_threshold"],
        bins=[-1e9, 0, 2, 5, 10, 20, 1e9],
        include_lowest=True,
        labels=["passing_side", "fail_0_2", "fail_2_5", "fail_5_10", "fail_10_20", "fail_20p"],
    )

    # SMA spread proximity (for sma-failing groups)
    sma_bins = pd.cut(
        -df["feat_sma_spread"],  # positive when fast < slow
        bins=[-1e9, 0, 0.25, 1.0, 5.0, 1e9],
        include_lowest=True,
        labels=["passing_side", "fail_0_0.25", "fail_0.25_1", "fail_1_5", "fail_5p"],
    )

    df = df.copy()
    df["rsi_proximity_bin"] = rsi_bins.astype(str)
    df["sma_proximity_bin"] = sma_bins.astype(str)

    for h in horizons:
        col = f"{h}_return"
        for bin_label, sub in df.groupby("rsi_proximity_bin"):
            stats = horizon_stats(sub[col])
            stats.update({
                "view": _view_of(df),
                "metric": "rsi_distance",
                "bin": bin_label,
                "horizon": h,
            })
            rows.append(stats)
        for bin_label, sub in df.groupby("sma_proximity_bin"):
            stats = horizon_stats(sub[col])
            stats.update({
                "view": _view_of(df),
                "metric": "sma_spread_distance",
                "bin": bin_label,
                "horizon": h,
            })
            rows.append(stats)
    return pd.DataFrame(rows)


def score_band_returns(
    df: pd.DataFrame,
    horizons: Iterable[str],
) -> pd.DataFrame:
    """Bin by total_score and report forward-return stats per band."""
    if df.empty:
        return pd.DataFrame()

    score = df["feat_total_score"]
    bands = pd.cut(
        score,
        bins=[-1e9, 10, 25, 40, 55, 70, 85, 1e9],
        include_lowest=True,
        labels=["<=10", "10-25", "25-40", "40-55", "55-70", "70-85", "85+"],
    )
    df = df.assign(score_band=bands.astype(str))

    rows = []
    for band, sub in df.groupby("score_band"):
        for h in horizons:
            stats = horizon_stats(sub[f"{h}_return"])
            stats.update({
                "view": _view_of(df),
                "score_band": band,
                "horizon": h,
            })
            rows.append(stats)
    return pd.DataFrame(rows)


def joint_pass_analysis(
    df: pd.DataFrame,
    horizons: Iterable[str],
) -> pd.DataFrame:
    """Examine the rare joint-pass subset (group A)."""
    if df.empty:
        return pd.DataFrame()
    df = assign_gate_group(df)
    sub = df[df["gate_group"] == "A_both_pass"]
    if sub.empty:
        return pd.DataFrame()
    rows = []
    for h in horizons:
        stats = horizon_stats(sub[f"{h}_return"])
        stats.update({
            "view": _view_of(df),
            "subset": "A_both_pass",
            "horizon": h,
        })
        rows.append(stats)
    # Also list other_gate_names_failed to see what stopped the BUY.
    other_fail_counter = {}
    for tup in sub["feat_other_gates_names_failed"]:
        if isinstance(tup, str):
            for n in tup.split(","):
                n = n.strip()
                if n:
                    other_fail_counter[n] = other_fail_counter.get(n, 0) + 1
    return pd.DataFrame(rows)


def view_comparison(
    raw: pd.DataFrame, dedup: pd.DataFrame, horizons: Iterable[str],
) -> pd.DataFrame:
    """Compare stats between raw and dedup views side by side.

    Returns a single DataFrame with view, horizon, n, mean, median, p_positive.
    """
    rows = []
    for view_name, df in (("raw", raw), ("dedup", dedup)):
        for h in horizons:
            stats = horizon_stats(df[f"{h}_return"])
            stats.update({"view": view_name, "horizon": h})
            rows.append(stats)
    return pd.DataFrame(rows)


def _view_of(df: pd.DataFrame) -> str:
    if "_dedup_marker" in df.columns:
        return str(df["_dedup_marker"].iloc[0]) if len(df) else "unknown"
    return "raw"


def attach_view_marker(df: pd.DataFrame, name: str) -> pd.DataFrame:
    df = df.copy()
    df["_dedup_marker"] = name
    return df
