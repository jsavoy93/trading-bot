"""
Trading Bot Dashboard - Web Interface
Mobile-friendly dashboard to monitor and control your trading bot.
"""
import json
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
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

# Settings persistence (SQLite) — shared with the trading bot
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
try:
    from core.settings_service import (
        STRATEGY_SETTINGS_SCHEMA,
        dashboard_parameters,
        save_typed as _ss_save_typed,
        validate_typed as _ss_validate_typed,
    )
    _USE_SETTINGS_SERVICE = True
except Exception:
    STRATEGY_SETTINGS_SCHEMA = {}
    dashboard_parameters = lambda: {}
    _USE_SETTINGS_SERVICE = False
    _ss_save_typed = lambda k, v: None
    _ss_validate_typed = lambda k, v: v

# Alpaca client
api_key = os.getenv("ALPACA_API_KEY")
api_secret = os.getenv("ALPACA_API_SECRET")
base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

trading_client = None
if api_key and api_secret:
    trading_client = TradingClient(api_key, api_secret, paper=True)

# Cached smart bot instance for analysis status
_smart_bot_instance = None

# Trading parameters — editable via dashboard and derived from the shared schema
_TRADING_PARAMS = dashboard_parameters()


def _refresh_trading_params():
    """Refresh dashboard metadata from persisted effective strategy settings."""
    global _TRADING_PARAMS
    if _USE_SETTINGS_SERVICE:
        _TRADING_PARAMS = dashboard_parameters()
    return _TRADING_PARAMS


def _update_cached_trading_param(key: str, value):
    if key in _TRADING_PARAMS:
        _TRADING_PARAMS[key]["value"] = value


def _coerce_dashboard_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _normalize_dashboard_value(key: str, value):
    """Normalize one dashboard setting without persisting it."""
    definition = STRATEGY_SETTINGS_SCHEMA[key]
    if _USE_SETTINGS_SERVICE:
        return _ss_validate_typed(key, value)
    if definition.param_type == "bool":
        return _coerce_dashboard_bool(value)
    if definition.param_type == "int":
        return int(float(value))
    if definition.param_type == "float":
        return float(value)
    return str(value)


def _validate_dashboard_settings_batch(updates: Dict):
    """Validate all known submitted settings before any setting is persisted."""
    normalized = {}
    for key, value in updates.items():
        if key not in _TRADING_PARAMS:
            continue
        normalized[key] = _normalize_dashboard_value(key, value)
    return normalized


def _persist_dashboard_settings_batch(normalized: Dict):
    """Persist a fully validated batch and refresh local dashboard metadata."""
    if _USE_SETTINGS_SERVICE:
        for key, value in normalized.items():
            _ss_save_typed(key, value)
        _refresh_trading_params()
    for key, value in normalized.items():
        _update_cached_trading_param(key, value)

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


def is_smartbot_runner_active() -> bool:
    """Return True if the smartbot-runner.service is currently active on this host.

    Detection is by checking the systemd user-level unit. We do NOT check
    for the SmartTradingBot Python process directly because the runner may
    have crashed silently, leaving the process gone but the unit not
    noticing yet; systemd's view of "active" is the right authority here.

    This function deliberately does NOT spawn a bot. It only reports.
    Returns False if systemctl is unavailable, the unit is unknown, or the
    unit is not active. Caches the result for 2 seconds to avoid hammering
    systemd on every dashboard poll.
    """
    import subprocess
    import time
    cache_key = "_smartbot_runner_active_cache"
    cache_ts_key = "_smartbot_runner_active_cache_ts"
    cache = globals()
    now = time.time()
    cached_ts = cache.get(cache_ts_key)
    if cached_ts is not None and (now - cached_ts) < 2.0:
        return cache.get(cache_key, False)
    result = False
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", "smartbot-runner.service"],
            capture_output=True, text=True, timeout=2,
        )
        result = out.stdout.strip() == "active"
    except Exception:
        result = False
    cache[cache_key] = result
    cache[cache_ts_key] = now
    return result


