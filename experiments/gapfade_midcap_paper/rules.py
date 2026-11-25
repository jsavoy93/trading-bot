"""GAPFADE-MIDCAP-PAPER — FROZEN decision logic (pure functions, no I/O).

FROZEN RULE (do not tune here; see README.md):
  Universe: current S&P 400 members, injected (never hardcoded).
  Earnings filter: skip if earnings on t-1, t, or t+1 relative to the
  entry day, and skip names with no earnings-calendar coverage.
  Entry: gap = expected_open / prior_close - 1 <= -4.0%; ranked by most
  negative gap; max 3 new entries/day, max 10 open positions, skip names
  already held by this runner. Sizing = fixed fraction of paper equity
  (default 1%), skip if notional < 1 share or buying power insufficient.
  Exits: profit = first later open strictly above the entry price (OPG
  sell); stop = close <= entry - 2*ATR14[t-1] (ATR14 as of the day BEFORE
  the entry day), sell at the next open (documented divergence from the
  research, which exited at the triggering close); time cap = 3 trading
  days (entry day = 1), sell at the open of entry day + 3.
  Priority when several exits are due: profit at the open FIRST, then
  stop at the close, then the time cap.

Nothing in this module imports broker code, reads env vars, touches the
filesystem or sleeps. Part 2 feeds it real bars/quotes and submits orders.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# --- frozen thresholds ---------------------------------------------------
GAP_THRESHOLD = -0.04          # signal only when gap <= -4.0%
MAX_NEW_ENTRIES_PER_DAY = 3
MAX_OPEN_POSITIONS = 10
DEFAULT_SIZE_FRACTION = 0.01   # 1% of paper equity per entry
MAX_OPEN_POSITIONS_PER_SYMBOL = 1
STOP_ATR_MULT = 2.0
ATR_PERIOD = 14
TIME_CAP_TRADING_DAYS = 3      # entry day = day 1
SHADOW_VOL_LOOKBACK = 60       # close-to-close volatility window
SHADOW_VOL_QUINTILES = 5
DEFAULT_COST_BPS_PER_SIDE = 27.0   # research used 10 bps; see README

# --- ET windows (DST-aware; never a fixed UTC hour) ----------------------
MORNING_START = (9, 20)        # 09:20 ET
MORNING_DEADLINE = (9, 28)     # all OPG orders out by 09:28 ET
AFTER_CLOSE_START = (16, 0)    # official close

COID_PREFIX = "gf_mid-"
COID_MAX = 48                  # Alpaca client_order_id limit

# exit reasons
PROFIT = "profit"
STOP = "stop"
TIME_CAP = "time_cap"


class RuleError(ValueError):
    """Raised on invalid inputs (bad prices, empty ATR, ...)."""


# --- small helpers -------------------------------------------------------
def parse_date(value) -> date:
    """Accept ISO 'YYYY-MM-DD' strings or date objects."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def gap_pct(expected_open: float, prior_close: float) -> float:
    """gap = expected_open / prior_close - 1. Returns a fraction."""
    if prior_close is None or expected_open is None:
        raise RuleError("gap_pct needs prior_close and expected_open")
    prior_close = float(prior_close)
    expected_open = float(expected_open)
    if prior_close <= 0 or expected_open <= 0:
        raise RuleError("gap_pct needs strictly positive prices")
    return expected_open / prior_close - 1.0


def is_gap_signal(gap: float, threshold: float = GAP_THRESHOLD) -> bool:
    """Signal only if the gap is at or below the (negative) threshold."""
    return float(gap) <= threshold


def atr(values: list[float], period: int = ATR_PERIOD) -> float:
    """Simple (SMA) ATR over `period` true ranges, most recent last.

    Part 2 supplies TRs computed from official bars; this keeps the math
    pure and testable. Requires at least `period` values.
    """
    if period < 1:
        raise RuleError("atr period must be >= 1")
    if not values or len(values) < period:
        raise RuleError(
            f"atr needs >= {period} true ranges, got {len(values)}")
    window = [float(v) for v in values[-period:]]
    if any(v < 0 for v in window):
        raise RuleError("atr true ranges must be non-negative")
    return sum(window) / period


