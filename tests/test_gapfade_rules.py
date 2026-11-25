"""GAPFADE-MIDCAP-PAPER — frozen decision logic (rules.py) unit tests."""

import random
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP = PROJECT_ROOT / "experiments" / "gapfade_midcap_paper"
sys.path.insert(0, str(PROJECT_ROOT))
# Import ONLY via the package path: never put the experiment dir itself on
# sys.path, or a bare `rules`/`guards`/`runner` module would shadow the
# other experiments' same-named modules (e.g. experiments/swing_paper/rules.py).
from experiments.gapfade_midcap_paper import rules  # noqa: E402

ET = ZoneInfo("America/New_York")
DAYS = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4),
        date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)]


# --- gap filter ----------------------------------------------------------
def test_gap_boundary_at_exactly_minus_4pct_is_a_signal():
    g = rules.gap_pct(96.0, 100.0)          # exactly -4.0%
    assert g <= -0.04
    assert rules.is_gap_signal(g) is True


def test_gap_just_above_minus_4pct_is_not_a_signal():
    g = rules.gap_pct(96.1, 100.0)
    assert rules.is_gap_signal(g) is False


def test_gap_deeper_than_minus_4pct_is_a_signal():
    assert rules.is_gap_signal(rules.gap_pct(90.0, 100.0)) is True


def test_gap_pct_rejects_bad_prices():
    with pytest.raises(rules.RuleError):
        rules.gap_pct(0.0, 100.0)
    with pytest.raises(rules.RuleError):
        rules.gap_pct(100.0, None)


# --- earnings filter -----------------------------------------------------
CAL = rules.EarningsCalendar({
    "AAA": {"2026-11-03"},      # t-1 for a 2026-11-04 entry
    "BBB": {"2026-11-04"},      # t
    "CCC": {"2026-11-05"},      # t+1
    "DDD": {"2026-10-01"},      # clean
    "EEE": set(),               # has coverage, no dates
})


@pytest.mark.parametrize("sym", ["AAA", "BBB", "CCC"])
def test_earnings_skips_t_minus_1_t_and_t_plus_1(sym):
    assert rules.earnings_skip(sym, date(2026, 11, 4), CAL, DAYS) is True


def test_earnings_clean_name_is_not_skipped():
    assert rules.earnings_skip("DDD", date(2026, 11, 4), CAL, DAYS) is False
    assert rules.earnings_skip("EEE", date(2026, 11, 4), CAL, DAYS) is False


def test_earnings_skips_name_with_no_coverage():
    assert rules.earnings_skip("ZZZ", date(2026, 11, 4), CAL, DAYS) is True


def test_earnings_uses_trading_days_for_monday_entry():
    cal = rules.EarningsCalendar({"FRY": {"2026-11-06"}})  # Friday
    monday = date(2026, 11, 9)
    assert rules.earnings_skip("FRY", monday, cal, DAYS) is True
    # calendar-day fallback would look at Sunday 11-08 only
    assert rules.earnings_skip("FRY", monday, cal, []) is False


# --- ATR / stop ----------------------------------------------------------
def test_atr14_is_mean_of_last_14_true_ranges():
    trs = [float(i) for i in range(1, 21)]
    assert rules.atr(trs) == pytest.approx(sum(range(7, 21)) / 14)


def test_atr_requires_a_full_window():
    with pytest.raises(rules.RuleError):
        rules.atr([1.0] * 5)


def test_stop_level_is_entry_minus_2x_atr_t_minus_1():
    assert rules.stop_level(100.0, 1.5) == pytest.approx(97.0)


# --- entry ranking / caps / sizing ---------------------------------------
def cand(sym, pc, eo):
    return rules.Candidate(sym, pc, eo)


def test_rank_sorts_most_negative_gap_first():
    cands = [cand("A", 100, 95.0), cand("B", 100, 90.0), cand("C", 100, 99.0)]
    dec = rules.rank_candidates(cands, held=set(), skipped={},
                                entry_day=date(2026, 11, 4), entries_today=0,
                                open_positions=0, equity=100_000.0,
                                buying_power=100_000.0, size_fraction=0.01)
    assert [p.symbol for p in dec.plans] == ["B", "A"]
    assert dec.skipped["C"] == "no_signal"


