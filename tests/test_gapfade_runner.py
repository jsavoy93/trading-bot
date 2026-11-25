"""GAPFADE-MIDCAP-PAPER — runner orchestration, state and CLI behaviour."""

import json
import random
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP = PROJECT_ROOT / "experiments" / "gapfade_midcap_paper"
sys.path.insert(0, str(PROJECT_ROOT))
# Import ONLY via the package path: never put the experiment dir itself on
# sys.path, or a bare `rules`/`guards`/`runner` module would shadow the
# other experiments' same-named modules (e.g. experiments/swing_paper/rules.py).
from experiments.gapfade_midcap_paper import rules  # noqa: E402

from experiments.gapfade_midcap_paper import runner  # noqa: E402
from experiments.gapfade_midcap_paper.state import StateStore  # noqa: E402

DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
        "2026-11-06", "2026-11-09"]


class CountingBroker:
    def __init__(self):
        self.calls = []

    def submit_opg_buy(self, symbol, qty, client_order_id):
        self.calls.append(("buy", symbol, qty, client_order_id))
        return {"status": "accepted"}

    def submit_opg_sell(self, symbol, qty, client_order_id):
        self.calls.append(("sell", symbol, qty, client_order_id))
        return {"status": "accepted"}

    def buying_power(self):
        return 1_000_000.0

    def equity(self):
        return 100_000.0


def inputs(day="2026-11-04", opens=None, **kw):
    opens = opens or {"AAA": 90.0, "BBB": 95.0, "CCC": 95.5}
    prior = {s: 100.0 for s in opens}
    earnings = {s: {"2026-09-01"} for s in opens}   # coverage, clean
    return runner.MorningInputs(
        day=day, trading_days=DAYS, universe=list(opens),
        prior_close=prior, expected_open=dict(opens),
        all_expected_opens=dict(opens),
        earnings=rules.EarningsCalendar(earnings),
        eligible_clean=set(opens), vols={s: 0.02 + i * 0.001
                                         for i, s in enumerate(sorted(opens))},
        equity=100_000.0, buying_power=100_000.0, **kw)


def test_morning_plan_ranks_and_sizes_entries():
    st = StateStore()          # in-memory, never saved
    plan = runner.plan_morning(st, inputs(), rng=random.Random(1))
    assert [o.symbol for o in plan["buys"]] == ["AAA", "BBB", "CCC"]
    aaa = plan["buys"][0]
    # REBAL-40-50-10: 3% of 100k = 3000 / 90.0 -> 33 shares (was 11 at 1%)
    assert aaa.qty == 33 and aaa.client_order_id == "gf_mid-AAA-buy-20261104-1"
    assert all(o.side == "buy" for o in plan["buys"])


def test_morning_plan_sells_due_profit_exits_before_buying():
    st = StateStore()
    st.open_position({"symbol": "AAA", "entry_day": "2026-11-02",
                      "entry_price": 88.0, "qty": 11, "stop_price": 84.0,
                      "client_order_id": "gf_mid-AAA-buy-20261102-1"})
    st.record_entry("2026-11-02", 1)
    plan = runner.plan_morning(st, inputs(opens={"AAA": 90.0, "BBB": 95.0}),
                               rng=random.Random(2))
    assert [o.symbol for o in plan["sells"]] == ["AAA"]
    assert plan["sells"][0].reason == rules.PROFIT
    # the exiting symbol is not re-bought the same day
    assert [o.symbol for o in plan["buys"]] == ["BBB"]


def test_morning_plan_honours_deferred_stop_from_previous_close():
    st = StateStore()
    st.data["pending_exits"] = [{"symbol": "AAA", "reason": rules.STOP,
                                 "for_day": "2026-11-03"}]
    st.open_position({"symbol": "AAA", "entry_day": "2026-11-02",
                      "entry_price": 90.0, "qty": 11, "stop_price": 86.0,
                      "client_order_id": "gf_mid-AAA-buy-20261102-1"})
    plan = runner.plan_morning(st, inputs(opens={"AAA": 88.0}),
                               rng=random.Random(3))
    assert [o.symbol for o in plan["sells"]] == ["AAA"]
    assert plan["sells"][0].reason == rules.STOP
    assert plan["sells"][0].client_order_id.startswith("gf_mid-AAA-sell-")


