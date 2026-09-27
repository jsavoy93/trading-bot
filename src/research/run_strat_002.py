"""STRAT-002 out-of-sample replication pipeline (memory-safe).

Frozen pre-registration (PR #98, commit e62a31e, merged as c8da017):

    Eligibility:   7 NY trading dates (Sept 14-18, 21-22, 2026)
    Unit:          SYMBOL × TRADING DAY
    Representative: LAST cycle whose cycle_start in NY local time
                    falls in [09:30, 16:00) ET on a weekday
    Cohorts:       HIGH_RSI (RSI >= 55), COMPARISON (RSI < 55)
    Primary horizon: fwd_next_session_open
    Secondary:     fwd_240m wall-clock
    Primary effect: delta_median, delta_p_positive (between-group)
    Practical-replication thresholds: +0.10 pp, +5 pp
    Min sample:    n_high_rsi_OK >= 200, n_high_rsi_with_RSI >= 5000,
                   n_comparison_OK >= 5000
    Verdict precedence (mutually exclusive):
        UNDERPOWERED > REPLICATED > DIRECTIONALLY CONSISTENT BUT WEAK >
        FAILED TO REPLICATE

This module is read-only with respect to the production database
(file:trading_bot.db?mode=ro). It calls only Alpaca historical-data
endpoints (``get_stock_bars``) via BarCache, not trading endpoints.

Memory-safety: this module processes ONE ELIGIBLE DATE at a time
rather than loading all decisions into memory. The OOM-kill on
2026-09-27 (process reached 6.9 GB RSS loading 1.4M rows of JSON
snapshots) forced this redesign. The cache is keyed (symbol, date)
so per-date processing preserves resumability and idempotency.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from src.research.bar_cache import BarCache
from src.research.decision_source import open_decision_db
from src.research.feature_extraction import extract_features
from src.research.labeling import label_decisions
from src.research.symbol_day import (
    assign_strat_002_cohort,
    collapse_to_symbol_days,
    is_regular_session_decision,
)
from src.research.strat_002_verdict import (
    BetweenGroupDelta,
    GroupStats,
    Verdict,
    compute_verdict,
    per_date_diagnostic,
)


# Frozen eligibility (PR #98)
DEFAULT_ELIGIBLE_TRADING_DATES = [
    "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
    "2026-09-21", "2026-09-22",
]

FROZEN_HIGH_RSI_CUTOFF = 55.0
FROZEN_COMPARISON_CUTOFF = 55.0
FROZEN_PRIMARY_HORIZON = "fwd_next_session_open"
FROZEN_SECONDARY_HORIZON = "fwd_240m"

HORIZON_RETURN_COL = {
    "fwd_next_session_open": "fwd_next_session_open_return",
    "fwd_240m": "fwd_240m_return",
}
HORIZON_STATUS_COL = {
    "fwd_next_session_open": "fwd_next_session_open_status",
    "fwd_240m": "fwd_240m_status",
}


def load_v0_decisions_for_date(
    conn,
    ny_trading_date: str,
) -> pd.DataFrame:
    """Load v0 decisions whose cycle_start's NY date is ``ny_trading_date``.

    Includes a ±1 day buffer in cycle_start so we catch any late-evening
    or early-morning decisions adjacent to the date. The regular-session
    predicate is applied later in ``collapse_to_symbol_days``.
    """
    sql = """
        SELECT id, symbol, cycle_start, session_id, decision_snapshot,
               created_at, analytics_persistence_version
        FROM decision_history
        WHERE analytics_persistence_version = 0
          AND cycle_start >= ?
          AND cycle_start < ?
        ORDER BY cycle_start, id
    """
    iso_start = f"{ny_trading_date}T00:00:00+00:00"
    iso_end = f"{ny_trading_date}T23:59:59+00:00"
    cur = conn.execute(sql, (iso_start, iso_end))
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    df["decision_snapshot"] = df["decision_snapshot"].apply(
        lambda s: json.loads(s) if isinstance(s, str) else s
    )
    df["cycle_start"] = pd.to_datetime(df["cycle_start"], utc=True, format="ISO8601")
    return df


def build_features_inplace(df: pd.DataFrame) -> pd.DataFrame:
    """Apply extract_features and add an ``rsi_value`` column for cohort classification."""
    if df.empty:
        return df.assign(features=[])
    feats = [
        extract_features(row["decision_snapshot"], row["symbol"], row["cycle_start"].isoformat())
        for _, row in df.iterrows()
    ]
    df = df.copy()
    df["features"] = feats
    df["rsi_value"] = [f.get("rsi_value") if isinstance(f, dict) else None for f in feats]
    return df


def collapse_one_date(
    decisions: pd.DataFrame,
    ny_date: str,
    high_rsi_cutoff: float = FROZEN_HIGH_RSI_CUTOFF,
) -> tuple[pd.DataFrame, int]:
    """Apply symbol-day collapse + cohort classification for one date.

    Returns (symbol_day_for_date, n_excluded_no_regular_session).
    """
    sd, excluded = collapse_to_symbol_days(
        decisions, symbol_col="symbol", cycle_start_col="cycle_start"
    )
    n_excluded = int(len(excluded))
    if sd.empty:
        return sd, n_excluded
    sd = sd.copy()
    sd["strat_002_cohort"] = sd["rsi_value"].map(
        lambda v: assign_strat_002_cohort(v, high_rsi_cutoff=high_rsi_cutoff)
    )
    sd = sd[sd["strat_002_cohort"].notna()].reset_index(drop=True)
    sd["ny_trading_date"] = ny_date
    return sd, n_excluded


def horizon_tuples(primary: str, secondary: str) -> list[tuple[str, Optional[int]]]:
    """Build the horizon tuples for OBS-003 label_horizons."""
    out = []
    for h in (primary, secondary):
        if h == "fwd_next_session_open":
            out.append((h, None))
        elif h.startswith("fwd_") and h.endswith("m"):
            out.append((h, int(h[4:-1])))
        else:
            raise ValueError(f"Unknown horizon name: {h}")
    return out


def fetch_bars_for_symbol_day_frame(
    sd: pd.DataFrame,
    bar_cache: BarCache,
) -> None:
    """Pre-populate the cache for the symbol-day frame.

    Calls bar_cache.ensure_many() which is idempotent (skips already-cached
    pairs). The cache is keyed (symbol, YYYY-MM-DD) so re-runs are safe.
    """
    from src.research.bar_cache import collect_symbol_dates
    pairs = collect_symbol_dates(sd, symbol_col="symbol", ts_col="cycle_start",
                                 include_next_day=True)
    bar_cache.ensure_many(pairs, batch_size=100, progress_every=5)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="STRAT-002 frozen replication pipeline (memory-safe)")
    parser.add_argument("--holdout-start", default="2026-09-12T00:00:00+00:00")
    parser.add_argument("--holdout-end", default="2026-09-23T00:00:00+00:00")
    parser.add_argument(
        "--eligible-dates",
        default=",".join(DEFAULT_ELIGIBLE_TRADING_DATES),
        help="Comma-separated YYYY-MM-DD list of eligible NY trading dates",
    )
    parser.add_argument("--high-rsi-cutoff", type=float, default=FROZEN_HIGH_RSI_CUTOFF)
    parser.add_argument("--comparison-cutoff", type=float, default=FROZEN_COMPARISON_CUTOFF)
    parser.add_argument("--primary-horizon", default=FROZEN_PRIMARY_HORIZON)
    parser.add_argument("--secondary-horizon", default=FROZEN_SECONDARY_HORIZON)
    parser.add_argument("--cache-dir", default="reports/research/bar_cache")
    parser.add_argument("--output-dir", default="reports/research/strat_002")
    parser.add_argument("--max-cache-age-hours", type=float, default=None,
                        help="Trust cache forever if None (default)")
    parser.add_argument("--per-date-progress", action="store_true",
                        help="Print per-date progress")
    args = parser.parse_args(argv)

    eligible_dates = [d.strip() for d in args.eligible_dates.split(",") if d.strip()]
    print(f"[strat_002] eligible_dates: {eligible_dates}", flush=True)
    print(f"[strat_002] high_rsi_cutoff: {args.high_rsi_cutoff}", flush=True)
    print(f"[strat_002] comparison_cutoff: {args.comparison_cutoff}", flush=True)
    print(f"[strat_002] primary_horizon: {args.primary_horizon}", flush=True)
    print(f"[strat_002] secondary_horizon: {args.secondary_horizon}", flush=True)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("ALPACA_API_KEY")
    api_secret = os.environ.get("ALPACA_API_SECRET")
    if not api_key or not api_secret:
        print("ERROR: ALPACA_API_KEY / ALPACA_API_SECRET not set", flush=True)
        return 2

    bar_cache = BarCache(
        api_key=api_key,
        api_secret=api_secret,
        cache_dir=Path(args.cache_dir),
        max_age_hours=args.max_cache_age_hours,
    )
    horizons = horizon_tuples(args.primary_horizon, args.secondary_horizon)

    primary_return = HORIZON_RETURN_COL[args.primary_horizon]
    primary_status = HORIZON_STATUS_COL[args.primary_horizon]
    secondary_return = HORIZON_RETURN_COL[args.secondary_horizon]
    secondary_status = HORIZON_STATUS_COL[args.secondary_horizon]

    # Accumulators
    all_labeled_chunks: list[pd.DataFrame] = []
    n_total_decisions = 0
    n_total_excluded_no_regular = 0
    n_total_high_rsi_with_RSI = 0
    n_total_comparison_with_RSI = 0

    conn = open_decision_db(uri="file:trading_bot.db?mode=ro")
    try:
        for ny_date in eligible_dates:
            print(f"\n[strat_002] === {ny_date} ===", flush=True)
            t0 = time.time()

            # 1. Load decisions for this NY date (small slice).
            decisions = load_v0_decisions_for_date(conn, ny_date)
            n_loaded = int(len(decisions))
            n_total_decisions += n_loaded
            print(f"[strat_002]   loaded {n_loaded:,} v0 decisions for {ny_date}", flush=True)
            if decisions.empty:
                print(f"[strat_002]   no decisions for {ny_date}", flush=True)
                continue
            gc.collect()

            # 2. Build features (in-place to avoid duplicate copy).
            decisions = build_features_inplace(decisions)
            gc.collect()

            # 3. Collapse to symbol-day (LAST regular-session decision per symbol).
            sd, n_excluded = collapse_one_date(
                decisions, ny_date, high_rsi_cutoff=args.high_rsi_cutoff
            )
            n_total_excluded_no_regular += n_excluded
            print(f"[strat_002]   symbol-days after collapse: {len(sd):,} "
                  f"(excluded no-regular-session: {n_excluded:,})", flush=True)
            del decisions
            gc.collect()

            if sd.empty:
                print(f"[strat_002]   no symbol-days for {ny_date}", flush=True)
                continue

            n_high_date = int((sd["strat_002_cohort"] == "HIGH_RSI").sum())
            n_comp_date = int((sd["strat_002_cohort"] == "COMPARISON").sum())
            n_total_high_rsi_with_RSI += n_high_date
            n_total_comparison_with_RSI += n_comp_date
            print(f"[strat_002]   HIGH_RSI: {n_high_date:,}; COMPARISON: {n_comp_date:,}", flush=True)

            # 4. Fetch bars for this date's symbol-days (idempotent).
            fetch_bars_for_symbol_day_frame(sd, bar_cache)
            gc.collect()

            # 5. Label horizons for this date's symbol-days.
            labeled = label_decisions(sd, bar_cache, horizons=horizons)
            print(f"[strat_002]   labeled: {len(labeled):,} ({time.time()-t0:.1f}s)", flush=True)
            del sd
            gc.collect()

            all_labeled_chunks.append(labeled)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not all_labeled_chunks:
        print("[strat_002] no labeled chunks; aborting", flush=True)
        return 0

    print(f"\n[strat_002] === Aggregating across {len(all_labeled_chunks)} dates ===", flush=True)
    labeled = pd.concat(all_labeled_chunks, ignore_index=True)
    print(f"[strat_002] total labeled rows: {len(labeled):,}", flush=True)

    verdict_primary = compute_verdict(
        labeled,
        horizon_col=primary_return,
        status_col=primary_status,
        cohort_col="strat_002_cohort",
        horizon_name=args.primary_horizon,
    )
    verdict_secondary = compute_verdict(
        labeled,
        horizon_col=secondary_return,
        status_col=secondary_status,
        cohort_col="strat_002_cohort",
        horizon_name=args.secondary_horizon,
    )
    per_date = per_date_diagnostic(
        labeled,
        horizon_col=primary_return,
        status_col=primary_status,
        cohort_col="strat_002_cohort",
        date_col="ny_trading_date",
    )

    labeled.to_csv(out_dir / "symbol_day_labeled.csv", index=False)
    pd.DataFrame([verdict_primary.high_stats.to_dict()]).to_csv(
        out_dir / "high_rsi_stats.csv", index=False
    )
    pd.DataFrame([verdict_primary.comparison_stats.to_dict()]).to_csv(
        out_dir / "comparison_stats.csv", index=False
    )
    pd.DataFrame([verdict_primary.primary.to_dict()]).to_csv(
        out_dir / "between_group_delta_primary.csv", index=False
    )
    pd.DataFrame([verdict_secondary.primary.to_dict()]).to_csv(
        out_dir / "between_group_delta_secondary.csv", index=False
    )
    per_date.to_csv(out_dir / "per_date_diagnostic.csv", index=False)

    summary = {
        "branch": os.environ.get("STRAT_002_BRANCH", ""),
        "commit": os.environ.get("STRAT_002_COMMIT", ""),
        "eligible_dates": eligible_dates,
        "high_rsi_cutoff": args.high_rsi_cutoff,
        "comparison_cutoff": args.comparison_cutoff,
        "primary_horizon": args.primary_horizon,
        "secondary_horizon": args.secondary_horizon,
        "n_v0_decisions_total": n_total_decisions,
        "n_excluded_no_regular_session": n_total_excluded_no_regular,
        "n_symbol_days": int(len(labeled)),
        "n_high_rsi_with_RSI": verdict_primary.primary.n_high_with_RSI,
        "n_high_rsi_OK": verdict_primary.primary.n_high_OK,
        "n_comparison_with_RSI": verdict_primary.primary.n_comparison_with_RSI,
        "n_comparison_OK": verdict_primary.primary.n_comparison_OK,
        "primary_verdict": verdict_primary.verdict,
        "primary_verdict_details": verdict_primary.to_dict(),
        "secondary_verdict": verdict_secondary.verdict,
        "secondary_verdict_details": verdict_secondary.to_dict(),
    }
    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    with open(out_dir / "verdict_primary.json", "w") as f:
        json.dump(verdict_primary.to_dict(), f, indent=2, default=str)
    with open(out_dir / "verdict_secondary.json", "w") as f:
        json.dump(verdict_secondary.to_dict(), f, indent=2, default=str)

    print(f"\n[STRAT-002] PRIMARY VERDICT: {verdict_primary.verdict}", flush=True)
    if verdict_primary.coverage_flag:
        print(f"[STRAT-002] COVERAGE FLAG: {verdict_primary.coverage_flag}", flush=True)
    print(f"[STRAT-002] HIGH_RSI OK={verdict_primary.primary.n_high_OK} "
          f"median={verdict_primary.primary.median_high} "
          f"ppos={verdict_primary.primary.p_positive_high}", flush=True)
    print(f"[STRAT-002] COMPARISON OK={verdict_primary.primary.n_comparison_OK} "
          f"median={verdict_primary.primary.median_comparison} "
          f"ppos={verdict_primary.primary.p_positive_comparison}", flush=True)
    print(f"[STRAT-002] DELTA median={verdict_primary.primary.delta_median} "
          f"ppos={verdict_primary.primary.delta_p_positive}", flush=True)
    print(f"[STRAT-002] SECONDARY VERDICT: {verdict_secondary.verdict}", flush=True)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[strat_002] FATAL: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
