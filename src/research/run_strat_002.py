"""STRAT-002 out-of-sample replication pipeline.

Frozen pre-registration (PR #98, commit e62a31e, merged as c8da017):

    Eligibility:   7 NY trading dates (Sept 14-18, 21-22, 2026)
    Unit:          SYMBOL × TRADING DAY
    Representative: LAST cycle whose cycle_start in NY local time
                    falls in [09:30, 16:00) ET on a weekday
                    (reuses OBS-003 is_regular_session_minute predicate)
    Cohorts:       HIGH_RSI (RSI >= 55), COMPARISON (RSI < 55)
    Primary horizon: next_session_open
    Secondary:     +240m wall-clock
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

It does NOT alter SmartBot, settings, schema, or services.

Inputs (CLI flags):
    --holdout-start, --holdout-end (ISO calendar bounds)
    --eligible-dates (comma-separated YYYY-MM-DD list)
    --high-rsi-cutoff (frozen: 55)
    --comparison-cutoff (frozen: 55)
    --primary-horizon (frozen: next_session_open)
    --secondary-horizon (frozen: fwd_240m)
    --cache-dir (default: reports/research/bar_cache)
    --output-dir (default: reports/research/strat_002)
    --max-acquire-workers (concurrent Alpaca fetches; default 4)
    --acquire-batch-size (default 100)
    --acquire-only (skip labeling; useful for resumable runs)

Outputs (under output-dir):
    symbol_day_labeled.csv     — per-symbol-day frame with horizons
    high_rsi_stats.csv         — GroupStats for HIGH_RSI
    comparison_stats.csv       — GroupStats for COMPARISON
    between_group_delta.csv    — BetweenGroupDelta
    verdict.json               — frozen verdict
    per_date_diagnostic.csv    — per-date per-cohort diagnostic
    summary.json               — top-level summary for audit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from src.research.bar_cache import BarCache, collect_symbol_dates
from src.research.deduplication import deduplicate_by_gate_state
from src.research.decision_source import open_decision_db
from src.research.feature_extraction import extract_features
from src.research.labeling import (
    DEFAULT_HORIZONS,
    build_features,
    label_decisions,
)
from src.research.symbol_day import (
    assign_strat_002_cohort,
    collapse_to_symbol_days,
)
from src.research.strat_002_verdict import (
    BetweenGroupDelta,
    GroupStats,
    Verdict,
    compute_between_group_delta,
    compute_verdict,
    per_date_diagnostic,
)


# Frozen eligibility (PR #98)
DEFAULT_ELIGIBLE_TRADING_DATES = [
    "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
    "2026-09-21", "2026-09-22",
]
DEFAULT_HOLDOUT_START = "2026-09-12T00:00:00+00:00"
DEFAULT_HOLDOUT_END = "2026-09-23T00:00:00+00:00"

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


def load_v0_decisions(
    conn,
    start_iso: str,
    end_iso: str,
    eligible_ny_dates: Iterable[str],
) -> pd.DataFrame:
    """Load v0 decisions from ``decision_history`` for the eligible dates.

    ``v0`` here means ``analytics_persistence_version = 0`` (the OBS-001
    Phase A pre-OBS-002 schema). The snapshot structure is the same as
    v1; we filter by cycle_start in the eligible NY trading date
    union plus the surrounding day so any cycle_start within that
    window is included.

    The representative-decision rule (regular-session in NY local
    time) is applied later by ``collapse_to_symbol_days``.
    """
    eligible_set = set(eligible_ny_dates)
    if not eligible_set:
        return pd.DataFrame(columns=[
            "id", "symbol", "cycle_start", "session_id", "decision_snapshot",
            "created_at", "analytics_persistence_version",
        ])

    # Build a date range covering eligible_dates +/- 1 day so we don't
    # miss late-evening or early-morning decisions adjacent to a
    # trading date. The representative-decision rule then filters
    # down to [09:30, 16:00) ET on the eligible NY date.
    sorted_dates = sorted(eligible_set)
    iso_start = f"{sorted_dates[0]}T00:00:00+00:00"
    # End-of-day UTC for the LAST eligible date (exclusive next-day 00:00 UTC)
    iso_end = f"{sorted_dates[-1]}T23:59:59+00:00"

    sql = """
        SELECT id, symbol, cycle_start, session_id, decision_snapshot,
               created_at, analytics_persistence_version
        FROM decision_history
        WHERE analytics_persistence_version = 0
          AND cycle_start >= ?
          AND cycle_start <= ?
        ORDER BY cycle_start, id
    """
    cur = conn.execute(sql, (iso_start, iso_end))
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    import json as _json
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    df["decision_snapshot"] = df["decision_snapshot"].apply(
        lambda s: _json.loads(s) if isinstance(s, str) else s
    )
    df["cycle_start"] = pd.to_datetime(df["cycle_start"], utc=True, format="ISO8601")
    # Convert cycle_start to NY-local date and keep only those whose
    # NY trading date is in the eligible set. This is a coarse filter
    # before the regular-session filter.
    from src.research.symbol_day import ny_trading_date
    df["_ny_date"] = df["cycle_start"].map(ny_trading_date)
    df = df[df["_ny_date"].astype(str).isin(eligible_set)].drop(columns=["_ny_date"]).reset_index(drop=True)
    return df


def apply_strat_002_unit_and_cohort(
    decisions_with_features: pd.DataFrame,
    high_rsi_cutoff: float = FROZEN_HIGH_RSI_CUTOFF,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply symbol-day collapse and cohort classification.

    Returns (symbol_day_frame, excluded_decisions).
    """
    symbol_day, excluded = collapse_to_symbol_days(
        decisions_with_features,
        symbol_col="symbol",
        cycle_start_col="cycle_start",
    )
    if symbol_day.empty:
        return symbol_day, excluded

    symbol_day = symbol_day.copy()
    symbol_day["strat_002_cohort"] = symbol_day["rsi_value"].map(
        lambda v: assign_strat_002_cohort(v, high_rsi_cutoff=high_rsi_cutoff)
    )
    # Exclude symbol-days where RSI is unknown (None / NaN)
    n_total = len(symbol_day)
    symbol_day = symbol_day[symbol_day["strat_002_cohort"].notna()].reset_index(drop=True)
    return symbol_day, excluded


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="STRAT-002 frozen replication pipeline")
    parser.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    parser.add_argument("--holdout-end", default=DEFAULT_HOLDOUT_END)
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
    parser.add_argument("--acquire-only", action="store_true",
                        help="Fetch bars only; do not run labeling")
    parser.add_argument("--label-only", action="store_true",
                        help="Skip fetch; use existing cache only")
    args = parser.parse_args(argv)

    eligible_dates = [d.strip() for d in args.eligible_dates.split(",") if d.strip()]
    print(f"[strat_002] eligible_dates: {eligible_dates}", flush=True)
    print(f"[strat_002] high_rsi_cutoff: {args.high_rsi_cutoff}", flush=True)
    print(f"[strat_002] comparison_cutoff: {args.comparison_cutoff}", flush=True)
    print(f"[strat_002] primary_horizon: {args.primary_horizon}", flush=True)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("ALPACA_API_KEY")
    api_secret = os.environ.get("ALPACA_API_SECRET")
    if not api_key or not api_secret:
        print("ERROR: ALPACA_API_KEY / ALPACA_API_SECRET not set", flush=True)
        return 2

    conn = open_decision_db(uri="file:trading_bot.db?mode=ro")
    try:
        decisions = load_v0_decisions(
            conn, args.holdout_start, args.holdout_end, eligible_dates
        )
        print(f"[strat_002] loaded v0 decisions (eligible-date pre-filter): {len(decisions):,}", flush=True)
        if decisions.empty:
            print("[strat_002] no decisions in eligible dates; aborting", flush=True)
            return 0
        decisions = build_features(decisions)
        # Pull rsi_value out of the per-row features dict so the cohort
        # classifier can use it without first flattening to feat_* cols.
        if "rsi_value" not in decisions.columns and "features" in decisions.columns:
            decisions = decisions.copy()
            decisions["rsi_value"] = decisions["features"].map(
                lambda f: f.get("rsi_value") if isinstance(f, dict) else None
            )
        symbol_day, excluded = apply_strat_002_unit_and_cohort(
            decisions, high_rsi_cutoff=args.high_rsi_cutoff
        )
        print(f"[strat_002] symbol-days: {len(symbol_day):,}", flush=True)
        print(f"[strat_002] excluded (no regular-session decision): {len(excluded):,}", flush=True)
        if symbol_day.empty:
            print("[strat_002] no symbol-days survived collapse; aborting", flush=True)
            return 0

        n_high = int((symbol_day["strat_002_cohort"] == "HIGH_RSI").sum())
        n_comp = int((symbol_day["strat_002_cohort"] == "COMPARISON").sum())
        print(f"[strat_002] HIGH_RSI symbol-days: {n_high:,}", flush=True)
        print(f"[strat_002] COMPARISON symbol-days: {n_comp:,}", flush=True)

        if args.acquire_only:
            print("[strat_002] --acquire-only: skipping labeling", flush=True)
            return 0

        bar_cache = BarCache(
            api_key=api_key,
            api_secret=api_secret,
            cache_dir=Path(args.cache_dir),
            max_age_hours=args.max_cache_age_hours,
        )

        # horizons: list of (name, n_wall_clock_minutes). None means
        # next-session-open (uses the OBS-003 forward_bar_next_session_open
        # branch). The OBS-003 DEFAULT_HORIZONS uses the same names.
        horizons = []
        for h in (args.primary_horizon, args.secondary_horizon):
            if h == "fwd_next_session_open":
                horizons.append((h, None))
            elif h.startswith("fwd_") and h.endswith("m"):
                horizons.append((h, int(h[4:-1])))
            else:
                raise ValueError(f"Unknown horizon name: {h}")
        # Label the symbol-day frame
        labeled = label_decisions(symbol_day, bar_cache, horizons=horizons)
        print(f"[strat_002] labeled: {len(labeled):,}", flush=True)

        primary_return = HORIZON_RETURN_COL[args.primary_horizon]
        primary_status = HORIZON_STATUS_COL[args.primary_horizon]
        secondary_return = HORIZON_RETURN_COL[args.secondary_horizon]
        secondary_status = HORIZON_STATUS_COL[args.secondary_horizon]

        # Verdict for primary horizon
        verdict_primary = compute_verdict(
            labeled,
            horizon_col=primary_return,
            status_col=primary_status,
            cohort_col="strat_002_cohort",
            horizon_name=args.primary_horizon,
        )
        # Verdict for secondary horizon
        verdict_secondary = compute_verdict(
            labeled,
            horizon_col=secondary_return,
            status_col=secondary_status,
            cohort_col="strat_002_cohort",
            horizon_name=args.secondary_horizon,
        )

        # Per-date diagnostic (primary)
        per_date = per_date_diagnostic(
            labeled,
            horizon_col=primary_return,
            status_col=primary_status,
            cohort_col="strat_002_cohort",
            date_col="ny_trading_date",
        )

        # Persist outputs
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
            "n_v0_decisions_eligible_pre_filter": int(len(decisions)),
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
    except Exception as e:
        print(f"[strat_002] ERROR: {e}", flush=True)
        traceback.print_exc()
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
