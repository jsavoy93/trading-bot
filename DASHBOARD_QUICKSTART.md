# 🌐 Web Dashboard Quick Start

## What You Just Got

A beautiful, real-time web dashboard for monitoring and controlling your trading bot!

## Features

✅ **Real-Time Monitoring**
- Live portfolio value and P&L
- Recent trades table with signal strengths
- Performance metrics (win rate, total trades)
- WebSocket updates (no page refresh needed)

✅ **Signal Control**
- Switch between Conservative/Balanced/Aggressive profiles
- Adjust minimum signal strength filter
- Changes apply to running bot in real-time

✅ **Modern UI**
- Dark theme optimized for trading
- Color-coded P&L (green/red)
- Responsive layout
- Signal strength badges

## How to Use

### 1. Start the Dashboard API
```bash
python run_dashboard.py
```

You'll see:
```
============================================================
🌐 TRADING BOT WEB DASHBOARD
============================================================
Dashboard will be available at: http://localhost:5000
```

### 2. Open the Dashboard
Open your browser and go to:
```
http://localhost:5000
```

Or directly open the HTML file:
```bash
open dashboard/index.html  # Mac
xdg-open dashboard/index.html  # Linux
```

### 3. Start the Trading Bot (in another terminal)
```bash
python main.py -c
```

### 4. Watch It Live!
The dashboard will automatically:
- Connect via WebSocket
- Load your portfolio data
- Show recent trades
- Update in real-time as trades execute

## Dashboard Controls

### Signal Profile Selector
- **Conservative**: min_buy=4.0, strong=5.5 (fewer, higher quality trades)
- **Balanced**: min_buy=3.0, strong=4.5 (default)
- **Aggressive**: min_buy=2.5, strong=4.0 (more opportunities)

### Minimum Strength Filter
- **Weak**: Accept all signals
- **Medium**: Skip WEAK signals (default)
- **Strong**: Only trade STRONG signals

### Apply Changes Button
Click to update the bot configuration in real-time!

### Refresh Data Button
Manually refresh all data from the API

## What You See

### Top Bar
- **Status Indicator**: Green dot = connected, Red = disconnected
- **Mode**: PAPER or LIVE trading
- **Profile**: Current signal profile
- **Last Update**: Timestamp of last refresh

### Portfolio Cards
- **💰 Portfolio Value**: Total equity and cash available
- **📈 P&L**: Unrealized profit/loss in $ and %
- **🎯 Performance**: Win rate and total trade count

### Recent Trades Table
- **Time**: When the trade was executed
- **Symbol**: Stock ticker
- **Side**: BUY or SELL (color-coded badges)
- **Qty**: Number of shares
- **Price**: Execution price
- **Signal**: STRONG or MEDIUM (color-coded)
- **P&L**: Profit/loss if closed

## API Endpoints

The dashboard connects to these endpoints:

### Status
- `GET /api/status` - Bot status and configuration
- `GET /health` - API health check

### Portfolio
- `GET /api/portfolio` - Live positions and account data
- `GET /api/performance` - Performance statistics

### Trades
- `GET /api/trades?limit=20` - Recent trades
- `GET /api/sessions?limit=10` - Trading sessions

### Configuration
- `POST /api/config` - Update signal thresholds
- `GET /api/signal-profiles` - List available profiles
- `POST /api/signal-profiles/conservative` - Apply a profile

## Troubleshooting

**Dashboard shows "Connecting..."**
- Make sure `run_dashboard.py` is running
- Check that Flask started on port 5000

**Portfolio shows errors**
- Start the trading bot: `python main.py -c`
- Bot must be running to fetch live data

**Trades table is empty**
- Normal if bot just started
- Trades will appear after first execution

**Configuration changes don't work**
- Bot must be running for live updates
- Check browser console (F12) for errors

## Next Steps

### Run Both Together
Terminal 1 (Dashboard):
```bash
python run_dashboard.py
```

Terminal 2 (Bot):
```bash
python main.py -c -d 300 --signal-profile balanced
```

Browser:
```
http://localhost:5000
```

### Monitor Multiple Bots
The dashboard can monitor any bot that:
1. Uses the same Supabase database
2. Is running the updated SmartBot code
3. Has the same environment variables

### Customize
- Edit `dashboard/index.html` for UI changes
- Edit `src/api/dashboard_api.py` for API changes
- Add new charts, metrics, or controls

Enjoy your professional trading dashboard! 🚀
