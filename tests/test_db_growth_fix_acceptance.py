"""DB-GROWTH-FIX-3 — acceptance tests for the database-growth controls.

Covers the three observability-only features:

A. Unchanged-HOLD suppression
   Only a plain ``HOLD_INELIGIBLE`` decision may be skipped, and only
   when the observable decision state is unchanged versus the LAST
   persisted ``decision_history`` row for that symbol. There is NO
   same-day heartbeat exception. BUY, SELL and every other non-HOLD
   outcome is ALWAYS persisted in full.

B. Closed-market write suppression
   While the US equity market is closed, per-symbol
   ``decision_history`` rows and their normalized
   ``decision_gate_evaluations`` / ``decision_execution_checks``
   children are not written. The ``analyzed_stocks`` latest-state
   upsert and ``cycle_funnel`` handling still happen.

C. Retention pruning with verified gzip JSONL archive
   Expired ``decision_history`` parents and their
   ``decision_gate_evaluations`` / ``decision_execution_checks``
   children are deleted in small batches. Every other table is left
   untouched. The parent rows are archived to a gzip JSONL file FIRST;
   the archive is closed, read back, and its JSON line count is
   compared with the deletion-candidate count. Only a verified archive
   allows the delete. A write failure or a count mismatch keeps the
   rows and records an error.

Every test runs against a small temporary SQLite database created with
``tmp_path``. The production ``trading_bot.db`` is fail-closed: any
attempt to open it raises immediately (mirroring the existing
PHASE-OBS-001-ISO test-isolation architecture).
"""

import gzip
import importlib
import json
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import src.database.sqlite_db as _sqlite_db_module_for_path_capture

PRODUCTION_DB_PATH = pathlib.Path(
    str(_sqlite_db_module_for_path_capture.DB_PATH)
).resolve()


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fail_closed_production_db_guard(monkeypatch, request):
    """Fail-closed guard: any connection resolving to the production
    trading_bot.db raises immediately."""
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
    """Synthetic temp DB with the canonical schema. Yields
    (db, sqlite_db_module, db_path)."""
    import src.database.sqlite_db as sqlite_db_module

    importlib.reload(sqlite_db_module)
    temp_db_path = tmp_path / "db_growth_fix_acceptance.db"
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", temp_db_path)

    db = sqlite_db_module.SQLiteDB()
    assert db.available
    yield db, sqlite_db_module, temp_db_path


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_bot(isolated_db, *, suppress=True, market_hours_only=False,
              tolerance=1.0, prune_enabled=False, retention_days=30,
              batch_size=5000, archive_dir=None):
    """SmartTradingBot built via __new__ (repo convention) with the
    DB-GROWTH-FIX-3 knobs set explicitly."""
    _db, sqlite_db_module, _path = isolated_db
    from src.core.smart_bot import SmartTradingBot

    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = sqlite_db_module.SQLiteDB()
    bot.session_id = None
    bot.enable_volume_confirmation = False
    bot.rsi_buy_threshold = 30
    bot.rsi_sell_threshold = 70
    bot.bot_version = "2.1.0-test"
    bot.decision_history_market_hours_only = market_hours_only
    bot.decision_suppress_unchanged_holds = suppress
    bot.decision_snapshot_score_tolerance = tolerance
    bot.decision_history_prune_enabled = prune_enabled
    bot.decision_history_retention_days = retention_days
    bot.decision_history_prune_batch_size = batch_size
    bot.decision_history_archive_dir = (
        archive_dir if archive_dir is not None else ""
    )
    bot._last_retention_prune_date = None
    return bot


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _entry(symbol="HOLDT", signal="HOLD", total_score=50, rsi=45.0,
           price=100.0, sma_fast=100.0, sma_slow=110.0, macd=-0.2,
           ranking=None, selection=None, execution=None, order=None,
           execute_returned=None, not_attempted=False):
    """Build an analysis entry for the OBS-001 snapshot builder."""
    return {
        "analysis": {
            "symbol": symbol,
            "signal": signal,
            "signal_strength": "STRONG" if signal in ("BUY", "SELL") else "WEAK",
            "price": price,
            "rsi": rsi,
            "sma_fast": sma_fast,
            "sma_slow": sma_slow,
            "macd_histogram": macd,
            "total_score": total_score,
        },
        "ranking_state": ranking if ranking is not None else {"applicable": False},
        "selection_state": selection if selection is not None else {"attempted": False},
        "execution_state": execution if execution is not None else {
            "first_blocking_check": None, "checks": [],
        },
        "order_state": order if order is not None else {},
        "_execute_returned": execute_returned,
        "not_attempted": not_attempted,
        "_cycle_baseline_diagnostics": None,
    }


