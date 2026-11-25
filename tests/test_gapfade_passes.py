"""GAPFADE-MIDCAP-PAPER part 2 — pass orchestration, gates, DST, shadows.

Offline only: a fake data object and a fake broker stand in for Alpaca; the
systemd calendar is checked by parsing the committed unit files.
"""

import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.gapfade_midcap_paper import runner, rules  # noqa: E402
from experiments.gapfade_midcap_paper.state import StateStore  # noqa: E402

DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
        "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11"]
UNIT_DIR = PROJECT_ROOT / "experiments" / "gapfade_midcap_paper" / "systemd"


# --- DST / windows -------------------------------------------------------
def test_dst_spring_forward_edges():
    """From 2026-03-08 02:00 EST->EDT, 09:20-09:28 ET is 13:20-13:28 UTC."""
    assert rules.morning_window(
        datetime(2026, 3, 9, 13, 20, tzinfo=timezone.utc)) is True
    assert rules.morning_window(
        datetime(2026, 3, 9, 13, 27, tzinfo=timezone.utc)) is True
    assert rules.morning_window(
        datetime(2026, 3, 9, 13, 28, tzinfo=timezone.utc)) is False
    # the week before (EST) needs 14:20 UTC; 13:28 UTC is 08:28 ET
    assert rules.morning_window(
        datetime(2026, 3, 2, 13, 28, tzinfo=timezone.utc)) is False
    assert rules.morning_window(
        datetime(2026, 3, 2, 14, 20, tzinfo=timezone.utc)) is True


def test_dst_fall_back_edges():
    """From 2026-11-01 02:00 EDT->EST, 09:20 ET is 14:20 UTC."""
    assert rules.morning_window(
        datetime(2026, 10, 30, 13, 20, tzinfo=timezone.utc)) is True
    assert rules.morning_window(
        datetime(2026, 11, 2, 13, 20, tzinfo=timezone.utc)) is False
    assert rules.morning_window(
        datetime(2026, 11, 2, 14, 20, tzinfo=timezone.utc)) is True
    assert rules.morning_window(
        datetime(2026, 11, 2, 14, 28, tzinfo=timezone.utc)) is False


def test_after_close_window_dst():
    assert rules.after_close_window(
        datetime(2026, 10, 30, 20, 5, tzinfo=timezone.utc)) is True
    assert rules.after_close_window(
        datetime(2026, 11, 2, 20, 5, tzinfo=timezone.utc)) is False
    assert rules.after_close_window(
        datetime(2026, 11, 2, 21, 5, tzinfo=timezone.utc)) is True


def test_within_deadline_uses_et():
    assert runner.within_deadline(
        datetime(2026, 11, 2, 14, 27, tzinfo=timezone.utc)) is True
    assert runner.within_deadline(
        datetime(2026, 11, 2, 14, 28, tzinfo=timezone.utc)) is False


def test_units_reflect_morning_and_after_close_in_new_york_time():
    def oncalendar(name):
        text = (UNIT_DIR / f"{name}.timer").read_text()
        return [ln.strip() for ln in text.splitlines()
                if ln.strip().startswith("OnCalendar=")]

    morning = oncalendar("gapfade-midcap-morning")
    assert any("09:20" in ln and "America/New_York" in ln and
               "Mon..Fri" in ln for ln in morning)
    after = oncalendar("gapfade-midcap-after-close")
    assert any("16:10" in ln and "America/New_York" in ln for ln in after)
    # the morning trigger must fire BEFORE the 09:28 ET submission deadline
    assert all("09:28" not in ln for ln in morning)


def test_units_are_disabled_by_design():
    for name in ("gapfade-midcap-morning", "gapfade-midcap-after-close"):
        svc = (UNIT_DIR / f"{name}.service").read_text()
        assert "[Install]" not in svc        # enable would be a no-op
        assert "NOT INSTALLED" in svc or "DISABLED" in svc
        assert "TRADING_BOT_PAPER_ONLY=1" in svc
    assert not (UNIT_DIR / "enable.sh").exists()


