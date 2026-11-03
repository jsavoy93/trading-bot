"""Report tests for STRAT-SWING-PAPER report_pnl — no network.

Covers the PR #135 review item 9: fills quantified via filled_qty /
filled_avg_price (orders have no `trades` field), partially-filled
canceled orders included, -stop and -stop-p labels parsed, open positions
attributed by labeled fills only (never by last buyer), and realized P&L
FIFO math.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "experiments" / "swing_paper"
sys.path.insert(0, str(HERE))

import report_pnl  # noqa: E402


def order(coid, symbol, side, qty, filled_qty, filled_px, status="filled",
          filled_at="2026-11-03T15:00:00Z"):
    return {
        "client_order_id": coid, "symbol": symbol, "side": side,
        "qty": str(qty), "filled_qty": str(filled_qty),
        "filled_avg_price": str(filled_px), "status": status,
        "submitted_at": filled_at, "filled_at": filled_at,
    }


def _collect(monkeypatch, orders, positions=(), last=100.0):
    monkeypatch.setattr(report_pnl, "fetch_positions", lambda: list(positions))
    monkeypatch.setattr(report_pnl, "fetch_last_quote", lambda s: last)
    return report_pnl.collect_from_orders(orders)


def test_realized_pnl_uses_filled_qty_and_avg_price(monkeypatch):
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 100, 100, 50.0),
        order("rsi2-AMD-20261102-1-stop-p", "AMD", "sell", 50, 50, 60.0),
        order("rsi2-AMD-20261103-1-stop", "AMD", "sell", 50, 50, 45.0),
    ]
    rep = _collect(monkeypatch, orders)
    # +50*(60-50) + 50*(45-50) = 500 - 250 = 250
    assert rep["realized"]["rsi2"] == 250.0
    assert len(rep["trades"]["rsi2"]) == 2
    assert rep["trades"]["rsi2"][0]["sell_id"].endswith("-stop-p")


def test_orders_without_trades_field_are_fine(monkeypatch):
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 10, 10, 50.0),
        order("rsi2-AMD-20261102-1-stop", "AMD", "sell", 10, 10, 55.0),
    ]
    assert all("trades" not in o for o in orders)
    rep = _collect(monkeypatch, orders)
    assert rep["realized"]["rsi2"] == 50.0


def test_partially_filled_canceled_order_included(monkeypatch):
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 100, 100, 50.0),
        order("rsi2-AMD-20261102-1-stop", "AMD", "sell", 100, 40, 60.0,
              status="canceled"),
    ]
    rep = _collect(monkeypatch, orders)
    assert rep["realized"]["rsi2"] == 400.0  # 40 * (60-50)
    assert len(rep["trades"]["rsi2"]) == 1


def test_unfilled_orders_excluded(monkeypatch):
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 100, 0, 0, status="canceled"),
        order("rsi2-AMD-20261102-1", "AMD", "buy", 100, 0, 0, status="expired"),
    ]
    rep = _collect(monkeypatch, orders)
    assert rep["realized"]["rsi2"] == 0.0
    assert rep["trades"]["rsi2"] == []


def test_unlabeled_orders_ignored(monkeypatch):
    orders = [
        order("sl_short-AMD-20261101-1", "AMD", "buy", 100, 100, 50.0),
        order("sl_short-AMD-20261102-1", "AMD", "sell", 100, 100, 60.0),
    ]
    rep = _collect(monkeypatch, orders)
    assert rep["realized"]["rsi2"] == 0.0
    assert rep["open"]["rsi2"] == []


def test_open_position_attributed_by_labeled_fills_not_last_buyer(monkeypatch):
    # strategy-rsi2 labeled buy of 60 shares; another unlabeled buyer
    # (e.g. main bot) bought later — attribution must stay with rsi2's
    # labeled net, not the last buyer
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 60, 60, 50.0),
        order("bot-internal-77", "AMD", "buy", 40, 40, 70.0,
              filled_at="2026-11-02T15:00:00Z"),
    ]
    positions = [{"symbol": "AMD", "qty": "100", "avg_entry_price": "58",
                  "current_price": "60"}]
    rep = _collect(monkeypatch, orders, positions=positions, last=60.0)
    # position is attributed only via labeled fills; qty reported is the
    # account position (100) but attribution requires a labeled net>0
    assert len(rep["open"]["rsi2"]) == 1
    assert rep["open"]["rsi2"][0]["symbol"] == "AMD"


def test_position_with_no_labeled_fills_not_attributed(monkeypatch):
    orders = [
        order("bot-internal-77", "AMD", "buy", 100, 100, 50.0),
    ]
    positions = [{"symbol": "AMD", "qty": "100", "avg_entry_price": "50",
                  "current_price": "55"}]
    rep = _collect(monkeypatch, orders, positions=positions, last=55.0)
    assert rep["open"]["rsi2"] == []
    assert rep["unrealized"]["rsi2"] == 0.0


def test_labeled_sells_reduce_net_attribution(monkeypatch):
    # rsi2 bought 100 and fully sold via labeled -stop order -> position
    # no longer attributed to rsi2 even though shares remain (other buyer)
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 100, 100, 50.0),
        order("rsi2-AMD-20261105-1-stop", "AMD", "sell", 100, 100, 55.0),
        order("bot-internal-99", "AMD", "buy", 100, 100, 56.0),
    ]
    positions = [{"symbol": "AMD", "qty": "100", "avg_entry_price": "56",
                  "current_price": "60"}]
    rep = _collect(monkeypatch, orders, positions=positions, last=60.0)
    assert rep["open"]["rsi2"] == []


def test_stop_and_partial_labels_parsed_for_attribution(monkeypatch):
    from rules import parse_label
    assert parse_label("rsi2-NVDA-20261103-7-stop")["kind"] == "stop"
    assert parse_label("rsi2-NVDA-20261103-7-stop-p")["kind"] == "partial"
    assert parse_label("rsi2-NVDA-20261103-7")["kind"] == "entry"


# ------------------------------------------- round-2 fix I (pagination,
# activities fields, labeled open qty)
class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_pagination_no_double_count_same_day_full_pages(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    # FIX I(1): date-granularity windows could re-fetch a whole day. With
    # a full page sharing one timestamp the fetch must still terminate and
    # never return duplicate order ids.
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params.get("after"))
        if len(calls) == 1:
            page = [{"id": f"o{i}", "submitted_at": "2026-11-03T15:00:00Z",
                     "created_at": "2026-11-03T15:00:00Z"}
                    for i in range(report_pnl.PAGE)]
        elif len(calls) == 2:
            # server re-sends the same ids (old bug double-counted these)
            page = [{"id": f"o{i}", "submitted_at": "2026-11-03T15:00:00Z",
                     "created_at": "2026-11-03T15:00:00Z"}
                    for i in range(3)]
        else:
            page = []
        return FakeResp(page)

    monkeypatch.setattr(report_pnl.requests, "get", fake_get)
    out = report_pnl.fetch_all_orders(None)
    ids = [o["id"] for o in out]
    assert len(ids) == len(set(ids)) == report_pnl.PAGE  # no double count
    assert len(calls) <= 4  # terminated


def test_pagination_terminates_when_window_cannot_advance(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    # adversarial server always returns a full page whose last timestamp
    # equals the window start: the fetch must break, not loop forever.
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        after = params.get("after") or "2026-11-03T15:00:00Z"
        return FakeResp([{"id": f"x{len(calls)}-{i}",
                          "submitted_at": after}
                         for i in range(report_pnl.PAGE)])

    monkeypatch.setattr(report_pnl.requests, "get", fake_get)
    out = report_pnl.fetch_all_orders("2026-11-03T15:00:00Z")
    assert len(calls) == 1  # one fetch, immediate guard-break — no loop


def test_activities_cross_check_uses_real_feed_fields(monkeypatch):
    # FIX I(2): FILL activities carry symbol/side/qty/price/order_id —
    # NOT pl / client_order_id. Attribution goes through order_id ->
    # client_order_id and P&L is computed FIFO from the legs.
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 100, 100, 50.0),
        order("rsi2-AMD-20261102-1-stop", "AMD", "sell", 100, 100, 60.0),
    ]
    id_map = {f"oid-{i}": o["client_order_id"]
              for i, o in enumerate(orders)}
    acts = [
        {"activity_type": "FILL", "order_id": "oid-0", "symbol": "AMD",
         "side": "buy", "qty": "100", "price": "50.0",
         "transaction_time": "2026-11-01T15:00:00Z"},  # no pl, no coid
        {"activity_type": "FILL", "order_id": "oid-1", "symbol": "AMD",
         "side": "sell", "qty": "100", "price": "60.0",
         "transaction_time": "2026-11-02T15:00:00Z"},
        {"activity_type": "FILL", "order_id": "oid-9", "symbol": "TSLA",
         "side": "buy", "qty": "10", "price": "200.0",
         "transaction_time": "2026-11-02T15:05:00Z"},  # unlabeled order
    ]
    rep = report_pnl.collect_from_activities(acts, id_map)
    assert rep["realized"]["rsi2"] == 1000.0  # 100 * (60 - 50)
    assert len(rep["trades"]["rsi2"]) == 1


def test_open_pnl_uses_labeled_qty_not_whole_account(monkeypatch):
    # FIX I(3): only the RSI(2)-labeled net qty (and its labeled average
    # entry) drives open P&L, not the whole-account position.
    orders = [
        order("rsi2-AMD-20261101-1", "AMD", "buy", 60, 60, 50.0),
        order("bot-internal-77", "AMD", "buy", 40, 40, 70.0,
              filled_at="2026-11-02T15:00:00Z"),
    ]
    positions = [{"symbol": "AMD", "qty": "100", "avg_entry_price": "58",
                  "current_price": "60"}]
    rep = _collect(monkeypatch, orders, positions=positions, last=60.0)
    open_pos = rep["open"]["rsi2"]
    assert len(open_pos) == 1
    assert open_pos[0]["qty"] == 60          # labeled qty, not 100
    assert open_pos[0]["avg_entry"] == 50.0  # labeled avg, not 58
    assert rep["unrealized"]["rsi2"] == 60 * (60.0 - 50.0)