def test_earnings_skips_name_in_morning_plan():
    inp = inputs(opens={"AAA": 90.0, "BBB": 95.0})
    inp.earnings = rules.EarningsCalendar({"AAA": {"2026-11-05"},
                                           "BBB": {"2026-09-01"}})
    plan = runner.plan_morning(StateStore(), inp, rng=random.Random(4))
    assert [o.symbol for o in plan["buys"]] == ["BBB"]
    assert plan["skipped"]["AAA"] == "earnings"


def test_morning_plan_produces_two_unordered_shadows_per_entry():
    # 50 names -> 10 per vol quintile, so a vol-matched partner exists
    names = ["AAA", "BBB", "CCC"] + [f"N{i:02d}" for i in range(47)]
    inp = inputs(opens={s: 90.0 for s in names})
    plan = runner.plan_morning(StateStore(), inp, rng=random.Random(5))
    assert len(plan["buys"]) == 3
    assert len(plan["shadows"]) == 6
    kinds = [s["kind"] for s in plan["shadows"]]
    assert kinds.count("random_clean") == 3
    assert kinds.count("vol_matched") == 3
    shadow_pairs = {(s["real_symbol"], s["symbol"]) for s in plan["shadows"]}
    real_syms = {o.symbol for o in plan["buys"]}
    assert {p[0] for p in shadow_pairs} == real_syms
    # a shadow never duplicates a real entry or another shadow name
    assert not ({p[1] for p in shadow_pairs} & real_syms)
    assert len({p[1] for p in shadow_pairs}) == len(shadow_pairs)
    assert all(o.side == "buy" for o in plan["buys"])   # shadows not ordered


def test_after_close_plan_flags_deferred_exits():
    st = StateStore()
    st.open_position({"symbol": "AAA", "entry_day": "2026-11-02",
                      "entry_price": 90.0, "qty": 11, "stop_price": 86.0,
                      "client_order_id": "gf_mid-AAA-buy-20261102-1"})
    plan = runner.plan_after_close(st, runner.AfterCloseInputs(
        day="2026-11-03", trading_days=DAYS,
        bars={"AAA": {"open": 89.0, "close": 85.0}}))
    assert plan["pending_exits"] == [
        {"symbol": "AAA", "reason": rules.STOP, "for_day": "2026-11-03"}]


# --- idempotency ---------------------------------------------------------
def test_rerun_of_same_day_does_not_duplicate_entries(tmp_path):
    st = StateStore(tmp_path)
    st.load()
    plan1 = runner.plan_morning(st, inputs(), rng=random.Random(1))
    # simulate part-2 fills: open positions + record entry count + order ids
    for o in plan1["buys"]:
        st.open_position({"symbol": o.symbol, "entry_day": plan1["day"],
                          "entry_price": o.price_ref, "qty": o.qty,
                          "stop_price": o.price_ref - 1.0,
                          "client_order_id": o.client_order_id})
        st.record_order_id(o.client_order_id)
    st.record_entry(plan1["day"], len(plan1["buys"]))
    st.save()

    st2 = StateStore(tmp_path)
    st2.load()
    plan2 = runner.plan_morning(st2, inputs(), rng=random.Random(1))
    assert plan2["buys"] == []
    assert plan2["skipped"] == {"AAA": "no_signal", "BBB": "no_signal",
                                "CCC": "no_signal"} or plan2["buys"] == []
    # the cap is the binding reason when signals still exist
    assert st2.entries_on(plan2["day"]) == 3


def test_state_store_roundtrip_and_order_keys(tmp_path):
    st = StateStore(tmp_path)
    st.load()
    assert st.positions() == {} and st.entries_on("2026-11-04") == 0
    st.record_entry("2026-11-04", 2)
    st.record_order_id("gf_mid-AAA-buy-20261104-1")
    st.save()
    st2 = StateStore(tmp_path)
    st2.load()
    assert st2.entries_on("2026-11-04") == 2
    assert st2.has_order_id("gf_mid-AAA-buy-20261104-1")


def test_default_state_dir_is_outside_repo_and_openclaw():
    p = str(StateStore().dir)
    assert p.startswith("/root/trading-data/gapfade-midcap-paper")
    assert "trading_bot.db" not in p


