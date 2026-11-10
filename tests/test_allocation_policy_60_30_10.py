"""
REBAL-60-30-10 — long-term allocation budget, cash floor, and
swing-runner position isolation.

Approved split (Josh, 2026-11-09): 60% long-term (this bot), 30%
short-term (external swing runners C4 + RSI(2) sharing the same
Alpaca paper account), 10% cash floor.

Contracts under test (see src/core/allocation_policy.py):
- Pure budget math; no broker calls.
- Foreign swing-runner positions are NEVER sold/resized/counted as own.
- Ownership must be ledger-proven (own trades, status FILLED, filled_qty).
- Unattributed positions are never managed but DO count toward the
  long-term budget (under-deploy rather than over-deploy).
- The cash floor is a hard BUY-side constraint.
- No entry-rule (min_score_buy, RSI, liquidity...) changes anywhere.
"""

import sqlite3
import time
from types import SimpleNamespace

import pytest

from src.core import allocation_policy as ap
from src.core import settings_service
from src.core.smart_bot import SmartTradingBot


# ── fakes ────────────────────────────────────────────────────────────────────

def _order(client_order_id, symbol):
    return SimpleNamespace(client_order_id=client_order_id, symbol=symbol)


def _position(symbol, qty, market_value):
    return SimpleNamespace(
        symbol=symbol,
        qty=qty,
        market_value=market_value,
        current_price=market_value / max(qty, 1),
        unrealized_plpc=0.0,
    )


def _ledger_row(symbol, side, qty, status="FILLED"):
    return {
        "symbol": symbol,
        "side": side,
        "filled_qty": qty,
        "status": status,
    }


def _make_bot():
    """Bot skeleton without __init__ (no Alpaca creds, no DB, no network)."""
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.long_term_budget_pct = 60.0
    bot.cash_floor_pct = 10.0
    bot.swing_order_label_prefixes = '["sl_short-", "rsi2-"]'
    bot.swing_runner_db_paths = "[]"
    return bot


def _prime_caches(bot, swing_symbols, own_net):
    """Pre-populate the attribution caches so no client is contacted."""
    now = time.monotonic()
    bot._swing_symbols_cache = (set(swing_symbols), now)
    bot._own_net_cache = (dict(own_net), now)


# ── 1. Pure budget math ─────────────────────────────────────────────────────

class TestBudgetMath:
    def test_long_term_budget(self):
        assert ap.long_term_budget(106_622.46, 60.0) == pytest.approx(63_973.476)

    def test_cash_floor_amount(self):
        assert ap.cash_floor_amount(106_622.46, 10.0) == pytest.approx(10_662.246)

    def test_budget_room_basic(self):
        assert ap.budget_room(100_000, 60.0, 20_000) == pytest.approx(40_000)

    def test_budget_room_never_negative(self):
        assert ap.budget_room(100_000, 60.0, 75_000) == 0.0

    def test_max_buy_cost_takes_tighter_constraint(self):
        # cash headroom (76k - 10k) > budget room (47.8k) -> budget wins
        assert ap.max_buy_cost(76_000, 100_000, 10.0, 47_800) == pytest.approx(47_800)
        # cash headroom tighter than budget room -> cash floor wins
        assert ap.max_buy_cost(12_000, 100_000, 10.0, 47_800) == pytest.approx(2_000)

    def test_max_buy_cost_blocks_when_cash_at_floor(self):
        assert ap.max_buy_cost(10_000, 100_000, 10.0, 47_800) == 0.0

    def test_cap_quantity_for_cost(self):
        assert ap.cap_quantity_for_cost(10, 50.0, 225.0) == 4
        assert ap.cap_quantity_for_cost(10, 50.0, 500.0) == 10
        assert ap.cap_quantity_for_cost(10, 50.0, 49.99) == 0
        assert ap.cap_quantity_for_cost(0, 50.0, 500.0) == 0
        assert ap.cap_quantity_for_cost(5, 0.0, 500.0) == 0

    def test_buy_respects_cash_floor(self):
        assert ap.buy_respects_cash_floor(76_000, 66_000, 100_000, 10.0)
        assert not ap.buy_respects_cash_floor(76_000, 66_000.01, 100_000, 10.0)


# ── 2. Swing-runner attribution (order labels) ───────────────────────────────