def get_runtime_status() -> Dict:
    """Three-tier runtime status for the dashboard SPA.

    Returns a dict with three independent booleans so the UI can present
    each level honestly without conflating them:

      alpaca_api_reachable: True iff /api/account returned a populated dict.
        This is what the legacy green/red dot encoded.

      smartbot_runner_active: True iff the smartbot-runner.service systemd
        unit is currently active. False means the bot is NOT running,
        even if Alpaca is reachable.

      active_session_id: integer session id of the current ACTIVE row in
        trading_sessions that belongs to the running runner, or None if
        none is active.

    These three are independent. Today (BOT-001/BOT-002), all three
    should normally be True in production when the SmartBot runner is
    enabled and operational.

    BOT-003 deterministic active-session selection: when the runner is
    active, the active session is selected via process-start-time
    linkage to the runner PID (read from /tmp/trading_bot.lock + read
    /proc/<pid>/stat). This excludes stale/fixture rows whose
    session_start is before the current runner's process start time,
    regardless of whether the row's timestamp is in the past, the
    future, or otherwise skewed. When the runner is NOT active, the
    function falls back to the most recently created ACTIVE row (id
    DESC) for backward compatibility.
    """
    # Tier 1: Alpaca API reachable
    account = get_account_info()
    alpaca_ok = bool(account) and "error" not in account and "portfolio_value" in account

    # Tier 2: smartbot-runner.service active
    runner_active = is_smartbot_runner_active()

    # Tier 3: active session in DB
    active_session_id = None
    active_session_start = None
    active_session_source = None  # "runner-pid" or "id-desc" (diagnostic)

    try:
        active = None
        if runner_active:
            # BOT-003: when the runner is active, prefer the
            # process-start-time-filtered selection. Read the runner
            # PID from /tmp/trading_bot.lock and ask the DB to filter
            # ACTIVE rows by the runner's process start time. This
            # deterministically excludes stale/fixture rows.
            try:
                runner_pid = None
                lock_path = "/tmp/trading_bot.lock"
                if os.path.exists(lock_path):
                    with open(lock_path, "r") as f:
                        pid_text = f.read().strip()
                    if pid_text.isdigit():
                        runner_pid = int(pid_text)
                if runner_pid:
                    active = simple_rest.get_active_session_for_runner(runner_pid)
                    if active:
                        active_session_source = "runner-pid"
            except Exception as e:
                logger.debug(
                    f"get_runtime_status: runner-pid lookup failed: {e}"
                )
        if not active:
            # Fallback: most recently created ACTIVE row by id DESC.
            # Still immune to clock-skewed fixtures because id is
            # auto-increment. This handles the case where the runner
            # is not active (so we have no PID to filter by), or the
            # runner-pid lookup failed.
            active = simple_rest.get_active_session()
            if active:
                active_session_source = "id-desc"
        if active:
            active_session_id = active.get("id")
            active_session_start = active.get("session_start")
    except Exception as e:
        logger.debug(f"get_runtime_status: get_active_session failed: {e}")

    # Summary booleans for backward-compatible SPA rendering.
    return {
        "alpaca_api_reachable": alpaca_ok,
        "smartbot_runner_active": runner_active,
        "active_session_id": active_session_id,
        "active_session_start": active_session_start,
        "active_session_source": active_session_source,
        # Convenience: fully_ready means everything is green AND an active
        # session is running. This is the strongest signal the bot is
        # currently doing work. Used by the dot legend.
        "fully_ready": alpaca_ok and runner_active and active_session_id is not None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


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
                rsi_val = sma_val = macd_val = bb_val = 0  # defaults in case exception fires
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


# Dashboard read-model fidelity: filter future-dated test fixtures out of the
# Recent Sessions view and source Symbols/Trades from OBS-001 ground truth.
# Mirrors the BOT-003 skew tolerance (CLOCK_SKEW_SECONDS=300 in sqlite_db.py).
DASHBOARD_RECENT_SESSIONS_MAX_FUTURE_SKEW_SECONDS = 300


def _parse_iso8601_utc(value: str):
    """Parse an ISO-8601 timestamp string into an aware UTC datetime.

    Accepts the formats actually written by the bot and tests:
    trailing 'Z', explicit '+00:00', or naive strings (treated as UTC).
    Raises ValueError on malformed input so callers can skip the row.
    """
    if not value or not isinstance(value, str):
        raise ValueError("empty timestamp")
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _aggregate_session_counts(session_ids):
    """Aggregate decision_history symbols and trades counts per session.

    Returns (symbols_by_session, trades_by_session) dicts. Returns empty
    dicts on any error; callers must treat missing keys as zero.

    Uses the same DB_PATH the rest of the dashboard reads from, so the
    helper stays consistent with `db.get_sessions()` and respects any
    test-time monkeypatching of DB_PATH.
    """
    sym_by_session = {}
    trd_by_session = {}
    if not session_ids:
        return sym_by_session, trd_by_session

    try:
        import sqlite3 as _sqlite3
        import database.sqlite_db as _sqlite_mod  # dashboard-side module
        db_path = _sqlite_mod.DB_PATH
        if not db_path or not Path(str(db_path)).exists():
            return sym_by_session, trd_by_session
        placeholders = ",".join("?" for _ in session_ids)
        conn = _sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT session_id, COUNT(DISTINCT symbol)
                FROM decision_history
                WHERE session_id IN ({placeholders})
                GROUP BY session_id
                """,
                session_ids,
            )
            for sid, n in cur.fetchall():
                sym_by_session[int(sid)] = int(n)
            cur.execute(
                f"""
                SELECT session_id, COUNT(*)
                FROM trades
                WHERE session_id IN ({placeholders})
                GROUP BY session_id
                """,
                session_ids,
            )
            for sid, n in cur.fetchall():
                trd_by_session[int(sid)] = int(n)
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            f"_aggregate_session_counts failed (returning empty dicts): {e}"
        )
    return sym_by_session, trd_by_session


def get_recent_sessions_with_truthful_counts(limit: int = 5) -> List[Dict]:
    """Recent trading sessions with truthful Symbols and Trades counts.

    Symbols semantic:
        - If decision_history has any rows for the session:
            Symbols = COUNT(DISTINCT decision_history.symbol)
            (repeated analysis of the same symbol across multiple cycles
            counts once; this is the authoritative OBS-001 value).
        - Else (legacy session with no OBS-001 decision rows):
            Symbols = trading_sessions.total_symbols_processed.

    Trades semantic:
        - Trades = COUNT(trades.id) WHERE trades.session_id = X.
        - The legacy trading_sessions.total_trades_executed scalar is NOT used.

    Filter (read-layer only; no row is mutated or deleted):
        - Exclude rows where parsed session_start > now(UTC) + 300 seconds.
        - The BOT-003 skew tolerance is the same 5-minute future bound used
          to keep implausibly future-dated test fixtures (e.g. session 71804
          with session_start='2099-01-01') out of the active-session and
          recent-sessions selectors. No session row is deleted or hidden at
          the DB level by this helper.

    Sort:
        - ORDER BY session_start DESC, id DESC (deterministic tiebreak).

    Shape:
        - Returns the same per-row dict as db.get_sessions(), augmented
          with `symbols_count` (int), `symbols_source` ("decision_history"
          or "legacy_scalar"), and `trades_count` (int). The legacy
          `total_symbols_processed` and `total_trades_executed` fields are
          preserved on the dict for any consumer that still reads them.
    """
    if limit <= 0:
        return []

    # Over-fetch to absorb the future-skew filter. Bounded so a pathological
    # table cannot force a full scan.
    raw = db.get_sessions(limit=max(limit * 4, 50))
    if not raw:
        return []

    now = datetime.now(timezone.utc)
    upper_bound = now + timedelta(
        seconds=DASHBOARD_RECENT_SESSIONS_MAX_FUTURE_SKEW_SECONDS
    )

    parsed = []
    for s in raw:
        ss_raw = s.get("session_start")
        if not ss_raw:
            continue
        try:
            ss_dt = _parse_iso8601_utc(ss_raw)
        except Exception:
            # Malformed timestamp: skip rather than render an ambiguous row.
            continue
        if ss_dt > upper_bound:
            continue
        parsed.append((ss_dt, s))

    # Deterministic sort: session_start DESC, id DESC.
    parsed.sort(
        key=lambda pair: (pair[0], int(pair[1].get("id") or 0)),
        reverse=True,
    )
    chosen = [s for _, s in parsed[:limit]]
    if not chosen:
        return []

    session_ids = [
        int(s["id"]) for s in chosen if s.get("id") is not None
    ]
    sym_by_session, trd_by_session = _aggregate_session_counts(session_ids)

    enriched = []
    for s in chosen:
        sid = s.get("id")
        sid_int = int(sid) if sid is not None else None
        if sid_int is not None and sid_int in sym_by_session:
            s["symbols_count"] = int(sym_by_session[sid_int])
            s["symbols_source"] = "decision_history"
        else:
            s["symbols_count"] = int(s.get("total_symbols_processed") or 0)
            s["symbols_source"] = "legacy_scalar"
        s["trades_count"] = int(trd_by_session.get(sid_int, 0)) if sid_int is not None else 0
        enriched.append(s)
    return enriched


def get_recent_sessions(limit: int = 5) -> List[Dict]:
    """Get recent trading sessions.

    Backward-compatible wrapper. The History → Recent Sessions panel and
    /api/sessions now use get_recent_sessions_with_truthful_counts(), which
    applies the BOT-003 future-skew filter and sources Symbols/Trades from
    OBS-001 ground truth. This wrapper is preserved for any external
    consumer that wants the raw, unfiltered row list.
    """
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
        sessions = get_recent_sessions_with_truthful_counts(5)
        trading_status = get_trading_status()
        runtime_status = get_runtime_status()

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
            runtime_status=runtime_status,
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
    """Get top stock opportunities from SQLite database.

    OBS-001 Phase A: read decision_snapshot when present and use the
    bot's stored signal/signal_strength rather than recomputing them
    from total_score. For legacy rows (decision_snapshot IS NULL) the
    dashboard reads the persisted `signal` and `signal_strength`
    columns from analyzed_stocks directly; it MUST NOT re-derive them
    from total_score thresholds because doing so would rewrite the
    meaning of an old analysis under new thresholds.
    """
    import sqlite3

    try:
        db_path = Path(__file__).parent / "trading_bot.db"

        if not db_path.exists():
            return {"opportunities": [], "error": "Database not found", "analyzed": 0}

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT symbol, price, total_score, signal, signal_strength,
                   rsi, rsi_score, sma_score,
                   macd_score, bb_score, regime_score, catalyst_score,
                   buy_criteria, passes_all_buy_criteria,
                   decision_snapshot, decision_schema_version,
                   last_analyzed
            FROM analyzed_stocks
            ORDER BY total_score DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        opportunities = []
        for row in rows:
            score = row['total_score'] or 0

            # Parse decision_snapshot if present (OBS-001 Phase A row)
            snapshot = None
            schema_version = row['decision_schema_version'] or 0
            if row['decision_snapshot']:
                try:
                    snapshot = json.loads(row['decision_snapshot'])
                except Exception as e:
                    logger.debug(f"Error parsing decision_snapshot for {row['symbol']}: {e}")

            if snapshot is not None and schema_version >= 1:
                # OBS-001 Phase A row: read the bot's actual stored decision.
                signal = snapshot.get('strategy_eligibility', {}).get('signal') \
                    or row['signal'] or 'HOLD'
                strength = snapshot.get('strategy_eligibility', {}).get('signal_strength') \
                    or row['signal_strength'] or 'WEAK'
                decision_outcome = snapshot.get('decision', {}).get('outcome')
                decision_primary_reason = snapshot.get('decision', {}).get('primary_reason')
                ranking_block = snapshot.get('ranking', {}) or {}
                execution_block = snapshot.get('execution_checks', {}) or {}
                order_block = snapshot.get('order', {}) or {}
            else:
                # Legacy row (decision_snapshot IS NULL): use the
                # persisted `signal` and `signal_strength` columns
                # directly. NEVER re-derive from total_score thresholds:
                # doing so would rewrite the meaning of an old
                # analysis under a current threshold regime.
                signal = row['signal'] or 'HOLD'
                strength = row['signal_strength'] or 'WEAK'
                decision_outcome = None
                decision_primary_reason = None
                ranking_block = {}
                execution_block = {}
                order_block = {}
                # Defensive: if signal is NULL for any reason (e.g.
                # extremely old rows), keep it as HOLD rather than
                # re-deriving. The legacy pathway is "use what the
                # bot wrote at analysis time, not what current code
                # thinks the score implies".

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

            # SCORE-002: rank entries (kind='rank', passed=None) are not
            # gates and must not appear in failed_criteria. Old historical
            # rows without `kind` keep the prior behavior naturally.
            failed_criteria = [
                c['name'] for c in buy_criteria
                if c.get('passed') is False
                and c.get('kind') != 'rank'
            ]

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
                # OBS-001 Phase A fields (None for legacy rows)
                'decision_schema_version': schema_version,
                'decision_outcome': decision_outcome,
                'decision_primary_reason': decision_primary_reason,
                'rank': ranking_block.get('candidate_rank'),
                'eligible_candidate_count': ranking_block.get('eligible_candidate_count'),
                'fill_confirmed': order_block.get('fill_confirmed', False) if order_block else False,
            })

        return {"opportunities": opportunities, "analyzed": len(opportunities)}

    except Exception as e:
        logger.error(f"Failed to get opportunities: {e}")
        return {"opportunities": [], "error": str(e), "analyzed": 0}


@app.get("/api/decision/{symbol}")
def api_decision(symbol: str):
    """OBS-001 Phase A: full decision_snapshot for one symbol.

    Returns the latest decision_snapshot from analyzed_stocks.
    Legacy rows (decision_snapshot IS NULL) return a sentinel.
    """
    import sqlite3
    from pathlib import Path as _P
    db_path = _P(__file__).parent / "trading_bot.db"
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT decision_snapshot, decision_schema_version, signal,
                   signal_strength, total_score, last_analyzed
            FROM analyzed_stocks WHERE symbol = ?
        """, (symbol.upper(),))
        row = cur.fetchone()
        conn.close()
        if not row:
            return {"error": f"Symbol {symbol} not found"}
        if not row['decision_snapshot']:
            return {
                "symbol": symbol.upper(),
                "decision_schema_version": 0,
                "is_legacy": True,
                "legacy_message": "Legacy analysis — detailed decision trace unavailable",
                "signal": row['signal'],
                "signal_strength": row['signal_strength'],
                "total_score": row['total_score'],
                "analyzed_at": row['last_analyzed'],
            }
        snapshot = json.loads(row['decision_snapshot'])
        return {
            "symbol": symbol.upper(),
            "decision_schema_version": row['decision_schema_version'],
            "is_legacy": False,
            "snapshot": snapshot,
            "analyzed_at": row['last_analyzed'],
        }
    except Exception as e:
        logger.error(f"api_decision failed for {symbol}: {e}")
        return {"error": str(e)}


