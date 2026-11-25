"""GAPFADE-MIDCAP-PAPER part 2 — Alpaca PAPER client, broker, data, sources.

Everything here is offline: a fake requests session returns canned JSON, the
membership and earnings sources are exercised through their injectable
fetchers, and NO test reads a real .env, prints a credential or touches the
network.
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.gapfade_midcap_paper import (  # noqa: E402
    alpaca, earnings, guards, marketdata, membership, runner, rules)
from experiments.gapfade_midcap_paper import broker  # noqa: E402


# --- fakes ---------------------------------------------------------------
class Resp:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise alpaca.AlpacaError(f"HTTP {self.status_code}")


class FakeSession:
    """Records every request; answers from `routes` (method,url)->callable."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None,
                timeout=None):
        self.calls.append({"method": method, "url": url, "params": params,
                           "json": json, "headers": headers})
        for (m, frag), fn in self.routes.items():
            if m == method and frag in url:
                return fn(params, json)
        return Resp(404, {"message": "not found"})


def client(routes=None, **kw):
    return alpaca.AlpacaPaperClient("test-key", "test-secret",
                                    session=FakeSession(routes), **kw)


# --- guards / URL lock ---------------------------------------------------
def test_client_refuses_any_other_base_url():
    for url in ("https://api.alpaca.markets",
                "https://paper-api.alpaca.markets.evil.example",
                "https://paper-api.alpaca.markets/v2 ",):
        with pytest.raises(alpaca.AlpacaError):
            alpaca.AlpacaPaperClient("k", "s", session=FakeSession(),
                                     base_url=url)


def test_live_endpoint_env_cannot_switch_the_url():
    """The URL is a constant: an env var (even a live-looking one) is inert."""
    import os
    os.environ["ALPACA_TRADING_URL"] = "https://api.alpaca.markets"
    try:
        with pytest.raises(guards.GuardError):
            guards.check_paper_only(env={"TRADING_BOT_PAPER_ONLY": "1"},
                                    base_url="https://api.alpaca.markets")
    finally:
        os.environ.pop("ALPACA_TRADING_URL", None)


def test_order_body_is_opg_market_and_lookup_by_client_id(tmp_path,
                                                         monkeypatch):
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("GAPFADE_MIDCAP_KILL_FILE", str(tmp_path / "kill"))
    sess_routes = {
        ("POST", "/v2/orders"): lambda p, b: Resp(200, {
            "id": "o-1", "client_order_id": b["client_order_id"],
            "status": "accepted"}),
        ("GET", "orders:by_client_order_id"): lambda p, b: Resp(200, {
            "id": "o-1", "status": "filled", "filled_qty": "5",
            "filled_avg_price": "10.5"}),
    }
    c = client(sess_routes)
    order = c.submit_order({"symbol": "AAA", "qty": "5", "side": "buy",
                            "type": "market", "time_in_force": "opg",
                            "client_order_id": "gf_mid-AAA-buy-20261102-1"})
    assert order["id"] == "o-1"
    sent = [call for call in c.sess.calls if call["method"] == "POST"][0]
    assert sent["json"]["type"] == "market"
    assert sent["json"]["time_in_force"] == "opg"
    got = c.order_by_client_id("gf_mid-AAA-buy-20261102-1")
    assert got["status"] == "filled"
    assert c.sess.calls[-1]["params"] == {
        "client_order_id": "gf_mid-AAA-buy-20261102-1"}


def test_submit_without_prefix_refused_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("GAPFADE_MIDCAP_KILL_FILE", str(tmp_path / "kill"))
    c = client({})
    with pytest.raises(alpaca.AlpacaError):
        c.submit_order({"symbol": "AAA", "qty": "1", "side": "buy",
                        "type": "market", "time_in_force": "opg",
                        "client_order_id": "rsi2-AAA"})
    assert c.sess.calls == []


def test_auth_error_is_not_retried():
    c = client({("GET", "/v2/account"): lambda p, b: Resp(401, {})})
    with pytest.raises(alpaca.AuthError):
        c.account()


def test_credentials_from_env_never_from_missing_file(tmp_path):
    env = {"ALPACA_API_KEY": "k1", "ALPACA_API_SECRET": "s1"}
    assert alpaca.load_paper_credentials(env=env) == ("k1", "s1")
    with pytest.raises(alpaca.AuthError):
        alpaca.load_paper_credentials(env={},
                                      env_file=tmp_path / "missing.env")


