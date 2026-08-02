# Trading Bot Dashboards

This document describes all dashboard interfaces available for the trading bot.

---

## 1. Main Web Dashboard

**URL:** `http://localhost:8080/` (when running)

**Purpose:** Primary control interface — positions, orders, opportunities, analytics, and now filter analysis.

**Sections:**

| Section | What it shows |
|---|---|
| 🔍 Symbol Lookup | Search any symbol, view stored analysis |
| 💰 Account | Balance, buying power, margin status |
| ⚠️ Trading Status | Active sessions, loop state |
| ⚙️ Trading Parameters | Live-editable bot settings |
| 🟢 Recent Sessions | Last 5 trading sessions with P&L |
| 📜 Session Logs | Live log viewer filtered by session |
| 📊 Positions | Open positions with unrealized P&L |
| 🎯 Top Opportunities | Top 30 symbols by score from DB |
| 📋 Recent Orders | Last 20 orders |
| 📈 Performance Analytics | Win rate, P&L, timing, RSI breakdown (tabs) |
| 🚫 Failed Analysis Breakdown | Why symbols failed (pie chart + table) |
| 🔍 **Filter Analysis** | **Why symbols didn't generate BUY signals — the new per-filter breakdown** |

---

## 2. Filter Analysis (Web Dashboard)

**Location:** Bottom of the main web dashboard, in the 🔍 Filter Analysis card.

**What it shows** (once the bot has run with the new code):

### Summary Bar
```
13,174 symbols analyzed  |  4,933 blocked  |  8,241 passed all filters
```

### Per-Filter Breakdown Table
| Filter | Blocked | Passed | %Block | Rate |
|---|---|---|---|---|
| MTF Conflict | 2,847 | 5,328 | 21.6% | ████████████░░░░░░░░░░░░░░ |
| Volume Downgrade | 1,103 | 7,072 | 8.4% | █████░░░░░░░░░░░░░░░░░░░░ |
| Market Regime | 482 | 7,693 | 3.7% | ██░░░░░░░░░░░░░░░░░░░░░░░░░ |
| ... | | | | |

### Most Filtered Symbols
Symbols with 2+ filters blocking them — the most "stuck" candidates.

| Symbol | Score | #Blocked | First Blocker | All Blockers |
|---|---|---|---|---|
| GRABW | 84 | 2 | mtf_conflict | mtf_conflict, volume_downgrade |
| BTBD | 75 | 2 | volume_confirmation | volume_confirmation, earnings_filter |

### Top Scored HOLD Signals
High-scoring symbols that still returned HOLD — the best candidates to watch.

| Symbol | Score | RSI | Strength | First Blocker | All Blockers |
|---|---|---|---|---|---|
| AETH | 71 | 5.1 | WEAK | regime_filter | regime_filter |
| BTBD | 70 | 30.0 | WEAK | mtf_conflict | mtf_conflict |

**Filters:**
- **Hours:** Past 6h / 12h / 24h / 48h / 7d
- **Symbol:** Optional — look up a specific symbol for a full filter breakdown

---

## 3. REST API Endpoints

All endpoints return JSON.

### Filter Dashboard API
```
GET /api/filter-dashboard?hours=24&symbol=GRABW
```

Response:
```json
{
  "hours": 24,
  "updated": "2026-06-24 13:46 UTC",
  "sections": {
    "blocked_by": {
      "total": 13174,
      "distribution": [
        {"label": "(none — no block)", "count": 8241, "pct": 62.6},
        {"label": "multi_timeframe_conflict", "count": 2847, "pct": 21.6}
      ]
    },
    "blocked_count": [
      {"blocked_count": 1, "count": 3102},
      {"blocked_count": 2, "count": 847}
    ],
    "per_filter": [
      {"key": "multi_timeframe_conflict", "label": "MTF Conflict",
       "blocked": 2847, "passed": 5328, "pct": 21.6}
    ],
    "most_filtered": [
      {"symbol": "GRABW", "score": 84, "signal": "HOLD",
       "blocked_by": "multi_timeframe_conflict", "blocked_count": 2,
       "filters": ["multi_timeframe_conflict", "volume_downgrade"]}
    ],
    "top_holds": [
      {"symbol": "AETH", "score": 71, "rsi": 5.1, "strength": "WEAK",
       "blocked_by": "regime_filter", "blocked_count": 1,
       "filters": ["regime_filter"]}
    ]
  }
}
```

### Other Useful Endpoints
```
GET /api/opportunities?limit=30       Top scoring symbols
GET /api/positions                     Open positions
GET /api/orders?limit=20              Recent orders
GET /api/score/{symbol}              Score breakdown for one symbol
GET /api/search/{symbol}             Full analysis for one symbol
GET /api/analytics/overview          P&L summary
GET /api/failed-analyses              Failed analysis breakdown
```

---

## Filter Keys Reference

`filter_results` stores the result of every downstream filter:

| Key | Filter Name | Blocks When |
|---|---|---|
| `multi_timeframe_conflict` | MTF Conflict | Daily and hourly signals disagree |
| `volume_downgrade` | Volume Downgrade | Volume ratio < 1.0x avg (signal weakened, not always blocked) |
| `regime_filter` | Market Regime | Trending market + RSI not strong enough |
| `earnings_filter` | Earnings Proximity | Earnings within `earnings_days_skip` days |
| `sp_relative_strength` | SPY Underperformance | Stock underperforming SPY |
| `volume_confirmation` | Volume Confirmation | `volume_ratio < 1.0` (Phase 7 check) |
| `trading_window` | Trading Window | Outside valid trading hours |
| `news_sentiment` | News Sentiment | Negative news sentiment |
| `short_interest` | Short Interest | High short squeeze risk |
| `ai_conflict` | AI Conflict | AI recommendation disagrees with signal |
| `liquidity_filter` | Low Liquidity | Avg daily volume below minimum |
| `sector_filter` | Sector Underperformance | Sector underperforming SPY by >2% |

Each entry:
```json
"multi_timeframe_conflict": {
  "passed": false,
  "blocked": true,
  "reason": "Daily BUY vs hourly SELL conflict",
  "daily_signal": "BUY",
  "hourly_signal": "SELL"
}
```

**`blocked_by`** — the *first* filter where `blocked: true` (quick summary field)
**`blocked_count`** — total number of filters that blocked the signal
