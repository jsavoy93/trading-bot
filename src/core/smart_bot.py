"""
Enhanced trading bot with simple REST API database integration.
Works around package conflicts by using direct HTTP requests.
"""
import os
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

# Import our simple REST API manager
import sys
from pathlib import Path
# Add the parent directory to path to access database module
sys.path.append(str(Path(__file__).parent.parent))
from database.simple_rest import simple_rest

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)

class SmartTradingBot:
    def __init__(self):
        """Initialize the smart trading bot"""
        # Alpaca API setup
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.api_secret = os.getenv("ALPACA_API_SECRET")  # Fixed variable name
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Missing Alpaca API credentials")
        
        self.api = tradeapi.REST(
            self.api_key,
            self.api_secret,
            self.base_url,
            api_version='v2'
        )
        
        # Trading parameters
        self.trade_amount = 1000  # $1000 per trade
        self.sma_fast = 10
        self.sma_slow = 30
        self.rsi_period = 14
        self.rsi_buy_threshold = 30
        self.rsi_sell_threshold = 70
        
        # Session tracking
        self.db = simple_rest
        self.session_id = None
        self.trades_executed = 0
        self.symbols_processed = 0
        self.errors_count = 0
        
        logging.info("🤖 Smart Trading Bot initialized")
        if self.db.is_available():
            logging.info("✅ Database available via REST API")
        else:
            logging.warning("⚠️  Database not available - running locally only")
    
    def show_database_setup(self):
        """Show database setup instructions"""
        if not self.db.is_available():
            print("\n" + "="*80)
            print("🔧 DATABASE SETUP INSTRUCTIONS")
            print("="*80)
            print("To enable database logging:")
            print("1. Go to https://supabase.com/dashboard")
            print("2. Select your project")
            print("3. Go to SQL Editor")
            print("4. Create a new query and run this SQL:")
            print()
            print("""-- Create trading_sessions table
CREATE TABLE IF NOT EXISTS trading_sessions (
    id SERIAL PRIMARY KEY,
    session_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_end TIMESTAMP,
    bot_version VARCHAR(50),
    configuration JSON,
    total_symbols_processed INTEGER DEFAULT 0,
    total_trades_executed INTEGER DEFAULT 0,
    session_pnl FLOAT DEFAULT 0.0,
    error_count INTEGER DEFAULT 0,
    is_paper_trading BOOLEAN DEFAULT true,
    notes TEXT
);

-- Create trades table
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES trading_sessions(id),
    alpaca_order_id VARCHAR(100) UNIQUE,
    symbol VARCHAR(10),
    side VARCHAR(10),
    quantity INTEGER,
    price FLOAT,
    order_price FLOAT,
    signal_time TIMESTAMP,
    order_time TIMESTAMP,
    fill_time TIMESTAMP,
    sma_fast FLOAT,
    sma_slow FLOAT,
    rsi FLOAT,
    signal_strength VARCHAR(20),
    status VARCHAR(20),
    pnl FLOAT,
    market_conditions JSON
);

-- Enable RLS and allow operations
ALTER TABLE trading_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all operations" ON trading_sessions FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON trades FOR ALL USING (true);""")
            print("="*80)
            print("5. After running the SQL, restart the bot")
            print("="*80)
    
    def start_session(self):
        """Start a new trading session"""
        if self.db.is_available():
            self.session_id = self.db.create_session(
                bot_version="2.1.0",
                configuration={
                    "sma_fast": self.sma_fast,
                    "sma_slow": self.sma_slow,
                    "rsi_period": self.rsi_period,
                    "trade_amount": self.trade_amount
                },
                is_paper_trading=True,
                notes="Smart bot with simple REST API"
            )
        
        logging.info(f"🚀 Started session {self.session_id or 'LOCAL-MODE'}")
    
    def end_session(self):
        """End the current trading session"""
        if self.db.is_available() and self.session_id:
            self.db.update_session(self.session_id, {
                "session_end": datetime.utcnow().isoformat(),
                "total_symbols_processed": self.symbols_processed,
                "total_trades_executed": self.trades_executed,
                "error_count": self.errors_count
            })
        
        logging.info(f"🏁 Session ended: {self.symbols_processed} symbols, {self.trades_executed} trades")
    
    def get_all_us_symbols(self) -> List[str]:
        """Get all tradeable US stock symbols"""
        try:
            assets = self.api.list_assets(status='active', asset_class='us_equity')
            symbols = [asset.symbol for asset in assets if asset.tradable and asset.shortable]
            logging.info(f"📊 Retrieved {len(symbols)} tradeable symbols")
            return symbols
        except Exception as e:
            logging.error(f"❌ Failed to get symbols: {e}")
            return []
    
    def get_market_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Get market data for analysis"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=100)
            
            barset = self.api.get_bars(
                symbol,
                tradeapi.TimeFrame.Day,
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d')
            )
            
            if not barset or len(barset) == 0:
                return None
            
            df = pd.DataFrame([{
                'timestamp': bar.t,
                'open': bar.o,
                'high': bar.h,
                'low': bar.l,
                'close': bar.c,
                'volume': bar.v
            } for bar in barset])
            
            return df
            
        except Exception as e:
            logging.debug(f"Data fetch failed for {symbol}: {e}")
            return None
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators"""
        if len(df) < max(self.sma_slow, self.rsi_period):
            return df
        
        # SMAs
        df[f'SMA_{self.sma_fast}'] = df['close'].rolling(window=self.sma_fast).mean()
        df[f'SMA_{self.sma_slow}'] = df['close'].rolling(window=self.sma_slow).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        return df
    
    def analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """Analyze symbol for trading opportunities"""
        try:
            df = self.get_market_data(symbol)
            if df is None or len(df) < self.sma_slow:
                return None
            
            df = self.calculate_indicators(df)
            latest = df.iloc[-1]
            
            if pd.isna(latest[f'SMA_{self.sma_fast}']) or pd.isna(latest['RSI']):
                return None
            
            sma_fast = latest[f'SMA_{self.sma_fast}']
            sma_slow = latest[f'SMA_{self.sma_slow}']
            rsi = latest['RSI']
            price = latest['close']
            
            signal = None
            signal_strength = "WEAK"
            
            # Trading logic
            if sma_fast > sma_slow and rsi < self.rsi_buy_threshold:
                signal = "BUY"
                signal_strength = "STRONG" if rsi < 25 else "MEDIUM"
            elif sma_fast < sma_slow and rsi > self.rsi_sell_threshold:
                signal = "SELL"
                signal_strength = "STRONG" if rsi > 75 else "MEDIUM"
            
            return {
                'symbol': symbol,
                'price': price,
                'sma_fast': sma_fast,
                'sma_slow': sma_slow,
                'rsi': rsi,
                'signal': signal,
                'signal_strength': signal_strength,
                'timestamp': latest['timestamp']
            }
            
        except Exception as e:
            logging.debug(f"Analysis failed for {symbol}: {e}")
            self.errors_count += 1
            return None
    
    def execute_trade(self, analysis: Dict) -> bool:
        """Execute trade based on analysis"""
        try:
            symbol = analysis['symbol']
            signal = analysis['signal']
            price = analysis['price']
            
            if not signal:
                return False
            
            quantity = int(self.trade_amount / price)
            if quantity <= 0:
                return False
            
            side = 'buy' if signal == 'BUY' else 'sell'
            
            order = self.api.submit_order(
                symbol=symbol,
                qty=quantity,
                side=side,
                type='market',
                time_in_force='day'
            )
            
            # Log to database if available
            if self.db.is_available() and self.session_id:
                trade_data = {
                    'session_id': self.session_id,
                    'alpaca_order_id': order.id,
                    'symbol': symbol,
                    'side': side.upper(),
                    'quantity': quantity,
                    'order_price': price,
                    'signal_time': datetime.fromisoformat(str(analysis['timestamp']).replace('Z', '+00:00')).isoformat(),
                    'order_time': datetime.utcnow().isoformat(),
                    'sma_fast': analysis['sma_fast'],
                    'sma_slow': analysis['sma_slow'],
                    'rsi': analysis['rsi'],
                    'signal_strength': analysis['signal_strength'],
                    'status': 'SUBMITTED'
                }
                self.db.log_trade(self.session_id, trade_data)
            
            self.trades_executed += 1
            logging.info(f"✅ {signal} {quantity} {symbol} @ ${price:.2f} (Order: {order.id})")
            return True
            
        except Exception as e:
            logging.error(f"❌ Trade failed for {analysis['symbol']}: {e}")
            self.errors_count += 1
            return False
    
    def run_analysis(self, max_symbols: int = 50, max_trades: int = 3):
        """Run trading analysis"""
        logging.info(f"🔍 Starting analysis (max {max_symbols} symbols, max {max_trades} trades)")
        
        symbols = self.get_all_us_symbols()
        if not symbols:
            logging.error("❌ No symbols available")
            return
        
        symbols = symbols[:max_symbols]
        trades_executed = 0
        opportunities = 0
        
        for i, symbol in enumerate(symbols, 1):
            try:
                self.symbols_processed += 1
                
                if i % 10 == 0:
                    logging.info(f"📊 Progress: {i}/{len(symbols)} symbols, {opportunities} opportunities, {trades_executed} trades")
                
                analysis = self.analyze_symbol(symbol)
                if analysis and analysis['signal']:
                    opportunities += 1
                    logging.info(f"📈 {analysis['signal']} signal: {symbol} RSI={analysis['rsi']:.1f} Price=${analysis['price']:.2f}")
                    
                    if trades_executed < max_trades:
                        if self.execute_trade(analysis):
                            trades_executed += 1
                        time.sleep(1)  # Rate limiting
                    else:
                        logging.info(f"⚠️  Max trades reached ({max_trades})")
                
                time.sleep(0.1)  # API rate limiting
                
            except Exception as e:
                logging.error(f"❌ Error with {symbol}: {e}")
                self.errors_count += 1
        
        logging.info(f"🏁 Analysis complete: {opportunities} opportunities, {trades_executed} trades executed")
    
    def show_database_status(self):
        """Show database status and recent sessions"""
        if not self.db.is_available():
            logging.info("📊 No database connection - running in local mode only")
            return
        
        # Get database info
        db_info = self.db.get_database_info()
        
        print("\n" + "="*60)
        print("📊 DATABASE STATUS")
        print("="*60)
        print(f"Connection: {'✅ Connected' if db_info['available'] else '❌ Disconnected'}")
        print(f"Tables: {'✅ Exist' if db_info['tables_exist'] else '❌ Missing'}")
        print(f"Schema Version: {db_info['schema_version'] or 'Unknown'}")
        print(f"Total Sessions: {db_info['total_sessions']}")
        print(f"Total Trades: {db_info['total_trades']}")
        
        if db_info['tables_exist']:
            sessions = self.db.get_sessions(5)
            if sessions:
                print("\n📈 RECENT SESSIONS:")
                for session in sessions:
                    start_time = session.get('session_start', 'Unknown')
                    trades = session.get('total_trades_executed', 0)
                    symbols = session.get('total_symbols_processed', 0)
                    pnl = session.get('session_pnl', 0.0)
                    print(f"  Session {session['id']}: {start_time[:19]} | {trades} trades | {symbols} symbols | P&L: ${pnl:.2f}")
            else:
                print("\n📈 No sessions yet")
        
        print("="*60)

def main():
    """Main bot execution"""
    try:
        bot = SmartTradingBot()
        
        # Show setup instructions if database not available
        bot.show_database_setup()
        
        # Show database status
        bot.show_database_status()
        
        # Start session
        bot.start_session()
        
        try:
            # Run analysis
            bot.run_analysis(max_symbols=30, max_trades=2)
        finally:
            bot.end_session()
        
        # Show final summary
        bot.show_database_status()
        
    except KeyboardInterrupt:
        logging.info("🛑 Bot stopped by user")
    except Exception as e:
        logging.error(f"❌ Bot failed: {e}")
        raise

if __name__ == "__main__":
    main()