def test_credentials_read_from_env_file_style(tmp_path):
    f = tmp_path / "p.env"
    f.write_text("# comment\nALPACA_API_KEY=kk\nALPACA_API_SECRET='ss'\n")
    assert alpaca.load_paper_credentials(env={}, env_file=f) == ("kk", "ss")


# --- PaperBroker ---------------------------------------------------------
class FakeAccountClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, body):
        self.submitted.append(body)
        return {"id": f"o-{len(self.submitted)}", "status": "accepted",
                "client_order_id": body["client_order_id"]}

    def order_by_client_id(self, coid):
        return None

    def account(self):
        return {"buying_power": "1000.0", "equity": "2500.0"}


@pytest.fixture()
def paper_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("GAPFADE_MIDCAP_KILL_FILE", str(tmp_path / "kill"))
    return tmp_path


def test_paper_broker_submits_opg_orders(paper_env):
    cl = FakeAccountClient()
    pb = broker.PaperBroker(cl)
    out = pb.submit_opg_buy("AAA", 3, "gf_mid-AAA-buy-20261102-1")
    assert out["status"] == "accepted"
    assert cl.submitted[0]["side"] == "buy"
    assert pb.submit_opg_sell("AAA", 3, "gf_mid-AAA-sell-20261103-1")
    assert cl.submitted[1]["side"] == "sell"
    assert cl.submitted[1]["time_in_force"] == "opg"
    assert pb.equity() == 2500.0 and pb.buying_power() == 1000.0


def test_paper_broker_requires_paper_env(paper_env, monkeypatch):
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY")
    cl = FakeAccountClient()
    pb = broker.PaperBroker(cl)
    with pytest.raises(guards.GuardError):
        pb.submit_opg_buy("AAA", 1, "gf_mid-AAA-buy-20261102-1")
    assert cl.submitted == []


def test_paper_broker_requires_paper_floor_qty(paper_env):
    cl = FakeAccountClient()
    pb = broker.PaperBroker(cl)
    for qty in (0, -1, 1.5, "2"):
        with pytest.raises(alpaca.AlpacaError):
            pb.submit_opg_buy("AAA", qty, "gf_mid-AAA-buy-20261102-1")
    assert cl.submitted == []


def test_paper_broker_kill_switch_blocks_orders(paper_env):
    (paper_env / "kill").write_text("stop")
    cl = FakeAccountClient()
    pb = broker.PaperBroker(cl)
    with pytest.raises(guards.GuardError):
        pb.submit_opg_buy("AAA", 1, "gf_mid-AAA-buy-20261102-1")
    assert cl.submitted == []


# --- membership (MDY holdings CSV) ---------------------------------------
MDY_CSV = """Fund Name,SPDR S&P MidCap 400 ETF Trust
Ticker,Name,Weight,Sector
AAA,A Corp,1.1,Industrials
BBB,B Corp,0.9,Financials
CCC,C Corp,0.5,Health Care
-,US Dollar,0.01,Cash
US Dollar,Cash line,0.0,Cash
DDD,D Corp,0.2,Utilities
"""


def test_membership_parses_mdy_payload():
    got = membership.parse_holdings(MDY_CSV)
    assert got == ["AAA", "BBB", "CCC", "DDD"]
    assert "US" not in got


def test_membership_rejects_garbage():
    with pytest.raises(membership.MembershipError):
        membership.parse_holdings("no ticker here\n")


def test_membership_caches_daily_and_falls_back(tmp_path):
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return MDY_CSV

    d = date(2026, 11, 24)
    assert membership.load_members(tmp_path, fetch=fetch, today=d) == [
        "AAA", "BBB", "CCC", "DDD"]
    assert calls["n"] == 1
    # same day: served from the cache
    membership.load_members(tmp_path, fetch=fetch, today=d)
    assert calls["n"] == 1

    def boom():
        raise OSError("offline")

    # download failure one day later -> the fresh cached copy is used
    assert membership.load_members(tmp_path, fetch=boom,
                                   today=date(2026, 11, 25)) == [
        "AAA", "BBB", "CCC", "DDD"]


