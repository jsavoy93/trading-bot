from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.smart_bot import OrderSide, SmartTradingBot


def _analysis(signal: str) -> dict:
    return {
        "symbol": "AAPL",
        "signal": signal,
        "price": 100.0,
        "signal_strength": "MEDIUM",
        "sma_fast": 101.0,
        "sma_slow": 99.0,
        "rsi": 25.0 if signal == "BUY" else 75.0,
        "timestamp": "2026-08-03T00:00:00+00:00",
    }


def _bot(*, pending_order: bool = False, position_qty: float = 0.0):
    trading_client = Mock()
    trading_client.get_account.return_value = SimpleNamespace(cash="10000")
    trading_client.get_open_position.return_value = (
        SimpleNamespace(qty=str(position_qty)) if position_qty else None
    )
    trading_client.submit_order.return_value = SimpleNamespace(id="paper-order-1")

    bot = SimpleNamespace(
        trading_client=trading_client,
        trade_amount=200.0,
        db=Mock(),
        session_id=None,
        trades_executed=0,
        _pending_entry_tranches={},
        _portfolio_beta_cache=None,
        get_portfolio_total_value=Mock(return_value=100_000.0),
        has_pending_orders=Mock(return_value=pending_order),
        is_in_cooldown=Mock(return_value=False),
        get_current_position_size=Mock(return_value=0.0),
        calculate_position_size=Mock(return_value=2),
        check_position_limits=Mock(return_value=(True, 2, 0.2)),
        check_sector_concentration=Mock(return_value=(True, 0.0, 0.2, "ok")),
        check_correlation_risk=Mock(return_value=(True, 0.0, None, "ok")),
        check_beta_exposure=Mock(return_value=(True, 1.0, "ok")),
        send_trade_notification=Mock(),
        invalidate_sector_cache=Mock(),
        mark_recent_trade=Mock(),
    )
    bot.db.is_available.return_value = False
    return bot


def _execute(bot, signal: str) -> bool:
    return SmartTradingBot.execute_trade(bot, _analysis(signal))


def test_buy_signal_submits_buy_order_for_unowned_symbol() -> None:
    bot = _bot(position_qty=0)

    assert _execute(bot, "BUY") is True

    order = bot.trading_client.submit_order.call_args.kwargs["order_data"]
    assert order.symbol == "AAPL"
    assert order.qty == 2
    assert order.side == OrderSide.BUY


def test_sell_signal_submits_sell_order_for_owned_symbol() -> None:
    bot = _bot(position_qty=5)

    assert _execute(bot, "SELL") is True

    order = bot.trading_client.submit_order.call_args.kwargs["order_data"]
    assert order.symbol == "AAPL"
    assert order.qty == 2
    assert order.side == OrderSide.SELL


def test_sell_signal_is_rejected_for_unowned_symbol() -> None:
    bot = _bot(position_qty=0)

    assert _execute(bot, "SELL") is False
    bot.trading_client.submit_order.assert_not_called()


def test_hold_signal_never_submits_an_order() -> None:
    bot = _bot(position_qty=5)

    assert _execute(bot, "HOLD") is False
    bot.trading_client.submit_order.assert_not_called()


def test_unknown_signal_never_submits_an_order() -> None:
    bot = _bot(position_qty=5)

    assert _execute(bot, "REBALANCE") is False
    bot.trading_client.submit_order.assert_not_called()


@pytest.mark.parametrize("signal", ["BUY", "SELL"])
def test_pending_order_prevents_duplicate_submission(signal: str) -> None:
    bot = _bot(pending_order=True, position_qty=5)

    assert _execute(bot, signal) is False
    bot.trading_client.submit_order.assert_not_called()
