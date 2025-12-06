# Trading Bot Web Dashboard

## Quick Start

### 1. Install Dashboard Dependencies
```bash
pip install flask flask-cors flask-socketio python-socketio
```

### 2. Start the API Server
```bash
python run_dashboard.py
```

### 3. Open the Dashboard
Open `dashboard/index.html` in your web browser, or navigate to:
```
http://localhost:5000
```

### 4. Start the Trading Bot (in another terminal)
```bash
python main.py -c
```

## Features

### Real-Time Monitoring
- **Portfolio Overview**: Live equity, cash, and P&L tracking
- **Performance Metrics**: Win rate, total trades, profit factor
- **Recent Trades**: Scrollable table of recent executions
- **WebSocket Updates**: Real-time trade and signal notifications

### Signal Configuration
- **Signal Profiles**: Switch between Conservative, Balanced, Aggressive
- **Minimum Strength**: Filter signals by WEAK, MEDIUM, STRONG
- **Live Updates**: Changes apply immediately to running bot

### API Endpoints

#### Status & Configuration
- `GET /api/status` - Bot status and current configuration
- `POST /api/config` - Update signal thresholds
- `GET /api/signal-profiles` - Available profile presets
- `POST /api/signal-profiles/{name}` - Apply a profile

#### Portfolio & Performance
- `GET /api/portfolio` - Current positions and account data
- `GET /api/performance` - Performance metrics and statistics

#### Trades & Sessions
- `GET /api/trades?limit=N` - Recent trades
- `GET /api/sessions?limit=N` - Recent trading sessions

#### Health
- `GET /health` - API health check

### WebSocket Events

#### Client → Server
- `connect` - Establish WebSocket connection
- `disconnect` - Close connection

#### Server → Client
- `status` - Initial status on connect
- `status_update` - Bot status changes
- `new_trade` - New trade executed
- `new_signal` - New signal detected

## Architecture

```
┌─────────────────┐
│  Web Browser    │
│  (Dashboard)    │
└────────┬────────┘
         │ HTTP/WebSocket
         ▼
┌─────────────────┐
│  Flask API      │
│  (Port 5000)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────┐
│  Trading Bot    │◄────►│  Supabase DB │
│  (main.py)      │      │              │
└─────────────────┘      └──────────────┘
```

## Customization

### Change API Port
Edit `run_dashboard.py`:
```python
run_dashboard(host='0.0.0.0', port=8000, debug=False)
```

### Update Frontend API URL
Edit `dashboard/index.html`:
```javascript
const API_URL = 'http://localhost:8000/api';
```

### Add Custom Metrics
1. Add endpoint in `src/api/dashboard_api.py`
2. Add UI component in `dashboard/index.html`
3. Wire up with `fetch()` or WebSocket events

## Troubleshooting

### Dashboard shows "Connecting..."
- Ensure `run_dashboard.py` is running
- Check console for CORS errors
- Verify API_URL matches Flask port

### Portfolio data shows errors
- Start the trading bot: `python main.py -c`
- Bot must be running to fetch Alpaca data

### Database not available
- Check Supabase environment variables
- Verify `SUPABASE_URL` and `SUPABASE_KEY` are set

### Configuration changes not applying
- Bot must be running for live updates
- Check browser console for API errors
- Verify API server logs for errors

## Future Enhancements

- [ ] Charts for portfolio performance over time
- [ ] Signal strength distribution histogram
- [ ] Position management (close positions from UI)
- [ ] Trade history export (CSV/JSON)
- [ ] Email/SMS alert configuration
- [ ] Multi-bot support (monitor multiple instances)
- [ ] Dark/light theme toggle
- [ ] Mobile responsive layout improvements