def test_membership_refuses_stale_copy(tmp_path):
    cache = tmp_path / "mdy_holdings.csv"
    cache.write_text(MDY_CSV)
    import os
    age = 11 * 86400
    os.utime(cache, (cache.stat().st_atime - age, cache.stat().st_mtime - age))

    def boom():
        raise OSError("offline")

    with pytest.raises(membership.MembershipError):
        membership.load_members(tmp_path, fetch=boom, today=date(2026, 11, 25))


# --- earnings calendar ---------------------------------------------------
def test_earnings_cache_and_fail_closed(tmp_path):
    now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    got = {"AAA": ["2026-11-26"], "BBB": ["2026-10-01"]}
    fetched = []

    def fetch_one(sym):
        fetched.append(sym)
        if sym == "CCC":
            raise RuntimeError("vendor down")
        return got.get(sym, [])

    cal, missing = earnings.load_calendar(tmp_path, ["AAA", "BBB", "CCC"],
                                          now=now, fetch_one=fetch_one)
    assert missing == ["CCC"]
    assert cal.coverage("AAA") and cal.coverage("BBB")
    assert not cal.coverage("CCC")          # fail closed -> skipped
    # cached inside the refresh window -> no refetch of covered names
    fetched.clear()
    earnings.load_calendar(tmp_path, ["AAA", "BBB"], now=now,
                           fetch_one=fetch_one)
    assert fetched == []


def test_earnings_skip_window_via_calendar(tmp_path):
    now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    cal, _ = earnings.load_calendar(
        tmp_path, ["AAA"], now=now,
        fetch_one=lambda s: ["2026-11-25"])
    assert cal.coverage("AAA")
    days = [date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26)]
    assert rules.earnings_skip("AAA", date(2026, 11, 24), cal, days) is True


# --- market data ---------------------------------------------------------
def _bars_payload(sym, rows):
    return {sym: [{"t": f"{d}T04:00:00Z", "o": o, "h": hi, "l": lo, "c": c,
                   "v": 1000}
                  for d, o, hi, lo, c in rows]}


def test_marketdata_prior_close_open_and_atr():
    rows = [("2026-11-20", 10.0, 11.0, 9.5, 10.5),
            ("2026-11-23", 10.5, 11.5, 10.0, 11.0),
            ("2026-11-24", 11.0, 12.0, 10.5, 11.8)]
    routes = {
        ("GET", "/v2/stocks/bars"): lambda p, b: Resp(200, {
            "bars": _bars_payload("AAA", rows)}),
        ("GET", "/v1beta1/stocks/snapshots"): lambda p, b: Resp(200, {
            "snapshots": {"AAA": {"latestTrade": {"p": 10.2}}}}),
    }
    data = marketdata.AlpacaMarketData(client(routes), cache_dir="/tmp/x",
                                       members=["AAA"], today=date(2026, 11, 25))
    data.load_day("2026-11-25")
    assert data.prior_close("2026-11-25") == {"AAA": 11.8}
    assert data.expected_open("2026-11-25") == {"AAA": 10.2}
    trs = data.true_ranges("AAA", "2026-11-25")
    assert len(trs) == 3
    assert trs[0] == pytest.approx(1.5)          # first bar: high-low only
    assert data.official_bar("AAA", "2026-11-24")["open"] == 11.0


def test_marketdata_official_bar_missing_raises():
    data = marketdata.AlpacaMarketData(client({}), cache_dir="/tmp/x",
                                       members=["AAA"])
    with pytest.raises(marketdata.MarketDataError):
        data.official_bar("AAA", "2026-11-24")


def test_eligible_clean_requires_calendar_and_bars():
    rows = [("2026-11-24", 10.0, 11.0, 9.0, 10.5)]
    routes = {
        ("GET", "/v2/stocks/bars"): lambda p, b: Resp(200, {
            "bars": _bars_payload("AAA", rows)}),
        ("GET", "/v1beta1/stocks/snapshots"): lambda p, b: Resp(
            200, {"snapshots": {}}),
    }
    data = marketdata.AlpacaMarketData(client(routes), cache_dir="/tmp/x",
                                       members=["AAA", "BBB"],
                                       today=date(2026, 11, 25))
    data.load_day("2026-11-25")
    cal = rules.EarningsCalendar({"AAA": set(), "BBB": set()})
    # BBB has no bars -> excluded; AAA covered + clean -> eligible
    assert data.eligible_clean("2026-11-25", cal) == ["AAA"]
    # no calendar at all -> nobody is eligible (fail closed)
    assert data.eligible_clean("2026-11-25", None) == []