def stop_level(entry_price: float, atr14_t_minus_1: float,
               mult: float = STOP_ATR_MULT) -> float:
    """Stop = entry price - 2 x ATR14 as of t-1 (the day before entry)."""
    if entry_price is None or atr14_t_minus_1 is None:
        raise RuleError("stop_level needs entry_price and atr14")
    if float(entry_price) <= 0:
        raise RuleError("entry_price must be positive")
    if float(atr14_t_minus_1) < 0:
        raise RuleError("atr14 must be non-negative")
    return float(entry_price) - mult * float(atr14_t_minus_1)


# --- earnings filter -----------------------------------------------------
@dataclass(frozen=True)
class EarningsCalendar:
    """Injected earnings-date coverage.

    `dates_by_symbol`: symbol -> set of earnings dates (any date type
    parse_date accepts). A symbol absent from the dict has NO coverage and
    is skipped (fail closed).
    """

    dates_by_symbol: dict[str, set]

    def coverage(self, symbol: str) -> bool:
        return symbol in self.dates_by_symbol

    def dates(self, symbol: str) -> set[date]:
        return {parse_date(d) for d in self.dates_by_symbol.get(symbol, ())}

    def has_earnings_on(self, symbol: str, day: date) -> bool:
        return parse_date(day) in self.dates(symbol)


def earnings_skip(symbol: str, entry_day: date,
                  calendar: EarningsCalendar,
                  trading_days: list[date]) -> bool:
    """True when the name must be skipped for an entry on `entry_day`.

    Skips when earnings fall on the day before, the day of, or the day
    after the entry day (t-1, t, t+1) — the previous/next TRADING days
    when `trading_days` is supplied (a Monday entry looks back to Friday),
    calendar days otherwise — and skips names with no coverage at all.
    """
    if not calendar.coverage(symbol):
        return True
    entry_day = parse_date(entry_day)
    days = sorted({parse_date(d) for d in trading_days}) if trading_days else []
    if days and entry_day in days:
        i = days.index(entry_day)
        neighbours = {days[j] for j in (i - 1, i, i + 1) if 0 <= j < len(days)}
    else:
        from datetime import timedelta
        neighbours = {entry_day - timedelta(days=1), entry_day,
                      entry_day + timedelta(days=1)}
    return bool(calendar.dates(symbol) & neighbours)


# --- entries -------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    """One symbol's pre-market data for the entry decision."""

    symbol: str
    prior_close: float
    expected_open: float

    @property
    def gap(self) -> float:
        return gap_pct(self.expected_open, self.prior_close)


@dataclass(frozen=True)
class EntryPlan:
    symbol: str
    entry_day: date
    expected_open: float
    gap: float
    qty: int
    notional: float
    client_order_id: str


@dataclass(frozen=True)
class EntryDecision:
    plans: tuple[EntryPlan, ...]
    skipped: dict[str, str]           # symbol -> reason
    remaining_slots_today: int
    remaining_positions: int


def build_client_order_id(symbol: str, kind: str, ref_date: date,
                          seq: int = 1) -> str:
    """Deterministic idempotency key: gf_mid-<SYM>-<kind>-<ymd>-<n>.

    kind is 'buy' or 'sell'. Deterministic per (symbol, kind, day, n) so a
    rerun of the same day recomputes the same key and never double-enters.
    """
    sym = str(symbol).upper().strip()
    if not sym or not sym.replace(".", "").replace("-", "").isalnum():
        raise RuleError(f"invalid symbol {symbol!r}")
    if kind not in ("buy", "sell"):
        raise RuleError(f"kind must be 'buy' or 'sell', got {kind!r}")
    if not isinstance(seq, int) or seq < 1:
        raise RuleError("seq must be an int >= 1")
    ymd = parse_date(ref_date).strftime("%Y%m%d")
    cid = f"{COID_PREFIX}{sym}-{kind}-{ymd}-{seq}"
    if len(cid) > COID_MAX:
        raise RuleError(f"client_order_id too long: {cid!r}")
    return cid


