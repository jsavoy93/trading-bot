"""early_stop: config/file flag only, scoped to this trial, no side effects."""

import json
from pathlib import Path

from experiments.gapfade_midcap_paper import early_stop
from experiments.gapfade_midcap_paper import state as state_mod


def _state(tmp_path, n_real, real_net, shadow_net):
    """n_real real fills and 3x that many shadows, priced to hit the nets."""
    st = state_mod.StateStore(tmp_path)
    st.load()
    for i in range(n_real):
        st.data["closed"].append({
            "symbol": f"R{i}", "entry_day": "2026-11-03",
            "entry_price": 100.0, "qty": 10, "exit_day": "2026-11-04",
            "exit_price": 100.0 + real_net / 10.0, "exit_reason": "profit"})
    for kind in ("random_clean", "vol_matched"):
        for i in range(n_real * 2):
            st.data["shadows"].append({
                "kind": kind, "symbol": f"{kind[:2]}{i}",
                "entry_day": "2026-11-03", "entry_price": 100.0, "qty": 10,
                "exit_day": "2026-11-04",
                "exit_price": 100.0 + shadow_net / 10.0,
                "exit_reason": "profit", "status": "closed"})
    st.save()
    return st


def test_running_before_the_fill_threshold(tmp_path):
    st = _state(tmp_path, 10, real_net=-1.0, shadow_net=1.0)
    d = early_stop.evaluate(st, cost_bps=0.0)
    assert d["real_filled"] == 10
    assert d["status"] == "running" and d["reasons"] == []


def test_trips_when_edge_vs_vol_matched_is_negative(tmp_path):
    st = _state(tmp_path, 160, real_net=-1.0, shadow_net=0.5)
    d = early_stop.evaluate(st, cost_bps=0.0)
    assert d["real_filled"] >= early_stop.EARLY_STOP_MIN_REAL_FILLS
    assert d["status"] == "tripped"
    assert d["edge_vs_vol_matched"] < 0
    assert "edge_vs_vol_matched_negative" in d["reasons"]


def test_stays_running_when_real_beats_shadows(tmp_path):
    st = _state(tmp_path, 160, real_net=2.0, shadow_net=0.5)
    assert early_stop.evaluate(st, cost_bps=0.0)["status"] == "running"


def test_flag_is_a_file_record_in_this_trials_state_only(tmp_path):
    st = _state(tmp_path, 160, real_net=-1.0, shadow_net=0.5)
    before = {p.name for p in Path(tmp_path).iterdir()}
    d = early_stop.evaluate(st, cost_bps=0.0)
    assert early_stop.record_if_tripped(st, d) is not None
    after = {p.name for p in Path(tmp_path).iterdir()}
    assert after - before <= {"state.json", "state.tmp"}
    saved = json.loads((Path(tmp_path) / "state.json").read_text())
    recs = saved["early_stops"]
    assert len(recs) == 1 and recs[0]["trial"] == "gapfade_midcap_paper"
    assert recs[0]["status"] == "tripped"
    # idempotent, and the record says a human decides
    assert early_stop.record_if_tripped(st, d) == recs[0]
    assert "human decides" in recs[0]["action"]
    # no kill-switch file, no process, no order keys are created anywhere
    assert not list(Path(tmp_path).glob("*.service"))
    assert not list(Path(tmp_path).glob("*kill*"))


def test_cli_prints_and_records_nothing_by_default(tmp_path, capsys):
    st = _state(tmp_path, 160, real_net=-1.0, shadow_net=0.5)
    del st
    assert early_stop.main(["--state-dir", str(tmp_path),
                            "--cost-bps", "0"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "tripped"
    saved = json.loads((Path(tmp_path) / "state.json").read_text())
    assert saved["early_stops"] == []          # print-only by default


def test_no_process_or_systemd_management_in_the_module():
    src = Path(early_stop.__file__).read_text()
    for banned in ("subprocess", "systemctl", "os.system", "kill(", "Popen",
                   "kill_switch", "unlink", "signal."):
        assert banned not in src
