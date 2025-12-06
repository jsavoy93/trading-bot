# Signal Threshold Tuning Guide

## Overview
Fine-tune your trading bot's signal sensitivity and quality requirements using command-line flags. Perfect for adjusting strategy aggressiveness based on market conditions.

## Quick Start: Signal Profiles

The easiest way to control signal thresholds is with `--signal-profile`:

### Conservative Profile (Fewer, Higher Quality Trades)
```bash
python main.py -c --signal-profile conservative
```
**Settings**: min_buy=4.0, min_sell=-4.0, strong=5.5  
**Effect**: Only trades when multiple factors strongly align

### Balanced Profile (Default)
```bash
python main.py -c --signal-profile balanced
# or just
python main.py -c
```
**Settings**: min_buy=3.0, min_sell=-3.0, strong=4.5  
**Effect**: Standard thresholds, good balance of quality vs quantity

### Aggressive Profile (More Trading Opportunities)
```bash
python main.py -c --signal-profile aggressive
```
**Settings**: min_buy=2.5, min_sell=-2.5, strong=4.0  
**Effect**: Accepts weaker signals, more trades but lower average quality

## Advanced: Custom Thresholds

### Override Profile Settings
You can start with a profile and override specific values:
```bash
# Conservative base, but custom buy threshold
python main.py -c --signal-profile conservative --min-buy-score 3.5

# Aggressive base, but require strong signals only
python main.py -c --signal-profile aggressive --min-strength strong
```

### Manual Control (No Profile)
Set all thresholds individually:
### Manual Control (No Profile)
Set all thresholds individually:
```bash
python main.py -c --min-buy-score 3.5 --min-sell-score -3.5 --strong-threshold 5.0
```

## All Available Flags

### Signal Profile (Easiest)
| Flag | Options | Effect |
|------|---------|--------|
| `--signal-profile` | conservative, balanced, aggressive | Sets all thresholds at once |

### Score Thresholds (Advanced)
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--min-buy-score N` | Minimum score for BUY signal | 3.0 | `--min-buy-score 3.5` |
| `--min-sell-score N` | Minimum score for SELL signal | -3.0 | `--min-sell-score -3.5` |
| `--strong-threshold N` | Score for STRONG classification | 4.5 | `--strong-threshold 5.0` |

### Strength Filter
| Flag | Description | Default | Options |
|------|-------------|---------|---------|
| `--min-strength LEVEL` | Minimum signal strength to trade | medium | weak, medium, strong |

## Profile Comparison

| Profile | Min Buy | Min Sell | Strong | Use When |
|---------|---------|----------|--------|----------|
| **Conservative** | 4.0 | -4.0 | 5.5 | High volatility, uncertain markets |
| **Balanced** | 3.0 | -3.0 | 4.5 | Normal conditions (default) |
| **Aggressive** | 2.5 | -2.5 | 4.0 | Low volatility, trending markets |

## How Scoring Works

The bot uses a **voting system** where each indicator contributes points:

### Positive (Bullish) Contributions
- **Trend UP** (price > 200-SMA band): +2.0
- **Local trend bullish** (fast SMA > slow SMA): +1.0
- **RSI strongly oversold** (<30): +2.0
- **RSI mildly oversold** (30-40): +1.0
- **MACD bullish** (MACD > signal): +1.0
- **MACD histogram positive**: +0.5
- **Near volume shelf support**: +0.5

### Negative (Bearish) Contributions
- **Trend DOWN** (price < 200-SMA band): -2.0
- **Local trend bearish** (fast SMA < slow SMA): -1.0
- **RSI strongly overbought** (>75): -2.0
- **RSI mildly overbought** (60-75): -1.0
- **MACD bearish** (MACD < signal): -1.0
- **MACD histogram negative**: -0.5
- **Near volume shelf resistance**: -0.5

### Example Score Calculation
```
AAPL Analysis:
+ Trend UP: +2.0
+ Local trend bullish: +1.0
+ RSI neutral (45): +0.0
+ MACD bullish: +1.0
+ MACD histogram positive: +0.5
+ Near support shelf: +0.5
= Total Score: 5.0 → STRONG BUY
```

## Strategy Tuning Scenarios

### 1. Bear Market / High Volatility
**Goal**: Only trade highest conviction signals
```bash
python main.py -c --signal-profile conservative --min-strength strong
```
**Effect**: Ultra-selective, only STRONG signals with score ≥4.0

### 2. Bull Market / Low Volatility
**Goal**: Capture more opportunities
```bash
python main.py -c --signal-profile aggressive
```
**Effect**: More trades with score ≥2.5 threshold

### 3. Testing New Strategy
**Goal**: See all signals including weaker ones
```bash
python main.py -c --signal-profile aggressive --min-strength weak
```
**Effect**: Maximum opportunities, good for backtesting

### 4. Risk-Off Mode
**Goal**: Only perfect setups
```bash
python main.py -c --signal-profile conservative --min-strength strong
```
**Effect**: STRONG signals only with score ≥4.0, very selective

## Impact on Trading

### Trade Frequency
- **Lower thresholds** = More trades (more opportunities but lower average quality)
- **Higher thresholds** = Fewer trades (higher quality but may miss moves)

### Signal Strength Filter
- **--min-strength weak**: Trade all signals (most opportunities)
- **--min-strength medium**: Trade MEDIUM + STRONG only (balanced)
- **--min-strength strong**: Trade STRONG only (most selective)

### Position Sizing Integration
The signal strength can be used for:
1. **Position sizing**: Larger positions for STRONG signals
2. **Risk adjustment**: Tighter stops for MEDIUM, wider for STRONG
3. **Filtering**: Skip trades below minimum strength threshold

## Console Output Examples

### With Conservative Profile
```
📊 Signal Profile: CONSERVATIVE