def size_entry(equity: float, size_fraction: float, price: float,
               buying_power: float, whole_shares_only: bool = True) -> int:
    """Shares for a fixed-fraction-of-equity entry; 0 means 'skip'.

    Returns 0 when the notional buys no whole share (when
    whole_shares_only) or when buying power cannot fund it.
    """
    for name, val in (("equity", equity), ("size_fraction", size_fraction),
                      ("price", price), ("buying_power", buying_power)):
        if val is None:
            raise RuleError(f"size_entry needs {name}")
    equity, size_fraction = float(equity), float(size_fraction)
    price, buying_power = float(price), float(buying_power)
    if equity < 0 or not (0 < size_fraction <= 1) or price <= 0 \
            or buying_power < 0:
        raise RuleError("size_entry inputs out of range")
    notional = equity * size_fraction
    if notional > buying_power:
        return 0
    qty = int(notional // price) if whole_shares_only else 0
    if qty < 1:
        return 0
    return qty


def rank_candidates(candidates: list[Candidate],
                    held: set[str] | frozenset[str],
                    skipped: dict[str, str],
                    entry_day: date,
                    entries_today: int,
                    open_positions: int,
                    equity: float,
                    buying_power: float,
                    size_fraction: float = DEFAULT_SIZE_FRACTION,
                    max_new_today: int = MAX_NEW_ENTRIES_PER_DAY,
                    max_open: int = MAX_OPEN_POSITIONS) -> EntryDecision:
    """Rank gap signals and apply caps + sizing.

    `skipped` (symbol -> reason) carries already-known skips (no reliable
    prior close, earnings); this function adds its own reasons ('no
    signal', 'already held', ...). Buying power is spent greedily in rank
    order. Returns plans plus what remains of each cap.
    """
    held_up = {s.upper() for s in held}
    slots = max(0, int(max_new_today) - int(entries_today))
    room = max(0, int(max_open) - int(open_positions))
    plans: list[EntryPlan] = []
    skipped = dict(skipped)

    signals = []
    for cand in candidates:
        if not cand or cand.prior_close is None or cand.expected_open is None:
            continue
        try:
            g = cand.gap
        except RuleError:
            skipped[cand.symbol] = "bad_price"
            continue
        if not is_gap_signal(g):
            skipped[cand.symbol] = "no_signal"
            continue
        signals.append((g, cand))

    # most negative gap first; tie-break by symbol for determinism
    signals.sort(key=lambda pair: (pair[0], pair[1].symbol))

    seen: set[str] = set()
    for g, cand in signals:
        sym = cand.symbol.upper()
        if sym in seen:
            continue
        seen.add(sym)
        if sym in held_up:
            skipped[sym] = "already_held"
            continue
        if slots <= 0:
            skipped[sym] = "daily_cap"
            continue
        if room <= 0:
            skipped[sym] = "max_open"
            continue
        qty = size_entry(equity, size_fraction, cand.expected_open,
                         buying_power)
        if qty < 1:
            skipped[sym] = "size_zero"
            continue
        notional = qty * cand.expected_open
        cid = build_client_order_id(
            sym, "buy", entry_day, seq=entries_today + len(plans) + 1)
        plans.append(EntryPlan(symbol=sym, entry_day=parse_date(entry_day),
                               expected_open=float(cand.expected_open),
                               gap=g, qty=qty, notional=notional,
                               client_order_id=cid))
        buying_power -= notional
        slots -= 1
        room -= 1

    return EntryDecision(plans=tuple(plans), skipped=skipped,
                         remaining_slots_today=slots,
                         remaining_positions=room)


# --- exits ---------------------------------------------------------------
@dataclass(frozen=True)
class Position:
    """Open position as this runner knows it."""

    symbol: str
    entry_day: date
    entry_price: float
    qty: int
    stop_price: float          # entry - 2*ATR14[t-1], fixed at entry
    client_order_id: str

    def trading_day_number(self, day: date,
                           trading_days: list[date]) -> int:
        """Entry day = 1, next trading day = 2, ..."""
        days = [parse_date(d) for d in trading_days]
        return days.index(parse_date(day)) - days.index(
            parse_date(self.entry_day)) + 1


@dataclass(frozen=True)
class ExitDecision:
    symbol: str
    reason: str | None          # PROFIT / STOP / TIME_CAP / None
    when: str | None            # 'open' or 'close'
    trigger_price: float | None
    stop_pending: bool          # close <= stop -> sell NEXT open


def decide_exit(pos: Position, day: date, trading_days: list[date],
                day_open: float | None = None,
                day_close: float | None = None) -> ExitDecision:
    """Priority: profit at the OPEN first, then stop at the CLOSE, then
    the time cap. `day_open`/`day_close` are for `day` (official bars).

    - Profit: on any later trading day whose official open is strictly
      above `entry_price`, sell at that open (OPG) -> reason PROFIT,
      when='open'.
    - Stop: if the day closes at or below `stop_price`, sell at the NEXT
      open -> reason STOP, when='close', stop_pending=True.
    - Time cap: if `day` is the close of entry day + 2 (day number 3),
      sell at the next open -> reason TIME_CAP, when='close'.
    """
    day = parse_date(day)
    pos_day = parse_date(pos.entry_day)
    if day < pos_day:
        raise RuleError("decide_exit day precedes entry day")
    strict_later = day > pos_day

    # 1. profit check at the open: the first LATER open strictly above the
    #    entry price. The previous ``later = day > pos_day`` was shadowed by
    #    the local ``later`` list in due_exits and silently became truthy
    #    even on the entry day, which made the stop/time cap unreachable.
    if strict_later and day_open is not None \
            and float(day_open) > pos.entry_price:
        return ExitDecision(pos.symbol, PROFIT, "open", float(day_open),
                            stop_pending=False)

    stop_hit = (day_close is not None
                and float(day_close) <= pos.stop_price)

    # 3. time cap: entry day = day 1; sell after the close of day 3
    #    (judged whenever the calendar actually contains the entry day; a
    #    calendar that does not is not this position's calendar)
    days = [parse_date(d) for d in trading_days]
    time_cap = (parse_date(pos.entry_day) in days and
                pos.trading_day_number(day, trading_days) >=
                TIME_CAP_TRADING_DAYS)

    # 2. stop at the close
    if stop_hit:
        return ExitDecision(pos.symbol, STOP, "close",
                            float(day_close), stop_pending=True)

    if time_cap:
        return ExitDecision(pos.symbol, TIME_CAP, "close",
                            float(day_close) if day_close is not None
                            else None, stop_pending=False)

    return ExitDecision(pos.symbol, None, None, None, stop_pending=False)


def due_exits(positions: list[Position], day: date,
              trading_days: list[date],
              bars: dict[str, dict]) -> tuple[list[ExitDecision], list[str]]:
    """One day's exits. Returns (decisions to act on NOW, symbols whose
    stop fired at the close but whose sell is deferred to the next open).

    `bars[symbol]` = {'open': float, 'close': float} for `day`.
    Profit exits are due for the morning OPG pass; stop/time-cap decisions
    returned in `deferred` are executed by the NEXT morning's pass.
    """
    day = parse_date(day)
    due: list[ExitDecision] = []
    deferred: list[str] = []
    for pos in positions:
        bar = bars.get(pos.symbol) or {}
        dec = decide_exit(pos, day, trading_days,
                          day_open=bar.get("open"), day_close=bar.get("close"))
        if dec.reason == PROFIT:
            due.append(dec)
        elif dec.reason in (STOP, TIME_CAP):
            deferred.append(pos.symbol)
    return due, deferred


def sort_morning_sells(exits: list[ExitDecision]) -> list[ExitDecision]:
    """Morning pass order: OPG sells first; profit exits before deferred
    stop/time-cap sells (priority order), then by symbol for determinism."""
    order = {PROFIT: 0, STOP: 1, TIME_CAP: 2}
    return sorted(exits, key=lambda d: (order.get(d.reason, 9), d.symbol))


# --- costs ---------------------------------------------------------------
def round_trip_cost(notional: float,
                    bps_per_side: float = DEFAULT_COST_BPS_PER_SIDE) -> float:
    """Total per-position cost: bps_per_side on entry AND exit notional."""
    if notional is None or float(notional) < 0:
        raise RuleError("round_trip_cost needs a non-negative notional")
    if float(bps_per_side) < 0:
        raise RuleError("bps_per_side must be non-negative")
    return 2.0 * float(notional) * float(bps_per_side) / 10_000.0


# --- shadows -------------------------------------------------------------
def return_vol(closes: list[float],
               lookback: int = SHADOW_VOL_LOOKBACK) -> float:
    """Population stdev of the last `lookback` close-to-close returns."""
    vals = [float(c) for c in closes]
    if len(vals) < lookback + 1:
        raise RuleError(
            f"return_vol needs {lookback + 1} closes, got {len(vals)}")
    vals = vals[-(lookback + 1):]
    rets = [vals[i] / vals[i - 1] - 1.0 for i in range(1, len(vals))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    return var ** 0.5


def vol_quintile(values: dict[str, float],
                 n_quintiles: int = SHADOW_VOL_QUINTILES) -> dict[str, int]:
    """Map symbol -> quintile 0 (lowest vol) .. n-1, by rank."""
    syms = sorted(values, key=lambda s: (values[s], s))
    total = len(syms)
    if total == 0:
        return {}
    out: dict[str, int] = {}
    for i, sym in enumerate(syms):
        q = min(n_quintiles - 1, int(i * n_quintiles // total))
        out[sym] = q
    return out


@dataclass(frozen=True)
class ShadowPick:
    kind: str                  # 'random_clean' or 'vol_matched'
    symbol: str | None
    quintile: int | None
    reason: str | None         # why None, when no eligible name


def select_shadows(symbol: str, universe: list[str],
                   eligible_clean: set[str] | frozenset[str],
                   chosen: set[str] | frozenset[str],
                   vols: dict[str, float],
                   rng: random.Random,
                   n_quintiles: int = SHADOW_VOL_QUINTILES,
                   ) -> tuple[ShadowPick, ShadowPick]:
    """Pick the two NOT-ordered shadow trades for a real entry.

    1. random_clean: a uniformly random name from `universe` that is
       earnings-clean and was not chosen (real or shadow) already.
    2. vol_matched: a uniformly random name of the same 60-day
       close-to-close volatility quintile, same eligibility rules.
    `rng` is injected so tests are deterministic. Returns two picks (with
    symbol=None and a reason when nothing eligible exists).
    """
    chosen_up = {s.upper() for s in chosen} | {symbol.upper()}
    clean = {s.upper() for s in eligible_clean}
    eligible = sorted({s.upper() for s in universe}
                      & clean - chosen_up)

    picked: set[str] = set()
    first_pool = list(eligible)
    rng.shuffle(first_pool)
    first_sym = first_pool[0] if first_pool else None
    if first_sym:
        picked.add(first_sym)
    first = ShadowPick("random_clean", first_sym, None,
                       None if first_sym else "no_eligible_name")

    sym_up = symbol.upper()
    vol_map = {k.upper(): v for k, v in vols.items()}
    if sym_up not in vol_map:
        return first, ShadowPick("vol_matched", None, None, "no_vol_for_symbol")
    try:
        qmap = vol_quintile(vol_map, n_quintiles)
    except RuleError:
        return first, ShadowPick("vol_matched", None, None, "insufficient_vol")
    target_q = qmap[sym_up]
    same_q = [s for s in eligible if s not in picked
              and qmap.get(s) == target_q]
    rng.shuffle(same_q)
    if not same_q:
        return first, ShadowPick("vol_matched", None, target_q,
                                 "no_name_in_quintile")
    return first, ShadowPick("vol_matched", same_q[0], target_q, None)


# --- market windows ------------------------------------------------------
def etc_now() -> datetime:
    return datetime.now(ET)


def morning_window(now: datetime) -> bool:
    """Morning pass is allowed in [09:20, 09:28) ET."""
    now = now.astimezone(ET)
    t = (now.hour, now.minute)
    return MORNING_START <= t < MORNING_DEADLINE


def after_close_window(now: datetime) -> bool:
    """After-close pass from 16:00 ET onwards (same date)."""
    now = now.astimezone(ET)
    return (now.hour, now.minute) >= AFTER_CLOSE_START


def choose_pass(now: datetime) -> str | None:
    """'morning' / 'after_close' / None (outside any pass window)."""
    if morning_window(now):
        return "morning"
    if after_close_window(now):
        return "after_close"
    return None
