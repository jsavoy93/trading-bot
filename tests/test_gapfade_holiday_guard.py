"""GAPFADE-MIDCAP-PAPER — holiday guard (fail closed) for BOTH passes.

No network: every test injects a fake Alpaca client (calendar rows) and a
fixed ET clock, and monkeypatches the data/broker builders so the only thing
under test is the runner's calendar gate. State lives in pytest tmp_path.
"""

import random
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.gapfade_midcap_paper import rules  # noqa: E402
from experiments.gapfade_midcap_paper import runner  # noqa: E402
from experiments.gapfade_midcap_paper import guards  # noqa: E402
from experiments.gapfade_midcap_paper.state import StateStore  # noqa: E402

ET = rules.ET
# the real live-path builders, captured before any fixture fakes them
_REAL_MORNING = runner._build_real_morning
_REAL_AFTER_CLOSE = runner._build_real_after_close


def _row(d, open_="09:30", close="16:00", early=False):
    row = {"date": d, "open": open_, "close": close}
    if early:
        row["close_early"] = True
    return row


# 2026-11-26 Thanksgiving (holiday), 11-27 early close 13:00,
# 11-25 Wednesday, 11-28/29 weekend (inside the +/-40d window).
CALENDAR = [
    _row("2026-11-23"), _row("2026-11-24"), _row("2026-11-25"),
    _row("2026-11-27", "09:30", "13:00", early=True),
    _row("2026-11-30"), _row("2026-12-01"),
]


class FakeCalendarClient:
    base = guards.PAPER_API_URL

    def __init__(self, rows=None, error=None):
        self.rows = CALENDAR if rows is None else rows
        self.error = error
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if self.error is not None:
            raise self.error
        return self.rows


class NoBroker:
    def __getattr__(self, name):
        raise AssertionError(f"broker must not be built ({name})")