class TestSwingAttribution:
    def test_order_label_prefixes_match_real_runner_ids(self):
        orders = [
            _order("sl_short-AMD-s-20261102", "AMD"),      # C4 stop (live)
            _order("rsi2-AMD-20261103-1-stop-r2", "AMD"),  # RSI(2) stop variant
            _order("rsi2-AVTR-20261105-2", "AVTR"),
            _order("AAPL-20261105-991", "AAPL"),           # main bot
        ]
        syms = ap.swing_symbols_from_orders(orders, ap.DEFAULT_SWING_ORDER_LABEL_PREFIXES)
        assert syms == {"AMD", "AVTR"}

    def test_no_labeled_orders_means_no_swing_symbols(self):
        orders = [_order("AAPL-20261105-991", "AAPL")]
        assert ap.swing_symbols_from_orders(orders, ap.DEFAULT_SWING_ORDER_LABEL_PREFIXES) == set()

    def test_runner_db_open_positions(self, tmp_path):
        db = tmp_path / "live_paper.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE positions (symbol TEXT, status TEXT)")
        conn.executemany(
            "INSERT INTO positions VALUES (?, ?)",
            [("AMD", "open"), ("TSLA", "closed"), ("AVTR", "OPEN")],
        )
        conn.commit()
        conn.close()
        assert ap.swing_symbols_from_runner_db(str(db)) == {"AMD", "AVTR"}

    def test_runner_db_missing_file_fails_closed(self, tmp_path):
        assert ap.swing_symbols_from_runner_db(str(tmp_path / "nope.db")) == set()

    def test_runner_db_without_positions_table_fails_closed(self, tmp_path):
        db = tmp_path / "empty.db"
        sqlite3.connect(db).close()
        assert ap.swing_symbols_from_runner_db(str(db)) == set()


# ── 3. Ledger-proven ownership ───────────────────────────────────────────────

class TestLedgerOwnership:
    def test_net_qty_from_filled_rows_only(self):
        rows = [
            _ledger_row("QQQI", "BUY", 39),
            _ledger_row("QQQI", "BUY", 34),
            _ledger_row("QQQI", "BUY", 25),
            _ledger_row("QQQI", "SELL", 98),
            _ledger_row("CMA", "BUY", 17),
            # non-FILLED rows prove nothing
            _ledger_row("FAKE", "BUY", 100, status="SUBMITTED"),
            _ledger_row("FAKE2", "SELL", 100, status="CANCELED"),
        ]
        net = ap.own_net_qty_from_ledger_rows(rows)
        assert net == {"CMA": 17.0}  # QQQI nets to 0 -> fully exited

    def test_empty_and_malformed_rows(self):
        assert ap.own_net_qty_from_ledger_rows(None) == {}
        assert ap.own_net_qty_from_ledger_rows([{}, None, {"status": "FILLED"}]) == {}


class TestClassifyPositions:
    def test_three_way_classification(self):
        positions = [
            _position("AMD", 22, 11_061.38),    # swing (runner-labeled)
            _position("CMA", 17, 1_507.39),     # own (ledger-proven)
            _position("AVTR", 679, 10_711.23),  # unattributed
        ]
        out = ap.classify_positions(positions, own_net={"CMA": 17.0}, swing_symbols={"AMD"})
        assert out["by_symbol"]["AMD"]["classification"] == ap.SWING
        assert out["by_symbol"]["CMA"]["classification"] == ap.OWN
        assert out["by_symbol"]["AVTR"]["classification"] == ap.UNATTRIBUTED
        # Unattributed still counts toward the budget (under-deploy, never over)
        assert out["long_term_book_mv"] == pytest.approx(1_507.39 + 10_711.23)
        assert out["own_mv"] == pytest.approx(1_507.39)
        assert out["swing_mv"] == pytest.approx(11_061.38)
        assert out["unattributed_mv"] == pytest.approx(10_711.23)


# ── 4. Config values, not hard-coded ────────────────────────────────────────

class TestConfigNotHardCoded:
    def test_settings_schema_contains_new_keys_with_approved_defaults(self):
        for key in (
            "long_term_budget_pct",
            "cash_floor_pct",
            "swing_order_label_prefixes",
            "swing_runner_db_paths",
        ):
            assert key in settings_service.STRATEGY_SETTINGS_SCHEMA, key
        assert settings_service.STRATEGY_SETTINGS_SCHEMA["long_term_budget_pct"].default == 60.0
        assert settings_service.STRATEGY_SETTINGS_SCHEMA["cash_floor_pct"].default == 10.0
        assert settings_service.STRATEGY_SETTINGS_SCHEMA["swing_order_label_prefixes"].default == '["sl_short-", "rsi2-"]'

    def test_parse_json_list_fail_closed(self):
        assert ap.parse_json_list('["sl_short-"]', ()) == ("sl_short-",)
        assert ap.parse_json_list(None, ("x",)) == ("x",)
        assert ap.parse_json_list("not json", ("x",)) == ("x",)
        assert ap.parse_json_list('["a", "b"]', ()) == ("a", "b")

    def test_budget_respects_config_override(self):
        bot = _make_bot()
        bot.long_term_budget_pct = 50.0
        bot.cash_floor_pct = 20.0
        _prime_caches(bot, set(), {})
        bot.trading_client = SimpleNamespace(
            get_account=lambda: SimpleNamespace(portfolio_value=100_000, cash=50_000),
            get_all_positions=lambda: [],
        )
        view = bot.get_allocation_budget_view()
        assert view["long_term_budget"] == pytest.approx(50_000)
        assert view["cash_floor"] == pytest.approx(20_000)


