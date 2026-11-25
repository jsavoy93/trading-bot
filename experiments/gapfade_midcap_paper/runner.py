"""GAPFADE-MIDCAP-PAPER — CLI orchestration (PAPER ONLY).

Usage (from the repo root):
  python experiments/gapfade_midcap_paper/runner.py                 # dry run
  python experiments/gapfade_midcap_paper/runner.py --dry-run       # same
  python experiments/gapfade_midcap_paper/runner.py --execute       # guarded

--dry-run is the DEFAULT and places ZERO orders: it prints the candidates,
the intended OPG buys/sells and the shadow picks. --execute refuses to run
(exit code 2) unless TRADING_BOT_PAPER_ONLY=1, the URL is exactly
https://paper-api.alpaca.markets and the kill-switch file is absent.
Part 1 has no broker wired: --execute stops at the broker boundary with
exit code 3; part 2 implements broker.submit_* on that same path.

Pass selection: --pass {auto,morning,after_close}. 'auto' (default) uses
the America/New_York clock: morning 09:20-09:28, after-close from 16:00.
Morning output outside that window is refused unless --ignore-window.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):        # allow `python .../runner.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.gapfade_midcap_paper import broker as broker_mod  # noqa: E402
from experiments.gapfade_midcap_paper import guards, rules  # noqa: E402
from experiments.gapfade_midcap_paper.state import StateStore  # noqa: E402

EXIT_NOT_WIRED = 3


# --- injected inputs (part 2 supplies these from Alpaca PAPER) -----------
@dataclass
class MorningInputs:
    day: str                                  # ISO trading day
    trading_days: list[str]                   # days incl. `day`, ordered
    universe: list[str]                       # current S&P 400 members
    prior_close: dict[str, float]
    expected_open: dict[str, float]
    all_expected_opens: dict[str, float]      # all universe names (shadows)
    earnings: rules.EarningsCalendar
    eligible_clean: set[str] = field(default_factory=set)
    vols: dict[str, float] = field(default_factory=dict)
    equity: float = 0.0
    buying_power: float = 0.0


@dataclass
class AfterCloseInputs:
    day: str
    trading_days: list[str]
    bars: dict[str, dict]                     # symbol -> {'open','close'}
    all_expected_opens: dict[str, float] = field(default_factory=dict)


# --- part 2: containers built from Alpaca PAPER data --------------------
@dataclass
class RealMorningInputs:
    """Everything plan_morning needs, plus what the executor must log."""
    day: str
    trading_days: list[str]
    universe: list[str]
    prior_close: dict[str, float]
    expected_open: dict[str, float]
    expected_open_all: dict[str, float]
    earnings: rules.EarningsCalendar
    eligible_clean: set[str]
    vols: dict[str, float]
    atr14: dict[str, float]
    equity: float = 0.0
    buying_power: float = 0.0
    sent_at: str | None = None
    _data: object = field(default=None, repr=False)

    def as_signal_inputs(self) -> MorningInputs:
        """The frozen plan_morning view: candidates are the universe names
        that HAVE a prior close and an expected open; everything else is
        reported as skipped by plan_morning itself."""
        return MorningInputs(
            day=self.day, trading_days=self.trading_days,
            universe=list(self.universe),
            prior_close=dict(self.prior_close),
            expected_open=dict(self.expected_open),
            all_expected_opens=dict(self.expected_open_all),
            earnings=self.earnings, eligible_clean=set(self.eligible_clean),
            vols=dict(self.vols), equity=self.equity,
            buying_power=self.buying_power)


@dataclass
class RealAfterCloseInputs:
    day: str
    trading_days: list[str]
    bars: dict[str, dict]
    prior_close: dict[str, float] = field(default_factory=dict)
    vols: dict[str, float] = field(default_factory=dict)
    eligible_clean: set[str] = field(default_factory=set)
    rng: random.Random | None = None
    _data: object = field(default=None, repr=False)


# --- plan containers (printed in dry run, submitted in part 2) -----------
@dataclass(frozen=True)
class PlannedOrder:
    side: str            # 'buy' | 'sell'
    symbol: str
    qty: int
    reason: str
    client_order_id: str
    price_ref: float | None = None


# --- morning pass --------------------------------------------------------
def plan_morning(state: StateStore, inp: MorningInputs,
                 now=None, rng: random.Random | None = None,
                 size_fraction: float = rules.DEFAULT_SIZE_FRACTION,
                 ) -> dict:
    """Return {'sells': [...], 'buys': [...], 'shadows': [...],
    'candidates': [...], 'skipped': {...}} — pure w.r.t. orders."""
    day = rules.parse_date(inp.day)
    pos_map = {s: rules.Position(
        symbol=rec["symbol"], entry_day=rules.parse_date(rec["entry_day"]),
        entry_price=float(rec["entry_price"]), qty=int(rec["qty"]),
        stop_price=float(rec["stop_price"]),
        client_order_id=rec["client_order_id"])
        for s, rec in state.positions().items()}

    sells: list[PlannedOrder] = []
    skip_reason_by_sym: dict[str, str] = {}

    # 1. OPG sells for due exits (profit at the open; deferred stop/time-cap
    #    from the previous after-close pass carried in state)
    deferred = {str(item.get("symbol", "")).upper()
                for item in state.data.get("pending_exits", [])
                if isinstance(item, dict)}
    open_by_sym = inp.expected_open
    for sym, pos in pos_map.items():
        dec = rules.decide_exit(pos, day, inp.trading_days,
                                day_open=open_by_sym.get(sym))
        reason = dec.reason
        if reason == rules.PROFIT:
            sells.append(_sell_order(pos, rules.PROFIT, dec.trigger_price))
        elif sym in deferred:
            sells.append(_sell_order(pos, _deferred_reason(state, sym),
                                     open_by_sym.get(sym)))
        else:
            continue
        skip_reason_by_sym[sym] = "exit_due"
    sells.sort(key=lambda o: ({rules.PROFIT: 0, rules.STOP: 1,
                               rules.TIME_CAP: 2}.get(o.reason, 9),
                              o.symbol))

    # 2. new entries
    candidates = []
    for sym in inp.universe:
        pc = inp.prior_close.get(sym)
        eo = inp.expected_open.get(sym)
        if pc is None or eo is None:
            skip_reason_by_sym[sym] = "no_prior_close_or_open"
            continue
        if rules.earnings_skip(sym, day, inp.earnings, inp.trading_days):
            skip_reason_by_sym[sym] = "earnings"
            continue
        candidates.append(rules.Candidate(sym, pc, eo))

    exiting = {o.symbol for o in sells}
    # a symbol sold today may be re-entered only on a LATER day
    held = set(pos_map) | {p.symbol for p in pos_map.values()} | exiting
    deciding = [c for c in candidates if c.symbol.upper() not in exiting]
    skipped = {k: v for k, v in skip_reason_by_sym.items()
               if k not in {o.symbol for o in sells}}
    decision = rules.rank_candidates(
        deciding, held=held, skipped=skipped,
        entry_day=day, entries_today=state.entries_on(day.isoformat()),
        open_positions=len(state.positions()), equity=inp.equity,
        buying_power=inp.buying_power, size_fraction=size_fraction)

    buys = [PlannedOrder("buy", p.symbol, p.qty, "signal", p.client_order_id,
                         p.expected_open) for p in decision.plans]

    # 3. shadows (NOT ordered): one random clean name + one vol-matched
    rng = rng or random.Random()
    # shadows never duplicate today's real entries or other shadow names
    chosen = state.chosen_symbols() | {p.symbol for p in decision.plans}
    shadows = []
    dropped: list[dict] = []
    for p in decision.plans:
        picks = rules.select_shadows(
            p.symbol, inp.universe, inp.eligible_clean, chosen,
            inp.vols, rng)
        for pick in picks:
            if pick.symbol is None:
                print(f"gapfade_midcap_paper: no {pick.kind} shadow for "
                      f"{p.symbol}: {pick.reason}", file=sys.stderr)
        for pick in picks:
            if pick.symbol is None:
                continue
            entry = inp.all_expected_opens.get(pick.symbol)
            if entry is None:
                # one shadow per REAL entry per kind is the spec, but a
                # shadow we cannot value from the official open is worthless
                # (and a pre-market print is NOT the official open), so it
                # is dropped and logged rather than guessed.
                dropped.append({"real_symbol": p.symbol, "kind": pick.kind,
                                "symbol": pick.symbol,
                                "reason": "no_expected_open"})
                print(f"gapfade_midcap_paper: shadow {pick.kind} "
                      f"{pick.symbol} dropped for {p.symbol}: no expected "
                      "open", file=sys.stderr)
                continue
            shadows.append({
                "real_symbol": p.symbol,
                "real_client_order_id": p.client_order_id,
                "kind": pick.kind,
                "symbol": pick.symbol, "quintile": pick.quintile,
                "entry_day": day.isoformat(),
                "entry_price": entry,
            })
            chosen.add(pick.symbol)

    return {"day": day.isoformat(), "sells": sells, "buys": buys,
            "shadows": shadows, "candidates": candidates,
            "skipped": decision.skipped, "dropped_shadows": dropped}


def _sell_order(pos: rules.Position, reason: str,
                price_ref: float | None) -> PlannedOrder:
    cid = rules.build_client_order_id(pos.symbol, "sell",
                                      pos.entry_day, seq=_seq_for(pos))
    return PlannedOrder("sell", pos.symbol, pos.qty, reason, cid, price_ref)


def _seq_for(pos: rules.Position) -> int:
    """Sell ids use the entry-day sequence stored on the position."""
    try:
        return int(str(pos.client_order_id).rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return 1


def _deferred_reason(state: StateStore, symbol: str) -> str:
    for item in state.data.get("pending_exits", []):
        if isinstance(item, dict) and item.get("symbol", "").upper() == \
                symbol.upper():
            return item.get("reason", rules.TIME_CAP)
    return rules.TIME_CAP


# --- after-close pass ----------------------------------------------------
def plan_after_close(state: StateStore, inp: AfterCloseInputs) -> dict:
    """Record official bars: flag stop/time-cap exits for the next open."""
    day = rules.parse_date(inp.day)
    pos_list = [rules.Position(
        symbol=rec["symbol"], entry_day=rules.parse_date(rec["entry_day"]),
        entry_price=float(rec["entry_price"]), qty=int(rec["qty"]),
        stop_price=float(rec["stop_price"]),
        client_order_id=rec["client_order_id"])
        for rec in state.positions().values()]
    # calendar guard: pre-compute only the deferred (stop / time-cap)
    # symbols, and never let a decide_exit call below re-evaluate the
    # profit branch on a day whose open is already above entry.
    bars_today = {sym: {"close": (bar or {}).get("close")}
                  for sym, bar in inp.bars.items()}
    _due, deferred = rules.due_exits(pos_list, day, inp.trading_days,
                                     bars_today)
    pending = []
    for sym in deferred:
        pos = next(p for p in pos_list if p.symbol == sym)
        bar = inp.bars.get(sym) or {}
        dec = rules.decide_exit(pos, day, inp.trading_days,
                                day_close=bar.get("close"))
        pending.append({"symbol": sym, "reason": dec.reason,
                        "for_day": inp.day})
    return {"day": inp.day, "pending_exits": pending}


# --- execution (part 2 wires the broker) ---------------------------------
def execute_plan(state: StateStore, plan: dict, broker,
                 data=None) -> int:
    """Submit the plan's orders. Part 1: the broker is unwired -> exit 3."""
    n_orders = len(plan.get("sells", [])) + len(plan.get("buys", []))
    if n_orders == 0:
        return 0
    for order in list(plan.get("sells", [])) + list(plan.get("buys", [])):
        if state.has_order_id(order.client_order_id):
            print(f"skip duplicate {order.client_order_id}", file=sys.stderr)
            continue
        if order.side == "buy":
            broker.submit_opg_buy(order.symbol, order.qty,
                                  order.client_order_id)
        else:
            broker.submit_opg_sell(order.symbol, order.qty,
                                   order.client_order_id)
        state.record_order_id(order.client_order_id)
    return 0


