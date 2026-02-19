"""
Trading Bot Dashboard - Web Interface
Mobile-friendly dashboard to monitor and control your trading bot.
"""
import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv

# Import trading bot components
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent / "src"))
from database.simple_rest import simple_rest
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(title="Trading Bot Dashboard")

# Static files
static_path = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Jinja2 templates
template_path = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_path))

# Custom filter for timezone conversion
def to_central_time(utc_str):
    """Convert UTC ISO string to Central Time"""
    if not utc_str:
        return 'N/A'
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromisoformat(utc_str.replace('Z', '+00:00'))
        # Convert to Central Time (UTC-6, or UTC-5 during DST)
        # For simplicity, use UTC-6 (CST)
        central = dt.astimezone(timezone(timedelta(hours=-6)))
        return central.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return utc_str[:19] if utc_str else 'N/A'

jinja_env.filters['to_central'] = to_central_time

# Alpaca client
api_key = os.getenv("ALPACA_API_KEY")
api_secret = os.getenv("ALPACA_API_SECRET")
base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

trading_client = None
if api_key and api_secret:
    trading_client = TradingClient(api_key, api_secret, paper=True)

# Database
db = simple_rest


def get_account_info() -> Dict:
    """Get account info from Alpaca"""
    if not trading_client:
        return {}
    try:
        account = trading_client.get_account()
        return {
            "portfolio_value": float(account.portfolio_value),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "pattern_day_trader": account.pattern_day_trader,
            "trading_blocked": account.trading_blocked,
            "transfers_blocked": account.transfers_blocked,
        }
    except Exception as e:
        logger.error(f"Failed to get account info: {e}")
        return {"error": str(e)}


def get_positions() -> List[Dict]:
    """Get current positions"""
    if not trading_client:
        return []
    try:
        positions = trading_client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "current_price": float(p.current_price),
            }
            for p in positions
            if float(p.qty) > 0
        ]
    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        return []


def get_orders(limit: int = 20) -> List[Dict]:
    """Get recent orders"""
    if not trading_client:
        return []
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import OrderStatus
        
        request = GetOrdersRequest(
            status=OrderStatus.ALL,
            limit=limit
        )
        orders = trading_client.get_orders(request)
        return [
            {
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "filled_qty": float(o.filled_qty or 0),
                "status": o.status.value,
                "created_at": o.created_at,
                "filled_at": o.filled_at,
            }
            for o in orders
        ]
    except Exception as e:
        logger.error(f"Failed to get orders: {e}")
        return []


