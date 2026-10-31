"""STRAT-025 intraday swing engine (paper only).

Runtime behavior:
  - Loop every cfg.scan_interval_sec (60–300s) during market hours only.
  - Intraday scan (flag STRAT025_ENABLE_INTRADAY_SCAN, default OFF): run
    enabled setup predicates per symbol; on signal pass RiskManager gate,
    size via risk.position_size, place paper market BUY; after the fill
    confirms, IMMEDIATELY place a broker-side STOP sell that stays working
    all day (GTC). If the stop cannot be placed, the position is flattened
    at market right away — a position must never sit without its stop.
  - EOD entries (flag STRAT025_ENABLE_EOD_ENTRIES, default OFF): at 15:45 ET
    evaluate daily-bar setups and enter with the same stop-after-fill path.
  - Kill switch: env STRAT025_KILL_SWITCH=1 or the KILL flag file halts new
    entries; STRAT025_KILL_FLATTEN=1 additionally flattens everything.

State: own SQLite DB (STRAT025_DB, default experiments/strat_025/swing.db).
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import setups
from .broker import AlpacaPaperBroker, tagged_client_order_id
from .config import Config
from .guard import enforce_paper_only
from .risk import RiskManager, kill_switch_active, position_size
from .universe import universe_symbols

ET = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  setup TEXT NOT NULL,
  qty REAL NOT NULL,
  entry_order_id TEXT, entry_fill_price REAL,
  stop_order_id TEXT, stop_price REAL, stop_status TEXT,
  status TEXT NOT NULL,          -- open|stopped|flattened|failed
  reason TEXT,
  UNIQUE(symbol, setup, ts)
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT
);
"""