📊 Signal Thresholds:
   BUY: score ≥ 4.0 | SELL: score ≤ -4.0
   STRONG: |score| ≥ 5.5 | Min strength: MEDIUM

✅ AAPL: BUY (STRONG) at $175.45
   🎯 Score: 5.8 | Signal: BUY (STRONG)
   🗳️  Voting Breakdown:
      • Trend: UP +2.0
      • Local trend bullish +1.0
      • RSI mildly oversold +1.0
      • MACD bullish +1.5
      • MACD histogram positive +0.5
      • Near volume shelf (support) +0.5
   ✅ Trade #1 executed

⏭️ MSFT: ⊗ No signal (score=3.2, need ≥4.0 or ≤-4.0)
   🗳️  Score breakdown:
      • Trend: UP +2.0
      • Local trend bullish +1.0
      • RSI neutral +0.0
      • MACD bearish -0.5
```

### With Aggressive Profile
```
📊 Signal Profile: AGGRESSIVE

📊 Signal Thresholds:
   BUY: score ≥ 2.5 | SELL: score ≤ -2.5
   STRONG: |score| ≥ 4.0 | Min strength: MEDIUM

✅ TSLA: BUY (MEDIUM) at $245.67
   🎯 Score: 2.8 | Signal: BUY (MEDIUM)
   🗳️  Voting Breakdown:
      • Trend: UP +2.0
      • Local trend bullish +1.0
      • RSI neutral +0.0
      • MACD bearish -0.5
   ✅ Trade #1 executed
```

## Pro Tips

1. **Start Conservative**: Begin with `--min-strength strong` and lower if needed
2. **Market Adaptation**: Tighten thresholds in volatile markets
3. **Backtest First**: Test different thresholds with historical data
4. **Monitor Win Rate**: Higher thresholds should increase win rate
5. **Track Missed Opportunities**: Lower thresholds if too many good setups are filtered

## Combining with Other Flags

Full pro mode with conservative profile:
```bash
python main.py -c -d 300 \
  --signal-profile conservative \
  --min-strength strong \
  --max-trades 3 \
  --max-symbols 50
```

Aggressive trading with AI assistance:
```bash
python main.py -c --signal-profile aggressive --ai-full
```

Conservative with profile override:
```bash
python main.py -c --signal-profile conservative --min-buy-score 3.5
```