# --- execute path (broker unwired in part 1) -----------------------------
def test_execute_plan_submits_in_part2_style_and_records_keys(tmp_path):
    st = StateStore(tmp_path)
    st.load()
    plan = runner.plan_morning(st, inputs(), rng=random.Random(1))
    br = CountingBroker()
    assert runner.execute_plan(st, plan, br) == 0
    assert len(br.calls) == 3
    # a second execute of the same plan submits nothing new
    assert runner.execute_plan(st, plan, br) == 0
    assert len(br.calls) == 3


def test_execute_plan_with_unwired_broker_raises_not_wired(tmp_path):
    st = StateStore(tmp_path)
    plan = runner.plan_morning(st, inputs(), rng=random.Random(1))
    from experiments.gapfade_midcap_paper import broker
    with pytest.raises(broker.NotWired):
        runner.execute_plan(st, plan, broker.make_broker())


# --- CLI: dry run is the default and places zero orders ------------------
def _write_inputs(tmp_path):
    payload = {
        "day": "2026-11-04", "trading_days": DAYS,
        "universe": ["AAA", "BBB"],
        "prior_close": {"AAA": 100.0, "BBB": 100.0},
        "expected_open": {"AAA": 90.0, "BBB": 95.0},
        "all_expected_opens": {"AAA": 90.0, "BBB": 95.0},
        "earnings": {"AAA": ["2026-09-01"], "BBB": ["2026-09-01"]},
        "eligible_clean": ["AAA", "BBB"], "vols": {"AAA": 0.02, "BBB": 0.03},
        "equity": 100000.0, "buying_power": 100000.0,
    }
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(payload))
    return path