# --- part 2: real passes (Alpaca PAPER data + broker) --------------------
DEADLINE_ET = (rules.MORNING_DEADLINE)     # (9, 28); OPG orders in by then
_MISS = {"canceled", "cancelled", "rejected", "expired", "stopped",
         "replaced", "done_for_day", "suspended"}


def _now(now=None):
    return (now or rules.etc_now()).astimezone(rules.ET)


def within_deadline(now=None) -> bool:
    """True while OPG orders may still be submitted (before 09:28 ET)."""
    n = _now(now)
    return (n.hour, n.minute) < DEADLINE_ET


def _fill(order: dict | None) -> dict:
    """Normalise an Alpaca order into the fill facts we log."""
    order = order or {}
    status = str(order.get("status") or "").lower()
    filled = float(order.get("filled_qty") or 0)
    price = order.get("filled_avg_price")
    return {
        "order_id": order.get("id"),
        "order_status": status or None,
        "filled_qty": filled,
        "fill_price": float(price) if price not in (None, "") else None,
        "fill_time": order.get("filled_at") or order.get("updated_at"),
        "accepted_at": order.get("submitted_at") or order.get("created_at"),
        "filled": filled > 0,
        "missed": bool(status) and not (filled > 0) and status in _MISS,
    }


def _log_intent(state: StateStore, order, fill: dict, *, day: str,
                prior_close=None, expected_open=None, gap=None,
                intended_size: float | None = None, note=None,
                kind: str = "real") -> dict:
    rec = {
        "kind": kind,                       # real | shadow
        "side": order.side, "symbol": order.symbol, "qty": order.qty,
        "reason": order.reason, "client_order_id": order.client_order_id,
        "day": day, "prior_close": prior_close,
        "expected_open": expected_open, "gap": gap,
        "intended_notional": intended_size,
        "submitted": bool(fill.get("order_id") or fill.get("order_status")),
        "fill_price": fill.get("fill_price"), "fill_qty": fill.get("filled_qty"),
        "fill_time": fill.get("fill_time"), "order_status": fill.get("order_status"),
        "order_id": fill.get("order_id"), "missed": bool(fill.get("missed")),
        "note": note,
    }
    state.add_intent(rec)
    return rec