def _hold_entry(symbol="HOLDT", total_score=50):
    """Entry that resolves to the canonical HOLD_INELIGIBLE outcome."""
    return _entry(symbol=symbol, signal="HOLD", total_score=total_score)


def _buy_entry(symbol="BUYT", total_score=88):
    """Entry that resolves to a non-HOLD BUY outcome (BUY_BLOCKED_DYNAMIC)."""
    return _entry(
        symbol=symbol,
        signal="BUY",
        total_score=total_score,
        rsi=25.0,
        sma_fast=120.0,
        sma_slow=100.0,
        macd=0.5,
        ranking={"applicable": True, "ranked_candidate": True,
                 "candidate_rank": 1},
        selection={"attempted": True},
        execution={
            "first_blocking_check": "existing_position_check",
            "checks": [
                {"name": "existing_position_check", "applied": True,
                 "passed": False},
            ],
        },
    )


def _sell_entry(symbol="SELLT", total_score=72):
    """Entry that resolves to a non-HOLD SELL outcome (SELL_BLOCKED_DYNAMIC)."""
    return _entry(
        symbol=symbol,
        signal="SELL",
        total_score=total_score,
        rsi=78.0,
        sma_fast=100.0,
        sma_slow=90.0,
        macd=0.4,
        execution={
            "first_blocking_check": "existing_position_check",
            "checks": [
                {"name": "existing_position_check", "applied": True,
                 "passed": False},
            ],
        },
    )


def _persist(bot, entry, cycle_id):
    bot._persist_obs_001_decision_snapshot(
        entry["analysis"]["symbol"], entry, cycle_id, _now_iso(),
    )