def test_max_three_new_entries_per_day():
    cands = [cand(s, 100, 90.0) for s in "ABCDE"]
    dec = rules.rank_candidates(cands, held=set(), skipped={},
                                entry_day=date(2026, 11, 4), entries_today=0,
                                open_positions=0, equity=100_000.0,
                                buying_power=100_000.0)
    assert len(dec.plans) == 3
    assert dec.skipped["D"] == "daily_cap"
    assert dec.remaining_slots_today == 0


def test_daily_cap_counts_entries_already_made_today():
    cands = [cand(s, 100, 90.0) for s in "ABC"]
    dec = rules.rank_candidates(cands, held=set(), skipped={},
                                entry_day=date(2026, 11, 4), entries_today=2,
                                open_positions=0, equity=100_000.0,
                                buying_power=100_000.0)
    assert len(dec.plans) == 1


def test_max_ten_open_positions():
    cands = [cand(s, 100, 90.0) for s in "ABCD"]
    dec = rules.rank_candidates(cands, held=set(), skipped={},
                                entry_day=date(2026, 11, 4), entries_today=0,
                                open_positions=9, equity=100_000.0,
                                buying_power=100_000.0)
    assert len(dec.plans) == 1
    assert dec.skipped["B"] == "max_open"
    assert dec.remaining_positions == 0


def test_held_symbol_is_skipped():
    cands = [cand("A", 100, 90.0), cand("B", 100, 90.0)]
    dec = rules.rank_candidates(cands, held={"a"}, skipped={},
                                entry_day=date(2026, 11, 4), entries_today=0,
                                open_positions=1, equity=100_000.0,
                                buying_power=100_000.0)
    assert [p.symbol for p in dec.plans] == ["B"]
    assert dec.skipped["A"] == "already_held"


def test_sizing_is_one_percent_of_equity_by_default():
    dec = rules.rank_candidates([cand("A", 100, 50.0)], held=set(),
                                skipped={}, entry_day=date(2026, 11, 4),
                                entries_today=0, open_positions=0,
                                equity=100_000.0, buying_power=100_000.0)
    # 1% of 100k = 1000 -> 20 shares at 50.00
    assert dec.plans[0].qty == 20
    assert dec.plans[0].notional == pytest.approx(1000.0)


def test_sizing_requires_at_least_one_whole_share():
    dec = rules.rank_candidates([cand("A", 1000, 900.0)], held=set(),
                                skipped={}, entry_day=date(2026, 11, 4),
                                entries_today=0, open_positions=0,
                                equity=100_000.0, buying_power=1_000_000.0)
    # 1000 notional / 900 = 1 share -> still allowed
    assert dec.plans[0].qty == 1
    # 1% of 20k = 200 notional at 1200 -> no whole share
    dec2 = rules.rank_candidates([cand("A", 1250.0, 1200.0)], held=set(),
                                 skipped={}, entry_day=date(2026, 11, 4),
                                 entries_today=0, open_positions=0,
                                 equity=20_000.0, buying_power=1_000_000.0)
    assert dec2.plans == ()
    assert dec2.skipped["A"] == "size_zero"


def test_sizing_skips_when_buying_power_insufficient():
    dec = rules.rank_candidates([cand("A", 100, 90.0)], held=set(),
                                skipped={}, entry_day=date(2026, 11, 4),
                                entries_today=0, open_positions=0,
                                equity=100_000.0, buying_power=500.0)
    assert dec.plans == ()
    assert dec.skipped["A"] == "size_zero"


def test_buying_power_is_spent_greedily_in_rank_order():
    cands = [cand("A", 100, 90.0), cand("B", 100, 95.0)]
    dec = rules.rank_candidates(cands, held=set(), skipped={},
                                entry_day=date(2026, 11, 4), entries_today=0,
                                open_positions=0, equity=100_000.0,
                                buying_power=1005.0)
    assert [p.symbol for p in dec.plans] == ["A"]   # 11 x 90 = 990
    assert dec.skipped["B"] == "size_zero"