def execute_morning(state: StateStore, plan: dict, broker, data,
                    inp: RealMorningInputs, now=None,
                    started: float | None = None) -> int:
    """Submit the morning OPG plan: SELLS first, then BUYS, all by 09:28 ET.

    Idempotent: a client_order_id already known to state (or already known
    to the broker) is never submitted again. Returns the number of orders
    actually submitted. Raises guards.GuardError (exit 2) on a guard fail.
    """
    delays = getattr(data, "delays", None) or {}
    day = plan["day"]
    submitted = 0
    for order in list(plan.get("sells", [])) + list(plan.get("buys", [])):
        clock_now = _now(now)
        if not within_deadline(now):
            print(f"morning: 09:28 ET cutoff passed; {order.client_order_id} "
                  "NOT submitted", file=sys.stderr)
            state.add_intent({"kind": "real", "side": order.side,
                              "symbol": order.symbol, "qty": order.qty,
                              "reason": order.reason,
                              "client_order_id": order.client_order_id,
                              "day": day, "submitted": False,
                              "missed": True, "note": "after_cutoff"})
            continue
        if started is not None and (clock_now.timestamp() - started
                                    >= 8 * 60):
            print(f"morning: deadline guard; {order.client_order_id} NOT "
                  "submitted", file=sys.stderr)
            continue
        if state.has_order_id(order.client_order_id):
            print(f"skip duplicate {order.client_order_id}", file=sys.stderr)
            continue
        delay = delays.get(order.symbol)
        if delay:
            print(f"morning: {order.symbol} expected open unavailable "
                  f"({delay}); order not submitted", file=sys.stderr)
            state.record_order_id(order.client_order_id)
            state.add_intent({"kind": "real", "side": order.side,
                              "symbol": order.symbol, "qty": order.qty,
                              "reason": order.reason,
                              "client_order_id": order.client_order_id,
                              "day": day, "submitted": False,
                              "missed": True, "note": f"data:{delay}"})
            continue

        # idempotency for a rerun after a crash: ask the broker whether this
        # key was already accepted (plan builders may not carry the map)
        existing = None
        if hasattr(broker, "order_by_client_id"):
            existing = broker.order_by_client_id(order.client_order_id)
        if existing is None:
            if order.side == "buy":
                resp = broker.submit_opg_buy(order.symbol, order.qty,
                                             order.client_order_id)
            else:
                resp = broker.submit_opg_sell(order.symbol, order.qty,
                                              order.client_order_id)
            submitted += 1
        else:
            resp = existing
        state.record_order_id(order.client_order_id)

        if order.side == "buy":
            pc = inp.prior_close.get(order.symbol)
            _log_intent(
                state, order, _fill(resp), day=day, prior_close=pc,
                expected_open=inp.expected_open.get(order.symbol),
                gap=(order.price_ref / pc - 1.0) if pc else None,
                intended_size=(order.qty * order.price_ref)
                if order.price_ref else None)
            state.record_entry(day, 1)
            _log_shadows(state, plan, order, inp, day)
        else:
            _log_intent(state, order, _fill(resp), day=day,
                        expected_open=order.price_ref)
    # every shadow the plan produced is logged once, even if its real order
    # could not be submitted (the shadow set stays exactly two per signal)
    return submitted


