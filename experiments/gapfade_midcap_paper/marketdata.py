"""GAPFADE-MIDCAP-PAPER — Alpaca PAPER market data (broker.MarketData).

Method map (all read-only GETs against https://data.alpaca.markets):

| Protocol call                  | Alpaca call                                        |
|--------------------------------|----------------------------------------------------|
| `prior_close(day)`             | POST-free: last DAILY bar (adjustment=split, feed=iex) strictly before `day` -> `close`, collected by `load_day` |
| `expected_open(day)`           | GET /v2/stocks/snapshots?feed=iex (batched, 200 symbols/request) -> `latestTrade.p`, falling back to `minuteBar.c` then the prior close |
| `true_ranges(symbol, through)` | the daily bars before `through` -> Wilder-style TR (high-low, vs prior close), ATR14 = mean of the last 14 |
| `official_bar(symbol, day)`    | the daily bar dated `day` -> `{'open', 'close', 'high', 'low', 'volume'}` |
| `eligible_clean(day)`          | current S&P 400 membership (membership.py, MDY holdings) minus names with earnings on t-1/t/t+1 (frozen rule) |
| `vols(day, symbols)`           | `rules.return_vol` over the 61 most recent official closes before `day` |

Expected-open caveat (documented in the README): at 09:20 ET there is no
official open yet, so the gap is measured on the last pre-market print —
IEX's latest trade from the snapshot. It is an ESTIMATE of the open; the
actual fill is the official opening auction price, which is what the
after-close pass records. `feed=iex` is Alpaca's free feed; SIP would be
closer to the consolidation but is not required for a paper experiment.

Nothing in this module places orders. `quotes`/`bars` are fetched once per
`load_day` and cached in memory for the run.
"""

from __future__ import annotations

from datetime import date, timedelta

from experiments.gapfade_midcap_paper import membership, rules


class MarketDataError(RuntimeError):
    """Data needed for a decision is missing; the caller skips or stops."""