def test_client_order_id_prefix_and_shape():
    cid = rules.build_client_order_id("AAA", "buy", date(2026, 11, 4), 1)
    assert cid.startswith("gf_mid-")
    assert cid == "gf_mid-AAA-buy-20261104-1"
    with pytest.raises(rules.RuleError):
        rules.build_client_order_id("AAA", "hold", date(2026, 11, 4), 1)


# --- exits ---------------------------------------------------------------
def pos(entry_day=date(2026, 11, 4), entry_price=100.0, stop=97.0):
    return rules.Position("AAA", entry_day, entry_price, 10, stop,
                          "gf_mid-AAA-buy-20261104-1")


def test_profit_exit_on_first_later_open_above_entry():
    # entry day itself: even an open above entry does not count
    d0 = rules.decide_exit(pos(), DAYS[2], DAYS, day_open=101.0)
    assert d0.reason is None
    d1 = rules.decide_exit(pos(), DAYS[3], DAYS, day_open=100.01)
    assert (d1.reason, d1.when, d1.trigger_price) == (rules.PROFIT, "open",
                                                      100.01)


def test_open_equal_to_entry_is_not_a_profit_exit():
    d = rules.decide_exit(pos(), DAYS[3], DAYS, day_open=100.0)
    assert d.reason is None


def test_stop_after_close_at_or_below_level_sells_next_open():
    at = rules.decide_exit(pos(), DAYS[3], DAYS, day_open=99.0,
                           day_close=97.0)
    assert (at.reason, at.when, at.stop_pending) == (rules.STOP, "close",
                                                     True)
    below = rules.decide_exit(pos(), DAYS[3], DAYS, day_close=96.5)
    assert below.reason == rules.STOP


def test_stop_on_the_entry_day_close_counts():
    d = rules.decide_exit(pos(), DAYS[2], DAYS, day_close=96.0)
    assert d.reason == rules.STOP
    assert d.when == "close"


def test_time_cap_fires_after_close_of_entry_day_plus_2():
    p = pos()
    # day 1 = entry day -> nothing
    assert rules.decide_exit(p, DAYS[2], DAYS, day_close=100.0).reason is None
    # day 2 (t+1) -> nothing
    assert rules.decide_exit(p, DAYS[3], DAYS, day_close=100.0).reason is None
    # day 3 = t+2 -> time cap
    d = rules.decide_exit(p, DAYS[4], DAYS, day_close=100.0)
    assert (d.reason, d.when) == (rules.TIME_CAP, "close")


def test_priority_profit_at_open_beats_stop_at_close():
    # open above entry AND close below stop on the same day -> profit first
    d = rules.decide_exit(pos(), DAYS[5], DAYS, day_open=101.0,
                          day_close=90.0)
    assert d.reason == rules.PROFIT


def test_priority_profit_at_open_beats_time_cap():
    d = rules.decide_exit(pos(), DAYS[4], DAYS, day_open=101.0,
                          day_close=100.0)   # t+2 close day
    assert d.reason == rules.PROFIT


def test_priority_stop_at_close_beats_time_cap():
    d = rules.decide_exit(pos(), DAYS[4], DAYS, day_open=99.0,
                          day_close=96.0)    # t+2, stop hit
    assert d.reason == rules.STOP


def test_due_exits_splits_profit_now_from_deferred_sells():
    p1 = rules.Position("AAA", DAYS[2], 100.0, 10, 97.0, "x")
    p2 = rules.Position("BBB", DAYS[2], 100.0, 10, 97.0, "y")
    p3 = rules.Position("CCC", DAYS[2], 100.0, 10, 97.0, "z")
    bars = {"AAA": {"open": 101.0, "close": 100.0},
            "BBB": {"open": 99.0, "close": 96.0},
            "CCC": {"open": 99.0, "close": 99.5}}
    due, deferred = rules.due_exits([p1, p2, p3], DAYS[4], DAYS, bars)
    assert [d.symbol for d in due] == ["AAA"]
    assert sorted(deferred) == ["BBB", "CCC"]     # stop + time cap