def _log_shadows(state: StateStore, plan: dict, order, inp: RealMorningInputs,
                 day: str) -> None:
    for shadow in plan.get("shadows", []):
        if shadow.get("real_client_order_id") != order.client_order_id:
            continue
        if state.shadow_for(order.client_order_id) and any(
                s.get("kind") == shadow["kind"] and s.get("symbol")
                == shadow["symbol"] for s in
                state.shadow_for(order.client_order_id)):
            continue
        # the plan's price is only a REFERENCE (pre-market print); the fill
        # recorded below is the name's official opening-auction price
        price = shadow.get("entry_price")
        rec = {
            "real_symbol": shadow["real_symbol"],
            "real_client_order_id": order.client_order_id,
            "kind": shadow["kind"], "symbol": shadow["symbol"],
            "quintile": shadow.get("quintile"),
            "entry_day": shadow.get("entry_day", day),
            # entry_open = the OFFICIAL opening-auction price of the
            # shadow's name; the pre-market print in the plan is only a
            # reference and must never be treated as the fill.
            "entry_open": None,
            "entry_price": None,
            "entry_price_ref": price,
            "qty": order.qty,
            "stop_price": None, "status": "open",
            "note": None if price else "no_official_open_yet",
        }
        state.add_shadow(rec)


def execute_after_close(state: StateStore, plan: dict, broker, data,
                        inp: RealAfterCloseInputs) -> dict:
    """Record official fills, refresh stops, flag exits, settle shadows.

    Order of work (official daily bars only, never intraday):
      1. real intents still without a fill are looked up once and recorded;
      2. filled entries become positions with stop = fill - 2 x ATR14[t-1];
         unfilled/missed entries are logged as misses (never chased);
      3. filled exits close the position and log the realised trade;
      4. tomorrow's stop / time-cap exits are stored for the morning pass;
      5. every open shadow advances by the same frozen exit rules.
    """
    day = plan["day"]
    bars = inp.bars
    summary = {"settled_entries": 0, "filled_entries": 0,
               "missed_entries": 0, "closed": 0,
               "pending_exits": len(plan.get("pending_exits", []))}

    for rec in list(state.intents()):
        if rec.get("kind") != "real" or rec.get("fill_price"):
            continue
        coid = rec.get("client_order_id")
        order = None
        if hasattr(broker, "order_by_client_id") and coid:
            try:
                order = broker.order_by_client_id(coid)
            except Exception as exc:      # lookup failure: retry next pass
                print(f"after_close: fill lookup {coid} failed ({exc})",
                      file=sys.stderr)
                continue
        fill = _fill(order)
        rec["order_status"] = fill["order_status"]
        rec["fill_qty"] = fill["filled_qty"]
        rec["fill_price"] = fill["fill_price"]
        rec["fill_time"] = fill["fill_time"]
        rec["missed"] = bool(not fill["filled"])
        summary["settled_entries"] += 1
        summary["filled_entries"] += int(fill["filled"])
        summary["missed_entries"] += int(not fill["filled"])

    # 2. entries -> positions (stop from ATR14 as of t-1)
    for rec in list(state.intents()):
        if rec.get("kind") != "real" or rec.get("side") != "buy":
            continue
        if not rec.get("fill_price") or not rec.get("fill_qty"):
            continue
        sym = rec["symbol"].upper()
        if sym in state.positions():
            continue
        atr = None
        if hasattr(data, "atr14"):
            try:
                atr = data.atr14(sym, day)
            except Exception as exc:
                print(f"after_close: ATR14 for {sym} failed ({exc})",
                      file=sys.stderr)
        if atr is None:
            print(f"after_close: no ATR14[t-1] for {sym}; stop not set, the "
                  "position exits on profit / time cap", file=sys.stderr)
        state.open_position({
            "symbol": sym, "entry_day": day,
            "entry_price": rec["fill_price"], "qty": int(rec["fill_qty"]),
            "stop_price": rules.stop_level(rec["fill_price"], atr)
            if atr is not None else None,
            "atr14_t_minus_1": atr,
            "client_order_id": rec["client_order_id"],
        })

    # 3. exits -> realised trades
    for rec in list(state.intents()):
        if rec.get("kind") != "real" or rec.get("side") != "sell":
            continue
        sym = rec["symbol"].upper()
        pos = state.positions().get(sym)
        if not pos:
            continue
        fill_px = rec.get("fill_price") or (bars.get(sym) or {}).get("open")
        if not fill_px:
            continue
        state.close_position(sym, {
            "symbol": sym, "entry_day": pos["entry_day"],
            "entry_price": pos["entry_price"], "qty": pos["qty"],
            "exit_day": day, "exit_price": float(fill_px),
            "exit_reason": rec.get("reason"),
            "entry_client_order_id": pos.get("client_order_id"),
            "exit_client_order_id": rec.get("client_order_id"),
        })
        summary["closed"] += 1

    # 4. tomorrow morning's deferred exits
    state.data["pending_exits"] = list(plan.get("pending_exits", []))

    # 5. shadows
    sh = settle_shadows(state, data, day, inp.trading_days, bars)
    summary.update({"shadows_new": sh["new"],
                    "shadows_closed": sh["closed"],
                    "shadows_pending": sh["pending"]})
    return summary


