# STRAT-SWING-PAPER — RSI(2) paper launcher under version control

**Task:** Put the RSI(2) paper launcher under version control and open a PR (no merge, no deploy).
**Approval:** Josh approved opening a PR on 2026-11-04. Merge and deploy explicitly excluded.
**Branch:** `agent/swing-rsi2-launcher-vc-20261104`
**Commit:** `3f69f72`
**PR:** https://github.com/jsavoy93/trading-bot/pull/138 (OPEN, not merged)
**Model that completed the work:** `minimax-portal/MiniMax-M3` (current runtime model identity; no model fallback observed)

---

## Executive Summary

The daily RSI(2) paper run is launched by `run_daily.sh`, the `ExecStart` target of
the `swing-rsi2-paper.service` user unit. That script existed only on the server at
`/root/deploy/rsi2-swing-run/run_daily.sh` and was **not tracked in the repository**.
That is how the 2026-11-04 run broke: the final `exec` line omitted the required
`--symbols` flag, so the runner's argparse rejected the invocation and the unit exited 2
(`INVALIDARGUMENT`). No orders were placed.

This task brought the **corrected** script under version control on a fresh branch off
`main` @ `e1f53a3`, and added a test that parses the real launch command and feeds it to
the runner's **real** argparse, pinning the launch contract so the same class of
breakage fails in CI instead of on the server at 21:30 UTC.

**No threshold was changed. No deployed server file, timer, or systemd unit was touched.
Paper trading only. The PR is open and unmerged.**
## Files Changed

| File | Change |
|---|---|
| `experiments/swing_paper/deploy/run_daily.sh` | **ADDED** — byte-identical to the fixed deployed script; mode `0755` preserved |
| `experiments/swing_paper/deploy/README.md` | **ADDED** — launch contract, kill switch, paper-only posture |
| `tests/test_swing_paper_launcher.py` | **ADDED** — 17 tests pinning the launch contract |
| `experiments/swing_paper/README.md` | **MODIFIED** — points at the launcher and its test (+11 lines) |

Diff stat: 4 files changed, 413 insertions(+), 0 deletions(-).

---

## Tests Run

### swing_paper suite (the task's required scope)

```bash
/root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest \
  tests/test_swing_paper_runner.py tests/test_swing_paper_rules.py \
  tests/test_swing_paper_report.py tests/test_swing_paper_launcher.py -q
```

**Exact result: `161 passed, 1 warning in 18.15s`** — 0 failed, 0 skipped.

Baseline on `main` @ `e1f53a3` (same three pre-existing files, launcher test absent):
**`144 passed, 1 warning in 17.31s`** → **+17 new tests, zero regressions.**

### Full repository suite

```bash
/root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest -q
```

**Exact result: `1 failed, 2298 passed, 2 skipped, 13 deselected, 83 warnings in 142.98s`**

The single failure is `tests/test_strat026_shadow.py::TestSystemdUnits::test_units_not_installed`,
which is **pre-existing and environment-dependent** (the strat026 shadow units are
installed on this host). Verified identical on the untouched base commit:

```bash
cd /root/.openclaw/clean/trading-bot && pytest -q tests/test_strat026_shadow.py
# => 1 failed, 29 passed  (same test, same reason)
```

**Zero new failures introduced by this change.**

### Mutation verification — the tests have teeth

Each mutation was applied to the launcher, the suite was run, and the launcher was
restored byte-identically afterward (`diff` confirmed identical to the deployed copy).

| Mutation | Result | Tests that caught it |
|---|---|---|
| Remove `--symbols` (**the exact 2026-11-04 regression**) | **8 failed, 9 passed** | `test_launch_command_includes_symbols_flag`, `test_no_unexpected_threshold_flags`, `test_runner_argparse_accepts_launch_command`, `test_parsed_values_match_limits`, + 4 constant-drift tests |
| `--sleeve-pct 0.15` → `0.20` | **3 failed, 14 passed** | `test_launch_command_includes_current_limits`, `test_parsed_values_match_limits`, `test_sleeve_pct_matches_rules` |
| Remove `--execute` | **3 failed, 14 passed** | `test_launch_command_includes_execute`, `test_no_unexpected_threshold_flags`, `test_parsed_values_match_values` |
| `--rsi2-cap 4` → `8`, `--max-open 4` → `6` | **4 failed, 13 passed** | `test_launch_command_includes_current_limits`, `test_parsed_values_match_limits`, `test_rsi2_cap_matches_rules`, `test_max_open_matches_rules` |

