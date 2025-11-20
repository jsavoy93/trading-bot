import os
import time
import logging
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

# Database imports
from data_manager import data_manager
from models import TradingSessionCreate, TradeCreate, MarketDataCreate, ErrorLogCreate
from database import init_database
from migrations import run_migrations
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass, AssetStatus
from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Load .env from current directory
load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")

if not ALPACA_API_KEY or not ALPACA_API_SECRET:
    raise RuntimeError("Missing Alpaca API keys. Check your .env file.")

# Configuration
BAR_INTERVAL = "1Hour"  # Options: "5Min", "15Min", "1Hour", "1Day"
SLEEP_SECONDS = 60
MAX_SYMBOLS_FOR_TESTING = 100  # Set to None to use all symbols
RATE_LIMIT_DELAY = 1  # Seconds to wait every 10 symbols to avoid rate limiting

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

trading_client = TradingClient(
    ALPACA_API_KEY,
    ALPACA_API_SECRET,
    paper=True
)

data_client = StockHistoricalDataClient(
    ALPACA_API_KEY,
    ALPACA_API_SECRET
)

def get_all_us_symbols() -> list:
    """Fetch all tradeable US stock symbols from Alpaca"""
    try:
        assets_request = GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE
        )
        assets = trading_client.get_all_assets(assets_request)
        
        # Filter for stocks that are tradeable and fractionable (generally more liquid)
        symbols = [
            asset.symbol for asset in assets 
            if asset.tradable and asset.fractionable and not asset.symbol.endswith('.WS')
        ]
        
        logging.info(f"Found {len(symbols)} tradeable US equity symbols")
        return symbols
    except Exception as e:
        logging.error(f"Error fetching symbols: {e}")
        # Fallback to a smaller list if API fails
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META", "SPY", "QQQ", "IWM"]

def get_alpaca_bars(symbol: str, timeframe: str = "1Hour") -> pd.DataFrame:
    """Fetch historical bar data from Alpaca"""
    from datetime import datetime, timedelta
    import pytz
    
    # Convert timeframe string to Alpaca TimeFrame
    if timeframe == "60min" or timeframe == "1Hour":
        tf = TimeFrame.Hour
    elif timeframe == "1Day":
        tf = TimeFrame.Day
    elif timeframe == "5Min":
        tf = TimeFrame.Minute * 5
    elif timeframe == "15Min":
        tf = TimeFrame.Minute * 15
    else:
        tf = TimeFrame.Hour  # Default to 1 hour
    
    # Get data for the last several days (more than needed to account for weekends/holidays)
    us_eastern = pytz.timezone('US/Eastern')
    end_time = datetime.now(us_eastern)
    start_time = end_time - timedelta(days=60)  # Go back further to ensure we get enough data
    
    request_params = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=tf,
        start=start_time,
        end=end_time
    )
    
    bars = data_client.get_stock_bars(request_params)
    
    # Debug logging
    logging.debug(f"Data request for {symbol}: {start_time} to {end_time}")
    
    # Check if we got data back
    if not bars or not hasattr(bars, 'data') or symbol not in bars.data:
        raise RuntimeError(f"No data returned for symbol {symbol}")
    
    symbol_bars = bars.data[symbol]
    if not symbol_bars or len(symbol_bars) == 0:
        raise RuntimeError(f"No bars returned for symbol {symbol}")
    
    # Convert to DataFrame
    data = []
    for bar in symbol_bars:
        data.append({
            'timestamp': bar.timestamp,
            'open': float(bar.open),
            'high': float(bar.high),
            'low': float(bar.low),
            'close': float(bar.close),
            'volume': int(bar.volume)
        })
    
    df = pd.DataFrame(data)
    df.set_index('timestamp', inplace=True)
    df = df.sort_index()
    
    # Ensure we have enough data for indicators (at least 30 bars for SMA)
    if len(df) < 30:
        raise RuntimeError(f"Insufficient data for {symbol}: only {len(df)} bars available")
    
    return df

