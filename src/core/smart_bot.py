"""
Enhanced trading bot with simple REST API database integration.
Works around package conflicts by using direct HTTP requests.
"""
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import pandas as pd
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# Import our simple REST API manager and AI agent
import sys
from pathlib import Path
# Add the parent directory to path to access database module
sys.path.append(str(Path(__file__).parent.parent))
from database.simple_rest import simple_rest
from analysis.ai_agent import ai_agent

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
        
        # Initialize Alpaca clients
        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=True  # Always use paper trading for safety
        )
        
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.api_secret
        )
        
        # Trading parameters
        self.trade_amount = 1000  # $1000 per trade
        self.sma_fast = 10
        self.sma_slow = 30
        self.rsi_period = 14
        self.rsi_buy_threshold = 30
        self.rsi_sell_threshold = 70
        
        # Database integration
        self.db = simple_rest
        self.session_id = None
        self.trades_executed = 0
        self.symbols_processed = 0
        self.errors_count = 0
        
        # AI integration
        self.ai = ai_agent
        
        logging.info("🤖 Smart Trading Bot initialized")
        if self.db.is_available():
            logging.info("✅ Database available via REST API")
        else:
            logging.warning("⚠️  Database not available - running locally only")
            
        if self.ai.is_configured:
            ai_config = self.ai.get_configuration_status()
            logging.info(f"🧠 AI Agent configured: {sum(ai_config.values())}/{len(ai_config)} services")
        else:
            logging.warning("⚠️  AI Agent not configured - running without advanced analysis")
    
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
                "session_end": datetime.now(timezone.utc).isoformat(),
                "total_symbols_processed": self.symbols_processed,
                "total_trades_executed": self.trades_executed,
                "error_count": self.errors_count
            })
        
        logging.info(f"🏁 Session ended: {self.symbols_processed} symbols, {self.trades_executed} trades")
    
    def get_ai_recommended_tickers(self, portfolio_analysis: Dict) -> List[str]:
        """Get AI-recommended tickers based on portfolio analysis"""
        if not self.ai.is_configured:
            logging.warning("⚠️ AI not configured, falling back to standard symbol selection")
            return self.get_all_us_symbols()[:30]
        
        try:
            # Prepare portfolio context for AI
            portfolio_context = {
                'total_value': portfolio_analysis.get('total_value', 0),
                'positions': portfolio_analysis.get('total_positions', 0),
                'cash_percentage': portfolio_analysis.get('cash_percentage', 0),
                'concentration_risk': portfolio_analysis.get('concentration_risk', 0),
                'top_holdings': portfolio_analysis.get('top_holdings', []),
                'sector_allocation': portfolio_analysis.get('sector_allocation', {}),
                'underperforming_positions': portfolio_analysis.get('underperforming_positions', []),
                'high_concentration_positions': portfolio_analysis.get('high_concentration_positions', [])
            }
            
            prompt = f"""
            Based on this portfolio analysis, recommend 30 specific stock tickers to research for potential trades.
            
            Portfolio Context:
            - Total Value: ${portfolio_context['total_value']:,.2f}
            - Number of Positions: {portfolio_context['positions']}
            - Cash Percentage: {portfolio_context['cash_percentage']:.1f}%
            - Concentration Risk: {portfolio_context['concentration_risk']:.1f}% in top 5 positions
            
            Top Holdings: {portfolio_context['top_holdings'][:5]}
            Sector Allocation: {portfolio_context['sector_allocation']}
            Underperforming Positions: {portfolio_context['underperforming_positions'][:3]}
            High Concentration Positions: {portfolio_context['high_concentration_positions']}
            
            Provide recommendations for:
            1. Diversification opportunities (if concentration risk is high)
            2. Sector rebalancing (if overweight in certain sectors)
            3. Growth opportunities (if excess cash available)
            4. Defensive positions (if portfolio needs stability)
            5. Replacement candidates (for underperforming positions)
            
            Return ONLY a JSON object with this format:
            {{
                "recommended_tickers": ["AAPL", "MSFT", "GOOGL", ...],
                "reasoning": "Brief explanation of the selection strategy",
                "focus_areas": ["diversification", "growth", "defensive", ...]
            }}
            
            Focus on liquid, well-known stocks. Avoid penny stocks or highly speculative tickers.
            """
            
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                response = loop.run_until_complete(self.ai.analyze_with_context(prompt, "portfolio_ticker_selection"))
                
                if isinstance(response, dict) and 'recommended_tickers' in response:
                    tickers = response['recommended_tickers'][:30]  # Limit to 30
                    reasoning = response.get('reasoning', 'No reasoning provided')
                    focus_areas = response.get('focus_areas', [])
                    
                    logging.info(f"🧠 AI Ticker Selection Strategy: {reasoning}")
                    logging.info(f"🎯 Focus Areas: {', '.join(focus_areas)}")
                    logging.info(f"📊 AI recommended {len(tickers)} tickers for analysis")
                    
                    return tickers
                else:
                    logging.warning("⚠️ AI returned invalid ticker recommendations, using fallback")
                    return self.get_all_us_symbols()[:30]
                    
            finally:
                loop.close()
                
        except Exception as e:
            logging.error(f"❌ Failed to get AI ticker recommendations: {e}")
            return self.get_all_us_symbols()[:30]
    
    def get_all_us_symbols(self) -> List[str]:
        """Get all tradeable US stock symbols"""
        try:
            assets = self.trading_client.get_all_assets()
            # Filter for active, tradable US equity assets
            symbols = []
            for asset in assets:
                try:
                    if (asset.tradable and 
                        str(asset.status) == 'AssetStatus.ACTIVE' and 
                        str(asset.asset_class) == 'AssetClass.US_EQUITY'):
                        symbols.append(asset.symbol)
                except Exception:
                    # If we can't check the attributes, just check tradable
                    if asset.tradable:
                        symbols.append(asset.symbol)
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
            
            request = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date
            )
            
            barset = self.data_client.get_stock_bars(request)
            
            if not barset or symbol not in barset.data:
                return None
            
            bars = barset.data[symbol]
            if not bars:
                return None
            
            df = pd.DataFrame([{
                'timestamp': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume
            } for bar in bars])
            
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
    
    def analyze_symbol(self, symbol: str, use_ai: bool = False) -> Optional[Dict]:
        """Analyze symbol for trading opportunities with optional AI enhancement"""
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
            ai_insight = None
            
            # Traditional technical analysis
            if sma_fast > sma_slow and rsi < self.rsi_buy_threshold:
                signal = "BUY"
                signal_strength = "STRONG" if rsi < 25 else "MEDIUM"
            elif sma_fast < sma_slow and rsi > self.rsi_sell_threshold:
                signal = "SELL"
                signal_strength = "STRONG" if rsi > 75 else "MEDIUM"
            
            # AI enhancement (if enabled and configured)
            if use_ai and self.ai.is_configured and signal:
                try:
                    # Get AI research (non-blocking)
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    ai_research = loop.run_until_complete(self.ai.research_symbol(symbol, lookback_days=2))
                    loop.close()
                    
                    if ai_research and ai_research.get('ai_recommendation'):
                        ai_rec = ai_research['ai_recommendation']
                        ai_signal = ai_rec.recommendation.upper()
                        
                        # Enhance signal with AI
                        if ai_signal == signal and ai_rec.confidence > 0.7:
                            signal_strength = "AI_ENHANCED"
                            ai_insight = f"AI confirms {signal} with {ai_rec.confidence:.1%} confidence"
                        elif ai_signal != signal:
                            signal_strength = "CONFLICTED"
                            ai_insight = f"AI suggests {ai_signal} vs technical {signal}"
                        
                except Exception as e:
                    logging.debug(f"AI analysis failed for {symbol}: {e}")
            
            analysis_result = {
                'symbol': symbol,
                'price': price,
                'sma_fast': sma_fast,
                'sma_slow': sma_slow,
                'rsi': rsi,
                'signal': signal,
                'signal_strength': signal_strength,
                'timestamp': latest['timestamp']
            }
            
            if ai_insight:
                analysis_result['ai_insight'] = ai_insight
            
            return analysis_result
            
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
            
            side = OrderSide.BUY if signal == 'BUY' else OrderSide.SELL
            
            market_order_data = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=side,
                time_in_force=TimeInForce.DAY
            )
            
            order = self.trading_client.submit_order(order_data=market_order_data)
            
            # Log to database if available
            if self.db.is_available() and self.session_id:
                trade_data = {
                    'session_id': self.session_id,
                    'alpaca_order_id': str(order.id) if order.id else None,
                    'symbol': symbol,
                    'side': side.upper(),
                    'quantity': quantity,
                    'order_price': price,
                    'signal_time': datetime.fromisoformat(str(analysis['timestamp']).replace('Z', '+00:00')).isoformat(),
                    'order_time': datetime.now(timezone.utc).isoformat(),
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
    
    def run_analysis(self, max_symbols: int = 50, max_trades: int = 3, use_ai: bool = False):
        """Run trading analysis with optional AI enhancement"""
        logging.info(f"🔍 Starting analysis (max {max_symbols} symbols, max {max_trades} trades, AI: {use_ai})")
        

        trades_executed = 0
        opportunities = 0
        ai_enhanced_trades = 0
        
        # Perform portfolio analysis before trading
        logging.info("📊 Pre-trading portfolio analysis...")
        portfolio_analysis = self.analyze_portfolio()
        if portfolio_analysis:
            self.execute_portfolio_actions(portfolio_analysis)
        
        # Get AI-recommended tickers based on portfolio analysis
        if portfolio_analysis and use_ai and self.ai.is_configured:
            logging.info("🧠 Getting AI-recommended tickers based on portfolio analysis...")
            symbols = self.get_ai_recommended_tickers(portfolio_analysis)
        else:
            symbols = self.get_all_us_symbols()
            symbols = symbols[:max_symbols]
        
        if not symbols:
            logging.error("❌ No symbols available")
            return
        
        # Create AI market summary if enabled
        if use_ai and self.ai.is_configured:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                market_summary = loop.run_until_complete(self.ai.create_market_summary(symbols[:5]))
                loop.close()
                
                logging.info(f"🧠 AI Market Summary: {market_summary.get('overall_sentiment', 'neutral')} sentiment")
                
            except Exception as e:
                logging.warning(f"⚠️  AI market summary failed: {e}")
        
        for i, symbol in enumerate(symbols, 1):
            try:
                self.symbols_processed += 1
                
                if i % 10 == 0:
                    logging.info(f"📊 Progress: {i}/{len(symbols)} symbols, {opportunities} opportunities, {trades_executed} trades")
                
                analysis = self.analyze_symbol(symbol, use_ai=use_ai)
                if analysis and analysis['signal']:
                    opportunities += 1
                    
                    # Enhanced logging with AI insights
                    ai_info = ""
                    if analysis.get('ai_insight'):
                        ai_info = f" | {analysis['ai_insight']}"
                        if analysis['signal_strength'] == "AI_ENHANCED":
                            ai_enhanced_trades += 1
                    
                    logging.info(f"📈 {analysis['signal']} signal: {symbol} RSI={analysis['rsi']:.1f} Price=${analysis['price']:.2f}{ai_info}")
                    
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
        
        # Enhanced completion summary
        ai_summary = f", {ai_enhanced_trades} AI-enhanced" if use_ai else ""
        logging.info(f"🏁 Analysis complete: {opportunities} opportunities, {trades_executed} trades executed{ai_summary}")
        
        # Final portfolio analysis
        logging.info("📊 Post-trading portfolio analysis...")
        final_portfolio = self.analyze_portfolio()
        if final_portfolio:
            logging.info("📊 Final Portfolio Status:")
            logging.info(f"   Positions: {final_portfolio['total_positions']}")
            logging.info(f"   Value: ${final_portfolio['total_value']:,.2f}")
            logging.info(f"   P&L: ${final_portfolio['total_unrealized_pnl']:,.2f}")
            logging.info(f"   Win Rate: {final_portfolio['win_rate']:.1f}%")
    
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
    
    def analyze_portfolio(self):
        """Comprehensive portfolio analysis with automated recommendations"""
        try:
            logging.info("📊 Starting portfolio analysis...")
            
            # Get portfolio data
            account = self.trading_client.get_account()
            positions = self.trading_client.get_all_positions()
            
            portfolio_data = {
                'account': {
                    'portfolio_value': float(account.portfolio_value),
                    'cash': float(account.cash),
                    'buying_power': float(account.buying_power),
                },
                'positions': []
            }
            
            total_market_value = 0
            total_unrealized_pnl = 0
            
            for pos in positions:
                position_data = {
                    'symbol': pos.symbol,
                    'qty': float(pos.qty),
                    'market_value': float(pos.market_value),
                    'unrealized_pl': float(pos.unrealized_pl),
                    'unrealized_plpc': float(pos.unrealized_plpc) * 100,
                    'current_price': float(pos.current_price) if pos.current_price else 0,
                    'side': pos.side.value if pos.side else 'long'
                }
                
                portfolio_data['positions'].append(position_data)
                total_market_value += abs(position_data['market_value'])
                total_unrealized_pnl += position_data['unrealized_pl']
            
            # Calculate metrics
            winners = [p for p in portfolio_data['positions'] if p['unrealized_pl'] > 0]
            losers = [p for p in portfolio_data['positions'] if p['unrealized_pl'] < 0]
            win_rate = (len(winners) / len(positions)) * 100 if positions else 0
            
            # Calculate concentration risk
            position_weights = [(p['symbol'], abs(p['market_value']) / total_market_value * 100) for p in portfolio_data['positions']]
            position_weights.sort(key=lambda x: x[1], reverse=True)
            top_5_concentration = sum(weight for _, weight in position_weights[:5])
            
            portfolio_analysis = {
                'total_positions': len(positions),
                'total_value': portfolio_data['account']['portfolio_value'],
                'total_unrealized_pnl': total_unrealized_pnl,
                'cash_available': portfolio_data['account']['cash'],
                'cash_percentage': (portfolio_data['account']['cash'] / portfolio_data['account']['portfolio_value']) * 100 if portfolio_data['account']['portfolio_value'] > 0 else 0,
                'win_rate': win_rate,
                'concentration_risk': top_5_concentration,
                'largest_position': position_weights[0] if position_weights else ('N/A', 0),
                'winners': len(winners),
                'losers': len(losers),
                'positions': portfolio_data['positions']
            }
            
            # Log portfolio summary
            logging.info(f"📊 Portfolio Analysis Complete:")
            logging.info(f"   Total Value: ${portfolio_analysis['total_value']:,.2f}")
            logging.info(f"   Unrealized P&L: ${portfolio_analysis['total_unrealized_pnl']:,.2f}")
            logging.info(f"   Win Rate: {portfolio_analysis['win_rate']:.1f}%")
            logging.info(f"   Cash Available: ${portfolio_analysis['cash_available']:,.2f}")
            logging.info(f"   Concentration Risk: {portfolio_analysis['concentration_risk']:.1f}%")
            
            return portfolio_analysis
            
        except Exception as e:
            logging.error(f"❌ Portfolio analysis failed: {e}")
            return None
    
    def execute_portfolio_actions(self, portfolio_analysis):
        """Execute automated actions based on portfolio analysis"""
        if not portfolio_analysis:
            return
            
        actions_taken = []
        
        try:
            logging.info("🎯 Evaluating portfolio actions...")
            
            # Action 1: Reduce high-concentration positions (>15% of portfolio)
            for position in portfolio_analysis['positions']:
                position_weight = abs(position['market_value']) / portfolio_analysis['total_value'] * 100
                
                if position_weight > 15 and position['unrealized_plpc'] > 50:
                    # Consider taking partial profits on large winning positions
                    reduce_qty = int(abs(position['qty']) * 0.25)  # Reduce by 25%
                    
                    if reduce_qty > 0:
                        logging.info(f"🎯 Reducing {position['symbol']} position by {reduce_qty} shares ({position_weight:.1f}% concentration, {position['unrealized_plpc']:.1f}% gain)")
                        
                        if self._place_portfolio_order(position['symbol'], reduce_qty, 'sell', f"Reduce concentration risk - {position_weight:.1f}% of portfolio"):
                            actions_taken.append(f"Reduced {position['symbol']} by {reduce_qty} shares")
            
            # Action 2: Set stop losses on losing positions (>-10%)
            for position in portfolio_analysis['positions']:
                if position['unrealized_plpc'] < -10 and abs(position['market_value']) > 100:
                    # Calculate stop loss price (5% below current price)
                    stop_price = position['current_price'] * 0.95
                    
                    logging.info(f"🛑 Setting stop loss for {position['symbol']} at ${stop_price:.2f} ({position['unrealized_plpc']:.1f}% loss)")
                    
                    # Note: This would require stop order functionality - placeholder for now
                    actions_taken.append(f"Stop loss recommended for {position['symbol']} at ${stop_price:.2f}")
            
            # Action 3: Deploy excess cash if available
            cash_percentage = portfolio_analysis['cash_available'] / portfolio_analysis['total_value'] * 100
            
            if cash_percentage > 20:  # More than 20% cash
                deployable_cash = portfolio_analysis['cash_available'] * 0.5  # Deploy 50% of excess cash
                
                logging.info(f"💰 High cash position ({cash_percentage:.1f}%) - considering deployment of ${deployable_cash:,.2f}")
                actions_taken.append(f"High cash position identified: ${portfolio_analysis['cash_available']:,.2f} ({cash_percentage:.1f}%)")
            
            # Action 4: Diversification recommendations
            if portfolio_analysis['concentration_risk'] > 60:
                logging.warning(f"⚠️ High concentration risk: {portfolio_analysis['concentration_risk']:.1f}% in top 5 positions")
                actions_taken.append(f"High concentration risk: {portfolio_analysis['concentration_risk']:.1f}%")
            
            # Log actions taken
            if actions_taken:
                logging.info(f"✅ Portfolio actions completed: {len(actions_taken)} actions")
                for action in actions_taken:
                    logging.info(f"   • {action}")
            else:
                logging.info("✅ Portfolio appears balanced - no actions needed")
                
        except Exception as e:
            logging.error(f"❌ Portfolio action execution failed: {e}")
    
    def _place_portfolio_order(self, symbol, quantity, side, reason):
        """Place a portfolio management order"""
        try:
            order_side = OrderSide.SELL if side.lower() == 'sell' else OrderSide.BUY
            
            market_order_data = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY
            )
            
            order = self.trading_client.submit_order(order_data=market_order_data)
            
            logging.info(f"📋 Portfolio order placed: {side.upper()} {quantity} {symbol} - {reason}")
            
            # Log to database if available
            if self.db.is_available():
                trade_data = {
                    'symbol': symbol,
                    'signal': side.upper(),
                    'price': 0,  # Will be filled at market
                    'quantity': quantity,
                    'reason': f"Portfolio Management: {reason}",
                    'rsi': 0,
                    'sma_fast': 0,
                    'sma_slow': 0,
                    'timestamp': datetime.now().isoformat()
                }
                
                self.db.insert_data('trades', trade_data)
            
            return True
            
        except Exception as e:
            logging.error(f"❌ Portfolio order failed for {symbol}: {e}")
            return False

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
            # Run analysis with AI if configured
            use_ai = bot.ai.is_configured
            if use_ai:
                logging.info("🧠 AI-enhanced analysis enabled")
            bot.run_analysis(max_symbols=30, max_trades=2, use_ai=use_ai)
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