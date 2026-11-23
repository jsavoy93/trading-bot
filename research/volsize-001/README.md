# VOLSIZE-001 — vol-scaled position sizing (research)

Step 1: the research **script** (design frozen in `PREREGISTRATION.md`).
Step 2: results and findings in **`research/volsize-001.md`** (with
`results.json` / `results.md` from the single full run).

## What it does
- Baseline: MOM 12-1 long-short, equal-weight decile legs (frozen engine).
- V1: inverse-volatility weights within each leg, 3x-equal-weight cap, turnover cost.
- V2: volatility-target scaling of the baseline book (15% target, clamp 0.25..1.5).
- DEV window only (months 2021-01 .. 2024-11, i.e. exit opens <= 2024-12-31);
  holdout rows are never loaded (parquet row filter + assert at load time).

## Panel
DEV-only artifact actually used by the step-2 run:

    /tmp/panel_dev.parquet
    sha256 cf70f94c31fcec21350ab1aa60a8a599f6bf88f8f344aa283ebc764b23b861f1
    dates 2019-01-02 .. 2024-12-31 (6,743,965 rows)

Built from the repo's DEV/yearly dataset with `scripts/build_panel.py`
(same engine that produced the full `panel_holdout.parquet`, sha256 `4387206b...`).
The full panel is also accepted: the loader row-filters `date <= 2024-12-31`
and asserts the window, so no holdout row is read. Prefer the DEV file.

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

On run it writes `results.json` and `results.md` here. The step-2 run's outputs
are committed (they were force-added; `.git/info/exclude` ignores them locally).

Determinism: the run prints a sha256 of the DEV monthly-net vectors. The step-2
run produced `092a83a80e3e65ce3859cad7659bf5fbdce210a9e9ef42513a0232d3e8e5c35a`.
