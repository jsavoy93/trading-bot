"""STRAT-025 engine configuration.

Every setup/entry flag DEFAULTS OFF. Nothing trades unless explicitly enabled:
  STRAT025_ENABLE_INTRADAY_SCAN=1   intraday scanner loop places entries
  STRAT025_ENABLE_EOD_ENTRIES=1     15:45 ET daily-bar setup evaluation
  STRAT025_SETUP_<NAME>=1           enable setup predicate <NAME> (see setups.py)

All values overridable via env vars prefixed STRAT025_.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

ORDER_TAG = "sl_short"          # client_order_id prefix on every order
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"   # Alpaca PAPER only
PAPER_DATA_URL = "https://data.alpaca.markets"

# Read-only sources in the MAIN workspace (never written by this engine)
MAIN_WS = "/root/.openclaw/workspace/trading-bot"
C4_UNIVERSE_FILE = f"{MAIN_WS}/experiments/strat_006r_candidate_universe.py"
MAIN_WATCHLIST_JSON = f"{MAIN_WS}/analysis_status.json"   # symbols the main bot tracks

# EOD entry evaluation: 15 minutes before the 16:00 ET close
EOD_EVAL_TIME_ET = "15:45"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # --- loops (default OFF; the runner also refuses without paper guard) ---
    enable_intraday_scan: bool = field(default_factory=lambda: _env_bool("STRAT025_ENABLE_INTRADAY_SCAN"))
    enable_eod_entries: bool = field(default_factory=lambda: _env_bool("STRAT025_ENABLE_EOD_ENTRIES"))
    scan_interval_sec: int = field(default_factory=lambda: min(300, max(60, _env_int("STRAT025_SCAN_INTERVAL_SEC", 300))))

    # --- risk controls ---
    risk_per_trade_pct: float = field(default_factory=lambda: _env_float("STRAT025_RISK_PER_TRADE_PCT", 1.0))   # % of sleeve equity
    daily_loss_limit_pct: float = field(default_factory=lambda: _env_float("STRAT025_DAILY_LOSS_LIMIT_PCT", 3.0))
    max_positions_per_sector: int = field(default_factory=lambda: _env_int("STRAT025_MAX_POSITIONS_PER_SECTOR", 3))
    max_open_positions: int = field(default_factory=lambda: _env_int("STRAT025_MAX_OPEN_POSITIONS", 10))
    sleeve_equity_floor_usd: float = field(default_factory=lambda: _env_float("STRAT025_SLEEVE_EQUITY_FLOOR_USD", 100.0))

    # --- earnings blackout: skip entries within N trading days of earnings ---
    earnings_blackout_days: int = field(default_factory=lambda: _env_int("STRAT025_EARNINGS_BLACKOUT_DAYS", 1))

    # --- kill switch ---
    kill_switch_env: str = "STRAT025_KILL_SWITCH"
    kill_switch_file: str = os.environ.get("STRAT025_KILL_FILE",
                                           os.path.join(os.path.dirname(__file__), "..", "KILL"))
    kill_flatten: bool = field(default_factory=lambda: _env_bool("STRAT025_KILL_FLATTEN"))

    # --- paths / endpoints ---
    db_path: str = os.environ.get("STRAT025_DB",
                                  os.path.join(os.path.dirname(__file__), "..", "swing.db"))

    # setup enable flags are read through setups.is_setup_enabled()
    eod_eval_time_et: str = EOD_EVAL_TIME_ET
