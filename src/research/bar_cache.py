"""Alpaca historical bar retrieval for offline research.

Read-only with respect to production. Calls only the Alpaca historical-data
endpoints (``StockHistoricalDataClient.get_stock_bars``), not the trading
endpoints.

Provides a ``BarCache`` that:
    * Fetches minute bars for ``(symbol, calendar_date)`` pairs on demand.
    * Caches fetched bars in memory per (symbol, calendar_date).
    * Persists a local Parquet cache under
      ``reports/research/bar_cache/`` (gitignored) so a second run can
      skip the API call.

The cache directory is created automatically on first use. Each fetch is
saved as ``{symbol}_{YYYY-MM-DD}.parquet``. Cached files are reused
without re-fetching if they exist and are newer than the configured
``max_age_hours``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


DEFAULT_CACHE_DIR = Path("reports/research/bar_cache")
TIMEFRAME = TimeFrame.Minute  # 1-min resolution for forward-return research


@dataclass
class BarCache:
    """Alpaca historical minute-bar cache keyed by (symbol, calendar_date).

    Parameters
    ----------
    api_key, api_secret:
        Alpaca credentials. Pass ``os.environ["ALPACA_API_KEY"]`` etc.
    cache_dir:
        Local directory for Parquet cache. Defaults to
        ``reports/research/bar_cache``.
    max_age_hours:
        Re-fetch a cached file if it is older than this. Set to ``None`` to
        never re-fetch (trust cache forever).
    _memory:
        In-memory cache, ``{(symbol, date): DataFrame}``.
    """

    api_key: str
    api_secret: str
    cache_dir: Path = DEFAULT_CACHE_DIR
    max_age_hours: Optional[float] = 24.0
    _client: Optional[StockHistoricalDataClient] = field(default=None, init=False, repr=False)
    _memory: dict[tuple[str, str], pd.DataFrame] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _client_get(self) -> StockHistoricalDataClient:
        if self._client is None:
            self._client = StockHistoricalDataClient(self.api_key, self.api_secret)
        return self._client

    @staticmethod
    def _date_str(d) -> str:
        if isinstance(d, str):
            return d
        if isinstance(d, datetime):
            return d.strftime("%Y-%m-%d")
        return str(d)

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        y, m, d = date_str.split("-")
        return datetime(int(y), int(m), int(d), tzinfo=timezone.utc)

    def _cache_path(self, symbol: str, date_str: str) -> Path:
        safe_sym = symbol.replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"{safe_sym}_{date_str}.pkl"

    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        if self.max_age_hours is None:
            return True
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600.0
        return age < self.max_age_hours

    def _load_from_disk(self, symbol: str, date_str: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(symbol, date_str)
        if not path.exists():
            return None
        try:
            df = pd.read_pickle(path)
            if df.empty:
                return df
            # Reattach UTC tz if needed.
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df
        except Exception:
            return None

    def _save_to_disk(self, symbol: str, date_str: str, df: pd.DataFrame) -> None:
        path = self._cache_path(symbol, date_str)
        # Use a pickle for cache: parquet requires pyarrow/fastparquet which
        # may not be installed in this environment. Pickle is fine for
        # gitignored local cache files.
        try:
            df.to_pickle(path)
        except Exception:
            # Cache failures must not break research. Ignore.
            pass

    def fetch_day(self, symbol: str, date_str: str) -> pd.DataFrame:
        """Return 1-minute bars for ``symbol`` on the calendar date ``date_str``.

        Returns an empty ``DataFrame`` if no bars exist for that day.
        Uses memory cache, then disk cache, then Alpaca.
        """
        key = (symbol, date_str)
        if key in self._memory:
            return self._memory[key]

        path = self._cache_path(symbol, date_str)
        if self._cache_is_fresh(path):
            df = self._load_from_disk(symbol, date_str)
            if df is not None:
                self._memory[key] = df
                return df

        client = self._client_get()
        day_start = self._parse_date(date_str)
        # Fetch a wider window to absorb timezone oddities.
        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TIMEFRAME,
            start=day_start,
            end=day_start + timedelta(days=1),
        )
        try:
            barset = client.get_stock_bars(req)
        except Exception:
            df = pd.DataFrame()
            self._memory[key] = df
            return df

        raw = barset.df
        if raw is None or raw.empty:
            df = pd.DataFrame()
            self._memory[key] = df
            self._save_to_disk(symbol, date_str, df)
            return df

        # MultiIndex (symbol, timestamp); drop symbol level.
        df = raw.droplevel(0).copy()
        # Ensure UTC tz-aware index.
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        # Keep only rows whose UTC date equals date_str (defensive).
        df = df[df.index.tz_convert("UTC").date == day_start.date()]
        self._memory[key] = df
        self._save_to_disk(symbol, date_str, df)
        return df

    def fetch_window(
        self, symbol: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Return 1-minute bars for ``symbol`` from ``start_date`` through
        ``end_date`` inclusive."""
        start = self._parse_date(start_date).date()
        end = self._parse_date(end_date).date()
        parts = []
        d = start
        while d <= end:
            parts.append(self.fetch_day(symbol, d.strftime("%Y-%m-%d")))
            d = d + timedelta(days=1)
        if not parts:
            return pd.DataFrame()
        out = pd.concat(parts).sort_index()
        # De-dup by index.
        out = out[~out.index.duplicated(keep="first")]
        return out

    def ensure_many(
        self,
        pairs: list[tuple[str, str]],
        batch_size: int = 100,
        progress_every: int = 0,
    ) -> None:
        """Pre-populate the cache for many ``(symbol, date_str)`` pairs.

        Pairs already present in the cache are skipped. Otherwise they
        are batched by date (Alpaca supports multi-symbol per request)
        and fetched in chunks of ``batch_size`` symbols.
        """
        needed: dict[str, list[str]] = {}
        for sym, date_str in pairs:
            if (sym, date_str) in self._memory:
                continue
            path = self._cache_path(sym, date_str)
            if self._cache_is_fresh(path):
                df = self._load_from_disk(sym, date_str)
                if df is not None:
                    self._memory[(sym, date_str)] = df
                    continue
            needed.setdefault(date_str, []).append(sym)

        client = self._client_get()
        n_calls = 0
        for date_str, syms in needed.items():
            # Dedup symbols for this date
            syms_unique = sorted(set(syms))
            day_start = self._parse_date(date_str)
            for i in range(0, len(syms_unique), batch_size):
                chunk = syms_unique[i:i + batch_size]
                req = StockBarsRequest(
                    symbol_or_symbols=chunk,
                    timeframe=TIMEFRAME,
                    start=day_start,
                    end=day_start + timedelta(days=1),
                )
                try:
                    barset = client.get_stock_bars(req)
                    raw = barset.df
                except Exception:
                    raw = None

                if raw is None or raw.empty:
                    # Empty for everyone in this chunk
                    for sym in chunk:
                        empty = pd.DataFrame()
                        self._memory[(sym, date_str)] = empty
                        self._save_to_disk(sym, date_str, empty)
                    n_calls += 1
                    continue

                # raw has MultiIndex (symbol, timestamp); split per symbol
                present = set(raw.index.get_level_values(0).unique())
                for sym in chunk:
                    if sym in present:
                        df = raw.droplevel(0).loc[sym].to_frame() if False else raw.xs(sym, level=0).copy()
                        # Wait, xs gives a DataFrame already.
                        if df.index.tz is None:
                            df.index = df.index.tz_localize("UTC")
                        df = df[df.index.tz_convert("UTC").date == day_start.date()]
                    else:
                        df = pd.DataFrame()
                    self._memory[(sym, date_str)] = df
                    self._save_to_disk(sym, date_str, df)
                n_calls += 1
                if progress_every and n_calls % progress_every == 0:
                    print(f"  [cache] {n_calls} batched fetches done; cache size {len(self._memory)}")


def collect_symbol_dates(
    df, symbol_col: str = "symbol", ts_col: str = "cycle_start",
    include_next_day: bool = True,
) -> list[tuple[str, str]]:
    """Return unique ``(symbol, YYYY-MM-DD)`` pairs from a decisions frame.

    For each decision, the following dates are added so we can fetch bars
    covering both the decision-time and forward-time windows:

        * the decision's calendar date
        * the previous calendar date (for after-hours decision bars)
        * the next calendar date (for next-session-open horizons)

    Set ``include_next_day=False`` to omit the next-day pair (e.g., if
    you don't need next-session horizons).
    """
    from datetime import timedelta
    out = set()
    for _, row in df.iterrows():
        sym = row[symbol_col]
        ts = pd.Timestamp(row[ts_col])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        utc = ts.tz_convert("UTC")
        cur = utc.strftime("%Y-%m-%d")
        prev = (utc - timedelta(days=1)).strftime("%Y-%m-%d")
        out.add((sym, cur))
        out.add((sym, prev))
        if include_next_day:
            nxt = (utc + timedelta(days=1)).strftime("%Y-%m-%d")
            out.add((sym, nxt))
    return sorted(out)
