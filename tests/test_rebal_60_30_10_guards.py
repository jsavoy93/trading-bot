"""REBAL-60-30-10 (fix2) — tranche/continuation BUY budget guard, SELL
ownership guard, and swing-symbol blocking.

Scope (PAPER ONLY, no merge/deploy without approval):

  A. Tranche/continuation BUYs are subject to the SAME money
     constraints as an initial BUY:
       A1. the allocation budget caps the tranche quantity;
       A2. the cash floor blocks the tranche when it would be broken;
       A3. an UNAVAILABLE allocation-budget view FAILS CLOSED (the
           tranche is blocked — a broker order is irreversible);
       A4. a swing-runner-attributed symbol blocks the tranche BUY
           (continuation BUY path);
       A5. a swing-attribution READ failure does NOT block the tranche
           (fail-open on attribution reads, matching the initial BUY).
  B. SELLs refuse foreign or unproven positions:
       B1. a swing-runner-attributed position (AMD) is never sold;
       B2. a position with no ledger-proven own quantity is never sold;
       B3. the SELL quantity is capped to the ledger-proven own quantity
           (never the raw broker quantity).
  C. The OBS-001 check order/snapshot reflects the new guards.
  D. Portfolio-management SELL sizing (R2): the concentration-reduction
     and stop-loss paths cap the SELL to min(broker_qty, own_qty) using the
     ledger-proven own quantity, and fail closed when ownership cannot be
     proven.

Deterministic. Mocked broker responses only — NO Alpaca/network calls and
no order is ever submitted to a real endpoint.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src.core.smart_bot import SmartTradingBot  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _budget_view(**overrides):
    view = {
        "equity": 100_000.0,
        "cash": 50_000.0,
        "budget_pct": 60.0,
        "cash_floor_pct": 10.0,
        "long_term_budget": 60_000.0,
        "cash_floor": 10_000.0,
        "long_term_book_mv": 20_000.0,
        "own_mv": 20_000.0,
        "swing_mv": 0.0,
        "unattributed_mv": 0.0,
        "budget_room": 40_000.0,
        "max_buy_cost": 40_000.0,
        "by_symbol": {},
    }
    view.update(overrides)
    return view


def _tranche_bot(*, budget_view=_budget_view(), swing_symbols=(), qty=5,
                 price=100.0):
    """Namespace exercising the REAL _tranche_risk_gate."""
    client = Mock()
    client.get_account.return_value = SimpleNamespace(
        cash=50_000.0, portfolio_value=100_000.0
    )
    client.get_orders.return_value = []

    ns = SimpleNamespace(
        trading_client=client,
        db=None,
        session_id=7,
        long_term_budget_pct=60.0,
        cash_floor_pct=10.0,
        # checks that must all pass for the gate to reach its verdict
        get_portfolio_total_value=Mock(return_value=100_000.0),
        check_position_limits=Mock(return_value=(True, qty, 1.0)),
        check_sector_concentration=Mock(return_value=(True, 0.0, 0.0, "ok")),
        check_correlation_risk=Mock(return_value=(True, 0.0, [], "ok")),
        check_beta_exposure=Mock(return_value=(True, 1.0, "ok")),
        get_allocation_budget_view=Mock(return_value=budget_view),
        get_swing_runner_symbols=Mock(return_value=set(swing_symbols)),
    )
    ns._tranche_risk_gate = (
        lambda symbol, q, pr, **kw: SmartTradingBot._tranche_risk_gate(
            ns, symbol, q, pr, **kw
        )
    )
    return ns


def _analysis(signal="SELL", symbol="AMD", price=100.0):
    return {
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "signal_strength": "MEDIUM",
        "sma_fast": 101.0,
        "sma_slow": 99.0,
        "rsi": 25.0 if signal == "BUY" else 75.0,
        "timestamp": "2026-11-09T00:00:00+00:00",
    }


def _sell_bot(*, broker_qty=10.0, own_qty=0.0, swing_symbols=(), trade_amount=200.0):
    """Stub bot exercising the REAL execute_trade SELL path."""
    trading_client = Mock()
    trading_client.get_account.return_value = SimpleNamespace(
        cash=50_000.0, portfolio_value=100_000.0
    )
    trading_client.get_open_position.return_value = SimpleNamespace(
        qty=str(broker_qty)
    )
    trading_client.submit_order.return_value = SimpleNamespace(id="paper-order-1")

    bot = SimpleNamespace(
        trading_client=trading_client,
        trade_amount=trade_amount,
        db=Mock(),
        session_id=None,
        trades_executed=0,
        _pending_entry_tranches={},
        _portfolio_beta_cache=None,
        get_portfolio_total_value=Mock(return_value=100_000.0),
        has_pending_orders=Mock(return_value=False),
        is_in_cooldown=Mock(return_value=False),
        get_current_position_size=Mock(return_value=0.0),
        calculate_position_size=Mock(return_value=2),
        check_position_limits=Mock(return_value=(True, 2, 0.2)),
        check_sector_concentration=Mock(return_value=(True, 0.0, 0.2, "ok")),
        check_correlation_risk=Mock(return_value=(True, 0.0, None, "ok")),
        check_beta_exposure=Mock(return_value=(True, 1.0, "ok")),
        get_allocation_budget_view=Mock(return_value=_budget_view()),
        get_swing_runner_symbols=Mock(return_value=set(swing_symbols)),
        _ledger_own_net_qty=Mock(return_value={"AMD": own_qty} if own_qty else {}),
        send_trade_notification=Mock(),
        invalidate_sector_cache=Mock(),
        mark_recent_trade=Mock(),
    )
    bot.db.is_available.return_value = False
    # Bind the REAL ownership gate (it composes get_swing_runner_symbols +
    # _ledger_own_net_qty) so the test exercises production logic.
    bot._is_foreign_or_unproven_position = (
        lambda symbol: SmartTradingBot._is_foreign_or_unproven_position(
            bot, symbol
        )
    )
    return bot


def _execute(bot, analysis):
    return SmartTradingBot.execute_trade(bot, analysis)


# --------------------------------------------------------------------------- #
# A. Tranche / continuation BUY guards
# --------------------------------------------------------------------------- #


class TestTrancheBudgetGuard:
    def test_a1_budget_cap_reduces_tranche_quantity(self):
        """max_buy_cost = $250 with a $100 price caps 5 shares to 2."""
        bot = _tranche_bot(
            budget_view=_budget_view(max_buy_cost=250.0), qty=5, price=100.0
        )
        allowed, qty, reason = bot._tranche_risk_gate("TEST", 5, 100.0)
        assert allowed is True
        assert qty == 2, "the tranche quantity must be capped to the budget"
        assert reason == "passed"

    def test_a1b_budget_smaller_than_one_share_blocks_tranche(self):
        bot = _tranche_bot(
            budget_view=_budget_view(max_buy_cost=50.0), qty=5, price=100.0
        )
        allowed, qty, reason = bot._tranche_risk_gate("TEST", 5, 100.0)
        assert allowed is False
        assert qty == 0
        assert "allocation budget" in reason
        assert "one share" in reason

    def test_a2_cash_floor_breach_blocks_tranche(self):
        """A budget view whose cash headroom is fine but whose cash floor
        would be broken must block the tranche."""
        # cash = 10_500, floor = 10_000 -> a $700 BUY is allowed, $1_200 is not.
        view = _budget_view(
            cash=10_500.0, cash_floor=10_000.0, max_buy_cost=40_000.0
        )
        bot = _tranche_bot(budget_view=view, qty=12, price=100.0)
        allowed, qty, reason = bot._tranche_risk_gate("TEST", 12, 100.0)
        assert allowed is False
        assert qty == 0
        assert "cash floor" in reason

    def test_a3_unavailable_budget_view_fails_closed(self):
        """An unreadable account must BLOCK the tranche, not authorize it."""
        bot = _tranche_bot(budget_view=None, qty=5, price=100.0)
        allowed, qty, reason = bot._tranche_risk_gate("TEST", 5, 100.0)
        assert allowed is False
        assert qty == 0
        assert "fail-closed" in reason
        assert "unverifiable" in reason

    def test_a4_swing_symbol_blocks_tranche_buy(self):
        bot = _tranche_bot(swing_symbols={"AMD"}, qty=5, price=100.0)
        allowed, qty, reason = bot._tranche_risk_gate("AMD", 5, 100.0)
        assert allowed is False
        assert qty == 0
        assert "swing-runner guard" in reason

    def test_a4b_non_swing_symbol_is_not_blocked(self):
        bot = _tranche_bot(swing_symbols={"AMD"}, qty=5, price=100.0)
        allowed, qty, reason = bot._tranche_risk_gate("TEST", 5, 100.0)
        assert allowed is True
        assert qty == 5

    def test_a5_swing_read_failure_does_not_block_tranche(self):
        """Attribution evidence degrades to fewer sources — a failed read
        must never block a legitimate tranche."""
        bot = _tranche_bot(qty=5, price=100.0)
        bot.get_swing_runner_symbols = Mock(
            side_effect=RuntimeError("runner db unreadable")
        )
        allowed, qty, reason = bot._tranche_risk_gate("TEST", 5, 100.0)
        assert allowed is True
        assert qty == 5

    def test_a6_gate_never_raises(self):
        """Any unexpected error fails CLOSED (a broker order is
        irreversible)."""
        bot = _tranche_bot(qty=5, price=100.0)
        bot.get_allocation_budget_view = Mock(
            side_effect=RuntimeError("boom")
        )
        allowed, qty, reason = bot._tranche_risk_gate("TEST", 5, 100.0)
        assert allowed is False
        assert qty == 0


# --------------------------------------------------------------------------- #
# B. SELL ownership guard
# --------------------------------------------------------------------------- #


class TestSellOwnershipGuard:
    def test_b1_swing_owned_position_is_never_sold(self):
        """AMD is held by the external swing runner — the main bot must
        refuse to sell it."""
        bot = _sell_bot(broker_qty=10.0, own_qty=10.0, swing_symbols={"AMD"})
        assert _execute(bot, _analysis("SELL", "AMD")) is False
        bot.trading_client.submit_order.assert_not_called()

    def test_b2_unproven_position_is_never_sold(self):
        """No ledger-proven own quantity -> refuse to sell."""
        bot = _sell_bot(broker_qty=10.0, own_qty=0.0, swing_symbols=set())
        assert _execute(bot, _analysis("SELL", "AMD")) is False
        bot.trading_client.submit_order.assert_not_called()

    def test_b3_sell_quantity_is_capped_to_ledger_proven_qty(self):
        """Broker reports 10 shares; the ledger proves only 4 -> sell 4.

        trade_amount is set so the DESIRED quantity (10) exceeds the
        ledger-proven quantity (4) — i.e. the cap is what binds.
        """
        bot = _sell_bot(
            broker_qty=10.0, own_qty=4.0, swing_symbols=set(), trade_amount=1000.0
        )
        assert _execute(bot, _analysis("SELL", "AMD")) is True
        order = bot.trading_client.submit_order.call_args.kwargs["order_data"]
        assert order.qty == 4, "the SELL must be capped to the proven own qty"
        assert order.side.value == "sell"

    def test_b3b_position_sell_qty_is_also_capped(self):
        bot = _sell_bot(
            broker_qty=10.0, own_qty=3.0, swing_symbols=set(), trade_amount=1000.0
        )
        analysis = _analysis("SELL", "AMD")
        analysis["position_sell_qty"] = 9
        assert _execute(bot, analysis) is True
        order = bot.trading_client.submit_order.call_args.kwargs["order_data"]
        assert order.qty == 3

    def test_b4_proven_own_qty_above_broker_qty_sells_broker_qty(self):
        """The cap is min(broker qty, own qty) — never more than the
        broker reports."""
        bot = _sell_bot(
            broker_qty=5.0, own_qty=25.0, swing_symbols=set(), trade_amount=1000.0
        )
        assert _execute(bot, _analysis("SELL", "AMD")) is True
        order = bot.trading_client.submit_order.call_args.kwargs["order_data"]
        assert order.qty == 5


# --------------------------------------------------------------------------- #
# C. OBS-001 check order / snapshot fidelity
# --------------------------------------------------------------------------- #


class TestObsCheckOrder:
    def test_c1_swing_symbol_check_sits_before_budget_check(self):
        from src.core.smart_bot import OBS_001_EXECUTION_CHECK_ORDER
        order = list(OBS_001_EXECUTION_CHECK_ORDER)
        assert "swing_symbol_check" in order
        assert order.index("swing_symbol_check") == (
            order.index("position_concentration_check") + 1
        )
        assert order.index("allocation_budget_check") == (
            order.index("swing_symbol_check") + 1
        )

    def test_c2_ownership_check_is_sell_only(self):
        from src.core.smart_bot import (
            OBS_001_EXECUTION_CHECK_ORDER,
            OBS_001_SELL_OWNERSHIP_CHECK,
        )
        assert OBS_001_SELL_OWNERSHIP_CHECK == "position_ownership_check"
        # The ownership check is NOT part of the BUY check order.
        assert OBS_001_SELL_OWNERSHIP_CHECK not in OBS_001_EXECUTION_CHECK_ORDER

    def test_c3_sell_signal_marks_swing_and_budget_checks_not_run(self):
        from src.core.smart_bot import (
            _obs_001_begin_attempt,
            _obs_001_finalize_attempt,
        )
        bot = _sell_bot(broker_qty=10.0, own_qty=10.0, swing_symbols={"AMD"})
        _obs_001_begin_attempt("AMD", "SELL")
        _execute(bot, _analysis("SELL", "AMD"))
        checks = _obs_001_finalize_attempt(False)
        by_name = {c["name"]: c for c in checks}
        assert by_name["position_ownership_check"]["applied"] is True
        assert by_name["position_ownership_check"]["passed"] is False
        for buy_only in (
            "swing_symbol_check",
            "allocation_budget_check",
            "sector_concentration_check",
        ):
            assert by_name[buy_only]["applied"] is False
            assert "BUY only" in by_name[buy_only]["reason"]

    def test_c4_buy_path_records_the_swing_check(self):
        from src.core.smart_bot import (
            _obs_001_begin_attempt,
            _obs_001_finalize_attempt,
        )
        bot = _sell_bot(broker_qty=0.0, own_qty=0.0, swing_symbols=set())
        bot.trading_client.get_open_position.return_value = None
        _obs_001_begin_attempt("AMD", "BUY")
        _execute(bot, _analysis("BUY", "AMD"))
        checks = _obs_001_finalize_attempt(True)
        by_name = {c["name"]: c for c in checks}
        assert by_name["swing_symbol_check"]["applied"] is True
        assert by_name["swing_symbol_check"]["passed"] is True


# --------------------------------------------------------------------------- #
# D. Initial BUY path — swing symbol blocking
# --------------------------------------------------------------------------- #


class TestInitialBuySwingGuard:
    def test_d1_buy_is_blocked_for_a_swing_symbol(self):
        bot = _sell_bot(broker_qty=0.0, own_qty=0.0, swing_symbols={"AMD"})
        bot.trading_client.get_open_position.return_value = None
        assert _execute(bot, _analysis("BUY", "AMD")) is False
        bot.trading_client.submit_order.assert_not_called()

    def test_d2_buy_proceeds_for_a_non_swing_symbol(self):
        bot = _sell_bot(broker_qty=0.0, own_qty=0.0, swing_symbols={"AMD"})
        bot.trading_client.get_open_position.return_value = None
        assert _execute(bot, _analysis("BUY", "TEST")) is True
        bot.trading_client.submit_order.assert_called_once()

    def test_d3_swing_read_failure_does_not_block_the_initial_buy(self):
        bot = _sell_bot(broker_qty=0.0, own_qty=0.0, swing_symbols=set())
        bot.trading_client.get_open_position.return_value = None
        bot.get_swing_runner_symbols = Mock(
            side_effect=RuntimeError("order read failed")
        )
        assert _execute(bot, _analysis("BUY", "TEST")) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# --------------------------------------------------------------------------- #
# D. Portfolio-management SELL sizing (R2 remediation)
#
# R2: the two portfolio-management SELL paths (concentration reduction and
# stop-loss) sized their orders from the RAW broker quantity. The broker
# account is shared with the swing runners, so the raw broker quantity can
# include shares this bot does NOT own -> the bot could sell a foreign
# position. Both paths must now cap the SELL to min(broker_qty, own_qty)
# where own_qty is ledger-proven, and FAIL CLOSED when ownership cannot be
# proven.
# --------------------------------------------------------------------------- #


def _portfolio_bot(*, broker_qty=40.0, own_qty=0.0, swing_symbols=(),
                    broker_read_raises=False, broker_symbol_absent=False):
    """Namespace exercising the REAL portfolio-management SELL paths.

    `broker_read_raises` makes the broker position read raise (the broker
    bound is unavailable). `broker_symbol_absent` makes the broker report
    positions that do NOT include the symbol under test (also: no broker
    bound for that symbol).
    """
    trading_client = Mock()
    trading_client.get_account.return_value = SimpleNamespace(
        cash=50_000.0, portfolio_value=100_000.0
    )
    trading_client.get_orders.return_value = []
    trading_client.submit_order.return_value = SimpleNamespace(
        id="paper-order-1", fill_price=None
    )
    trading_client.get_market_data.return_value = None
    if broker_read_raises:
        trading_client.get_all_positions.side_effect = RuntimeError(
            "alpaca positions read failed"
        )
    elif broker_symbol_absent:
        trading_client.get_all_positions.return_value = [
            SimpleNamespace(
                symbol="MSFT",
                qty="7",
                market_value=7_000.0,
                avg_entry_price=100.0,
                current_price=100.0,
            )
        ]
    else:
        trading_client.get_all_positions.return_value = [
            SimpleNamespace(
                symbol="AMD",
                qty=str(broker_qty),
                market_value=40_000.0,
                avg_entry_price=100.0,
                current_price=100.0,
            )
        ]

    bot = SimpleNamespace(
        trading_client=trading_client,
        db=Mock(),
        session_id=7,
        use_atr_stop_loss=False,
        stop_loss_pct=10.0,
        atr_stop_multiplier=2.0,
        get_allocation_budget_view=Mock(return_value=_budget_view()),
        get_swing_runner_symbols=Mock(return_value=set(swing_symbols)),
        _ledger_own_net_qty=Mock(
            return_value={"AMD": own_qty} if own_qty else {}
        ),
        has_active_stop_order=Mock(return_value=False),
        has_pending_orders=Mock(return_value=False),
        send_trade_notification=Mock(),
    )
    bot.db.is_available.return_value = False

    # Bind the REAL production methods so the test exercises them.
    for name in (
        "_is_foreign_or_unproven_position",
        "_proven_own_qty",
        "_sell_qty_cap",
        "_broker_position_qty",
        "_place_portfolio_order",
        "_place_stop_loss_order",
    ):
        setattr(
            bot,
            name,
            lambda symbol, *a, _n=name, **kw: getattr(
                SmartTradingBot, _n
            )(bot, symbol, *a, **kw),
        )
    return bot


def _portfolio_analysis(*, qty=40.0, market_value=60_000.0, plpc=80.0):
    return {
        "positions": [
            {
                "symbol": "AMD",
                "qty": qty,
                "market_value": market_value,
                "unrealized_plpc": plpc,
                "current_price": 100.0,
            }
        ],
        "total_value": 100_000.0,
        "cash_available": 40_000.0,
        "concentration_risk": 60.0,
    }


def _submitted_qty(bot):
    return bot.trading_client.submit_order.call_args.kwargs["order_data"].qty


class TestPortfolioManagementSellCap:
    """R2: portfolio-management SELLs are capped to ledger-proven own qty."""

    # -- concentration reduction ------------------------------------------- #

    def test_d1_concentration_reduction_capped_to_ledger_proven_qty(self):
        """Broker reports 40 shares, the ledger proves 4 -> reduce by 4.

        Without the cap this would sell int(40 * 0.25) = 10 shares, i.e.
        6 shares that this bot cannot prove it owns.
        """
        bot = _portfolio_bot(broker_qty=40.0, own_qty=4.0)
        SmartTradingBot.execute_portfolio_actions(
            bot, _portfolio_analysis(qty=40.0)
        )
        bot.trading_client.submit_order.assert_called_once()
        assert _submitted_qty(bot) == 4, (
            "the concentration reduction must be capped to the "
            "ledger-proven own quantity"
        )

    def test_d2_concentration_reduction_keeps_25pct_rule_when_own_is_enough(self):
        """When the ledger proves the whole position, the existing 25% rule
        still governs (no behaviour change for fully-owned positions)."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=40.0)
        SmartTradingBot.execute_portfolio_actions(
            bot, _portfolio_analysis(qty=40.0)
        )
        bot.trading_client.submit_order.assert_called_once()
        assert _submitted_qty(bot) == 10  # int(40 * 0.25)

    def test_d3_concentration_reduction_skipped_when_ownership_unproven(self):
        """No ledger-proven own quantity -> no SELL at all."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=0.0)
        SmartTradingBot.execute_portfolio_actions(
            bot, _portfolio_analysis(qty=40.0)
        )
        bot.trading_client.submit_order.assert_not_called()

    def test_d4_portfolio_sell_refused_without_ledger_proof(self):
        """_place_portfolio_order fails closed when ownership is unproven."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=0.0)
        result = SmartTradingBot._place_portfolio_order(
            bot, "AMD", 10, "sell", "test"
        )
        assert result is False
        bot.trading_client.submit_order.assert_not_called()

    def test_d5_portfolio_sell_capped_to_ledger_proven_qty(self):
        """A direct portfolio SELL is capped to min(broker_qty, own_qty)."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=4.0)
        result = SmartTradingBot._place_portfolio_order(
            bot, "AMD", 10, "sell", "test"
        )
        assert result is True
        assert _submitted_qty(bot) == 4

    def test_d6_portfolio_sell_never_exceeds_broker_qty(self):
        """The cap is min(broker_qty, own_qty) — never more than the broker
        reports, even if the ledger claims more."""
        bot = _portfolio_bot(broker_qty=5.0, own_qty=25.0)
        result = SmartTradingBot._place_portfolio_order(
            bot, "AMD", 10, "sell", "test"
        )
        assert result is True
        assert _submitted_qty(bot) == 5

    # -- stop-loss ---------------------------------------------------------- #

    def test_d7_stop_loss_capped_to_ledger_proven_qty(self):
        """A stop-loss is a SELL: it may cover at most the proven own qty."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=4.0)
        result = SmartTradingBot._place_stop_loss_order(bot, "AMD", 40, 90.0)
        assert result is True
        bot.trading_client.submit_order.assert_called_once()
        assert _submitted_qty(bot) == 4

    def test_d8_stop_loss_refused_without_ledger_proof(self):
        """No ledger-proven own quantity -> refuse to place the stop."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=0.0)
        result = SmartTradingBot._place_stop_loss_order(bot, "AMD", 40, 90.0)
        assert result is False
        bot.trading_client.submit_order.assert_not_called()

    def test_d9_stop_loss_never_exceeds_broker_qty(self):
        bot = _portfolio_bot(broker_qty=6.0, own_qty=25.0)
        result = SmartTradingBot._place_stop_loss_order(bot, "AMD", 6, 90.0)
        assert result is True
        assert _submitted_qty(bot) == 6

    def test_d10_stop_loss_from_portfolio_actions_capped(self):
        """End-to-end through execute_portfolio_actions: the stop covers only
        the ledger-proven quantity."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=4.0)
        SmartTradingBot.execute_portfolio_actions(
            bot,
            _portfolio_analysis(qty=40.0, market_value=60_000.0, plpc=-20.0),
        )
        bot.trading_client.submit_order.assert_called_once()
        assert _submitted_qty(bot) == 4

    def test_d11_stop_loss_from_portfolio_actions_skipped_when_unproven(self):
        bot = _portfolio_bot(broker_qty=40.0, own_qty=0.0)
        SmartTradingBot.execute_portfolio_actions(
            bot,
            _portfolio_analysis(qty=40.0, market_value=60_000.0, plpc=-20.0),
        )
        bot.trading_client.submit_order.assert_not_called()

    # -- helper contract ---------------------------------------------------- #

    def test_d12_sell_qty_cap_is_min_of_broker_and_own(self):
        bot = _portfolio_bot(broker_qty=40.0, own_qty=4.0)
        assert bot._sell_qty_cap("AMD", 40) == 4
        assert bot._sell_qty_cap("AMD", 2) == 2

    def test_d13_sell_qty_cap_is_zero_when_ownership_unproven(self):
        bot = _portfolio_bot(broker_qty=40.0, own_qty=0.0)
        assert bot._sell_qty_cap("AMD", 40) == 0

    def test_d14_proven_own_qty_fails_closed_on_ledger_error(self):
        """A failed ledger read proves nothing -> 0.0 (fail closed)."""
        bot = _portfolio_bot(broker_qty=40.0, own_qty=4.0)
        bot._ledger_own_net_qty = Mock(side_effect=RuntimeError("db down"))
        assert bot._proven_own_qty("AMD") == 0.0
        assert bot._sell_qty_cap("AMD", 40) == 0

    # -- broker bound unavailable ------------------------------------------- #

    def test_d15_portfolio_sell_refused_when_broker_read_raises(self):
        """An unreadable broker position is an UNAVAILABLE bound.

        The requested amount is not a broker fact, so it must never be
        used as the broker-side bound. The SELL is refused and NO order
        is submitted.
        """
        bot = _portfolio_bot(
            broker_qty=40.0, own_qty=4.0, broker_read_raises=True
        )
        result = SmartTradingBot._place_portfolio_order(
            bot, "AMD", 10, "sell", "test"
        )
        assert result is False
        bot.trading_client.submit_order.assert_not_called()

    def test_d16_portfolio_sell_refused_when_symbol_absent_from_broker(self):
        """A symbol the broker does not report has no broker-side bound.

        The SELL is refused and NO order is submitted.
        """
        bot = _portfolio_bot(
            broker_qty=40.0, own_qty=4.0, broker_symbol_absent=True
        )
        result = SmartTradingBot._place_portfolio_order(
            bot, "AMD", 10, "sell", "test"
        )
        assert result is False
        bot.trading_client.submit_order.assert_not_called()
