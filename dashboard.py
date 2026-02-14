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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
