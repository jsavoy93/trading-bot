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
            shadows.append({
                "real_symbol": p.symbol, "kind": pick.kind,
                "symbol": pick.symbol, "quintile": pick.quintile,
                "entry_day": day.isoformat(),
                "entry_price": inp.all_expected_opens.get(pick.symbol),
            })
            chosen.add(pick.symbol)

    return {"day": day.isoformat(), "sells": sells, "buys": buys,
            "shadows": shadows, "candidates": candidates,
            "skipped": decision.skipped}


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
    _due, deferred = rules.due_exits(pos_list, day, inp.trading_days,
                                     inp.bars)
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
                        "(part 2 replaces this with Alpaca PAPER data)")
    p.add_argument("--size-fraction", type=float,
                   default=rules.DEFAULT_SIZE_FRACTION)
    p.add_argument("--seed", type=int, default=None,
                   help="RNG seed for deterministic shadow picks")
    p.add_argument("--ignore-window", action="store_true",
                   help="run the pass outside its ET window (dry run only)")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute and args.dry_run:
        print("runner: --execute and --dry-run are mutually exclusive",
              file=sys.stderr)
        return guards.EXIT_GUARD_FAILED
    execute = bool(args.execute)          # dry run is the default

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
    if pass_name == "morning" and not execute and not args.ignore_window \
            and not rules.morning_window(now):  # informational only
        print(f"runner: morning pass window is 09:20-09:28 ET (now "
              f"{now.isoformat()}); dry run continuing anyway",
              file=sys.stderr)

    state = StateStore(args.state_dir)
    state.load()
    if args.inputs is None:
        print("runner: --inputs JSON is required in part 1 "
              "(part 2 reads Alpaca PAPER data directly)", file=sys.stderr)
        return EXIT_NOT_WIRED
    payload = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    rng = random.Random(args.seed)

    if pass_name == "morning":
        inp = _morning_inputs(payload)
        plan = plan_morning(state, inp, now=now, rng=rng,
                            size_fraction=args.size_fraction)
    else:
        plan = plan_after_close(state, _after_close_inputs(payload))
        if not execute:
            print(f"day={plan['day']} pending exits for next open: "
                  f"{json.dumps(plan['pending_exits'], sort_keys=True)}")
            print("DRY RUN — zero orders placed (state unchanged).")
            return 0
        state.data["pending_exits"] = plan["pending_exits"]
        state.save()
        return 0

    if not execute:
        _print_plan(plan)
        print("DRY RUN — zero orders placed.")
        return 0

    try:
        rc = execute_plan(state, plan, broker_mod.make_broker(args))
    except broker_mod.NotWired as exc:
        print(f"runner: {exc}", file=sys.stderr)
        return EXIT_NOT_WIRED
    state.save()
    return rc


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