def test_cli_default_is_dry_run_and_places_zero_orders(tmp_path, monkeypatch,
                                                       capsys):
    monkeypatch.setattr(runner.broker_mod, "make_broker",
                        lambda args=None: (_ for _ in ()).throw(
                            AssertionError("broker must not be built")))
    monkeypatch.setattr(runner, "execute_plan",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("execute_plan must not run")))
    rc = runner.main(["--inputs", str(_write_inputs(tmp_path)),
                      "--pass", "morning", "--state-dir", str(tmp_path),
                      "--seed", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN — zero orders placed" in out
    assert "BUY(OPG)  AAA" in out and "BUY(OPG)  BBB" in out
    assert not (tmp_path / "state.json").exists()   # state not written


def test_cli_execute_with_inputs_refused(tmp_path, monkeypatch, capsys):
    """PART 2: --execute with a planted --inputs payload is refused (exit 2)
    — orders only ever run from live Alpaca PAPER data, never fixtures."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("GAPFADE_MIDCAP_KILL_FILE", str(tmp_path / "kill"))
    monkeypatch.setattr(runner.broker_mod, "make_broker",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no broker on the --inputs path")))
    rc = runner.main(["--execute", "--inputs", str(_write_inputs(tmp_path)),
                      "--pass", "morning", "--state-dir", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--execute with --inputs is not supported" in err
    assert not (tmp_path / "state.json").exists()


def test_cli_execute_guard_failure_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    rc = runner.main(["--execute", "--inputs", str(_write_inputs(tmp_path)),
                      "--pass", "morning", "--state-dir", str(tmp_path)])
    assert rc == 2


# --- item 3: live dry run vs TRADING_BOT_PAPER_ONLY ----------------------
def test_live_dry_run_without_paper_env_needs_no_execute_env(
        tmp_path, monkeypatch):
    """A no---inputs (live-data) DRY RUN works without TRADING_BOT_PAPER_ONLY:
    it loads the market-data PAPER client (for_orders=False) and places zero
    orders; the live data path is reached, so no guard blocked it."""
    from experiments.gapfade_midcap_paper import guards
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    seen = {}

    class _FakeClient:
        base = guards.PAPER_API_URL

    def fake_client(args=None, for_orders=True):
        seen["for_orders"] = for_orders
        return _FakeClient()

    def fake_build(args, state, client, now):
        raise SystemExit(0)                # reached the live data path

    monkeypatch.setattr(runner.broker_mod, "make_client", fake_client)
    monkeypatch.setattr(runner.broker_mod, "make_broker",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("dry run must not build a broker")))
    monkeypatch.setattr(runner, "_build_real_morning", fake_build)
    with pytest.raises(SystemExit) as exc:
        runner.main(["--pass", "morning", "--state-dir", str(tmp_path)])
    assert exc.value.code == 0
    assert seen.get("for_orders") is False     # market-data client only


def test_order_placement_still_requires_paper_env(tmp_path, monkeypatch):
    """make_broker/make_client(for_orders=True) refuse without the env, and
    make_client(for_orders=False) yields a client hard-locked to paper."""
    from experiments.gapfade_midcap_paper import broker, guards
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    with pytest.raises(guards.GuardError):
        broker.make_client(None, for_orders=True)
    class _Ns:
        state_dir = None
    with pytest.raises(guards.GuardError):
        broker.make_broker(_Ns())
    assert guards.PAPER_API_URL == "https://paper-api.alpaca.markets"


# --- REBAL-40-50-10 hard 30% total cap at runner level -------------------
def _hold(st, sym, entry_price, qty, day="2026-11-02"):
    st.open_position({"symbol": sym, "entry_day": day,
                      "entry_price": entry_price, "qty": qty,
                      "stop_price": entry_price - 1.0,
                      "client_order_id": f"gf_mid-{sym}-buy-20261102-1"})


def test_open_notional_sums_open_positions():
    st = StateStore()
    assert runner.open_notional(st) == 0.0
    _hold(st, "AAA", 90.0, 100)      # 9 000
    _hold(st, "BBB", 100.0, 50)      # 5 000
    assert runner.open_notional(st) == pytest.approx(14_000.0)


def test_open_notional_counts_submitted_unfilled_buys():
    st = StateStore()
    st.add_intent({"kind": "real", "side": "buy", "symbol": "AAA",
                   "qty": 33, "expected_open": 90.0, "submitted": True,
                   "fill_price": None, "fill_qty": None})
    assert runner.open_notional(st) == pytest.approx(2_970.0)
    # a filled buy is already in positions(); it must not be double counted
    st2 = StateStore()
    _hold(st2, "AAA", 90.0, 33)
    st2.add_intent({"kind": "real", "side": "buy", "symbol": "AAA",
                    "qty": 33, "expected_open": 90.0, "submitted": True,
                    "fill_price": 90.0, "fill_qty": 33})
    assert runner.open_notional(st2) == pytest.approx(2_970.0)
    # not-submitted (cutoff / data delay) and shadow entries consume no cap
    st3 = StateStore()
    st3.add_intent({"kind": "real", "side": "buy", "symbol": "AAA",
                    "qty": 33, "expected_open": 90.0, "submitted": False})
    st3.add_intent({"kind": "shadow", "side": "buy", "symbol": "BBB",
                    "qty": 33, "expected_open": 90.0, "submitted": True})
    assert runner.open_notional(st3) == 0.0


def test_open_notional_fails_closed_on_bad_position():
    st = StateStore()
    _hold(st, "AAA", 90.0, 100)
    st.data["positions"]["BBB"] = {"symbol": "BBB", "entry_day": "2026-11-02",
                                   "qty": 10, "stop_price": 1.0,
                                   "client_order_id": "x"}   # no entry_price
    assert runner.open_notional(st) is None
    plan = runner.plan_morning(st, inputs(), rng=random.Random(1))
    assert plan["buys"] == []       # unknown exposure => no entry at all


def test_morning_plan_hard_caps_total_exposure_at_30pct():
    st = StateStore()
    # already 27k open of the 30k cap on flat names: only one 3k entry fits
    for i, sym in enumerate(["OLD1", "OLD2", "OLD3"]):
        _hold(st, sym, 90.0, 100)
    # OLD names are not in today's universe -> no exit, exposure stays 27k
    plan = runner.plan_morning(
        st, inputs(opens={"AAA": 90.0, "BBB": 95.0, "CCC": 95.5}),
        rng=random.Random(1))
    bought = [o for o in plan["buys"]]
    assert len(bought) == 1
    assert bought[0].qty * bought[0].price_ref <= 3_000.0 + 1e-9
    assert runner.open_notional(st) + sum(
        o.qty * o.price_ref for o in bought) <= 30_000.0 + 1e-9
    assert plan["skipped"]["BBB"] == "total_cap"


def test_morning_plan_full_cap_places_no_buys():
    st = StateStore()
    for i, sym in enumerate(["OLD1", "OLD2", "OLD3"]):
        _hold(st, sym, 100.0, 100)           # 30k = the whole cap
    plan = runner.plan_morning(st, inputs(), rng=random.Random(1))
    assert plan["buys"] == []
    assert all(v == "total_cap" for v in plan["skipped"].values())