@app.get("/api/decision-history/{symbol}")
def api_decision_history(symbol: str, limit: int = 50):
    """OBS-001 Phase A: cycle-over-cycle decision_history rows for symbol."""
    import sqlite3
    from pathlib import Path as _P
    db_path = _P(__file__).parent / "trading_bot.db"
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT cycle_id, cycle_start, session_id, decision_schema_version,
                   decision_snapshot
            FROM decision_history
            WHERE symbol = ?
            ORDER BY cycle_start DESC
            LIMIT ?
        """, (symbol.upper(), limit))
        rows = cur.fetchall()
        conn.close()
        out = []
        for r in rows:
            out.append({
                "cycle_id": r['cycle_id'],
                "cycle_start": r['cycle_start'],
                "session_id": r['session_id'],
                "decision_schema_version": r['decision_schema_version'],
                "snapshot": json.loads(r['decision_snapshot']) if r['decision_snapshot'] else None,
            })
        return {"symbol": symbol.upper(), "history": out, "count": len(out)}
    except Exception as e:
        logger.error(f"api_decision_history failed for {symbol}: {e}")
        return {"error": str(e)}


@app.get("/api/actionability-summary")
def api_actionability_summary():
    """OBS-001 Phase A: cycle funnel from the latest cycle_funnel row."""
    import sqlite3
    from pathlib import Path as _P
    db_path = _P(__file__).parent / "trading_bot.db"
    if not db_path.exists():
        return {"error": "Database not found"}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM cycle_funnel
            ORDER BY cycle_start DESC LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        if not row:
            return {"error": "No cycle_funnel rows yet", "funnel": None}
        d = dict(row)
        return {
            "funnel": {
                "cycle_id": d['cycle_id'],
                "session_id": d['session_id'],
                "cycle_start": d['cycle_start'],
                "cycle_end": d['cycle_end'],
                "analyzed_count": d['analyzed_count'],
                "strategy_eligible_count": d['strategy_eligible_count'],
                "ranked_candidate_count": d['ranked_candidate_count'],
                "execution_attempt_count": d['execution_attempt_count'],
                "execution_blocked_count": d['execution_blocked_count'],
                "order_submission_attempt_count": d['order_submission_attempt_count'],
                "order_submitted_count": d['order_submitted_count'],
                "order_failed_count": d['order_failed_count'],
                "not_attempted_count": d['not_attempted_count'],
                "not_attempted_reason": d['not_attempted_reason'],
                "bot_version": d['bot_version'],
                "schema_version": d['schema_version'],
            }
        }
    except Exception as e:
        logger.error(f"api_actionability_summary failed: {e}")
        return {"error": str(e)}


@app.get("/api/cycle-candidates/{cycle_id}")
def api_cycle_candidates(cycle_id: str, near_miss_limit: int = 3):
    """OBS-001 Phase B: persistent candidate + near-miss facts for one cycle.

    Reads from `decision_history` (immutable, append-only) — never writes.
    The renderer, never the bot, derives the truth from these rows.

    Two sets, intentionally separated:

    - candidates: rows where the persisted snapshot's
      ranking.candidate_rank is non-null. These are the symbols that
      SCORE-002 actually ranked in this cycle. Ordered by
      ranking.candidate_rank ASC (rank-1 first), NOT by total_score DESC.

    - near_misses: rows where the persisted snapshot's
      decision.outcome == 'HOLD_INELIGIBLE' AND ranking.candidate_rank
      IS NULL. The IS NULL guard ensures candidates and near misses
      never overlap. Ordered by scoring.total_score DESC, capped at
      `near_miss_limit` (default 3, clamped to 1-10). These are NOT
      candidates — Phase B explicitly labels them so.

    The response also echoes the canonical cycle_funnel counters when
    that row exists, so the Dashboard Latest Cycle card and Top
    Candidates card can be rendered from one fetch (plus the funnel
    endpoint for the card itself; this endpoint only carries per-symbol
    rows). `cycle_known` distinguishes "no such cycle" from "cycle
    exists but produced zero rows of this kind".

    The candidate_rank gate is critical: under SCORE-002 a symbol with
    total_score above the OLD `min_score_buy` threshold is NOT a
    candidate unless it was actually ranked by the bot. This endpoint
    enforces that distinction at the data-source boundary so the UI
    cannot accidentally promote a high-score HOLD to a candidate.
    """
    import sqlite3
    from pathlib import Path as _P
    # Clamp near_miss_limit defensively (1-10).
    if near_miss_limit < 1: near_miss_limit = 1
    if near_miss_limit > 10: near_miss_limit = 10
    db_path = _P(__file__).parent / "trading_bot.db"
    if not db_path.exists():
        return {"error": "Database not found", "cycle_known": False, "candidates": [], "near_misses": []}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Confirm the cycle exists in cycle_funnel (so the UI knows
        # which funnel row to associate with this candidate list).
        cf_row = cur.execute(
            "SELECT cycle_id, session_id, cycle_start, cycle_end, "
            "analyzed_count, strategy_eligible_count, ranked_candidate_count "
            "FROM cycle_funnel WHERE cycle_id = ? LIMIT 1",
            (cycle_id,),
        ).fetchone()
        cycle_known = cf_row is not None

        # Candidates: ranking.candidate_rank populated. Order by
        # candidate_rank ASC (rank 1 first), with total_score as a
        # secondary tie-break for stability across renderer versions.
        candidate_rows = cur.execute(
            """
            SELECT id, symbol, cycle_start, session_id, decision_snapshot
            FROM decision_history
            WHERE cycle_id = ?
              AND json_extract(decision_snapshot, '$.ranking.candidate_rank') IS NOT NULL
            ORDER BY CAST(json_extract(decision_snapshot, '$.ranking.candidate_rank') AS INTEGER) ASC,
                     CAST(json_extract(decision_snapshot, '$.scoring.total_score') AS REAL) DESC
            """,
            (cycle_id,),
        ).fetchall()

        candidates = []
        for row in candidate_rows:
            try:
                snap = json.loads(row['decision_snapshot']) if row['decision_snapshot'] else {}
            except Exception as e:
                logger.debug(f"decision_snapshot parse failed for {row['symbol']}: {e}")
                snap = {}
            ranking = snap.get('ranking') or {}
            decision = snap.get('decision') or {}
            scoring = snap.get('scoring') or {}
            candidates.append({
                "symbol": row['symbol'],
                "candidate_rank": ranking.get('candidate_rank'),
                "eligible_candidate_count": ranking.get('eligible_candidate_count'),
                "ranking_tiebreak_basis": ranking.get('tiebreak_basis'),
                "total_score": scoring.get('total_score'),
                "decision_outcome": decision.get('outcome'),
                "decision_primary_reason": decision.get('primary_reason'),
                "cycle_start": row['cycle_start'],
                "session_id": row['session_id'],
                "decision_history_id": row['id'],
            })

        # Near misses: outcome = HOLD_INELIGIBLE and candidate_rank is
        # NOT populated (so candidates/near-misses never double-count).
        # Order by scoring.total_score DESC.
        nm_rows = cur.execute(
            """
            SELECT id, symbol, decision_snapshot
            FROM decision_history
            WHERE cycle_id = ?
              AND json_extract(decision_snapshot, '$.decision.outcome') = 'HOLD_INELIGIBLE'
              AND json_extract(decision_snapshot, '$.ranking.candidate_rank') IS NULL
            ORDER BY CAST(json_extract(decision_snapshot, '$.scoring.total_score') AS REAL) DESC
            LIMIT ?
            """,
            (cycle_id, near_miss_limit),
        ).fetchall()

        near_misses = []
        for row in nm_rows:
            try:
                snap = json.loads(row['decision_snapshot']) if row['decision_snapshot'] else {}
            except Exception as e:
                logger.debug(f"decision_snapshot parse failed for {row['symbol']}: {e}")
                snap = {}
            decision = snap.get('decision') or {}
            scoring = snap.get('scoring') or {}
            strategy_eligibility = snap.get('strategy_eligibility') or {}
            failed_gates = [
                g.get('name') for g in (strategy_eligibility.get('gates') or [])
                if g.get('passed') is False and g.get('name')
            ]
            near_misses.append({
                "symbol": row['symbol'],
                "total_score": scoring.get('total_score'),
                "decision_outcome": decision.get('outcome'),
                "decision_primary_reason": decision.get('primary_reason'),
                "failed_strategy_gates": failed_gates,
                "decision_history_id": row['id'],
            })

        conn.close()
        envelope = {
            "cycle_id": cycle_id,
            "cycle_known": cycle_known,
            "candidates": candidates,
            "candidates_found": len(candidates),
            "near_misses": near_misses,
            "near_misses_found": len(near_misses),
            "near_miss_limit": near_miss_limit,
        }
        if cycle_known:
            envelope["session_id"] = cf_row['session_id']
            envelope["cycle_start"] = cf_row['cycle_start']
            envelope["cycle_end"] = cf_row['cycle_end']
            envelope["analyzed_count"] = cf_row['analyzed_count']
            envelope["strategy_eligible_count"] = cf_row['strategy_eligible_count']
            envelope["ranked_candidate_count"] = cf_row['ranked_candidate_count']
        return envelope
    except Exception as e:
        logger.error(f"api_cycle_candidates failed: {e}")
        return {"error": str(e), "cycle_known": False, "candidates": [], "near_misses": []}


@app.get("/api/filter-dashboard")
def api_filter_dashboard(hours: int = 24, symbol: str = None):
    """Filter analysis dashboard — why symbols didn't generate BUY signals.

    Returns blocked_by distribution, per-filter breakdown, top scored HOLD
    signals, and most filtered symbols from the analyzed_stocks table.
    """
    import sqlite3
    from datetime import datetime, timedelta, timezone

    try:
        db_path = Path(__file__).parent / "trading_bot.db"
        if not db_path.exists():
            return {"error": "Database not found", "sections": {}}

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        sections = {}

        # ── 1. blocked_by distribution ───────────────────────────────────
        cur.execute("""
            SELECT blocked_by, COUNT(*) as cnt
            FROM analyzed_stocks
            WHERE last_analyzed > ?
            GROUP BY blocked_by
            ORDER BY cnt DESC
        """, (cutoff_str,))
        rows = cur.fetchall()
        total = sum(r["cnt"] for r in rows)
        blocked_by_dist = [
            {"label": str(r["blocked_by"]) if r["blocked_by"] else "(none — no block)",
             "count": r["cnt"],
             "pct": round(r["cnt"] / total * 100, 1) if total else 0}
            for r in rows
        ]
        sections["blocked_by"] = {"total": total, "distribution": blocked_by_dist}

        # ── 2. blocked_count distribution ──────────────────────────────
        cur.execute("""
            SELECT blocked_count, COUNT(*) as cnt
            FROM analyzed_stocks
            WHERE last_analyzed > ? AND blocked_count > 0
            GROUP BY blocked_count
            ORDER BY blocked_count
        """, (cutoff_str,))
        sections["blocked_count"] = [
            {"blocked_count": r["blocked_count"], "count": r["cnt"]}
            for r in cur.fetchall()
        ]

        # ── 3. Per-filter breakdown (from filter_results JSON) ────────
        cur.execute("""
            SELECT symbol, total_score, filter_results, blocked_by, blocked_count
            FROM analyzed_stocks
            WHERE last_analyzed > ? AND filter_results IS NOT NULL
        """, (cutoff_str,))
        rows = cur.fetchall()
        filter_names = [
            ("multi_timeframe_conflict", "MTF Conflict"),
            ("volume_downgrade",         "Volume Downgrade"),
            ("regime_filter",            "Market Regime"),
            ("earnings_filter",          "Earnings Proximity"),
            ("sp_relative_strength",     "SPY Underperformance"),
            ("volume_confirmation",      "Volume Confirmation"),
            ("trading_window",           "Trading Window"),
            ("news_sentiment",           "News Sentiment"),
            ("short_interest",           "Short Interest"),
            ("ai_conflict",              "AI Conflict"),
            ("liquidity_filter",         "Low Liquidity"),
            ("sector_filter",            "Sector Underperformance"),
        ]
        per_filter = []
        for key, label in filter_names:
            blocked = sum(1 for r in rows if _parse_fr(r["filter_results"]).get(key, {}).get("blocked"))
            passed  = sum(1 for r in rows if _parse_fr(r["filter_results"]).get(key, {}).get("passed", False))
            denom = blocked + passed
            per_filter.append({
                "key":      key,
                "label":    label,
                "blocked":  blocked,
                "passed":   passed,
                "pct":      round(blocked / denom * 100, 1) if denom else 0,
            })
        sections["per_filter"] = per_filter

        # ── 4. Most filtered symbols (2+ filters blocking) ─────────────
        cur.execute("""
            SELECT symbol, total_score, signal, blocked_by, blocked_count, filter_results
            FROM analyzed_stocks
            WHERE last_analyzed > ? AND blocked_count >= 2
            ORDER BY blocked_count DESC, total_score DESC
            LIMIT 15
        """, (cutoff_str,))
        most_filtered = []
        for r in cur.fetchall():
            fr = _parse_fr(r["filter_results"])
            blocking = [k for k, v in fr.items() if v.get("blocked")]
            most_filtered.append({
                "symbol":       r["symbol"],
                "score":        r["total_score"],
                "signal":       r["signal"],
                "blocked_by":   r["blocked_by"],
                "blocked_count":r["blocked_count"],
                "filters":      blocking,
            })
        sections["most_filtered"] = most_filtered

        # ── 5. Top scored HOLD signals (high score, no BUY) ────────────
        if symbol:
            cur.execute("""
                SELECT symbol, total_score, rsi, signal_strength, blocked_by, blocked_count, filter_results
                FROM analyzed_stocks
                WHERE symbol = ?
            """, (symbol.upper(),))
        else:
            cur.execute("""
                SELECT symbol, total_score, rsi, signal_strength, blocked_by, blocked_count, filter_results
                FROM analyzed_stocks
                WHERE last_analyzed > ? AND signal = 'HOLD' AND total_score >= 40
                ORDER BY total_score DESC
                LIMIT 20
            """, (cutoff_str,))

        top_holds = []
        for r in (cur.fetchall() if symbol else cur.fetchall()):
            fr = _parse_fr(r["filter_results"])
            blocking = [k for k, v in fr.items() if v.get("blocked")]
            top_holds.append({
                "symbol":        r["symbol"],
                "score":         r["total_score"],
                "rsi":           r["rsi"],
                "strength":       r["signal_strength"],
                "blocked_by":     r["blocked_by"],
                "blocked_count":  r["blocked_count"],
                "filters":       blocking,
            })
        sections["top_holds"] = top_holds

        conn.close()
        return {
            "hours":   hours,
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sections": sections,
        }

    except Exception as e:
        logger.error(f"Filter dashboard error: {e}")
        return {"error": str(e), "sections": {}}


def _parse_fr(fr_json):
    """Parse filter_results JSON, return {} on failure."""
    if not fr_json:
        return {}
    try:
        return json.loads(fr_json)
    except Exception:
        return {}


def api_orders(limit: int = 20):
    """API endpoint for orders"""
    return get_orders(limit)


@app.get("/api/trades")
def api_trades(limit: int = 20):
    """API endpoint for trades from DB"""
    return get_trades_from_db(limit)


@app.get("/api/sessions")
def api_sessions(limit: int = 5):
    """API endpoint for sessions.

    Returns the recent-sessions read model with truthful Symbols and Trades
    counts (decision_history / trades ground truth) and the BOT-003
    future-skew filter applied. See
    get_recent_sessions_with_truthful_counts for the full contract.
    """
    return get_recent_sessions_with_truthful_counts(limit)



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


@app.get("/api/runtime-status")
def api_runtime_status():
    """Three-tier runtime status (BOT-001).

    Independent booleans so the UI never conflates them:

      alpaca_api_reachable     - Alpaca paper API returned a populated account.
      smartbot_runner_active   - smartbot-runner.service systemd unit active.
      active_session_id        - id of the current ACTIVE row (None if none).
      fully_ready              - all three true (bot is genuinely running).

    See get_runtime_status() in this module for the detection logic.
    """
    return get_runtime_status()


@app.post("/api/start-session")
def api_start_session():
    """Insert a placeholder trading_sessions row.

    BOT-001 NOTE: This endpoint does NOT start SmartBot. It only inserts
    an audit-row in trading_sessions so the operator can see when a manual
    run was attempted. The actual bot loop is started by the
    smartbot-runner.service systemd unit (see BOT-001 audit). When the
    runner is not active, calling this endpoint is a no-op for runtime
    control: it does not spawn a process, does not start analyzing, and
    does not place trades.

    The response makes this contract explicit so the SPA can render an
    honest "Not Started" state instead of implying the bot is running.
    """
    if not db.is_available():
        raise HTTPException(status_code=400, detail="Database not available")

    session_id = db.create_session(
        bot_version="2.1.0",
        configuration={},
        is_paper_trading=True,
        notes="Started from dashboard (placeholder; smartbot-runner.service does not start SmartBot)",
    )
    return {
        "status": "recorded",
        "session_id": session_id,
        "bot_started": False,
        "message": (
            "Recorded a placeholder trading_sessions row. SmartBot is NOT "
            "running. The smartbot-runner.service unit (currently disabled) "
            "is the only way to actually start the bot."
        ),
        "runtime_status": get_runtime_status(),
    }


@app.post("/api/stop-session")
def api_stop_session():
    """Reap any stale-open ACTIVE row owned by the dashboard.

    BOT-001 NOTE: This endpoint does NOT stop SmartBot (the bot does not
    run inside this process). It only marks the most recent ACTIVE row
    FAILED with a notes suffix so operators can clean up phantom rows
    after a crash. The actual bot loop is killed by stopping the
    smartbot-runner.service systemd unit, which is intentionally NOT
    wired here because the runner is currently disabled by policy.
    """
    closed = 0
    try:
        cutoff = datetime.now(timezone.utc).isoformat()
        closed = simple_rest.close_stale_sessions(
            cutoff_iso=cutoff,
            reason="stopped-from-dashboard",
        )
    except Exception as e:
        logger.debug(f"api_stop_session: close_stale_sessions failed: {e}")
    return {
        "status": "ok",
        "sessions_closed": closed,
        "bot_stopped": False,
        "message": (
            "Closed {n} stale-open trading_sessions row(s). SmartBot was "
            "not running in this process; if smartbot-runner.service is "
            "active elsewhere, stop it with `systemctl --user stop "
            "smartbot-runner.service`.".format(n=closed)
        ),
        "runtime_status": get_runtime_status(),
    }


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




# ─────────────────────────────────────────────────────────────────────────────
# OBS-001 Phase C — OBS Analytics endpoints
# ─────────────────────────────────────────────────────────────────────────────
#
# Five read-only endpoints backing the redesigned Analytics tab. Every
# endpoint reads ONLY from persisted columns or persisted JSON fields.
# No endpoint mutates the database. No endpoint consults live settings
# or recomputes a decision. No endpoint uses heuristic text parsing
# of `decision.primary_reason`. No endpoint infers a strategy-gate
# failure from HOLD_INELIGIBLE alone.
#
# Cohort semantics:
#   - "post_obs002": only rows whose cycle_start >= the OBS-002
#     deployment boundary (2026-09-14T20:24:55 UTC, the first
#     SKIPPED_INVALID_DATA cycle).
#   - "all": every cycle, regardless of boundary. The UI exposes
#     this only behind a coverage banner.
#
# Range semantics:
#   - "latest":  only the latest single completed cycle (Phase B's
#     "latest cycle" notion).
#   - "today":   rows whose cycle_start falls within the server's
#                 current calendar day (UTC for production).
#   - "24h":     cycle_start >= now() - 24h
#   - "7d":      cycle_start >= now() - 7*24h
#
# All boundary computation is performed server-side from
# datetime.now(timezone.utc). The browser only sends the choice
# enum; it does not compute a timestamp.

OBS_002_DEPLOYMENT_BOUNDARY_UTC = "2026-09-14T20:24:55"
_VALID_PHASE_C_RANGES = {"latest", "today", "24h", "7d"}
_VALID_PHASE_C_COHORTS = {"post_obs002", "all"}

# PHASE-C9: shared helper accepts a fully-qualified column name
# ("alias.column" or "column") so a single helper can serve all
# five Phase C endpoints regardless of whether they alias the
# source table (`dh` for decision_history, `cf` for cycle_funnel)
# or not. Aliases must come from this internal allowlist — never
# from request data — and are interpolated as-is into the SQL
# fragment. User-supplied values stay bound via `?` parameters.
_PHASE_C_ALIAS_ALLOWLIST = frozenset({"", "dh", "cf"})


def _phase_c_qualify_column(column: str) -> str:
    """Validate and return a fully-qualified column name.

    Accepts either "column" or "alias.column" where alias is one
    of the internal allowlist members. This is the ONLY function
    that interpolates an alias into a SQL fragment; user input
    never reaches it. Column names are also restricted to a
    small known set; this prevents accidental injection if a
    future caller mistakenly passes a request-derived string.
    """
    # Allowlist of column names Phase C endpoints may filter on.
    # Kept narrow on purpose: extending it requires reviewing the
    # call sites and the test surface.
    allowed_columns = {"cycle_start"}
    if "." in column:
        alias, _, col = column.partition(".")
        if alias not in _PHASE_C_ALIAS_ALLOWLIST:
            raise ValueError(f"unknown Phase C alias '{alias}'")
        if col not in allowed_columns:
            raise ValueError(f"unknown Phase C column '{col}'")
        return column
    if column not in allowed_columns:
        raise ValueError(f"unknown Phase C column '{column}'")
    return column


def _phase_c_range_clause(range_name: str, column: str = "cycle_start"):
    """Return (where_clause, params) for a Phase C range filter.

    The clause is intentionally simple and deterministic. SQLite
    handles ISO-8601 UTC lexicographic comparison correctly because
    every persisted timestamp is a `+00:00` suffix ISO string.

    The `column` argument may be either `"cycle_start"` or a
    fully-qualified `"alias.cycle_start"` so a single helper
    serves endpoints that alias the source table and those that
    don't. The alias is validated against an internal allowlist
    in `_phase_c_qualify_column`.
    """
    qualified = _phase_c_qualify_column(column)
    if range_name == "latest":
        # Only the row with the largest cycle_end that is non-null.
        # The subquery's `cycle_start` is unqualified because
        # cycle_funnel is always the inner table here.
        return (
            f"({qualified} = (SELECT cycle_start FROM cycle_funnel "
            f"WHERE cycle_end IS NOT NULL ORDER BY cycle_end DESC LIMIT 1))",
            (),
        )
    if range_name == "today":
        # cycle_start is stored as ISO-8601 UTC. Date prefix
        # lexicographic comparison yields the UTC calendar day.
        from datetime import datetime as _dt
        today_prefix = _dt.now(timezone.utc).strftime("%Y-%m-%d")
        return (f"({qualified} >= ? AND {qualified} < ?)", (f"{today_prefix}T00:00:00+00:00", f"{today_prefix}T23:59:59.999999+00:00"))
    if range_name == "24h":
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.now(timezone.utc) - _td(hours=24)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return (f"({qualified} >= ?)", (cutoff,))
    if range_name == "7d":
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.now(timezone.utc) - _td(days=7)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return (f"({qualified} >= ?)", (cutoff,))
    # Defensive: the FastAPI dependency below enforces the enum.
    raise ValueError(f"invalid Phase C range: {range_name}")


def _phase_c_cohort_clause(cohort: str, column: str = "cycle_start"):
    """Return (where_clause, params) for a Phase C cohort filter.

    Same alias handling as `_phase_c_range_clause`.
    """
    qualified = _phase_c_qualify_column(column)
    if cohort == "post_obs002":
        return (f"({qualified} >= ?)", (OBS_002_DEPLOYMENT_BOUNDARY_UTC,))
    if cohort == "all":
        return ("(1=1)", ())
    raise ValueError(f"invalid Phase C cohort: {cohort}")


def _phase_c_window_predicates(range_name: str, cohort: str, column: str):
    """PHASE-C9: bundle the cohort + range filter fragments.

    Each Phase C endpoint that filters by range + cohort repeats
    the same three-line pattern:

        cohort_sql, cohort_params = _phase_c_cohort_clause(cohort, column)
        range_sql, range_params = _phase_c_range_clause(range_name, column)
        params = cohort_params + range_params

    This helper consolidates the helper-call + param-concat step.
    Endpoint-specific WHERE composition (joins, additional filters,
    aggregation, ordering) stays in the caller. No new query
    builder is introduced.

    Returns a tuple of:
        cohort_clause_sql: SQL fragment for the cohort predicate
        range_clause_sql:  SQL fragment for the range predicate
        combined_params:   bound parameters in cohort-then-range order

    The caller composes the WHERE clause from the two fragments,
    e.g.:

        cohort_sql, range_sql, params = _phase_c_window_predicates(
            range_name, cohort, "dh.cycle_start"
        )
        sql = (
            "SELECT ... FROM decision_history dh "
            f"WHERE {cohort_sql} AND {range_sql} ..."
        )
        cur.execute(sql, params)

    Alias handling for `column` flows through `_phase_c_qualify_column`
    (allowlist: "", "dh", "cf"). Aliases come only from internal
    call sites, never from request data. User-supplied values
    remain bound via `?` parameters.
    """
    cohort_clause_sql, cohort_params = _phase_c_cohort_clause(cohort, column)
    range_clause_sql, range_params = _phase_c_range_clause(range_name, column)
    return cohort_clause_sql, range_clause_sql, cohort_params + range_params


def _phase_c_open_db():
    """Open trading_bot.db as a read-only sqlite3 connection."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _P
    db_path = _P(__file__).parent / "trading_bot.db"
    if not db_path.exists():
        return None
    conn = _sqlite3.connect(str(db_path))
    conn.row_factory = _sqlite3.Row
    return conn


