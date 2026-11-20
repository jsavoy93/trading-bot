"""SPLITSCREEN-001: detection + price-level back-adjustment (see PREREGISTRATION.md).

Implementation rules (fixed before any re-run; detection only, no strategy rule):
  * base series: close (open/close/high/low corrected, volume divided by factor)
  * a symbol-day t is evaluated on the CURRENT series of the symbol; which is
    re-derived after every correction, so corrections compose.
  * PERIOD-style flag (frozen clause 1): 1-day close ratio within 3% of
    1:2, 1:3, 1:4, 1:5, 1:10, 1:20, or of 2:1 / 3:1 when the move is < 45%
    (a +100%/+200% day is a real move, not a split tell). Applied by
    back-adjusting prior dates.
  * CURRENT-style flag (frozen clause 2, labelled Q in the report): |move| > 40%
    with no reversal-consistent path. A reversal-consistent path means BOTH the
    day before and the day after undo at least 60% of the move (median band).
    Such a massive jump (e.g. BHVN 151.80 -> 8.30, ratio 0.0547, ~9% off an
    exact 1:20) is a split, not a print move. Applied vendor-style: the whole
    ticker series is divided by the ratio.
  * sign consistency: a print never needs both a period-style and a current-style
    correction.
  * churn guard: >6 flags of the same direction in 2021-2024 -> drop the symbol.
  * pre-2021: every flag is corrected period-style; pre-2021 events on more than
    PRE2021_MAX distinct symbols in a month are the market-wide 2020 path and are
    dropped (DROP_PRE2021_WIDESPREAD).
"""
import json, math
import numpy as np, pandas as pd

TARGETS_P = (0.5, 1/3, 0.25, 0.2, 0.1, 0.05)
TARGETS_UP = (2.0, 3.0)
TOL, MAX_FLAGS, PRE2021_MAX = 0.03, 6, 10


def _near(r, targets, tol):
    return any(abs(r / t - 1.0) <= tol for t in targets)


def _band_ok(r, prev_r, next_r):
    """False when the move is reversed on BOTH neighbouring days by >=60%."""
    if prev_r is None or not np.isfinite(prev_r):
        return True
    if next_r is None or not np.isfinite(next_r):
        return True
    mv = abs(r - 1.0)
    undone_prev = abs(prev_r - 1.0) >= 0.6 * mv and (prev_r - 1.0) * (r - 1.0) < 0
    undone_next = abs(next_r - 1.0) >= 0.6 * mv and (next_r - 1.0) * (r - 1.0) < 0
    return not (undone_prev and undone_next)


def classify(r, prev_r, next_r):
    """return 'P', 'Q' or None for one 1-day close ratio"""
    mv = abs(r - 1.0)
    if not np.isfinite(r) or mv <= 0.10:
        return None
    ok_band = _band_ok(r, prev_r, next_r)
    if r < 1 and _near(r, TARGETS_P, TOL) and (ok_band or mv > 0.90):
        return 'P'
    if r > 1 and _near(r, TARGETS_UP, TOL) and mv < 0.45:
        return 'P'
    if mv > 0.40 and ok_band:
        return 'Q'
    return None


def repair(df):
    df = df.sort_values(['symbol', 'date'], kind='mergesort').reset_index(drop=True)
    flags, dropped = [], {}
    out_parts = []
    for sym, g in df.groupby('symbol', sort=False):
        g = g.copy()
        cols = [c for c in ('open', 'close', 'high', 'low') if c in g]
        for _ in range(25):
            c = g['close'].to_numpy(float)
            r = c[1:] / c[:-1]
            hit = None
            for i in range(1, c.size):
                prev_r = r[i-2] if i >= 2 else None
                next_r = r[i] if i < r.size else None
                kind = classify(r[i-1], prev_r, next_r)
                if kind:
                    hit = (i, kind, float(r[i-1]))
                    break
            if hit is None:
                break
            i, kind, rat = hit
            d = str(g['date'].iloc[i])[:10]
            rec = {'symbol': sym, 'date': d, 'kind': kind, 'ratio': rat,
                   'prev_close': float(c[i-1]), 'close': float(c[i])}
            if d < '2021-01-01' and kind == 'Q':
                kind = 'P'; rec['kind'] = 'P'          # pre-2021: never full-series
                rec['note'] = 'downgraded to period-style (pre-2021)'
            if kind == 'P' and abs(rat - 0.5) <= TOL and abs(c[i+1] / c[i] - 1) < 0.02:
                rec['drop'] = 'DROP_FLAT_AFTER'
            scale = c[i-1] / c[i]
            if kind == 'P':
                idx = g.index[:i]
            else:
                idx = g.index
            for col in cols:
                g.loc[idx, col] = g.loc[idx, col].to_numpy(float) * scale
            if 'volume' in g:
                g.loc[idx, 'volume'] = g.loc[idx, 'volume'].to_numpy(float) / scale
            flags.append(rec)
            if rec.get('drop'):
                dropped_hits = rec
                break
        # churn guard 2021-2024
        mine = [f for f in flags if f['symbol'] == sym]
        if any(f.get('drop') for f in mine):
            for f in mine:
                f.pop('drop', None)
            dropped[sym] = 'DROP_FLAT_AFTER'
            continue
        fl = pd.DataFrame(mine)
        if len(fl):
            fl = fl[(fl.date >= '2021-01-01') & (fl.date <= '2024-12-31')]
            if (fl.ratio < 1).sum() > MAX_FLAGS or (fl.ratio > 1).sum() > MAX_FLAGS:
                dropped[sym] = 'DROP_CHURN'
                continue
        out_parts.append(g)
    out = pd.concat(out_parts).sort_values(['symbol', 'date'], kind='mergesort').reset_index(drop=True)
    # widespread pre-2021 path guard
    fl = pd.DataFrame(flags)
    if len(fl):
        pre = fl[(fl.date < '2021-01-01') & (fl.kind == 'P')].copy()
        if len(pre):
            pre['month'] = pre.date.str[:7]
            per_month = pre.groupby('month').symbol.nunique()
            bad_months = set(per_month[per_month > PRE2021_MAX].index)
            bad_syms = set(pre[pre.month.isin(bad_months)].symbol)
            for s in bad_syms:
                dropped[s] = f'DROP_PRE2021_WIDESPREAD({",".join(sorted(bad_months))})'
            out = out[~out.symbol.isin(bad_syms)].reset_index(drop=True)
        flags = [f for f in flags if f['symbol'] not in set(dropped)]
    return out, flags, dropped
