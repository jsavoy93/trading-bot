"""
SQLite database integration - single source of truth for all bot data.
Replaces Supabase REST API with a local SQLite database.
"""
import sqlite3
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

DB_PATH = Path(__file__).parent.parent.parent / "trading_bot.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _row_to_dict(row) -> Dict:
    return dict(row) if row else {}


class SQLiteDB:
    """SQLite-backed database with the same interface as SimpleSupabaseREST."""

    def __init__(self):
        self.available = False
        self.current_session_id = None
        self._init_schema()

    def _init_schema(self):
        try:
            with _get_conn() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS trading_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_start TEXT NOT NULL,
                        session_end TEXT,
                        bot_version TEXT,
                        configuration TEXT,
                        is_paper_trading INTEGER DEFAULT 1,
                        notes TEXT,
                        total_symbols_processed INTEGER DEFAULT 0,
                        total_trades_executed INTEGER DEFAULT 0,
                        session_pnl REAL DEFAULT 0.0,
                        error_count INTEGER DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER,
                        symbol TEXT NOT NULL,
                        side TEXT,
                        qty REAL,
                        price REAL,
                        pnl REAL,
                        signal TEXT,
                        rsi REAL,
                        order_time TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        FOREIGN KEY (session_id) REFERENCES trading_sessions(id)
                    );

                    CREATE TABLE IF NOT EXISTS research_cooldowns (
                        symbol TEXT PRIMARY KEY,
                        last_research_time TEXT NOT NULL,
                        updated_at TEXT DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS position_sell_cooldowns (
                        symbol TEXT PRIMARY KEY,
                        last_analysis_time TEXT NOT NULL,
                        updated_at TEXT DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS trade_cooldowns (
                        symbol TEXT PRIMARY KEY,
                        last_trade_time TEXT NOT NULL,
                        trade_type TEXT,
                        updated_at TEXT DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS analyzed_stocks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL UNIQUE,
                        price REAL,
                        total_score INTEGER DEFAULT 0,
                        signal TEXT DEFAULT 'HOLD',
                        signal_strength TEXT DEFAULT 'WEAK',
                        rsi REAL,
                        rsi_score REAL DEFAULT 0,
                        sma_score REAL DEFAULT 0,
                        macd_score REAL DEFAULT 0,
                        bb_score REAL DEFAULT 0,
                        vwap_score REAL DEFAULT 0,
                        regime_score REAL DEFAULT 0,
                        catalyst_score REAL DEFAULT 0,
                        earnings_score REAL DEFAULT 0,
                        volatility_score REAL DEFAULT 0,
                        last_analyzed TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                    );

                    CREATE INDEX IF NOT EXISTS idx_analyzed_stocks_score
                        ON analyzed_stocks(total_score DESC);
                    CREATE INDEX IF NOT EXISTS idx_analyzed_stocks_time
                        ON analyzed_stocks(last_analyzed DESC);
                    CREATE INDEX IF NOT EXISTS idx_trades_session
                        ON trades(session_id);
                    CREATE INDEX IF NOT EXISTS idx_trades_time
                        ON trades(created_at DESC);
                """)
            self.available = True
            logging.info(f"✅ SQLite database ready: {DB_PATH}")
        except Exception as e:
            logging.error(f"❌ Failed to initialise SQLite database: {e}")
            self.available = False

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self.available

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(self, bot_version: str = "2.0.0",
                       configuration: Dict = None,
                       is_paper_trading: bool = True,
                       notes: str = None) -> Optional[int]:
        try:
            with _get_conn() as conn:
                cur = conn.execute(
                    """INSERT INTO trading_sessions
                       (session_start, bot_version, configuration, is_paper_trading, notes,
                        total_symbols_processed, total_trades_executed, session_pnl, error_count)
                       VALUES (?, ?, ?, ?, ?, 0, 0, 0.0, 0)""",
                    (
                        datetime.utcnow().isoformat(),
                        bot_version,
                        json.dumps(configuration) if configuration else None,
                        1 if is_paper_trading else 0,
                        notes,
                    ),
                )
                session_id = cur.lastrowid
                self.current_session_id = session_id
                logging.info(f"✅ Created session {session_id}")
                return session_id
        except Exception as e:
            logging.error(f"Exception creating session: {e}")
            return None

    def update_session(self, session_id: int, updates: Dict) -> bool:
        if not updates:
            return False
        try:
            cols = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [session_id]
            with _get_conn() as conn:
                conn.execute(
                    f"UPDATE trading_sessions SET {cols} WHERE id = ?", vals
                )
            return True
        except Exception as e:
            logging.warning(f"Exception updating session: {e}")
            return False

    def get_sessions(self, limit: int = 10) -> List[Dict]:
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM trading_sessions ORDER BY session_start DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [_row_to_dict(r) for r in rows]
        except Exception as e:
            logging.warning(f"Exception getting sessions: {e}")
            return []

    def get_database_info(self) -> Dict[str, Any]:
        info = {
            "available": self.available,
            "db_path": str(DB_PATH),
            "tables_exist": self.available,
            "schema_version": "1.0",
            "total_sessions": 0,
            "total_trades": 0,
        }
        if not self.available:
            return info
        try:
            with _get_conn() as conn:
                info["total_sessions"] = conn.execute(
                    "SELECT COUNT(*) FROM trading_sessions"
                ).fetchone()[0]
                info["total_trades"] = conn.execute(
                    "SELECT COUNT(*) FROM trades"
                ).fetchone()[0]
        except Exception as e:
            logging.debug(f"Error getting database info: {e}")
        return info

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def log_trade(self, session_id: int, trade_data: Dict) -> bool:
        try:
            cols = list(trade_data.keys())
            placeholders = ", ".join("?" * len(cols))
            col_names = ", ".join(cols)
            with _get_conn() as conn:
                conn.execute(
                    f"INSERT INTO trades ({col_names}) VALUES ({placeholders})",
                    [trade_data[c] for c in cols],
                )
            logging.debug(f"✅ Logged trade for {trade_data.get('symbol', 'unknown')}")
            return True
        except Exception as e:
            logging.warning(f"Exception logging trade: {e}")
            return False

    def get_all_trades(self, limit: int = 1000) -> List[Dict]:
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
                return [_row_to_dict(r) for r in rows]
        except Exception as e:
            logging.debug(f"Error getting trades: {e}")
            return []

    # ------------------------------------------------------------------
    # Cooldowns
    # ------------------------------------------------------------------

    def get_research_cooldown(self, symbol: str) -> Optional[datetime]:
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT last_research_time FROM research_cooldowns WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                if row:
                    return datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        except Exception as e:
            logging.debug(f"Error getting research cooldown for {symbol}: {e}")
        return None

    def set_research_cooldown(self, symbol: str, research_time: datetime = None) -> bool:
        if research_time is None:
            research_time = datetime.utcnow()
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO research_cooldowns (symbol, last_research_time, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           last_research_time = excluded.last_research_time,
                           updated_at = excluded.updated_at""",
                    (symbol, research_time.isoformat(), datetime.utcnow().isoformat()),
                )
            return True
        except Exception as e:
            logging.debug(f"Error setting research cooldown for {symbol}: {e}")
            return False

    def get_position_sell_cooldown(self, symbol: str) -> Optional[datetime]:
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT last_analysis_time FROM position_sell_cooldowns WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                if row:
                    return datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        except Exception as e:
            logging.debug(f"Error getting position sell cooldown for {symbol}: {e}")
        return None

    def set_position_sell_cooldown(self, symbol: str, analysis_time: datetime = None) -> bool:
        if analysis_time is None:
            analysis_time = datetime.utcnow()
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO position_sell_cooldowns (symbol, last_analysis_time, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           last_analysis_time = excluded.last_analysis_time,
                           updated_at = excluded.updated_at""",
                    (symbol, analysis_time.isoformat(), datetime.utcnow().isoformat()),
                )
            return True
        except Exception as e:
            logging.debug(f"Error setting position sell cooldown for {symbol}: {e}")
            return False

    def get_trade_cooldown(self, symbol: str) -> Optional[datetime]:
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT last_trade_time FROM trade_cooldowns WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                if row:
                    return datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        except Exception as e:
            logging.debug(f"Error getting trade cooldown for {symbol}: {e}")
        return None

    def set_trade_cooldown(self, symbol: str, trade_time: datetime = None,
                           trade_type: str = None) -> bool:
        if trade_time is None:
            trade_time = datetime.utcnow()
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO trade_cooldowns (symbol, last_trade_time, trade_type, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           last_trade_time = excluded.last_trade_time,
                           trade_type = excluded.trade_type,
                           updated_at = excluded.updated_at""",
                    (symbol, trade_time.isoformat(), trade_type, datetime.utcnow().isoformat()),
                )
            return True
        except Exception as e:
            logging.debug(f"Error setting trade cooldown for {symbol}: {e}")
            return False

    def cleanup_old_cooldowns(self, days_old: int = 7) -> bool:
        cutoff = (datetime.utcnow() - timedelta(days=days_old)).isoformat()
        try:
            with _get_conn() as conn:
                conn.execute(
                    "DELETE FROM research_cooldowns WHERE last_research_time < ?", (cutoff,)
                )
                conn.execute(
                    "DELETE FROM position_sell_cooldowns WHERE last_analysis_time < ?", (cutoff,)
                )
                conn.execute(
                    "DELETE FROM trade_cooldowns WHERE last_trade_time < ?", (cutoff,)
                )
            logging.info(f"🧹 Cleaned up cooldown entries older than {days_old} days")
            return True
        except Exception as e:
            logging.debug(f"Error cleaning up old cooldowns: {e}")
            return False

    # ------------------------------------------------------------------
    # Analysis results
    # ------------------------------------------------------------------

    def save_analysis_result(self, symbol: str, analysis: Dict) -> bool:
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO analyzed_stocks
                       (symbol, price, total_score, signal, signal_strength,
                        rsi, rsi_score, sma_score, macd_score, bb_score,
                        vwap_score, regime_score, catalyst_score, earnings_score,
                        volatility_score, buy_criteria, passes_all_buy_criteria, last_analyzed)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           price = excluded.price,
                           total_score = excluded.total_score,
                           signal = excluded.signal,
                           signal_strength = excluded.signal_strength,
                           rsi = excluded.rsi,
                           rsi_score = excluded.rsi_score,
                           sma_score = excluded.sma_score,
                           macd_score = excluded.macd_score,
                           bb_score = excluded.bb_score,
                           vwap_score = excluded.vwap_score,
                           regime_score = excluded.regime_score,
                           catalyst_score = excluded.catalyst_score,
                           earnings_score = excluded.earnings_score,
                           volatility_score = excluded.volatility_score,
                           buy_criteria = excluded.buy_criteria,
                           passes_all_buy_criteria = excluded.passes_all_buy_criteria,
                           last_analyzed = excluded.last_analyzed""",
                    (
                        symbol,
                        analysis.get("price"),
                        analysis.get("total_score", 50),
                        analysis.get("signal", "HOLD"),
                        analysis.get("signal_strength", "WEAK"),
                        analysis.get("rsi"),
                        analysis.get("rsi_score", 0),
                        analysis.get("sma_score", 0),
                        analysis.get("macd_score", 0),
                        analysis.get("bb_score", 0),
                        analysis.get("vwap_score", 0),
                        analysis.get("regime_score", 0),
                        analysis.get("catalyst_score", 0),
                        analysis.get("earnings_score", 0),
                        analysis.get("volatility_score", 0),
                        json.dumps(analysis.get("buy_criteria", [])),
                        1 if analysis.get("passes_all_buy_criteria", False) else 0,
                        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ),
                )
            logging.debug(f"💾 Saved analysis for {symbol} to SQLite")
            return True
        except Exception as e:
            logging.debug(f"Error saving analysis for {symbol}: {e}")
            return False

    def get_analysis_results(self, limit: int = 100) -> List[Dict]:
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT *, last_analyzed AS analyzed_at FROM analyzed_stocks"
                    " ORDER BY total_score DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [_row_to_dict(r) for r in rows]
        except Exception as e:
            logging.debug(f"Error getting analysis results: {e}")
            return []

    def get_analysis_for_symbol(self, symbol: str) -> Optional[Dict]:
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM analyzed_stocks WHERE symbol = ?", (symbol,)
                ).fetchone()
                return _row_to_dict(row) if row else None
        except Exception as e:
            logging.debug(f"Error getting analysis for {symbol}: {e}")
            return None

    def get_unanalyzed_symbols(self, all_symbols: List[str], limit: int = 30) -> List[str]:
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT symbol FROM analyzed_stocks ORDER BY last_analyzed ASC"
                ).fetchall()
                analyzed_symbols = {r[0] for r in rows}
                oldest = [r[0] for r in rows[:limit]]

            unanalyzed = [s for s in all_symbols if s not in analyzed_symbols]
            result = unanalyzed + oldest
            return result[:limit]
        except Exception as e:
            logging.debug(f"Error getting unanalyzed symbols: {e}")
            return all_symbols[:limit]


# Global singleton
sqlite_db = SQLiteDB()