def test_sort_morning_sells_orders_profit_then_stop_then_cap():
    exits = [
        rules.ExitDecision("Z", rules.TIME_CAP, "open", None, False),
        rules.ExitDecision("Y", rules.STOP, "open", None, False),
        rules.ExitDecision("X", rules.PROFIT, "open", None, False),
    ]
    assert [d.symbol for d in rules.sort_morning_sells(exits)] == \
        ["X", "Y", "Z"]


# --- costs ---------------------------------------------------------------
def test_round_trip_cost_is_bps_on_both_sides():
    assert rules.round_trip_cost(10_000.0, 27.0) == pytest.approx(54.0)
    assert rules.DEFAULT_COST_BPS_PER_SIDE == 27.0


# --- shadows -------------------------------------------------------------
def _vols():
    # 10 symbols with increasing vol; quintiles hold 2 each
    return {s: float(i) for i, s in enumerate(
        ["V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9"])}


def test_vol_quintile_buckets_by_rank():
    q = rules.vol_quintile(_vols())
    assert [q[f"V{i}"] for i in range(10)] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_select_shadows_picks_clean_unused_names_deterministically():
    vols = _vols()
    # real entry is V2 (quintile 1 -> partner must be V3)
    universe = list(vols)
    picks1 = rules.select_shadows("V2", universe, set(universe), set(),
                                  vols, random.Random(7))
    picks2 = rules.select_shadows("V2", universe, set(universe), set(),
                                  vols, random.Random(7))
    assert picks1 == picks2
    first, second = picks1
    assert first.kind == "random_clean" and first.symbol in universe
    assert first.symbol != "V2"
    assert second.kind == "vol_matched"
    assert second.symbol == "V3" and second.quintile == 1
    assert second.symbol != first.symbol


def test_select_shadows_skips_names_with_earnings_and_chosen():
    vols = _vols()
    universe = list(vols)
    clean = {"V0", "V1", "V2", "V4", "V5"}      # V3 (vol partner) dirty
    first, second = rules.select_shadows("V2", universe, clean, {"V0"},
                                         vols, random.Random(1))
    assert first.symbol in {"V1", "V4", "V5"}
    assert second.symbol is None
    assert second.reason == "no_name_in_quintile"


def test_return_vol_needs_lookback_plus_one_closes():
    with pytest.raises(rules.RuleError):
        rules.return_vol([100.0] * 60)
    assert rules.return_vol([100.0] * 61) == pytest.approx(0.0)


# --- ET windows ----------------------------------------------------------
def et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_morning_window_is_0920_to_0928_et():
    assert rules.morning_window(et(2026, 11, 4, 9, 20)) is True
    assert rules.morning_window(et(2026, 11, 4, 9, 27)) is True
    assert rules.morning_window(et(2026, 11, 4, 9, 28)) is False
    assert rules.morning_window(et(2026, 11, 4, 9, 19)) is False


def test_after_close_window_and_dst_awareness():
    assert rules.after_close_window(et(2026, 11, 4, 16, 0)) is True
    assert rules.after_close_window(et(2026, 11, 4, 15, 59)) is False
    assert rules.choose_pass(et(2026, 11, 4, 9, 25)) == "morning"
    assert rules.choose_pass(et(2026, 11, 4, 12, 0)) is None
    # 13:25 UTC in November (EST = UTC-5) is 08:25 ET -> not morning,
    # while 14:25 UTC is 09:25 ET -> morning. A fixed-UTC rule would break.
    from datetime import timezone
    assert rules.choose_pass(datetime(2026, 11, 4, 14, 25,
                                      tzinfo=timezone.utc)) == "morning"
    assert rules.choose_pass(datetime(2026, 11, 4, 13, 25,
                                      tzinfo=timezone.utc)) is None
