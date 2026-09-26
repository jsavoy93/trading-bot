"""Read-only access to ``decision_history`` for OBS-003.

The production database is opened with ``file:trading_bot.db?mode=ro`` so
that any code that mistakenly tries to write will fail rather than
silently mutate the database.

This module does NOT contain any trading, brokerage, or schema-modifying
logic.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


DEFAULT_DB_URI = "file:trading_bot.db?mode=ro"


def open_decision_db(db_path: str | Path = "trading_bot.db",
                     uri: str = DEFAULT_DB_URI) -> sqlite3.Connection:
    """Open the production ``decision_history`` table read-only.

    Defaults to ``file:trading_bot.db?mode=ro``. The ``?mode=ro`` part of
    the URI is enforced; if a caller passes a bare path it is converted
    to a read-only URI.
    """
    if "mode=ro" not in uri:
        # If the caller passed a plain path, convert it to a ro URI.
        uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    return conn


def load_v1_decisions(
    conn: sqlite3.Connection,
    start_iso: str,
    end_iso: str,
    symbols: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Load v1 decisions in ``[start_iso, end_iso)`` as a DataFrame.

    Columns:
        id, symbol, cycle_start, session_id, decision_snapshot (dict),
        created_at, analytics_persistence_version (=1)
    """
    sql = """
        SELECT id, symbol, cycle_start, session_id, decision_snapshot,
               created_at, analytics_persistence_version
        FROM decision_history
        WHERE analytics_persistence_version = 1
          AND cycle_start >= ?
          AND cycle_start < ?
    """
    params: list = [start_iso, end_iso]
    if symbols is not None:
        sym_list = list(symbols)
        if not sym_list:
            return pd.DataFrame(columns=[
                "id", "symbol", "cycle_start", "session_id",
                "decision_snapshot", "created_at",
                "analytics_persistence_version",
            ])
        placeholders = ",".join(["?"] * len(sym_list))
        sql += f" AND symbol IN ({placeholders})"
        params.extend(sym_list)

    sql += " ORDER BY cycle_start, id"

    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    df["decision_snapshot"] = df["decision_snapshot"].apply(
        lambda s: json.loads(s) if isinstance(s, str) else s
    )
    df["cycle_start"] = pd.to_datetime(df["cycle_start"], utc=True)
    return df


def count_distinct(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> int:
    """Run a COUNT query and return the integer result."""
    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    if not row:
        return 0
    return int(row[0])
