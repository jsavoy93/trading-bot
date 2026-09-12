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
                        analysis_successes INTEGER DEFAULT 0,
                        analysis_failures INTEGER DEFAULT 0,
                        filter_results TEXT,
                        blocked_by TEXT,
                        blocked_count INTEGER DEFAULT 0,
                        last_analyzed TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                    );

                    CREATE INDEX IF NOT EXISTS idx_analyzed_stocks_score
                        ON analyzed_stocks(total_score DESC);
                    CREATE INDEX IF NOT EXISTS idx_analyzed_stocks_time
                        ON analyzed_stocks(last_analyzed DESC);
                    CREATE INDEX IF NOT EXISTS idx_analyzed_stocks_failures
                        ON analyzed_stocks(analysis_failures DESC);
                    CREATE INDEX IF NOT EXISTS idx_trades_session
                        ON trades(session_id);
                    CREATE INDEX IF NOT EXISTS idx_trades_time
                        ON trades(created_at DESC);
                """)
                
                # Migration: Add analysis_successes/failures columns if they don't exist
                # Migration: Add analysis_successes/failures columns if they don't exist
                try:
                    conn.execute("ALTER TABLE analyzed_stocks ADD COLUMN analysis_successes INTEGER DEFAULT 0;")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
                try:
                    conn.execute("ALTER TABLE analyzed_stocks ADD COLUMN analysis_failures INTEGER DEFAULT 0;")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
                try:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_analyzed_stocks_failures ON analyzed_stocks(analysis_failures DESC);")
                except sqlite3.OperationalError:
                    pass  # Index may already exist

                # Migration: Add filter_results / blocked_by / blocked_count if they don't exist
                try:
                    conn.execute("ALTER TABLE analyzed_stocks ADD COLUMN filter_results TEXT;")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
                try:
                    conn.execute("ALTER TABLE analyzed_stocks ADD COLUMN blocked_by TEXT;")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
                try:
                    conn.execute("ALTER TABLE analyzed_stocks ADD COLUMN blocked_count INTEGER DEFAULT 0;")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise

                # ── BOT-001: trading_sessions.status column (session lifecycle) ──────────
                # Lifecycle states (canonical):
                #   OPEN     — row created, no work has begun yet (rare in practice;
                #              typically only seen immediately after create_session).
                #   ACTIVE   — start_session() has run and the bot is currently
                #              executing this session. session_end IS NULL.
                #   ENDED    — end_session() persisted normally. session_end IS NOT NULL.
                #   FAILED   — end_session() or the surrounding try/except marked the
                #              session as failed (e.g., crash loop). session_end
                #              IS NOT NULL but status='FAILED'.
                # Values used in code MUST be uppercase string literals to match the
                # values stored by create_session / update_session. Schema defaults
                # to 'OPEN' for any new row; existing rows that pre-date this column
                # are backfilled below using the legacy `session_end IS NULL` heuristic.
                try:
                    conn.execute(
                        "ALTER TABLE trading_sessions ADD COLUMN status TEXT DEFAULT 'OPEN';"
                    )
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
                # Backfill: any historical row missing status gets a sensible value
                # derived from session_end (legacy data only; new rows always set it
                # explicitly). Do not silently rewrite history beyond this one-time
                # classification. (BOT-001 explicit requirement.)
                conn.execute(
                    "UPDATE trading_sessions SET status = 'ACTIVE' "
                    "WHERE status IS NULL AND session_end IS NULL;"
                )
                conn.execute(
                    "UPDATE trading_sessions SET status = 'ENDED' "
                    "WHERE status IS NULL AND session_end IS NOT NULL;"
                )
                conn.execute(
                    "UPDATE trading_sessions SET status = 'OPEN' "
                    "WHERE status IS NULL;"
                )
                # Index helps the dashboard /api/sessions listing filter by lifecycle.
                try:
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_trading_sessions_status "
                        "ON trading_sessions(status);"
                    )
                except sqlite3.OperationalError:
                    pass  # index may already exist

                # ── failed_analyses: why BUY/SELL signals were rejected ──────────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS failed_analyses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                        signal_type TEXT NOT NULL,
                        total_score REAL,
                        rsi REAL,
                        price REAL,
                        failed_filters TEXT NOT NULL,
                        filter_details TEXT,
                        blocked_by TEXT,
                        session_id INTEGER,
                        threshold_key TEXT,
                        threshold_value REAL,
                        actual_value REAL
                    )
                """)
                try:
                    conn.execute("CREATE INDEX idx_failed_analyses_time ON failed_analyses(timestamp DESC);")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("CREATE INDEX idx_failed_analyses_symbol ON failed_analyses(symbol);")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("CREATE INDEX idx_failed_analyses_blocked ON failed_analyses(blocked_by);")
                except sqlite3.OperationalError:
                    pass

                # ── OBS-001 Phase A: decision_snapshot + decision_history + cycle_funnel ──
                # Phase A is observability-only. Two new nullable columns on
                # analyzed_stocks (latest per-symbol state) plus two new tables
                # (append-only history). No data rewrite. No row drop.
                #
                #   decision_snapshot TEXT        — JSON blob, schema_version=1
                #   decision_schema_version INT   — 0 = legacy (NULL snapshot);
                #                                     1 = OBS-001 Phase A.
                #
                # decision_history: one finalized row per (cycle_id, symbol).
                # cycle_funnel:     one row per cycle, counters reflect CURRENT
                #                   runtime behavior (no invented pre-rank gate).
                try:
                    conn.execute(
                        "ALTER TABLE analyzed_stocks ADD COLUMN decision_snapshot TEXT;"
                    )
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
                try:
                    conn.execute(
                        "ALTER TABLE analyzed_stocks ADD COLUMN decision_schema_version INTEGER DEFAULT 0;"
                    )
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS decision_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cycle_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        cycle_start TEXT NOT NULL,
                        session_id INTEGER,
                        decision_schema_version INTEGER NOT NULL,
                        decision_snapshot TEXT NOT NULL,
                        created_at TEXT DEFAULT (datetime('now')),
                        UNIQUE(cycle_id, symbol)
                    )
                """)
                try:
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_decision_history_symbol "
                        "ON decision_history(symbol, cycle_start DESC);"
                    )
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_decision_history_session "
                        "ON decision_history(session_id, cycle_start DESC);"
                    )
                except sqlite3.OperationalError:
                    pass

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cycle_funnel (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cycle_id TEXT NOT NULL UNIQUE,
                        session_id INTEGER,
                        cycle_start TEXT NOT NULL,
                        cycle_end TEXT NOT NULL,
                        analyzed_count INTEGER NOT NULL,
                        strategy_eligible_count INTEGER NOT NULL,
                        ranked_candidate_count INTEGER NOT NULL,
                        execution_attempt_count INTEGER NOT NULL,
                        execution_blocked_count INTEGER NOT NULL,
                        order_submission_attempt_count INTEGER NOT NULL,
                        order_submitted_count INTEGER NOT NULL,
                        order_failed_count INTEGER NOT NULL,
                        not_attempted_count INTEGER NOT NULL,
                        not_attempted_reason TEXT,
                        bot_version TEXT,
                        schema_version INTEGER DEFAULT 1,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                try:
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_cycle_funnel_session "
                        "ON cycle_funnel(session_id, cycle_start DESC);"
                    )
                except sqlite3.OperationalError:
                    pass

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
                       notes: str = None,
                       status: str = "ACTIVE") -> Optional[int]:
        """Insert a new trading_sessions row.

        status defaults to 'ACTIVE' because create_session is only ever called
        from SmartTradingBot.start_session(), at which point work is about to
        begin. The legacy 'OPEN' value is reserved for rows that exist with
        no work and no end_session call (rare). FAILED transitions happen via
        update_session(... status='FAILED'). Validated against the lifecycle
        vocabulary to keep dashboards honest.
        """
        if status not in ("OPEN", "ACTIVE", "ENDED", "FAILED"):
            raise ValueError(
                f"create_session: invalid status {status!r}; "
                "must be one of OPEN/ACTIVE/ENDED/FAILED"
            )
        try:
            with _get_conn() as conn:
                cur = conn.execute(
                    """INSERT INTO trading_sessions
                       (session_start, bot_version, configuration, is_paper_trading, notes,
                        total_symbols_processed, total_trades_executed, session_pnl, error_count,
                        status)
                       VALUES (?, ?, ?, ?, ?, 0, 0, 0.0, 0, ?)""",
                    (
                        datetime.utcnow().isoformat(),
                        bot_version,
                        json.dumps(configuration) if configuration else None,
                        1 if is_paper_trading else 0,
                        notes,
                        status,
                    ),
                )
                session_id = cur.lastrowid
                self.current_session_id = session_id
                logging.info(f"✅ Created session {session_id} (status={status})")
                return session_id
        except Exception as e:
            logging.error(f"Exception creating session: {e}")
            return None

    _SESSION_STATUS_VALUES = ("OPEN", "ACTIVE", "ENDED", "FAILED")

    def update_session(self, session_id: int, updates: Dict) -> bool:
        if not updates:
            return False
        # Validate status transitions (BOT-001). Only allow lifecycle vocabulary.
        if "status" in updates and updates["status"] not in self._SESSION_STATUS_VALUES:
            logging.warning(
                f"update_session({session_id}): rejected invalid status "
                f"{updates['status']!r}; must be one of {self._SESSION_STATUS_VALUES}"
            )
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

    # ------------------------------------------------------------------
    # Session lifecycle helpers (BOT-001)
    # ------------------------------------------------------------------

    def get_active_session(self) -> Optional[Dict]:
        """Return the most recent ACTIVE row (session_end IS NULL and status='ACTIVE').

        ACTIVE is the only lifecycle state that may legitimately have
        session_end IS NULL. Any other state with session_end IS NULL is a
        stale-open anomaly and should be reaped via close_stale_sessions().

        BOT-003 fix: ordered by `id DESC` (auto-increment primary key)
        rather than `session_start DESC` (string compare). The auto-
        increment id is strictly monotonic and set by SQLite at row
        creation time, so it is immune to clock-skewed test fixtures
        or intentionally-wrong dates. The current runner's session is
        always the most recent row created, so id-DESC ordering
        reliably returns the runner's ACTIVE session when one exists,
        regardless of any clock-skewed or fixture rows that may also
        be ACTIVE.

        NOTE: This method is intentionally NOT runner-aware (it does
        not filter by a specific PID). For runtime-status callers
        that can identify the runner PID, prefer
        `get_active_session_for_runner(pid)` which additionally
        filters by the runner's process start time.
        """
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM trading_sessions "
                    "WHERE status='ACTIVE' AND session_end IS NULL "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                return _row_to_dict(row) if row else None
        except Exception as e:
            logging.warning(f"Exception getting active session: {e}")
            return None

    def get_active_session_for_runner(self, runner_pid: int) -> Optional[Dict]:
        """Return the ACTIVE session belonging to the given runner PID.

        BOT-003 amendment linkage: a real session row created by the
        current runner must have a session_start timestamp that is
        chronologically consistent with the runner's process lifetime:

            runner_start_epoch <= session_start_epoch <= now + SKEW

        where:
          - runner_start_epoch is read from /proc/<pid>/stat field 22
            (starttime in clock ticks since boot) and converted to
            Unix epoch seconds via /proc/uptime.
          - now is the current Unix epoch seconds at lookup time.
          - SKEW is a documented tolerance for clock drift between
            the runner process and the lookup process, plus a small
            buffer for in-flight scheduling.

        The LOWER bound excludes:
          - stale ACTIVE rows from prior runners,
          - test fixtures dated before the runner started.

        The UPPER bound excludes:
          - test fixtures dated far in the future (e.g. 2099-01-01),
          - clock-skewed anomalies that produce post-now timestamps.

        A future-dated row's epoch seconds are numerically GREATER
        than any realistic current time, so the upper bound catches
        it. (The single-bound v2 implementation only had the lower
        bound, which future-dated rows trivially satisfy; that left
        the bug class alive.)

        Among survivors, id DESC selects the most recently created
        row, which is the current loop's session.

        Args:
            runner_pid: The PID of the SmartBot runner process (read
                from /tmp/trading_bot.lock). If the PID is not
                positive or the process is not running, this returns
                None and the caller should fall back to
                get_active_session().

        Returns:
            The ACTIVE session row for the given runner PID, or None
            if no such session exists (or the PID is invalid).

        LINKAGE LIMITATION:
            This is a RUNTIME-WINDOW HEURISTIC, not PID ownership.
            The current database schema does NOT store the runner
            PID in trading_sessions, so we cannot assert that any
            specific row was created by this specific PID. We can
            only assert that the row's session_start timestamp is
            chronologically consistent with the runner's process
            lifetime. If two runners run concurrently (e.g. a test
            harness and the live runner), this heuristic cannot
            distinguish their rows. The SmartBot's single-instance
            lock (/tmp/trading_bot.lock) prevents that scenario
            in production.
        """
        # Documented clock-skew tolerance. The runner's wall clock
        # and the lookup process's wall clock can drift by a small
        # amount (NTP-corrected systems typically drift <1s;
        # uncorrected virtual machines can drift tens of seconds).
        # 300s = 5 minutes is a generous upper bound that catches
        # any realistic fixture dated in the future while still
        # tolerating clock drift and test-environment noise.
        CLOCK_SKEW_SECONDS = 300

        if not isinstance(runner_pid, int) or runner_pid <= 0:
            return None
        try:
            import os
            import time
            # Read process start time from /proc/<pid>/stat.
            # field 22 (1-indexed) is starttime in clock ticks since boot.
            try:
                with open(f"/proc/{runner_pid}/stat", "r") as f:
                    stat_parts = f.read().split()
                starttime_ticks = int(stat_parts[21])
            except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
                # Process not running or stat not parseable.
                return None
            clk_tck = os.sysconf("SC_CLK_TCK")
            if not clk_tck or clk_tck <= 0:
                clk_tck = 100  # Linux default; safe fallback
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.read().split()[0])
            runner_start_epoch = int(time.time() - uptime_seconds + (starttime_ticks / float(clk_tck)))
            upper_bound_epoch = int(time.time()) + CLOCK_SKEW_SECONDS
            with _get_conn() as conn:
                # Both bounds use strftime('%s', session_start) so the
                # comparison is NUMERIC (Unix epoch seconds), not
                # lexicographic. A future-dated session_start
                # (e.g. '2099-01-01...') has epoch seconds far in
                # the future; the upper bound filters it out. A
                # pre-runner session_start (e.g. '1990-01-01...')
                # has epoch seconds far in the past; the lower bound
                # filters it out.
                row = conn.execute(
                    "SELECT * FROM trading_sessions "
                    "WHERE status='ACTIVE' AND session_end IS NULL "
                    "AND CAST(strftime('%s', session_start) AS INTEGER) >= ? "
                    "AND CAST(strftime('%s', session_start) AS INTEGER) <= ? "
                    "ORDER BY id DESC LIMIT 1",
                    (runner_start_epoch, upper_bound_epoch),
                ).fetchone()
                return _row_to_dict(row) if row else None
        except Exception as e:
            logging.warning(
                f"Exception getting active session for runner pid {runner_pid}: {e}"
            )
            return None

    def get_stale_open_sessions(self) -> List[Dict]:
        """Return rows that look open (session_end IS NULL) but are NOT ACTIVE.

        These are anomalies: rows that were created but never properly ended.
        Most commonly seen after a bot crash, host reboot, or process kill.
        """
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM trading_sessions "
                    "WHERE session_end IS NULL AND status != 'ACTIVE'"
                ).fetchall()
                return [_row_to_dict(r) for r in rows]
        except Exception as e:
            logging.warning(f"Exception getting stale-open sessions: {e}")
            return []

    def close_stale_sessions(
        self,
        cutoff_iso: str,
        reason: str = "stale-reaped",
    ) -> int:
        """Close any session whose session_start is older than cutoff_iso AND
        whose status is not yet ENDED/FAILED.

        Returns the number of rows closed. Used at SmartTradingBot startup
        to clean up after crashes/reboots. Does NOT delete rows; only
        transitions them to FAILED with a notes suffix so the audit trail
        is preserved.

        cutoff_iso must be an ISO-8601 timestamp string; older rows are closed.
        """
        closed = 0
        try:
            with _get_conn() as conn:
                # Find candidates first so we can append to notes.
                rows = conn.execute(
                    "SELECT id, notes FROM trading_sessions "
                    "WHERE session_end IS NULL AND status IN ('OPEN','ACTIVE') "
                    "AND session_start < ?",
                    (cutoff_iso,),
                ).fetchall()
                for row in rows:
                    session_id = row["id"]
                    existing_notes = row["notes"] or ""
                    new_notes = (
                        f"{existing_notes} [{reason}]" if existing_notes else reason
                    )
                    ok = self.update_session(
                        session_id,
                        {
                            "session_end": datetime.utcnow().isoformat(),
                            "status": "FAILED",
                            "notes": new_notes,
                        },
                    )
                    if ok:
                        closed += 1
                if closed:
                    logging.info(
                        f"close_stale_sessions: reaped {closed} stale-open "
                        f"rows older than {cutoff_iso}"
                    )
        except Exception as e:
            logging.warning(f"Exception closing stale sessions: {e}")
        return closed

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
            # OBS-001 Phase A: if a decision_snapshot is attached, persist
            # it too. The snapshot is the authoritative observation of what
            # the bot decided; the scalar columns above are retained for
            # backward compatibility with existing dashboard consumers.
            decision_snapshot = analysis.get("decision_snapshot")
            decision_schema_version = analysis.get("decision_schema_version", 0)
            if decision_snapshot is not None and decision_schema_version == 0:
                decision_schema_version = 1  # OBS-001 Phase A deployed version

            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO analyzed_stocks
                       (symbol, price, total_score, signal, signal_strength,
                        rsi, rsi_score, sma_score, macd_score, bb_score,
                        vwap_score, regime_score, catalyst_score, earnings_score,
                        volatility_score, buy_criteria, passes_all_buy_criteria,
                        filter_results, blocked_by, blocked_count, last_analyzed,
                        decision_snapshot, decision_schema_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?)
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
                           filter_results = excluded.filter_results,
                           blocked_by = excluded.blocked_by,
                           blocked_count = excluded.blocked_count,
                           last_analyzed = excluded.last_analyzed,
                           decision_snapshot = COALESCE(excluded.decision_snapshot, decision_snapshot),
                           decision_schema_version = CASE
                               WHEN excluded.decision_snapshot IS NOT NULL
                               THEN excluded.decision_schema_version
                               ELSE decision_schema_version
                           END""",
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
                        json.dumps(analysis.get("filter_results", {})),
                        analysis.get("blocked_by"),
                        analysis.get("blocked_count", 0),
                        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        json.dumps(decision_snapshot) if decision_snapshot is not None else None,
                        decision_schema_version,
                    ),
                )
            logging.debug(f"💾 Saved analysis for {symbol} to SQLite")
            return True
        except Exception as e:
            logging.debug(f"Error saving analysis for {symbol}: {e}")
            return False

    # ── OBS-001 Phase A: decision_history + cycle_funnel persistence ─────
    # decision_history is INSERT-only. UNIQUE(cycle_id, symbol) enforces
    # the "one finalized row per (cycle_id, symbol)" invariant required
    # by the Phase A design. Upsert via REPLACE only if the caller
    # intends to overwrite (e.g. for the same-cycle same-symbol re-write
    # before finalization); the canonical finalize path uses
    # finalize_decision_history() which only INSERTs.

    def finalize_decision_history(
        self,
        cycle_id: str,
        symbol: str,
        cycle_start: str,
        session_id: Optional[int],
        decision_schema_version: int,
        decision_snapshot: Dict,
    ) -> bool:
        """Append one finalized decision_history row for (cycle_id, symbol).

        UNIQUE(cycle_id, symbol) makes this idempotent on retry: a second
        call with the same (cycle_id, symbol) replaces the prior row.
        Phase A guarantees this is called at most ONCE per
        (cycle_id, symbol) by the snapshot writer at the moment the
        cycle finalizes that symbol's outcome.
        """
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO decision_history
                       (cycle_id, symbol, cycle_start, session_id,
                        decision_schema_version, decision_snapshot)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(cycle_id, symbol) DO UPDATE SET
                           decision_schema_version = excluded.decision_schema_version,
                           decision_snapshot = excluded.decision_snapshot""",
                    (
                        cycle_id,
                        symbol,
                        cycle_start,
                        session_id,
                        decision_schema_version,
                        json.dumps(decision_snapshot),
                    ),
                )
            return True
        except Exception as e:
            logging.debug(f"Error finalizing decision_history for {cycle_id}/{symbol}: {e}")
            return False

    def insert_cycle_funnel(self, funnel: Dict) -> bool:
        """Insert one cycle_funnel row. UNIQUE(cycle_id) makes it
        idempotent on retry. Phase A calls this exactly once at the
        end of run_analysis."""
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO cycle_funnel
                       (cycle_id, session_id, cycle_start, cycle_end,
                        analyzed_count, strategy_eligible_count,
                        ranked_candidate_count, execution_attempt_count,
                        execution_blocked_count, order_submission_attempt_count,
                        order_submitted_count, order_failed_count,
                        not_attempted_count, not_attempted_reason,
                        bot_version, schema_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        funnel.get("cycle_id"),
                        funnel.get("session_id"),
                        funnel.get("cycle_start"),
                        funnel.get("cycle_end"),
                        funnel.get("analyzed_count", 0),
                        funnel.get("strategy_eligible_count", 0),
                        funnel.get("ranked_candidate_count", 0),
                        funnel.get("execution_attempt_count", 0),
                        funnel.get("execution_blocked_count", 0),
                        funnel.get("order_submission_attempt_count", 0),
                        funnel.get("order_submitted_count", 0),
                        funnel.get("order_failed_count", 0),
                        funnel.get("not_attempted_count", 0),
                        funnel.get("not_attempted_reason"),
                        funnel.get("bot_version"),
                        funnel.get("schema_version", 1),
                    ),
                )
            return True
        except Exception as e:
            logging.debug(f"Error inserting cycle_funnel for {funnel.get('cycle_id')}: {e}")
            return False

    def get_decision_snapshot_for_symbol(self, symbol: str) -> Optional[Dict]:
        """Read the latest decision_snapshot for one symbol."""
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    """SELECT decision_snapshot, decision_schema_version
                       FROM analyzed_stocks
                       WHERE symbol = ? AND decision_snapshot IS NOT NULL""",
                    (symbol,),
                ).fetchone()
            if row and row[0]:
                return {
                    "decision_snapshot": json.loads(row[0]),
                    "decision_schema_version": row[1],
                }
            return None
        except Exception as e:
            logging.debug(f"Error reading decision_snapshot for {symbol}: {e}")
            return None

    def get_decision_history_for_symbol(self, symbol: str, limit: int = 50) -> list:
        """Read decision_history rows for one symbol in cycle_start DESC order."""
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    """SELECT cycle_id, cycle_start, session_id,
                              decision_schema_version, decision_snapshot
                       FROM decision_history
                       WHERE symbol = ?
                       ORDER BY cycle_start DESC
                       LIMIT ?""",
                    (symbol, limit),
                ).fetchall()
            out = []
            for r in rows:
                out.append({
                    "cycle_id": r[0],
                    "cycle_start": r[1],
                    "session_id": r[2],
                    "decision_schema_version": r[3],
                    "decision_snapshot": json.loads(r[4]) if r[4] else None,
                })
            return out
        except Exception as e:
            logging.debug(f"Error reading decision_history for {symbol}: {e}")
            return []

    def get_latest_cycle_funnel(self, session_id: Optional[int] = None) -> Optional[Dict]:
        """Read the most recent cycle_funnel row."""
        try:
            with _get_conn() as conn:
                if session_id is not None:
                    row = conn.execute(
                        """SELECT * FROM cycle_funnel
                           WHERE session_id = ?
                           ORDER BY cycle_start DESC LIMIT 1""",
                        (session_id,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT * FROM cycle_funnel
                           ORDER BY cycle_start DESC LIMIT 1"""
                    ).fetchone()
            if not row:
                return None
            cols = [d[0] for d in conn.execute("SELECT * FROM cycle_funnel LIMIT 1").description] \
                if False else [
                    "id", "cycle_id", "session_id", "cycle_start", "cycle_end",
                    "analyzed_count", "strategy_eligible_count",
                    "ranked_candidate_count", "execution_attempt_count",
                    "execution_blocked_count", "order_submission_attempt_count",
                    "order_submitted_count", "order_failed_count",
                    "not_attempted_count", "not_attempted_reason",
                    "bot_version", "schema_version", "created_at",
                ]
            return dict(zip(cols, row))
        except Exception as e:
            logging.debug(f"Error reading latest cycle_funnel: {e}")
            return None

    def increment_analysis_success(self, symbol: str) -> bool:
        """Increment the success counter for a symbol;
        resets failures when symbol has 2+ total successes (unbans it)."""
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO analyzed_stocks (symbol, analysis_successes, last_analyzed)
                       VALUES (?, 1, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           analysis_successes = analysis_successes + 1,
                           analysis_failures = CASE
                               WHEN analysis_successes + 1 >= 2 THEN 0
                               ELSE analysis_failures
                           END,
                           last_analyzed = excluded.last_analyzed""",
                    (symbol, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")),
                )
            return True
        except Exception as e:
            logging.debug(f"Error incrementing success for {symbol}: {e}")
            return False

    def increment_analysis_failure(self, symbol: str) -> bool:
        """Increment the failure counter for a symbol"""
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO analyzed_stocks (symbol, analysis_failures, last_analyzed)
                       VALUES (?, 1, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           analysis_failures = analysis_failures + 1,
                           last_analyzed = excluded.last_analyzed""",
                    (symbol, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")),
                )
            return True
        except Exception as e:
            logging.debug(f"Error incrementing failure for {symbol}: {e}")
            return False

    def get_analysis_stats(self, symbol: str) -> Dict:
        """Get success/failure counts for a symbol"""
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT analysis_successes, analysis_failures FROM analyzed_stocks WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                if row:
                    return {
                        "successes": row[0] or 0,
                        "failures": row[1] or 0,
                    }
        except Exception as e:
            logging.debug(f"Error getting stats for {symbol}: {e}")
        return {"successes": 0, "failures": 0}

    def get_consistently_failing_symbols(self, min_failures: int = 3, cooldown_days: int = 7) -> List[str]:
        """Get symbols that have failed analysis min_failures or more times
        AND haven't been attempted in the last cooldown_days."""
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    """SELECT symbol FROM analyzed_stocks
                       WHERE analysis_failures >= ?
                         AND last_analyzed < datetime('now', ?)
                       ORDER BY analysis_failures DESC""",
                    (min_failures, f"-{cooldown_days} days"),
                ).fetchall()
                return [r[0] for r in rows]
        except Exception as e:
            logging.debug(f"Error getting failing symbols: {e}")
            return []

    def reset_analysis_failures(self, symbol: str) -> bool:
        """Reset failure count for a symbol (call when it succeeds)"""
        try:
            with _get_conn() as conn:
                conn.execute(
                    "UPDATE analyzed_stocks SET analysis_failures = 0 WHERE symbol = ?",
                    (symbol,),
                )
            return True
        except Exception as e:
            logging.debug(f"Error resetting failures for {symbol}: {e}")
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

    # ── Failed Analyses ──────────────────────────────────────────────────────────────

    def save_failed_analysis(
        self,
        symbol: str,
        signal_type: str,
        total_score: float,
        rsi: float,
        failed_filters: List[str],
        filter_details: Dict,
        blocked_by: str,
        price: float = None,
        session_id: int = None,
        threshold_key: str = None,
        threshold_value: float = None,
        actual_value: float = None,
    ) -> bool:
        """Save a record of why a BUY/SELL signal was rejected.

        Args:
            symbol:           Stock ticker
            signal_type:       'BUY' or 'SELL'
            total_score:       Composite score (0-100)
            rsi:               RSI at time of analysis
            failed_filters:    List of filter names that caused the failure
            filter_details:    Dict of {filter_name: {passed, detail, value}}
            blocked_by:        Primary failure reason label, e.g. 'Score < 55'
            price:             Stock price at time of analysis
            session_id:        Trading session ID
            threshold_key:     The param name that caused failure, e.g. 'min_score_buy'
            threshold_value:   The limit value in effect, e.g. 55
            actual_value:      The stock's actual metric value, e.g. 40
        """
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO failed_analyses
                       (symbol, signal_type, total_score, rsi, price,
                        failed_filters, filter_details, blocked_by, session_id,
                        threshold_key, threshold_value, actual_value)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        symbol,
                        signal_type,
                        total_score,
                        rsi,
                        price,
                        json.dumps(failed_filters),
                        json.dumps(filter_details),
                        blocked_by,
                        session_id,
                        threshold_key,
                        threshold_value,
                        actual_value,
                    ),
                )
            return True
        except Exception as e:
            logging.debug(f"Error saving failed analysis for {symbol}: {e}")
            return False

    def get_failed_analyses(
        self,
        from_date: str = None,
        to_date: str = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """Retrieve failed analysis records within a date range.

        Args:
            from_date:  ISO timestamp (inclusive), e.g. '2026-06-01T00:00:00Z'
            to_date:    ISO timestamp (inclusive)
            limit:      Max rows to return

        Returns:
            List of dicts ordered by timestamp DESC
        """
        try:
            with _get_conn() as conn:
                query = "SELECT * FROM failed_analyses WHERE 1=1"
                params: List[Any] = []
                if from_date:
                    query += " AND timestamp >= ?"
                    params.append(from_date)
                if to_date:
                    query += " AND timestamp <= ?"
                    params.append(to_date)
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                results = []
                for row in rows:
                    r = dict(row)
                    if r.get("failed_filters"):
                        r["failed_filters"] = json.loads(r["failed_filters"])
                    if r.get("filter_details"):
                        r["filter_details"] = json.loads(r["filter_details"])
                    results.append(r)
                return results
        except Exception as e:
            logging.debug(f"Error getting failed analyses: {e}")
            return []

    def get_failed_analyses_summary(
        self,
        from_date: str = None,
        to_date: str = None,
    ) -> Dict:
        """Aggregate failed analyses into a breakdown by blocked_by filter.

        Returns:
            {"total": N, "breakdown": [{"blocked_by": "...", "count": N, "pct": P}, ...]}
        """
        try:
            with _get_conn() as conn:
                base_query = "FROM failed_analyses WHERE 1=1"
                params: List[Any] = []
                if from_date:
                    base_query += " AND timestamp >= ?"
                    params.append(from_date)
                if to_date:
                    base_query += " AND timestamp <= ?"
                    params.append(to_date)

                total_row = conn.execute(
                    f"SELECT COUNT(*) as cnt {base_query}", params
                ).fetchone()
                total = total_row[0] if total_row else 0

                rows = conn.execute(
                    f"""SELECT blocked_by, COUNT(*) as cnt
                       {base_query}
                       GROUP BY blocked_by
                       ORDER BY cnt DESC""",
                    params,
                ).fetchall()
                breakdown = []
                for row in rows:
                    cnt = row[1]
                    breakdown.append({
                        "blocked_by": row[0] or "unknown",
                        "count": cnt,
                        "pct": round(cnt / total * 100, 1) if total > 0 else 0,
                    })
                return {"total": total, "breakdown": breakdown}
        except Exception as e:
            logging.debug(f"Error getting failed analyses summary: {e}")
            return {"total": 0, "breakdown": []}

    # ── Remaining methods ───────────────────────────────────────────────────────────────

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