def _et(s):
    hh, mm = map(int, s.split(":"))
    return datetime(2026, 11, 25, hh, mm, tzinfo=ET)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Patch make_client/make_broker/data builders; return a controller."""
    box = {"client": FakeCalendarClient(), "now": _et("09:20"),
           "builds": 0, "orders": []}

    monkeypatch.setattr(runner.broker_mod, "make_client",
                        lambda args=None, for_orders=True: box["client"])
    monkeypatch.setattr(runner.broker_mod, "make_broker",
                        lambda *a, **k: NoBroker())
    monkeypatch.setattr(runner.rules, "etc_now", lambda: box["now"])

    def fake_morning(args, state, client, now, session=None):
        box["builds"] += 1
        box["session"] = session
        return runner.RealMorningInputs(
            day=box["now"].date().isoformat(),
            trading_days=["2026-11-24", "2026-11-25"],
            universe=["AAA", "BBB"], prior_close={"AAA": 100.0, "BBB": 100.0},
            expected_open={"AAA": 90.0, "BBB": 90.0},
            expected_open_all={"AAA": 90.0, "BBB": 90.0},
            earnings=rules.EarningsCalendar({"AAA": {"2026-09-01"},
                                             "BBB": {"2026-09-01"}}),
            eligible_clean={"AAA", "BBB"}, vols={"AAA": 0.02, "BBB": 0.021},
            atr14={"AAA": 3.0, "BBB": 3.0},
            equity=100_000.0, buying_power=100_000.0,
            sent_at=box["now"].isoformat(), _data=object())

    def fake_after_close(args, state, client, now):
        box["builds"] += 1
        box["after_builds"] = box.get("after_builds", 0) + 1
        return runner.RealAfterCloseInputs(
            day=box["now"].date().isoformat(),
            trading_days=["2026-11-24", "2026-11-25"],
            bars={"AAA": {"open": 90.0, "close": 89.0, "high": 91.0,
                          "low": 88.0}},
            prior_close={"AAA": 100.0}, _data=object())

    def fake_execute_morning(state, plan, broker, data, inp, now=None):
        box["orders"].append(("morning", len(plan["sells"]) + len(plan["buys"])))
        return len(plan["sells"]) + len(plan["buys"])

    def fake_execute_after_close(state, plan, broker, data, inp):
        box["orders"].append(("after_close", 0))
        return {"pending_exits": len(plan["pending_exits"])}

    monkeypatch.setattr(runner, "_build_real_morning", fake_morning)
    monkeypatch.setattr(runner, "_build_real_after_close", fake_after_close)
    monkeypatch.setattr(runner, "execute_morning", fake_execute_morning)
    monkeypatch.setattr(runner, "execute_after_close",
                        fake_execute_after_close)
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("GAPFADE_MIDCAP_KILL_FILE",
                       str(tmp_path / "no-kill-switch"))
    box["state_dir"] = str(tmp_path / "state")
    return box


def _run(box, pass_name, *extra):
    return runner.main(["--execute", "--pass", pass_name,
                        "--state-dir", box["state_dir"], *extra])


# --- criterion 1: non-trading day (holiday + weekend) --------------------
@pytest.mark.parametrize("pass_name", ["morning", "after_close"])
@pytest.mark.parametrize("day_time", [("2026-11-26", "09:20"),   # holiday
                                      ("2026-11-28", "09:20"),   # Saturday
                                      ("2026-11-29", "16:05")])  # Sunday
def test_non_trading_day_both_passes_exit_0_zero_orders(
        rig, capsys, pass_name, day_time):
    d, t = day_time
    y, m, dd = map(int, d.split("-"))
    hh, mm = map(int, t.split(":"))
    rig["now"] = datetime(y, m, dd, hh, mm, tzinfo=ET)
    rc = _run(rig, pass_name)
    err = capsys.readouterr().err
    assert rc == 0
    assert rig["orders"] == [] and rig["builds"] == 0
    assert f"{d} is not an Alpaca trading day" in err
    assert err.count("is not an Alpaca trading day") == 1


# --- criterion 2: calendar unavailable -> fail closed --------------------
@pytest.mark.parametrize("pass_name", ["morning", "after_close"])
@pytest.mark.parametrize("bad", ["raise", "empty", "none",
                                 "no_close", "garbage"])
def test_calendar_unavailable_fails_closed(rig, capsys, pass_name, bad):
    if bad == "raise":
        rig["client"] = FakeCalendarClient(error=OSError("calendar down"))
    elif bad == "empty":
        rig["client"] = FakeCalendarClient(rows=[])
    elif bad == "none":
        rig["client"] = FakeCalendarClient(rows=None)  # -> CALENDAR default
        rig["client"].rows = None

        def get(url, params=None):
            return None
        rig["client"].get = get
    elif bad == "no_close":
        rig["client"] = FakeCalendarClient(rows=[{"date": "2026-11-25"}])
    else:
        rig["client"] = FakeCalendarClient(rows=["not-a-row"])
    if bad == "none":
        rig["now"] = _et("16:05")
    rc = _run(rig, pass_name)
    err = capsys.readouterr().err
    assert rc == 1, err
    assert rig["orders"] == [] and rig["builds"] == 0
    assert "FAIL CLOSED" in err


# --- criterion 3: belt and braces -- resolved session date != today ET --
# The guard already proves today is a session; this isolates the builders'
# own second check by letting the REAL `_build_real_morning` /
# `_build_real_after_close` run with only their data layers faked.
def _fake_marketdata(monkeypatch, box):
    from experiments.gapfade_midcap_paper import marketdata

    real_cls = marketdata.AlpacaMarketData

    class FakeData(real_cls):
        def __init__(self, client, cache_dir, **kw):
            super().__init__(client, cache_dir, today=date(2026, 11, 25))
            self._members = ["AAA", "BBB"]
            self.bars = {s: [{"date": d, "open": 100.0, "high": 101.0,
                              "low": 99.0, "close": 100.0, "volume": 1e6}
                             for d in ("2026-11-20", "2026-11-23",
                                       "2026-11-24")]
                        for s in ("AAA", "BBB")}

        def members(self):
            return ["AAA", "BBB"]

        def load_day(self, day, symbols=None, with_quotes=False):
            box["loads"] = box.get("loads", []) + [day]

        def eligible_clean(self, day, calendar):
            return {"AAA", "BBB"}

        def vols(self, day, symbols):
            return {"AAA": 0.02, "BBB": 0.021}

        def atr14(self, symbol, day):
            return 3.0

        def expected_open(self, day):
            return {"AAA": 90.0, "BBB": 90.0}

    monkeypatch.setattr(marketdata, "AlpacaMarketData", FakeData)
    box["client"].account = lambda: {"equity": 100_000, "buying_power": 100_000}
    from experiments.gapfade_midcap_paper import earnings
    monkeypatch.setattr(earnings, "load_calendar",
                        lambda cache, universe, now=None:
                        (rules.EarningsCalendar({"AAA": {"2026-09-01"},
                                                 "BBB": {"2026-09-01"}}),
                         []))


def test_morning_builder_refuses_when_resolved_day_is_not_today(
        rig, capsys, monkeypatch):
    _fake_marketdata(monkeypatch, rig)
    monkeypatch.setattr(runner, "_build_real_morning", _REAL_MORNING)
    monkeypatch.setattr(runner, "_resolve_day",
                        lambda args_day, client, now: "2026-11-24")
    rc = _run(rig, "morning")
    assert rc == 0
    assert rig["orders"] == []
    assert "loads" not in rig                     # stopped before any data
    assert "!= today 2026-11-25" in capsys.readouterr().err


def test_after_close_builder_refuses_when_resolved_day_is_not_today(
        rig, capsys, monkeypatch):
    _fake_marketdata(monkeypatch, rig)
    monkeypatch.setattr(runner, "_build_real_after_close",
                        _REAL_AFTER_CLOSE)
    rig["now"] = _et("16:05")
    monkeypatch.setattr(runner, "_resolve_day",
                        lambda args_day, client, now: "2026-11-24")
    rc = _run(rig, "after_close")
    assert rc == 0
    assert rig["orders"] == []
    assert "loads" not in rig
    assert "!= today 2026-11-25" in capsys.readouterr().err


# --- criterion 4: "already ran" stops order submission ------------------
def test_already_ran_morning_stops_and_force_pass_overrides(rig, capsys):
    st = StateStore(rig["state_dir"])
    st.load()
    st.record_pass("2026-11-25", "morning", "2026-11-25T09:21:00-05:00")
    st.save()
    rc = _run(rig, "morning")
    err = capsys.readouterr().err
    assert rc == 0
    assert rig["orders"] == []
    assert "already ran" in err
    # the existing re-run flag still overrides
    rc = _run(rig, "morning", "--force-pass")
    assert rc == 0
    assert [p for p, _ in rig["orders"]] == ["morning"]


def test_already_ran_after_close_stops_and_force_pass_overrides(rig, capsys):
    rig["now"] = _et("16:05")
    st = StateStore(rig["state_dir"])
    st.load()
    st.record_pass("2026-11-25", "after_close", "2026-11-25T16:01:00-05:00")
    st.save()
    rc = _run(rig, "after_close")
    err = capsys.readouterr().err
    assert rc == 0
    assert rig["orders"] == []
    assert "already ran" in err
    rc = _run(rig, "after_close", "--force-pass")
    assert rc == 0
    assert [p for p, _ in rig["orders"]] == ["after_close"]


def test_second_execute_run_same_day_is_idempotent(rig):
    assert _run(rig, "morning") == 0
    first = list(rig["orders"])
    assert first and first[0][1] > 0
    assert _run(rig, "morning") == 0
    assert rig["orders"] == first                    # zero new orders


# --- criterion 5: early close uses the calendar's real close time -------
def test_after_close_on_early_close_day_waits_for_13_00_et(rig, capsys):
    rig["now"] = datetime(2026, 11, 27, 12, 45, tzinfo=ET)
    rc = _run(rig, "after_close")
    err = capsys.readouterr().err
    assert rc == 0 and rig["orders"] == []
    assert "market still open until 13:00 ET" in err and "early close" in err
    # early close: zero orders and NO data loads before the real 13:00 close
    # (the gate runs ahead of the after_close data build)
    assert "after_builds" not in rig


def test_after_close_on_early_close_day_runs_at_13_00_et(rig):
    rig["now"] = datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    assert _run(rig, "after_close") == 0
    assert [p for p, _ in rig["orders"]] == ["after_close"]


def test_after_close_normal_day_still_waits_for_16_00_et(rig, capsys):
    rig["now"] = _et("15:59")
    assert _run(rig, "after_close") == 0
    assert rig["orders"] == []
    assert "until 16:00 ET" in capsys.readouterr().err


def test_morning_on_early_close_day_runs_normally(rig):
    rig["now"] = datetime(2026, 11, 27, 9, 21, tzinfo=ET)
    assert _run(rig, "morning") == 0
    assert [p for p, _ in rig["orders"]] == ["morning"]


# --- criterion 6: normal day unchanged ----------------------------------
def test_normal_day_morning_submits_and_matches_plan(rig):
    rc = _run(rig, "morning")
    assert rc == 0
    # the plan (rules untouched) still produces its usual 2 entries
    st = StateStore(rig["state_dir"])
    st.load()
    assert st.pass_done("2026-11-25", "morning")
    assert rig["orders"] == [("morning", 2)]


def test_normal_day_after_close_submits(rig):
    rig["now"] = _et("16:05")
    assert _run(rig, "after_close") == 0
    assert [p for p, _ in rig["orders"]] == ["after_close"]


# --- calendar read uses the existing PAPER client, no fallback date -----
def test_calendar_is_read_from_v2_calendar_via_client(rig):
    _run(rig, "morning")
    url, params = rig["client"].calls[0]
    assert url == f"{guards.PAPER_API_URL}/v2/calendar"
    assert params["start"] <= "2026-11-25" <= params["end"]


def test_dry_run_on_holiday_also_skips_with_zero_orders(rig, capsys):
    rig["now"] = datetime(2026, 11, 26, 9, 20, tzinfo=ET)
    rc = runner.main(["--pass", "morning", "--state-dir", rig["state_dir"]])
    assert rc == 0
    assert rig["orders"] == [] and rig["builds"] == 0
    assert "2026-11-26 is not an Alpaca trading day" in capsys.readouterr().err
