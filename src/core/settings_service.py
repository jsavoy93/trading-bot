"""
Settings Service - persists trading params to SQLite.
Used by both the dashboard (FastAPI) and the trading bot (main.py).
Keeps them in sync without sharing process memory.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger(__name__)

# Path to the shared trading bot DB
_DB_PATH = Path(__file__).parent.parent.parent / "trading_bot.db"


@dataclass(frozen=True)
class StrategySetting:
    """Authoritative metadata for one strategy setting."""

    key: str
    default: Any
    param_type: str
    minimum: Optional[float]
    maximum: Optional[float]
    step: Optional[float]
    description: str
    category: str

    def dashboard_metadata(self, value: Any) -> Dict[str, Any]:
        return {
            "value": value,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "type": self.param_type,
            "default": self.default,
            "description": self.description,
            "category": self.category,
        }


STRATEGY_SETTINGS_SCHEMA: Dict[str, StrategySetting] = {
    "rsi_buy_threshold": StrategySetting(
        "rsi_buy_threshold", 30, "int", 10, 50, 1,
        "RSI must be below this to generate a BUY signal.", "Signal Thresholds"
    ),
    "rsi_sell_threshold": StrategySetting(
        "rsi_sell_threshold", 70, "int", 50, 90, 1,
        "RSI must be above this to generate a SELL signal.", "Signal Thresholds"
    ),
    "min_score_buy": StrategySetting(
        "min_score_buy", 50, "int", 0, 100, 1,
        "Minimum total score required for a BUY signal.", "Signal Thresholds"
    ),
    "rotation_threshold": StrategySetting(
        "rotation_threshold", 20, "int", 0, 50, 1,
        "Rotation score advantage threshold.", "Signal Thresholds"
    ),
    "sma_fast": StrategySetting(
        "sma_fast", 10, "int", 2, 50, 1,
        "Fast SMA period.", "Indicator Periods"
    ),
    "sma_slow": StrategySetting(
        "sma_slow", 30, "int", 5, 200, 1,
        "Slow SMA period.", "Indicator Periods"
    ),
    "rsi_period": StrategySetting(
        "rsi_period", 14, "int", 2, 50, 1,
        "RSI lookback period.", "Indicator Periods"
    ),
    "enable_multi_timeframe": StrategySetting(
        "enable_multi_timeframe", True, "bool", None, None, None,
        "Require daily AND hourly agreement.", "Multi-Timeframe"
    ),
    "hourly_weight": StrategySetting(
        "hourly_weight", 0.30, "float", 0.0, 1.0, 0.05,
        "Weight for hourly signals.", "Multi-Timeframe"
    ),
    "enable_volume_confirmation": StrategySetting(
        "enable_volume_confirmation", True, "bool", None, None, None,
        "Require volume above 20-day average.", "Filters"
    ),
    "enable_mtf_conflict_filter": StrategySetting(
        "enable_mtf_conflict_filter", True, "bool", None, None, None,
        "Block BUY if daily/hourly signals conflict.", "Filters"
    ),
    "enable_vol_downgrade_filter": StrategySetting(
        "enable_vol_downgrade_filter", True, "bool", None, None, None,
        "Block BUY if volume is below average (vol downgrade).", "Filters"
    ),
    "enable_ai_conflict_filter": StrategySetting(
        "enable_ai_conflict_filter", True, "bool", None, None, None,
        "Block BUY if AI recommendation conflicts with technical signal.", "Filters"
    ),
    "enable_regime_filter": StrategySetting(
        "enable_regime_filter", True, "bool", None, None, None,
        "ADX-based regime detection.", "Filters"
    ),
    "enable_sp_filter": StrategySetting(
        "enable_sp_filter", True, "bool", None, None, None,
        "Only buy stocks outperforming SPY.", "Filters"
    ),
    "enable_liquidity_filter": StrategySetting(
        "enable_liquidity_filter", True, "bool", None, None, None,
        "Skip illiquid stocks.", "Filters"
    ),
    "enable_sector_filter": StrategySetting(
        "enable_sector_filter", True, "bool", None, None, None,
        "Prefer strong sectors.", "Filters"
    ),
    "loop_delay_seconds": StrategySetting(
        "loop_delay_seconds", 300, "int", 10, 3600, 10,
        "Seconds between analysis loops.", "Bot Settings"
    ),
    "atr_position_size_pct": StrategySetting(
        "atr_position_size_pct", 2.0, "float", 0.1, 10.0, 0.1,
        "Risk per trade as % of portfolio.", "Risk & Sizing"
    ),
    "max_sector_concentration": StrategySetting(
        "max_sector_concentration", 0.25, "float", 0.05, 0.80, 0.05,
        "Max % in any single sector.", "Risk & Sizing"
    ),
    "max_correlation": StrategySetting(
        "max_correlation", 0.7, "float", 0.0, 1.0, 0.05,
        "Max correlation with existing positions.", "Risk & Sizing"
    ),
    "max_portfolio_beta": StrategySetting(
        "max_portfolio_beta", 1.5, "float", 0.0, 3.0, 0.1,
        "Max portfolio beta.", "Risk & Sizing"
    ),
    "max_drawdown_pct": StrategySetting(
        "max_drawdown_pct", 10.0, "float", 1.0, 50.0, 0.5,
        "Max drawdown before pause.", "Risk & Sizing"
    ),
    "daily_loss_limit_pct": StrategySetting(
        "daily_loss_limit_pct", 5.0, "float", 0.5, 20.0, 0.5,
        "Max daily loss before stopping.", "Risk & Sizing"
    ),
}


def _get_conn() -> sqlite3.Connection:
    """Get a DB connection. Creates tables if they don't exist."""
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create the settings table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key    TEXT PRIMARY KEY,
            value  TEXT
        )
    """)
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def get(key: str, default: Any = None) -> Any:
    """
    Get a single setting value by key.
    Returns default if the key doesn't exist.
    """
    try:
        conn = _get_conn()
        cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return default
        return row["value"]
    except Exception as e:
        log.warning(f"settings_service.get({key!r}) failed: {e}")
        return default


def save(key: str, value: Any) -> None:
    """Save a single setting. Overwrites if exists."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"settings_service.save({key!r}, {value!r}) failed: {e}")


