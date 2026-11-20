# HOLDOUT-MOM-002 — pre-registration (written and committed BEFORE any run)

Task: fix ONE engine defect found by HOLDOUT-MOM-001 (the block-seam padded-tail
exit) and re-run MOM 12-1 DEV once, then the HOLDOUT exactly once.
Parent: HOLDOUT-MOM-001, branch `research/holdout-mom-001`, commit b9e1fed.
Josh approved this single fix-and-rerun on 2026-11-20. NO other change is allowed.

## 1. The exact bug (as found by HOLDOUT-MOM-001)

`engine.compute_month_data` takes the delisting/halt fallback exit price from the
FORWARD tail of a symbol's slice of the panel:
`finite.shape[0] - 1 - np.argmax(finite[::-1, :], axis=0)` — i.e. the last finite
row in the slice `close[e:x, :]`. That is correct only if each symbol's rows form
one contiguous block. They do not: `engine.load_panel` calls
`m[di, si] = ...` where `di` comes from `pd.Index(dates).get_indexer(df["date"])`,
a WEEKDAY order statistic. When the source parquet is date-major, the weekday
order differs from chronological order inside 2024, so the mask gathers rows from
a date-major layout and each symbol's rows become an interleaved block.

Consequence (block-seam defect): a name held across the seam and missing a real
exit open (halt / delisting fallback) picks up the tail rows of its block, which
are a PADDED / fill price from another period instead of a real price. It drove
2026-07 down to -22.6% (52% of the whole holdout loss) and also hit 2024-07 in
the DEV window. The simulated number is contaminated; the strategy result is not
known yet.

## 2. The exact fix (only this may change)

- `engine.load_panel`: sort the long frame by `(symbol, date)` with a stable sort
  BEFORE building the `di`/`si` mask, so every symbol's rows are contiguous and in
  date order. This makes the delisting/halt fallback scan the symbol's real
  history. Control flow, selection, ranking, costs, borrow and every parameter are
  untouched.
- Held names across a block seam must be marked/exited at REAL panel prices, never
  padded/fill prices. If a held name has no finite close at all inside its real
  holding window, it exits at its LAST REAL close; the number of times that
  fallback is used is counted (`fallback_last_real_close`) and reported for DEV
  and for HOLDOUT.
- A unit test must show that a name held across a block seam exists from a real
  price (a synthetic date-major block with a known last real close; no padded
  price may enter the return).

## 3. Frozen, unchanged (asserted, not re-derived)

- MOM 12-1 rule (12-1 momentum P(M-2)/P(M-13)-1), decile 10%, long top / short
  bottom, volatility NOT evaluated.
- Costs 0.15% per side on open and on full close; borrow 1%/yr pro-rata on
  held days; 252 trading days.
- Seeds: seed 20261112, 1000 random draws, same `random_monthly_nets`.
- Data panel: the SAME split-records panel/method as HOLDOUT-MOM-001
  (`scripts_holdout/panel_holdout.parquet`, sha256
  4387206b0c4b1ad396596b6f84874b0ad44901237c5ae30adc530e1b8ae5c071, built by
  `build_holdout_panel.py`, 6258 symbols, 2019-01-02..2026-09-29). The panel is
  reused byte-identically, never rebuilt or modified.
- DEV window 2021-01..2024-12 (47 gate months, exit_date <= 2024-12-31);
  HOLDOUT window 2025-01..2026-08 (20 months, last exit 2026-09-29), run ONCE.
- All DEV bars and all HOLDOUT bars below are frozen and unchanged.

## 4. DEV bars (verbatim from LONGSHORT-001 PREREGISTRATION.md, section 195-215)

1. at least **24** months,
2. mean monthly **net > 0**,
3. mean monthly **excess > 0**,
4. **percentile >= 97.5**,
5. monthly **t-statistic on excess >= 2.24**.

## 5. HOLDOUT bars (verbatim, same file)

**HOLDOUT** (k = number of dev passers; run once) — all five must hold:

1. at least **12** months,
2. mean monthly **net > 0**,
3. mean monthly **excess > 0**,
4. **percentile >= 1 - 0.05/k** (for k=1 -> 95.0; for k=2 -> 97.5),
5. monthly **t >= 2.0** if k = 1, else the **two-sided 0.05/k cutoff** on the t
   distribution with n-1 degrees of freedom. The cutoff **value used** is stated in
   `RESULTS.md` (for k=2, n=20 months -> t_{0.975,19} ~= 2.093).

A variant **PASSES** only if it passes **both** dev and holdout. A variant that fails
dev is **REJECTED** at dev and its holdout is never computed.

## 6. Run rule

DEV first. If ANY DEV bar fails, STOP, the holdout is not run, and the report is
FAIL at DEV. If DEV passes all five, the HOLDOUT is run exactly ONCE with no
tweaks and no re-run after seeing the result. Only plumbing fixes are allowed after
that point, and each must be stated in the report.
