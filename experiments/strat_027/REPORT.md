# STRAT-027 — Plain-language report: does a systematic factor portfolio beat the benchmarks?

**Status: research only. Nothing was changed in the trading bot. The branch is not merged.**

---

## The question

Everything we had tested so far was about finding good individual BUY signals. Josh asked a
different question:

> *Can we hold a basket of stocks picked by a simple, measurable rule — and beat plain
> benchmarks after costs and risk — instead of hunting for individual signals?*

We tested four such rules ("factors"), each one on its own:

| Factor | The rule, in plain English |
|---|---|
| **MOM12_1** | Buy the stocks that went up most over the past year, ignoring the most recent month (the classic "12-1 month momentum" rule). |
| **LVOL** | Buy the least jumpy stocks — the ones with the lowest year-long daily volatility. |
| **QUALITY** | Buy the most profitable companies, measured as return on equity from the most recent annual report that was **public at the time**. |
| **VALUE** | Buy the cheapest stocks, measured as annual earnings divided by market value, again using only the most recent annual report that was **public at the time**. |

Each factor built the same portfolio: **30 stocks, equally weighted, capped at 8% per stock,
rebalanced on the first trading day of every month, with 10 bps of trading cost per side
on everything that was bought or sold.** We then compared each portfolio against:

1. **Buy-and-hold SPY** over exactly the same months.
2. **1,000 random portfolios** of 30 stocks, drawn from the same eligible stocks on the same
   days, with the same rebalancing and the same costs. This is the honest benchmark: *is
   this rule doing anything, or is picking 30 liquid stocks at random just as good?*

We split the time in two: **2021–2024 was the development window** (used to decide which
factor, if any, was worth taking forward), and **January 2025 → September 2026 was a
one-shot holdout** that was run once, after the plan was written down and frozen in
`PREREGISTRATION.md`.

---

## What we found

### In the development window (2021–2024), VALUE looked strong

| Factor | Return/yr | Volatility/yr | Sharpe | Worst drawdown | Turnover/yr | vs 1,000 random portfolios |
|---|---|---|---|---|---|---|
| **VALUE** | **23.8%** | 21.9% | **1.09** | −23.6% | 5.3x | **99.9th percentile** (z = 3.41) |
| MOM12_1 | 14.4% | 39.1% | 0.37 | −38.8% | 10.6x | 99.8th percentile (z = 3.00) |
| QUALITY | 13.6% | 18.7% | 0.73 | −22.8% | 4.0x | 84th percentile (z = 1.02) |
| LVOL | 5.7% | 10.7% | 0.53 | −14.7% | 4.3x | 26th percentile (z = −0.63) |
| *SPY* | *13.6%* | *15.6%* | *0.87* | *−23.3%* | — | — |
| *random portfolios* | *4.9% avg* | — | *0.22 avg* | — | *19x* | — |

VALUE was the only factor that passed all three of our pre-written tests (it beat SPY on
both return and risk-adjusted return; it was at least 2 standard deviations above the
random-portfolio average; and it beat 95% of the random portfolios). So VALUE was carried
into the holdout.

### In the holdout (January 2025 → September 2026, run once), the edge did not hold up

| Factor | Return/yr | Volatility/yr | Sharpe | Worst drawdown | vs 1,000 random portfolios |
|---|---|---|---|---|---|
| **VALUE** | **20.2%** | 12.2% | **1.65** | −8.8% | 73rd percentile (z = 0.58) |
| QUALITY | 14.2% | 13.7% | 1.03 | −14.0% | 43rd percentile |
| LVOL | 9.8% | 9.8% | 1.00 | −3.0% | 24th percentile |
| MOM12_1 | −0.7% | 45.9% | −0.01 | −27.4% | 27th percentile |
| *SPY* | *17.9%* | *12.2%* | *1.47* | *−6.2%* | — |
| *random portfolios* | *12.1% avg, 27.6% at the 95th percentile* | — | *0.72 avg* | — | — |

VALUE still beat SPY on both return (20.2% vs 17.9%) and Sharpe (1.65 vs 1.47). But it
**failed the holdout bar**: it was only at the 73rd percentile of the random portfolios
(we needed the 99th), and the paired t-statistic against random portfolios was 0.68, not
the 2.0 we required. In plain English: **on the holdout, a value screen was not reliably
better than picking 30 random liquid stocks.**

The reason "beats SPY" is not enough evidence shows up clearly in that table: in this
window, **most random portfolios beat SPY** (95th percentile: 27.6%/yr versus SPY's
17.9%/yr). Beating SPY in a strong two-year bull market is easy and proves very little.

### The other three factors

