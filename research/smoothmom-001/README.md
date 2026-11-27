# SMOOTHMOM-001 — smooth vs spiky momentum (research script)

Step 1 of SMOOTHMOM-001: preregistration + research **script only**. The design is
frozen in `PREREGISTRATION.md`; this directory commits no results.

## What it does
- Baseline: MOM 12-1 long-short, equal-weight decile legs (frozen engine
  `engine_fixed.py`, an unchanged vendored copy of `research/volsize-001/engine_fixed.py`).
- Smoothness ID = sign(cum 12-1 return) x (% neg days - % pos days) over trading days
  t-252..t-21, lower = smoother.
- V1 double sort: among the top 20% by MOM, long the low-ID half; among the bottom 20%
  by MOM, short the low-ID half (smooth losers).
- V2 combined score: mean(MOM pct rank, (-ID) pct rank); long top decile, short bottom
  decile.
- DEV window only (months 2021-01 .. 2024-12). Holdout rows are never loaded (parquet
  row filter + assert at load time).
- The full run asserts the DEV baseline reproduces mean net 1.708%/mo, t 2.294, maxDD
  -27.7% and exits non-zero otherwise.

## Environment
Plain system Python 3 with numpy / pandas / pyarrow (the same environment
`research/volsize-001/README.md` documents for the research scripts; verified with
numpy 2.5.4, pandas 3.1.0, pyarrow 25.0.1). No extra packages, no API keys.

## Data
- DEV panel: `/tmp/panel_dev.parquet` (symbol, date, open, close, volume).
  Override with `--panel PATH`.

## Run

Full study (DEV 2021-01..2024-12, 1000 random draws per variant) — this is what step 2 runs:

    python3 research/smoothmom-001/run_smoothmom.py

Run it from the repo/worktree root (`/root/.openclaw/worktrees/smoothmom-001`).

Smoke test (first 3 DEV months, 20 draws; mechanics only):

    python3 research/smoothmom-001/run_smoothmom.py --smoke

Flags:
- `--n-random N` — random draws per variant (default 1000)
- `--smoke` — first 3 DEV months, 20 draws unless `--n-random` overrides
- `--panel PATH` — DEV parquet panel (default `/tmp/panel_dev.parquet`)
- `--out-dir DIR` — output dir (default: this folder)

## Outputs
On run it writes `results.json` and `results.md` into this folder (or `--out-dir`).
They are local artifacts only — delete them / never commit them.