def _no_atr(symbol, day):
    return None


def shadow_stops(state: StateStore, data, day: str) -> int:
    """Give every open shadow whose entry fill is known its frozen stop."""
    n = 0
    for sh in state.shadows():
        if sh.get("status") != "open" or sh.get("stop_price") is not None:
            continue
        if not sh.get("entry_price"):
            bar = _safe_official(data, sh["symbol"], sh["entry_day"])
            if bar and bar.get("open"):
                sh["entry_price"] = sh["entry_open"] = float(bar["open"])
            else:
                continue
        atr = (getattr(data, "atr14", None) or _no_atr)(
            sh["symbol"], sh["entry_day"])
        if atr is None:
            continue
        sh["atr14_t_minus_1"] = atr
        sh["stop_price"] = rules.stop_level(sh["entry_price"], atr)
        n += 1
    return n


def _safe_official(data, symbol, day):
    try:
        return data.official_bar(symbol, day)
    except Exception:
        return None


def settle_shadows(state: StateStore, data, day: str,
                   trading_days: list[str],
                   bars: dict[str, dict]) -> dict:
    """Advance every open shadow with the SAME frozen exits as real trades.

    A shadow enters at its name's official open on the entry day (the
    morning pass records it; the first after-close pass fills it in from the
    official bar) and is closed on an official daily bar:
      * profit: the first later official open strictly above the entry;
      * stop:   any close <= entry - 2 x ATR14[t-1] -> next official open;
      * cap:    close of entry day + 2 -> next official open.
    A shadow is never ordered; it only moves between 'open' and 'closed'.
    Returns counts for the summary line.
    """
    out = {"new": shadow_stops(state, data, day), "closed": 0, "pending": 0}
    day_d = rules.parse_date(day)
    days = [rules.parse_date(d) for d in trading_days]
    for sh in state.shadows():
        if sh.get("status") != "open":
            continue
        sym = sh["symbol"].upper()
        entry_day = rules.parse_date(sh["entry_day"])
        if not sh.get("entry_price"):
            # shadow_stops() above already filled it when a bar + ATR exist;
            # reaching here means the official open is not available yet
            sh["note"] = "no_official_open_yet"
            continue
        stop = sh.get("stop_price")
        pos = rules.Position(
            symbol=sym, entry_day=entry_day,
            entry_price=float(sh["entry_price"]), qty=int(sh.get("qty", 1)),
            stop_price=float(stop) if stop is not None else float("-inf"),
            client_order_id=sh.get("real_client_order_id", ""))
        if day_d < entry_day:
            continue
        bar = bars.get(sym) or {}
        dec = rules.decide_exit(pos, day_d, days,
                                day_open=bar.get("open"),
                                day_close=bar.get("close"))
        deferred = sh.get("exit_pending")
        if dec.reason == rules.PROFIT:
            sh.update(exit_price=dec.trigger_price, exit_day=day,
                      exit_reason=rules.PROFIT, status="closed")
            sh.pop("exit_pending", None)
            out["closed"] += 1
            continue
        if dec.reason in (rules.STOP, rules.TIME_CAP):
            sh["exit_pending"] = dec.reason
            continue
        if deferred and bar.get("open"):
            # the deferred stop/time-cap sell executes at today's open,
            # exactly like the real OPG sell does
            sh.update(exit_price=float(bar["open"]), exit_day=day,
                      exit_reason=deferred, status="closed")
            sh.pop("exit_pending", None)
            out["closed"] += 1
            continue
        if deferred:
            out["pending"] += 1
    return out


