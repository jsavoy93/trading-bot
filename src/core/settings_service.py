"""
Settings Service - persists trading params to SQLite.
Used by both the dashboard (FastAPI) and the trading bot (main.py).
Keeps them in sync without sharing process memory.
"""
import sqlite3
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Path to the shared trading bot DB
_DB_PATH = Path(__file__).parent.parent.parent / "trading_bot.db"


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
    Use load_typed() for deserialised values.
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


# ── Typed helpers for dashboard compat ───────────────────────────────────────

def save_typed(key: str, value: Any, param_type: str) -> None:
    """
    Save a value that matches the dashboard param type
    ('int', 'float', 'bool', 'str').
    """
    if param_type == "bool":
        value = "true" if value else "false"
    elif param_type == "int":
        value = str(int(value))
    elif param_type == "float":
        value = str(float(value))
    else:
        value = str(value)
    save(key, value)


def load_typed(key: str, default: Any, param_type: str) -> Any:
    """
    Load a value and cast it to the correct Python type.
    param_type: 'int' | 'float' | 'bool' | 'str'
    """
    raw = get(key)
    if raw is None:
        return default
    if param_type == "int":
        return int(float(raw))
    if param_type == "float":
        return float(raw)
    if param_type == "bool":
        return raw.lower() in ("true", "1", "yes")
    return str(raw)