# --- fakes ---------------------------------------------------------------
class FakeBroker:
    def __init__(self, orders=None):
        self.submitted = []
        self.orders = orders or {}

    def submit_opg_buy(self, symbol, qty, coid):
        self.submitted.append(("buy", symbol, qty, coid))
        return {"id": f"o{len(self.submitted)}", "status": "accepted",
                "client_order_id": coid, "filled_qty": "0"}

    def submit_opg_sell(self, symbol, qty, coid):
        self.submitted.append(("sell", symbol, qty, coid))
        return {"id": f"o{len(self.submitted)}", "status": "accepted",
                "client_order_id": coid, "filled_qty": "0"}

    def order_by_client_id(self, coid):
        if coid in self.orders:
            return self.orders[coid]
        for side, _sym, _qty, cid in self.submitted:
            if cid == coid:                 # already accepted earlier
                return {"id": f"known-{cid}", "status": "accepted",
                        "client_order_id": cid, "filled_qty": "0"}
        return None


class FakeData:
    def __init__(self, atrs=None, bars=None):
        self.atrs = atrs or {}
        self._bars = bars or {}

    def atr14(self, symbol, day):
        return self.atrs.get(symbol)

    def official_bar(self, symbol, day):
        bar = self._bars.get((symbol, day))
        if bar is None:
            raise RuntimeError("no bar")
        return bar


def _payload(day="2026-11-03", universe=None):
    universe = list(universe or ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF",
                                 "GGG", "HHH", "III", "JJJ"])
    prior = {s: 100.0 for s in universe}
    opens = {s: 100.0 for s in universe}
    opens.update({"AAA": 95.0, "BBB": 96.0})
    return {
        "day": day, "trading_days": [d for d in DAYS if d >= day],
        "universe": universe, "prior_close": prior,
        "expected_open": opens, "all_expected_opens": dict(opens),
        "earnings": {s: [] for s in universe},
        "eligible_clean": list(universe),
        # ten distinct vols => five populated quintiles
        "vols": {s: 0.01 * (i + 1) for i, s in enumerate(universe)},
        "equity": 100_000.0, "buying_power": 100_000.0}


def _morning(payload, data):
    inp = runner._morning_inputs(payload)
    return runner.RealMorningInputs(
        day=inp.day, trading_days=inp.trading_days, universe=inp.universe,
        prior_close=inp.prior_close, expected_open=inp.expected_open,
        expected_open_all=inp.all_expected_opens, earnings=inp.earnings,
        eligible_clean=inp.eligible_clean, vols=inp.vols,
        atr14=dict(data.atrs), equity=inp.equity,
        buying_power=inp.buying_power, _data=data)


def _st(tmp_path):
    st = StateStore(tmp_path)
    st.load()
    return st


# --- morning execution ---------------------------------------------------
def test_morning_sells_first_then_buys_and_two_shadows_each(tmp_path):
    st = _st(tmp_path)
    st.open_position({"symbol": "ZZZ", "entry_day": "2026-11-02",
                      "entry_price": 50.0, "qty": 4, "stop_price": 40.0,
                      "client_order_id": "gf_mid-ZZZ-buy-20261102-1"})
    payload = _payload()
    payload["universe"].append("ZZZ")
    payload["prior_close"]["ZZZ"] = 50.0
    payload["expected_open"]["ZZZ"] = 51.0     # profit at the open
    payload["all_expected_opens"]["ZZZ"] = 51.0
    payload["vols"]["ZZZ"] = 0.02
    payload["eligible_clean"].append("ZZZ")
    data = FakeData(atrs={"AAA": 1.0, "BBB": 1.0})
    inp = _morning(payload, data)
    plan = runner.plan_morning(st, inp.as_signal_inputs(),
                               rng=random.Random(1), size_fraction=0.01)
    br = FakeBroker()
    n = runner.execute_morning(st, plan, br, data, inp, now=datetime(
        2026, 11, 3, 14, 20, tzinfo=timezone.utc))
    assert n == len(br.submitted) == len(plan["sells"]) + len(plan["buys"])
    sides = [c[0] for c in br.submitted]
    assert sides[0] == "sell" and "sell" not in sides[sides.index("buy"):]
    buys = [c for c in br.submitted if c[0] == "buy"]
    assert len(buys) == 2                      # AAA -5%, BBB -4%
    for _, sym, _, coid in buys:
        shadows = st.shadow_for(coid)
        assert sorted(s["kind"] for s in shadows) == ["random_clean",
                                                       "vol_matched"]
        for s in shadows:
            assert s["status"] == "open"
            assert s["entry_price"] is None       # official open, not yet
            assert s["qty"] >= 1
    # shadows are never sent to the broker
    sent = {c[3] for c in br.submitted}
    assert not any(coid.startswith("gf_mid-SHADOW") for coid in sent)
    # intents recorded for sells + buys, entries counted
    assert st.entries_on("2026-11-03") == len(buys)
    assert st.pass_done("2026-11-03", "morning") is False   # caller saves