# --- logging -------------------------------------------------------------
LOG_FILE = "events.log"


def log_event(state: StateStore, pass_name: str, day: str, ts: str,
              **fields) -> None:
    """Append one JSON line to <state dir>/events.log (no market data,
    no credentials, no account numbers — counts and symbols only)."""
    rec = {"ts": ts, "day": day, "pass": pass_name}
    rec.update({k: v for k, v in fields.items()})
    try:
        Path(state.dir).mkdir(parents=True, exist_ok=True)
        with open(Path(state.dir) / LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError as exc:                    # logging never kills a pass
        print(f"runner: event log write failed ({exc})", file=sys.stderr)


# --- CLI -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--execute", action="store_true",
                   help="submit PAPER orders (guarded; exit 2 on failure)")
    p.add_argument("--dry-run", action="store_true",
                   help="DEFAULT: print intended orders, place none")
    p.add_argument("--pass", dest="pass_name", default="auto",
                   choices=["auto", "morning", "after_close"])
    p.add_argument("--day", default=None, help="trading day YYYY-MM-DD")
    p.add_argument("--state-dir", default=None,
                   help="state directory (default "
                        "/root/trading-data/gapfade-midcap-paper)")
    p.add_argument("--inputs", default=None,
                   help="JSON file with the morning/after-close inputs "
                        "(offline/dry-run use; live data comes from Alpaca)")
    p.add_argument("--size-fraction", type=float,
                   default=rules.DEFAULT_SIZE_FRACTION)
    p.add_argument("--seed", type=int, default=None,
                   help="RNG seed for deterministic shadow picks")
    p.add_argument("--ignore-window", action="store_true",
                   help="run the pass outside its ET window (dry run only)")
    p.add_argument("--force-pass", action="store_true",
                   help="re-run a pass that already ran today")
    p.add_argument("--dump-json", action="store_true",
                   help="print the data payload the pass consumed "
                        "(symbols/prices only, no account data)")
    return p


def _print_plan(plan: dict) -> None:
    print(f"day={plan['day']}  sells={len(plan['sells'])} "
          f"buys={len(plan['buys'])} shadows={len(plan['shadows'])}")
    for o in plan["sells"]:
        print(f"  SELL(OPG) {o.symbol} qty={o.qty} reason={o.reason} "
              f"ref={o.price_ref} coid={o.client_order_id}")
    for o in plan["buys"]:
        print(f"  BUY(OPG)  {o.symbol} qty={o.qty} ref={o.price_ref:.2f} "
              f"coid={o.client_order_id}")
    for s in plan["shadows"]:
        print(f"  SHADOW(not ordered) {s['kind']} {s['symbol']} "
              f"for {s['real_symbol']} q={s['quintile']}")
    if plan.get("skipped"):
        print("  skipped: " + json.dumps(plan["skipped"], sort_keys=True))


def _trading_days(client, day: str, lookback: int = 30,
                  ahead: int = 10) -> list[str]:
    """Recent trading days (from Alpaca's market calendar) + next dates."""
    day_d = rules.parse_date(day)
    start = (day_d - timedelta(days=lookback * 2)).isoformat()
    end = (day_d + timedelta(days=ahead)).isoformat()
    cal = client.get(f"{client.base}/v2/calendar",
                     params={"start": start, "end": end})
    days = sorted({str(item.get("date", ""))[:10] for item in cal or []
                   if item.get("date")})
    # Only give up when the calendar endpoint returned NOTHING at all (then
    # the requested weekday is all we have); a real calendar that omits the
    # day means it is a holiday, and inventing it would break the time cap.
    if not days and day_d.weekday() < 5:
        days = [day_d.isoformat()]
    return days


def _resolve_day(args_day, client, now) -> str:
    if args_day:
        return rules.parse_date(args_day).isoformat()
    today = _now(now).date().isoformat()
    days = _trading_days(client, today)
    past = [d for d in days if d <= today]
    return past[-1] if past else today


def _build_real_morning(args, state, client, now) -> RealMorningInputs:
    from experiments.gapfade_midcap_paper import earnings as earnings_mod
    from experiments.gapfade_midcap_paper import marketdata
    day = _resolve_day(getattr(args, "day", None), client, now)
    data = marketdata.AlpacaMarketData(
        client, Path(state.dir) / "cache", today=_now(now).date())
    universe = data.members()
    # ATR needs bars from well before the entry day; the shadow vol needs 61
    # closes. 200 calendar days covers both.
    data.load_day(day, universe)
    calendar, _missing = earnings_mod.load_calendar(
        Path(state.dir) / "cache", universe, now=_now(now).astimezone(
            timezone.utc))
    prior = data.prior_close(day)
    opens = data.expected_open(day)
    vols = data.vols(day, universe)
    atr = {s: data.atr14(s, day) for s in universe}
    returns = None
    try:
        returns = {
            "equity": float(client.account().get("equity") or 0.0),
            "buying_power": float(client.account().get("buying_power") or 0.0),
        }
    except Exception as exc:
        print(f"morning: account fetch failed ({exc}); sizing will skip all "
              "entries", file=sys.stderr)
        returns = {"equity": 0.0, "buying_power": 0.0}
    clean = set(data.eligible_clean(day, calendar))
    return RealMorningInputs(
        day=day, trading_days=_trading_days(client, day),
        universe=universe, prior_close=prior,
        expected_open={s: opens[s] for s in prior if s in opens},
        expected_open_all=opens,
        earnings=calendar, eligible_clean=clean, vols=vols,
        atr14={s: v for s, v in atr.items() if v is not None},
        equity=returns["equity"], buying_power=returns["buying_power"],
        sent_at=_now(now).isoformat(), _data=data)


