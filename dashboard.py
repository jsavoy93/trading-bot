"""
Trading Bot Dashboard - Web Interface
Mobile-friendly dashboard to monitor and control your trading bot.
"""
import json
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
from database.sqlite_db import sqlite_db as simple_rest
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

# Cached smart bot instance for analysis status
_smart_bot_instance = None

def get_smart_bot():
    """Get or create cached SmartTradingBot instance"""
    global _smart_bot_instance
    if _smart_bot_instance is None:
        try:
            sys.path.insert(0, str(Path(__file__).parent / "src"))
            from core.smart_bot import SmartTradingBot
            _smart_bot_instance = SmartTradingBot()
        except Exception as e:
            logger.error(f"Failed to create SmartTradingBot: {e}")
            return None
    return _smart_bot_instance

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


def get_trading_status() -> Dict:
    """Get current trading rules status"""
    status = {
        "margin_ok": True,
        "beta_ok": True,
        "beta_value": 1.0,
        "cash": 0,
        "rules": []
    }
    
    try:
        account = trading_client.get_account()
        status["cash"] = float(account.cash)
        
        if status["cash"] < 0:
            status["margin_ok"] = False
            status["rules"].append({
                "type": "danger",
                "icon": "🚫",
                "text": f"MARGIN ACTIVE: Cash is negative (${status['cash']:.2f}) - No new trades allowed"
            })
        else:
            status["rules"].append({
                "type": "success",
                "icon": "✅",
                "text": f"Cash: ${status['cash']:.2f} - Trading allowed"
            })
        
        # Get actual portfolio beta from bot
        beta_value = 1.0
        beta_error = None
        try:
            sys.path.insert(0, str(Path(__file__).parent / "src"))
            from core.smart_bot import SmartTradingBot
            bot = SmartTradingBot()
            beta_value = bot.get_portfolio_beta()
            # If beta is very small (< 0.01), something went wrong - use default
            if beta_value is None or beta_value < 0.01:
                beta_value = 1.0
                beta_error = "using default"
        except Exception as e:
            beta_error = str(e)[:30]
            beta_value = 1.0
        
        status["beta_value"] = beta_value
        
        # Show beta with limit
        if beta_value > 1.5:
            status["beta_ok"] = False
            status["rules"].append({
                "type": "danger",
                "icon": "🚫",
                "text": f"⚠️ Beta: {beta_value:.2f} (limit: 1.5) - BUYs blocked!"
            })
        else:
            status["rules"].append({
                "type": "info",
                "icon": "📊",
                "text": f"📊 Beta: ~{beta_value:.1f} (limit: 1.5) - OK"
            })
        
        status["rules"].append({
            "type": "info", 
            "icon": "⏱️",
            "text": "Trading window: 9:45 AM - 3:45 PM ET (excludes first/last 15 min)"
        })
        
    except Exception as e:
        logger.error(f"Failed to get trading status: {e}")
    
    return status


