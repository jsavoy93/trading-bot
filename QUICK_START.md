# 🚀 Quick Start Guide

## Running the Trading Bot

### 🏃‍♂️ Single Session (Run Once)
```bash
python main.py
```

### 🔄 Continuous Mode (Loop Forever)
```bash
python main.py --continuous -d 60
# -d 60 = 60 seconds between loops
```

## AI Configuration Options

### ⚡ Fastest: Disable AI Ticker Analysis (Recommended)
```bash
python main.py --no-ai-ticker-analysis -c -d 60
```
- Uses only RSI/SMA technical indicators for trading decisions
- AI still picks smart tickers and provides market overview
- Analysis takes <1 second per ticker vs 5-15 seconds with AI
- **Best for production: Fast + cost-effective**

### 🚫 Pure Technical Analysis (No AI At All)
```bash
python main.py --no-ai -c -d 60
```
- 100% technical analysis (RSI, SMA only)
- Standard ticker lists (S&P 500)
- Zero AI API costs
- **Best for testing and debugging**

### 🧠 Full AI Mode (Default)
```bash
python main.py -c -d 60
```
- AI-enhanced ticker analysis with news/sentiment
- Smart ticker selection based on portfolio
- Market sentiment summaries
- **Best for deep research**

## Common Options

```bash
# Analyze more symbols per loop
python main.py -c -d 60 --max-symbols 50

# Allow more trades per loop
python main.py -c -d 60 --max-trades 5

# Combine options for high-volume fast trading
python main.py --no-ai-ticker-analysis -c -d 120 --max-symbols 100 --max-trades 3
```

## When to Use Each Mode

| Mode | Speed | API Cost | AI Features | Best For |
|------|-------|----------|-------------|----------|
| `--no-ai` | Fastest | $0 | None | Testing, rate limits |
| `--no-ai-ticker-analysis` | Fast | Low | Smart picks | **Production** |
| Full AI (default) | Slower | Higher | All features | Research |

## All Available Flags

```bash
python main.py --help

Options:
  --continuous, -c              Run in continuous loop mode
  --delay DELAY, -d             Seconds between loops (default: 300)
  --max-symbols N               Max symbols per loop (default: 30)
  --max-trades N                Max trades per loop (default: 2)
  --no-ai                       Disable ALL AI features
  --no-ai-ticker-analysis       Disable AI for individual tickers (fastest)
  --no-ai-ticker-selection      Disable AI ticker picking
  --no-ai-market-summary        Disable AI market summaries
```

## Example Commands

### High-Volume Fast Trading
```bash
python main.py --no-ai-ticker-analysis -c -d 30 --max-symbols 100
```

### Conservative Deep Analysis  
```bash
python main.py -c -d 300 --max-symbols 10 --max-trades 1
```

### Quick Test Run
```bash
python main.py --no-ai --max-symbols 5
```

## 🗄️ Database Management

```bash
# Check database status
python scripts/run_migrations.py status

# Setup database tables
python scripts/run_migrations.py setup

# Validate migration worked
python scripts/run_migrations.py validate
```

## 🛠️ Easiest Way (Shell Scripts)

```bash
# Run bot
./run.sh

# Manage database
./migrate.sh status
./migrate.sh setup
./migrate.sh validate
```

### 🏃‍♂️ **Easiest Way** (Recommended)

```bash
./run.sh
```

This script automatically:
- ✅ Checks for virtual environment
- ✅ Installs missing packages if needed
- ✅ Runs the trading bot with correct Python

### 🗄️ **Database Management**

```bash
# Check database status
./migrate.sh status

# Setup database tables
./migrate.sh setup

# Validate migration worked
./migrate.sh validate
```

### 🛠️ **Alternative Method**

If you prefer manual control:

```bash
# Activate virtual environment and run
.venv/bin/python main.py

# Or for migrations
.venv/bin/python scripts/run_migrations.py status
```

### 📁 **Current Project Structure**

```
trading-bot/
├── run.sh                    # ✨ Easy bot runner
├── migrate.sh                # ✨ Easy migration tool
├── main.py                   # Main entry point
├── src/                      # Source code
├── scripts/                  # Migration scripts
├── migrations/               # Database migrations
├── docs/                     # Documentation
└── legacy/                   # Old files (safe to delete)
```

### 🎯 **What's Working Now**

- ✅ **Trading Bot**: `./run.sh` works perfectly
- ✅ **Database Migrations**: `./migrate.sh status` works
- ✅ **Virtual Environment**: Automatically handled
- ✅ **Package Management**: Auto-installs if needed
- ✅ **Import Paths**: All fixed and working

### 🚨 **If You See Import Errors**

The scripts handle this automatically, but if you run Python directly:

```bash
# Always use the virtual environment Python
.venv/bin/python main.py

# NOT just: python main.py
```

### 🎉 **You're All Set!**

Your trading bot is now fully functional and ready to trade! 

**Next Steps:**
1. Run `./run.sh` to start trading
2. Optionally setup database with `./migrate.sh setup`
3. Monitor performance and adjust parameters as needed

The bot will safely run in paper trading mode, so no real money is at risk. 📈