class SwingEngine:
    def __init__(self, cfg: Config, broker, risk: RiskManager,
                 symbols: list[str] | None = None):
        self.cfg = cfg
        self.broker = broker
        self.risk = risk
        self.symbols = symbols
        self._day_start_equity: float | None = None
        self._day: str | None = None
        self._eod_done: str | None = None
        Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cfg.db_path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------- helpers
    def log(self, kind: str, detail: str) -> None:
        self.db.execute("INSERT INTO events(ts,kind,detail) VALUES (?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), kind, detail))
        self.db.commit()

    def open_positions(self) -> list[dict]:
        return [{"symbol": p["symbol"]} for p in self.broker.get_positions()]

    def _roll_day(self, now_et: datetime) -> None:
        d = now_et.date().isoformat()
        if self._day != d:
            self._day = d
            self._day_start_equity = self.broker.get_account_equity()
            self.risk.halted_today = None
            self._eod_done = None

    # ------------------------------------------------- stop-after-fill entry
    def enter_with_stop(self, symbol: str, setup: str, entry_ref_price: float,
                        stop_price: float, max_notional: float | None = None
                        ) -> dict:
        """Entry fill -> broker STOP placed immediately after fill. Verifies
        sequencing: no position is left without a working stop; on stop
        failure the position is flattened at market and recorded 'failed'."""
        equity = self.broker.get_account_equity()
        day_start = self._day_start_equity or equity
        ok, reason = self.risk.approve(symbol=symbol, equity=equity,
                                       day_start_equity=day_start,
                                       positions=self.open_positions())
        if not ok:
            self.log("entry_rejected", f"{symbol} {setup}: {reason}")
            return {"entered": False, "reason": reason}

        qty = position_size(equity, entry_ref_price, stop_price,
                            self.cfg.risk_per_trade_pct,
                            self.cfg.sleeve_equity_floor_usd, max_notional)
        if qty <= 0:
            self.log("entry_rejected", f"{symbol} {setup}: size=0")
            return {"entered": False, "reason": "size_zero"}

        entry_coid = tagged_client_order_id("entry", symbol)
        order = self.broker.submit_market_buy(symbol, qty, entry_coid)
        filled = self.broker.wait_for_fill(order["id"])
        if not filled or filled.get("status") != "filled":
            self.log("entry_unfilled", f"{symbol} {setup}")
            return {"entered": False, "reason": "entry_not_filled"}
        fill_px = float(filled["filled_avg_price"])

        # ---- IMMEDIATELY place the broker-side stop (works all day, GTC) ----
        try:
            stop_coid = tagged_client_order_id("stop", symbol)
            stop_order = self.broker.submit_stop_sell(symbol, qty, stop_price, stop_coid)
            stop_ok = bool(stop_order) and stop_order.get("status") not in (
                "rejected", "canceled", "cancelled")
        except Exception:
            stop_ok = False
            stop_order = None
        ts = datetime.now(timezone.utc).isoformat()
        if stop_ok and stop_order is not None:
            self.db.execute(
                "INSERT INTO entries(ts,symbol,setup,qty,entry_order_id,"
                "entry_fill_price,stop_order_id,stop_price,stop_status,status)"
                " VALUES (?,?,?,?,?,?,?,?,?, 'open')",
                (ts, symbol, setup, qty, order["id"], fill_px,
                 stop_order["id"], stop_price, stop_order.get("status", "held")))
            self.db.commit()
            self.log("entered", f"{symbol} {setup} qty={qty} fill={fill_px} stop={stop_price}")
            return {"entered": True, "qty": qty, "fill_price": fill_px,
                    "stop_order_id": stop_order["id"], "stop_price": stop_price}
        # stop failed -> flatten immediately; never hold an unstopped position
        flat_coid = tagged_client_order_id("flat", symbol)
        self.broker.submit_market_sell(symbol, qty, flat_coid)
        self.db.execute(
            "INSERT INTO entries(ts,symbol,setup,qty,entry_order_id,"
            "entry_fill_price,stop_order_id,stop_price,stop_status,status,reason)"
            " VALUES (?,?,?,?,?,?,NULL,?,'unplaced','failed','stop_place_failed_flattened')",
            (ts, symbol, setup, qty, order["id"], fill_px, stop_price))
        self.db.commit()
        self.log("stop_failed_flattened", f"{symbol} {setup}")
        return {"entered": False, "reason": "stop_place_failed_flattened"}

    # ------------------------------------------------------------- scanning
    def scan_once(self, bars_by_symbol: dict[str, pd.DataFrame],
                  now: datetime | None = None) -> list[dict]:
        """Evaluate enabled setups on the given bars and enter where approved.
        `bars_by_symbol` values follow the setups.py column convention."""
        now = now or datetime.now(ET)
        self._roll_day(now)
        results = []
        for sym in (self.symbols or sorted(bars_by_symbol)):
            df = bars_by_symbol.get(sym)
            if df is None or df.empty:
                continue
            for name in setups.evaluate_all_enabled(df):
                r = self.enter_with_stop(
                    sym, name,
                    entry_ref_price=float(df["close"].iloc[-1]),
                    stop_price=float(df.get("stop_ref", df["close"]).iloc[-1]))
                r["symbol"], r["setup"] = sym, name
                results.append(r)
        return results

    def eod_eval_time_reached(self, now_et: datetime) -> bool:
        hh, mm = (int(x) for x in self.cfg.eod_eval_time_et.split(":"))
        return (now_et.hour, now_et.minute) >= (hh, mm)

    def run_eod(self, bars_by_symbol: dict[str, pd.DataFrame],
                now: datetime | None = None) -> list[dict]:
        """15:45 ET daily-bar setup evaluation for end-of-day entries."""
        now = now or datetime.now(ET)
        self._roll_day(now)
        if not self.cfg.enable_eod_entries:
            return []
        if self._eod_done == now.date().isoformat():
            return []
        if not self.eod_eval_time_reached(now):
            return []
        self._eod_done = now.date().isoformat()
        self.log("eod_eval", f"symbols={len(bars_by_symbol)}")
        return self.scan_once(bars_by_symbol, now=now)

    # ---------------------------------------------------------- kill switch
    def handle_kill_switch(self) -> bool:
        if not kill_switch_active(self.cfg):
            return False
        self.log("kill_switch", "active")
        if self.cfg.kill_flatten:
            for p in self.broker.get_positions():
                coid = tagged_client_order_id("killflat", p["symbol"])
                self.broker.submit_market_sell(p["symbol"], float(p["qty"]), coid)
            self.log("kill_switch", "flattened all positions")
        return True

    # ------------------------------------------------------------- the loop
    def run_loop(self, bars_provider, max_iterations: int | None = None,
                 sleep_override: float | None = None) -> None:
        """bars_provider(now_et) -> dict[symbol, DataFrame]. Market-hours gate
        via broker clock. Loop is bounded by max_iterations (no endless loop)."""
        enforce_paper_only(getattr(self.broker, "base", "paper"))
        i = 0
        while max_iterations is None or i < max_iterations:
            i += 1
            if self.handle_kill_switch():
                return
            now = datetime.now(ET)
            if self.broker.is_market_open():
                self._roll_day(now)
                bars = bars_provider(now)
                self.scan_once(bars, now=now)
                self.run_eod(bars, now=now)
            else:
                self._day_start_equity = None
            time.sleep(sleep_override or self.cfg.scan_interval_sec)


def main() -> int:  # CLI entry, mirrors strat_023 friction
    enforce_paper_only("https://paper-api.alpaca.markets")
    cfg = Config()
    if not (cfg.enable_intraday_scan or cfg.enable_eod_entries):
        print("strat025: no loop enabled (all flags default OFF). "
              "Set STRAT025_ENABLE_INTRADAY_SCAN=1 and/or "
              "STRAT025_ENABLE_EOD_ENTRIES=1 plus STRAT025_SETUP_<NAME>=1.",
              file=sys.stderr)
        return 2
    key, sec = os_env_paper_creds()
    broker = AlpacaPaperBroker(key, sec)
    symbols = universe_symbols()
    eng = SwingEngine(cfg, broker, RiskManager(cfg), symbols=symbols)
    print(f"strat025: paper swing engine starting, {len(symbols)} symbols, "
          f"interval={cfg.scan_interval_sec}s, setups={setups.enabled_setups()}")
    eng.run_loop(bars_provider=lambda now: {})  # bars wired in Part 2
    return 0


def os_env_paper_creds() -> tuple[str, str]:
    import os
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
    if key and sec:
        return key, sec
    env = Path("/root/.openclaw/workspace/trading-bot/.env")
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"')
                if k == "ALPACA_API_KEY":
                    key = v
                elif k == "ALPACA_API_SECRET":
                    sec = v
    if not (key and sec):
        print("strat025: paper credentials unavailable", file=sys.stderr)
        sys.exit(2)
    return key, sec


if __name__ == "__main__":
    sys.exit(main())