- **Momentum** was the most violent. In development it out-earned SPY but with 39%
  volatility and a −39% drawdown; in the holdout it *lost* money (−0.7%/yr) with 46%
  volatility while SPY made 17.9%. Its beta to SPY in the holdout was 2.36 — it was
  mostly a levered bet on the market, and the market rewarded that in 2021–2024 and
  punished it afterwards.
- **Low volatility** did exactly what the name promises: it cut risk (holdout max drawdown
  −3.0%, best of any factor, half of SPY's) but it never made more money than the market
  (9.8%/yr vs SPY 17.9%). It is a defensive factor, not an alpha factor.
- **Quality** was respectable but unremarkable: it earned less than SPY in both windows and
  was no better than random stock picking.

---

## Why we trust (and where we distrust) the result

**Checks that passed**
- *No look-ahead.* Every factor input comes from data **strictly before** the rebalance
  date. We proved this by shuffling all prices after a rebalance date and confirming the
  selection did not move (engine sanity check `test_sanity.py`).
- *Point-in-time fundamentals.* The quality and value factors use only annual reports
  (10-K) that were **already filed** on the rebalance date. We verified by hand that
  AAPL, AAL, NVDA and MSFT each use the correct fiscal year as of each rebalance date.
- *Not a stale-data artefact.* Restricting the value factor to companies whose latest
  annual report was less than 400 days old changed nothing (every eligible company
  already qualifies; 23.7% vs 23.8% a year).
- *The engine.* Turnover, cost accounting, the delisting rule and the position cap all
  have their own passing checks.

**Honest weaknesses**
- The development-window value result was **concentrated**: the 10 best of 150 names held
  contributed about half of the total gain (SMCI, AVGO, AEHR, AR, MLI, FHN, KLAC, ORLY,
  NRG, NVDA — deep-value names that re-rated hard). A result that depends on ~10 names is
  fragile, and the holdout duly failed to replicate it.
- The holdout is only **20 monthly periods** in one strong bull market — low power.
- The data still carries **survivorship bias** (it is reduced, not eliminated: the source
  dataset includes delisted names, but Alpaca gives no listing/delisting dates).
- Fundamentals are **annual only**, so quality and value change roughly once a year.
- Costs are a **flat 10 bps per side**, not a real spread/market-impact model.
- We ran four factors and looked at several windows, so some of the "wins" in development
  are luck. That is exactly why the holdout was run once and why the random-portfolio
  test — not SPY — is the deciding test.

---

## How this relates to what we already knew (STRAT-021)

This is **not a new factor family**. STRAT-021 (October 2026) already tested momentum,
low volatility and gross profitability as standalone quintile sleeves with **annual**
rebalancing and found all three FAIL against SPY.

STRAT-027 re-tests the same three families with a different construction (monthly
rebalancing, a concentrated 30-stock basket with position caps) and a much sharper test
(1,000 random portfolios and t-statistics, plus a real dev/holdout split), and adds
earnings-based **value** as a fourth family.

The two studies broadly agree: low volatility behaves almost identically (STRAT-021:
5.65%/yr, Sharpe 0.59, drawdown −17.7%; STRAT-027: 5.74%/yr, Sharpe 0.53, drawdown
−14.7%). The difference is that STRAT-027 can say *how much* of the result is luck,
because it has a random-portfolio null; STRAT-021 could only report a bootstrap interval.
**STRAT-027 is a re-test of known factor families, not a new discovery** — and the honest
summary is that nothing passed.

---

## Recommendation

**Do not build any of these factor portfolios into the bot.** On the pre-written test, in
the one-shot holdout, no factor earned a return that was distinguishable from picking
stocks at random.

What we *should* keep:

1. **The random-portfolio benchmark.** It is the only benchmark in this study that
   discriminated signal from luck, and it immediately exposed a "beat SPY" result that
   was not a real edge. Any future portfolio study should use it from the start.
2. **The point-in-time fundamentals pipeline** — it works, it is reproducible, and it is
   the only thing in this study that is broadly reusable.
3. **The data audit finding** — point-in-time fundamentals *do* exist locally
   (`research_data/sec_fundamentals/`), so future factor work does not have to be
   price-only.

What we should *not* do: re-run these factors with different windows, different universe
sizes, or different portfolio sizes until one of them passes. That is the exact trap this
study was designed to avoid, and the pre-written rules are what stop it.

---

## What was NOT touched

No changes to the main bot, C4, the RSI(2) shadow, BUY thresholds, settings, the database,
or any broker/order path. No orders were placed and no live credentials were used — the
price data came from the Alpaca **paper** data API through a local read-only research
dataset, and the fundamentals came from local SEC filings. The dirty checkout at
`/root/.openclaw/workspace/trading-bot` was used **read-only** (its data only); all work
happened in the new worktree `/root/.openclaw/worktrees/STRAT-027` on branch
`research/strat-027-portfolio`. Nothing was merged.
