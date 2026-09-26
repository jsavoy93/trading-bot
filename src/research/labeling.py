"""End-to-end forward-return labeling pipeline for OBS-003.

Read-only with respect to production. The only outbound calls are
Alpaca historical-data requests, batched by (symbol, calendar_date).

The pipeline:

    1. Load v1 decisions in the research window (read-only).
    2. Extract decision-time features for every decision.
    3. Determine the unique ``(symbol, date)`` pairs that need bars.
    4. Fetch bars once per pair (with local cache).
    5. For each decision, compute horizon labels (decision price +
       forward returns).
    6. Emit a single labeled dataset ready for analysis.

The output is a DataFrame with one row per decision and a wide set of
horizon columns (``fwd_30m_return``, ``fwd_60m_return``, ``fwd_240m_return``,
``fwd_next_session_open_return``, plus their status columns).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from src.research.bar_cache import BarCache, collect_symbol_dates
from src.research.decision_source import load_v1_decisions, open_decision_db
from src.research.deduplication import deduplicate_by_gate_state
from src.research.feature_extraction import extract_features
from src.research.price_alignment import (
    label_horizons,
)


# Default horizons. Trading minutes counted only inside regular session.
DEFAULT_HORIZONS = [
    ("fwd_30m", 30),
    ("fwd_60m", 60),
    ("fwd_240m", 240),
    ("fwd_next_session_open", None),
]


@dataclass
class PipelineConfig:
    start_iso: str
    end_iso: str
    horizons: list[tuple[str, Optional[int]]] = None
    db_uri: str = "file:trading_bot.db?mode=ro"
    cache_dir: Path = Path("reports/research/bar_cache")
    max_cache_age_hours: Optional[float] = 24.0
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    symbols: Optional[Iterable[str]] = None


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Apply ``extract_features`` to every row of *df*.

    Adds a ``features`` column containing the per-row feature dict.
    """
    if df.empty:
        return df.assign(features=[])
    feats = [
        extract_features(row["decision_snapshot"], row["symbol"], row["cycle_start"].isoformat())
        for _, row in df.iterrows()
    ]
    df = df.copy()
    df["features"] = feats
    return df


