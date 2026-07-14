# Agent Repair Backlog

## Phase A — Trustworthy Tests

- Replace side-effect-heavy scripts with repeatable tests.
- Fix syntax/runtime errors in analysis modules.
- Add indicator calculation tests.
- Add BUY, SELL, and HOLD decision tests.
- Add owned vs unowned position tests.
- Add paper-mode safety tests.
- Add settings loading tests.
- Ensure tests cannot contact live brokerage endpoints.

## Phase B — Settings and Configuration

- Create authoritative strategy configuration object.
- Load dashboard/database settings into that object.
- Remove unused hardcoded thresholds where appropriate.
- Validate settings at startup.
- Log effective settings each session.
- Ensure dashboard settings affect engine behavior.

## Phase C — Score Normalization

- Normalize indicator scores to predictable ranges.
- Keep combined score within 0–100.
- Document indicator weights.
- Separate signal eligibility from candidate ranking.
- Add bullish, neutral, and bearish scoring tests.

## Phase D — Execution Paths

- Verify daily-only analysis.
- Verify multi-timeframe analysis.
- Verify BUY order flow.
- Verify SELL order flow.
- Verify position lookup.
- Verify duplicate-order prevention.
- Verify market-hours behavior.
- Verify paper-order submission.
- Verify restart/recovery behavior.

## Phase E — Stock Universe

- Exclude ETFs, warrants, units, rights, preferred shares, OTC instruments, inactive assets, nontradable assets, illiquid stocks, and symbols without enough history.
- Add representative classification tests.

## Phase F — Dashboard

- Inventory existing settings.
- Create configuration schema.
- Build read-only status page.
- Add decision funnel.
- Add per-symbol explanations.
- Build organized settings UI.
- Add configuration versioning and rollback.
- Add safe paper-bot controls.