# ── 5. SmartBot wiring ──────────────────────────────────────────────────────

class TestSmartBotWiring:
    def test_foreign_or_unproven_position(self):
        bot = _make_bot()
        _prime_caches(bot, swing_symbols={"AMD"}, own_net={"CMA": 17.0})
        assert bot._is_foreign_or_unproven_position("AMD") is True    # swing
        assert bot._is_foreign_or_unproven_position("AVTR") is True   # not ledger-proven
        assert bot._is_foreign_or_unproven_position("CMA") is False   # proven own

    def test_get_allocation_budget_view_full_math(self):
        bot = _make_bot()
        # Paper-account-shaped scenario: equity 106.6k, 76% cash,
        # AMD=swing, CMA=own, AVTR=unattributed.
        _prime_caches(bot, swing_symbols={"AMD"}, own_net={"CMA": 17.0})
        bot.trading_client = SimpleNamespace(
            get_account=lambda: SimpleNamespace(portfolio_value=106_622.46, cash=81_090.96),
            get_all_positions=lambda: [
                _position("AMD", 22, 11_061.38),
                _position("CMA", 17, 1_507.39),
                _position("AVTR", 679, 10_711.23),
            ],
        )
        view = bot.get_allocation_budget_view()
        assert view["equity"] == pytest.approx(106_622.46)
        assert view["long_term_budget"] == pytest.approx(63_973.476)
        assert view["cash_floor"] == pytest.approx(10_662.246)
        assert view["long_term_book_mv"] == pytest.approx(1_507.39 + 10_711.23)
        room = 63_973.476 - 12_218.62
        assert view["budget_room"] == pytest.approx(room, rel=1e-6)
        assert view["max_buy_cost"] == pytest.approx(min(room, 81_090.96 - 10_662.246), rel=1e-6)

    def test_budget_view_fails_closed_without_account(self):
        bot = _make_bot()

        def _boom():
            raise RuntimeError("alpaca down")

        bot.trading_client = SimpleNamespace(get_account=_boom, get_all_positions=lambda: [])
        assert bot.get_allocation_budget_view() is None

    def test_get_swing_runner_symbols_uses_labels_and_db(self, tmp_path):
        db = tmp_path / "live_paper.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE positions (symbol TEXT, status TEXT)")
        conn.execute("INSERT INTO positions VALUES ('AMD', 'open')")
        conn.commit()
        conn.close()
        bot = _make_bot()
        bot.swing_runner_db_paths = '["{}"]'.format(db)
        bot._swing_symbols_cache = (set(), 0.0)  # force refresh
        bot.trading_client = SimpleNamespace(
            get_orders=lambda: [
                _order("sl_short-AVTR-s-20261102", "AVTR"),
                _order("AAPL-20261105-1", "AAPL"),
            ]
        )
        assert bot.get_swing_runner_symbols(force_refresh=True) == {"AMD", "AVTR"}

    def test_ledger_unavailable_means_nothing_is_own(self):
        bot = _make_bot()
        bot._swing_symbols_cache = (set(), 0.0)
        bot._own_net_cache = ({}, 0.0)
        bot.db = None  # no ledger -> fail-closed
        assert bot._ledger_own_net_qty(force_refresh=True) == {}
        assert bot._is_foreign_or_unproven_position("CMA") is True

    def test_obs_check_order_registers_allocation_budget(self):
        from src.core.smart_bot import OBS_001_EXECUTION_CHECK_ORDER
        assert "allocation_budget_check" in OBS_001_EXECUTION_CHECK_ORDER
        assert (
            OBS_001_EXECUTION_CHECK_ORDER.index("allocation_budget_check")
            == OBS_001_EXECUTION_CHECK_ORDER.index("swing_symbol_check") + 1
        )
        assert (
            OBS_001_EXECUTION_CHECK_ORDER.index("swing_symbol_check")
            == OBS_001_EXECUTION_CHECK_ORDER.index("position_concentration_check") + 1
        )
        assert OBS_001_EXECUTION_CHECK_ORDER[-1] == "quantity_post_sizing_check"
