"""
Smart Limit Order System

Replaces market orders with intelligent limit orders to reduce slippage.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class QuoteData:
    """Latest quote for a ticker"""
    symbol: str
    bid: float
    ask: float
    mid: float
    spread: float
    vwap: float
    timestamp: datetime


def get_latest_quote(ticker: str) -> Optional[QuoteData]:
    """
    Fetch latest bid/ask from Alpaca with yfinance fallback.
    
    Args:
        ticker: Stock symbol
        
    Returns:
        QuoteData object or None if unavailable
    """
    try:
        from alpaca.data import StockLatestQuoteRequest
        from src.core.smart_bot import SmartTradingBot
        
        bot = SmartTradingBot()
        request = StockLatestQuoteRequest(symbol_or_symbols=[ticker])
        quote = bot.data_client.get_stock_latest_quote(request)
        
        if ticker in quote.data:
            q = quote.data[ticker]
            
            return QuoteData(
                symbol=ticker,
                bid=float(q.bid_price),
                ask=float(q.ask_price),
                mid=float((q.bid_price + q.ask_price) / 2),
                spread=float(q.ask_price - q.bid_price),
                vwap=0,  # Will be calculated separately if needed
                timestamp=q.timestamp
            )
    except Exception as e:
        logger.debug(f"Alpaca quote failed for {ticker}: {e}")
    
    # Fallback: use yfinance
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        
        # Get current price and estimate spread (yfinance doesn't give bid/ask)
        current_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
        if current_price:
            # Estimate spread as 0.1% of price (realistic for most stocks)
            spread = current_price * 0.001
            bid = current_price - spread / 2
            ask = current_price + spread / 2
            
            return QuoteData(
                symbol=ticker,
                bid=bid,
                ask=ask,
                mid=current_price,
                spread=spread,
                vwap=current_price,
                timestamp=datetime.now(timezone.utc)
            )
    except Exception as e:
        logger.debug(f"YFinance quote also failed for {ticker}: {e}")
    
    return None


def get_vwap(ticker: str) -> Optional[float]:
    """
    Calculate VWAP from intraday bars.
    
    Args:
        ticker: Stock symbol
        
    Returns:
        VWAP value or None if unavailable
    """
    try:
        from alpaca.data import StockBarsRequest, TimeFrame
        from src.core.smart_bot import SmartTradingBot
        from datetime import timedelta
        
        bot = SmartTradingBot()
        
        # Get today's intraday data
        request = StockBarsRequest(
            symbol_or_symbols=[ticker],
            timeframe=TimeFrame.Minute,
            start=datetime.now() - timedelta(hours=5),
            end=datetime.now()
        )
        bars = bot.data_client.get_stock_bars(request)
        
        if ticker not in bars.data or not bars.data[ticker]:
            return None
            
        day_bars = bars.data[ticker]
        
        # Calculate VWAP
        total_pv = sum(bar.close * bar.volume for bar in day_bars)
        total_v = sum(bar.volume for bar in day_bars)
        
        if total_v > 0:
            return total_pv / total_v
            
        return None
        
    except Exception as e:
        logger.debug(f"Failed to get VWAP for {ticker}: {e}")
        return None


def calculate_limit_price(ticker: str, side: str, strategy: str = "vwap_discount") -> Optional[Dict]:
    """
    Calculate optimal limit price based on strategy.
    
    Args:
        ticker: Stock symbol
        side: 'buy' or 'sell'
        strategy: Strategy name (default: 'vwap_discount')
        
    Returns:
        Dict with limit_price, bid, ask, spread, vwap, mid
    """
    # Get quote data
    quote = get_latest_quote(ticker)
    if quote is None:
        logger.warning(f"Could not get quote for {ticker}, cannot calculate limit price")
        return None
    
    # Get VWAP
    vwap = get_vwap(ticker)
    if vwap is None:
        # Fallback: use mid price as VWAP proxy
        vwap = quote.mid
        logger.debug(f"Using mid price as VWAP proxy for {ticker}")
    
    # Calculate limit price based on strategy
    if side.lower() == 'buy':
        # BUY: bid + 0.01, but never below bid and never above VWAP discount
        # Must be >= bid (can't bid below bid) and <= vwap * 0.999
        raw_limit = min(quote.bid + 0.01, vwap * 0.999)
        limit_price = max(quote.bid, raw_limit)  # Ensure at least bid
    elif side.lower() == 'sell':
        # SELL: ask - 0.01, but never above ask and never below VWAP premium
        # Must be <= ask and >= vwap * 1.001
        raw_limit = max(quote.ask - 0.01, vwap * 1.001)
        limit_price = min(quote.ask, raw_limit)  # Ensure at most ask
    else:
        logger.error(f"Invalid side: {side}")
        return None
    
    result = {
        'limit_price': round(limit_price, 2),
        'bid': round(quote.bid, 2),
        'ask': round(quote.ask, 2),
        'spread': round(quote.spread, 2),
        'vwap': round(vwap, 2),
        'mid': round(quote.mid, 2),
        'timestamp': datetime.now(timezone.utc)
    }
    
    logger.info(f"📊 Limit price for {ticker} {side}: ${result['limit_price']} "
                f"(bid: ${result['bid']}, ask: ${result['ask']}, vwap: ${result['vwap']})")
    
    return result


def place_limit_order(ticker: str, qty: int, side: str, 
                     limit_price: float, time_in_force: str = "day") -> Optional[Dict]:
    """
    Place a limit order via Alpaca API.
    
    Args:
        ticker: Stock symbol
        qty: Number of shares
        side: 'buy' or 'sell'
        limit_price: Limit price
        time_in_force: Order time in force (default: 'day')
        
    Returns:
        Order dict from Alpaca or None on failure
    """
    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        from src.core.smart_bot import SmartTradingBot
        
        bot = SmartTradingBot()
        
        order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force.lower() == 'day' else TimeInForce.GTC
        
        order_request = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            limit_price=limit_price,
            time_in_force=tif
        )
        
        order = bot.trading_client.submit_order(order_request)
        
        result = {
            'id': order.id,
            'symbol': order.symbol,
            'side': str(order.side).split('.')[-1].lower(),
            'qty': order.qty,
            'limit_price': float(order.limit_price) if order.limit_price else None,
            'filled_qty': order.filled_qty,
            'status': str(order.status).split('.')[-1].lower(),
            'created_at': order.created_at.isoformat() if order.created_at else None
        }
        
        logger.info(f"✅ Limit order placed: {ticker} {side} {qty} @ ${limit_price} "
                    f"(status: {result['status']})")
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to place limit order for {ticker}: {e}")
        return None


def place_market_order(ticker: str, qty: int, side: str) -> Optional[Dict]:
    """
    Place a market order as fallback.
    
    Args:
        ticker: Stock symbol
        qty: Number of shares
        side: 'buy' or 'sell'
        
    Returns:
        Order dict from Alpaca or None on failure
    """
    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        from src.core.smart_bot import SmartTradingBot
        
        bot = SmartTradingBot()
        
        order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
        
        order_request = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY
        )
        
        order = bot.trading_client.submit_order(order_request)
        
        result = {
            'id': order.id,
            'symbol': order.symbol,
            'side': str(order.side).split('.')[-1].lower(),
            'qty': order.qty,
            'filled_qty': order.filled_qty,
            'status': str(order.status).split('.')[-1].lower(),
            'created_at': order.created_at.isoformat() if order.created_at else None
        }
        
        logger.info(f"✅ Market order placed (fallback): {ticker} {side} {qty}")
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to place market order for {ticker}: {e}")
        return None


def cancel_order(order_id: str) -> bool:
    """
    Cancel an order.
    
    Args:
        order_id: Alpaca order ID
        
    Returns:
        True if cancelled successfully
    """
    try:
        from src.core.smart_bot import SmartTradingBot
        bot = SmartTradingBot()
        bot.trading_client.cancel_order(order_id)
        logger.info(f"✅ Order cancelled: {order_id}")
        return True
    except Exception as e:
        logger.warning(f"Failed to cancel order {order_id}: {e}")
        return False


def get_order_status(order_id: str) -> Optional[Dict]:
    """
    Get current order status.
    
    Args:
        order_id: Alpaca order ID
        
    Returns:
        Order dict or None
    """
    try:
        from src.core.smart_bot import SmartTradingBot
        bot = SmartTradingBot()
        order = bot.trading_client.get_order(order_id)
        
        return {
            'id': order.id,
            'status': str(order.status).split('.')[-1].lower(),
            'filled_qty': order.filled_qty,
            'filled_avg_price': float(order.filled_avg_price) if order.filled_avg_price else None,
            'limit_price': float(order.limit_price) if order.limit_price else None,
        }
    except Exception as e:
        logger.debug(f"Failed to get order status for {order_id}: {e}")
        return None


def monitor_order_fill(order_id: str, timeout_minutes: int = 30, 
                      mid_at_signal: float = None) -> Dict:
    """
    Monitor an order until filled or timeout.
    
    Args:
        order_id: Alpaca order ID
        timeout_minutes: Max time to wait (default: 30)
        mid_at_signal: Mid price when signal triggered (for slippage calculation)
        
    Returns:
        Dict with status, fill_price, savings_vs_market
    """
    start_time = datetime.now(timezone.utc)
    check_interval = 60  # Check every 60 seconds
    max_checks = timeout_minutes * 60 // check_interval
    
    logger.info(f"🔄 Monitoring order {order_id} (timeout: {timeout_minutes} min)")
    
    for check_num in range(max_checks):
        # Check order status
        status = get_order_status(order_id)
        if status is None:
            time.sleep(check_interval)
            continue
        
        order_status = status['status']
        
        # Order filled
        if order_status in ['filled', 'partially_filled']:
            fill_price = status.get('filled_avg_price') or status.get('limit_price')
            
            # Calculate savings vs market
            savings = 0
            if mid_at_signal and fill_price:
                # For BUY: market would have been at ask (mid + spread/2)
                # We got limit_price which should be lower
                savings = mid_at_signal - fill_price
            
            logger.info(f"✅ Order filled: {order_id} @ ${fill_price}")
            
            return {
                'status': 'filled',
                'fill_price': fill_price,
                'fill_time_seconds': (datetime.now(timezone.utc) - start_time).total_seconds(),
                'savings_vs_market': savings,
                'order_id': order_id
            }
        
        # Order cancelled or rejected
        elif order_status in ['cancelled', 'rejected', 'expired']:
            logger.info(f"⚠️ Order {order_id} {order_status}")
            return {
                'status': order_status,
                'fill_price': None,
                'fill_time_seconds': (datetime.now(timezone.utc) - start_time).total_seconds(),
                'savings_vs_market': 0,
                'order_id': order_id
            }
        
        # Still pending
        time.sleep(check_interval)
    
    # Timeout - check if we should cancel and replace or go market
    status = get_order_status(order_id)
    if status and status.get('filled_qty', 0) > 0:
        # Partially filled
        return {
            'status': 'partial',
            'fill_price': status.get('filled_avg_price'),
            'fill_time_seconds': timeout_minutes * 60,
            'savings_vs_market': 0,
            'order_id': order_id
        }
    
    # Timeout with no fill - get current price to decide
    # This is a fallback scenario - in real use we'd check price movement
    logger.warning(f"⏱️ Order {order_id} timed out after {timeout_minutes} minutes")
    
    return {
        'status': 'timeout',
        'fill_price': None,
        'fill_time_seconds': timeout_minutes * 60,
        'savings_vs_market': 0,
        'order_id': order_id
    }


def log_order_execution(order_data: Dict, db=None) -> bool:
    """
    Log order execution to Supabase.
    
    Args:
        order_data: Order details dict
        db: Supabase client (optional)
        
    Returns:
        True if logged successfully
    """
    try:
        if db is None:
            from src.database.simple_rest import SimpleSupabaseREST
            db = SimpleSupabaseREST()
        
        if not db.is_available():
            logger.debug("Database not available, skipping order log")
            return False
        
        record = {
            'ticker': order_data.get('symbol', ''),
            'side': order_data.get('side', ''),
            'signal_time': order_data.get('signal_time'),
            'order_type': order_data.get('order_type', 'limit'),
            'limit_price': order_data.get('limit_price'),
            'fill_price': order_data.get('fill_price'),
            'mid_at_signal': order_data.get('mid_at_signal'),
            'vwap_at_signal': order_data.get('vwap_at_signal'),
            'spread_at_signal': order_data.get('spread_at_signal'),
            'savings_vs_market': order_data.get('savings_vs_market'),
            'fill_time_seconds': order_data.get('fill_time_seconds'),
            'status': order_data.get('status', 'unknown'),
        }
        
        import requests
        response = requests.post(
            f"{db.rest_url}/order_execution_log",
            headers=db.headers,
            json=record,
            timeout=10
        )
        
        if response.status_code in [200, 201]:
            logger.debug(f"Order execution logged to database")
            return True
        else:
            logger.warning(f"Failed to log order: {response.status_code}")
            return False
            
    except Exception as e:
        logger.debug(f"Error logging order: {e}")
        return False


# Convenience function for full limit order flow
def execute_smart_order(ticker: str, qty: int, side: str, 
                       timeout_minutes: int = 30) -> Dict:
    """
    Execute a smart limit order with full flow.
    
    1. Calculate limit price
    2. Place limit order
    3. Monitor for fill
    4. Fallback to market if needed
    5. Log to database
    
    Args:
        ticker: Stock symbol
        qty: Number of shares
        side: 'buy' or 'sell'
        timeout_minutes: Max time to wait for fill
        
    Returns:
        Dict with execution details
    """
    signal_time = datetime.now(timezone.utc)
    
    # Step 1: Calculate limit price
    price_data = calculate_limit_price(ticker, side)
    if price_data is None:
        logger.error(f"Cannot calculate limit price for {ticker}, using market order")
        order = place_market_order(ticker, qty, side)
        return {
            'status': 'market_fallback',
            'reason': 'limit_price_calculation_failed',
            'order': order
        }
    
    mid_at_signal = price_data['mid']
    
    # Step 2: Place limit order
    order = place_limit_order(ticker, qty, side, price_data['limit_price'])
    if order is None:
        logger.error(f"Failed to place limit order, using market order")
        order = place_market_order(ticker, qty, side)
        return {
            'status': 'market_fallback',
            'reason': 'limit_order_placement_failed',
            'order': order
        }
    
    # Step 3: Monitor for fill
    fill_result = monitor_order_fill(
        order['id'], 
        timeout_minutes=timeout_minutes,
        mid_at_signal=mid_at_signal
    )
    
    # Step 4: Handle timeout - decide whether to cancel or go market
    if fill_result['status'] == 'timeout':
        # Get current price to decide
        current = get_latest_quote(ticker)
        if current:
            price_move = abs(current.mid - price_data['limit_price']) / current.mid
            if price_move > 0.005:  # Moved more than 0.5%
                # Cancel and go market
                cancel_order(order['id'])
                market_order = place_market_order(ticker, qty, side)
                fill_result['status'] = 'market_fallback'
                fill_result['market_order_id'] = market_order['id'] if market_order else None
                logger.info(f"🔄 Price moved {price_move:.2%}, went to market")
            else:
                # Price still close, extend order (would need replace logic)
                logger.info(f"📊 Price close to limit, keeping order")
    
    # Step 5: Log to database
    log_data = {
        'symbol': ticker,
        'side': side,
        'signal_time': signal_time.isoformat(),
        'order_type': 'limit' if fill_result['status'] != 'market_fallback' else 'market',
        'limit_price': price_data['limit_price'],
        'fill_price': fill_result.get('fill_price'),
        'mid_at_signal': mid_at_signal,
        'vwap_at_signal': price_data.get('vwap'),
        'spread_at_signal': price_data.get('spread'),
        'savings_vs_market': fill_result.get('savings_vs_market', 0),
        'fill_time_seconds': fill_result.get('fill_time_seconds', 0),
        'status': fill_result['status'],
    }
    log_order_execution(log_data)
    
    return {
        'limit_order': order,
        'fill_result': fill_result,
        'price_data': price_data
    }