def label_decisions(
    df: pd.DataFrame,
    bar_cache: BarCache,
    horizons: Optional[list[tuple[str, Optional[int]]]] = None,
) -> pd.DataFrame:
    """Label every decision in *df* using cached bars.

    Returns a DataFrame with one row per decision plus horizon columns.
    """
    horizons = horizons or DEFAULT_HORIZONS
    if df.empty:
        return _empty_labeled_frame(horizons)

    # Group decisions by (symbol, calendar_date) so we fetch bars once per
    # (symbol, date) and reuse across all decisions for that symbol-date.
    # Include the next day so the next-session-open horizon can resolve.
    pairs = collect_symbol_dates(df, include_next_day=True)
    # Pre-populate the cache in batched API calls (one call per date, with
    # many symbols per call). Per-symbol fetch is still available as a
    # fallback inside ``bar_cache.fetch_day`` for cache misses.
    bar_cache.ensure_many(pairs, batch_size=100, progress_every=5)
    by_pair: dict[tuple[str, str], pd.DataFrame] = {}
    for sym, date in pairs:
        by_pair[(sym, date)] = bar_cache.fetch_day(sym, date)

    # For each (symbol, decision_date), build a merged frame covering
    # decision_date + next_date so forward horizons and next-session can
    # resolve. Sort and de-dup.
    from datetime import timedelta

    by_merged: dict[tuple[str, str], pd.DataFrame] = {}
    unique_decision_keys = set()
    for cs, sym in zip(df["cycle_start"], df["symbol"]):
        unique_decision_keys.add((sym, cs.tz_convert("UTC").strftime("%Y-%m-%d")))
    for sym, cur_date in unique_decision_keys:
        cur = by_pair.get((sym, cur_date), pd.DataFrame())
        try:
            cur_dt = pd.Timestamp(cur_date).to_pydatetime()
        except Exception:
            cur_dt = None
        if cur_dt is None:
            by_merged[(sym, cur_date)] = cur
            continue
        nxt_date = (cur_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        nxt = by_pair.get((sym, nxt_date), pd.DataFrame())
        if cur.empty and nxt.empty:
            merged = pd.DataFrame()
        elif cur.empty:
            merged = nxt
        elif nxt.empty:
            merged = cur
        else:
            merged = pd.concat([cur, nxt]).sort_index()
            merged = merged[~merged.index.duplicated(keep="first")]
        by_merged[(sym, cur_date)] = merged

    # Per-decision labeling using the audited ``label_horizons`` helper.
    # This is the same helper the deterministic unit tests verify, so
    # ``label_decisions`` inherits that exact semantics.
    #
    # Performance: ``itertuples`` is roughly 10x faster than ``iterrows``
    # and avoids the Series construction per row.
    horizon_names = [name for name, _ in horizons]
    cols = [
        f"{name}_{sub}"
        for name in horizon_names
        for sub in ("decision_price", "decision_price_ts", "forward_price",
                    "forward_price_ts", "return", "status")
    ]
    records: list[list] = []

    # Build a flat tuple list once, then iterate.
    cycles = list(zip(df["symbol"], df["cycle_start"]))
    total = len(cycles)
    from src.research.price_alignment import label_horizons
    for i, (sym, cycle_start) in enumerate(cycles):
        if i and i % 10000 == 0:
            print(f"  [label] {i}/{total} ({i*100//total}%)", flush=True)
        date_str = cycle_start.tz_convert("UTC").strftime("%Y-%m-%d")
        bars = by_merged.get((sym, date_str))
        if bars is None:
            bars = pd.DataFrame()
        labs = label_horizons(bars, cycle_start, horizons)
        row_out = []
        for name in horizon_names:
            lab = labs[name]
            row_out.extend([
                lab.decision_price,
                lab.decision_price_ts.isoformat() if lab.decision_price_ts is not None else None,
                lab.forward_price,
                lab.forward_price_ts.isoformat() if lab.forward_price_ts is not None else None,
                lab.forward_return,
                lab.label_status,
            ])
        records.append(row_out)
    print(f"  [label] {total}/{total} (100%)", flush=True)

    flat_df = pd.DataFrame(records, columns=cols, index=df.index)
    out = pd.concat([df.reset_index(drop=True), flat_df], axis=1)
    return out


def _empty_labeled_frame(horizons) -> pd.DataFrame:
    cols = []
    for name, _ in horizons:
        for sub in ["decision_price", "decision_price_ts", "forward_price",
                    "forward_price_ts", "return", "status"]:
            cols.append(f"{name}_{sub}")
    return pd.DataFrame(columns=cols)


def run_pipeline(cfg: PipelineConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run the full pipeline and return ``(raw_labeled, dedup_labeled, summary)``.

    Returns
    -------
    raw_labeled : pd.DataFrame
        One row per decision in the research window, with feature columns
        and horizon-label columns.
    dedup_labeled : pd.DataFrame
        The deduplicated view (one row per ``(symbol, date, gate_state)``
        contiguous block).
    summary : pd.DataFrame
        Per-horizon label-coverage summary.
    """
    import os
    api_key = cfg.api_key or os.environ.get("ALPACA_API_KEY")
    api_secret = cfg.api_secret or os.environ.get("ALPACA_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_API_SECRET must be set in environment."
        )

    conn = open_decision_db(uri=cfg.db_uri)
    try:
        decisions = load_v1_decisions(
            conn, cfg.start_iso, cfg.end_iso, cfg.symbols
        )
    finally:
        conn.close()

    decisions = build_features(decisions)

    bar_cache = BarCache(
        api_key=api_key,
        api_secret=api_secret,
        cache_dir=cfg.cache_dir,
        max_age_hours=cfg.max_cache_age_hours,
    )

    raw = label_decisions(decisions, bar_cache, cfg.horizons)

    # Deduplicate first (which needs the unflattened ``features`` dict),
    # then flatten for output.
    raw_view, dedup_view = deduplicate_by_gate_state(raw)
    raw_view = _flatten_features(raw_view)
    dedup_view = _flatten_features(dedup_view)
    summary = _label_coverage_summary(raw_view, dedup_view, cfg.horizons or DEFAULT_HORIZONS)

    return raw_view, dedup_view, summary


def _flatten_features(df: pd.DataFrame) -> pd.DataFrame:
    """Replace the ``features`` column with flattened scalar columns.

    Tuple-typed features are converted to comma-joined strings so they
    can be CSV-serialized.
    """
    if "features" not in df.columns:
        return df
    feats = pd.DataFrame(
        [f if isinstance(f, dict) else {} for f in df["features"]],
        index=df.index,
    )
    for col in feats.columns:
        # tuple -> comma-joined string
        if feats[col].dtype == "object":
            feats[col] = feats[col].apply(
                lambda v: ",".join(v) if isinstance(v, (list, tuple)) else v
            )
    df = df.drop(columns=["features"])
    df = pd.concat([df, feats.add_prefix("feat_")], axis=1)
    return df


def _label_coverage_summary(
    raw: pd.DataFrame, dedup: pd.DataFrame, horizons
) -> pd.DataFrame:
    """Return a per-horizon coverage summary table."""
    rows = []
    for name, _ in horizons:
        col_status = f"{name}_status"
        col_ret = f"{name}_return"
        for view_name, df in (("raw", raw), ("dedup", dedup)):
            if df.empty:
                rows.append({
                    "view": view_name,
                    "horizon": name,
                    "n_total": 0,
                    "n_ok": 0,
                    "n_missing_decision_bar": 0,
                    "n_horizon_beyond": 0,
                    "n_no_next_session": 0,
                    "pct_ok": 0.0,
                })
                continue
            vc = df[col_status].value_counts().to_dict()
            n_total = len(df)
            n_ok = int(vc.get("OK", 0))
            n_missing_decision = int(vc.get("MISSING_DECISION_BAR", 0))
            n_horizon_beyond = int(vc.get("HORIZON_BEYOND_AVAILABLE_BARS", 0))
            n_no_next = int(vc.get("NO_NEXT_SESSION_BAR", 0))
            pct_ok = (100.0 * n_ok / n_total) if n_total else 0.0
            rows.append({
                "view": view_name,
                "horizon": name,
                "n_total": n_total,
                "n_ok": n_ok,
                "n_missing_decision_bar": n_missing_decision,
                "n_horizon_beyond": n_horizon_beyond,
                "n_no_next_session": n_no_next,
                "pct_ok": pct_ok,
            })
    return pd.DataFrame(rows)