class AlpacaMarketData:
    """`broker.MarketData` over an `alpaca.AlpacaPaperClient`."""

    def __init__(self, client, cache_dir, *, members=None, bars_days: int = 200,
                 today: date | None = None):
        self.client = client
        self.cache_dir = cache_dir
        self._members = list(members) if members else None
        self.bars_days = bars_days
        self.today = today
        self.quotes: dict[str, dict] = {}
        self.bars: dict[str, list[dict]] = {}

    # --- universe -------------------------------------------------------
    def members(self) -> list[str]:
        if self._members is None:
            self._members = membership.load_members(
                self.cache_dir, today=self.today)
        return list(self._members)

    # --- bulk load ------------------------------------------------------
    def load_day(self, day: str, symbols: list[str] | None = None,
                 with_quotes: bool = True) -> None:
        """Fetch daily bars (ending the day before `day`) and snapshots."""
        day_d = rules.parse_date(day)
        syms = sorted({s.upper() for s in (symbols or self.members())})
        start = (day_d - timedelta(days=self.bars_days)).isoformat()
        end = day_d.isoformat()             # exclusive upper bound
        bars = self.client.daily_bars(syms, start, end)
        for sym, rows in bars.items():
            self.bars[sym.upper()] = [
                {"date": str(b.get("t", ""))[:10],
                 "open": float(b["o"]), "high": float(b["h"]),
                 "low": float(b["l"]), "close": float(b["c"]),
                 "volume": float(b.get("v", 0) or 0)}
                for b in rows if "o" in b and "c" in b]
        if with_quotes:
            self.quotes = self.client.snapshot_prices(syms) or {}

    # --- protocol -------------------------------------------------------
    def prior_close(self, day: str) -> dict[str, float]:
        day_s = rules.parse_date(day).isoformat()
        out = {}
        for sym, rows in self.bars.items():
            before = [r for r in rows if r["date"] < day_s]
            if before:
                out[sym] = before[-1]["close"]
        return out

    def expected_open(self, day: str) -> dict[str, float]:
        day_s = rules.parse_date(day).isoformat()
        prior = self.prior_close(day_s)
        out = {}
        for sym in prior:
            snap = self.quotes.get(sym) or {}
            price = None
            lt = snap.get("latestTrade") or {}
            mb = snap.get("minuteBar") or {}
            if lt.get("p") is not None:
                price = float(lt["p"])
            elif mb.get("c") is not None:
                price = float(mb["c"])
            if price is not None and price > 0:
                out[sym] = price
        return out

    def official_bar(self, symbol: str, day: str) -> dict[str, float]:
        rows = self.bars.get(symbol.upper()) or []
        day_s = rules.parse_date(day).isoformat()
        for bar in rows:
            if bar["date"] == day_s:
                return bar
        # not loaded for this day yet -> fetch just this symbol
        try:
            bars = self.client.daily_bars([symbol.upper()], day_s,
                                          (rules.parse_date(day_s)
                                           + timedelta(days=1)).isoformat())
        except Exception as exc:      # transport/API -> a data error
            raise MarketDataError(
                f"daily bars for {symbol} on {day_s} failed: {exc}") from exc
        rows = [{"date": str(b.get("t", ""))[:10], "open": float(b["o"]),
                 "high": float(b["h"]), "low": float(b["l"]),
                 "close": float(b["c"])}
                for b in bars.get(symbol.upper(), []) if "o" in b]
        if not rows:
            raise MarketDataError(f"no official bar for {symbol} on {day_s}")
        return rows[0]

    def true_ranges(self, symbol: str, through_day: str,
                    n: int = rules.ATR_PERIOD) -> list[float]:
        """The last `n` true ranges of the trading days BEFORE
        `through_day`, i.e. for an entry on day t they cover the closes of
        t-1 (and earlier): ATR14[t-1]."""
        rows = [r for r in (self.bars.get(symbol.upper()) or [])
                if r["date"] < rules.parse_date(through_day).isoformat()]
        return list(_true_ranges(rows))[-n:] if rows else []

    def atr14(self, symbol: str, entry_day: str) -> float | None:
        """ATR14 as of the close of t-1 (the frozen stop input)."""
        trs = self.true_ranges(symbol, entry_day, n=rules.ATR_PERIOD)
        if len(trs) < rules.ATR_PERIOD:
            return None
        return rules.atr(trs, rules.ATR_PERIOD)

    def closes_before(self, symbol: str, day: str,
                      n: int = rules.SHADOW_VOL_LOOKBACK + 1) -> list[float]:
        rows = [r for r in (self.bars.get(symbol.upper()) or [])
                if r["date"] < rules.parse_date(day).isoformat()]
        return [r["close"] for r in rows[-n:]]

    def vols(self, day: str, symbols: list[str]) -> dict[str, float]:
        out = {}
        for sym in symbols:
            closes = self.closes_before(sym, day)
            try:
                out[sym.upper()] = rules.return_vol(closes)
            except rules.RuleError:
                continue
        return out

    def eligible_clean(self, day: str,
                       calendar: rules.EarningsCalendar | None = None,
                       ) -> list[str]:
        """Members with earnings-calendar coverage and no earnings on
        t-1/t/t+1 (the frozen skip rule), restricted to names with bars."""
        day_d = rules.parse_date(day)
        if calendar is None:
            return []                     # no calendar -> no coverage -> none
        cal = calendar
        # neighbours use calendar days for the shadow pool: shadows enter on
        # `day` exactly like a real entry, so the same t-1/t/t+1 window is
        # applied (the runner passes the trading-day list for real entries)
        out = []
        for sym in self.members():
            if not cal.coverage(sym):
                continue
            if rules.earnings_skip(sym, day_d, cal, []):
                continue
            if not (self.bars.get(sym) or self.bars.get(sym.upper())):
                continue
            out.append(sym)
        return out


def _true_ranges(rows: list[dict]) -> list[float]:
    """True range per row: max(high-low, |high-prev_close|,
    |low-prev_close|); the first row has no previous close."""
    out: list[float] = []
    prev_close = None
    for row in rows:
        hi = row.get("high", row.get("close"))
        lo = row.get("low", row.get("close"))
        if hi is None or lo is None:
            prev_close = row.get("close")
            continue
        tr = float(hi) - float(lo)
        if prev_close is not None:
            tr = max(tr, abs(float(hi) - prev_close),
                     abs(float(lo) - prev_close))
        out.append(tr)
        prev_close = row.get("close")
    return out
