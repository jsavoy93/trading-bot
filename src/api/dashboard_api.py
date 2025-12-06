"""
Flask API for Trading Bot Web Dashboard

Provides REST endpoints for:
- Bot status and configuration
- Portfolio data and performance
- Recent trades and signals
- Live bot control (start/stop/configure)
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import os
import sys
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent.parent)
sys.path.insert(0, src_path)

from database.simple_rest import SimpleSupabaseREST
from config.settings import validate_settings

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize database client
db = SimpleSupabaseREST()

# Initialize Alpaca clients for fetching data
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient

alpaca_api_key = os.getenv("ALPACA_API_KEY")
alpaca_api_secret = os.getenv("ALPACA_API_SECRET")
alpaca_base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

trading_client = None
data_client = None

if alpaca_api_key and alpaca_api_secret:
    trading_client = TradingClient(
        api_key=alpaca_api_key,
        secret_key=alpaca_api_secret,
        paper=True
    )
    data_client = StockHistoricalDataClient(
        api_key=alpaca_api_key,
        secret_key=alpaca_api_secret
    )

# Bot instance (will be set when bot starts)
bot_instance = None
bot_status = {
    "running": False,
    "mode": "stopped",
    "session_id": None,
    "started_at": None,
    "loop_count": 0,
    "last_update": None
}


# ============================================================================
# STATUS & CONFIGURATION ENDPOINTS
# ============================================================================

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get current bot status and configuration"""
    config = {
        "alpaca_mode": "PAPER" if "paper-api" in os.getenv("ALPACA_BASE_URL", "") else "LIVE",
        "use_advanced_signals": os.getenv("USE_ADVANCED_SIGNALS", "true") == "true",
        "use_scored_signals": os.getenv("USE_SCORED_SIGNALS", "true") == "true",
        "use_atr_exits": os.getenv("USE_ATR_EXITS", "true") == "true",
        "use_atr_sizing": os.getenv("USE_ATR_SIZING", "true") == "true",
        "min_buy_score": float(os.getenv("MIN_BUY_SCORE", "3.0")),
        "min_sell_score": float(os.getenv("MIN_SELL_SCORE", "-3.0")),
        "strong_threshold": float(os.getenv("STRONG_SIGNAL_THRESHOLD", "4.5")),
        "min_signal_strength": os.getenv("MIN_SIGNAL_STRENGTH", "medium")
    }
    
    return jsonify({
        "status": bot_status,
        "config": config,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@app.route('/api/config', methods=['POST'])
def update_config():
    """Update bot configuration (signal thresholds, etc)"""
    data = request.json
    
    # Update environment variables
    if 'min_buy_score' in data:
        os.environ["MIN_BUY_SCORE"] = str(data['min_buy_score'])
    if 'min_sell_score' in data:
        os.environ["MIN_SELL_SCORE"] = str(data['min_sell_score'])
    if 'strong_threshold' in data:
        os.environ["STRONG_SIGNAL_THRESHOLD"] = str(data['strong_threshold'])
    if 'min_signal_strength' in data:
        os.environ["MIN_SIGNAL_STRENGTH"] = data['min_signal_strength']
    
    # If bot is running, update its settings
    if bot_instance:
        bot_instance.min_buy_score = float(os.getenv("MIN_BUY_SCORE", "3.0"))
        bot_instance.min_sell_score = float(os.getenv("MIN_SELL_SCORE", "-3.0"))
        bot_instance.strong_threshold = float(os.getenv("STRONG_SIGNAL_THRESHOLD", "4.5"))
        bot_instance.min_signal_strength = os.getenv("MIN_SIGNAL_STRENGTH", "medium")
        
        # Recreate strategy with new thresholds
        if hasattr(bot_instance, '_tech_strategy'):
            from trading.strategy import TechnicalStrategy
            bot_instance._tech_strategy = TechnicalStrategy(
                min_buy_score=bot_instance.min_buy_score,
                min_sell_score=bot_instance.min_sell_score,
                strong_threshold=bot_instance.strong_threshold
            )
    
    return jsonify({"success": True, "message": "Configuration updated"})


@app.route('/api/signal-profiles', methods=['GET'])
def get_signal_profiles():
    """Get available signal profile presets"""
    profiles = {
        "conservative": {
            "name": "Conservative",
            "description": "Fewer, higher quality trades",
            "min_buy_score": 4.0,
            "min_sell_score": -4.0,
            "strong_threshold": 5.5
        },
        "balanced": {
            "name": "Balanced",
            "description": "Default - good balance of quality vs quantity",
            "min_buy_score": 3.0,
            "min_sell_score": -3.0,
            "strong_threshold": 4.5
        },
        "aggressive": {
            "name": "Aggressive",
            "description": "More trading opportunities, lower quality bar",
            "min_buy_score": 2.5,
            "min_sell_score": -2.5,
            "strong_threshold": 4.0
        }
    }
    return jsonify(profiles)


@app.route('/api/signal-profiles/<profile_name>', methods=['POST'])
def apply_signal_profile(profile_name):
    """Apply a signal profile preset"""
    profiles = {
        "conservative": {"min_buy": 4.0, "min_sell": -4.0, "strong": 5.5},
        "balanced": {"min_buy": 3.0, "min_sell": -3.0, "strong": 4.5},
        "aggressive": {"min_buy": 2.5, "min_sell": -2.5, "strong": 4.0}
    }
    
    if profile_name not in profiles:
        return jsonify({"success": False, "error": "Invalid profile"}), 400
    
    profile = profiles[profile_name]
    return update_config({
        "min_buy_score": profile["min_buy"],
        "min_sell_score": profile["min_sell"],
        "strong_threshold": profile["strong"]
    })


# ============================================================================
# PORTFOLIO & PERFORMANCE ENDPOINTS
# ============================================================================

@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    """Get current portfolio holdings and performance"""
    if not trading_client:
        return jsonify({"error": "Alpaca client not initialized"}), 503
    
    try:
        # Get account info
        account = trading_client.get_account()
        
        # Get positions
        positions = trading_client.get_all_positions()
        
        # Calculate total unrealized P&L from positions
        total_unrealized_pl = sum(float(pos.unrealized_pl) for pos in positions)
        total_cost_basis = sum(float(pos.cost_basis) for pos in positions)
        unrealized_pnl_pct = (total_unrealized_pl / total_cost_basis * 100) if total_cost_basis > 0 else 0
        
        portfolio_data = {
            "account": {
                "equity": float(account.equity),
                "cash": float(account.cash),
                "buying_power": float(account.buying_power),
                "portfolio_value": float(account.portfolio_value),
                "last_equity": float(account.last_equity),
                "unrealized_pnl": total_unrealized_pl,
                "unrealized_pnl_pct": unrealized_pnl_pct,
            },
            "positions": [
                {
                    "symbol": pos.symbol,
                    "qty": int(pos.qty),
                    "market_value": float(pos.market_value),
                    "cost_basis": float(pos.cost_basis),
                    "unrealized_pl": float(pos.unrealized_pl),
                    "unrealized_plpc": float(pos.unrealized_plpc) * 100,
                    "current_price": float(pos.current_price),
                    "avg_entry_price": float(pos.avg_entry_price),
                    "side": pos.side
                }
                for pos in positions
            ],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return jsonify(portfolio_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/performance', methods=['GET'])
def get_performance():
    """Get performance metrics and statistics"""
    if not db.is_available():
        return jsonify({"error": "Database not available"}), 503
    
    try:
        # Get recent sessions
        sessions = db.get_recent_sessions(limit=10)
        
        # Get all trades
        all_trades = db.get_all_trades(limit=200)
        
        # Separate closed trades (with P&L) from open trades
        closed_trades = [t for t in all_trades if t.get('pnl') is not None]
        open_trades = [t for t in all_trades if t.get('pnl') is None]
        
        # Calculate metrics for closed trades only
        total_closed = len(closed_trades)
        winning_trades = sum(1 for t in closed_trades if t.get('pnl', 0) > 0)
        losing_trades = sum(1 for t in closed_trades if t.get('pnl', 0) < 0)
        win_rate = (winning_trades / total_closed * 100) if total_closed > 0 else 0
        
        total_pnl = sum(t.get('pnl', 0) for t in closed_trades)
        avg_win = sum(t.get('pnl', 0) for t in closed_trades if t.get('pnl', 0) > 0) / winning_trades if winning_trades > 0 else 0
        avg_loss = sum(t.get('pnl', 0) for t in closed_trades if t.get('pnl', 0) < 0) / losing_trades if losing_trades > 0 else 0
        
        # Get unrealized P&L from current positions
        unrealized_pnl = 0
        if trading_client:
            try:
                positions = trading_client.get_all_positions()
                unrealized_pnl = sum(float(pos.unrealized_pl) for pos in positions)
            except:
                pass
        
        return jsonify({
            "total_trades": len(all_trades),
            "closed_trades": total_closed,
            "open_trades": len(open_trades),
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "realized_pnl": total_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl + unrealized_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(avg_win / avg_loss) if avg_loss != 0 else 0,
            "recent_sessions": sessions,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# TRADES & SIGNALS ENDPOINTS
# ============================================================================

@app.route('/api/trades', methods=['GET'])
def get_trades():
    """Get recent trades"""
    limit = request.args.get('limit', 50, type=int)
    
    if not db.is_available():
        return jsonify({"error": "Database not available"}), 503
    
    try:
        trades = db.get_all_trades(limit=limit)
        return jsonify({
            "trades": trades,
            "count": len(trades),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get recent trading sessions"""
    limit = request.args.get('limit', 10, type=int)
    
    if not db.is_available():
        return jsonify({"error": "Database not available"}), 503
    
    try:
        sessions = db.get_recent_sessions(limit=limit)
        return jsonify({
            "sessions": sessions,
            "count": len(sessions),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Get recent bot logs"""
    limit = request.args.get('limit', 1000, type=int)
    
    try:
        # Try multiple log files in order of preference
        log_files = ['bot_run.log', 'bot_live.log', 'ai_agent.log', 'trading_bot.log', 'bot.log']
        logs = []
        
        for log_file in log_files:
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    # Get last N lines
                    recent_lines = lines[-limit:] if len(lines) > limit else lines
                    
                    for line in recent_lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        # Parse log line format: timestamp - module - level - message
                        # Example: 2025-12-06 04:26:17,953 - AITradingAgent - INFO - 📰 Found 11 articles for TJX
                        try:
                            parts = line.split(' - ', 3)
                            if len(parts) >= 4:
                                # Standard format with module
                                logs.append({
                                    'timestamp': parts[0],
                                    'level': parts[2].strip(),
                                    'message': parts[3]
                                })
                            elif len(parts) >= 3:
                                # Simpler format: timestamp - level - message
                                logs.append({
                                    'timestamp': parts[0],
                                    'level': parts[1].strip(),
                                    'message': parts[2]
                                })
                            else:
                                # Fallback for non-standard format
                                logs.append({
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                    'level': 'INFO',
                                    'message': line
                                })
                        except:
                            # If parsing fails, just show the raw line
                            logs.append({
                                'timestamp': datetime.now(timezone.utc).isoformat(),
                                'level': 'INFO',
                                'message': line
                            })
                break  # Stop after first log file found
        
        return jsonify({
            "logs": logs,
            "count": len(logs),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# BOT CONTROL ENDPOINTS
# ============================================================================

@app.route('/api/bot/start', methods=['POST'])
def start_bot():
    """Start the trading bot (not implemented - bot runs separately)"""
    return jsonify({
        "success": False,
        "message": "Bot must be started via command line: python main.py -c"
    }), 501


@app.route('/api/bot/stop', methods=['POST'])
def stop_bot():
    """Stop the trading bot (not implemented - use Ctrl+C)"""
    return jsonify({
        "success": False,
        "message": "Bot must be stopped manually with Ctrl+C"
    }), 501


# ============================================================================
# WEBSOCKET EVENTS (Real-time Updates)
# ============================================================================

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print('Client connected')
    emit('status', bot_status)


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print('Client disconnected')


def broadcast_trade(trade_data: Dict):
    """Broadcast new trade to all connected clients"""
    socketio.emit('new_trade', trade_data)


def broadcast_signal(signal_data: Dict):
    """Broadcast new signal to all connected clients"""
    socketio.emit('new_signal', signal_data)


def broadcast_status_update(status_data: Dict):
    """Broadcast bot status update"""
    bot_status.update(status_data)
    bot_status['last_update'] = datetime.now(timezone.utc).isoformat()
    socketio.emit('status_update', bot_status)


def broadcast_log(level: str, message: str):
    """Broadcast log message to all connected clients"""
    log_data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'level': level,
        'message': message
    }
    socketio.emit('new_log', log_data)


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/', methods=['GET'])
def index():
    """Serve dashboard HTML"""
    from flask import send_from_directory
    import os
    dashboard_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'dashboard')
    return send_from_directory(dashboard_path, 'index.html')


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "available" if db.is_available() else "unavailable"
    })


# ============================================================================
# MAIN
# ============================================================================

def run_dashboard(host='0.0.0.0', port=5000, debug=False):
    """Run the dashboard API server"""
    print(f"🌐 Starting Trading Bot Dashboard API on http://{host}:{port}")
    print(f"📊 Database: {'✅ Available' if db.is_available() else '❌ Not available'}")
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    validate_settings()
    run_dashboard(debug=True)
