"""Unit tests for STRAT-SWING-PAPER rules: labels (with year), skip/overlap
rule (any held symbol / any open order / C4 same-day), selection caps, and
per-position sizing. Pure logic only — no network, no I/O."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "experiments" / "swing_paper"
sys.path.insert(0, str(HERE))

from rules import (ALPACA_CLIENT_ORDER_ID_MAX, RSI2,
                   apply_rsi2_daily_cap, make_client_order_id,
                   make_labels, make_partial_client_order_id,
                   make_stop_client_order_id, next_sequence, parse_label,
                   rsi2_slice_dollars, rsi2_skip_blocked, size_rsi2_position,
                   symbol_has_open_order, symbol_in_positions)


# ---------------------------------------------------------------- labels
class TestLabels:
    def test_format_includes_year(self):
        assert make_client_order_id("rsi2", "AMD", "20261103", 1) == \
            "rsi2-AMD-20261103-1"

    def test_stop_suffix(self):
        assert make_stop_client_order_id("rsi2", "AMD", "20261103", 2) == \
            "rsi2-AMD-20261103-2-stop"

    def test_partial_suffix_is_stop_p(self):
        assert make_partial_client_order_id("rsi2", "AMD", "20261103", 2) == \
            "rsi2-AMD-20261103-2-stop-p"

    def test_pair(self):
        entry, stop = make_labels(RSI2, "TSLA", "20260105", 2)
        assert entry == "rsi2-TSLA-20260105-2"
        assert stop == entry + "-stop"

    def test_dotted_symbol_supported(self):
        assert make_client_order_id("rsi2", "BRK.B", "20261103", 1) == \
            "rsi2-BRK.B-20261103-1"

    def test_symbol_normalized_uppercase(self):
        assert make_client_order_id("rsi2", "amd", "20261103", 1) == \
            "rsi2-AMD-20261103-1"

    def test_length_within_alpaca_limit(self):
        worst = make_partial_client_order_id("rsi2", "BRK.B", "20261103", 99999)
        assert len(worst) <= ALPACA_CLIENT_ORDER_ID_MAX

    @pytest.mark.parametrize("strategy,symbol,ymd,n", [
        ("c4", "AMD", "20261103", 1),          # C4 removed from this runner
        ("rsi2", "BAD-SYM", "20261103", 1),    # '-' would break parsing
        ("rsi2", "AMD", "1103", 1),            # no year
        ("rsi2", "AMD", "20261303", 1),        # bad month
        ("rsi2", "AMD", "20261103", 0),
        ("rsi2", "AMD", "20261103", -1),
    ])
    def test_invalid_inputs_raise(self, strategy, symbol, ymd, n):
        with pytest.raises(ValueError):
            make_client_order_id(strategy, symbol, ymd, n)

    def test_parse_roundtrip_entry_stop_partial(self):
        assert parse_label("rsi2-NVDA-20261103-7") == {
            "strategy": "rsi2", "symbol": "NVDA", "ymd": "20261103",
            "n": 7, "kind": "entry"}
        assert parse_label("rsi2-NVDA-20261103-7-stop")["kind"] == "stop"
        assert parse_label("rsi2-NVDA-20261103-7-stop-p")["kind"] == "partial"

    def test_parse_rejects_unknown_strategy(self):
        with pytest.raises(ValueError):
            parse_label("sl_short-AMD-20261103-1")

    def test_next_sequence_ignores_stop_and_partial_suffixes(self):
        labels = ["rsi2-AMD-20261103-1", "rsi2-AMD-20261103-1-stop",
                  "rsi2-AMD-20261103-1-stop-p", "rsi2-AMD-20261102-9"]
        assert next_sequence(labels, "rsi2", "AMD", "20261103") == 2

    def test_next_sequence_deterministic_same_day_rerun(self):
        # identical inputs -> identical next id (Alpaca dedupes duplicates)
        labels = ["rsi2-AMD-20261103-1"]
        a = make_client_order_id("rsi2", "AMD", "20261103",
                                 next_sequence(labels, "rsi2", "AMD", "20261103"))
        b = make_client_order_id("rsi2", "AMD", "20261103",
                                 next_sequence(labels, "rsi2", "AMD", "20261103"))
        assert a == b == "rsi2-AMD-20261103-2"


# ------------------------------------------------------- skip / overlap
class TestSkipRule:
    def test_blocked_when_held(self):
        assert symbol_in_positions([{"symbol": "AMD"}], "AMD")
        assert rsi2_skip_blocked([{"symbol": "AMD"}], [], "AMD") is True

    def test_blocked_when_c4_position_held_any_label(self):
        positions = [{"symbol": "AMD", "avg_entry_price": "100"}]
        assert rsi2_skip_blocked(positions, [], "AMD") is True

    def test_blocked_when_any_open_order_including_sl_short(self):
        orders = [{"symbol": "AMD", "side": "buy",
                   "client_order_id": "sl_short-AMD-20261103-1"}]
        assert symbol_has_open_order(orders, "AMD")
        assert rsi2_skip_blocked([], orders, "AMD") is True

    def test_blocked_when_own_pending_order(self):
        orders = [{"symbol": "NVDA", "side": "buy",
                   "client_order_id": "rsi2-NVDA-20261103-1"}]
        assert rsi2_skip_blocked([], orders, "NVDA") is True

    def test_blocked_when_c4_signaled_same_day(self):
        assert rsi2_skip_blocked([], [], "AMD", c4_signaled_symbols=["AMD"])

    def test_not_blocked_for_clean_symbol(self):
        orders = [{"symbol": "TSLA", "side": "buy",
                   "client_order_id": "sl_short-TSLA-20261103-1"}]
        assert rsi2_skip_blocked([], orders, "AMD") is False

    def test_dotted_symbol_overlap(self):
        orders = [{"symbol": "BRK.B", "side": "buy",
                   "client_order_id": "sl_short-BRK.B-20261103-1"}]
        assert rsi2_skip_blocked([], orders, "brk.b") is True


# ------------------------------------------------------------- caps
class TestCaps:
    def test_daily_cap_ranks_by_lowest_rsi2(self):
        cands = [{"symbol": "B", "rsi2": 5}, {"symbol": "A", "rsi2": 9},
                 {"symbol": "C", "rsi2": 1}, {"symbol": "D", "rsi2": 3},
                 {"symbol": "E", "rsi2": 2}]
        out = apply_rsi2_daily_cap(cands, cap=4)
        assert [c["symbol"] for c in out] == ["C", "E", "D", "B"]

    def test_tiebreak_symbol_asc(self):
        cands = [{"symbol": "Z", "rsi2": 5}, {"symbol": "A", "rsi2": 5}]
        assert [c["symbol"] for c in apply_rsi2_daily_cap(cands, 4)] == ["A", "Z"]


# ------------------------------------------------------------- sizing
class TestSizing:
    def test_first_pick_cannot_take_whole_slice(self):
        # 10% per-position cap: slice $100k, price $10 -> 1000 shares
        # whole-slice would be 10,000 shares; cap must bite first
        qty = size_rsi2_position(10.0, 100_000.0)
        assert qty == 1000

    def test_cap_configurable(self):
        assert size_rsi2_position(10.0, 100_000.0, per_position_cap_pct=0.20) == 2000

    def test_remaining_slice_bounds(self):
        # open notional eats the slice: $100k slice, $95k open -> $5k left,
        # per-position cap is $10k -> remaining wins
        qty = size_rsi2_position(10.0, 100_000.0, open_notional=95_000.0)
        assert qty == 500

    def test_max_open_positions_zero(self):
        assert size_rsi2_position(10.0, 100_000.0, open_positions=4,
                                  max_open_positions=4) == 0

    def test_slice_dollars(self):
        assert rsi2_slice_dollars(200_000.0, 0.30) == 60_000.0