def get_positions() -> List[Dict]:
    """Get current positions with scores"""
    import pandas as pd
    
    if not trading_client:
        return {"positions": [], "by_sector": {}}
    try:
        positions = trading_client.get_all_positions()
        
        # Get bot for scoring
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from core.smart_bot import SmartTradingBot
        bot = SmartTradingBot()
        
        # Sector mapping for common stocks
        sector_map = {
            'AAPL': 'Technology', 'MSFT': 'Technology', 'GOOGL': 'Technology', 'GOOG': 'Technology',
            'AMZN': 'Consumer', 'META': 'Technology', 'NVDA': 'Technology', 'TSLA': 'Consumer',
            'BRK.B': 'Financial', 'JPM': 'Financial', 'V': 'Financial', 'JNJ': 'Healthcare',
            'WMT': 'Consumer', 'PG': 'Consumer', 'MA': 'Financial', 'UNH': 'Healthcare',
            'HD': 'Consumer', 'DIS': 'Communication', 'PYPL': 'Financial', 'BAC': 'Financial',
            'ADBE': 'Technology', 'CRM': 'Technology', 'NFLX': 'Communication', 'INTC': 'Technology',
            'AMD': 'Technology', 'CSCO': 'Technology', 'PFE': 'Healthcare', 'ABBV': 'Healthcare',
            'T': 'Communication', 'VZ': 'Communication', 'KO': 'Consumer', 'PEP': 'Consumer',
            'COST': 'Consumer', 'NKE': 'Consumer', 'MCD': 'Consumer', 'SBUX': 'Consumer',
            'BA': 'Industrial', 'CAT': 'Industrial', 'GE': 'Industrial', 'MMM': 'Industrial',
            'GS': 'Financial', 'MS': 'Financial', 'C': 'Financial', 'WFC': 'Financial',
            'XOM': 'Energy', 'CVX': 'Energy', 'COP': 'Energy', 'SLB': 'Energy',
            'PLD': 'Real Estate', 'AMT': 'Real Estate', 'CCI': 'Real Estate', 'EQIX': 'Real Estate',
            'LMT': 'Industrial', 'RTX': 'Industrial', 'NOC': 'Industrial', 'UPS': 'Industrial',
            'UNP': 'Industrial', 'HON': 'Industrial', 'LOW': 'Consumer',
            'TGT': 'Consumer', 'TJX': 'Consumer', 'ROST': 'Consumer',
            'AMAT': 'Technology', 'KLAC': 'Technology', 'LRCX': 'Technology', 'MU': 'Technology',
            'SNOW': 'Technology', 'SHOP': 'Technology', 'CRWD': 'Technology', 'NET': 'Technology',
            'DDOG': 'Technology', 'ZS': 'Technology', 'OKTA': 'Technology', 'MDB': 'Technology',
            'PANW': 'Technology', 'FTNT': 'Technology', 'NOW': 'Technology', 'TEAM': 'Technology',
            'ADSK': 'Technology', 'INTU': 'Technology', 'ADP': 'Technology', 'PAYX': 'Technology',
            'ISRG': 'Healthcare', 'MDT': 'Healthcare', 'SYK': 'Healthcare', 'BMY': 'Healthcare',
            'LLY': 'Healthcare', 'GILD': 'Healthcare', 'VRTX': 'Healthcare', 'REGN': 'Healthcare',
            'KKR': 'Financial', 'EXPE': 'Consumer', 'CYPH': 'Healthcare',
        }
        
        result = []
        for p in positions:
            if float(p.qty) > 0:
                symbol = p.symbol
                sector = sector_map.get(symbol, 'Other')
                
                # Calculate score for this position
                score = 50
                rsi = None
                try:
                    df = bot.get_market_data(symbol)
                    if df is not None and len(df) >= bot.sma_slow:
                        df = bot.calculate_indicators(df)
                        latest = df.iloc[-1]
                        
                        if not pd.isna(latest.get(f'SMA_{bot.sma_fast}')) and not pd.isna(latest.get('RSI')):
                            sma_fast = latest[f'SMA_{bot.sma_fast}']
                            sma_slow = latest[f'SMA_{bot.sma_slow}']
                            rsi = latest['RSI']
                            price = latest['close']
                            
                            # RSI Score (with partial credit)
                            rsi_score = 0
                            if rsi < 30:
                                rsi_score = 25 * (1 - rsi / 30)  # Full positive at oversold
                            elif rsi < 50:
                                rsi_score = 12.5 * (1 - (rsi - 30) / 20)  # Partial positive
                            elif rsi < 70:
                                rsi_score = -12.5 * ((rsi - 50) / 20)  # Partial negative
                            else:
                                rsi_score = -25 * min(1, (rsi - 70) / 30)  # Full negative at overbought
                            
                            # SMA Score
                            sma_score = 0
                            if sma_fast > sma_slow:
                                sma_pct = ((sma_fast - sma_slow) / sma_slow) * 100
                                sma_score = min(25, sma_pct * 5)
                            elif sma_fast < sma_slow:
                                sma_pct = ((sma_slow - sma_fast) / sma_slow) * 100
                                sma_score = -min(25, sma_pct * 5)
                            
                            # MACD Score — normalize by price so high-priced stocks don't always max out.
                            macd_hist = latest.get('MACD_histogram', 0)
                            if pd.notna(macd_hist) and price > 0:
                                macd_score = max(-25, min(25, (macd_hist / price) * 5000))
                            else:
                                macd_score = 0
                            
                            # Bollinger Score
                            bb_lower = latest.get('BB_lower')
                            bb_middle = latest.get('BB_middle')
                            bb_upper = latest.get('BB_upper')
                            bb_score = 0
                            if pd.notna(bb_lower) and pd.notna(bb_middle) and price > 0 and pd.notna(bb_upper):
                                bb_position = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
                                bb_score = max(-25, min(25, 25 - (bb_position * 50)))
                            
                            score = 50 + rsi_score + sma_score + macd_score + bb_score
                            score = max(0, min(100, score))
                            
                            # Store individual scores
                            rsi_val = round(rsi_score, 1) if rsi else 0
                            sma_val = round(sma_score, 1)
                            macd_val = round(macd_score, 1)
                            bb_val = round(bb_score, 1)
                except:
                    pass
                
                result.append({
                    "symbol": symbol,
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc),
                    "current_price": float(p.current_price),
                    "sector": sector,
                    "score": round(score, 1),
                    "score_rsi": rsi_val,
                    "score_sma": sma_val,
                    "score_macd": macd_val,
                    "score_bb": bb_val,
                    "rsi": round(rsi, 1) if rsi else None,
                })
        
        # Group by sector
        sectors = {}
        for pos in result:
            sec = pos.get('sector', 'Other')
            if sec not in sectors:
                sectors[sec] = []
            sectors[sec].append(pos)
        
        return {"positions": result, "by_sector": sectors}
        
    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        return {"positions": [], "by_sector": {}}