def _phase_c_validate(range_name: str, cohort: str):
    """Validate Phase C query params. Returns error envelope or None."""
    if range_name not in _VALID_PHASE_C_RANGES:
        return {"error": f"invalid range '{range_name}'; must be one of {sorted(_VALID_PHASE_C_RANGES)}",
                "valid_ranges": sorted(_VALID_PHASE_C_RANGES)}
    if cohort not in _VALID_PHASE_C_COHORTS:
        return {"error": f"invalid cohort '{cohort}'; must be one of {sorted(_VALID_PHASE_C_COHORTS)}",
                "valid_cohorts": sorted(_VALID_PHASE_C_COHORTS)}
    return None


@app.get("/api/phase-c/funnel")
def api_phase_c_funnel(range_name: str = Query("7d", alias="range"), cohort: str = "post_obs002"):
    """OBS-001 Phase C: aggregate cycle_funnel counters for a window.

    Returns the canonical counters summed across the cohort. Counts
    come from `cycle_funnel` only — never from `decision_history`,
    because `decision_history` has multiple rows per cycle and would
    double-count.

    Forward path:
      analyzed_count -> strategy_eligible_count -> ranked_candidate_count
      -> execution_attempt_count -> order_submitted_count.

    Off-path (NEVER shown flowing into submission):
      execution_blocked_count (off-path)
      order_failed_count     (off-path)
      not_attempted_count    (off-path; reason preserved)
    """
    err = _phase_c_validate(range_name, cohort)
    if err: return err
    conn = _phase_c_open_db()
    if conn is None:
        return {"error": "Database not found", "range": range_name, "cohort": cohort}

    try:
        cur = conn.cursor()
        # PHASE-C9: use the bundle helper. The column is unqualified
        # ("cycle_start") because cycle_funnel is the only source here.
        cohort_sql, range_sql, params = _phase_c_window_predicates(
            range_name, cohort, "cycle_start"
        )
        sql = (
            "SELECT "
            "  COUNT(*) AS cycles, "
            "  COALESCE(SUM(analyzed_count), 0) AS analyzed_count, "
            "  COALESCE(SUM(strategy_eligible_count), 0) AS strategy_eligible_count, "
            "  COALESCE(SUM(ranked_candidate_count), 0) AS ranked_candidate_count, "
            "  COALESCE(SUM(execution_attempt_count), 0) AS execution_attempt_count, "
            "  COALESCE(SUM(execution_blocked_count), 0) AS execution_blocked_count, "
            "  COALESCE(SUM(order_submission_attempt_count), 0) AS order_submission_attempt_count, "
            "  COALESCE(SUM(order_submitted_count), 0) AS order_submitted_count, "
            "  COALESCE(SUM(order_failed_count), 0) AS order_failed_count, "
            "  COALESCE(SUM(not_attempted_count), 0) AS not_attempted_count "
            "FROM cycle_funnel "
            f"WHERE {cohort_sql} AND {range_sql}"
        )
        row = cur.execute(sql, params).fetchone()

        # Largest dropoff for the headline callout. Compare forward
        # path stages pairwise; the largest delta wins.
        forward = [
            ("Analyzed", row["analyzed_count"]),
            ("Strategy Eligible", row["strategy_eligible_count"]),
            ("Ranked Candidates", row["ranked_candidate_count"]),
            ("Execution Attempted", row["execution_attempt_count"]),
            ("Orders Submitted", row["order_submitted_count"]),
        ]
        largest_dropoff = None
        for i in range(len(forward) - 1):
            delta = forward[i][1] - forward[i + 1][1]
            if delta <= 0:
                continue
            if largest_dropoff is None or delta > largest_dropoff["delta"]:
                largest_dropoff = {
                    "from_stage": forward[i][0],
                    "to_stage": forward[i + 1][0],
                    "delta": int(delta),
                }

        return {
            "range": range_name,
            "cohort": cohort,
            "cycles": int(row["cycles"]),
            "forward_path": {
                "analyzed_count": int(row["analyzed_count"]),
                "strategy_eligible_count": int(row["strategy_eligible_count"]),
                "ranked_candidate_count": int(row["ranked_candidate_count"]),
                "execution_attempt_count": int(row["execution_attempt_count"]),
                "orders_submitted_count": int(row["order_submitted_count"]),
            },
            "off_path": {
                "execution_blocked_count": int(row["execution_blocked_count"]),
                "orders_failed_count": int(row["order_failed_count"]),
                "not_attempted_count": int(row["not_attempted_count"]),
            },
            "largest_dropoff": largest_dropoff,
        }
    except Exception as e:
        logger.error(f"api_phase_c_funnel failed: {e}")
        return {"error": str(e), "range": range_name, "cohort": cohort}
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/phase-c/strategy-gates")
def api_phase_c_strategy_gates(range_name: str = Query("7d", alias="range"), cohort: str = "post_obs002"):
    """OBS-001 Phase C: aggregate `strategy_eligibility.gates[]`.

    Source of truth: `decision_history.decision_snapshot ->
    $.strategy_eligibility.gates[]`. Each gate record has
    `{name, category, applied, passed, observed_value,
    threshold_value, reason}`. We never parse `primary_reason`.

    For each distinct gate `name`, returns:
      total_evaluations, passed, failed, failure_rate, applied.

    A gate is "passed" iff `passed == true`. A gate is "failed"
    iff `passed == false`. `applied == true` entries are
    exclusively those the bot actually evaluated (vs back-filled
    NOT RUN). All denominators use the same applied count so
    failure_rate = failed / total_evaluations.

    NO row-level inference is performed. We never map
    HOLD_INELIGIBLE -> failed gate; we only count rows where
    `gates[].passed = false`.

    **PHASE-C7 gate-aggregation contract** (closed 2026-09-17):

    Only persisted `applied == true` gate evaluations contribute
    to the aggregation. The semantic mapping is:

      gate.applied=true  + gate.passed=true  -> passed += 1, total += 1
      gate.applied=true  + gate.passed=false -> failed += 1, total += 1
      gate.applied=false                       -> contributes 0 to
                                                   passed, failed,
                                                   and total
      gate.applied missing / malformed / NULL -> contributes 0 to
                                                   passed, failed,
                                                   and total
                                                   (fails closed)

    The SQL filters at the join level:
      WHERE COALESCE(json_extract(gate.value, '$.applied'), 0) = 1

    so `COUNT(*)` per gate equals the number of applied=true gate
    evaluations (the denominator). `failure_rate` = failed / total
    uses only the applied=true denominator. See
    `tests/test_dashboard_phase_c_obs_analytics.py ::
    TestGateAggregationAppliedFilter` for regression coverage.
    """
    err = _phase_c_validate(range_name, cohort)
    if err: return err
    conn = _phase_c_open_db()
    if conn is None:
        return {"error": "Database not found", "range": range_name, "cohort": cohort}

    try:
        cur = conn.cursor()
        # PHASE-C9: use the bundle helper. The alias is internal
        # ("dh" for decision_history) and goes through
        # `_phase_c_qualify_column`'s allowlist. No request data
        # is interpolated.
        cohort_clause_sql, range_clause_sql, params = _phase_c_window_predicates(
            range_name, cohort, "dh.cycle_start"
        )

        # Count rows in the cohort (for the explicit coverage footer).
        # This is a fast COUNT(*) — no JSON extraction — so it stays
        # under a second on the production DB. JSON-derived cohort
        # counts would extract JSON for every row and take 10s+.
        rows_in_cohort = int(cur.execute(
            "SELECT COUNT(*) AS c FROM decision_history dh "
            f"WHERE {cohort_clause_sql} AND {range_clause_sql}",
            params,
        ).fetchone()["c"])

        sql = (
            "SELECT "
            "  json_extract(gate.value, '$.name') AS gate_name, "
            "  json_extract(gate.value, '$.category') AS gate_category, "
            "  SUM(CASE WHEN COALESCE(json_extract(gate.value, '$.passed'), 0) = 1 "
            "           THEN 1 ELSE 0 END) AS passed_count, "
            "  SUM(CASE WHEN COALESCE(json_extract(gate.value, '$.applied'), 0) = 1 "
            "           THEN 1 ELSE 0 END) AS applied_count, "
            "  COUNT(*) AS total_count, "
            "  MIN(CASE WHEN json_extract(gate.value, '$.observed_value') IS NOT NULL "
            "           THEN CAST(json_extract(gate.value, '$.observed_value') AS REAL) "
            "           ELSE NULL END) AS sampled_observed_min, "
            "  MAX(CASE WHEN json_extract(gate.value, '$.observed_value') IS NOT NULL "
            "           THEN CAST(json_extract(gate.value, '$.observed_value') AS REAL) "
            "           ELSE NULL END) AS sampled_observed_max, "
            "  MIN(CASE WHEN json_extract(gate.value, '$.threshold_value') IS NOT NULL "
            "           THEN CAST(json_extract(gate.value, '$.threshold_value') AS REAL) "
            "           ELSE NULL END) AS sampled_threshold_min, "
            "  MAX(CASE WHEN json_extract(gate.value, '$.threshold_value') IS NOT NULL "
            "           THEN CAST(json_extract(gate.value, '$.threshold_value') AS REAL) "
            "           ELSE NULL END) AS sampled_threshold_max "
            "FROM decision_history dh, "
            "     json_each(json_extract(dh.decision_snapshot, '$.strategy_eligibility.gates')) AS gate "
            f"WHERE {cohort_clause_sql} AND {range_clause_sql} "
            # PHASE-C7: only persisted applied=true gate evaluations contribute.
            # Missing or malformed `applied` is coalesced to 0 (fail closed).
            "  AND COALESCE(json_extract(gate.value, '$.applied'), 0) = 1 "
            "GROUP BY gate_name, gate_category "
            "ORDER BY SUM(CASE WHEN COALESCE(json_extract(gate.value, '$.passed'), 0) = 0 "
            "                  THEN 1 ELSE 0 END) DESC, gate_name ASC"
        )
        rows = cur.execute(sql, params).fetchall()

        # Aggregate per gate_name. Deterministic ordering: failures DESC
        # then name ASC (already enforced by the ORDER BY clause above).
        gates_list = []
        for r in rows:
            name = r["gate_name"]
            if name is None or name == "":
                continue
            total = int(r["total_count"])
            passed = int(r["passed_count"])
            # PHASE-C7: the WHERE filter restricted the join to
            # applied=true rows, so total = COUNT of applied=true
            # gate evaluations, and failed = total - passed counts
            # only applied=true + passed=false (NOT applied=false,
            # NOT missing/malformed applied).
            failed = total - passed
            slot = {
                "gate_name": name,
                "category": r["gate_category"],
                "total_evaluations": total,
                "passed": passed,
                "failed": failed,
                "applied_count": int(r["applied_count"]),
                "failure_rate": (failed / total) if total else 0.0,
            }
            if r["sampled_observed_min"] is not None:
                slot["sampled_observed_min"] = float(r["sampled_observed_min"])
                slot["sampled_observed_max"] = float(r["sampled_observed_max"])
                slot["sampled_threshold_min"] = float(r["sampled_threshold_min"])
                slot["sampled_threshold_max"] = float(r["sampled_threshold_max"])
            gates_list.append(slot)

        return {
            "range": range_name,
            "cohort": cohort,
            "rows_in_cohort": rows_in_cohort,
            "gate_rows_aggregated": sum(g["total_evaluations"] for g in gates_list),
            "gates": gates_list,
            "source": "decision_history.decision_snapshot -> $.strategy_eligibility.gates[]",
            "inference_rule": (
                "failures counted only where gate.passed == false AND "
                "gate.applied == true; gate.applied == false and "
                "malformed/missing applied both contribute 0 (fail closed); "
                "never from HOLD_INELIGIBLE outcome"
            ),
        }
    except Exception as e:
        logger.error(f"api_phase_c_strategy_gates failed: {e}")
        return {"error": str(e), "range": range_name, "cohort": cohort}
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/phase-c/execution-blockers")
def api_phase_c_execution_blockers(range_name: str = Query("7d", alias="range"), cohort: str = "post_obs002"):
    """OBS-001 Phase C: aggregate `execution_checks.checks[]`.

    Honest universe: only `decision_history` rows whose snapshot has
    a populated `$.execution_checks.checks[]` contribute. In current
    runtime this is the SELL path (SELL_BLOCKED_DYNAMIC). The
    response surfaces the universe size explicitly so the UI can
    render the "Blocker data only exists for symbols that reached
    execute_trade" caveat.

    Per check `name`: total_evaluations, passed, failed, applied_count.
    A check is "passed" iff `passed == true`; "failed" iff
    `passed == false`. `first_blocking_check` is preserved per row
    in a small sample so the UI can show the most common blockers
    without doing client-side aggregation.
    """
    err = _phase_c_validate(range_name, cohort)
    if err: return err
    conn = _phase_c_open_db()
    if conn is None:
        return {"error": "Database not found", "range": range_name, "cohort": cohort}

    try:
        cur = conn.cursor()
        # PHASE-C9: use the bundle helper. The alias is internal
        # ("dh" for decision_history) and goes through
        # `_phase_c_qualify_column`'s allowlist. No request data
        # is interpolated.
        cohort_clause_sql, range_clause_sql, params = _phase_c_window_predicates(
            range_name, cohort, "dh.cycle_start"
        )

        # Single-statement CTE chain. `cohort` materializes the
        # filtered set once (uses idx_decision_history_cycle_start);
        # `parsed` materializes the JSON-extracted columns once per
        # cohort row so the four aggregate subqueries reuse them
        # instead of re-parsing `decision_snapshot`. MATERIALIZED
        # forces single evaluation — without it SQLite may inline
        # the CTE on each reference and re-do the JSON work.
        # `decision_history` is scanned at most once per request;
        # `parsed` consumers read from the materialized CTE only.
        #
        # Filter semantics (preserved from the prior per-pass SQL):
        #   * rows_with_checks: json_type='array' AND
        #     json_array_length>0.
        #   * per-check: checks_json IS NOT NULL AND
        #     json_array_length>0; check_name IS NOT NULL AND !=''
        #     AND !=0 (matches Python `if not name: continue`).
        #   * first_blocker: first_blocker IS NOT NULL; null/empty
        #     maps to 'unknown' (matches Python `r["blocker"] or
        #     "unknown"`).
        #   * checks sort: `failed DESC, check_name ASC`.
        #     first_blocking sort: `cnt DESC, blocker ASC`.
        # Regression tests: tests/test_phase_c14_execution_blockers_refactor.py.
        single_sql = (
            "WITH cohort AS MATERIALIZED ( "
            "  SELECT decision_snapshot FROM decision_history dh "
            f"  WHERE {cohort_clause_sql} AND {range_clause_sql} "
            "), parsed AS MATERIALIZED ( "
            "  SELECT "
            "    decision_snapshot, "
            "    json_extract(decision_snapshot, '$.execution_checks.checks') AS checks_json, "
            "    json_extract(decision_snapshot, '$.execution_checks.first_blocking_check') AS first_blocker "
            "  FROM cohort "
            ") "
            "SELECT "
            "  (SELECT COUNT(*) FROM cohort) AS rows_in_cohort, "
            "  (SELECT COUNT(*) FROM parsed "
            "   WHERE json_type(checks_json) = 'array' "
            "     AND json_array_length(checks_json) > 0) AS rows_with_checks, "
            "  (SELECT json_group_array(json_object( "
            "     'check_name', check_name, "
            "     'total_evaluations', total_evaluations, "
            "     'passed', passed, "
            "     'failed', failed, "
            "     'applied_count', applied_count)) "
            "   FROM ( "
            "     SELECT "
            "       json_extract(\"check\".value, '$.name') AS check_name, "
            "       SUM(1) AS total_evaluations, "
            "       SUM(CASE WHEN COALESCE(json_extract(\"check\".value, '$.passed'), 0) THEN 1 ELSE 0 END) AS passed, "
            "       SUM(CASE WHEN COALESCE(json_extract(\"check\".value, '$.passed'), 0) THEN 0 ELSE 1 END) AS failed, "
            "       SUM(CASE WHEN COALESCE(json_extract(\"check\".value, '$.applied'), 0) THEN 1 ELSE 0 END) AS applied_count "
            "     FROM parsed, json_each(parsed.checks_json) AS \"check\" "
            "     WHERE parsed.checks_json IS NOT NULL "
            "       AND json_array_length(parsed.checks_json) > 0 "
            "       AND json_extract(\"check\".value, '$.name') IS NOT NULL "
            "       AND json_extract(\"check\".value, '$.name') != '' "
            "       AND json_extract(\"check\".value, '$.name') != 0 "
            "     GROUP BY check_name "
            "     ORDER BY failed DESC, check_name ASC "
            "   )) AS checks_json, "
            "  (SELECT json_group_array(json_object('check_name', blocker, 'count', cnt)) "
            "   FROM ( "
            "     SELECT "
            "       CASE WHEN first_blocker IS NULL OR first_blocker = '' "
            "            THEN 'unknown' ELSE first_blocker END AS blocker, "
            "       COUNT(*) AS cnt "
            "     FROM parsed "
            "     WHERE first_blocker IS NOT NULL "
            "     GROUP BY blocker "
            "     ORDER BY cnt DESC, blocker ASC "
            "   )) AS first_blocking_json "
        )
        row = cur.execute(single_sql, params).fetchone()
        rows_in_cohort = int(row["rows_in_cohort"])
        rows_with_checks = int(row["rows_with_checks"])
        # `json_group_array` returns a JSON array string (or '[]' for
        # empty input). json.loads decodes it back into Python lists.
        import json as _json
        checks_raw = _json.loads(row["checks_json"] or "[]")
        first_blocking_raw = _json.loads(row["first_blocking_json"] or "[]")
        # The SQL aggregations already produce the exact field shape
        # and ordering used in the response; no post-processing
        # beyond the JSON decode is needed.
        checks_list = [
            {
                "check_name": c["check_name"],
                "total_evaluations": int(c["total_evaluations"]),
                "passed": int(c["passed"]),
                "failed": int(c["failed"]),
                "applied_count": int(c["applied_count"]),
            }
            for c in checks_raw
        ]
        first_blocking = [
            {
                "check_name": r["check_name"],
                "count": int(r["count"]),
            }
            for r in first_blocking_raw
        ]

        return {
            "range": range_name,
            "cohort": cohort,
            "rows_in_cohort": rows_in_cohort,
            "rows_with_checks": rows_with_checks,
            "universe_caveat": (
                "Blocker data only exists for decision_history rows whose "
                "OBS-001 trace reached execute_trade. Currently this is the "
                "SELL path (SELL_BLOCKED_DYNAMIC). BUY paths and HOLD outcomes "
                "are not in this universe."
            ),
            "checks": checks_list,
            "first_blocking_check": first_blocking,
            "source": "decision_history.decision_snapshot -> $.execution_checks.checks[]",
        }
    except Exception as e:
        logger.error(f"api_phase_c_execution_blockers failed: {e}")
        return {"error": str(e), "range": range_name, "cohort": cohort}
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/phase-c/outcomes")
def api_phase_c_outcomes(range_name: str = Query("7d", alias="range"), cohort: str = "post_obs002"):
    """OBS-001 Phase C: per-decision outcome distribution.

    Counts every `decision_history` row by `$.decision.outcome`. This
    is per-decision, not per-cycle, so SKIPPED_INVALID_DATA can be
    shown as its own bar with the explicit label "Invalid data (no
    score, no rank)".

    Deterministic sort: count DESC, outcome enum ASC.
    """
    err = _phase_c_validate(range_name, cohort)
    if err: return err
    conn = _phase_c_open_db()
    if conn is None:
        return {"error": "Database not found", "range": range_name, "cohort": cohort}

    try:
        cur = conn.cursor()
        # PHASE-C9: use the bundle helper. The alias is internal
        # ("dh" for decision_history) and goes through
        # `_phase_c_qualify_column`'s allowlist. No request data
        # is interpolated.
        cohort_clause_sql, range_clause_sql, params = _phase_c_window_predicates(
            range_name, cohort, "dh.cycle_start"
        )

        sql = (
            "SELECT json_extract(decision_snapshot, '$.decision.outcome') AS outcome, "
            "       COUNT(*) AS cnt "
            "FROM decision_history dh "
            f"WHERE {cohort_clause_sql} AND {range_clause_sql} "
            "GROUP BY outcome "
            "ORDER BY cnt DESC, outcome ASC"
        )
        rows = cur.execute(sql, params).fetchall()
        total = sum(int(r["cnt"]) for r in rows)
        return {
            "range": range_name,
            "cohort": cohort,
            "total_decisions": total,
            "outcomes": [
                {"outcome": (r["outcome"] or "NULL"), "count": int(r["cnt"]),
                 "pct": (int(r["cnt"]) / total if total else 0.0)}
                for r in rows
            ],
            "skipped_invalid_data_label": "Invalid data (no score, no rank)",
            "source": "decision_history.decision_snapshot -> $.decision.outcome",
        }
    except Exception as e:
        logger.error(f"api_phase_c_outcomes failed: {e}")
        return {"error": str(e), "range": range_name, "cohort": cohort}
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/phase-c/coverage")
def api_phase_c_coverage(range_name: str = Query("7d", alias="range")):
    """OBS-001 Phase C: pre vs post OBS-002 cycle completion coverage.

    For each cycle in `cycle_funnel`, completion is defined as
    `cycle_funnel.analyzed_count == COUNT(DISTINCT decision_history.symbol
    for that cycle)`. The endpoint computes per-cycle coverage
    separately for the pre-OBS-002 and post-OBS-002 bands so the UI
    can show the historical coverage gap honestly.

    Coverage stats:
      pre_obs002:
        cycle_count, complete_cycles, incomplete_cycles,
        total_missing_symbol_decisions
      post_obs002:
        cycle_count, complete_cycles, incomplete_cycles,
        total_missing_symbol_decisions

    Pre-OBS-002 cycles that predate `decision_history` entirely are
    not backfilled. Their `complete_cycles` count is 0 and they
    contribute `analyzed_count` to `total_missing_symbol_decisions`
    (because the DB has no `decision_history` rows for them).
    """
    if range_name not in _VALID_PHASE_C_RANGES:
        return {"error": f"invalid range '{range_name}'",
                "valid_ranges": sorted(_VALID_PHASE_C_RANGES)}
    conn = _phase_c_open_db()
    if conn is None:
        return {"error": "Database not found", "range": range_name}

    try:
        cur = conn.cursor()
        # PHASE-C9: use the bundle helper. The alias is internal
        # ("cf" for cycle_funnel) and goes through
        # `_phase_c_qualify_column`'s allowlist. No request data
        # is interpolated. The coverage endpoint does not filter by
        # cohort at the SQL level — it computes pre_obs002 vs
        # post_obs002 stats in Python — so we pass the constant
        # `cohort="all"` here. The bundle helper returns
        # `(1=1)` as the cohort fragment, which is harmless and
        # keeps the endpoint on the same code path as the others.
        _, range_clause, params = _phase_c_window_predicates(
            range_name, "all", "cf.cycle_start"
        )

        # Pull every cycle in the range with its decision_history distinct
        # symbol count. SQLite's LEFT JOIN + subquery handles legacy
        # cycles (no matching rows => 0 distinct symbols).
        sql = (
            "SELECT cf.cycle_id, cf.cycle_start, cf.cycle_end, cf.analyzed_count, "
            "       cf.schema_version, "
            "       (SELECT COUNT(DISTINCT dh.symbol) FROM decision_history dh "
            "          WHERE dh.cycle_id = cf.cycle_id) AS distinct_symbols "
            "FROM cycle_funnel cf "
            f"WHERE {range_clause} "
            "ORDER BY cf.cycle_start ASC"
        )
        rows = cur.execute(sql, params).fetchall()

        def _band_stats(band_rows):
            cycle_count = len(band_rows)
            complete = 0
            incomplete = 0
            missing = 0
            for r in band_rows:
                gap = int(r["analyzed_count"]) - int(r["distinct_symbols"])
                if gap == 0:
                    complete += 1
                else:
                    incomplete += 1
                    missing += gap
            return {
                "cycle_count": cycle_count,
                "complete_cycles": complete,
                "incomplete_cycles": incomplete,
                "total_missing_symbol_decisions": missing,
            }

        pre_rows = [r for r in rows if (r["cycle_start"] or "") < OBS_002_DEPLOYMENT_BOUNDARY_UTC]
        post_rows = [r for r in rows if (r["cycle_start"] or "") >= OBS_002_DEPLOYMENT_BOUNDARY_UTC]

        pre = _band_stats(pre_rows)
        post = _band_stats(post_rows)

        return {
            "range": range_name,
            "obs002_deployment_boundary_utc": OBS_002_DEPLOYMENT_BOUNDARY_UTC,
            "pre_obs002": {**pre, "note": (
                "Pre-OBS-002 cycles may be incomplete. "
                "decision_history is sparse for these cycles; the dashboard "
                "does not backfill missing decisions from current settings "
                "or raw indicators."
            )},
            "post_obs002": {**post, "note": (
                "Post-OBS-002 cycles carry a complete decision_history "
                "footprint (terminal decision coverage invariant)."
            )},
            "cycles_total": len(rows),
        }
    except Exception as e:
        logger.error(f"api_phase_c_coverage failed: {e}")
        return {"error": str(e), "range": range_name}
    finally:
        try:
            conn.close()
        except Exception:
            pass


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


