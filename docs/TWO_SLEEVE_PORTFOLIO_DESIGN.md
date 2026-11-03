# Two-Sleeve Portfolio Architecture — Design Document

> **Status:** PROPOSED DESIGN — no production code. Nothing in this document is
> deployed, merged into runtime behavior, or authorized for implementation
> without Josh's explicit approval of the open decisions in §12.
>
> **Owner directive:** 2026-10-29 00:23 UTC — PAPER ONLY. Do not merge/deploy.
> Do not touch the dirty STRAT-020 checkout. Do not relax BUY thresholds.
>
> **Base revision:** `f8e2144` (origin/main, includes merged PR #111 strategy
> docs and PR #112 EXEC-004 ledger fidelity).
>
> **Prior work this design builds on (search-first — no duplication):**
> - STRAT-017 final pre-registration + results (frozen; annual June formation,
>   GP quintiles, equal weight) — `reports/2026-10-07_131446_STRAT-017_FINAL_PRE_REGISTRATION.md`
> - STRAT-009 multi-horizon sleeve feasibility architecture audit (the schema
>   gap list in §6 comes from there) — `reports/2026-10-01_222532_STRAT-009.md`
> - STRAT-019 closure (composite REJECT stands; STRAT-017 standalone PASS,
>   NOT deployed)
> - STRAT-020 Phases 1–4 corrective audit (data limitations in §11)
> - EXEC-004 ledger lifecycle (SUBMITTED rows + `broker_order_id` + fill
>   poller reconciliation, merged in PR #112)

---

## 1. Purpose and scope

Move from a single-strategy SmartBot to a **two-sleeve portfolio manager**:

- **Sleeve L — Long-term factor sleeve:** a systematic, low-turnover,
  fundamentals-driven allocation based on the frozen STRAT-017 gross
  profitability methodology.
- **Sleeve S — Short-term technical sleeve:** the redesigned SmartBot (current
  hybrid daily/hourly technical strategy, per `docs/SMARTBOT_STRATEGY.md`),
  owning the existing swing-trade entry/exit machinery.

This document is a DESIGN ONLY: architecture, data flow, schema, risk, and
evaluation plan. **No production code is written or changed by this document.**

Out of scope: any change to SmartBot BUY thresholds, signal logic, sizing
formulas, or exit rules; live trading; broker credentials; the deployed
`smartbot-runner` service; the STRAT-020 research checkout.

## 2. Design principles (inherited from house rules)

1. **Paper only** until Josh explicitly authorizes otherwise.
2. **Risk checks may only become more restrictive**, never less. A sleeve may
   not weaken, bypass, or reorder-away any existing check.
3. **Broker-authoritative ledger** (EXEC-004): every submitted order gets a
   SUBMITTED ledger row carrying `broker_order_id`; fills reconcile via the
   poller.
4. **Frozen factor methodology:** STRAT-017's §15/16 pre-registration is frozen.
   The sleeve implements it; it does not re-research it.
5. **One broker account, two logical portfolios.** Sleeves are a bookkeeping
   layer over a single paper account; the broker knows nothing about sleeves.
6. **Fail closed** everywhere a sleeve decision depends on a read that errors.

## 3. Target architecture

```
                       ┌──────────────────────────────┐
                       │   Portfolio Manager (new)    │
                       │  - capital split allocator   │
                       │  - portfolio risk governor   │
                       │  - sleeve performance views  │
                       └──────────┬───────────────────┘
              ┌───────────────────┴────────────────────┐
              ▼                                        ▼
 ┌────────────────────────┐              ┌────────────────────────────┐
 │ Sleeve L: Factor       │              │ Sleeve S: Technical        │
 │ - STRAT-017 frozen     │              │ - redesigned SmartBot      │
 │   selection engine     │              │   (current strategy,       │
 │ - annual June          │              │    unchanged signals)      │
 │   formation +          │              │ - existing risk checks,    │
 │   equal-weight Q5      │              │   tranches, exits          │
 └───────────┬────────────┘              └─────────────┬──────────────┘
             │      both sleeves share (unchanged):     │
             ▼                                         ▼
     SmartTradingBot order path: risk gate → submit → ledger row
     (SUBMITTED + broker_order_id) → fill poller reconciliation
```

The Portfolio Manager does **not** place orders. Each sleeve emits
**allocation intents** (symbol, side, target quantity, sleeve id, reason).
The shared execution path (SmartTradingBot's existing, EXEC-004-hardened
order path) validates intents against the portfolio-wide risk governor,
then submits or rejects. This keeps a single, auditable order path with a
single broker client and one paper-only guard.

## 4. Sleeve L — Long-term factor sleeve (STRAT-017, frozen)

Everything in this section is *implementation of the frozen methodology*,
not new research. Where an implementation choice was not fixed by the
pre-registration, it is listed as an open decision (§12).

- **Factor:** gross profitability = `GROSS_PROFIT / TOTAL_ASSETS`, fiscal
  year Y−1 (Compustat PIT, as used in STRAT-017).
- **Universe:** PIT-eligible XNYS companies with valid GP (the frozen
  STRAT-017 eligibility). Frozen cohort files
  (`experiments/strat_017/_cohort_*.csv.gz`, 11 files) are the authoritative
  selections; **membership reconstruction is READY per STRAT-020 Phase 1**.
- **Signal / selection:** NYSE GP quintile breakpoints computed on the
  eligible universe each formation; **hold Q5 (top GP quintile)**, equal
  weight. No monthly re-ranking, no quarterly refresh.
- **Rebalance cadence:** **annual** — formation at late June, holding year
  July→June (frozen §16.2). No intra-year turnover except forced exits
  (§4.3).
- **Position sizing:** equal weight across Q5 members, scaled to Sleeve L's
  capital allocation. Liquidity floor: exclude members below the frozen
  liquidity percentile cut actually used in STRAT-017 (documented there);
  the known dollar-volume caveat (adjusted-close × raw-volume understates
  10–30%) means the floor is a **relative** rank, not an absolute ADV —
  flagged as a known limitation, methodology unchanged.
- **Holding period:** July through next June formation. Forced exits only:
  delisting/acquisition, or a portfolio-governor breach (§7). Fundamental
  deterioration mid-year does NOT trigger exits (no quarterly refresh —
  frozen).
- **Expected characteristics (from frozen results):** standalone mean IC
  +0.0562 (95% CI [+0.012, +0.100]); Q5−Q1 mean monthly +0.39%; 7/10
  positive years; top-decile 9-yr geometric +265.75%. Coverage 30–40% of
  PIT-eligible is a binding constraint on breadth, not a defect.
- **What Sleeve L must NOT do:** synthesize historical market cap; re-rank
  monthly; change weighting after observing results; mix in STRAT-013/015
  factors (composite is REJECT / DO NOT DEPLOY per STRAT-019 closure).

### 4.1 Data flow (formation-time, once per year)

1. Load frozen PIT fundamentals for the new formation year (same pipeline
   as STRAT-017; no new datasets without owner authorization).
2. Compute GP per eligible company; NYSE quintile breakpoints; select Q5.
3. Diff Q5(target) vs current Sleeve L holdings → allocation intents
   (BUY new members to equal weight; SELL exits to 0).
4. Intents pass through the portfolio risk governor, then the existing
   execution path, with `sleeve='L'` tagging (§6).

### 4.2 Operational cadence

- Between formations: weekly (or per-loop, cheap) reconciliation of Sleeve L
  ledger rows vs broker positions (reuse the EXEC-004 reconciliation
  pattern, read-only) and drift re-weighting **only if** a position's weight
  deviates beyond a tolerance band (open decision D5; default: no
  intra-year re-weighting, matching the frozen no-refresh rule).

### 4.3 Known limitations carried forward

- 41–90% coverage gap (missing tickers are predominantly
  delisted/acquired/renamed — a form of survivorship; owner qualification
  2026-10-12: selection bias NOT ruled out).
- Absolute dollar volume unsuitable for transaction-cost estimates.
- Fundamentals data currently ends with the frozen research window; live
  formation requires a **data refresh decision** (open decision D2).

## 5. Sleeve S — Short-term technical sleeve (redesigned SmartBot)

- **Strategy:** exactly the current documented strategy
  (`docs/SMARTBOT_STRATEGY.md`, AUTHORITATIVE as of 2026-10-22). No BUY
  threshold relaxation, no signal changes.
- **Redesign = isolation, not modification:** SmartBot's analysis/entry/exit
  logic stays; what changes is that its orders are expressed as Sleeve S
  allocation intents, sized against **Sleeve S's capital allocation** rather
  than the whole account, and its risk checks operate on the shared
  governor's portfolio-wide state.
- Existing machinery retained as-is: tranche risk gate (EXEC-004),
  stop-loss / portfolio-management ledger rows, fill confirmation,
  paper-only guard.
- Sleeve S keeps its fast loop (10 s persisted); Sleeve L's governor
  evaluation rides the same loop but only acts at formation/drift events.

## 6. Sleeve tagging — positions, orders, ledger

STRAT-009's audit found the blocker: positions/orders/trades have **no
sleeve ownership anywhere** (0 YES / 1 PARTIAL / 4 NO). Minimal schema
change:

1. **`trades` table:** add nullable column `sleeve TEXT` (`'L'` / `'S'`,
   NULL = legacy pre-sleeve rows). Every ledger write path (execute_trade,
   tranche submissions, stop-loss, portfolio orders, reconciliation
   backfills) sets it. `log_trade` schema validation updated accordingly.
2. **`order_events`:** inherit sleeve via the parent trade row (no new
   column needed; joins through `trade_id`).
3. **In-memory sleeve positions:** a `SleevePosition` bookkeeping map
   (`symbol → per-sleeve qty/entry`) maintained from ledger fills, since the
   broker only reports one aggregate position per symbol. This map is
   **derived, not authoritative** — the broker remains authoritative for
   real quantities; the map resolves sleeve-level intent questions
   ("Sleeve S wants to exit 10 AAPL while Sleeve L holds 20").
4. **Conflicting-signal arbitration** (STRAT-009 requirement): netting rule
   per symbol — sleeve intents are netted at the governor (e.g. L BUY 20 +
   S SELL 10 → net BUY 10 tagged with the dominant intent and both sleeve
   records), OR simpler: same-symbol simultaneous opposite intents are
   rejected and retried next loop (open decision D6; default: **reject both
   and log** — simplest, fail-closed).
5. **Legacy rows:** left NULL, never back-filled by guessing; a one-time
   classification script may *propose* labels from timestamps/notes for
   Josh's review (open decision D7).

## 7. Shared portfolio-wide risk limits (the governor)

Applies across sleeves before any order (more restrictive than today, never
less):

| Limit | Rule (defaults; all tunable) |
|---|---|
| Per-symbol total exposure | same-symbol value summed across sleeves ≤ 10% of total portfolio (unchanged cap, now cross-sleeve) |
| Per-sleeve cap | sleeve market value ≤ its allocation ± tolerance band (open decision D3; default ±10% drift band) |
| Total portfolio exposure | ≤ 100% of portfolio value (no leverage; cash < 0 fails closed) |
| Correlated exposure | highly-correlated positions across sleeves treated as ONE risk unit (extends existing correlation check to cross-sleeve aggregation) |
| Sector concentration | existing per-sector caps, computed cross-sleeve |
| Beta | existing portfolio beta cap, computed cross-sleeve |
| Duplicate open BUY | cross-sleeve: an open BUY from either sleeve blocks a new BUY for the same symbol (excluding the plan's own tranche order ids, per EXEC-004) |
| Sleeve kill-switch | if a sleeve's drawdown from its own high-water mark exceeds a bound (open decision D4), the governor stops accepting that sleeve's intents and alerts Josh; it never auto-liquidates |

Fail-closed: any governor input read that errors blocks the intent.
All governor decisions logged with reason codes into the ledger notes /
order event log.

## 8. Capital split

- Total paper equity is read from the broker (single account).
- The split allocator partitions it: Sleeve L target %, Sleeve S target %,
  cash buffer % (open decision D1; **default proposal: L 60% / S 30% /
  10% cash buffer**, reflecting L's higher evidence quality and low turnover
  vs S's active risk).
- Sleeve capital drifts with P&L; the split is re-marked to target only at
  Sleeve L's annual formation (avoid forced rebalancing churn), unless a
  governor breach forces earlier action.
- **No forced transfers ever place orders by themselves.**

## 9. Per-sleeve performance reporting

Extend the existing dashboard/queries with a sleeve dimension:

- Per sleeve: realized/unrealized P&L (from ledger fills, broker prices),
  exposure, position count, turnover, win rate (FILLED rows with pnl ≠ NULL
  only — EXEC-004 caveat: backfilled rows keep pnl NULL and never count),
  monthly return series, max drawdown, high-water mark (feeds the kill-switch).
- Combined: portfolio view + attribution split (which sleeve drove P&L).
- Sleeve L additionally reports its **formation-to-formation** holding-year
  return, labeled PARTIAL where incomplete (frozen convention).
- Legacy NULL-sleeve rows reported as a third bucket "legacy (pre-sleeve)".

## 10. Implementation phases (each gated on Josh's approval)

| Phase | Content | Risk |
|---|---|---|
| P1 | Schema: `trades.sleeve` column + migration; sleeve tagging on all write paths | Low, additive |
| P2 | Governor: cross-sleeve limit checks in front of the existing order path | Medium — touches order path; fail-closed; extensive tests |
| P3 | Sleeve L selection engine (frozen methodology) + annual formation runner producing intents only | Medium — new module, no order path change |
| P4 | Sleeve S isolation: capital-scope sizing + intent emission | Medium |
| P5 | Per-sleeve reporting views | Low, read-only |
| P6 | Paper evaluation window (§11) before any consideration of shared-account operation | — |

Order: P1 → P2 → P5 (measure the current bot as "Sleeve S" first), then
P3/P4 once the tagging is proven in paper. Each phase: branch, tests, PR,
independent review, Josh approval. **Nothing auto-deploys.**

## 11. Priority 4 — historical evaluation plan (before Sleeve L goes live)

**Question to answer:** would the two-sleeve portfolio have beaten (a) SPY,
(b) Sleeve-S-alone, and (c) Sleeve-L-alone on a historical window — net of
an explicit cost model, at paper-scale position sizes?

**Data (existing only, per STRAT-020 Option D constraints):**
- Frozen STRAT-017 cohort selections (READY) + frozen adjusted Alpaca daily
  prices 2016–2026 (endpoint 2026-09-29).
- SmartBot Sleeve-S strategy replay: requires historical hourly data for the
  hourly-confirmation component — **known gap**; fallback is daily-only
  approximation, which must be labeled as an approximation, or the hourly
  sleeve is evaluated only on the window where hourly data exists (open
  decision D8).
- Cost model: flat per-trade bps assumption + the frozen relative-liquidity
  ranks as a size-feasibility filter (absolute dollar-volume is NOT valid
  for cost estimation — documented limitation).

**Pass/fail bar (pre-registered before running, frozen before results):**
1. **IC is necessary, not sufficient.** Sleeve L's evaluation bar is
   *portfolio-level* net return/risk (net Sharpe vs SPY, net Q5−Q1 spread),
   NOT the factor IC. IC +0.0562 ≠ profitability.
2. Combined two-sleeve portfolio: net monthly return ≥ max(Sleeve S alone,
   SPY) minus a pre-registered tolerance (proposed 25 bps/mo), AND net
   max-drawdown ≤ Sleeve S alone (the point of adding L is diversification).
3. Cross-sleeve correlation of monthly returns < 0.5 on the overlap window
   (else the second sleeve adds no diversification).
4. Every metric reported with bootstrap 95% CIs; a PASS requires the CI to
   exclude "materially worse than SPY", not merely a positive point estimate.
5. Survivorship caveat stated in every output (owner qualification
   2026-10-12: selection bias NOT ruled out); the 2016–2026 window contains
   no regime like a live 2027+ environment.

**Explicit non-goals:** no IC re-computation as a success criterion; no
parameter sweeps; no re-optimization of the frozen factor; no new data
acquisition (any data purchase = separate owner decision, STRAT-020 Option B).

## 12. Open decisions for Josh

| # | Decision | Default proposal |
|---|---|---|
| D1 | Capital split L / S / cash | 60% / 30% / 10%, re-marked annually |
| D2 | Sleeve L live formation data source (fundamentals refresh after the frozen window ends) | Manual, owner-triggered annual refresh; no auto-acquisition |
| D3 | Sleeve drift tolerance band before governor action | ±10% of allocation |
| D4 | Sleeve kill-switch drawdown bound | −15% from sleeve high-water mark, alert-only (no auto-liquidation) |
| D5 | Intra-year weight re-balancing for Sleeve L | None (matches frozen no-refresh rule); revisit only with new pre-registration |
| D6 | Same-symbol opposite-intent arbitration | Reject both, log, retry next loop (simplest, fail-closed) |
| D7 | Legacy NULL-sleeve row classification | One-time proposal script, human-reviewed, optional |
| D8 | Sleeve S historical hourly-data gap | Evaluate S only on the hourly-data window; label clearly; no interpolation |
| D9 | Phase order confirmation (P1→P2→P5 before any Sleeve L code) | As proposed in §10 |

## 13. What this design deliberately does NOT do

- No change to any BUY threshold, signal, sizing formula, or exit rule.
- No STRAT-019 composite resurrection; no STRAT-013/015 addition.
- No live trading, no credential changes, no deployment, no restart.
- No new data acquisition.
- No modification of frozen STRAT-017 artifacts or their SHA-verified state.