def get_orders(limit: int = 20) -> List[Dict]:
    """Get recent orders"""
    if not trading_client:
        return []
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from datetime import datetime, timezone
        
        request = GetOrdersRequest(limit=limit)
        orders = trading_client.get_orders(request)
        
        # Filter to last 7 days
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=7)
        
        # Central Time zone
        central = datetime.now().astimezone().tzinfo
        
        result = []
        for o in orders:
            # Skip if created_at is older than 7 days
            if o.created_at and o.created_at.replace(tzinfo=timezone.utc) < cutoff.replace(tzinfo=timezone.utc):
                continue
            # Convert created_at to Central Time
            created_dt = o.created_at.replace(tzinfo=timezone.utc).astimezone(central) if o.created_at else None
            
            # Calculate total cost - use filled_avg_price if filled, otherwise stop_price or limit_price
            if o.filled_avg_price:
                price = float(o.filled_avg_price)
            elif o.limit_price:
                price = float(o.limit_price)
            elif o.stop_price:
                price = float(o.stop_price)
            else:
                price = 0
            total = price * float(o.qty)
            
            result.append({
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "filled_qty": float(o.filled_qty or 0),
                "price": round(price, 2),
                "total": round(total, 2),
                "status": o.status.value,
                "created_at": o.created_at,
                "date": created_dt.strftime("%Y-%m-%d %H:%M") if created_dt else None,
                "filled_at": o.filled_at,
            })
        return result
    except Exception as e:
        logger.error(f"Failed to get orders: {e}")
        return []


def get_trades_from_db(limit: int = 20) -> List[Dict]:
    """Get trades from database"""
    return db.get_all_trades(limit)


def get_recent_sessions(limit: int = 5) -> List[Dict]:
    """Get recent trading sessions"""
    return db.get_sessions(limit)