@app.get("/api/search/{symbol}")
def api_search_symbol(symbol: str):
    """Search for a symbol and return stored analysis from database"""
    import sqlite3
    from pathlib import Path
    
    db_path = Path(__file__).parent / "trading_bot.db"
    
    if not db_path.exists():
        return {"error": "Database not found"}
    
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM analyzed_stocks WHERE symbol = ?
        """, (symbol.upper(),))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {"error": f"Symbol {symbol} not found in database"}
        
        # Parse buy_criteria
        buy_criteria = []
        try:
            if row['buy_criteria']:
                import json
                buy_criteria = json.loads(row['buy_criteria'])
        except:
            pass
        
        return {
            "symbol": row['symbol'],
            "price": row['price'],
            "total_score": row['total_score'],
            "signal": row['signal'],
            "signal_strength": row['signal_strength'],
            "rsi": row['rsi'],
            "rsi_score": row['rsi_score'],
            "sma_score": row['sma_score'],
            "macd_score": row['macd_score'],
            "bb_score": row['bb_score'],
            "regime_score": row['regime_score'],
            "catalyst_score": row['catalyst_score'],
            "buy_criteria": buy_criteria,
            "passes_all_buy_criteria": bool(row['passes_all_buy_criteria']),
            "last_analyzed": row['last_analyzed'],
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


@app.get("/api/failed-analyses")
def api_get_failed_analyses(
    from_date: str = None,
    to_date: str = None,
    summary_only: bool = False,
):
    """Get failed analysis records or summary for the pie chart.

    Query params:
        from_date:      ISO timestamp (e.g. '2026-06-01T00:00:00Z')
        to_date:        ISO timestamp
        summary_only:   If true, returns aggregated breakdown instead of rows
    """
    try:
        from database.sqlite_db import sqlite_db
        if summary_only:
            result = sqlite_db.get_failed_analyses_summary(
                from_date=from_date, to_date=to_date
            )
            return result
        else:
            records = sqlite_db.get_failed_analyses(
                from_date=from_date, to_date=to_date, limit=500
            )
            return {"records": records, "count": len(records)}
    except Exception as e:
        logging.error(f"Error fetching failed analyses: {e}")
        return {"error": str(e), "records": [], "count": 0}



@app.get("/api/settings")
def api_get_settings():
    """Return all trading parameters with metadata for the dashboard UI."""
    params = _refresh_trading_params()
    return {
        key: {
            "value": param["value"],
            "min": param["min"],
            "max": param["max"],
            "step": param["step"],
            "type": param["type"],
            "default": param["default"],
            "description": param["description"],
            "category": param["category"],
        }
        for key, param in params.items()
    }


@app.post("/api/settings")
def api_update_settings(updates: Dict):
    """Update one or more trading parameters. Returns updated values."""
    global _smart_bot_instance
    _refresh_trading_params()
    try:
        updated = _validate_dashboard_settings_batch(updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _persist_dashboard_settings_batch(updated)
    for key, normalized in updated.items():
        if _smart_bot_instance is not None and hasattr(_smart_bot_instance, key):
            setattr(_smart_bot_instance, key, normalized)
    return {"updated": updated, "status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