def _count(isolated_db, sql, params=()):
    _db, sqlite_db_module, _p = isolated_db
    with sqlite_db_module._get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _outcome_of(isolated_db, symbol):
    _db, sqlite_db_module, _p = isolated_db
    with sqlite_db_module._get_conn() as conn:
        row = conn.execute(
            "SELECT decision_snapshot FROM decision_history "
            "WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])["decision"]["outcome"]


# ── A. Unchanged-HOLD suppression ─────────────────────────────────────────


class TestAcceptanceUnchangedHold:
    def test_1_unchanged_hold_is_skipped(self, isolated_db):
        """AC1: an unchanged HOLD_INELIGIBLE decision is NOT persisted a
        second time. Exactly ONE decision_history row for the symbol."""
        bot = _make_bot(isolated_db, suppress=True)
        sym = "HOLDT"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_c1")
        _persist(bot, _hold_entry(sym, total_score=50), "acc_c2")
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 1, f"unchanged HOLD must be suppressed; got {n} rows"
        assert _outcome_of(isolated_db, sym) == "HOLD_INELIGIBLE"

    def test_1b_no_heartbeat_exception_first_row_still_written(self, isolated_db):
        """AC1b: there is NO same-day heartbeat exception. The FIRST row
        for a symbol is always written (there is nothing to compare
        against), and an unchanged second row is skipped."""
        bot = _make_bot(isolated_db, suppress=True)
        sym = "HBCT"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_h1")
        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        ) == 1
        _persist(bot, _hold_entry(sym, total_score=50), "acc_h2")
        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        ) == 1

    def test_2_changed_hold_is_retained(self, isolated_db):
        """AC2: a HOLD whose observable state CHANGED is persisted."""
        bot = _make_bot(isolated_db, suppress=True, tolerance=1.0)
        sym = "HOLDT"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_d1")
        # Score moved 20 points — far beyond the tolerance.
        _persist(bot, _hold_entry(sym, total_score=70), "acc_d2")
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2, f"changed HOLD must be retained; got {n} rows"

    def test_2b_hold_within_tolerance_is_skipped(self, isolated_db):
        """AC2b: a HOLD whose score moved less than the tolerance still
        counts as unchanged and is skipped."""
        bot = _make_bot(isolated_db, suppress=True, tolerance=1.0)
        sym = "HOLDT"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_t1")
        _persist(bot, _hold_entry(sym, total_score=50.5), "acc_t2")
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 1

    def test_2c_comparison_is_against_last_row_for_that_symbol_only(
        self, isolated_db,
    ):
        """AC2c: suppression compares against the LAST stored row for
        THAT symbol. A different symbol never affects the comparison."""
        bot = _make_bot(isolated_db, suppress=True)
        _persist(bot, _hold_entry("HBA", total_score=50), "acc_s1")
        _persist(bot, _hold_entry("HBB", total_score=50), "acc_s2")
        # Both symbols have exactly one row.
        for sym in ("HBA", "HBB"):
            assert _count(
                isolated_db,
                "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
            ) == 1
        # An unchanged repeat for HBA is skipped, for HBB is skipped too.
        _persist(bot, _hold_entry("HBA", total_score=50), "acc_s3")
        _persist(bot, _hold_entry("HBB", total_score=50), "acc_s4")
        for sym in ("HBA", "HBB"):
            assert _count(
                isolated_db,
                "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
            ) == 1

    def test_3_buy_is_always_retained(self, isolated_db):
        """AC3: a BUY decision is ALWAYS persisted, even when the state is
        identical to the previous BUY row for that symbol."""
        bot = _make_bot(isolated_db, suppress=True)
        sym = "BUYT"
        _persist(bot, _buy_entry(sym), "acc_b1")
        _persist(bot, _buy_entry(sym), "acc_b2")
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2, f"BUY must always be persisted; got {n} rows"
        assert _outcome_of(isolated_db, sym).startswith("BUY")

    def test_4_sell_is_always_retained(self, isolated_db):
        """AC4: a SELL decision is ALWAYS persisted, even when the state
        is identical to the previous SELL row for that symbol."""
        bot = _make_bot(isolated_db, suppress=True)
        sym = "SELLT"
        _persist(bot, _sell_entry(sym), "acc_sell1")
        _persist(bot, _sell_entry(sym), "acc_sell2")
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2, f"SELL must always be persisted; got {n} rows"
        assert _outcome_of(isolated_db, sym).startswith("SELL")

    def test_suppression_failure_falls_back_to_writing(self, isolated_db,
                                                        monkeypatch):
        """Fail-safe: a lookup failure must never drop a row."""
        bot = _make_bot(isolated_db, suppress=True)
        sym = "FAILT"

        def _boom(_symbol):
            raise RuntimeError("lookup exploded")

        monkeypatch.setattr(bot.db, "get_latest_decision_history", _boom)
        _persist(bot, _hold_entry(sym, total_score=50), "acc_f1")
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 1, "a failed lookup must fall back to writing the row"

    def test_suppression_disabled_writes_every_row(self, isolated_db):
        """The knob can be turned off: every HOLD is persisted."""
        bot = _make_bot(isolated_db, suppress=False)
        sym = "HOLDT"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_o1")
        _persist(bot, _hold_entry(sym, total_score=50), "acc_o2")
        n = _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        )
        assert n == 2

    def test_only_hold_ineligible_is_suppressible(self):
        """The suppressible set contains exactly HOLD_INELIGIBLE."""
        from src.core.smart_bot import SmartTradingBot

        assert SmartTradingBot.DB_GROWTH_SUPPRESSIBLE_OUTCOMES == frozenset(
            {"HOLD_INELIGIBLE"}
        )


# ── B. Closed-market write suppression ────────────────────────────────────


