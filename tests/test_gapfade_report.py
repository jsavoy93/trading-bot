"""report_pnl: real vs both shadows, cost configurable, paper records only."""

import json

import pytest

from experiments.gapfade_midcap_paper import report_pnl
from experiments.gapfade_midcap_paper import state as state_mod


def _seed(tmp_path):
    st = state_mod.StateStore(tmp_path)
    st.load()
    st.data["closed"] = [
        {"symbol": "AAA", "entry_day": "2026-11-03", "entry_price": 100.0,
         "qty": 10, "exit_day": "2026-11-05", "exit_price": 102.0,
         "exit_reason": "profit", "entry_client_order_id": "gf_mid-AAA-buy-1"},
        {"symbol": "BBB", "entry_day": "2026-11-03", "entry_price": 50.0,
         "qty": 20, "exit_day": "2026-11-06", "exit_price": 49.0,
         "exit_reason": "stop", "entry_client_order_id": "gf_mid-BBB-buy-1"},
    ]
    st.data["shadows"] = [
        {"kind": "random_clean", "symbol": "R1", "entry_day": "2026-11-03",
         "entry_price": 100.0, "qty": 10, "exit_day": "2026-11-04",
         "exit_price": 101.0, "exit_reason": "profit", "status": "closed"},
        {"kind": "random_clean", "symbol": "R2", "entry_day": "2026-11-03",
         "entry_price": 100.0, "qty": 10, "exit_day": "2026-11-06",
         "exit_price": 99.0, "exit_reason": "time_cap", "status": "closed"},
        {"kind": "vol_matched", "symbol": "V1", "entry_day": "2026-11-03",
         "entry_price": 100.0, "qty": 10, "exit_day": "2026-11-04",
         "exit_price": 103.0, "exit_reason": "profit", "status": "closed"},
        # still open: counted as a trade but never in the averages
        {"kind": "vol_matched", "symbol": "V2", "entry_day": "2026-11-05",
         "entry_price": None, "qty": 10, "status": "open"},
    ]
    st.save()
    return st


def test_report_covers_real_and_both_shadows_with_default_cost(tmp_path):
    _seed(tmp_path)
    st = state_mod.StateStore(tmp_path)
    st.load()
    rep = report_pnl.build_report(st, report_pnl.rules.DEFAULT_COST_BPS_PER_SIDE)
    assert rep["cost_bps_per_side"] == 27.0
    assert set(rep["groups"]) == {"real", "random_clean", "vol_matched"}
    real = rep["groups"]["real"]
    assert real["trades"] == 2 and real["filled"] == 2
    # 2 trades, 1 win: 10*(102-100) - 0.27% of 1000*2 sides = +14.60
    #               20*(49-50)  - 0.27% of 1000*2 sides = -25.40
    assert real["wins"] == 1 and real["win_rate"] == 0.5
    assert real["mean_net_per_trade"] == pytest.approx(-5.40)
    assert real["cumulative_net"] == pytest.approx(-10.80)
    vols = rep["groups"]["vol_matched"]
    assert vols["trades"] == 2 and vols["filled"] == 1 and vols["open"] == 1
    assert vols["cumulative_net"] == pytest.approx(24.60)
    # edge = real minus shadow over the same window
    assert rep["edge_vs_vol_matched"] == pytest.approx(-35.40)
    assert rep["edge_vs_random_clean"] == pytest.approx(
        real["cumulative_net"] - rep["groups"]["random_clean"]
        ["cumulative_net"])


def test_cost_is_configurable_and_changes_net_only(tmp_path):
    _seed(tmp_path)
    st = state_mod.StateStore(tmp_path)
    st.load()
    free = report_pnl.build_report(st, 0.0)
    research = report_pnl.build_report(st, 10.0)
    assert free["groups"]["real"]["cumulative_net"] == pytest.approx(0.0)
    # 2 trades x 1000 notional x 2 sides x 10bps
    assert research["groups"]["real"]["cumulative_net"] == pytest.approx(-4.0)
    assert (research["groups"]["real"]["wins"]
            == free["groups"]["real"]["wins"] == 1)


def test_report_reads_only_the_paper_trial_state(tmp_path, capsys):
    st = _seed(tmp_path)
    rc = report_pnl.main(["--state-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "edge vs vol_matched" in out and "real" in out
    assert "random_clean" in out and "27 bps" in out
    # no database is ever opened by this script
    assert "trading_bot.db" not in out
    assert st.path.name == "state.json"


def test_report_json_is_machine_readable(tmp_path, capsys):
    _seed(tmp_path)
    assert report_pnl.main(["--state-dir", str(tmp_path), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["groups"]["real"]["trades"] == 2
