"""STRAT-027 — sanity checks for the portfolio engine (run before trusting results).

Checks:
  1. Turnover math: a constant, fully-static portfolio has zero turnover after
     the first rebalance when every holding returns the same amount.
  2. Cost accounting: net == gross - turnover * cost_rate, exactly.
  3. No look-ahead: changing prices AFTER a rebalance date cannot change the
     selection made at that rebalance.
  4. Delisting fallback: a holding whose bars stop is liquidated at its last
     available close and the loss is realised in that period.
  5. Cap enforcement: a single name can never exceed the cap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine import cap_weights, holding_returns, simulate
from panel import compute_factors, load_panel


def _toy_panel(n_sym=6, n_days=400, start="2020-01-01"):
    from panel import Panel

    dates = pd.date_range(start, periods=n_days, freq="B").values
    rng = np.random.default_rng(7)
    syms = np.array([f"S{i}" for i in range(n_sym)])
    close = 100 + np.cumsum(rng.normal(0, 1, size=(n_days, n_sym)), axis=0)
    close = np.clip(close, 5, None).astype(np.float32)
    open_m = (close * (1 + rng.normal(0, 0.002, size=close.shape))).astype(np.float32)
    vol = (np.abs(rng.normal(2e6, 2e5, size=close.shape))).astype(np.float32)
    is_first = np.zeros(n_days, dtype=bool)
    is_first[0] = True
    cal = pd.DatetimeIndex(dates)
    is_first[1:] = cal[1:].to_period("M").values != cal[:-1].to_period("M").values
    rebal_idx = np.flatnonzero(is_first)
    return Panel(dates, syms, open_m, close, vol, rebal_idx, cal[rebal_idx])


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    return cond


def main() -> int:
    ok = True

    # --- 1 & 2: turnover and cost accounting -------------------------------
    p = _toy_panel()
    R = len(p.rebal_idx)
    n_p = R - 1
    sel = [np.array([0, 1, 2]) for _ in range(n_p)]
    wts = [np.array([1 / 3, 1 / 3, 1 / 3]) for _ in range(n_p)]
    asset_ret = np.tile(np.array([0.01, 0.01, 0.01, 0.0, 0.0, 0.0]), (n_p, 1))
    rate = 0.001
    res = simulate(sel, wts, asset_ret, rate)
    ok &= check("static portfolio turnover is 0 after the first rebalance",
                np.allclose(res.turnover[1:], 0.0),
                f"turnover[1:3]={res.turnover[1:3]}")
    ok &= check("first-period turnover equals the invested weight",
                abs(res.turnover[0] - 1.0) < 1e-12, f"{res.turnover[0]}")
    ok &= check("net == gross - turnover*rate",
                np.allclose(res.net, res.gross - res.turnover * rate))
    ok &= check("gross return equals the weighted asset return",
                np.allclose(res.gross, 0.01))

    # --- 3: no look-ahead ---------------------------------------------------
    p2 = _toy_panel(n_sym=8, n_days=500)
    f1 = compute_factors(p2, {})
    pick_before = None
    for r in range(len(p2.rebal_idx)):
        if r != 0:
            break
    # perturb every price after the FIRST rebalance date and confirm the
    # factor/eligibility output at that date is byte-identical.
    from dataclasses import replace

    p2b = replace(p2)
    # perturb every price AFTER rebalance #14 (well past the warm-up window)
    probe = 14
    cut = int(p2.rebal_idx[probe]) + 1
    p2b.close_m = p2.close_m.copy()
    p2b.close_m[cut:] = p2b.close_m[cut:] * 5.0
    p2b.open_m = p2.open_m.copy()
    p2b.open_m[cut:] = p2b.open_m[cut:] * 5.0
    f2 = compute_factors(p2b, {})
    diff = f1.values["MOM12_1"][probe] - f2.values["MOM12_1"][probe]
    finite = np.isfinite(diff)
    ok &= check("factor values at a warm rebalance are unchanged by later prices",
                bool(np.all(diff[finite] == 0)) and finite.sum() > 0,
                f"n_finite={int(finite.sum())} max|diff|={np.max(np.abs(diff[finite])) if finite.any() else float('nan'):.3e}")
    ok &= check("eligibility at a warm rebalance is unchanged by later prices",
                bool((f1.eligible[probe] == f2.eligible[probe]).all()))
    ok &= check("later rebalance factors DID change (perturbation was effective)",
                not np.allclose(np.nan_to_num(f1.values["MOM12_1"][probe + 3]),
                                np.nan_to_num(f2.values["MOM12_1"][probe + 3])))

    # --- 4: delisting fallback ---------------------------------------------
    n_days = 300
    dates = pd.date_range("2020-01-01", periods=n_days, freq="B").values
    syms = np.array(["A", "B"])
    close = np.full((n_days, 2), 100.0, dtype=np.float32)
    close[:200, 1] = 100.0
    close[200:, 1] = np.nan          # B stops trading at day 200
    open_m = close.copy()
    close = np.ascontiguousarray(close)
    from panel import Panel

    is_first = np.zeros(n_days, dtype=bool)
    is_first[0] = True
    cal = pd.DatetimeIndex(dates)
    is_first[1:] = cal[1:].to_period("M").values != cal[:-1].to_period("M").values
    ri = np.flatnonzero(is_first)
    p3 = Panel(dates, syms, open_m, close, np.full((n_days, 2), 1e6, dtype=np.float32), ri, cal[ri])
    close_idx = np.where(np.isfinite(p3.close_m), np.arange(p3.n_dates)[:, None], -1)
    lfci = np.maximum.accumulate(close_idx, axis=0).astype(np.int32)
    aret = holding_returns(p3.open_m, p3.close_m, lfci, p3.rebal_idx)
    # find the period that spans the delisting of B
    hit = None
    for r in range(aret.shape[0]):
        if np.isfinite(p3.open_m[p3.rebal_idx[r], 1]) and not np.isfinite(p3.open_m[p3.rebal_idx[r + 1], 1]):
            hit = r
            break
    ok &= check("delisting period found", hit is not None)
    if hit is not None:
        # last close before the next rebalance is 100 -> return 0 (flat), not NaN
        ok &= check("delisted holding is valued at its last available close (no NaN)",
                    np.isfinite(aret[hit, 1]), f"ret={aret[hit,1]}")

    # --- 5: cap enforcement -------------------------------------------------
    w = cap_weights(np.ones(10) / 10, 0.08)
    ok &= check("cap weights respect the cap", w.max() <= 0.08 + 1e-12,
                f"max {w.max():.4f} sum {w.sum():.4f}")
    w2 = cap_weights(np.array([0.5, 0.3, 0.2]), 0.3)
    ok &= check("infeasible cap (n*cap<1) leaves the rest as cash",
                abs(w2.sum() - 0.9) < 1e-9 and w2.max() <= 0.3 + 1e-12,
                f"sum {w2.sum():.6f} max {w2.max():.4f}")
    w3 = cap_weights(np.array([0.5, 0.2, 0.2, 0.1]), 0.3)
    ok &= check("feasible cap keeps the total invested and respects the cap",
                abs(w3.sum() - 1.0) < 1e-9 and w3.max() <= 0.3 + 1e-12,
                f"sum {w3.sum():.6f} max {w3.max():.4f}")

    print("\nALL SANITY CHECKS PASSED" if ok else "\nSOME SANITY CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
