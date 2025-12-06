# Trading Bot Launcher

Simple script to start both the dashboard and trading bot together.

## Usage

```bash
# Start with default (balanced) profile
./launch.sh

# Start with conservative profile
./launch.sh conservative

# Start with aggressive profile
./launch.sh aggressive

# Pass additional flags
./launch.sh balanced --ai-full
```

## What it does

1. ✅ Starts the web dashboard on http://localhost:5000
2. 🤖 Starts the trading bot with your chosen signal profile
3. 📊 Shows live logs in the terminal
4. 🔗 Dashboard automatically displays the same logs in the "Live Logs" tab
5. 🛑 When you press Ctrl+C, both services stop cleanly

## Stopping

Press `Ctrl+C` to stop both the bot and dashboard.

## Dashboard Features

Open http://localhost:5000 to see:
- 💰 Portfolio value and P&L
- 📈 Performance metrics
- 📊 Recent trades
- 📋 Live logs (auto-refreshing every 2 seconds)
- ⚙️ Signal configuration controls
