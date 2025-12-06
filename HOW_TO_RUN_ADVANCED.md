# How to Run with Advanced Analysis

## Quick Start - 3 Ways to Enable

### ✅ Option 1: Environment Variable (Easiest)

Just set the flag before running:

```bash
# Linux/Mac
export USE_ADVANCED_SIGNALS=true
python main.py

# Or run inline
USE_ADVANCED_SIGNALS=true python main.py
```

```powershell
# Windows PowerShell
$env:USE_ADVANCED_SIGNALS="true"
python main.py
```

### ✅ Option 2: Edit Configuration File

Create/edit `.env` file in the project root:

```bash
# .env file
USE_ADVANCED_SIGNALS=true
USE_ATR_EXITS=true          # Optional: Enable ATR-based exits
USE_ATR_SIZING=true         # Optional: Enable ATR-based position sizing
```

Then run normally:
```bash
python main.py
```

### ✅ Option 3: Code Modification

Edit `src/core/smart_bot.py` around line 101:

```python
# Change this line:
self.use_advanced_signals = os.getenv("USE_ADVANCED_SIGNALS", "false").lower() == "true"

# To this:
self.use_advanced_signals = True  # Always use advanced signals
```

---

## What Changes When Enabled?

### Basic Mode (Default)
```
Analyzing AAPL...
  SMA Fast: 150.25
  SMA Slow: 148.10
  RSI: 35.2
  
  → Signal: BUY (MEDIUM)
```

### Advanced Mode (Enabled)
```
Analyzing AAPL...
  SMA Fast: 150.25
  SMA Slow: 148.10
  SMA Trend: 145.80
  RSI: 35.2
  MACD: 0.7131
  MACD Signal: 0.9129
  ATR: $2.50
  
AAPL: BUY signal (STRONG)
  • Trend regime: UP
  • Local trend: bullish (fast SMA > slow SMA)
  • MACD bullish (MACD > signal)
  • RSI=35.2 below buy threshold 40
  • Strong BUY: deeply oversold RSI + MACD histogram > 0
```

---

## Feature Flags Summary

| Flag | Default | What It Does |
|------|---------|--------------|
| `USE_ADVANCED_SIGNALS` | false | Uses MACD, ATR, 200-SMA for better signals |
| `USE_ATR_EXITS` | false | Volatility-adjusted stop loss/take profit |
| `USE_ATR_SIZING` | false | Position size based on volatility |

---

## Verify It's Working

Run the bot and look for these log messages:

### Basic Mode
```
📊 Analyzing AAPL...
  Traditional signal: BUY (MEDIUM)
```

### Advanced Mode  
```
🎯 Analyzing AAPL...
  Advanced signal: BUY (STRONG)
  • Trend regime: UP
  • MACD bullish
  • RSI=35.2 below buy threshold 40
```

---

## Test First

Before running with real trading, test the demo:

```bash
# See advanced analysis in action
python examples/pro_strategy_demo.py

# Should show all 5 examples including:
# - Basic vs Advanced comparison
# - ATR exits
# - Position sizing
```

---

## Troubleshooting

### "Not enough historical data"
**Problem**: Advanced mode needs 200+ bars of data  
**Solution**: Bot will automatically skip symbols with insufficient data

### "No signals generated"
**Problem**: Advanced mode is more selective  
**Solution**: This is normal - signals are higher quality but less frequent

### "Import error: trading.strategy"
**Problem**: Module path issue  
**Solution**: Run from project root: `python main.py`

---

## Recommended Settings

### For Testing
```bash
# Start conservative
USE_ADVANCED_SIGNALS=true
# Keep exits and sizing on basic mode initially
```

### For Production
```bash
# Enable all features
USE_ADVANCED_SIGNALS=true
USE_ATR_EXITS=true
USE_ATR_SIZING=true
```

---

## What's Next?

1. ✅ Enable `USE_ADVANCED_SIGNALS=true`
2. ✅ Run the bot and monitor logs
3. ✅ Compare signal quality vs basic mode
4. ✅ Once confident, enable ATR exits
5. ✅ Finally enable ATR sizing

Read the full migration guide: `MIGRATION_GUIDE_PRO_STRATEGY.md`
