"""Unit tests for STRAT-SWING-PAPER per-strategy order tagging, overlap
rule, daily cap, and budget slices. Pure logic only — no network."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "experiments" / "swing_paper"
sys.path.insert(0, str(HERE))

from rules import (ALPACA_CLIENT_ORDER_ID_MAX, C4, RSI2, BudgetSlices,
                   apply_rsi2_daily_cap, label_prefix_for_day,
                   make_client_order_id, make_labels,
                   make_stop_client_order_id, next_sequence, parse_label,
                   rsi2_overlap_blocked)


# ---------------------------------------------------------------- labels
class TestLabels:
    def test_format_examples(self):
        assert make_client_order_id("c4", "AMD", "1102", 1) == "c4-AMD-1102-1"
        assert make_client_order_id("rsi2", "NVDA", "1102", 1) == "rsi2-NVDA-1102-1"

    def test_stop_suffix(self):
        assert make_stop_client_order_id("c4", "AMD", "1102", 1) == "c4-AMD-1102-1-stop"
        assert make_stop_client_order_id("rsi2", "NVDA", "1102", 3) == "rsi2-NVDA-1102-3-stop"

    def test_pair(self):
        entry, stop = make_labels(RSI2, "TSLA", "0105", 2)
        assert entry == "rsi2-TSLA-0105-2"
        assert stop == entry + "-stop"

    def test_symbol_normalized_uppercase(self):
        assert make_client_order_id("c4", "amd", "1102", 1) == "c4-AMD-1102-1"

    def test_length_within_alpaca_limit(self):
        worst = make_stop_client_order_id("rsi2", "GOOGL", "1102", 99999)
        assert len(worst) <= ALPACA_CLIENT_ORDER_ID_MAX

    @pytest.mark.parametrize("strategy,symbol,mmdd,n", [
        ("bad", "AMD", "1102", 1), ("c4", "TOOLONG1", "1102", 1),
        ("c4", "AMD", "110", 1), ("c4", "AMD", "1320", 1),
        ("c4", "AMD", "1102", 0), ("c4", "AMD", "1102", -1),
    ])
    def test_invalid_inputs_raise(self, strategy, symbol, mmdd, n):
        with pytest.raises(ValueError):
            make_client_order_id(strategy, symbol, mmdd, n)

    def test_parse_roundtrip(self):
        parsed = parse_label("rsi2-NVDA-1102-7-stop")
        assert parsed == {"strategy": "rsi2", "symbol": "NVDA",
                          "mmdd": "1102", "n": 7, "is_stop": True}
        assert parse_label("c4-AMD-1102-1")["is_stop"] is False

    def test_parse_rejects_unknown_strategy(self):
        with pytest.raises(ValueError):
            parse_label("zzz-AMD-1102-1")


class TestSequence:
    def test_next_sequence_empty(self):
        assert next_sequence([], "c4", "AMD", "1102") == 1

    def test_next_sequence_counts_entries_and_stops(self):
        labels = ["c4-AMD-1102-1", "c4-AMD-1102-1-stop", "c4-AMD-1102-2",
                  "c4-AMD-1103-5", "rsi2-AMD-1102-9", "c4-MSFT-1102-4"]
        assert next_sequence(labels, "c4", "AMD", "1102") == 3

    def test_next_sequence_ignores_other_days_symbols(self):
        labels = ["c4-AMD-1103-7"]
        assert next_sequence(labels, "c4", "AMD", "1102") == 1


# --------------------------------------------------------------- overlap
class TestOverlap:
    POSITIONS = ["AMD", "NVDA"]

    def test_blocked_by_c4_position(self):
        assert rsi2_overlap_blocked(self.POSITIONS, [], "amd", "1102") is True

    def test_blocked_by_c4_order_same_day(self):
        orders = [{"client_order_id": "c4-AMD-1102-1"},
                  {"client_order_id": "c4-AMD-1102-1-stop"}]
        assert rsi2_overlap_blocked([], orders, "AMD", "1102") is True

    def test_not_blocked_by_c4_order_other_day(self):
        orders = [{"client_order_id": "c4-AMD-1103-1"}]
        assert rsi2_overlap_blocked([], orders, "AMD", "1102") is False

    def test_not_blocked_by_rsi2_or_foreign_labels(self):
        orders = [{"client_order_id": "rsi2-AMD-1102-1"},
                  {"client_order_id": "c4-NVDA-1102-1"}]
        assert rsi2_overlap_blocked([], orders, "AMD", "1102") is False

    def test_no_overlap_when_empty(self):
        assert rsi2_overlap_blocked([], [], "AMD", "1102") is False


# --------------------------------------------------------------- daily cap
class TestDailyCap:
    def test_selects_lowest_rsi2(self):
        cands = [{"symbol": "A", "rsi2": 8.0}, {"symbol": "B", "rsi2": 5.0},
                 {"symbol": "C", "rsi2": 6.5}]
        assert [c["symbol"] for c in apply_rsi2_daily_cap(cands)] == ["B", "C", "A"]

    def test_cap_limit(self):
        cands = [{"symbol": f"S{i}", "rsi2": float(i)} for i in range(10)]
        out = apply_rsi2_daily_cap(cands, cap=4)
        assert len(out) == 4
        assert [c["symbol"] for c in out] == ["S0", "S1", "S2", "S3"]

    def test_default_cap_is_four(self):
        cands = [{"symbol": f"S{i}", "rsi2": float(i)} for i in range(9)]
        assert len(apply_rsi2_daily_cap(cands)) == 4

    def test_tie_break_symbol_asc(self):
        cands = [{"symbol": "ZZZ", "rsi2": 5.0}, {"symbol": "AAA", "rsi2": 5.0}]
        assert [c["symbol"] for c in apply_rsi2_daily_cap(cands)] == ["AAA", "ZZZ"]

    def test_empty_and_zero_cap(self):
        assert apply_rsi2_daily_cap([]) == []
        assert apply_rsi2_daily_cap([{"symbol": "A", "rsi2": 1.0}], cap=0) == []


# --------------------------------------------------------------- slices
class TestBudgetSlices:
    def test_default_30pct_bucket_50_50(self):
        b = BudgetSlices(portfolio_value=100_000.0)
        assert b.bucket_dollars == pytest.approx(30_000.0)
        assert b.slice_dollars("c4") == pytest.approx(15_000.0)
        assert b.slice_dollars("rsi2") == pytest.approx(15_000.0)

    def test_custom_split(self):
        b = BudgetSlices(portfolio_value=200_000.0, c4_share=0.25,
                         rsi2_share=0.75)
        assert b.slice_dollars("c4") == pytest.approx(15_000.0)
        assert b.slice_dollars("rsi2") == pytest.approx(45_000.0)

    def test_shares_must_sum_to_one(self):
        with pytest.raises(ValueError):
            BudgetSlices(portfolio_value=100.0, c4_share=0.6, rsi2_share=0.6)

    def test_remaining_tracks_only_own_strategy(self):
        b = BudgetSlices(portfolio_value=100_000.0)
        # c4 fully used must NOT consume the rsi2 slice
        assert b.remaining_dollars("c4", open_notional=15_000.0) == 0.0
        assert b.remaining_dollars("rsi2") == pytest.approx(15_000.0)
        assert b.remaining_dollars("rsi2", open_notional=5_000.0,
                                   pending_notional=2_000.0) == pytest.approx(8_000.0)

    def test_size_order_whole_shares_and_isolation(self):
        b = BudgetSlices(portfolio_value=100_000.0)
        assert b.size_order("c4", price=50.0, open_notional=14_800.0) == 4
        # c4 usage does NOT consume the rsi2 slice (isolation)
        assert b.size_order("rsi2", price=50.0) == 300
        assert b.size_order("rsi2", price=50.0, qty_or_none=10) == 10

    def test_size_order_zero_when_exhausted(self):
        b = BudgetSlices(portfolio_value=100_000.0)
        assert b.size_order("c4", price=100.0, open_notional=15_000.0) == 0

    def test_invalid_inputs(self):
        with pytest.raises(ValueError):
            BudgetSlices(portfolio_value=-1)
        with pytest.raises(ValueError):
            BudgetSlices(portfolio_value=100.0, c4_share=1.5)
        with pytest.raises(ValueError):
            BudgetSlices(portfolio_value=100.0).size_order("c4", price=0)


# ------------------------------------------------- runner import smoke test
def test_runner_module_imports_and_is_paper_locked():
    spec = importlib.util.spec_from_file_location("swing_runner", HERE / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.PAPER_TRADING_URL == "https://paper-api.alpaca.markets"
    assert mod.AlpacaPaper("k", "s", dry_run=True).dry_run is True
    assert mod.AlpacaPaper("k", "s", dry_run=False).dry_run is False


def test_report_module_imports_and_is_paper_locked():
    spec = importlib.util.spec_from_file_location("swing_report", HERE / "report_pnl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.PAPER_TRADING_URL == "https://paper-api.alpaca.markets"