def compute_simple_signals(df: pd.DataFrame) -> dict:
    df["sma_fast"] = df["close"].rolling(10).mean()
    df["sma_slow"] = df["close"].rolling(30).mean()

    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    last = df.iloc[-1]
    signal = "HOLD"

    if last["sma_fast"] > last["sma_slow"] and last["rsi"] < 70:
        signal = "BUY"
    if last["sma_fast"] < last["sma_slow"] or last["rsi"] > 75:
        signal = "SELL"

    return {
        "signal": signal,
        "price": last["close"],
        "sma_fast": last["sma_fast"],
        "sma_slow": last["sma_slow"],
        "rsi": last["rsi"]
    }

def get_positions():
    return {p.symbol: p for p in trading_client.get_all_positions()}

def submit_market_order(symbol: str, side: OrderSide, qty: int):
    """Submit a market order and return the order object"""
    try:
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        order = trading_client.submit_order(order_data=req)
        logging.info(f"Submitted {side} order: {order}")
        return order
    except Exception as e:
        logging.error(f"Failed to submit order for {symbol}: {e}")
        return None

def main_loop():
    # Initialize database (optional)
    database_available = False
    session_id = None
    
    logging.info("Checking database connection...")
    try:
        if init_database():
            logging.info("Database connection successful")
            
            # Run migrations
            logging.info("Running database migrations...")
            if run_migrations():
                logging.info("Database migrations completed")
                database_available = True
            else:
                logging.warning("Database migrations failed - continuing without database logging")
        else:
            logging.warning("Database initialization failed - continuing without database logging")
    except Exception as e:
        logging.warning(f"Database setup failed: {e} - continuing without database logging")
    
    # Start trading session (if database is available)
    if database_available:
        session_data = TradingSessionCreate(
            bot_version="1.0.0",
            configuration={
                "bar_interval": BAR_INTERVAL,
                "sleep_seconds": SLEEP_SECONDS,
                "max_symbols": MAX_SYMBOLS_FOR_TESTING,
                "rate_limit_delay": RATE_LIMIT_DELAY
            },
            is_paper_trading=True,  # Always start in paper mode for safety
            notes=f"Automated trading session started at {datetime.now()}"
        )
        
        session_id = data_manager.start_trading_session(session_data)
        if session_id:
            logging.info(f"Started trading session {session_id} with database logging")
        else:
            logging.warning("Failed to start database session - continuing without database logging")
            database_available = False
    else:
        logging.info("Starting trading bot without database logging")
    
    # Get all US market symbols at the start
    all_symbols = get_all_us_symbols()
    
    # Optional: Limit to a subset for testing
    if MAX_SYMBOLS_FOR_TESTING:
        all_symbols = all_symbols[:MAX_SYMBOLS_FOR_TESTING]
    
    logging.info(f"Trading bot will monitor {len(all_symbols)} symbols")
    
    while True:
        try:
            logging.info(f"--- Cycle start - Processing {len(all_symbols)} symbols ---")
            positions = get_positions()
            
            processed_count = 0
            error_count = 0

            for symbol in all_symbols:
                try:
                    df = get_alpaca_bars(symbol, BAR_INTERVAL)
                    sig = compute_simple_signals(df)

                    logging.info(
                        f"{symbol} signal={sig['signal']} "
                        f"price={sig['price']:.2f} rsi={sig['rsi']:.1f}"
                    )

                    # Log market data to database (if available)
                    if database_available and session_id and len(df) > 0:
                        latest_bar = df.iloc[-1]
                        market_data = MarketDataCreate(
                            session_id=session_id,
                            symbol=symbol,
                            timestamp=df.index[-1].to_pydatetime(),
                            open_price=float(latest_bar['open']),
                            high_price=float(latest_bar['high']),
                            low_price=float(latest_bar['low']),
                            close_price=float(latest_bar['close']),
                            volume=int(latest_bar['volume']),
                            sma_fast=float(sig.get('sma_fast', 0)),
                            sma_slow=float(sig.get('sma_slow', 0)),
                            rsi=float(sig.get('rsi', 0))
                        )
                        data_manager.log_market_data(market_data)

                    has_position = symbol in positions
                    qty = 1  # TODO: smarter sizing

                    if sig["signal"] == "BUY" and not has_position:
                        order = submit_market_order(symbol, OrderSide.BUY, qty)
                        
                        # Log trade to database (if available)
                        if database_available and session_id and order:
                            trade_data = TradeCreate(
                                session_id=session_id,
                                alpaca_order_id=str(order.id),
                                symbol=symbol,
                                side="BUY",
                                quantity=qty,
                                order_price=sig['price'],
                                signal_time=datetime.utcnow(),
                                order_time=datetime.utcnow(),
                                sma_fast=sig.get('sma_fast'),
                                sma_slow=sig.get('sma_slow'),
                                rsi=sig.get('rsi'),
                                signal_strength=sig['signal'],
                                status="PENDING",
                                market_conditions={
                                    "volatility": "normal",  # TODO: calculate actual volatility
                                    "trend": "bullish" if sig['signal'] == "BUY" else "bearish"
                                }
                            )
                            data_manager.log_trade(trade_data)
                            
                    elif sig["signal"] == "SELL" and has_position:
                        current_qty = abs(int(float(positions[symbol].qty)))
                        order = submit_market_order(symbol, OrderSide.SELL, current_qty)
                        
                        # Log trade to database (if available)
                        if database_available and session_id and order:
                            trade_data = TradeCreate(
                                session_id=session_id,
                                alpaca_order_id=str(order.id),
                                symbol=symbol,
                                side="SELL",
                                quantity=current_qty,
                                order_price=sig['price'],
                                signal_time=datetime.utcnow(),
                                order_time=datetime.utcnow(),
                                sma_fast=sig.get('sma_fast'),
                                sma_slow=sig.get('sma_slow'),
                                rsi=sig.get('rsi'),
                                signal_strength=sig['signal'],
                                status="PENDING",
                                market_conditions={
                                    "volatility": "normal",
                                    "trend": "bullish" if sig['signal'] == "BUY" else "bearish"
                                }
                            )
                            data_manager.log_trade(trade_data)

                    processed_count += 1
                    
                    # Add small delay between API calls to avoid rate limiting
                    if processed_count % 10 == 0:
                        time.sleep(RATE_LIMIT_DELAY)  # Brief pause every 10 symbols

                except Exception as e:
                    error_count += 1
                    logging.warning(f"Error handling symbol {symbol}: {e}")
                    
                    # Log error to database (if available)
                    if database_available and session_id:
                        data_manager.log_error_from_exception(
                            session_id=session_id,
                            error_type="SYMBOL_PROCESSING_ERROR",
                            symbol=symbol,
                            exception=e,
                            context_data={
                                "bar_interval": BAR_INTERVAL,
                                "processed_count": processed_count
                            }
                        )
                    
                    # Continue with next symbol instead of stopping

            logging.info(f"Cycle complete - Processed: {processed_count}, Errors: {error_count}")

        except Exception as e:
            logging.exception(f"Cycle-level error: {e}")
            
            # Log critical error to database (if available)
            if database_available and session_id:
                data_manager.log_error_from_exception(
                    session_id=session_id,
                    error_type="CYCLE_LEVEL_ERROR",
                    symbol=None,
                    exception=e,
                    context_data={
                        "processed_count": processed_count,
                        "error_count": error_count
                    }
                )

        # Refresh symbol list every 24 hours (adjust as needed)
        # This ensures we get newly listed stocks and remove delisted ones
        if processed_count > 0 and processed_count % 1000 == 0:
            logging.info("Refreshing symbol list...")
            all_symbols = get_all_us_symbols()

        time.sleep(SLEEP_SECONDS)
    
    # End trading session when exiting (if available)
    if database_available and session_id:
        total_trades = processed_count  # Approximate
        session_pnl = 0.0  # TODO: Calculate actual PnL
        data_manager.end_trading_session(
            session_id=session_id,
            total_symbols=processed_count,
            total_trades=total_trades,
            session_pnl=session_pnl,
            error_count=error_count
        )

if __name__ == "__main__":
    main_loop()
