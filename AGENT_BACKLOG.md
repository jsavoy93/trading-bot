# Agent Repair Backlog

Agents may work only on items listed here or explicitly approved by Josh.

## Status Values

- TODO
- IN_PROGRESS
- BLOCKED
- REVIEW
- DONE

---

## Phase O — Governance

### OPS-001 — Manager timebox and stale-task reporting

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- Every manager task report includes task start time, task end time, and elapsed time.
- Every manager task report states whether the task ran continuously or was resumed later.
- The manager reports blocked or stale status when a task cannot complete in reasonable time.
- Before continuing a resumed task, the manager explains why it was paused and what changed.

Allowed areas:

- AGENTS.md
- AGENT_OPERATING_PLAN.md
- AGENT_BACKLOG.md

## Phase A — Trustworthy Tests

### TEST-001 — Prevent live brokerage calls from tests

Status: TODO  
Owner: trading-exec  
Priority: P0

Acceptance criteria:

- Automated tests cannot contact a live brokerage endpoint.
- Paper or mocked brokerage clients are used.
- A test fails if live mode is enabled.

Allowed areas:

- tests/
- test configuration
- brokerage client abstractions

### TEST-002 — Indicator calculation tests

Status: TODO  
Owner: trading-exec  
Priority: P0

Acceptance criteria:

- Known inputs produce expected RSI, SMA, MACD, Bollinger, and volume values.
- Bullish, neutral, and bearish examples are covered.

### TEST-003 — Decision-path tests

Status: TODO  
Owner: trading-exec  
Priority: P0

Acceptance criteria:

- BUY, SELL, and HOLD paths are tested separately.
- Owned and unowned position behavior is tested.
- Duplicate-order prevention is tested.

### TEST-004 — Settings loading tests

Status: TODO  
Owner: trading-exec  
Priority: P0

Acceptance criteria:

- Defaults load correctly.
- Database/dashboard overrides load correctly.
- Invalid settings are rejected.

---

## Phase B — Configuration

### CONFIG-001 — Authoritative strategy configuration

Status: TODO  
Owner: trading-exec  
Priority: P1

Acceptance criteria:

- One schema defines effective strategy settings.
- The bot, tests, logs, and dashboard use the same schema.
- Effective values are logged at startup.

### CONFIG-002 — Dashboard-to-engine synchronization

Status: TODO  
Owner: dashboard-agent  
Priority: P1

Acceptance criteria:

- Every displayed strategy setting maps to a real engine setting.
- Saving a setting changes the next approved paper session.
- Invalid settings are rejected.

---

## Phase C — Scoring

### SCORE-001 — Normalize indicator scores

Status: TODO  
Owner: trading-exec  
Priority: P2

Acceptance criteria:

- Each indicator has a documented bounded range.
- Combined score remains within 0–100.
- MACD cannot dominate through incompatible scale.
- Bullish, neutral, and bearish tests pass.

### SCORE-002 — Separate eligibility from ranking

Status: TODO  
Owner: trading-exec  
Priority: P2

Acceptance criteria:

- Core strategy gates determine eligibility.
- Score ranks otherwise eligible candidates.
- Score does not silently duplicate strategy gates.

---

## Phase D — Execution Paths

### EXEC-001 — Repair daily-only analysis

Status: TODO  
Owner: trading-exec  
Priority: P2

### EXEC-002 — Verify multi-timeframe analysis

Status: TODO  
Owner: trading-exec  
Priority: P2

### EXEC-003 — Verify BUY and SELL order paths

Status: TODO  
Owner: trading-exec  
Priority: P2

### EXEC-004 — Verify restart and recovery behavior

Status: TODO  
Owner: trading-exec  
Priority: P2

---

## Phase E — Stock Universe

### UNIVERSE-001 — Common-stock filtering

Status: TODO  
Owner: trading-exec  
Priority: P3

Acceptance criteria:

- ETFs, funds, warrants, units, rights, preferred shares, OTC assets, inactive assets, and nontradable assets are excluded.
- Representative classification tests exist.

### UNIVERSE-002 — Liquidity and history filters

Status: TODO  
Owner: trading-exec  
Priority: P3

---

## Phase F — Dashboard

### DASH-001 — Existing settings inventory

Status: TODO  
Owner: dashboard-agent  
Priority: P1

### DASH-002 — Read-only bot status page

Status: TODO  
Owner: dashboard-agent  
Priority: P1

### DASH-003 — Decision funnel

Status: TODO  
Owner: dashboard-agent  
Priority: P2

### DASH-004 — Per-symbol explanation

Status: TODO  
Owner: dashboard-agent  
Priority: P2

### DASH-005 — Versioned settings and rollback

Status: TODO  
Owner: dashboard-agent  
Priority: P3

### DASH-006 — Restricted paper-bot controls

Status: BLOCKED  
Owner: dashboard-agent  
Priority: P4

Blocked until:

- Non-root paper user exists.
- Restricted control wrapper exists.
- Paper safety tests pass.