def test_morning_never_resubmits_and_refuses_after_deadline(tmp_path):
    st = _st(tmp_path)
    data = FakeData(atrs={"AAA": 1.0, "BBB": 1.0})
    inp = _morning(_payload(), data)
    plan = runner.plan_morning(st, inp.as_signal_inputs(),
                               rng=random.Random(1))
    late = datetime(2026, 11, 3, 14, 30, tzinfo=timezone.utc)   # 09:30 EST
    br = FakeBroker()
    assert runner.execute_morning(st, plan, br, data, inp, now=late) == 0
    assert br.submitted == []
    assert all(r.get("note") == "after_cutoff" for r in st.intents())

    early = datetime(2026, 11, 3, 14, 20, tzinfo=timezone.utc)  # 09:20 EST
    st2 = _st(tmp_path / "b")
    plan2 = runner.plan_morning(st2, inp.as_signal_inputs(),
                                rng=random.Random(1))
    br2 = FakeBroker()
    runner.execute_morning(st2, plan2, br2, data, inp, now=early)
    first = len(br2.submitted)
    # a rerun of the identical plan is idempotent (same client_order_ids)
    runner.execute_morning(st2, plan2, br2, data, inp, now=early)
    assert len(br2.submitted) == first


def test_morning_skips_symbol_without_expected_open_entry(tmp_path):
    st = _st(tmp_path)
    data = FakeData(atrs={"AAA": 1.0, "BBB": 1.0})
    inp = _morning(_payload(), data)
    plan = runner.plan_morning(st, inp.as_signal_inputs(),
                               rng=random.Random(1))
    br = FakeBroker()
    # simulate a missing pre-market print for the first buy
    sym = plan["buys"][0].symbol
    data.delays = {}
    delays = {sym: "no_expected_open"}
    data.delays = delays
    runner.execute_morning(st, plan, br, data, inp, now=datetime(
        2026, 11, 3, 14, 20, tzinfo=timezone.utc))
    assert all(c[1] != sym for c in br.submitted)
    assert any(r.get("note") == "data:no_expected_open" for r in st.intents())


# --- after-close ---------------------------------------------------------
def test_after_close_settles_fills_sets_stops_and_exits(tmp_path):
    st = _st(tmp_path)
    st.open_position({"symbol": "ZZZ", "entry_day": "2026-11-02",
                      "entry_price": 50.0, "qty": 4, "stop_price": 40.0,
                      "client_order_id": "gf_mid-ZZZ-buy-20261102-1"})
    st.add_intent({"kind": "real", "side": "buy", "symbol": "AAA", "qty": 5,
                   "reason": "signal", "client_order_id":
                   "gf_mid-AAA-buy-20261103-1", "day": "2026-11-03",
                   "submitted": True, "fill_price": None})
    ordered = {
        "gf_mid-AAA-buy-20261103-1": {"id": "oA", "status": "filled",
                                      "filled_qty": "5",
                                      "filled_avg_price": "95.5"},
        "gf_mid-ZZZ-sell-20261103-1": {"id": "oZ", "status": "filled",
                                       "filled_qty": "4",
                                       "filled_avg_price": "51.0"},
    }
    bars = {"ZZZ": {"open": 51.0, "close": 50.0},
            "AAA": {"open": 95.5, "close": 97.0}}
    plan = runner.plan_after_close(st, runner.AfterCloseInputs(
        day="2026-11-03", trading_days=DAYS, bars=bars))
    st.add_intent({"kind": "real", "side": "sell", "symbol": "ZZZ", "qty": 4,
                   "reason": "profit", "client_order_id":
                   "gf_mid-ZZZ-sell-20261103-1", "day": "2026-11-03",
                   "submitted": True, "fill_price": None})
    data = FakeData(atrs={"AAA": 1.5}, bars={("AAA", "2026-11-03"):
                                             {"open": 95.5, "close": 97.0}})
    summary = runner.execute_after_close(
        st, plan, FakeBroker(orders=ordered), data,
        runner.RealAfterCloseInputs(day="2026-11-03", trading_days=DAYS,
                                    bars=bars, _data=data))
    pos = st.positions()["AAA"]
    assert pos["entry_price"] == 95.5
    assert pos["stop_price"] == pytest.approx(95.5 - 2 * 1.5)
    assert "ZZZ" not in st.positions()             # profit exit realised
    closed = st.data["closed"][-1]
    assert closed["symbol"] == "ZZZ" and closed["exit_reason"] == "profit"
    assert summary["closed"] == 1 and summary["filled_entries"] >= 1


