"""Risk controls: per-trade cap, sector/correlation cap, daily loss limit,
earnings blackout, kill switch. All pure logic — broker supplied by caller,
so every rule is unit-testable with a mock.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Config
from .universe import sector_of


# ---------------------------------------------------------------- kill switch
def kill_switch_active(cfg: Config, now: datetime | None = None) -> bool:
    """Active when env STRAT025_KILL_SWITCH=1 OR the flag file exists.
    When active: no new entries; if cfg.kill_flatten, engine also flattens."""
    if os.environ.get(cfg.kill_switch_env, "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return Path(cfg.kill_switch_file).exists()


# ----------------------------------------------------------- earnings blackout
@dataclass
class EarningsBlackout:
    """Skip entries for a symbol within +/- `window_days` calendar days of an
    earnings date. Alpaca's trading API has no earnings-calendar endpoint, so
    dates come from a pluggable map: {symbol: [ISO dates, ...]} — sourced from
    STRAT025_EARNINGS_JSON (a file of that shape) or injected directly.
    Tests inject maps directly."""
    dates: dict[str, list[str]]
    window_days: int = 1

    @classmethod
    def from_json(cls, path: str, window_days: int = 1) -> "EarningsBlackout":
        p = Path(path)
        data: dict = {}
        if p.exists():
            import json
            try:
                data = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
        return cls(dates={k.upper(): [str(d) for d in v] for k, v in data.items()},
                   window_days=window_days)

    def is_blacked_out(self, symbol: str, today: date | None = None) -> bool:
        today = today or date.today()
        w = self.window_days
        for ds in self.dates.get(symbol.upper(), []):
            try:
                d = date.fromisoformat(ds[:10])
            except ValueError:
                continue
            if abs((d - today).days) <= w:
                return True
        return False

    def next_earnings(self, symbol: str, today: date | None = None) -> date | None:
        today = today or date.today()
        upcoming = []
        for ds in self.dates.get(symbol.upper(), []):
            try:
                d = date.fromisoformat(ds[:10])
            except ValueError:
                continue
            if d >= today - timedelta(days=self.window_days):
                upcoming.append(d)
        return min(upcoming) if upcoming else None


# ------------------------------------------------------------- daily loss halt
@dataclass
class DailyLossLimit:
    """Halt new entries for the day once the sleeve's day P&L (realized +
    unrealized, % of day-start equity) falls to/below -limit_pct."""
    limit_pct: float = 3.0

    def is_halted(self, day_start_equity: float, current_equity: float) -> bool:
        if day_start_equity <= 0:
            return False
        pnl_pct = (current_equity - day_start_equity) / day_start_equity * 100.0
        return pnl_pct <= -abs(self.limit_pct)


# ------------------------------------------------------------------- sizing
def position_size(equity: float, entry: float, stop: float,
                  risk_per_trade_pct: float,
                  floor_usd: float = 100.0,
                  max_notional: float | None = None) -> float:
    """Shares such that (entry - stop) * shares <= risk cap = pct% of equity.
    Returns 0 (no trade) if stop invalid, risk nonpositive, or entry/stop bad.
    Result is additionally capped by max_notional / entry if provided."""
    if equity <= 0 or entry <= 0 or stop <= 0 or stop >= entry:
        return 0.0
    risk_budget = equity * risk_per_trade_pct / 100.0
    per_share_risk = entry - stop
    shares = risk_budget / per_share_risk
    if shares * entry < floor_usd:
        # sizing below the sleeve floor is not executable — refuse rather than
        # over-risk; fractional paper shares could take it, but stay strict.
        return 0.0
    if max_notional is not None:
        shares = min(shares, max_notional / entry)
    return float(int(shares))  # whole shares only, conservative rounding


# ------------------------------------------------------------ portfolio gates
def sector_count(positions: list[dict], sector_map: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in positions:
        s = sector_of(p.get("symbol", ""), sector_map)
        counts[s] = counts.get(s, 0) + 1
    return counts


def sector_cap_ok(symbol: str, positions: list[dict], sector_map: dict[str, str],
                  max_per_sector: int) -> bool:
    cur = sector_count(positions, sector_map).get(sector_of(symbol, sector_map), 0)
    return cur < max_per_sector


class RiskManager:
    """One stop gate for entries. approve() returns (ok, reason)."""

    def __init__(self, cfg: Config, blackout: EarningsBlackout | None = None,
                 sector_map: dict[str, str] | None = None):
        self.cfg = cfg
        self.blackout = blackout or EarningsBlackout(dates={},
                                                     window_days=cfg.earnings_blackout_days)
        self.sector_map = sector_map or {}
        self.halted_today: str | None = None   # ISO date once daily loss halts

    def approve(self, *, symbol: str, equity: float, day_start_equity: float,
                positions: list[dict], today: date | None = None) -> tuple[bool, str]:
        today = today or date.today()
        if kill_switch_active(self.cfg):
            return False, "kill_switch"
        if self.halted_today == today.isoformat():
            return False, "daily_loss_halt"
        if DailyLossLimit(self.cfg.daily_loss_limit_pct).is_halted(day_start_equity, equity):
            self.halted_today = today.isoformat()
            return False, "daily_loss_halt"
        if self.blackout.is_blacked_out(symbol, today):
            return False, "earnings_blackout"
        if len(positions) >= self.cfg.max_open_positions:
            return False, "max_positions"
        if not sector_cap_ok(symbol, positions, self.sector_map,
                             self.cfg.max_positions_per_sector):
            return False, "sector_cap"
        return True, "ok"
