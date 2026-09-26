"""Run the OBS-003 retrospective forward-return research pipeline.

This is the offline entrypoint. It is read-only with respect to the
production database and SmartBot. It only calls the Alpaca historical-
data endpoint, batched by (symbol, calendar_date).

Outputs are written under ``reports/research/`` (gitignored):
    * ``labeled_dataset.csv``            - one row per decision
    * ``labeled_dataset_dedup.csv``      - deduplicated view
    * ``summary_label_coverage.csv``     - per-horizon coverage
    * ``summary_gate_groups.csv``        - per-group return stats
    * ``summary_threshold_proximity.csv`` - proximity-bin return stats
    * ``summary_score_bands.csv``        - per-score-band return stats
    * ``summary_joint_pass.csv``         - rare joint-pass subset
    * ``summary_view_comparison.csv``    - raw vs dedup comparison
    * ``metadata.json``                  - cohort + methodology metadata

Run:

    set -a; source ./.env; set +a
    .venv/bin/python -u -m src.research.run_obs_003
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.research.analysis import (
    attach_view_marker,
    gate_group_returns,
    joint_pass_analysis,
    score_band_returns,
    threshold_proximity_returns,
    view_comparison,
)
from src.research.labeling import (
    DEFAULT_HORIZONS,
    PipelineConfig,
    run_pipeline,
)


HORIZONS = [h[0] for h in DEFAULT_HORIZONS]


def main() -> int:
    out_dir = Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Research window: v1 cutover -> 2026-09-24T17:00:00Z.
    # End chosen so every decision has full +240 trading-minute horizon
    # available through 2026-09-25T21:00:00Z (next-session close), which
    # is confirmed by the Alpaca paper-tier historical-bar probe.
    cfg = PipelineConfig(
        start_iso="2026-09-23T23:53:16",
        end_iso="2026-09-24T17:00:00",
        horizons=DEFAULT_HORIZONS,
        # Trust the local cache files for this run; cache was populated
        # by the initial API fetch in this same session.
        max_cache_age_hours=None,
    )

    print(f"[{_ts()}] Starting OBS-003 pipeline", flush=True)
    print(f"  research window: {cfg.start_iso} .. {cfg.end_iso}", flush=True)
    print(f"  horizons: {HORIZONS}", flush=True)
    print(f"  cache_dir: {cfg.cache_dir}", flush=True)

    t0 = time.time()
    raw, dedup, summary = run_pipeline(cfg)
    elapsed = time.time() - t0
    print(f"[{_ts()}] Pipeline complete in {elapsed:.1f}s", flush=True)
    print(f"  raw rows: {len(raw)}", flush=True)
    print(f"  dedup rows: {len(dedup)}", flush=True)

    # 1. Coverage summary
    summary_path = out_dir / "summary_label_coverage.csv"
    summary.to_csv(summary_path, index=False)
    print(f"  wrote {summary_path}")

    # 2. Gate-group returns
    raw_g = gate_group_returns(attach_view_marker(raw, "raw"), HORIZONS)
    dedup_g = gate_group_returns(attach_view_marker(dedup, "dedup"), HORIZONS)
    gate_groups = pd.concat([raw_g, dedup_g], ignore_index=True)
    gate_path = out_dir / "summary_gate_groups.csv"
    gate_groups.to_csv(gate_path, index=False)
    print(f"  wrote {gate_path}")

    # 3. Threshold-proximity returns
    raw_tp = threshold_proximity_returns(attach_view_marker(raw, "raw"), HORIZONS)
    dedup_tp = threshold_proximity_returns(attach_view_marker(dedup, "dedup"), HORIZONS)
    tp = pd.concat([raw_tp, dedup_tp], ignore_index=True)
    tp_path = out_dir / "summary_threshold_proximity.csv"
    tp.to_csv(tp_path, index=False)
    print(f"  wrote {tp_path}")

    # 4. Score-band returns
    raw_sb = score_band_returns(attach_view_marker(raw, "raw"), HORIZONS)
    dedup_sb = score_band_returns(attach_view_marker(dedup, "dedup"), HORIZONS)
    sb = pd.concat([raw_sb, dedup_sb], ignore_index=True)
    sb_path = out_dir / "summary_score_bands.csv"
    sb.to_csv(sb_path, index=False)
    print(f"  wrote {sb_path}")

    # 5. Joint-pass analysis
    raw_jp = joint_pass_analysis(attach_view_marker(raw, "raw"), HORIZONS)
    dedup_jp = joint_pass_analysis(attach_view_marker(dedup, "dedup"), HORIZONS)
    jp = pd.concat([raw_jp, dedup_jp], ignore_index=True)
    jp_path = out_dir / "summary_joint_pass.csv"
    jp.to_csv(jp_path, index=False)
    print(f"  wrote {jp_path}")

    # 6. View comparison
    vc = view_comparison(raw, dedup, HORIZONS)
    vc_path = out_dir / "summary_view_comparison.csv"
    vc.to_csv(vc_path, index=False)
    print(f"  wrote {vc_path}")

    # 7. Labeled dataset CSVs (one row per decision)
    raw_csv = out_dir / "labeled_dataset.csv"
    _write_labeled_csv(raw, raw_csv)
    print(f"  wrote {raw_csv}")
    dedup_csv = out_dir / "labeled_dataset_dedup.csv"
    _write_labeled_csv(dedup, dedup_csv)
    print(f"  wrote {dedup_csv}")

    # 8. Metadata
    metadata = _build_metadata(
        cfg, raw, dedup, summary, elapsed
    )
    md_path = out_dir / "metadata.json"
    md_path.write_text(json.dumps(metadata, indent=2, default=str))
    print(f"  wrote {md_path}")

    print(f"[{_ts()}] Done.")
    return 0


def _write_labeled_csv(df: pd.DataFrame, path: Path) -> None:
    """Write the labeled dataset to CSV.

    Drop the ``features`` column (already flattened to ``feat_*``) and
    drop ``decision_snapshot`` (large, redundant for analysis).
    """
    cols = list(df.columns)
    keep = [c for c in cols if c not in ("features", "decision_snapshot")]
    df[keep].to_csv(path, index=False)


def _build_metadata(cfg, raw, dedup, summary, elapsed) -> dict:
    """Build the metadata.json sidecar."""
    n_raw = len(raw)
    n_dedup = len(dedup)
    unique_symbols = sorted(set(raw["symbol"].unique()))
    n_unique = len(unique_symbols)
    sym_date_pairs = raw.apply(
        lambda r: f"{r['symbol']}|{r['cycle_start'].tz_convert('UTC').strftime('%Y-%m-%d')}",
        axis=1,
    )
    n_pairs = len(set(sym_date_pairs))
    return {
        "pipeline": "OBS-003 retrospective forward-return research",
        "ran_at_utc": _ts(),
        "elapsed_seconds": elapsed,
        "research_window": {
            "start_iso": cfg.start_iso,
            "end_iso": cfg.end_iso,
            "rationale": (
                "v1 cutover = 2026-09-23T23:53:16Z. End = 2026-09-24T17:00:00Z. "
                "Latest decision can fit +240 trading-minute horizon within "
                "next-session Alpaca data (which extends through 2026-09-25T21:00Z)."
            ),
        },
        "horizons": [h[0] for h in (cfg.horizons or DEFAULT_HORIZONS)],
        "horizon_semantics": {
            "fwd_30m": "30 trading-minute forward close (regular-session minutes only).",
            "fwd_60m": "60 trading-minute forward close.",
            "fwd_240m": "240 trading-minute forward close (= 4 trading hours).",
            "fwd_next_session_open": "first regular-session bar of the next trading day.",
            "trading_session": "14:30 <= t < 21:00 UTC",
        },
        "price_semantics": {
            "decision_price": "1-minute bar close at-or-before cycle_start.",
            "look_ahead_protection": (
                "Decision bar is at-or-BEFORE cycle_start. "
                "Forward bar is at-or-AFTER target timestamp. "
                "No bars from the open interval are used for the label value."
            ),
        },
        "deduplication_rule": (
            "Time-based: within (symbol, UTC trading date), keep at most one "
            "observation every 30 minutes. First observation in each 30-min "
            "window is retained; subsequent ones are dropped. Spacing is "
            "measured in wall-clock minutes from the kept observation's "
            "cycle_start. Rule documented in src/research/deduplication.py."
        ),
        "cohort": {
            "raw_decisions": n_raw,
            "dedup_decisions": n_dedup,
            "unique_symbols": n_unique,
            "unique_symbol_date_pairs": n_pairs,
            "dedup_ratio": (n_dedup / n_raw) if n_raw else 0.0,
            "clarification_of_13_559_figure": (
                "The '13,559' figure cited in the OBS-003A archive was the count "
                "of unique symbols seen in v1 since cutover up to the moment the "
                "probe was taken. The current cohort uses a slightly later window "
                "cutoff (2026-09-24T17:00Z) and contains 13,522 unique symbols. "
                "The numbers are equivalent (the same data, just a different cutoff "
                "endpoint) and represent 'unique symbols seen at least once in v1'."
            ),
        },
        "label_coverage": summary.to_dict(orient="records"),
        "alpaca_data_used": True,
        "production_db_writes": False,
        "smartbot_modified": False,
        "schema_modified": False,
        "strategy_modified": False,
        "thresholds_changed": False,
    }


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    sys.exit(main())
