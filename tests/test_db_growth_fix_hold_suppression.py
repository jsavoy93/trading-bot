"""DB-GROWTH-FIX Stage 1 - unchanged-HOLD decision_history suppression tests.

Feature A only (observability-only): HOLD_INELIGIBLE rows whose observable
decision state is unchanged vs the last persisted row for the symbol are
skipped, together with their decision_gate_evaluations children (children
are written inside finalize_decision_history, so skipping the parent skips
them). There is NO same-day heartbeat exception: a HOLD is skipped only
when its observable decision state is unchanged versus the LAST persisted
decision_history row for that symbol. The first row for a symbol is always
written because there is nothing to compare against. Non-HOLD outcomes,
any state change, and anything leading to an order attempt are ALWAYS
persisted in full. cycle_funnel and analyzed_stocks writes are unaffected.

Follows the PHASE-OBS-001-ISO test-isolation architecture: every test runs
against a synthetic temp DB; the production trading_bot.db is fail-closed.
"""

import importlib
import json
import pathlib
import sqlite3

import pytest

import src.database.sqlite_db as _sqlite_db_module_for_path_capture

PRODUCTION_DB_PATH = pathlib.Path(
    str(_sqlite_db_module_for_path_capture.DB_PATH)
).resolve()


@pytest.fixture(autouse=True)
def _fail_closed_production_db_guard(monkeypatch, request):
    """Fail-closed guard mirroring PHASE-OBS-001-ISO: any connection that
    resolves to the production trading_bot.db raises immediately."""
    import src.database.sqlite_db as _sqlite_db_module

    _original_get_conn = _sqlite_db_module._get_conn
    opened_paths = []

    def _guarded_get_conn(*args, **kwargs):
        live = pathlib.Path(str(_sqlite_db_module.DB_PATH)).resolve()
        opened_paths.append(live)
        if live == PRODUCTION_DB_PATH:
            raise RuntimeError(
                "DB-GROWTH-FIX-ISO SAFETY VIOLATION: test "
                f"{request.node.nodeid!r} attempted to open the production "
                f"database at {PRODUCTION_DB_PATH!s}."
            )
        return _original_get_conn(*args, **kwargs)

    monkeypatch.setattr(_sqlite_db_module, "_get_conn", _guarded_get_conn)
    yield
    for p in opened_paths:
        if p == PRODUCTION_DB_PATH:
            raise RuntimeError(
                "DB-GROWTH-FIX-ISO SAFETY VIOLATION (post-test): test "
                f"{request.node.nodeid!r} opened the production database."
            )


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """Synthetic temp DB with the canonical schema plus the production
    analyzed_stocks drift columns. Yields (db, sqlite_db_module, path)."""
    import src.database.sqlite_db as sqlite_db_module

    importlib.reload(sqlite_db_module)
    temp_db_path = tmp_path / "db_growth_fix_test.db"
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", temp_db_path)

    db = sqlite_db_module.SQLiteDB()
    assert db.available
    with sqlite_db_module._get_conn() as conn:
        for col_name, col_def in (
            ("buy_criteria", "TEXT"),
            ("passes_all_buy_criteria", "INTEGER"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE analyzed_stocks ADD COLUMN {col_name} {col_def}"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
    yield db, sqlite_db_module, temp_db_path


# -- Helpers ---------------------------------------------------------------


def _make_bot(isolated_db, *, suppress=True, tolerance=1.0):
    """SmartTradingBot built via __new__ (repo convention) with the
    DB-GROWTH-FIX Stage-1 knobs set explicitly. Market-hours gating and
    retention pruning belong to later stages and stay disabled here."""
    _db, sqlite_db_module, _path = isolated_db
    from src.core.smart_bot import SmartTradingBot

    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = sqlite_db_module.SQLiteDB()
    bot.session_id = None
    bot.enable_volume_confirmation = False
    bot.rsi_buy_threshold = 30
    bot.rsi_sell_threshold = 70
    bot.bot_version = "2.1.0-test"
    # DB-GROWTH-FIX knobs (effective-settings overrides land on the same
    # attribute names; __new__ bots set them directly).
    bot.decision_history_market_hours_only = False  # stage 2 - off in tests
    bot.decision_suppress_unchanged_holds = suppress
    bot.decision_snapshot_score_tolerance = tolerance
    bot.decision_history_prune_enabled = False      # stage 3 - off in tests
    return bot


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _hold_entry(symbol="HOLDT", total_score=50, rsi=45.0):
    """Analysis entry whose built snapshot resolves to HOLD_INELIGIBLE
    (signal HOLD -> strategy_eligible False -> L1 outcome)."""
    return {
        "analysis": {
            "symbol": symbol,
            "signal": "HOLD",
            "signal_strength": "WEAK",
            "price": 100.0,
            "rsi": rsi,
            "sma_fast": 100.0,
            "sma_slow": 110.0,
            "macd_histogram": -0.2,
            "total_score": total_score,
        },
        "ranking_state": {"applicable": False},
        "selection_state": {"attempted": False},
        "execution_state": {"first_blocking_check": None, "checks": []},
        "order_state": {},
        "_cycle_baseline_diagnostics": None,
    }


def _buy_blocked_entry(symbol="BLKT", total_score=88):
    """Entry whose built snapshot resolves to BUY_BLOCKED_DYNAMIC
    (non-HOLD outcome; must ALWAYS persist)."""
    return {
        "analysis": {
            "symbol": symbol,
            "signal": "BUY",
            "signal_strength": "STRONG",
            "price": 200.0,
            "rsi": 25.0,
            "sma_fast": 120.0,
            "sma_slow": 100.0,
            "macd_histogram": 0.5,
            "total_score": total_score,
        },
        "ranking_state": {"applicable": True, "ranked_candidate": True,
                           "candidate_rank": 1},
        "selection_state": {"attempted": True},
        "execution_state": {
            "first_blocking_check": "existing_position_check",
            "checks": [
                {"check_name": "existing_position_check", "applied": True,
                 "passed": False},
            ],
        },
        "order_state": {"submit_attempted": False},
        "_execute_returned": True,
        "_cycle_baseline_diagnostics": None,
    }


def _count(isolated_db, sql, params=()):
    _db, sqlite_db_module, _p = isolated_db
    with sqlite_db_module._get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _persist_many(bot, entries, cycle_ids):
    for entry, cycle_id in zip(entries, cycle_ids):
        bot._persist_obs_001_decision_snapshot(
            entry["analysis"]["symbol"], entry, cycle_id, _now_iso(),
        )


# -- Suppression core ------------------------------------------------------


class TestUnchangedHoldSuppression:
    def test_unchanged_hold_row_suppressed(self, isolated_db):
        """Second identical HOLD_INELIGIBLE decision in the same NY day is
        skipped: exactly ONE decision_history row for the symbol."""
        bot = _make_bot(isolated_db)
        sym = "HOLDT"
        _persist_many(
            bot,
            [_hold_entry(sym, total_score=50), _hold_entry(sym, total_score=50)],
            ["dbgrow_c1", "dbgrow_c2"],
        )
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 1, f"unchanged HOLD must be suppressed; got {n} rows"

    def test_suppression_disabled_writes_every_row(self, isolated_db):
        bot = _make_bot(isolated_db, suppress=False)
        sym = "HOLDT"
        _persist_many(
            bot,
            [_hold_entry(sym, total_score=50), _hold_entry(sym, total_score=50)],
            ["dbgrow_d1", "dbgrow_d2"],
        )
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2

    def test_suppressed_row_still_upserts_analyzed_stocks(self, isolated_db):
        """analyzed_stocks keeps receiving the latest state even when the
        history row is suppressed (A4)."""
        bot = _make_bot(isolated_db)
        sym = "HOLDT"
        _persist_many(
            bot,
            [_hold_entry(sym, total_score=50), _hold_entry(sym, total_score=51)],
            ["dbgrow_e1", "dbgrow_e2"],
        )
        _db, sqlite_db_module, _p = isolated_db
        with sqlite_db_module._get_conn() as conn:
            row = conn.execute(
                "SELECT decision_snapshot FROM analyzed_stocks WHERE symbol=?",
                (sym,),
            ).fetchone()
        assert row is not None
        snap = json.loads(row[0])
        assert snap["scoring"]["total_score"] == 51

    def test_default_off_for_newless_bots(self, isolated_db):
        """A bot built via __new__ WITHOUT the new attributes must never
        suppress (fail-safe default; keeps legacy tests green)."""
        _db, sqlite_db_module, _p = isolated_db
        from src.core.smart_bot import SmartTradingBot

        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.db = sqlite_db_module.SQLiteDB()
        bot.session_id = None
        bot.enable_volume_confirmation = False
        bot.rsi_buy_threshold = 30
        bot.rsi_sell_threshold = 70
        bot.bot_version = "2.1.0-test"
        sym = "HOLDT"
        _persist_many(
            bot,
            [_hold_entry(sym, total_score=50), _hold_entry(sym, total_score=50)],
            ["dbgrow_f1", "dbgrow_f2"],
        )
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2


class TestFirstRowAlwaysWritten:
    """No same-day heartbeat exception exists.

    The FIRST row for a symbol is always written because there is no
    previous row to compare against; the SECOND unchanged HOLD is
    skipped. These tests pin that behaviour.
    """

    def test_first_row_of_symbol_always_written(self, isolated_db):
        """No previous row for the symbol: this row is always persisted,
        even though it is a HOLD."""
        bot = _make_bot(isolated_db)
        sym = "HBCT"
        _persist_many(bot, [_hold_entry(sym, total_score=50)], ["dbgrow_h1"])
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 1

    def test_second_symbol_first_row_always_written(self, isolated_db):
        """The comparison is per-symbol: symbol B's first HOLD is
        persisted even after symbol A already has a row."""
        bot = _make_bot(isolated_db)
        _persist_many(
            bot,
            [_hold_entry("HBA", total_score=50), _hold_entry("HBB", total_score=50)],
            ["dbgrow_i1", "dbgrow_i2"],
        )
        for sym in ("HBA", "HBB"):
            n = _count(
                isolated_db,
                "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
            )
            assert n == 1, f"{sym} first row missing"


class TestToleranceBoundary:
    def test_score_delta_exactly_at_tolerance_suppressed(self, isolated_db):
        """abs(delta) == tolerance -> still unchanged -> suppressed."""
        bot = _make_bot(isolated_db, tolerance=1.0)
        sym = "TOLT"
        _persist_many(
            bot,
            [_hold_entry(sym, total_score=50), _hold_entry(sym, total_score=51)],
            ["dbgrow_j1", "dbgrow_j2"],
        )
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 1

    def test_score_delta_just_above_tolerance_persisted(self, isolated_db):
        """abs(delta) > tolerance -> state changed -> row persisted."""
        bot = _make_bot(isolated_db, tolerance=1.0)
        sym = "TOLT"
        _persist_many(
            bot,
            [_hold_entry(sym, total_score=50), _hold_entry(sym, total_score=51.5)],
            ["dbgrow_k1", "dbgrow_k2"],
        )
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2

    def test_large_score_jump_persisted(self, isolated_db):
        bot = _make_bot(isolated_db, tolerance=1.0)
        sym = "TOLT"
        _persist_many(
            bot,
            [_hold_entry(sym, total_score=20), _hold_entry(sym, total_score=80)],
            ["dbgrow_l1", "dbgrow_l2"],
        )
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2
