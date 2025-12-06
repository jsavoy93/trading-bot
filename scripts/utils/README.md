# Utility Scripts

This directory contains utility scripts for monitoring and managing the trading bot.

## Scripts

### validate_config.py
Validates your `.env` configuration and checks that all required API keys are set.

```bash
python scripts/utils/validate_config.py
```

### view_cache.py
Displays cache statistics and performance metrics for the bot's caching system.

```bash
python scripts/utils/view_cache.py
```

### view_performance.py
Shows performance metrics including function execution times and API call statistics.

```bash
python scripts/utils/view_performance.py
```

### run_dashboard.py
Launches the web dashboard for monitoring the trading bot in real-time.

```bash
python scripts/utils/run_dashboard.py
```

## Usage

All scripts can be run from the project root:

```bash
# From project root
python scripts/utils/<script_name>.py
```

Or with the python path already configured:

```bash
cd /workspaces/trading-bot
python scripts/utils/view_performance.py
```