class TestAcceptanceClosedMarket:
    def test_5_closed_market_cycle_writes_no_history_rows(self, isolated_db,
                                                            monkeypatch):
        """AC5: while the market is closed the cycle writes NO
        decision_history row and NO normalized child rows, while the
        analyzed_stocks latest-state upsert still happens."""
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: False,
        )
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        sym = "CLOSEDT"

        # Spy on the analyzed_stocks latest-state upsert: the closed-market
        # control must NOT prevent it (it is latest state, not history).
        upsert_calls = []
        original_save = bot.db.save_analysis_result

        def _spy(symbol, analysis):
            upsert_calls.append(symbol)
            return original_save(symbol, analysis)

        bot.db.save_analysis_result = _spy

        _persist(bot, _hold_entry(sym, total_score=50), "acc_closed1")

        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        ) == 0
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_gate_evaluations",
        ) == 0
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_execution_checks",
        ) == 0
        # The analyzed_stocks latest-state upsert still happens.
        assert upsert_calls == [sym]

    def test_5b_closed_market_never_writes_buy_or_sell_rows(self, isolated_db,
                                                              monkeypatch):
        """AC5b: closed-market suppression applies to BUY and SELL too —
        the control is about storage, not about the decision."""
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: False,
        )
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        _persist(bot, _buy_entry("BUYC"), "acc_closed2")
        _persist(bot, _sell_entry("SELLC"), "acc_closed3")
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 0

    def test_5c_open_market_writes_rows(self, isolated_db, monkeypatch):
        """AC5c: with the market open the rows are written normally."""
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: True,
        )
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        sym = "OPENT"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_open1")
        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        ) == 1

    def test_5d_calendar_failure_falls_back_to_writing(self, isolated_db,
                                                        monkeypatch):
        """Fail-safe: a calendar error must never drop a row."""
        import src.core.smart_bot as smart_bot_module

        def _boom(*a, **k):
            raise RuntimeError("calendar exploded")

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open", _boom,
        )
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        sym = "CALCT"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_calcfail")
        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        ) == 1

    def test_5e_market_hours_only_disabled_writes_rows(self, isolated_db,
                                                        monkeypatch):
        """The knob can be turned off: rows are written while closed."""
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: False,
        )
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=False)
        sym = "ALWAYST"
        _persist(bot, _hold_entry(sym, total_score=50), "acc_always")
        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_history WHERE symbol=?", (sym,),
        ) == 1


# ── C. Retention pruning with verified archive ────────────────────────────


def _seed_history(isolated_db, *, old_rows=3, fresh_rows=1,
                  symbol_prefix="OLD"):
    """Insert decision_history parents (with children) directly.

    ``old_rows`` parents get a cycle_start older than the retention
    window; ``fresh_rows`` parents get a current cycle_start.
    Returns the list of old parent ids.
    """
    _db, sqlite_db_module, _p = isolated_db
    old_ids = []
    now = datetime.now(timezone.utc)
    with sqlite_db_module._get_conn() as conn:
        for i in range(old_rows):
            cycle_start = (
                now - timedelta(days=120, minutes=i)
            ).isoformat()
            snapshot = {
                "decision": {"outcome": "HOLD_INELIGIBLE",
                             "primary_reason": "seeded"},
                "strategy_eligibility": {
                    "strategy_eligible": False,
                    "gates": [{
                        "name": "rsi_oversold",
                        "category": "strategy_gate",
                        "applied": True,
                        "passed": False,
                        "observed_value": 45.0,
                        "threshold_value": 30.0,
                        "reason": "seeded",
                    }],
                },
                "execution_checks": {
                    "checks": [{
                        "name": "existing_position_check",
                        "applied": True,
                        "passed": False,
                        "observed_value": 1.0,
                        "threshold_value": 0.0,
                        "reason": "seeded",
                    }],
                    "first_blocking_check": "existing_position_check",
                },
                "scoring": {"total_score": 50},
            }
            cur = conn.execute(
                "INSERT INTO decision_history (cycle_id, symbol, cycle_start, "
                "session_id, decision_schema_version, decision_snapshot, "
                "analytics_persistence_version) VALUES (?,?,?,?,?,?,1)",
                (f"seed_old_{i}", f"{symbol_prefix}{i}", cycle_start, None,
                 1, json.dumps(snapshot)),
            )
            pid = cur.lastrowid
            old_ids.append(pid)
            conn.execute(
                "INSERT INTO decision_gate_evaluations (decision_history_id, "
                "cycle_id, symbol, cycle_start, ordinality, gate_name, "
                "gate_category, applied, passed, observed_value, "
                "threshold_value, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"seed_old_{i}", f"{symbol_prefix}{i}", cycle_start,
                 0, "rsi_oversold", "strategy_gate", 1, 0, 45.0, 30.0,
                 "seeded"),
            )
            conn.execute(
                "INSERT INTO decision_execution_checks (decision_history_id, "
                "cycle_id, symbol, cycle_start, ordinality, check_name, "
                "applied, passed, observed_value, threshold_value, reason, "
                "gap_note, is_first_blocking) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"seed_old_{i}", f"{symbol_prefix}{i}", cycle_start,
                 0, "existing_position_check", 1, 0, 1.0, 0.0, "seeded",
                 None, 1),
            )
        for i in range(fresh_rows):
            conn.execute(
                "INSERT INTO decision_history (cycle_id, symbol, cycle_start, "
                "session_id, decision_schema_version, decision_snapshot, "
                "analytics_persistence_version) VALUES (?,?,?,?,?,?,1)",
                (f"seed_fresh_{i}", f"FRESH{i}", now.isoformat(), None, 1,
                 json.dumps({"decision": {"outcome": "HOLD_INELIGIBLE"}})),
            )
    return old_ids