def test_after_close_flags_next_morning_stop_and_time_cap(tmp_path):
    st = _st(tmp_path)
    st.open_position({"symbol": "AAA", "entry_day": "2026-11-03",
                      "entry_price": 100.0, "qty": 3, "stop_price": 95.0,
                      "client_order_id": "gf_mid-AAA-buy-20261103-1"})
    bars = {"AAA": {"open": 99.0, "close": 94.0}}     # closes below the stop
    plan = runner.plan_after_close(st, runner.AfterCloseInputs(
        day="2026-11-03", trading_days=DAYS, bars=bars))
    assert plan["pending_exits"][0]["reason"] == "stop"
    data = FakeData()
    runner.execute_after_close(st, plan, FakeBroker(), data,
                               runner.RealAfterCloseInputs(
                                   day="2026-11-03", trading_days=DAYS,
                                   bars=bars, _data=data))
    assert st.data["pending_exits"] == plan["pending_exits"]
    # next morning: the deferred stop becomes an OPG sell
    mp = _payload(day="2026-11-04")
    mp["universe"].append("AAA")
    mp["prior_close"]["AAA"] = 94.0
    mp["expected_open"]["AAA"] = 94.5
    mp["all_expected_opens"]["AAA"] = 94.5
    inp = _morning(mp, FakeData())
    morning = runner.plan_morning(st, inp.as_signal_inputs(),
                                  rng=random.Random(2))
    sells = [o for o in morning["sells"] if o.symbol == "AAA"]
    assert len(sells) == 1 and sells[0].reason == "stop"
    assert sells[0].side == "sell"


def test_time_cap_flags_on_third_trading_day(tmp_path):
    st = _st(tmp_path)
    st.open_position({"symbol": "AAA", "entry_day": "2026-11-03",
                      "entry_price": 100.0, "qty": 3, "stop_price": 90.0,
                      "client_order_id": "gf_mid-AAA-buy-20261103-1"})
    bars = {"AAA": {"open": 101.0, "close": 99.0}}
    DAYS_ = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    # trading day 2: still inside the cap, nothing flagged
    assert runner.plan_after_close(st, runner.AfterCloseInputs(
        day="2026-11-04", trading_days=DAYS_, bars=bars))["pending_exits"] == []
    # trading day 3 (2026-11-05): cap reached at the close -> sell next open
    plan = runner.plan_after_close(st, runner.AfterCloseInputs(
        day="2026-11-05", trading_days=DAYS_, bars=bars))
    assert plan["pending_exits"][0] == {"symbol": "AAA",
                                        "reason": "time_cap",
                                        "for_day": "2026-11-05"}


# --- shadows -------------------------------------------------------------
def _seed_shadow(st, coid, sym, entry_day, entry_price, stop):
    st.add_shadow({"real_symbol": "AAA", "real_client_order_id": coid,
                   "kind": "random_clean", "symbol": sym,
                   "entry_day": entry_day, "entry_open": None,
                   "entry_price": None, "qty": 5, "stop_price": None,
                   "status": "open", "note": None})
    # the extra arguments are the already-filled entry a later pass sees
    # (positional order in the callers: entry_price, then stop_price)
    if entry_price is not None:
        st.shadows()[-1]["entry_price"] = st.shadows()[-1]["entry_open"] = (
            float(entry_price))
    if stop is not None:
        st.shadows()[-1]["stop_price"] = float(stop)


def test_shadow_entry_filled_from_official_open(tmp_path):
    st = _st(tmp_path)
    _seed_shadow(st, "gf_mid-AAA-buy-20261103-1", "CCC", "2026-11-03",
                 None, None)
    # the entry-day official bar (the fixture's other tests hand bars the
    # same way; without any official open the shadow must not be guessed)
    data = FakeData(atrs={"CCC": 1.0},
                    bars={("CCC", "2026-11-03"): {"open": 100.0,
                                                  "close": 99.0}})
    res = runner.settle_shadows(st, data, "2026-11-03", DAYS, {})
    sh = st.shadows()[0]
    assert sh["entry_price"] == 100.0 and sh["entry_open"] == 100.0
    assert sh["stop_price"] == pytest.approx(98.0)
    assert res["new"] == 1