def load_all() -> Dict[str, Any]:
    """
    Load all settings as a dict of {key: raw_string_value}.
    Use load_typed() or load_effective_strategy_settings() for deserialised values.
    """
    try:
        conn = _get_conn()
        cur = conn.execute("SELECT key, value FROM settings")
        rows = cur.fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}
    except Exception as e:
        log.error(f"settings_service.load_all() failed: {e}")
        return {}


# ── Typed helpers for dashboard and bot compatibility ───────────────────────

def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    raise ValueError(f"invalid boolean setting value: {value!r}")


def _coerce_value(value: Any, param_type: str) -> Any:
    if param_type == "int":
        coerced = int(float(value))
        if float(value) != float(coerced):
            raise ValueError(f"invalid integer setting value: {value!r}")
        return coerced
    if param_type == "float":
        return float(value)
    if param_type == "bool":
        return _coerce_bool(value)
    if param_type == "str":
        return str(value)
    raise ValueError(f"unknown setting type: {param_type!r}")


def validate_typed(key: str, value: Any, param_type: Optional[str] = None) -> Any:
    """Coerce and validate a value for a known strategy setting or explicit type."""
    definition = STRATEGY_SETTINGS_SCHEMA.get(key)
    effective_type = definition.param_type if definition else param_type
    if effective_type is None:
        return value

    coerced = _coerce_value(value, effective_type)
    if definition and effective_type in ("int", "float"):
        numeric = float(coerced)
        if definition.minimum is not None and numeric < definition.minimum:
            raise ValueError(f"{key} must be >= {definition.minimum}")
        if definition.maximum is not None and numeric > definition.maximum:
            raise ValueError(f"{key} must be <= {definition.maximum}")
    return coerced


def serialize_typed(value: Any, param_type: str) -> str:
    """Serialize a typed setting value for storage."""
    if param_type == "bool":
        return "true" if value else "false"
    return str(value)


def save_typed(key: str, value: Any, param_type: Optional[str] = None) -> None:
    """
    Validate, serialize, and save a setting.

    Known strategy setting keys use the authoritative schema. Unknown keys keep
    the historical explicit type path for compatibility.
    """
    definition = STRATEGY_SETTINGS_SCHEMA.get(key)
    effective_type = definition.param_type if definition else param_type
    if effective_type is None:
        save(key, value)
        return
    coerced = validate_typed(key, value, effective_type)
    save(key, serialize_typed(coerced, effective_type))


def load_typed(key: str, default: Any, param_type: str) -> Any:
    """
    Load a value and cast it to the correct Python type.
    param_type: 'int' | 'float' | 'bool' | 'str'
    """
    raw = get(key)
    if raw is None:
        return default
    return validate_typed(key, raw, param_type)


def load_effective_strategy_settings() -> Dict[str, Any]:
    """Return effective strategy settings from DB overrides plus schema defaults."""
    effective: Dict[str, Any] = {}
    for key, definition in STRATEGY_SETTINGS_SCHEMA.items():
        effective[key] = load_typed(key, definition.default, definition.param_type)
    return effective


def dashboard_parameters(effective: Optional[Mapping[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Return dashboard-ready metadata derived from the authoritative schema."""
    values = dict(effective) if effective is not None else load_effective_strategy_settings()
    return {
        key: definition.dashboard_metadata(values.get(key, definition.default))
        for key, definition in STRATEGY_SETTINGS_SCHEMA.items()
    }


def format_effective_strategy_settings_for_log(settings: Mapping[str, Any]) -> str:
    """Bounded deterministic startup-log representation of non-secret settings."""
    parts = [f"{key}={settings[key]!r}" for key in sorted(STRATEGY_SETTINGS_SCHEMA)]
    return "strategy_settings{" + ", ".join(parts) + "}"
