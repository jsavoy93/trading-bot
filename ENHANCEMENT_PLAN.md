# Trading Bot Enhancement Plan

## Phase 1: Smarter Signal Generation

### 1.1 — Add MACD crossover signal
- **What:** Add MACD as an independent signal alongside RSI/SMA
- **Why:** More signals = more opportunities, track which perform best
- **Implementation:** Calculate MACD (12/26/9), track signal performance independently

### 1.2 — Add Bollinger Band mean-reversion signal
- **What:** Buy when price touches lower band with RSI < 35, sell at middle band
- **Why:** Second uncorrelated strategy for diversification
- **Implementation:** Add Bollinger Bands (20,2), add separate buy/sell logic

### 1.3 — Build a signal scoring system
- **What:** Score each signal 0-100 instead of binary buy/sell
- **Why:** Weight and combine signals intelligently
- **Implementation:** Create weighted score combining RSI, SMA, MACD, Bollinger, volume

### 1.4 — Add earnings surprise filter
- **What:** Pull upcoming earnings dates via API
- **Why:** Avoid entering positions 3 days before earnings
- **Implementation:** Use Financial Modeling Prep or similar API for earnings dates

### 1.5 — Add relative strength vs. SPY
- **What:** Only buy stocks outperforming SPY over 20 days
- **Why:** Dramatically improves win rates
- **Implementation:** Compare stock return to SPY return over lookback period

### 1.6 — Implement VWAP-based entries
- **What:** Wait for price to pull back to VWAP before entering
- **Why:** Reduces average entry cost
- **Implementation:** Calculate VWAP, wait for price to retest before executing

---

## Phase 2: Intelligent Universe Selection

### 2.1 — Liquidity and spread filter
- **What:** Filter out stocks with avg daily volume < $1M or bid-ask spread > 0.3%
- **Why:** Reduces slippage on entries/exits
- **Implementation:** Add volume/spread checks before analyzing

### 2.2 — Volatility-based universe tiering
- **What:** Classify stocks into high/mid/low volatility buckets using ATR%
- **Why:** Apply different strategies to each tier
- **Implementation:** ATR% classification, apply mean-reversion for low-vol, momentum for high-vol

### 2.3 — Sector rotation scoring
- **What:** Track sector ETF performance (XLK, XLF, XLE, etc.) over 5/20/60 days
- **Why:** Overweight sectors with positive momentum
- **Implementation:** Score sectors, prioritize stocks in trending sectors

### 2.4 — Catalyst scanner
- **What:** Flag stocks with unusual volume (>2x avg), 52-week highs, or gap-ups
- **Why:** Prioritize high-catalyst stocks instead of random scanning
- **Implementation:** Scan for catalysts, boost signal scores for high-catalyst stocks

---

## Phase 3: Portfolio-Level Risk Management

### 3.1 — Sector concentration limits
- **What:** Cap exposure to any single sector at 25% of portfolio
- **Why:** Prevent accidentally loading up on all tech stocks
- **Implementation:** Track sector allocations, block new positions if sector overexposed

### 3.2 — Correlation-aware position sizing
- **What:** Check correlation with existing positions before entering
- **Why:** Reduce correlation risk
- **Implementation:** Calculate correlation, reduce size or skip if >0.7

### 3.3 — Beta-adjusted exposure
- **What:** Track portfolio's overall beta to SPY
- **Why:** Prevent blowups in market selloffs
- **Implementation:** If beta > 1.5, stop adding long positions

### 3.4 — Dynamic stop-loss using ATR
- **What:** Replace fixed 8% stop with 2x ATR
- **Why:** Volatile stocks get wider stops, tight stocks get tighter
- **Implementation:** Calculate ATR, set stop at current_price - (2 * ATR)

---

## Phase 4: Backtesting & Validation Infrastructure

### 4.1 — Historical data pipeline
- **What:** Set up storage for daily/hourly OHLCV data going back 3+ years
- **Why:** Can't validate without clean historical data
- **Implementation:** Store in database, use for backtesting

### 4.2 — Backtesting engine
- **What:** Run signals against historical data with slippage/commissions
- **Why:** Track Sharpe ratio, max drawdown, win rate, profit factor
- **Implementation:** Already have basic backtest.py - enhance it

### 4.3 — Walk-forward optimization
- **What:** Train on 12 months, test on next 3, roll forward
- **Why:** Only deploy signals that perform consistently out-of-sample
- **Implementation:** Implement rolling window backtesting

---

## Phase 5: Market Regime Detection

### 5.1 — Trend vs. range classifier
- **What:** Use ADX on SPY to detect trend vs range
- **Why:** Different strategies for different regimes
- **Implementation:** ADX > 25 = momentum, ADX < 20 = mean-reversion

### 5.2 — Volatility regime detection
- **What:** Track VIX or realized volatility of SPY
- **Why:** Adjust position sizes and stops based on vol regime
- **Implementation:** High-vol = smaller positions, low-vol = larger

### 5.3 — Sector momentum regime
- **What:** Detect when sector leadership is changing vs broad market moves
- **Why:** Adjust sector weights accordingly
- **Implementation:** Track sector relative performance

---

## Phase 6: Execution Optimization

### 6.1 — Limit order entries
- **What:** Replace market orders with limit orders at small discount
- **Why:** Reduce execution costs
- **Implementation:** Use limit orders, track fill rates

### 6.2 — Time-of-day optimization
- **What:** Analyze history by hour, avoid first/last 15 minutes
- **Why:** Reduce slippage during volatile periods
- **Implementation:** Use analytics to find best hours

### 6.3 — Partial position scaling
- **What:** Scale in over 2-3 entries as signal strengthens
- **Why:** Better entry, same for exits
- **Implementation:** Partial entries/exits with trailing stops

---

## Phase 7: Sentiment & Alternative Data

### 7.1 — News sentiment API integration
- **What:** Use news sentiment API (Benzinga, Alpha Vantage)
- **Why:** Avoid negative sentiment, amplify positive
- **Implementation:** API integration, sentiment scoring

### 7.2 — Short interest data
- **What:** Pull short interest / days-to-cover data
- **Why:** High short interest + bullish signal = short squeeze candidate
- **Implementation:** API for short interest, boost scores

### 7.3 — Insider trading filings
- **What:** Monitor SEC Form 4 filings
- **Why:** Insider buying clusters are strong predictive signals
- **Implementation:** API for insider filings, track clusters

---

## Current Implementation Status

### Already Implemented (Priority Items)
- ✅ Multi-timeframe analysis (daily + hourly)
- ✅ Volume confirmation
- ✅ Rolling ticker list (12,000+ symbols)
- ✅ Stop-loss order placement
- ✅ ATR position sizing
- ✅ Max drawdown protection
- ✅ Daily loss limit
- ✅ Telegram notifications (trade alerts + 30-min summaries)
- ✅ Performance dashboard analytics
- ✅ Backtesting framework

### Known Issues
- No AI ticker selection (uses rolling list)
- Market in neutral zone - few signals triggering
- Win rate shows 67.8% but trades not filling due to strict RSI thresholds
