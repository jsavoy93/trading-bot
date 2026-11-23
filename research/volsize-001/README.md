# VOLSIZE-001 — vol-scaled position sizing (research script)

Step 1 of VOLSIZE-001: the research **script only**. Design is frozen in
`PREREGISTRATION.md`; this directory produces no committed results.

## What it does
- Baseline: MOM 12-1 long-short, equal-weight decile legs (frozen engine).
- V1: inverse-volatility weights within each leg, 3x-equal-weight cap, turnover cost.
- V2: volatility-target scaling of the baseline book (15% target, clamp 0.25..1.5).
- DEV window only (months 2021-01 .. 2024-12); holdout rows are never loaded
  (parquet row filter + assert at load time).

## Run

Full study (DEV, 1000 random draws per variant):

    python3 research/volsize-001/run_volsize.py

Smoke test (3 DEV months, 20 draws):

    python3 research/volsize-001/run_volsize.py --smoke --n-random 20

Flags:
- `--n-random N` — random draws per variant (default 1000)
- `--smoke` — first 3 DEV months, 20 draws (unless `--n-random` overrides)
- `--panel PATH` — DEV parquet panel (default `/tmp/panel_dev.parquet`)
- `--out-dir DIR` — output dir (default: this folder)

On run it writes `results.json` and `results.md` here (local only, git-excluded).