def _build_real_after_close(args, state, client, now) -> RealAfterCloseInputs:
    from experiments.gapfade_midcap_paper import earnings as earnings_mod
    from experiments.gapfade_midcap_paper import marketdata
    day = _resolve_day(getattr(args, "day", None), client, now)
    data = marketdata.AlpacaMarketData(
        client, Path(state.dir) / "cache", today=_now(now).date())
    symbols = sorted(set(data.members()) | set(state.positions())
                     | {s["symbol"].upper() for s in state.shadows()
                        if s.get("status") == "open"}
                     | {r.get("symbol", "").upper() for r in state.intents()
                        if r.get("kind") == "real" and not r.get("fill_price")})
    data.load_day((rules.parse_date(day) + timedelta(days=1)).isoformat(),
                  [s for s in symbols if s])
    bars = {}
    for sym in symbols:
        bar = None
        rows = data.bars.get(sym.upper()) or []
        for row in rows:
            if row["date"] == day:
                bar = row
        if bar:
            bars[sym.upper()] = {"open": bar["open"], "close": bar["close"],
                                 "high": bar["high"], "low": bar["low"]}
        else:
            bar = _safe_official(data, sym.upper(), day)
            if bar:
                bars[sym.upper()] = {"open": bar["open"],
                                     "close": bar["close"],
                                     "high": bar["high"],
                                     "low": bar["low"]}
    if not bars:
        raise SystemExit(f"after_close: no official bars for {day} yet")
    return RealAfterCloseInputs(
        day=day, trading_days=_trading_days(client, day), bars=bars,
        prior_close=data.prior_close(day), _data=data)


def _run_real(args, pass_name, state, now, execute: bool) -> int:
    client = broker_mod.make_client(args)
    rng = random.Random(args.seed)
    if pass_name == "morning":
        inp = _build_real_morning(args, state, client, now)
        now_et = _now(now)
        if execute and not within_deadline(now_et):
            print(f"morning: past the 09:28 ET deadline ({now_et.isoformat()}"
                  "); refusing to submit", file=sys.stderr)
            return guards.EXIT_GUARD_FAILED
        plan = plan_morning(state, inp.as_signal_inputs(), now=now, rng=rng,
                            size_fraction=args.size_fraction)
        plan["reason_by_coid"] = {
            o.client_order_id: o.reason for o in plan["buys"]}
        for sh in plan["shadows"]:
            for o in plan["buys"]:
                if o.symbol == sh["real_symbol"]:
                    sh["real_client_order_id"] = o.client_order_id
        if args.dump_json:
            print(json.dumps({
                "day": plan["day"],
                "prior_close": inp.prior_close,
                "expected_open": inp.expected_open,
                "universe_size": len(inp.universe),
                "buy": [[o.symbol, o.qty, o.price_ref]
                        for o in plan["buys"]],
                "sell": [[o.symbol, o.qty, o.reason] for o in plan["sells"]],
                "shadows": [[s["real_symbol"], s["kind"], s["symbol"]]
                            for s in plan["shadows"]],
            }, sort_keys=True))
        _print_plan(plan)
        if not execute:
            print("DRY RUN — zero orders placed (state unchanged).")
            return 0
        if state.pass_done(plan["day"], "morning") and not args.force_pass:
            print(f"morning pass for {plan['day']} already ran "
                  "(idempotent rerun: nothing to do)", file=sys.stderr)
        n = execute_morning(state, plan, broker_mod.make_broker(
            args, client=client), inp._data, inp, now=now)
        state.record_pass(plan["day"], "morning", _now(now).isoformat())
        state.save()
        log_event(state, "morning", plan["day"], _now(now).isoformat(),
                  submitted=n, sells=len(plan["sells"]),
                  buys=len(plan["buys"]), shadows=len(plan["shadows"]),
                  dropped_shadows=plan.get("dropped_shadows", []))
        print(f"morning: submitted {n} OPG order(s); state saved")
        return 0

    inp = _build_real_after_close(args, state, client, now)
    plan = plan_after_close(state, AfterCloseInputs(
        day=inp.day, trading_days=inp.trading_days, bars=inp.bars))
    if args.dump_json:
        print(json.dumps({"day": plan["day"], "pending_exits":
                          plan["pending_exits"]}, sort_keys=True))
    if not execute:
        print(f"day={plan['day']} pending exits for next open: "
              f"{json.dumps(plan['pending_exits'], sort_keys=True)}")
        print("DRY RUN — zero orders placed (state unchanged).")
        return 0
    summary = execute_after_close(state, plan, broker_mod.make_broker(
        args, client=client), inp._data, inp)
    state.record_pass(plan["day"], "after_close", _now(now).isoformat())
    state.save()
    log_event(state, "after_close", plan["day"], _now(now).isoformat(),
              **summary)
    print(f"after_close: {json.dumps(summary, sort_keys=True)}")
    return 0