# Routes
@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Main dashboard page"""
    try:
        account = get_account_info()
        positions = get_positions()
        orders = get_orders(100)  # Get more orders to cover last week
        db_trades = get_trades_from_db(10)
        sessions = get_recent_sessions(5)
        trading_status = get_trading_status()
        
        # Get positions (returns dict with 'positions' and 'by_sector')
        positions_data = get_positions()
        positions = positions_data.get("positions", [])
        positions_by_sector = positions_data.get("by_sector", {})
        
        # Calculate totals
        total_position_value = sum(p["market_value"] for p in positions)
        total_unrealized_pl = sum(p["unrealized_pl"] for p in positions)
        
        template = jinja_env.get_template("dashboard.html")
        return template.render(
            account=account,
            trading_status=trading_status,
            positions=positions,
            positions_by_sector=positions_by_sector,
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


@app.get("/api/opportunities")
def api_opportunities(limit: int = 30):
    """Get top stock opportunities from SQLite database"""
    import sqlite3
    
    try:
        db_path = Path(__file__).parent / "trading_bot.db"
        
        if not db_path.exists():
            return {"opportunities": [], "error": "Database not found", "analyzed": 0}
        
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT symbol, price, total_score, rsi, rsi_score, sma_score, 
                   macd_score, bb_score, regime_score, catalyst_score, 
                   buy_criteria, passes_all_buy_criteria, last_analyzed
            FROM analyzed_stocks
            ORDER BY last_analyzed DESC, total_score DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        opportunities = []
        for row in rows:
            score = row['total_score'] or 0
            
            if score >= 65:
                signal = 'BUY'
                strength = 'STRONG' if score >= 80 else 'MEDIUM'
            elif score <= 35:
                signal = 'SELL'
                strength = 'STRONG' if score <= 20 else 'MEDIUM'
            else:
                signal = 'HOLD'
                strength = 'WEAK'
            
            # Parse buy_criteria from JSON string
            buy_criteria = []
            passes_all = False
            try:
                if row['buy_criteria']:
                    bc = row['buy_criteria']
                    buy_criteria = json.loads(bc)
                passes_all = bool(row['passes_all_buy_criteria'])
            except Exception as e:
                logger.debug(f"Error parsing buy_criteria for {row['symbol']}: {e}")
            
            failed_criteria = [c['name'] for c in buy_criteria if not c['passed']]
            
            opportunities.append({
                'symbol': row['symbol'],
                'price': row['price'],
                'signal': signal,
                'signal_strength': strength,
                'total_score': score,
                'rsi': row['rsi'],
                'rsi_score': row['rsi_score'],
                'sma_score': row['sma_score'],
                'macd_score': row['macd_score'],
                'bb_score': row['bb_score'],
                'regime_score': row['regime_score'],
                'catalyst_score': row['catalyst_score'],
                'buy_criteria': buy_criteria,
                'passes_all_buy_criteria': passes_all,
                'failed_criteria': failed_criteria,
                'analyzed_at': row['last_analyzed'],
            })
        
        return {"opportunities": opportunities, "analyzed": len(opportunities)}
        
    except Exception as e:
        logger.error(f"Failed to get opportunities: {e}")
        return {"opportunities": [], "error": str(e), "analyzed": 0}




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



@app.get("/api/analysis")
def api_analysis():
    """Get recent analyses from logs"""
    try:
        from pathlib import Path
        import re
        import subprocess
        
        log_path = Path(__file__).parent / "trading_bot.log"
        
        if not log_path.exists():
            return {"error": "Log file not found"}
        
        # Read last 500 lines
        result = subprocess.run(
            ["tail", "-500", str(log_path)],
            capture_output=True, text=True
        )
        
        lines = result.stdout.split("\n")
        
        # Extract score lines
        analyses = []
        for line in lines:
            if "Score:" in line and "RSI:" in line:
                match = re.search(r'📊\s+(\w+):\s+\$?([\d.]+).*?RSI:(\d+).*?Score:(\d+)', line)
                if match:
                    analyses.append({
                        'symbol': match.group(1),
                        'price': float(match.group(2)),
                        'rsi': int(match.group(3)),
                        'total_score': int(match.group(4)),
                    })
        
        # Get unique symbols, keep last 50
        seen = {}
        for a in analyses:
            seen[a['symbol']] = a
        unique = list(seen.values())[:50]
        
        return {
            'analyzed_today': len(unique),
            'recent_analyses': unique
        }
        
    except Exception as e:
        return {"error": str(e)}

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
                sessions = db.get_sessions(limit=100)
                matched = [s for s in sessions if str(s.get('id')) == str(session_id)]
                if matched:
                    session = matched[0]
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
        
        # Get unrealized P&L from open positions
        positions_data = get_positions()
        positions = positions_data.get("positions", []) if isinstance(positions_data, dict) else []
        total_unrealized_pl = sum(p.get("unrealized_pl", 0) for p in positions)
        
        # Helper to safely get pnl (handle None)
        def safe_pnl(t):
            pnl = t.get('pnl')
            return pnl if pnl is not None else 0
        
        # Basic stats from closed trades
        total_trades = len(trades)
        closed_pnl = sum(safe_pnl(t) for t in trades)
        winners = sum(1 for t in trades if safe_pnl(t) > 0)
        losers = sum(1 for t in trades if safe_pnl(t) < 0)
        win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
        
        # Total P&L = closed trades + unrealized from open positions
        total_pnl = closed_pnl + total_unrealized_pl
        avg_pnl = closed_pnl / total_trades if total_trades > 0 else 0
        
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
            "closed_trades": total_trades,
            "winners": winners,
            "losers": losers,
            "win_rate": round(win_rate, 1),
            "closed_pnl": round(closed_pnl, 2),
            "unrealized_pnl": round(total_unrealized_pl, 2),
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




@app.get("/api/score/{symbol}")
def api_score_breakdown(symbol: str):
    """Get full score breakdown for a symbol"""
    import sys
    from pathlib import Path
    import pandas as pd
    
    src_path = str(Path(__file__).parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    
    try:
        from core.smart_bot import SmartTradingBot
        
        bot = SmartTradingBot()
        
        # Get market data
        df = bot.get_market_data(symbol)
        if df is None or len(df) < bot.sma_slow:
            return {"error": "Insufficient data"}
        
        df = bot.calculate_indicators(df)
        latest = df.iloc[-1]
        
        if pd.isna(latest.get(f'SMA_{bot.sma_fast}')) or pd.isna(latest.get('RSI')):
            return {"error": "Insufficient indicator data"}
        
        price = latest['close']
        rsi = latest['RSI']
        sma_fast = latest[f'SMA_{bot.sma_fast}']
        sma_slow = latest[f'SMA_{bot.sma_slow}']
        macd_hist = latest.get('MACD_histogram', 0)
        atr = latest.get('ATR', 0)
        
        # Calculate scores
        rsi_score = 0
        if rsi < 30:
            rsi_score = 25 * (1 - rsi / 30)
        elif rsi < 50:
            rsi_score = 12.5 * (1 - (rsi - 30) / 20)
        elif rsi < 70:
            rsi_score = -12.5 * ((rsi - 50) / 20)
        else:
            rsi_score = -25 * min(1, (rsi - 70) / 30)
        
        sma_score = 0
        sma_pct = 0
        if sma_fast > sma_slow:
            sma_pct = ((sma_fast - sma_slow) / sma_slow) * 100
            sma_score = min(25, sma_pct * 5)
        elif sma_fast < sma_slow:
            sma_pct = ((sma_slow - sma_fast) / sma_slow) * 100
            sma_score = -min(25, sma_pct * 5)
        
        if pd.notna(macd_hist) and price > 0:
            macd_score = max(-25, min(25, (macd_hist / price) * 5000))
        else:
            macd_score = 0

        bb_lower = latest.get('BB_lower')
        bb_middle = latest.get('BB_middle')
        bb_upper = latest.get('BB_upper')
        bb_score = 0
        bb_position = 50
        if pd.notna(bb_lower) and pd.notna(bb_middle) and price > 0 and pd.notna(bb_upper):
            if (bb_upper - bb_lower) > 0:
                bb_position = ((price - bb_lower) / (bb_upper - bb_lower)) * 100
            bb_score = 25 - (bb_position / 2)
        
        catalyst_data = bot.scan_catalysts(symbol)
        catalyst_score = catalyst_data.get('catalyst_score', 0)
        
        total_score = 50 + rsi_score + sma_score + macd_score + bb_score + catalyst_score
        total_score = max(0, min(100, total_score))
        
        signal = 'HOLD'
        if total_score >= 65:
            signal = 'BUY'
        elif total_score <= 35:
            signal = 'SELL'
        
        # Additional checks
        has_earnings, earnings_date, days_until = bot.check_earnings_calendar(symbol, bot.earnings_days_skip)
        
        atr_pct = (atr / price * 100) if price > 0 and pd.notna(atr) else 0
        
        try:
            regime = bot.get_current_market_regime()
            regime_info = {'regime': regime.get('regime'), 'adx': round(regime.get('adx', 0), 1)}
        except:
            regime_info = {'regime': 'Unknown', 'adx': 0}
        
        return {
            'symbol': symbol,
            'price': round(price, 2),
            'total_score': round(total_score, 1),
            'signal': signal,
            'breakdown': {
                'rsi': {'value': round(rsi, 1), 'score': round(rsi_score, 1), 'max': 25},
                'sma': {'fast': round(sma_fast, 2), 'slow': round(sma_slow, 2), 'separation': round(sma_pct, 2), 'score': round(sma_score, 1), 'max': 25},
                'macd': {'histogram': round(macd_hist, 2) if pd.notna(macd_hist) else 0, 'score': round(macd_score, 1), 'max': 25},
                'bollinger': {'position': round(bb_position, 1), 'score': round(bb_score, 1), 'max': 25},
                'catalyst': {'score': catalyst_score, 'max': 25, 'catalysts': catalyst_data.get('catalysts', [])},
                'earnings': {'has_earnings': has_earnings, 'days_until': days_until},
                'volatility': {'atr_pct': round(atr_pct, 2), 'tier': 'low' if atr_pct < 2 else ('high' if atr_pct > 5 else 'mid')},
                'regime': regime_info
            },
            'scores': {
                'rsi': round(rsi_score, 1),
                'sma': round(sma_score, 1),
                'macd': round(macd_score, 1),
                'bollinger': round(bb_score, 1),
                'catalyst': catalyst_score
            }
        }
        
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/db-status")
def api_db_status():
    """Return SQLite database health — file info and per-table row counts."""
    import os as _os
    from database.sqlite_db import DB_PATH, _get_conn

    result = {
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size_bytes": 0,
        "db_size_human": "0 B",
        "tables": {},
        "status": "missing",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if DB_PATH.exists():
        size = DB_PATH.stat().st_size
        result["db_size_bytes"] = size
        if size >= 1_048_576:
            result["db_size_human"] = f"{size / 1_048_576:.1f} MB"
        elif size >= 1024:
            result["db_size_human"] = f"{size / 1024:.1f} KB"
        else:
            result["db_size_human"] = f"{size} B"

        table_queries = {
            "analyzed_stocks":      "SELECT COUNT(*), MAX(last_analyzed)      FROM analyzed_stocks",
            "trades":               "SELECT COUNT(*), MAX(created_at)         FROM trades",
            "trading_sessions":     "SELECT COUNT(*), MAX(session_start)      FROM trading_sessions",
            "research_cooldowns":   "SELECT COUNT(*), MAX(updated_at)         FROM research_cooldowns",
            "trade_cooldowns":      "SELECT COUNT(*), MAX(updated_at)         FROM trade_cooldowns",
        }
        total_rows = 0
        try:
            with _get_conn() as conn:
                for table, query in table_queries.items():
                    try:
                        row = conn.execute(query).fetchone()
                        count = row[0] if row else 0
                        last  = row[1] if row else None
                        result["tables"][table] = {"count": count, "last_updated": last}
                        total_rows += count
                    except Exception:
                        result["tables"][table] = {"count": 0, "last_updated": None}
            result["status"] = "ok" if total_rows > 0 else "empty"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

    return result


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
