# AI Configuration Guide

The trading bot now supports granular control over AI features, allowing you to enable/disable AI at different levels via command-line arguments or programmatically.

## Quick Start

### Pure Technical Analysis (No AI)
```bash
python main.py --no-ai -c -d 60
```

### Disable Only Individual Ticker AI (Fastest)
```bash
python main.py --no-ai-ticker-analysis -c -d 60
```

### Full AI Mode (Default)
```bash
python main.py -c -d 60
```

## Configuration Variables

The bot has three independent AI configuration flags:

### 1. `use_ai_for_ticker_analysis` (default: `True`)
Controls whether AI analyzes individual tickers for insights.

- **Enabled**: Each ticker gets AI-powered research including news analysis, sentiment, and enhanced trading signals
- **Disabled**: Trading decisions rely purely on technical indicators (RSI, SMA) for faster, more predictable analysis

### 2. `use_ai_for_ticker_selection` (default: `True`)
Controls whether AI intelligently selects which tickers to analyze based on portfolio needs.

- **Enabled**: AI recommends tickers based on diversification, sector balance, growth opportunities
- **Disabled**: Uses standard ticker list (S&P 500, etc.)

### 3. `use_ai_for_market_summary` (default: `True`)
Controls whether AI creates market sentiment summaries.

- **Enabled**: Shows overall market sentiment, key trends, and risk factors
- **Disabled**: Skips market-level AI analysis

## Usage Examples

### Pure Technical Analysis (No AI)
```python
from core.smart_bot import SmartTradingBot

bot = SmartTradingBot()

# Disable all AI features
bot.configure_ai_usage(
    ticker_analysis=False,
    ticker_selection=False,
    market_summary=False
)

bot.start_session()
bot.run_analysis(max_symbols=20, max_trades=2)
bot.end_session()
```

### Hybrid Approach (AI for Selection, Technical for Analysis)
```python
# Use AI to pick smart tickers, but analyze them with pure technicals
bot.configure_ai_usage(
    ticker_analysis=False,   # Fast technical-only analysis
    ticker_selection=True,   # Smart AI-powered ticker picks
    market_summary=False
)
```

### AI Everywhere Except Individual Ticker Analysis
```python
# Good for cost savings while keeping smart features
bot.configure_ai_usage(
    ticker_analysis=False,   # Save API calls per ticker
    ticker_selection=True,   # Portfolio-aware ticker selection
    market_summary=True      # Market context overview
)
```

## Benefits of Disabling Ticker-Level AI

1. **Faster Execution**: Technical analysis is instant vs AI API calls
2. **Cost Savings**: Reduces AI API usage significantly
3. **Predictable Behavior**: Pure math-based signals (RSI < 30 = BUY, etc.)
4. **Rate Limit Avoidance**: No risk of hitting AI provider rate limits
5. **Debugging**: Easier to trace exact decision logic

## When to Use Each Mode

| Scenario | ticker_analysis | ticker_selection | market_summary |
|----------|----------------|------------------|----------------|
| Maximum speed | ❌ | ❌ | ❌ |
| Cost-conscious | ❌ | ✅ | ❌ |
| Balanced approach | ❌ | ✅ | ✅ |
| Full AI features | ✅ | ✅ | ✅ |
| Rate limit issues | ❌ | ❌ | ❌ |

## Command Line Usage

### Disable All AI Features (Pure Technical Analysis)
```bash
python main.py --no-ai --continuous -d 60
```

### Disable Only Individual Ticker Analysis
```bash
# Use AI for smart ticker selection, but rely on technical indicators for analysis
python main.py --no-ai-ticker-analysis --continuous -d 60
```

### Disable Ticker Selection and Market Summary
```bash
# Use AI only for individual ticker insights
python main.py --no-ai-ticker-selection --no-ai-market-summary -c -d 90
```

### Custom Combinations
```bash
# Fast mode: Technical-only analysis with AI ticker selection
python main.py --no-ai-ticker-analysis --no-ai-market-summary -c -d 60 --max-symbols 50

# Cost-saving mode: No AI for individual tickers
python main.py --no-ai-ticker-analysis -c -d 120 --max-trades 5
```

### Available Command-Line Flags

| Flag | Description |
|------|-------------|
| `--no-ai` | Disable ALL AI features (ticker analysis, selection, and market summaries) |
| `--no-ai-ticker-analysis` | Disable AI for individual ticker analysis (use only RSI/SMA) |
| `--no-ai-ticker-selection` | Disable AI-based ticker selection (use standard ticker lists) |
| `--no-ai-market-summary` | Disable AI market sentiment summaries |

### View All Options
```bash
python main.py --help
```

Run the example script:
```bash
python example_no_ai_analysis.py
```

Or modify `main.py` to configure before running:
```python
# In main.py, after bot initialization:
bot = SmartTradingBot()

# Add configuration
bot.configure_ai_usage(ticker_analysis=False)

# Continue with normal execution
bot.run_analysis(...)
```

## Performance Comparison

### With AI Ticker Analysis
- Analysis time per ticker: ~5-15 seconds
- API calls per ticker: 3-5
- Cost per 100 tickers: $0.50-$2.00
- Signal quality: Enhanced with news/sentiment

### Without AI Ticker Analysis  
- Analysis time per ticker: <1 second
- API calls per ticker: 0
- Cost per 100 tickers: $0.00
- Signal quality: Pure technical (reliable but basic)

## Direct Variable Access

You can also set the flags directly:
```python
bot = SmartTradingBot()

# Direct assignment
bot.use_ai_for_ticker_analysis = False
bot.use_ai_for_ticker_selection = True
bot.use_ai_for_market_summary = False

# Or use the helper method (recommended)
bot.configure_ai_usage(ticker_analysis=False, ticker_selection=True)
```

## Logging Output

When configured, you'll see:
```
🧠 AI enabled for: ticker selection
```

Or for full technical:
```
🚫 All AI features disabled - using pure technical analysis
```