def main(argv: list[str] | None = None, execute: bool | None = None,
         ) -> int:
    """CLI entry point.

    `execute` is supplied by `guards.run_cli` (the guarded wrapper used by
    `__main__`); calling `main(argv)` directly derives it from argv and runs
    the same guard. Without any execute flag this is the DRY RUN default.
    """
    args = build_parser().parse_args(argv)
    if args.execute and args.dry_run:
        print("runner: --execute and --dry-run are mutually exclusive",
              file=sys.stderr)
        return guards.EXIT_GUARD_FAILED
    execute = bool(args.execute) if execute is None else bool(execute)

    if execute:
        try:
            guards.check_paper_only()
        except guards.GuardError as exc:
            print(f"runner: {exc}", file=sys.stderr)
            return guards.EXIT_GUARD_FAILED

    now = rules.etc_now()
    pass_name = (rules.choose_pass(now) if args.pass_name == "auto"
                 else args.pass_name)
    if pass_name is None:
        print(f"runner: outside any pass window at {now.isoformat()} ET "
              "(morning 09:20-09:28, after-close from 16:00)",
              file=sys.stderr)
        return 0 if not execute else 1
    if pass_name == "morning" and not execute and not args.ignore_window:
        print(f"runner: morning pass window is 09:20-09:28 ET (now "
              f"{now.isoformat()}); dry run continuing anyway",
              file=sys.stderr)

    state = StateStore(args.state_dir)
    state.load()

    if args.inputs is not None:
        return _main_offline(args, pass_name, state, now, execute)

    try:
        return _run_real(args, pass_name, state, now, execute)
    except broker_mod.alpaca.AuthError as exc:
        print(f"runner: {exc}", file=sys.stderr)
        return guards.EXIT_GUARD_FAILED if execute else 1
    except broker_mod.alpaca.AlpacaError as exc:
        print(f"runner: Alpaca error: {exc}", file=sys.stderr)
        return 1
    except broker_mod.MarketDataError as exc:
        print(f"runner: market data unavailable ({exc}); nothing submitted",
              file=sys.stderr)
        return 1


def _main_offline(args, pass_name, state, now, execute) -> int:
    """Offline path: `--inputs JSON` (dry runs / tests, no network).

    Execute with `--inputs` is REFUSED before anything else: the offline
    payload would let a caller (or a test fixture) replace the real data the
    guards exist for. Real orders only ever come from the live `_run_real`
    path, which fetches its own Alpaca PAPER data.
    """
    if execute:
        print("runner: --execute with --inputs is not supported; live "
              "orders run from Alpaca PAPER data only (drop --inputs)",
              file=sys.stderr)
        return guards.EXIT_GUARD_FAILED
    payload = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    rng = random.Random(args.seed)

    if pass_name == "morning":
        inp = _morning_inputs(payload)
        plan = plan_morning(state, inp, now=now, rng=rng,
                            size_fraction=args.size_fraction)
        plan["reason_by_coid"] = {
            o.client_order_id: o.reason for o in plan["buys"]}
        for sh in plan["shadows"]:
            for o in plan["buys"]:
                if o.symbol == sh["real_symbol"]:
                    sh["real_client_order_id"] = o.client_order_id
        _print_plan(plan)
        for c in plan.get("candidates", []):
            print(f"  CANDIDATE {c.symbol} prior_close={c.prior_close} "
                  f"expected_open={c.expected_open}")
        print("DRY RUN — zero orders placed (state unchanged).")
        return 0

    plan = plan_after_close(state, _after_close_inputs(payload))
    print(f"day={plan['day']} pending exits for next open: "
          f"{json.dumps(plan['pending_exits'], sort_keys=True)}")
    print("DRY RUN — zero orders placed (state unchanged).")
    return 0


def _morning_inputs(payload: dict) -> MorningInputs:
    earnings = rules.EarningsCalendar(
        {k.upper(): set(v) for k, v in
         (payload.get("earnings") or {}).items()})
    universe = [s.upper() for s in payload["universe"]]
    return MorningInputs(
        day=payload["day"], trading_days=payload["trading_days"],
        universe=universe,
        prior_close={k.upper(): v for k, v in
                     payload.get("prior_close", {}).items()},
        expected_open={k.upper(): v for k, v in
                       payload.get("expected_open", {}).items()},
        all_expected_opens={k.upper(): v for k, v in
                            payload.get("all_expected_opens", {}).items()},
        earnings=earnings,
        eligible_clean={s.upper() for s in payload.get("eligible_clean", [])},
        vols={k.upper(): v for k, v in payload.get("vols", {}).items()},
        equity=float(payload.get("equity", 0)),
        buying_power=float(payload.get("buying_power", 0)))


def _after_close_inputs(payload: dict) -> AfterCloseInputs:
    return AfterCloseInputs(
        day=payload["day"], trading_days=payload["trading_days"],
        bars={k.upper(): v for k, v in payload.get("bars", {}).items()},
        all_expected_opens={k.upper(): v for k, v in
                            payload.get("all_expected_opens", {}).items()})


if __name__ == "__main__":
    sys.exit(guards.run_cli(main))