def test_shadow_exits_identical_to_real_rules(tmp_path):
    st = _st(tmp_path)
    _seed_shadow(st, "gf_mid-AAA-buy-20261103-1", "CCC", "2026-11-03",
                 100.0, 100.0)
    st.shadows()[0]["stop_price"] = 98.0
    data = FakeData(atrs={"CCC": 1.0})
    # day 2: open above entry -> profit at the official open
    res = runner.settle_shadows(st, data, "2026-11-04", DAYS,
                                {"CCC": {"open": 101.0, "close": 100.5}})
    sh = st.shadows()[0]
    assert sh["status"] == "closed" and sh["exit_reason"] == "profit"
    assert sh["exit_price"] == 101.0 and res["closed"] == 1


def test_shadow_stop_defers_to_next_open_like_real(tmp_path):
    st = _st(tmp_path)
    _seed_shadow(st, "gf_mid-AAA-buy-20261103-1", "CCC", "2026-11-03",
                 100.0, 98.0)
    data = FakeData(atrs={"CCC": 1.0})
    runner.settle_shadows(st, data, "2026-11-03", DAYS,
                          {"CCC": {"open": 100.0, "close": 97.5}})
    assert st.shadows()[0]["exit_pending"] == "stop"
    assert st.shadows()[0]["status"] == "open"
    runner.settle_shadows(st, data, "2026-11-04", DAYS,
                          {"CCC": {"open": 96.0, "close": 96.5}})
    sh = st.shadows()[0]
    assert sh["status"] == "closed" and sh["exit_price"] == 96.0
    assert sh["exit_reason"] == "stop"


def test_shadow_without_official_open_is_not_guessed(tmp_path):
    st = _st(tmp_path)
    _seed_shadow(st, "gf_mid-AAA-buy-20261103-1", "CCC", "2026-11-03",
                 None, None)
    st.shadows()[0]["entry_price_ref"] = 99.0    # pre-market print only
    data = FakeData()                           # no official bar available
    runner.settle_shadows(st, data, "2026-11-03", DAYS, {})
    sh = st.shadows()[0]
    assert sh["entry_price"] is None
    assert sh["note"] == "no_official_open_yet"
    assert sh["status"] == "open"


def test_two_shadows_per_real_entry_never_duplicate(tmp_path):
    st = _st(tmp_path)
    payload = _payload()
    inp = runner._morning_inputs(payload)
    plan = runner.plan_morning(st, inp, rng=random.Random(7))
    real_and_shadow = ({o.symbol for o in plan["buys"]}
                       | {s["symbol"] for s in plan["shadows"]})
    assert len(real_and_shadow) == len(plan["buys"]) + len(plan["shadows"])
    for o in plan["buys"]:
        kinds = [s["kind"] for s in plan["shadows"]
                 if s["real_symbol"] == o.symbol]
        assert sorted(kinds) == ["random_clean", "vol_matched"]


# --- logging -------------------------------------------------------------
def test_event_log_written_and_carries_no_account_data(tmp_path):
    st = _st(tmp_path)
    runner.log_event(st, "morning", "2026-11-03", "2026-11-03T09:22:00-05:00",
                     submitted=3, sells=1, buys=2, shadows=4)
    line = (tmp_path / "events.log").read_text().strip()
    assert '"pass": "morning"' in line and '"buys": 2' in line
    assert "APCA" not in line and "secret" not in line.lower()


# --- gates through main() ------------------------------------------------
def test_gate_execute_requires_opt_in_flags(tmp_path, monkeypatch, capsys):
    import json
    payload = _payload()
    p = tmp_path / "in.json"
    p.write_text(json.dumps(payload))

    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    rc = runner.main(["--execute", "--inputs", str(p), "--pass", "morning",
                      "--state-dir", str(tmp_path / "s")])
    assert rc == 2

    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("GAPFADE_MIDCAP_KILL_FILE", str(tmp_path / "kill"))
    (tmp_path / "kill").write_text("x")
    rc = runner.main(["--execute", "--inputs", str(p), "--pass", "morning",
                      "--state-dir", str(tmp_path / "s")])
    assert rc == 2
    assert "KILL SWITCH" in capsys.readouterr().err
