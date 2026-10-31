"""Deterministic synthetic `trading_bot.db` builder for dashboard tests.

ENG-04b
=======

Several dashboard endpoint tests assert on specific symbol snapshots
that exist only in the LIVE production database (`ALPXR` with a full
`decision_snapshot`, `WBS` with a legacy NULL snapshot) or on a live
`cycle_funnel` row. Those tests therefore depend on whatever runtime
state the bot happened to write, which makes them:

  * non-hermetic (they silently read the live DB sitting next to the
    checkout), and
  * fragile (they pass or fail depending on the bot's live runtime
    state rather than on the behaviour they claim to pin).

`dashboard._dashboard_db_path()` honours the `TRADING_BOT_DASHBOARD_DB`
environment variable, so a test can point the dashboard at a
deterministic synthetic database instead. This module builds that
synthetic database.

The schema mirrors the production subset these endpoints read:
`analyzed_stocks`, `decision_history`, and `cycle_funnel`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now_iso(offset_seconds: float = 0.0) -> str:
    """UTC ISO-8601 timestamp anchored near `now()` (ENG-04b).

    Anchoring to `now()` (rather than a fixed calendar date) keeps the
    dashboard's `today` / `24h` / `7d` range filters meaningful for
    synthetic rows.
    """
    ts = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


# ── A representative, fully-populated OBS-001 decision snapshot ───────
#
# The Phase A renderer requires these top-level blocks:
#   schema_version, symbol, decision, strategy_eligibility, scoring,
#   ranking, selection, execution_checks, order
def _alpxr_snapshot() -> str:
    """Return a decision_snapshot JSON string shaped like the live one."""
    return json.dumps({
        "schema_version": 1,
        "symbol": "ALPXR",
        "cycle_id": "cycle_synth_alpxr",
        "session_id": 1,
        "bot_version": "2.1.0",
        "analysis_timestamp": _now_iso(),
        "timeframe_mode": "daily",
        "decision": {
            "signal": "SELL",
            "signal_strength": "DAILY_ONLY",
            "outcome": "SELL_BLOCKED_DYNAMIC",
            "reason": "Daily-only mode; intraday execution blocked.",
        },
        "strategy_eligibility": {
            "signal": "SELL",
            "gates": [
                {
                    "name": "rsi_oversold",
                    "category": "strategy_gate",
                    "applied": True,
                    "passed": False,
                    "observed_value": 100.0,
                    "threshold_value": 35.0,
                    "reason": "RSI 100.0 >= threshold 35.0",
                },
                {
                    "name": "sma_uptrend",
                    "category": "strategy_gate",
                    "applied": True,
                    "passed": False,
                    "observed_value": 0.16007,
                    "threshold_value": 0.16402333333333333,
                    "reason": "SMA fast <= SMA slow",
                },
            ],
        },
        "scoring": {
            "components": {
                "rsi_score": 25.0,
                "sma_score": -12.05,
                "macd_score": 25.0,
                "bb_score": -16.6,
                "catalyst_score": None,
                "regime_score": None,
            },
            "volatility_tier": None,
            "total_score": 21.34,
            "score_invalid_data": False,
        },
        "ranking": {
            "total_score": 21.34,
            "candidate_rank": None,
            "ranked_candidates": 0,
        },
        "selection": {
            "selected": False,
            "max_trades": 3,
            "reason": "Not eligible for BUY selection.",
        },
        "execution_checks": [
            {
                "name": "is_first_blocker",
                "applied": True,
                "passed": False,
                "reason": "Execution blocked in daily-only mode.",
            },
        ],
        "order": {
            "submitted": False,
            "filled": False,
            "fill_confirmed": False,
            "order_id": None,
            "reason": "No order submitted.",
        },
        "multi_timeframe": {
            "daily": {"rsi": 100.0, "sma_fast": 0.16, "sma_slow": 0.164},
        },
        "baseline_diagnostics": {},
        "schema_version_notes": [],
    })


def build_dashboard_db(path: Path) -> Path:
    """Create a deterministic synthetic dashboard DB at `path`.

    Contents:
      * `analyzed_stocks`:
          - ALPXR -> full decision_snapshot (schema_version 1)
          - WBS   -> legacy row, decision_snapshot IS NULL
      * `decision_history`: one row for ALPXR (for /api/decision-history)
      * `cycle_funnel`: one row (for /api/actionability-summary and
        /api/cycle-candidates)

    Returns the path that was written.
    """
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(str(path))
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE analyzed_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            signal TEXT,
            signal_strength TEXT,
            total_score REAL,
            decision_schema_version INTEGER DEFAULT 0,
            decision_snapshot TEXT,
            last_analyzed TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE decision_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            symbol TEXT,
            cycle_start TEXT,
            session_id INTEGER,
            decision_schema_version INTEGER,
            decision_snapshot TEXT,
            analytics_persistence_version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(cycle_id, symbol)
        )
    """)

    cur.execute("""
        CREATE TABLE cycle_funnel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            session_id INTEGER,
            cycle_start TEXT,
            cycle_end TEXT,
            analyzed_count INTEGER,
            strategy_eligible_count INTEGER,
            ranked_candidate_count INTEGER,
            execution_attempt_count INTEGER,
            execution_blocked_count INTEGER,
            order_submission_attempt_count INTEGER,
            order_submitted_count INTEGER,
            order_failed_count INTEGER,
            not_attempted_count INTEGER,
            not_attempted_reason TEXT,
            bot_version TEXT,
            schema_version INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cycle_start = _now_iso(offset_seconds=-60)
    cycle_end = _now_iso(offset_seconds=30)
    cycle_id = "cycle_synth_alpxr"

    # ALPXR: full snapshot (the "modern" shape).
    cur.execute(
        "INSERT INTO analyzed_stocks "
        "(symbol, signal, signal_strength, total_score, "
        " decision_schema_version, decision_snapshot, last_analyzed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ALPXR", "SELL", "DAILY_ONLY", 21.34, 1,
         _alpxr_snapshot(), cycle_start),
    )

    # WBS: legacy row — no decision_snapshot at all.
    cur.execute(
        "INSERT INTO analyzed_stocks "
        "(symbol, signal, signal_strength, total_score, "
        " decision_schema_version, decision_snapshot, last_analyzed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("WBS", "HOLD", "WEAK", 41.0, 0, None, cycle_start),
    )

    cur.execute(
        "INSERT INTO decision_history "
        "(cycle_id, symbol, cycle_start, session_id, "
        " decision_schema_version, decision_snapshot) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cycle_id, "ALPXR", cycle_start, 1, 1, _alpxr_snapshot()),
    )

    cur.execute(
        "INSERT INTO cycle_funnel "
        "(cycle_id, session_id, cycle_start, cycle_end, analyzed_count, "
        " strategy_eligible_count, ranked_candidate_count, "
        " execution_attempt_count, execution_blocked_count, "
        " order_submission_attempt_count, order_submitted_count, "
        " order_failed_count, not_attempted_count, not_attempted_reason, "
        " bot_version, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cycle_id, 1, cycle_start, cycle_end, 1,
         0, 0, 0, 1, 0, 0, 0, 1, "No eligible candidates", "2.1.0", 2),
    )

    conn.commit()
    conn.close()
    return path
