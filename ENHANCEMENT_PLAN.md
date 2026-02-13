# Trading Bot Enhancement Plan

**Prepared by:** Moose 🫎  
**Date:** Feb 13, 2026  
**Status:** Ready for Review

---

## Overview

Current state: Solid paper trading bot with good infrastructure. Strategy is basic, lacks proper risk management. Not ready for real money.

Goal: Transform into a more robust, risk-managed trading system suitable for eventual live trading.

---

## Priority 1: Risk Management (Critical)

### 1.1 Stop-Loss System
- **What:** Add configurable stop-loss percentage per trade
- **Why:** Currently no exit strategy for losing positions
- **Implementation:** 
  - Trailing stop: X% below entry
  - Hard stop: X% below entry (panic exit)
  - Time-based stop: Exit after N days if no profit

### 1.2 Position Sizing Improvements
- **What:** Volatility-adjusted position sizing
- **Why:** Same % allocation on volatile vs stable stocks = different risk
- **Implementation:**
  - Use ATR (Average True Range) to calculate position size
  - Smaller positions for volatile stocks, larger for stable

### 1.3 Max Drawdown Protection
- **What:** Pause trading if portfolio drops X% from peak
- **Why:** Prevent catastrophic losses in bad streaks
- **Implementation:**
  - Track peak portfolio value
  - Auto-pause if drawdown > 10%
  - Require manual resume

### 1.4 Daily/Weekly Loss Limits
- **What:** Stop trading for the day/week after losing X%
- **Why:** Prevent revenge trading after losses
- **Implementation:**
  - Track daily P&L
  - Auto-stop if daily loss > 5%

---

## Priority 2: Strategy Improvements

### 2.1 Multi-Timeframe Analysis
- **What:** Analyze on multiple timeframes (daily + hourly) before trading
- **Why:** Reduce false signals
- **Implementation:**
  - Require both daily AND hourly to agree on signal
  - Weight signals: daily 70%, hourly 30%

### 2.2 Trend Confirmation
- **What:** Only trade in direction of overall market trend
- **Why:** Counter-trend trades have lower win rate
- **Implementation:**
  - Add market trend indicator (S&P 500 SMA 200)
  - Only BUY in uptrend, only SELL in downtrend

### 2.3 Volume Confirmation
- **What:** Confirm signals with volume
- **Why:** Price movement without volume = unreliable
- **Implementation:**
  - Add volume SMA
  - Require volume > 20-day average for signal

### 2.4 Take-Profit Targets
- **What:** Auto-sell when profit reaches X%
- **Why:** Lock in gains instead of riding back to loss
- **Implementation:**
  - Configurable take-profit % (e.g., 5%, 10%, 15%)
  - Trailing take-profit: Lock in X%, let rest ride

---

## Priority 3: Data & Analytics

### 3.1 Backtesting Framework
- **What:** Test strategies on historical data
- **Why:** Validate before trading real money
- **Implementation:**
  - Use Alpaca historical data
  - Run simulation: 1 year backtest
  - Output: win rate, avg profit, max drawdown, Sharpe ratio

### 3.2 Performance Dashboard
- **What:** Enhanced analytics
- **Why:** Understand what's working
- **Implementation:**
  - Track by signal type (pure tech vs AI-enhanced)
  - Track by sector
  - Track by RSI range
  - Win rate by hour/day of week

### 3.3 Trade Logging Improvements
- **What:** More detailed trade records
- **Why:** Better analysis
- **Implementation:**
  - Log entry reasoning (which indicators triggered)
  - Log market conditions
  - Log AI confidence scores

---

## Priority 4: Infrastructure

### 4.1 Error Recovery
- **What:** Better error handling
- **Implementation:**
  - Retry logic with exponential backoff
  - Circuit breaker for failing APIs
  - Dead letter queue for failed trades

### 4.2 Configuration System
- **What:** Centralized config
- **Implementation:**
  - All parameters in one file
  - Environment-specific configs (dev/staging/prod)
  - Config validation on startup

### 4.3 Testing Suite
- **What:** Automated tests
- **Implementation:**
  - Unit tests for indicator calculations
  - Integration tests for API calls
  - Mock Alpaca responses for offline testing

---

## Priority 5: Future (Post-Profit)

### 5.1 Paper/Live Toggle
- **What:** Switch between paper and live trading
- **Implementation:**
  - Single config change to go live
  - Mandatory paper profits before live allowed

### 5.2 Multi-Strategy
- **What:** Run multiple strategies simultaneously
- **Implementation:**
  - Strategy A: RSI oversold
  - Strategy B: Breakout
  - Strategy C: Mean reversion

### 5.3 Portfolio Rebalancing
- **What:** Auto-rebalance portfolio
- **Implementation:**
  - Target sector allocations
  - Weekly/monthly rebalance

---

## Recommended Execution Order

```
Phase 1 (Week 1-2): Risk Management
  → Stop-loss, position sizing, drawdown protection

Phase 2 (Week 2-3): Strategy Improvements  
  → Multi-timeframe, trend confirmation, take-profit

Phase 3 (Week 3-4): Backtesting & Analytics
  → Backtest framework, enhanced dashboard

Phase 4 (Ongoing): Infrastructure
  → Testing, error recovery, config system
```

---

## Questions for Josh

1. Which priority resonates most?
2. Any specific feature you want first?
3. Target timeframe for live trading?
4. What's your risk tolerance? (aggressive vs conservative)

---

*Plan ready for approval. Moose standing by.* 🫎