def _seed_protected_tables(isolated_db):
    """Seed rows in tables the prune must never touch."""
    _db, sqlite_db_module, _p = isolated_db
    with sqlite_db_module._get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (session_id, symbol, side, qty, price, pnl, "
            "signal, rsi, order_time, status) "
            "VALUES (NULL,'PROTT','BUY',1,10.0,0.0,'BUY',30.0,'2020-01-01',"
            "'FILLED')"
        )
        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO order_events (trade_id, broker_order_id, "
            "canonical_status, raw_status, filled_qty, avg_fill_price, "
            "event_note, unresolved) VALUES (?,?,?,?,?,?,?,0)",
            (trade_id, "ord-1", "FILLED", "filled", 1.0, 10.0, "seeded"),
        )
        conn.execute(
            "INSERT INTO analyzed_stocks (symbol, price, total_score, signal, "
            "signal_strength, rsi, last_analyzed) "
            "VALUES ('PROTT',10.0,50,'HOLD','WEAK',30.0,'2020-01-01')"
        )
        conn.execute(
            "INSERT INTO failed_analyses (symbol, signal_type, total_score, "
            "rsi, price, failed_filters) VALUES ('PROTF','BUY',10,30,10,'x')"
        )
        conn.execute(
            "INSERT INTO cycle_funnel (cycle_id, session_id, cycle_start, "
            "cycle_end, analyzed_count, strategy_eligible_count, "
            "ranked_candidate_count, execution_attempt_count, "
            "execution_blocked_count, order_submission_attempt_count, "
            "order_submitted_count, order_failed_count, not_attempted_count) "
            "VALUES ('seed_funnel',NULL,'2020-01-01','2020-01-01',1,0,0,0,0,0,"
            "0,0,0)"
        )