def get_trades_from_db(limit: int = 20) -> List[Dict]:
    """Get trades from database"""
    if not db.is_available():
        return []
    try:
        # Get from Supabase
        import requests
        headers = {
            "apikey": db.api_key,
            "Authorization": f"Bearer {db.api_key}",
        }
        response = requests.get(
            f"{db.rest_url}/trades?order=created_at.desc&limit={limit}",
            headers=headers,
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get trades from DB: {e}")
    return []


def get_recent_sessions(limit: int = 5) -> List[Dict]:
    """Get recent trading sessions"""
    if not db.is_available():
        return []
    try:
        import requests
        headers = {
            "apikey": db.api_key,
            "Authorization": f"Bearer {db.api_key}",
        }
        response = requests.get(
            f"{db.rest_url}/trading_sessions?order=session_start.desc&limit={limit}",
            headers=headers,
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get sessions: {e}")
    return []


# Routes
@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Main dashboard page"""
    try:
        account = get_account_info()
        positions = get_positions()
        orders = get_orders(10)
        db_trades = get_trades_from_db(10)
        sessions = get_recent_sessions(5)
        
        # Calculate totals
        total_position_value = sum(p["market_value"] for p in positions)
        total_unrealized_pl = sum(p["unrealized_pl"] for p in positions)
        
        template = jinja_env.get_template("dashboard.html")
        return template.render(
            account=account,
            positions=positions,
            orders=orders,
            db_trades=db_trades,
            sessions=sessions,
            total_position_value=total_position_value,
            total_unrealized_pl=total_unrealized_pl,
            db_available=db.is_available(),
            now=datetime.now(),
        )
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return f"Error loading dashboard: {e}", 500


@app.get("/api/account")
def api_account():
    """API endpoint for account info"""
    return get_account_info()


@app.get("/api/positions")
def api_positions():
    """API endpoint for positions"""
    return get_positions()


@app.get("/api/orders")
def api_orders(limit: int = 20):
    """API endpoint for orders"""
    return get_orders(limit)


@app.get("/api/trades")
def api_trades(limit: int = 20):
    """API endpoint for trades from DB"""
    return get_trades_from_db(limit)


@app.get("/api/sessions")
def api_sessions(limit: int = 5):
    """API endpoint for sessions"""
    return get_recent_sessions(limit)


@app.get("/api/logs")
def api_logs(session_id: int = None, lines: int = 200):
    """Get log entries, optionally filtered by session"""
    import requests as _requests
    from datetime import datetime
    
    log_path = Path(__file__).parent / "trading_bot.log"
    if not log_path.exists():
        return {"logs": [], "error": "Log file not found"}
    
    try:
        with open(log_path, 'r') as f:
            all_lines = f.readlines()
        
        # If session_id provided, filter by session timestamps
        if session_id and db.is_available():
            try:
                headers = {
                    "apikey": db.api_key,
                    "Authorization": f"Bearer {db.api_key}",
                }
                # Get session info
                resp = requests.get(
                    f"{db.rest_url}/trading_sessions?id=eq.{session_id}",
                    headers=headers,
                    timeout=5
                )
                if resp.status_code == 200 and resp.json():
                    session = resp.json()[0]
                    start = session.get('session_start')
                    end = session.get('session_end')
                    
                    if start:
                        # Filter lines within session time range
                        filtered = []
                        in_session = False
                        for line in all_lines:
                            if not line.strip():
                                filtered.append(line)
                                continue
                            # Extract timestamp from log line (format: "2026-02-18 19:58:57 - INFO - ...")
                            try:
                                log_ts = line.split(' - ')[0].strip()
                                log_time = datetime.strptime(log_ts, '%Y-%m-%d %H:%M:%S')
                                
                                if start:
                                    start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                                    # Convert to CST (UTC-6) for comparison
                                    from datetime import timezone, timedelta
                                    start_cst = start_dt.astimezone(timezone(timedelta(hours=-6)))
                                    start_str = start_cst.strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    if log_time >= datetime.strptime(start_str, '%Y-%m-%d %H:%M:%S'):
                                        in_session = True
                                
                                if end:
                                    end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
                                    end_cst = end_dt.astimezone(timezone(timedelta(hours=-6)))
                                    end_str = end_cst.strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    if log_time > datetime.strptime(end_str, '%Y-%m-%d %H:%M:%S'):
                                        in_session = False
                                        
                                if in_session:
                                    filtered.append(line)
                            except:
                                # If we can't parse timestamp, include line if we're in session
                                if in_session:
                                    filtered.append(line)
                        
                        return {"logs": filtered[-lines:], "session_id": session_id}
            except Exception as e:
                pass  # Fall back to recent logs
        
        # Default: return most recent lines
        return {"logs": all_lines[-lines:], "session_id": None}
    except Exception as e:
        return {"logs": [], "error": str(e)}


@app.post("/api/start-session")
def api_start_session():
    """Start a new trading session"""
    if not db.is_available():
        raise HTTPException(status_code=400, detail="Database not available")
    
    session_id = db.create_session(
        bot_version="2.1.0",
        configuration={},
        is_paper_trading=True,
        notes="Started from dashboard"
    )
    return {"status": "ok", "session_id": session_id}


@app.post("/api/stop-session")
def api_stop_session():
    """Stop the current trading session"""
    # This would need the session ID - simplified for now
    return {"status": "ok", "message": "Session stopped"}


@app.get("/api/analytics/overview")
def api_analytics_overview():
    """Get overall trading analytics"""
    if not db.is_available():
        return {"error": "Database not available"}
    
    try:
        trades = db.get_all_trades()
        if not trades:
            return {"error": "No trades found"}
        
        # Helper to safely get pnl (handle None)
        def safe_pnl(t):
            pnl = t.get('pnl')
            return pnl if pnl is not None else 0
        
        # Basic stats
        total_trades = len(trades)
        winners = sum(1 for t in trades if safe_pnl(t) > 0)
        losers = sum(1 for t in trades if safe_pnl(t) < 0)
        win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
        
        total_pnl = sum(safe_pnl(t) for t in trades)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
        
        # By signal type (if available)
        signal_types = {}
        for t in trades:
            signal = t.get('signal_type', 'unknown')
            if signal not in signal_types:
                signal_types[signal] = {'count': 0, 'wins': 0, 'pnl': 0}
            signal_types[signal]['count'] += 1
            if safe_pnl(t) > 0:
                signal_types[signal]['wins'] += 1
            signal_types[signal]['pnl'] += safe_pnl(t)
        
        # By symbol
        symbols = {}
        for t in trades:
            sym = t.get('symbol', 'unknown')
            if sym not in symbols:
                symbols[sym] = {'count': 0, 'wins': 0, 'pnl': 0}
            symbols[sym]['count'] += 1
            if safe_pnl(t) > 0:
                symbols[sym]['wins'] += 1
            symbols[sym]['pnl'] += safe_pnl(t)
        
        # Top performers
        top_winners = sorted(symbols.items(), key=lambda x: x[1]['pnl'], reverse=True)[:5]
        top_losers = sorted(symbols.items(), key=lambda x: x[1]['pnl'])[:5]
        
        return {
            "total_trades": total_trades,
            "winners": winners,
            "losers": losers,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "by_signal_type": signal_types,
            "top_winners": [{"symbol": s, **stats} for s, stats in top_winners if stats['pnl'] > 0],
            "top_losers": [{"symbol": s, **stats} for s, stats in top_losers if stats['pnl'] < 0][:5]
        }
        
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/analytics/timing")
def api_analytics_timing():
    """Get timing-based analytics (hour/day of week)"""
    if not db.is_available():
        return {"error": "Database not available"}
    
    try:
        trades = db.get_all_trades()
        if not trades:
            return {"error": "No trades found"}
        
        # By hour of day
        hours = {h: {'count': 0, 'wins': 0, 'pnl': 0} for h in range(24)}
        
        # By day of week
        days = {d: {'count': 0, 'wins': 0, 'pnl': 0} for d in range(7)}
        
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        
        for t in trades:
            # Parse timestamp - check order_time or signal_time
            ts = t.get('order_time') or t.get('signal_time') or ''
            if isinstance(ts, str) and ts:
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    hour = dt.hour
                    day = dt.weekday()
                except:
                    continue
            else:
                continue
            
            hours[hour]['count'] += 1
            days[day]['count'] += 1
            
            pnl = t.get('pnl') or 0
            if pnl > 0:
                hours[hour]['wins'] += 1
                days[day]['wins'] += 1
            
            hours[hour]['pnl'] += pnl
            days[day]['pnl'] += pnl
        
        # Format results
        hours_data = []
        for h, stats in hours.items():
            if stats['count'] > 0:
                wr = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
                hours_data.append({
                    "hour": h,
                    "count": stats['count'],
                    "wins": stats['wins'],
                    "win_rate": round(wr, 1),
                    "pnl": round(stats['pnl'], 2)
                })
        
        days_data = []
        for d, stats in days.items():
            if stats['count'] > 0:
                wr = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
                days_data.append({
                    "day": day_names[d],
                    "day_num": d,
                    "count": stats['count'],
                    "wins": stats['wins'],
                    "win_rate": round(wr, 1),
                    "pnl": round(stats['pnl'], 2)
                })
        
        return {
            "by_hour": hours_data,
            "by_day": days_data
        }
        
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/analytics/rsi")
def api_analytics_rsi():
    """Get analytics by RSI range at entry"""
    if not db.is_available():
        return {"error": "Database not available"}
    
    try:
        trades = db.get_all_trades()
        if not trades:
            return {"error": "No trades found"}
        
        # By RSI range
        ranges = {
            'oversold (<30)': {'count': 0, 'wins': 0, 'pnl': 0},
            'neutral (30-50)': {'count': 0, 'wins': 0, 'pnl': 0},
            'neutral (50-70)': {'count': 0, 'wins': 0, 'pnl': 0},
            'overbought (>70)': {'count': 0, 'wins': 0, 'pnl': 0}
        }
        
        for t in trades:
            rsi_raw = t.get('rsi_entry') or t.get('rsi')
            if rsi_raw is None:
                continue
            
            try:
                rsi = float(rsi_raw)
            except (ValueError, TypeError):
                continue
            
            if rsi < 30:
                key = 'oversold (<30)'
            elif rsi < 50:
                key = 'neutral (30-50)'
            elif rsi < 70:
                key = 'neutral (50-70)'
            else:
                key = 'overbought (>70)'
            
            ranges[key]['count'] += 1
            pnl = t.get('pnl') or 0
            if pnl > 0:
                ranges[key]['wins'] += 1
            ranges[key]['pnl'] += pnl
        
        result = []
        for key, stats in ranges.items():
            if stats['count'] > 0:
                wr = (stats['wins'] / stats['count'] * 100)
                result.append({
                    "range": key,
                    "count": stats['count'],
                    "wins": stats['wins'],
                    "win_rate": round(wr, 1),
                    "pnl": round(stats['pnl'], 2)
                })
        
        return {"by_rsi_range": result}
        
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