After restore: `17 passed` again, and `diff` against the deployed script reported IDENTICAL.

---

## Acceptance Evidence

| # | Acceptance criterion | Proof method | Exact result | Status |
|---|---|---|---|---|
| 1 | Clean worktree off main (`e1f53a3`) | `git worktree add ... -b <branch> origin/main` | `HEAD is now at e1f53a3`; `git status --short` empty | **PASS** |
| 2 | New branch created | `git branch` | `agent/swing-rsi2-launcher-vc-20261104` | **PASS** |
| 3 | Current fixed `run_daily.sh` added under `experiments/swing_paper/deploy/` | `cp` + `diff` vs deployed | **IDENTICAL** to the deployed fixed script | **PASS** |
| 4 | No secrets in it | regex scan + `test_no_secret_material_in_launcher` | Only variable **names** referenced; values from external `.env`. PASS | **PASS** |
| 5 | Executable bit preserved | `git ls-files -s` | `100755 ... deploy/run_daily.sh` | **PASS** |
| 6 | Test confirms `--symbols` | `test_launch_command_includes_symbols_flag` | PASS; RED under mutation 1 | **PASS** |
| 7 | Test confirms `--execute` | `test_launch_command_includes_execute` | PASS; RED under mutation 3 | **PASS** |
| 8 | Test confirms sleeve `0.15` | `test_launch_command_includes_current_limits` | PASS; RED under mutation 2 | **PASS** |
| 9 | Test confirms rsi2-cap `4` | same | PASS; RED under mutation 4 | **PASS** |
| 10 | Test confirms max-open `4` | same | PASS; RED under mutation 4 | **PASS** |
| 11 | Test confirms per-position-cap `0.25` | same | PASS | **PASS** |
| 12 | Runner's argument parser accepts that command | `test_runner_argparse_accepts_launch_command` — real `runner.main()` argparse exercised, `runner.run` stubbed so nothing executes | PASS; namespace asserted field-by-field in `test_parsed_values_match_limits` | **PASS** |
| 13 | Launcher cannot drift from `rules.py` constants | `TestLauncherMatchesStrategyConstants` (4 tests) vs `RSI2_SLEEVE_PCT`, `RSI2_DAILY_CAP`, `RSI2_MAX_OPEN_POSITIONS`, `RSI2_PER_POSITION_CAP_PCT` | 4 passed | **PASS** |
| 14 | swing_paper tests run, real pass count | pytest over the 4 swing_paper files | **161 passed, 0 failed** (baseline 144 → +17) | **PASS** |
| 15 | PR opened on `jsavoy93/trading-bot` | `gh pr create --base main` | **https://github.com/jsavoy93/trading-bot/pull/138**, `state: OPEN` | **PASS** |
| 16 | NOT merged | `gh pr view 138 --json state,mergedAt` | `state: OPEN`, `mergedAt: null`, `auto-merge: disabled` | **PASS** |
| 17 | Deployed server files NOT changed | `diff` vs `/root/deploy/rsi2-swing-run/run_daily.sh` | IDENTICAL (unchanged since the 22:13 server fix) | **PASS** |
| 18 | Timers / units NOT changed | `systemctl --user list-timers --all`; `cat` | `swing-rsi2-paper.timer` next fires `Thu 2026-11-05 21:30:00 UTC`; `ExecStart` unchanged | **PASS** |
| 19 | No threshold changed | diff review | Only files added + a README pointer; no `rules.py`/`runner.py`/settings change | **PASS** |
| 20 | `/root/.openclaw/workspace/trading-bot` never touched | `git -C ... status --short` | Pre-existing dirty state unchanged; only the venv interpreter was *read* | **PASS** |
| 21 | Paper trading only | `export TRADING_BOT_PAPER_ONLY=1`; runner hard-locks paper URL | `test_launcher_exports_paper_only`, `test_launcher_points_at_paper_host_only` | **PASS** |
| 22 | No recursive chmod/chown under `/root` | only `chmod 0755` on the one file I created | no recursive permission change | **PASS** |

