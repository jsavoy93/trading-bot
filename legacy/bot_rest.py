"""
Enhanced bot with Supabase REST API integration.
This version works around network connectivity issues by using HTTPS instead of direct PostgreSQL.
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

# Import our REST API database manager
from supabase_rest import rest_manager

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

class EnhancedTradingBot:
    def __init__(self):
        """Initialize the trading bot with REST API database support"""
        # Alpaca API setup
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.api_secret = os.getenv("ALPACA_SECRET_KEY")
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
        
        # Database integration
        self.db = rest_manager
        self.session_id = None
        self.trades_executed = 0
        self.symbols_processed = 0
        self.errors_count = 0
        
        logging.info("🤖 Enhanced Trading Bot initialized")
        if self.db.is_available():
            logging.info("✅ Database REST API available")
        else:
            logging.warning("⚠️  Database REST API not available - running without persistence")
    
    def start_session(self):
        """Start a new trading session"""
        if self.db.is_available():
            self.session_id = self.db.start_trading_session(
                bot_version="2.0.0",
                configuration={
                    "sma_fast": self.sma_fast,
                    "sma_slow": self.sma_slow,
                    "rsi_period": self.rsi_period,
                    "rsi_buy_threshold": self.rsi_buy_threshold,
                    "rsi_sell_threshold": self.rsi_sell_threshold,
                    "trade_amount": self.trade_amount
                },
                is_paper_trading=True,
                notes="Enhanced bot with REST API database integration"
            )
        
        logging.info(f"🚀 Started trading session {self.session_id or 'LOCAL'}")
    
    def end_session(self):
        """End the current trading session"""
        if self.db.is_available() and self.session_id:
            # Calculate basic P&L from logged trades if available
            session_pnl = 0.0  # We'd need to calculate this from filled orders
            
            self.db.end_trading_session(
                self.session_id,
                total_symbols=self.symbols_processed,
                total_trades=self.trades_executed,
                session_pnl=session_pnl,
                error_count=self.errors_count
            )
        
        logging.info(f"🏁 Ended trading session. Processed {self.symbols_processed} symbols, executed {self.trades_executed} trades")
    
    def get_all_us_symbols(self) -> List[str]:
        """Get all tradeable US stock symbols from Alpaca"""
        try:
            assets = self.api.list_assets(status='active', asset_class='us_equity')
            symbols = [asset.symbol for asset in assets if asset.tradable and asset.shortable]
            logging.info(f"📊 Retrieved {len(symbols)} tradeable US symbols")
            return symbols
        except Exception as e:
            logging.error(f"❌ Failed to get symbols: {e}")
            return []
    
    def get_market_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Get market data for a symbol"""
        try:
            # Get historical data for the last 100 days
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
            
            # Convert to DataFrame
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
            logging.debug(f"Failed to get data for {symbol}: {e}")
            return None
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators"""
        if len(df) < max(self.sma_slow, self.rsi_period):
            return df
        
        # Simple Moving Averages
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
        """Analyze a symbol for trading opportunities"""
        try:
            df = self.get_market_data(symbol)
            if df is None or len(df) < self.sma_slow:
                return None
            
            df = self.calculate_indicators(df)
            latest = df.iloc[-1]
            
            # Check for valid indicators
            if pd.isna(latest[f'SMA_{self.sma_fast}']) or pd.isna(latest[f'SMA_{self.sma_slow}']) or pd.isna(latest['RSI']):
                return None
            
            sma_fast = latest[f'SMA_{self.sma_fast}']
            sma_slow = latest[f'SMA_{self.sma_slow}']
            rsi = latest['RSI']
            current_price = latest['close']
            
            signal = None
            signal_strength = "WEAK"
            
            # Enhanced signal logic
            if sma_fast > sma_slow and rsi < self.rsi_buy_threshold:
                signal = "BUY"
                signal_strength = "STRONG" if rsi < 25 else "MEDIUM"
            elif sma_fast < sma_slow and rsi > self.rsi_sell_threshold:
                signal = "SELL"
                signal_strength = "STRONG" if rsi > 75 else "MEDIUM"
            
            return {
                'symbol': symbol,
                'current_price': current_price,
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
        """Execute a trade based on analysis"""
        try:
            symbol = analysis['symbol']
            signal = analysis['signal']
            current_price = analysis['current_price']
            
            if not signal or signal not in ['BUY', 'SELL']:
                return False
            
            # Calculate quantity based on trade amount
            quantity = int(self.trade_amount / current_price)
            if quantity <= 0:
                return False
            
            # Determine side
            side = 'buy' if signal == 'BUY' else 'sell'
            
            # Submit order
            order = self.api.submit_order(
                symbol=symbol,
                qty=quantity,
                side=side,
                type='market',
                time_in_force='day'
            )
            
            # Log the trade
            if self.db.is_available() and self.session_id:
                self.db.log_trade(
                    session_id=self.session_id,
                    alpaca_order_id=order.id,
                    symbol=symbol,
                    side=side.upper(),
                    quantity=quantity,
                    order_price=current_price,
                    signal_time=datetime.fromisoformat(str(analysis['timestamp']).replace('Z', '+00:00')),
                    order_time=datetime.utcnow(),
                    sma_fast=analysis['sma_fast'],
                    sma_slow=analysis['sma_slow'],
                    rsi=analysis['rsi'],
                    signal_strength=analysis['signal_strength'],
                    status="SUBMITTED",
                    market_conditions={
                        'signal': signal,
                        'price': current_price,
                        'trade_amount': self.trade_amount
                    }
                )
            
            self.trades_executed += 1
            logging.info(f"✅ {signal} order submitted for {quantity} shares of {symbol} at ~${current_price:.2f} (Order ID: {order.id})")
            return True
            
        except APIError as e:
            logging.error(f"❌ Alpaca API error for {analysis['symbol']}: {e}")
            self.errors_count += 1
            return False
        except Exception as e:
            logging.error(f"❌ Trade execution failed for {analysis['symbol']}: {e}")
            self.errors_count += 1
            return False
    
    def run_analysis(self, max_symbols: int = 100, max_trades: int = 5):
        """Run the trading analysis"""
        logging.info(f"🔍 Starting analysis (max {max_symbols} symbols, max {max_trades} trades)")
        
        symbols = self.get_all_us_symbols()
        if not symbols:
            logging.error("❌ No symbols available for analysis")
            return
        
        # Limit symbols for this run
        symbols = symbols[:max_symbols]
        trades_executed = 0
        opportunities_found = 0
        
        for i, symbol in enumerate(symbols, 1):
            try:
                self.symbols_processed += 1
                
                if i % 10 == 0:
                    logging.info(f"📊 Processed {i}/{len(symbols)} symbols, found {opportunities_found} opportunities, executed {trades_executed} trades")
                
                analysis = self.analyze_symbol(symbol)
                if analysis and analysis['signal']:
                    opportunities_found += 1
                    logging.info(f"📈 {analysis['signal']} signal for {symbol}: RSI={analysis['rsi']:.1f}, Price=${analysis['current_price']:.2f}")
                    
                    if trades_executed < max_trades:
                        if self.execute_trade(analysis):
                            trades_executed += 1
                        
                        # Small delay between trades
                        time.sleep(1)
                    else:
                        logging.info(f"⚠️  Max trades ({max_trades}) reached, skipping execution")
                
                # Small delay to respect rate limits
                time.sleep(0.1)
                
            except Exception as e:
                logging.error(f"❌ Error processing {symbol}: {e}")
                self.errors_count += 1
                continue
        
        logging.info(f"🏁 Analysis complete: {opportunities_found} opportunities found, {trades_executed} trades executed out of {len(symbols)} symbols")
    
    def show_performance_summary(self):
        """Show performance summary from database"""
        if not self.db.is_available():
            logging.info("📊 Database not available - no performance history")
            return
        
        try:
            summary = self.db.get_performance_summary()
            
            print("\n" + "="*50)
            print("📊 PERFORMANCE SUMMARY")
            print("="*50)
            
            if "error" in summary:
                print(f"❌ Error: {summary['error']}")
                return
            
            if "recent_sessions" in summary:
                print(f"Recent Sessions: {summary['recent_sessions']}")
                print(f"Total Trades: {summary['total_trades']}")
                print(f"Total P&L: ${summary['total_pnl']}")
                
                if "sessions" in summary and summary["sessions"]:
                    print("\nRecent Sessions:")
                    for session in summary["sessions"][:3]:
                        print(f"  Session {session['id']}: {session.get('total_trades_executed', 0)} trades, P&L: ${session.get('session_pnl', 0):.2f}")
            else:
                print("No performance data available yet")
            
            print("="*50)
            
        except Exception as e:
            logging.error(f"Failed to show performance summary: {e}")

def main():
    """Main execution function"""
    try:
        bot = EnhancedTradingBot()
        
        # Show database setup instructions if needed
        if not bot.db.is_available():
            print("\n" + "="*60)
            print("🔧 DATABASE SETUP REQUIRED")
            print("="*60)
            print("The bot is running in NO-DATABASE mode.")
            print("To enable database features:")
            print("1. Go to https://supabase.com/dashboard")
            print("2. Navigate to SQL Editor")
            print("3. Run the table creation commands shown above")
            print("="*60)
            
            # Show performance summary if available
            bot.show_performance_summary()
        
        # Start trading session
        bot.start_session()
        
        try:
            # Run analysis with limited scope for demo
            bot.run_analysis(max_symbols=50, max_trades=3)
        finally:
            # Always end session
            bot.end_session()
        
        # Show final summary
        bot.show_performance_summary()
        
    except KeyboardInterrupt:
        logging.info("🛑 Bot stopped by user")
    except Exception as e:
        logging.error(f"❌ Bot failed: {e}")
        raise

if __name__ == "__main__":
    main()