class TestAcceptanceRetentionPrune:
    def test_6_archive_written_and_verified_before_deletion(self, isolated_db,
                                                             tmp_path):
        """AC6: the expired parents are archived to a gzip JSONL file and
        the archive is read back and verified BEFORE the rows are
        deleted."""
        _db, sqlite_db_module, _p = isolated_db
        old_ids = _seed_history(isolated_db, old_rows=3, fresh_rows=1)
        archive_dir = tmp_path / "archive"

        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30,
            batch_size=5000,
            archive_dir=str(archive_dir),
        )

        assert result["error"] is None, result["error"]
        assert result["parents_deleted"] == 3
        # The archive exists and contains one JSON object per deleted row.
        assert len(result["archived_files"]) == 1
        archive_path = pathlib.Path(result["archived_files"][0])
        assert archive_path.exists()
        assert archive_path.name.startswith("decision_history_")
        assert archive_path.name.endswith(".jsonl.gz")
        with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
            lines = [ln for ln in handle if ln.strip()]
        assert len(lines) == 3
        archived = [json.loads(ln) for ln in lines]
        assert sorted(int(a["id"]) for a in archived) == sorted(old_ids)
        # All columns of each archived row are present.
        for row in archived:
            for column in ("id", "cycle_id", "symbol", "cycle_start",
                           "decision_snapshot"):
                assert column in row
        # The rows really were deleted and their children too.
        assert _count(isolated_db, "SELECT COUNT(*) FROM decision_history") == 1
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_gate_evaluations",
        ) == 0
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_execution_checks",
        ) == 0

    def test_6b_verification_happens_before_the_delete(self, isolated_db,
                                                        tmp_path,
                                                        monkeypatch):
        """AC6b: the archive verification runs while the parent rows are
        STILL in the database — proof that the archive is verified
        before any deletion."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=2, fresh_rows=0)
        archive_dir = tmp_path / "archive"
        observed = {}

        original_count = sqlite_db_module.SQLiteDB._count_archive_rows

        def _counting(path):
            observed["parents_visible_during_verification"] = _count(
                isolated_db, "SELECT COUNT(*) FROM decision_history",
            )
            return original_count(path)

        monkeypatch.setattr(
            sqlite_db_module.SQLiteDB, "_count_archive_rows",
            staticmethod(_counting),
        )
        sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000, archive_dir=str(archive_dir),
        )
        assert observed["parents_visible_during_verification"] == 2

    def test_6c_archive_write_failure_keeps_the_rows(self, isolated_db,
                                                       tmp_path,
                                                       monkeypatch):
        """AC6c: if the archive write fails, nothing is deleted."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=2, fresh_rows=0)

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(
            sqlite_db_module.SQLiteDB, "_archive_decision_history_rows",
            staticmethod(_boom),
        )
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is not None
        assert "archive_write_failed" in result["error"]
        assert result["parents_deleted"] == 0
        assert _count(isolated_db, "SELECT COUNT(*) FROM decision_history") == 2

    def test_7_other_tables_are_untouched(self, isolated_db, tmp_path):
        """AC7: the prune only deletes decision_history parents and their
        two child tables. Every other table keeps its rows."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=2, fresh_rows=1)
        _seed_protected_tables(isolated_db)

        before = {
            table: _count(isolated_db, f"SELECT COUNT(*) FROM {table}")
            for table in ("trades", "order_events", "analyzed_stocks",
                          "failed_analyses", "cycle_funnel",
                          "market_data_eligibility_cache",
                          "research_cooldowns", "position_sell_cooldowns",
                          "trade_cooldowns", "trading_sessions")
        }
        assert before["trades"] == 1
        assert before["order_events"] == 1

        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None
        after = {
            table: _count(isolated_db, f"SELECT COUNT(*) FROM {table}")
            for table in before
        }
        assert after == before, f"protected tables changed: {before} -> {after}"
        # Only the three target tables shrank.
        assert _count(isolated_db, "SELECT COUNT(*) FROM decision_history") == 1

    def test_8_archive_count_mismatch_prevents_deletion(self, isolated_db,
                                                         tmp_path,
                                                         monkeypatch):
        """AC8: when the archive line count does not match the number of
        deletion candidates the rows are NOT deleted and the error is
        recorded."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=3, fresh_rows=0)

        monkeypatch.setattr(
            sqlite_db_module.SQLiteDB, "_count_archive_rows",
            staticmethod(lambda path: 1),  # wrong on purpose
        )
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is not None
        assert "archive_row_count_mismatch" in result["error"]
        assert result["parents_deleted"] == 0
        assert result["batches"] == 0
        # The rows and their children are still there.
        assert _count(isolated_db, "SELECT COUNT(*) FROM decision_history") == 3
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_gate_evaluations",
        ) == 3
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_execution_checks",
        ) == 3

    def test_8b_unreadable_archive_prevents_deletion(self, isolated_db,
                                                      tmp_path,
                                                      monkeypatch):
        """AC8b: an archive that cannot be read back (-1) also prevents
        the deletion."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=2, fresh_rows=0)

        monkeypatch.setattr(
            sqlite_db_module.SQLiteDB, "_count_archive_rows",
            staticmethod(lambda path: -1),
        )
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is not None
        assert "archive_row_count_mismatch" in result["error"]
        assert _count(isolated_db, "SELECT COUNT(*) FROM decision_history") == 2

    def test_prune_uses_small_batches(self, isolated_db, tmp_path):
        """The prune deletes in small batches and keeps going until the
        candidates are exhausted."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=5, fresh_rows=0)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=2,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None
        assert result["parents_deleted"] == 5
        assert result["batches"] >= 3
        assert _count(isolated_db, "SELECT COUNT(*) FROM decision_history") == 0

    def test_fresh_rows_are_never_pruned(self, isolated_db, tmp_path):
        """Rows inside the retention window are never deleted."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=1, fresh_rows=2)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["parents_deleted"] == 1
        assert _count(isolated_db, "SELECT COUNT(*) FROM decision_history") == 2

    def test_bot_prune_hook_runs_once_per_ny_day(self, isolated_db,
                                                  tmp_path, monkeypatch):
        """The automatic hook runs at most once per NY calendar day and
        only while the market is closed."""
        import src.core.smart_bot as smart_bot_module

        _db, sqlite_db_module, _p = isolated_db
        _seed_history(isolated_db, old_rows=2, fresh_rows=0)
        calls = []

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: False,
        )
        bot = _make_bot(isolated_db, prune_enabled=True,
                        archive_dir=str(tmp_path / "archive"))

        def _fake_prune():
            calls.append(1)
            return {}

        monkeypatch.setattr(bot, "run_decision_history_retention_prune",
                            _fake_prune)
        bot._maybe_run_retention_prune()
        bot._maybe_run_retention_prune()
        bot._maybe_run_retention_prune()
        assert len(calls) == 1, "the hook must run at most once per NY day"

    def test_bot_prune_hook_does_not_run_during_market_hours(
        self, isolated_db, tmp_path, monkeypatch,
    ):
        """The hook never runs while the market is open."""
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: True,
        )
        bot = _make_bot(isolated_db, prune_enabled=True,
                        archive_dir=str(tmp_path / "archive"))
        calls = []
        monkeypatch.setattr(bot, "run_decision_history_retention_prune",
                            lambda: calls.append(1))
        bot._maybe_run_retention_prune()
        assert calls == []

    def test_bot_prune_disabled_is_a_no_op(self, isolated_db, tmp_path):
        """With the master switch off the prune does nothing."""
        bot = _make_bot(isolated_db, prune_enabled=False,
                        archive_dir=str(tmp_path / "archive"))
        summary = bot.run_decision_history_retention_prune()
        assert summary["enabled"] is False
        assert summary["error"] == "disabled"
        assert summary["parents_deleted"] == 0


# ── Settings wiring ───────────────────────────────────────────────────────


class TestSettingsWiring:
    def test_retention_default_is_30_days(self):
        from src.core import settings_service

        definition = settings_service.STRATEGY_SETTINGS_SCHEMA[
            "decision_history_retention_days"
        ]
        assert definition.default == 30

    def test_archive_dir_default(self):
        from src.core import settings_service

        definition = settings_service.STRATEGY_SETTINGS_SCHEMA[
            "decision_history_archive_dir"
        ]
        assert definition.default == "research_data/db_archive"

    def test_effective_settings_reach_the_bot(self, monkeypatch):
        """The effective (DB-override) settings are applied to the bot
        attributes."""
        import src.core.smart_bot as smart_bot_module

        settings_service = smart_bot_module.settings_service

        bot = smart_bot_module.SmartTradingBot.__new__(
            smart_bot_module.SmartTradingBot
        )
        bot.decision_history_retention_days = 30
        bot.decision_suppress_unchanged_holds = True
        bot.decision_history_market_hours_only = True
        bot.atr_position_size_pct = 2.0

        monkeypatch.setattr(
            settings_service, "load_effective_strategy_settings",
            lambda: {
                "decision_history_retention_days": 45,
                "decision_suppress_unchanged_holds": False,
                "decision_history_market_hours_only": False,
            },
        )
        bot._load_effective_strategy_settings()
        assert bot.decision_history_retention_days == 45
        assert bot.decision_suppress_unchanged_holds is False
        assert bot.decision_history_market_hours_only is False

    def test_relative_archive_dir_resolves_against_repo_root(self, isolated_db):
        """A relative archive dir resolves against the repository root."""
        bot = _make_bot(isolated_db, archive_dir="research_data/db_archive")
        resolved = bot._resolve_archive_dir()
        assert resolved.endswith("research_data/db_archive")
        assert pathlib.Path(resolved).is_absolute()
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        assert pathlib.Path(resolved) == repo_root / "research_data/db_archive"