**Overall acceptance result: PASS** — 22 of 22 criteria.

---

## What the test actually does

`tests/test_swing_paper_launcher.py` (17 tests) — no network, no broker, no state writes.
The launcher is only ever **read and parsed**, never executed.

1. `parse_launch_command()` joins the shell's backslash line-continuations, isolates the
   final `exec` line, `shlex.split()`s it, drops the shell-resolved `$PY` / `$RUNNER`
   head, and substitutes a concrete placeholder for the shell-resolved `$SYMBOLS` list.
   What remains is the exact flag vector the runner receives.
2. `_parse_with_runner()` calls the runner's **real** `main()` — which builds the real
   `argparse` parser — with `runner.run` monkeypatched to a no-op that captures the
   parsed namespace. So a missing, renamed, or unknown flag raises `SystemExit(2)`
   **in the test**, not on the server at 21:30 UTC.

Classes: `TestLauncherTracked` (4), `TestLaunchCommandFlags` (4),
`TestRunnerAcceptsLaunchCommand` (2), `TestLauncherMatchesStrategyConstants` (4),
`TestPaperOnlyPosture` (3).

---

## Non-Blocking Findings

- The launcher references host-specific paths by design (`/root/deploy/rsi2-swing`,
  `/root/.openclaw/workspace/trading-bot/.env`, and the STRAT-006R universe file,
  which lives only in the workspace checkout and is **not** in this repository). These
  were deliberately left as-is so the tracked file stays byte-identical to the deployed
  one. The file is a reviewable source of truth, not a portable installer. This is
  documented in `deploy/README.md` and in the PR "Review notes".
- The unit still shows `failed (status=2)` from the 21:30 run. That is the **pre-fix**
  failure state and is expected: nothing was redeployed, as instructed. The next timer
  firing (Thu 2026-11-05 21:30:00 UTC) will use the already-fixed server script.
- `pytest.ini` carries an unknown `timeout` option warning (pre-existing, repo-wide).

---

## Risks

- **Low.** The change is additive: two new files under `experiments/swing_paper/deploy/`,
  one new test file, and a README pointer. No runtime code path, strategy parameter,
  or threshold was touched.
- Residual risk is **documentation drift**: if the deployed server script is edited again
  without a matching repo commit, the two diverge. This PR closes the *version-control*
  gap but does not add a CI check that the deployed copy matches the tracked one — that
  would require the server file to be reachable from CI, which is a separate task.
- Merging this PR changes **no** trading behavior. Deploying the tracked script to the
  server would be a no-op today (the files are already identical), but it remains a
  separate, explicit step.

---

## Manager Decision

**Decision: DONE — PR opened, awaiting independent review (ENG-07) and Josh's merge
decision. NOT merged. NOT deployed.**

Implementation is complete and every acceptance criterion is proven with evidence.
Per the stale-task rule, this task was active continuously and is not stale.

The independent reviewer-agent verdict (ENG-07) has **not** been obtained yet. That
gate must clear before Josh is asked to merge.

---

## Next Recommended Action

1. Independent reviewer-agent (ENG-07) reads the PR #138 diff against the 22 acceptance
   criteria and returns `APPROVE` or `BLOCK` with reasons. The reviewer is read-only
   and never edits, commits, pushes, or merges.
2. If `APPROVE` → present PR #138 to Josh for merge approval.
3. If `BLOCK` → resolve on the branch, re-review, and re-present.
4. Separate future task (parking lot, not authorized by this PR): a CI check that the
   deployed `/root/deploy/rsi2-swing-run/run_daily.sh` matches the tracked copy, so
   server-side edits cannot silently drift again.

**Josh's approval is required before merge.**
