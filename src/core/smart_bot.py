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

# Rationale helpers
from core.rationale import format_buy_rationale, format_sell_rationale

# Import performance monitoring
try:
    from utils.performance import perf_monitor, track_performance
except ImportError:
    # Fallback if performance module not available
    perf_monitor = None
    def track_performance(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

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

        # Track cash reserved for open/pending buy orders to avoid double-spending
        self._cash_reserves = {}
        
        # AI integration
        self.ai = ai_agent
        
        # AI configuration flags
        self.use_ai_for_ticker_analysis = True  # Set to False to disable AI analysis for individual tickers
        self.use_ai_for_ticker_selection = True  # Set to False to disable AI-based ticker selection
        self.use_ai_for_market_summary = True  # Set to False to disable AI market summaries
        
        # Advanced strategy flags
        self.use_advanced_signals = os.getenv("USE_ADVANCED_SIGNALS", "false").lower() == "true"
        self.use_atr_exits = os.getenv("USE_ATR_EXITS", "false").lower() == "true"
        self.use_atr_sizing = os.getenv("USE_ATR_SIZING", "false").lower() == "true"
        
        # Rate limit tracking
        self.rate_limit_detected = False
        self.rate_limit_count = 0
        self.last_rate_limit_time = None
        
        # Cooldown tracking for intelligent position management
        self.recent_trades = {}  # {symbol: timestamp} for tracking recent trades
        self.trade_times = {}  # symbol -> last trade timestamp
        self.position_sell_analysis_times = {}  # symbol -> last sell analysis timestamp
        self.research_times = {}  # symbol -> last research timestamp for AI ticker variety
        
        logging.info("🤖 Smart Trading Bot initialized")
        if self.db.is_available():
            logging.info("✅ Database available via REST API")
        else:
            logging.warning("⚠️  Database not available - running locally only")
            
        if self.ai.is_configured:
            ai_config = self.ai.get_configuration_status()
            provider_status = self.ai.get_provider_status()
            logging.info(f"🧠 AI Agent configured: {sum(ai_config.values())}/{len(ai_config)} services")
            logging.info(f"🤖 Active AI Provider: {provider_status['current_provider'].upper() if provider_status['current_provider'] else 'None'}")
            if provider_status['failed_providers']:
                logging.warning(f"⚠️  Failed AI Providers: {', '.join(provider_status['failed_providers'])}")
        else:
            logging.warning("⚠️  AI Agent not configured - running without advanced analysis")
    
    def configure_ai_usage(self, ticker_analysis: bool = True, ticker_selection: bool = True, market_summary: bool = True):
        """
        Configure which AI features to enable/disable
        
        Args:
            ticker_analysis: Enable AI analysis for individual ticker insights (default: True)
            ticker_selection: Enable AI-based intelligent ticker selection (default: True)  
            market_summary: Enable AI market sentiment summaries (default: True)
        
        Example:
            # Disable all AI features for pure technical analysis
            bot.configure_ai_usage(ticker_analysis=False, ticker_selection=False, market_summary=False)
            
            # Use AI only for ticker selection, not individual analysis
            bot.configure_ai_usage(ticker_analysis=False, ticker_selection=True, market_summary=False)
        """
        self.use_ai_for_ticker_analysis = ticker_analysis
        self.use_ai_for_ticker_selection = ticker_selection
        self.use_ai_for_market_summary = market_summary
        
        status = []
        if ticker_analysis:
            status.append("ticker analysis")
        if ticker_selection:
            status.append("ticker selection")
        if market_summary:
            status.append("market summaries")
        
        if status:
            logging.info(f"🧠 AI enabled for: {', '.join(status)}")
        else:
            logging.info("🚫 All AI features disabled - using pure technical analysis")
    
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
    
    def _detect_rate_limit_error(self, error_message):
        """Detect if error is a rate limit error"""
        rate_limit_indicators = [
            "429", "quota", "rate limit", "exceeded your current quota",
            "requests per day", "rate-limits", "too many requests"
        ]
        return any(indicator.lower() in str(error_message).lower() for indicator in rate_limit_indicators)
    
    def _detect_ai_failure_error(self, error_message):
        """Detect if error indicates AI service failure"""
        ai_failure_indicators = [
            "finish_reason", "response.text", "valid `Part`", "none were returned",
            "connection error", "service unavailable", "internal error",
            "safety filter", "blocked response", "content filtering"
        ]
        return any(indicator.lower() in str(error_message).lower() for indicator in ai_failure_indicators)
    
    def _handle_rate_limit_error(self, error_message):
        """Handle rate limit error by attempting AI fallback, then disabling AI temporarily"""
        error_str = str(error_message)
        
        # Try AI provider fallback first
        if self.ai.is_configured:
            provider_before = self.ai.current_provider
            logging.warning(f"🔄 AI error detected, attempting provider fallback...")
            
            if self.ai.try_fallback_provider():
                provider_after = self.ai.current_provider
                if provider_after != provider_before:
                    logging.info(f"✅ Successfully switched AI provider: {provider_before} → {provider_after}")
                    print(f"\n🔄 SWITCHED AI PROVIDER: {provider_before.upper()} → {provider_after.upper()}")
                    return  # Don't disable AI if fallback succeeded
                else:
                    logging.warning(f"⚠️  AI fallback kept same provider: {provider_after}")
            else:
                logging.error("❌ AI provider fallback failed")
        
        # If fallback failed or not available, disable AI temporarily
        self.rate_limit_detected = True
        self.rate_limit_count += 1
        self.last_rate_limit_time = datetime.now()
        
        if self._detect_ai_failure_error(str(error_message)):
            if "finish_reason" in str(error_message) or "safety" in str(error_message).lower():
                print(f"\n⚠️  AI CONTENT FILTERING DETECTED (#{self.rate_limit_count})")
                print(f"🔄 Google AI is blocking financial content - switching to technical analysis")
            else:
                print(f"\n⚠️  AI SERVICE ISSUE DETECTED (#{self.rate_limit_count})")
                print(f"🔄 Temporarily disabling AI due to service issues")
        else:
            print(f"\n⚠️  RATE LIMIT DETECTED (#{self.rate_limit_count})")
            print(f"🔄 Automatically disabling AI to continue with technical analysis only")
        
        print(f"📱 Error: {str(error_message)[:100]}{'...' if len(str(error_message)) > 100 else ''}")
        logging.warning(f"AI issue detected, count: {self.rate_limit_count}, AI disabled")
    
    def _should_retry_ai(self):
        """Determine if we should try re-enabling AI after rate limit"""
        if not self.rate_limit_detected:
            return True
            
        # Try re-enabling AI after 1 hour, or every 10 loops if we've had multiple rate limits
        if self.last_rate_limit_time:
            time_since_rate_limit = datetime.now() - self.last_rate_limit_time
            
            # After 1 hour, try re-enabling
            if time_since_rate_limit > timedelta(hours=1):
                self.rate_limit_detected = False
                print(f"\n🔄 Attempting to re-enable AI (1 hour since last rate limit)")
                return True
                
        return False
    
    def get_ai_recommended_tickers(self, portfolio_analysis: Dict) -> List[str]:
        """Get AI-recommended tickers based on portfolio analysis"""
        if not self.ai.is_configured:
            logging.warning("⚠️ AI not configured, using smart fallback")
            return self._get_smart_fallback_tickers(portfolio_analysis)
        
        # If AI has failed multiple times recently, skip AI for ticker selection
        if self.rate_limit_detected:
            logging.info("🔄 AI temporarily disabled, using smart fallback for ticker selection")
            return self._get_smart_fallback_tickers(portfolio_analysis)
        
        try:
            # If AI has been problematic recently, skip AI and use smart fallback
            if self.rate_limit_detected:
                logging.info("🔄 AI disabled due to rate limits, using smart fallback ticker selection")
                return self._get_smart_fallback_tickers(portfolio_analysis)
            
            # Get recently researched tickers to avoid recommending them again
            recently_researched = self.get_recently_researched_tickers(cooldown_minutes=15)
            if recently_researched:
                print(f"\n🔍 Research cooldown (15m, db+memory) tickers to EXCLUDE ({len(recently_researched)}): {', '.join(recently_researched[:20])}")
                logging.info(f"🔍 Research cooldown (15m, db+memory) tickers to EXCLUDE ({len(recently_researched)}): {', '.join(recently_researched[:20])}")
            else:
                print("\n🔍 Research cooldown (15m, db+memory) list is empty")
                logging.info("🔍 Research cooldown (15m, db+memory) list is empty")

            # Clarify which cooldown scopes are active this loop
            print(f"ℹ️  Cooldown scopes: 15m research list size={len(recently_researched)}; no global cooldown list in use")
            logging.info(f"ℹ️  Cooldown scopes: 15m research list size={len(recently_researched)}; no global cooldown list in use")
            
            # Prepare portfolio context for AI
            portfolio_context = {
                'total_value': portfolio_analysis.get('total_value', 0),
                'positions': portfolio_analysis.get('total_positions', 0),
                'cash_percentage': portfolio_analysis.get('cash_percentage', 0),
                'concentration_risk': portfolio_analysis.get('concentration_risk', 0),
                'top_holdings': portfolio_analysis.get('top_holdings', []),
                'sector_allocation': portfolio_analysis.get('sector_allocation', {}),
                'underperforming_positions': portfolio_analysis.get('underperforming_positions', []),
                'high_concentration_positions': portfolio_analysis.get('high_concentration_positions', []),
                'recently_researched': recently_researched
            }
            
            prompt = f"""
            For educational purposes, suggest 30 stock symbols to analyze based on this portfolio study.
            
            Portfolio Educational Data:
            - Portfolio Value: ${portfolio_context['total_value']:,.2f}
            - Position Count: {portfolio_context['positions']}
            - Cash Allocation: {portfolio_context['cash_percentage']:.1f}%
            - Concentration Level: {portfolio_context['concentration_risk']:.1f}% in top 5 positions
            
            Current Holdings Study: {portfolio_context['top_holdings'][:5]}
            Sector Distribution: {portfolio_context['sector_allocation']}
            Lower Performance Holdings: {portfolio_context['underperforming_positions'][:3]}
            High Concentration Holdings: {portfolio_context['high_concentration_positions']}
            
            ⚠️  CRITICAL EXCLUSION LIST - DO NOT RECOMMEND THESE TICKERS:
            {portfolio_context['recently_researched']}
            
            These tickers were recently analyzed (within 15 minutes). You MUST suggest COMPLETELY DIFFERENT tickers.
            
            Please suggest NEW symbols for educational analysis considering:
            1. Portfolio diversification patterns
            2. Sector balance opportunities  
            3. Market growth sectors (for educational study)
            4. Stability analysis options
            5. Alternative holdings for comparison study
            6. **MANDATORY: Every recommended ticker must NOT appear in the exclusion list above**
            
            Return educational data in JSON format:
            {{
                "recommended_tickers": ["30 DIFFERENT ticker symbols NOT in exclusion list"],
                "reasoning": "Educational analysis approach explanation",
                "focus_areas": ["diversification_study", "sector_analysis", "growth_patterns", ...]
            }}
            
            Focus on liquid, well-known stocks. Avoid penny stocks, highly speculative tickers, and recently analyzed symbols.
            Prioritize fresh analysis opportunities not in the recently researched list.
            """
            
            import asyncio
            
            try:
                response = asyncio.run(self.ai.analyze_with_context(prompt, "portfolio_ticker_selection"))
                
                if isinstance(response, dict) and 'recommended_tickers' in response:
                    tickers = response['recommended_tickers'][:30]  # Limit to 30
                    reasoning = response.get('reasoning', 'No reasoning provided')
                    focus_areas = response.get('focus_areas', [])
                    
                    # Print ticker selection summary for visibility
                    print("\n" + "="*60)
                    print("🧠 AI TICKER SELECTION SUMMARY")
                    print("="*60)
                    print(f"📋 Strategy (AI's stated intent): {reasoning}")
                    print(f"🎯 Focus Areas: {', '.join(focus_areas)}")
                    print(f"📊 Recommended Tickers ({len(tickers)}): {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")
                    print("ℹ️  Note: AI claims above are aspirational; actual cooldown compliance is enforced by filter below.")
                    
                    # Also log
                    logging.info("\n" + "="*60)
                    logging.info("🧠 AI TICKER SELECTION SUMMARY")
                    logging.info("="*60)
                    logging.info(f"📋 Strategy: {reasoning}")
                    logging.info(f"🎯 Focus Areas: {', '.join(focus_areas)}")
                    logging.info(f"📊 Recommended Tickers ({len(tickers)}): {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")
                    # Print reasoning for visibility
                    print("\n🔍 WHY THESE TICKERS WERE CHOSEN:")
                    if 'diversification' in focus_areas:
                        print("   📈 Diversification: Reduce concentration risk")
                    if 'growth' in focus_areas:
                        print("   🚀 Growth: Deploy excess cash in quality stocks")
                    if 'defensive' in focus_areas:
                        print("   🛡️  Defensive: Improve win rate with stable positions")
                    if 'quality' in focus_areas:
                        print("   ⭐ Quality: Focus on established, liquid companies")
                    print("="*60)
                    
                    # Also log
                    logging.info("\n🔍 WHY THESE TICKERS WERE CHOSEN:")
                    if 'diversification' in focus_areas:
                        logging.info("   📈 Diversification: Reduce concentration risk")
                    if 'growth' in focus_areas:
                        logging.info("   🚀 Growth: Deploy excess cash in quality stocks")
                    if 'defensive' in focus_areas:
                        logging.info("   🛡️  Defensive: Improve win rate with stable positions")
                    if 'quality' in focus_areas:
                        logging.info("   ⭐ Quality: Focus on established, liquid companies")
                    logging.info("="*60)
                    
                    # Filter out tickers in research cooldown before returning
                    cooldown_filtered_tickers = self.apply_cooldown_with_refresh(tickers, min_count=10, cooldown_minutes=15)

                    refresh_info = getattr(self, "_last_cooldown_refresh_info", None)
                    if refresh_info:
                        if refresh_info.get("used_refresh"):
                            print(
                                f"ℹ️  AI list depleted by cooldown: initial survivors {refresh_info['initial_survivors']} "
                                f"(of {refresh_info['initial_count']}) replaced with {refresh_info['final_count']} fresh survivors"
                            )
                            # Show overlap diagnostics for the fresh list
                            fresh_filtered_out_count = refresh_info.get("fresh_filtered_out", 0)
                            if fresh_filtered_out_count > 0:
                                fresh_pool = refresh_info.get("fresh_pool", 0)
                                print(
                                    f"ℹ️  Fresh ticker cooldown filter: kept {refresh_info['final_count']} of {fresh_pool} fresh candidates"
                                )
                                # Note: detailed overlap list is logged but not printed to avoid clutter
                            # When refresh happened, don't report AI violations (we're using a different list now)
                        else:
                            print(f"ℹ️  Cooldown filter kept {refresh_info['final_count']} of {refresh_info['initial_count']} AI tickers")
                            
                            # Only check for AI violations when we're actually using the AI list
                            filtered_out = set(tickers) - set(cooldown_filtered_tickers)
                            if filtered_out:
                                preview = list(filtered_out)[:10]
                                more = "" if len(filtered_out) <= 10 else f" (showing first 10 of {len(filtered_out)})"
                                print(
                                    f"\n⚠️  AI CLAIMED TO AVOID OVERLAP BUT VIOLATED CONSTRAINTS: "
                                    f"{len(filtered_out)} tickers matched the 15m research cooldown list (db+memory){more}: {preview}"
                                )
                                logging.warning(
                                    f"⚠️  AI claimed exclusion but recommended {len(filtered_out)} tickers in 15m research cooldown (db+memory){more}: {preview}"
                                )
                    
                    tail_note = "" if len(cooldown_filtered_tickers) <= 10 else f" (showing first 10 of {len(cooldown_filtered_tickers)})"
                    preview_final = cooldown_filtered_tickers[:10]
                    print(f"📊 AI recommended {len(tickers)} tickers, {len(cooldown_filtered_tickers)} available after cooldown filtering (final)")
                    print(f"📊 FINAL TICKERS FOR RESEARCH ({len(cooldown_filtered_tickers)}){tail_note}: {', '.join(preview_final)}")
                    logging.info(f"📊 AI recommended {len(tickers)} tickers, {len(cooldown_filtered_tickers)} available after cooldown filtering (final)")
                    logging.info(f"📊 Final tickers for research ({len(cooldown_filtered_tickers)}): {', '.join(cooldown_filtered_tickers[:20])}{'...' if len(cooldown_filtered_tickers) > 20 else ''}")
                    
                    return cooldown_filtered_tickers
                else:
                    # Provide detailed diagnosis of why AI response was invalid
                    response_type = type(response).__name__
                    if response is None:
                        logging.warning("⚠️ AI returned None response (likely API failure or safety filter), using smart fallback")
                    elif isinstance(response, str):
                        preview = response[:100] + "..." if len(response) > 100 else response
                        logging.warning(f"⚠️ AI returned string instead of JSON dict: '{preview}', using smart fallback")
                    elif isinstance(response, dict):
                        missing_keys = []
                        if 'recommended_tickers' not in response:
                            missing_keys.append('recommended_tickers')
                        available_keys = list(response.keys())
                        logging.warning(f"⚠️ AI returned dict missing required keys {missing_keys}. Available keys: {available_keys}, using smart fallback")
                    else:
                        logging.warning(f"⚠️ AI returned unexpected type {response_type}: {str(response)[:100]}, using smart fallback")
                    return self._get_smart_fallback_tickers(portfolio_analysis)

            except Exception as inner_e:
                # Keep scope narrow to avoid masking outer error handling
                logging.error(f"AI ticker recommendation execution failed: {inner_e}")
                return self._get_smart_fallback_tickers(portfolio_analysis)
                
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower():
                logging.error(f"❌ AI ticker recommendations failed: Rate limit exceeded - {error_msg[:200]}")
            elif "safety" in error_msg.lower() or "block" in error_msg.lower():
                logging.error(f"❌ AI ticker recommendations failed: Safety filter blocked request - {error_msg[:200]}")
            elif "timeout" in error_msg.lower():
                logging.error(f"❌ AI ticker recommendations failed: Request timeout - {error_msg[:200]}")
            elif "json" in error_msg.lower() or "parse" in error_msg.lower():
                logging.error(f"❌ AI ticker recommendations failed: JSON parsing error - {error_msg[:200]}")
            else:
                logging.error(f"❌ AI ticker recommendations failed: {type(e).__name__} - {error_msg[:200]}")
            return self._get_smart_fallback_tickers(portfolio_analysis)
    
    def _get_smart_fallback_tickers(self, portfolio_analysis: Dict) -> List[str]:
        """Intelligent fallback ticker selection based on portfolio analysis"""
        # Get recently researched tickers to avoid repeating them
        recently_researched = self.get_recently_researched_tickers(cooldown_minutes=15)
        
        fallback_tickers = []
        all_candidate_tickers = []
        
        # High concentration risk - add diversification tickers
        concentration_risk = portfolio_analysis.get('concentration_risk', 0)
        if concentration_risk > 50:
            # Add diversified large caps
            diversification_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'JNJ', 'V', 'MA', 'UNH', 'BRK.B', 'LLY', 'WMT']
            all_candidate_tickers.extend(diversification_tickers)
            logging.info(f"🎯 High concentration risk ({concentration_risk:.1f}%) - adding diversification stocks")
        
        # High cash percentage - add growth tickers
        cash_percentage = portfolio_analysis.get('cash_percentage', 0)
        if cash_percentage > 80:
            # Add growth and tech stocks
            growth_tickers = ['AMD', 'NFLX', 'CRM', 'ADBE', 'PYPL', 'UBER', 'SQ', 'ROKU', 'ZM', 'SHOP', 'SNOW', 'PLTR', 'NET', 'DDOG', 'OKTA']
            all_candidate_tickers.extend(growth_tickers)
            logging.info(f"💰 High cash percentage ({cash_percentage:.1f}%) - adding growth stocks")
        
        # Add stable picks and additional quality stocks
        stable_tickers = ['KO', 'PG', 'WMT', 'HD', 'VZ', 'T', 'PFE', 'XOM', 'CVX', 'BAC', 'IBM', 'INTC', 'ORCL', 'CSCO', 'MRK', 'ABT', 'TMO', 'COST', 'LOW', 'TGT']
        all_candidate_tickers.extend(stable_tickers)
        
        # Add more variety to avoid recent research patterns
        additional_variety = ['DIS', 'BABA', 'NKE', 'SBUX', 'MCD', 'MMM', 'CAT', 'GE', 'F', 'GM', 'DAL', 'UAL', 'SPCE', 'COIN', 'SQ', 'RIVN', 'LCID', 'NIO', 'XPEV', 'LI']
        all_candidate_tickers.extend(additional_variety)
        
        # Filter out recently researched tickers to provide variety
        for ticker in all_candidate_tickers:
            if ticker not in recently_researched and ticker not in fallback_tickers:
                fallback_tickers.append(ticker)
                
        # If we don't have enough variety, add from recently researched as last resort
        if len(fallback_tickers) < 15:
            logging.info(f"📊 Adding recently researched tickers as fallback ({len(fallback_tickers)} < 15)")
            for ticker in all_candidate_tickers:
                if ticker not in fallback_tickers:
                    fallback_tickers.append(ticker)
        
        # Ensure we have enough tickers
        unique_tickers = list(dict.fromkeys(fallback_tickers))  # Preserves order
        selected = unique_tickers[:30]  # Limit to 30
        
        print("\n" + "="*60)
        print("🧠 SMART FALLBACK TICKER SELECTION")
        print("="*60)
        print(f"📋 Strategy: Portfolio-based intelligent selection avoiding recent research")
        print(f"🎯 Focus: Concentration risk: {concentration_risk:.1f}%, Cash: {cash_percentage:.1f}%")
        print(f"⏰ Recently researched ({len(recently_researched)}): {', '.join(recently_researched[:5])}{'...' if len(recently_researched) > 5 else ''}")
        print(f"📊 Selected Tickers (pre-filter, {len(selected)}): {', '.join(selected[:10])}{'...' if len(selected) > 10 else ''}")
        print("="*60)
        
        # Filter out tickers in research cooldown before returning (with refresh if needed)
        cooldown_filtered_tickers = self.apply_cooldown_with_refresh(selected, min_count=10, cooldown_minutes=15)

        # Show the actual list we will use (post-filter/refresh)
        print(f"📊 USING TICKERS (post-filter, {len(cooldown_filtered_tickers)}): {', '.join(cooldown_filtered_tickers[:10])}{'...' if len(cooldown_filtered_tickers) > 10 else ''}")
        logging.info(f"📊 Smart fallback selected {len(selected)} pre-filter; using {len(cooldown_filtered_tickers)} after cooldown filter")
        
        return cooldown_filtered_tickers
    
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
        """Get market data for analysis (works with markets closed)"""
        try:
            # Use current date/time, but Alpaca will return latest available data
            # When markets are closed, this returns the most recent trading day's data
            end_date = datetime.now()
            # Request extra days to ensure we have enough bars even with weekends/holidays
            start_date = end_date - timedelta(days=150)
            
            request = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date
            )
            
            barset = self.data_client.get_stock_bars(request)
            
            if not barset or symbol not in barset.data:
                logging.debug(f"{symbol}: No barset returned from API (barset={barset is not None}, has_data={symbol in barset.data if barset else False})")
                return None
            
            bars = barset.data[symbol]
            if not bars:
                logging.debug(f"{symbol}: Barset returned but no bars")
                return None
            
            logging.debug(f"{symbol}: Received {len(bars)} bars from {bars[0].timestamp.date()} to {bars[-1].timestamp.date()}")
            
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
            if df is None:
                logging.debug(f"{symbol}: get_market_data returned None")
                return None
            
            # Check minimum bars based on strategy mode
            min_bars = 200 if self.use_advanced_signals else self.sma_slow
            if len(df) < min_bars:
                logging.debug(f"{symbol}: Only {len(df)} bars, need {min_bars}")
                return None
            
            df = self.calculate_indicators(df)
            latest = df.iloc[-1]
            
            if pd.isna(latest[f'SMA_{self.sma_fast}']):
                logging.debug(f"{symbol}: SMA_fast is NaN")
                return None
            if pd.isna(latest['RSI']):
                logging.debug(f"{symbol}: RSI is NaN")
                return None
            
            price = latest['close']
            
            # Use advanced or basic signal generation
            if self.use_advanced_signals:
                # Import here to avoid circular dependency
                from trading.strategy import TechnicalStrategy
                
                # Create strategy on-demand (could cache this in __init__)
                if not hasattr(self, '_tech_strategy'):
                    self._tech_strategy = TechnicalStrategy()
                
                # Use advanced signal with full diagnostic output
                signal, signal_strength, reasons = self._tech_strategy.evaluate_signal_advanced(latest)
                
                # Log diagnostic reasons
                if signal:
                    logging.info(f"{symbol}: {signal} signal ({signal_strength})")
                    for reason in reasons:
                        logging.info(f"  • {reason}")
                
                # Build enhanced analysis result
                analysis_result = {
                    'symbol': symbol,
                    'price': price,
                    'sma_fast': latest[f'SMA_{self.sma_fast}'],
                    'sma_slow': latest[f'SMA_{self.sma_slow}'],
                    'rsi': latest['RSI'],
                    'signal': signal,
                    'signal_strength': signal_strength,
                    'timestamp': latest.get('timestamp', datetime.now(timezone.utc)),
                    'reasons': reasons  # Diagnostic reasons
                }
                
                # Include advanced indicators if available
                if 'MACD' in latest and not pd.isna(latest['MACD']):
                    analysis_result['macd'] = latest['MACD']
                    analysis_result['macd_signal'] = latest.get('MACD_signal')
                    analysis_result['macd_hist'] = latest.get('MACD_hist')
                
                if 'ATR' in latest and not pd.isna(latest['ATR']):
                    analysis_result['atr'] = latest['ATR']
                
            else:
                # Traditional basic analysis
                sma_fast = latest[f'SMA_{self.sma_fast}']
                sma_slow = latest[f'SMA_{self.sma_slow}']
                rsi = latest['RSI']
                
                signal = None
                signal_strength = "WEAK"
                
                # Traditional technical analysis
                if sma_fast > sma_slow and rsi < self.rsi_buy_threshold:
                    signal = "BUY"
                    signal_strength = "STRONG" if rsi < 25 else "MEDIUM"
                elif sma_fast < sma_slow and rsi > self.rsi_sell_threshold:
                    signal = "SELL"
                    signal_strength = "STRONG" if rsi > 75 else "MEDIUM"
                
                analysis_result = {
                    'symbol': symbol,
                    'price': price,
                    'sma_fast': sma_fast,
                    'sma_slow': sma_slow,
                    'rsi': rsi,
                    'signal': signal,
                    'signal_strength': signal_strength,
                    'timestamp': latest.get('timestamp', datetime.now(timezone.utc))
                }
            
            # AI enhancement (if enabled globally, configured, and enabled at ticker level)
            ai_insight = None
            if use_ai and self.ai.is_configured and self.use_ai_for_ticker_analysis and signal:
                try:
                    # Get AI research
                    import asyncio
                    ai_research = asyncio.run(self.ai.research_symbol(symbol, lookback_days=2))
                    
                    if ai_research and ai_research.get('ai_recommendation'):
                        ai_rec = ai_research['ai_recommendation']
                        ai_signal = ai_rec.recommendation.upper()
                        
                        # Enhance signal with AI
                        if ai_signal == signal and ai_rec.confidence > 0.7:
                            analysis_result['signal_strength'] = "AI_ENHANCED"
                            ai_insight = f"AI confirms {signal} with {ai_rec.confidence:.1%} confidence"
                        elif ai_signal != signal:
                            analysis_result['signal_strength'] = "CONFLICTED"
                            ai_insight = f"AI suggests {ai_signal} vs technical {signal}"
                        
                except Exception as e:
                    if self._detect_rate_limit_error(str(e)) or self._detect_ai_failure_error(str(e)):
                        self._handle_rate_limit_error(e)
                        # Don't break the analysis, just skip AI for this symbol
                    logging.debug(f"AI analysis failed for {symbol}: {e}")
            
            # Add AI insight to result if available
            if ai_insight:
                analysis_result['ai_insight'] = ai_insight
            
            # Track research time for cooldown variety
            self.mark_recent_research(symbol)
            
            return analysis_result
            
        except Exception as e:
            logging.debug(f"Analysis failed for {symbol}: {e}")
            self.errors_count += 1
            return None
    
    def get_current_position_size(self, symbol: str) -> float:
        """Get current position size for a symbol"""
        try:
            position = self.trading_client.get_open_position(symbol)
            return float(position.qty) if position else 0.0
        except Exception as e:
            logging.debug(f"Could not get position size for {symbol}: {e}")
            return 0.0
    
    def get_current_position_value(self, symbol: str) -> float:
        """Get current position market value for a symbol"""
        try:
            position = self.trading_client.get_open_position(symbol)
            return float(position.market_value) if position else 0.0
        except Exception as e:
            logging.debug(f"Could not get position value for {symbol}: {e}")
            return 0.0

    def refresh_cash_reserves(self):
        """Keep cash reserve map in sync with currently open/pending buy orders"""
        try:
            orders = list(self.trading_client.get_orders())
            from alpaca.trading.enums import OrderStatus

            open_statuses = {
                OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW,
                OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL,
                OrderStatus.PENDING_REPLACE, OrderStatus.PENDING_REVIEW
            }

            open_buy_ids = {
                str(order.id) for order in orders
                if order.status in open_statuses and order.side == OrderSide.BUY
            }

            # Drop any reserves for orders that are no longer open
            stale_ids = set(self._cash_reserves.keys()) - open_buy_ids
            for oid in stale_ids:
                self._cash_reserves.pop(oid, None)

        except Exception as e:
            # Non-fatal; fall back to existing reserves
            logging.debug(f"Could not refresh cash reserves: {e}")

    def get_reserved_cash(self) -> float:
        """Return total cash reserved for open/pending buy orders"""
        self.refresh_cash_reserves()
        return sum(self._cash_reserves.values())

    def get_effective_cash_available(self) -> tuple:
        """Cash available after subtracting reserved amounts for open buys"""
        try:
            account = self.trading_client.get_account()
            cash = float(account.cash)
        except Exception as e:
            logging.warning(f"Could not fetch account cash, assuming 0: {e}")
            cash = 0.0

        reserved = self.get_reserved_cash()
        effective = max(cash - reserved, 0.0)
        return effective, reserved
    
    def has_pending_orders(self, symbol: str) -> bool:
        """Check if there are pending orders for a symbol"""
        try:
            # Get all orders and filter for open/pending ones
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import OrderStatus
            
            # Get recent orders and filter for open ones
            orders = list(self.trading_client.get_orders())
            
            # Filter for open statuses and matching symbol
            open_statuses = {
                OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW,
                OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL,
                OrderStatus.PENDING_REPLACE, OrderStatus.PENDING_REVIEW
            }
            
            pending_orders = [order for order in orders 
                            if order.status in open_statuses and order.symbol == symbol]
            pending_count = len(pending_orders)
            
            if pending_count > 0:
                logging.info(f"📋 {symbol}: Found {pending_count} pending orders - skipping trade")
                for order in pending_orders:
                    logging.info(f"  └─ Order {order.id}: {order.status} - {order.qty} shares")
            return pending_count > 0
        except Exception as e:
            logging.warning(f"⚠️ Failed to check pending orders for {symbol}: {e}")
            # Conservative: if we can't check, assume there might be pending orders
            return True
    
    def get_portfolio_total_value(self) -> float:
        """Get total portfolio value"""
        try:
            account = self.trading_client.get_account()
            return float(account.portfolio_value)
        except Exception as e:
            logging.warning(f"Could not get portfolio value, using default: {e}")
            return 100000.0  # Default fallback
    
    def calculate_position_size(self, symbol: str, signal_strength: str, price: float, portfolio_value: float) -> int:
        """Calculate position size based on signal strength and portfolio percentage"""
        
        # Base allocation percentages by signal strength
        allocation_map = {
            'AI_ENHANCED': 0.025,    # 2.5% for AI enhanced signals
            'STRONG': 0.02,          # 2.0% for strong signals  
            'MEDIUM': 0.015,         # 1.5% for medium signals
            'WEAK': 0.01,            # 1.0% for weak signals
            'CONFLICTED': 0.005      # 0.5% for conflicted signals
        }
        
        base_allocation = allocation_map.get(signal_strength, 0.01)
        target_value = portfolio_value * base_allocation
        quantity = int(target_value / price)
        
        # Minimum and maximum constraints
        min_quantity = 1
        max_quantity = int(portfolio_value * 0.05 / price)  # Max 5% of portfolio
        
        return max(min_quantity, min(quantity, max_quantity))
    
    def check_position_limits(self, symbol: str, new_quantity: int, price: float, portfolio_value: float) -> tuple:
        """Check if adding position would exceed concentration limits"""
        current_value = self.get_current_position_value(symbol)
        new_trade_value = new_quantity * price
        total_position_value = current_value + new_trade_value
        
        # Calculate percentage of portfolio this position would represent
        position_percentage = (total_position_value / portfolio_value) * 100
        
        # Set maximum position size per ticker (e.g., 10% of portfolio)
        max_position_percentage = 10.0
        
        if position_percentage > max_position_percentage:
            # Calculate maximum allowed additional quantity
            max_total_value = portfolio_value * (max_position_percentage / 100)
            max_additional_value = max_total_value - current_value
            max_additional_quantity = int(max_additional_value / price) if max_additional_value > 0 else 0
            
            return False, max_additional_quantity, position_percentage
        
        return True, new_quantity, position_percentage
    
    def is_in_cooldown(self, symbol: str, cooldown_minutes: int = 30) -> bool:
        """Check if a symbol is in cooldown period to prevent excessive repeat trades (using database if available)"""
        # Try database first
        if self.db.is_available():
            last_trade_time = self.db.get_trade_cooldown(symbol)
            if last_trade_time is None:
                return False
            cooldown_period = timedelta(minutes=cooldown_minutes)
            if last_trade_time.tzinfo is None:
                last_trade_time = last_trade_time.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            return now_utc - last_trade_time < cooldown_period
        
        # Fallback to in-memory
        if symbol not in self.recent_trades:
            return False
        
        last_trade_time = self.recent_trades[symbol]
        cooldown_period = timedelta(minutes=cooldown_minutes)
        
        return datetime.now() - last_trade_time < cooldown_period
    
    def is_position_sell_in_cooldown(self, symbol: str, cooldown_minutes: int = 30) -> bool:
        """Check if a position's sell analysis is in cooldown period (using database if available)"""
        # Try database first
        if self.db.is_available():
            last_analysis_time = self.db.get_position_sell_cooldown(symbol)
            if last_analysis_time is None:
                return False
            cooldown_period = timedelta(minutes=cooldown_minutes)
            if last_analysis_time.tzinfo is None:
                last_analysis_time = last_analysis_time.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            return now_utc - last_analysis_time < cooldown_period
        
        # Fallback to in-memory
        if symbol not in self.position_sell_analysis_times:
            return False
        
        last_analysis_time = self.position_sell_analysis_times[symbol]
        cooldown_period = timedelta(minutes=cooldown_minutes)
        return datetime.now() - last_analysis_time < cooldown_period
    
    def is_in_research_cooldown(self, symbol: str, cooldown_minutes: int = 15) -> bool:
        """Check if a symbol is in research cooldown period (using database if available)"""
        # Try database first
        if self.db.is_available():
            last_research_time = self.db.get_research_cooldown(symbol)
            if last_research_time is None:
                return False
            cooldown_period = timedelta(minutes=cooldown_minutes)
            if last_research_time.tzinfo is None:
                last_research_time = last_research_time.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            return now_utc - last_research_time < cooldown_period
        
        # Fallback to in-memory
        if symbol not in self.research_times:
            return False
        
        last_research_time = self.research_times[symbol]
        cooldown_period = timedelta(minutes=cooldown_minutes)
        if last_research_time.tzinfo is None:
            last_research_time = last_research_time.replace(tzinfo=timezone.utc)

        now_utc = datetime.now(timezone.utc)
        return now_utc - last_research_time < cooldown_period
    
    def filter_tickers_by_cooldown(self, tickers: List[str], cooldown_minutes: int = 15) -> List[str]:
        """Filter out tickers that are in research cooldown period"""
        filtered_tickers = []
        skipped_count = 0
        
        for ticker in tickers:
            if not self.is_in_research_cooldown(ticker, cooldown_minutes):
                filtered_tickers.append(ticker)
            else:
                skipped_count += 1
        
        if skipped_count > 0:
            logging.info(f"⏰ Filtered out {skipped_count} tickers in {cooldown_minutes}-minute research cooldown")
        
        return filtered_tickers

    def apply_cooldown_with_refresh(self, tickers: List[str], min_count: int = 10, cooldown_minutes: int = 15) -> List[str]:
        """Filter tickers by cooldown; if too few, replace with a fresh filtered list."""
        original_count = len(tickers)
        filtered = self.filter_tickers_by_cooldown(tickers, cooldown_minutes=cooldown_minutes)

        refresh_info = {
            "initial_count": original_count,
            "initial_survivors": len(filtered),
            "used_refresh": False,
            "fresh_pool": 0,
            "fresh_after_filter": 0,
            "final_count": len(filtered)
        }

        if len(filtered) < min_count:
            logging.warning(
                f"⚠️  Only {len(filtered)} tickers available after initial cooldown filter "
                f"(from {original_count} provided); refreshing list"
            )
            logging.info("🔄 Generating fresh ticker list excluding cooldown, orders, and portfolio positions...")
            fresh_tickers = self._get_fresh_ticker_list(target_count=30)
            fresh_filtered = self.filter_tickers_by_cooldown(fresh_tickers, cooldown_minutes=cooldown_minutes)

            # Track which fresh tickers were filtered out for diagnostics
            fresh_filtered_out = set(fresh_tickers) - set(fresh_filtered)
            
            filtered = fresh_filtered

            refresh_info.update({
                "used_refresh": True,
                "fresh_pool": len(fresh_tickers),
                "fresh_after_filter": len(fresh_filtered),
                "fresh_filtered_out": len(fresh_filtered_out),
                "final_count": len(filtered)
            })

            logging.info(
                f"🔁 Refresh after cooldown filter: initial survivors={refresh_info['initial_survivors']}, "
                f"replaced with {refresh_info['fresh_after_filter']} fresh survivors (from {refresh_info['fresh_pool']} candidates)"
            )
            
            # Log overlap diagnostics for the fresh list
            if fresh_filtered_out:
                preview = list(fresh_filtered_out)[:10]
                more = "" if len(fresh_filtered_out) <= 10 else f" (showing first 10 of {len(fresh_filtered_out)})"
                logging.info(
                    f"ℹ️  Fresh ticker overlap: {len(fresh_filtered_out)} of {len(fresh_tickers)} fresh candidates "
                    f"matched the 15m research cooldown{more}: {preview}"
                )
        else:
            refresh_info["final_count"] = len(filtered)

        # Store last refresh details for downstream logging
        self._last_cooldown_refresh_info = refresh_info

        return filtered
    
    def get_recently_researched_tickers(self, cooldown_minutes: int = 15) -> List[str]:
        """Get list of tickers researched within the cooldown period (from database if available)"""
        recently_researched: List[str] = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)
        
        # Try to get from database first
        if self.db.is_available():
            try:
                import requests
                headers = {
                    "apikey": self.db.api_key,
                    "Authorization": f"Bearer {self.db.api_key}",
                    "Content-Type": "application/json"
                }
                
                # Query all research cooldowns within the time window
                # Ensure ISO format with Z suffix for UTC (PostgREST expects RFC3339)
                cutoff_iso = cutoff_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                print(f"   🔍 Querying database for tickers researched after {cutoff_time.strftime('%H:%M:%S')} UTC...")
                
                response = requests.get(
                    f"{self.db.rest_url}/research_cooldowns?last_research_time=gte.{cutoff_iso}&select=symbol",
                    headers=headers,
                    timeout=5
                )
                
                if response.status_code == 200:
                    data = response.json()
                    recently_researched = [item['symbol'] for item in data]
                    print(f"   📊 Database returned {len(recently_researched)} tickers in cooldown period")
                    logging.debug(f"Retrieved {len(recently_researched)} recently researched tickers from database")
                    # Clear any previous DB error since this path succeeded
                    self.db.last_error = None
                    return recently_researched
                else:
                    error_body = response.text[:200] if response.text else "No error details"
                    print(f"   ⚠️  Database query failed with status {response.status_code}, using memory fallback")
                    print(f"   📝 Error detail: {error_body}")
                    self.db.last_error = f"research_cooldowns status {response.status_code}: {error_body}"
                    logging.warning(f"DB health degraded (status {response.status_code}) for research cooldowns; using memory fallback")
                    logging.debug(f"Failed to query research cooldowns from database: {response.status_code} - {error_body}")
            except Exception as e:
                print(f"   ⚠️  Database error: {str(e)[:50]}, using memory fallback")
                self.db.last_error = f"research_cooldowns exception: {e}"
                logging.warning(f"DB health degraded (exception) for research cooldowns; using memory fallback: {e}")
                logging.debug(f"Error querying research cooldowns from database: {e}")
        else:
            print(f"   ℹ️  Database not available, checking in-memory cooldowns only")
            self.db.last_error = "db_unavailable"
            logging.info("DB unavailable for research cooldowns; using memory store only")
        
        # Fallback to in-memory if database unavailable
        if not hasattr(self, 'research_times'):
            return []
        
        cutoff_time_local = cutoff_time
        pruned = self._prune_research_times(cutoff_time_local)
        if pruned:
            logging.info(f"🧹 Pruned {pruned} expired in-memory research cooldowns")

        for ticker, research_time in self.research_times.items():
            # Normalize any legacy naive timestamps to UTC for safe comparison
            ts = research_time
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            if ts > cutoff_time_local:
                recently_researched.append(ticker)
        
        if recently_researched:
            print(f"   📝 Found {len(recently_researched)} tickers in memory cooldown")
                
        return recently_researched
    
    def mark_position_sell_analysis(self, symbol: str):
        """Mark that we've analyzed this position for selling (using database if available)"""
        analysis_time = datetime.now()
        
        # Store in database if available
        if self.db.is_available():
            success = self.db.set_position_sell_cooldown(symbol, analysis_time)
            if success:
                logging.debug(f"✅ Stored position sell cooldown for {symbol} in database")
            else:
                logging.debug(f"⚠️ Failed to store position sell cooldown for {symbol}, using memory")
                # Fallback to memory
                self.position_sell_analysis_times[symbol] = analysis_time
        else:
            # Use in-memory storage
            self.position_sell_analysis_times[symbol] = analysis_time
    
    def mark_recent_trade(self, symbol: str, trade_type: str = None):
        """Mark a symbol as recently traded (using database if available)"""
        from datetime import datetime
        trade_time = datetime.now()
        
        # Store in database if available
        if self.db.is_available():
            success = self.db.set_trade_cooldown(symbol, trade_time, trade_type)
            if success:
                logging.debug(f"✅ Stored trade cooldown for {symbol} in database")
            else:
                logging.debug(f"⚠️ Failed to store trade cooldown for {symbol}, using memory")
                # Fallback to memory
                self.recent_trades[symbol] = trade_time
                self.trade_times[symbol] = trade_time
        else:
            # Use in-memory storage
            self.recent_trades[symbol] = trade_time
            self.trade_times[symbol] = trade_time
    
    def mark_recent_research(self, symbol: str):
        """Mark a symbol as recently researched for ticker variety (using database if available)"""
        research_time = datetime.now(timezone.utc)  # Use UTC to match database timestamps

        print(f"   🔖 Marking {symbol} as researched ({research_time.strftime('%H:%M:%S')} UTC)")

        # Always keep an in-memory record so fallback queries are consistent
        self.research_times[symbol] = research_time

        # Store in database if available
        if self.db.is_available():
            success = self.db.set_research_cooldown(symbol, research_time)
            if success:
                logging.debug(f"✅ Stored research cooldown for {symbol} in database")
            else:
                print(f"   ⚠️  Failed to store {symbol} cooldown in database")
                logging.debug(f"⚠️ Failed to store research cooldown for {symbol}, using memory")

        # Clean up old cooldowns aggressively when database is unavailable
        if not self.db.is_available():
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
            pruned = self._prune_research_times(cutoff_time)
            if pruned:
                logging.info(f"🧹 Cleaned up {pruned} old research entries (>24h)")
        elif len(self.research_times) % 100 == 0:
            # Periodically prune the database store when available
            self.db.cleanup_old_cooldowns(days_old=7)

    def _prune_research_times(self, cutoff_time: datetime) -> int:
        """Remove in-memory research cooldowns older than cutoff_time. Returns count pruned."""
        if not hasattr(self, 'research_times') or not self.research_times:
            return 0

        normalized_cutoff = cutoff_time if cutoff_time.tzinfo else cutoff_time.replace(tzinfo=timezone.utc)

        to_delete = []
        for symbol, ts in self.research_times.items():
            current_ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if current_ts < normalized_cutoff:
                to_delete.append(symbol)
        for symbol in to_delete:
            del self.research_times[symbol]
        return len(to_delete)
    
    def _get_fresh_ticker_list(self, target_count: int = 30) -> List[str]:
        """Generate a fresh list of tickers excluding cooldown, orders, and portfolio positions"""
        try:
            # Get all available symbols
            all_symbols = self.get_all_us_symbols()
            if not all_symbols:
                logging.error("❌ No symbols available from API")
                return []
            
            # Get exclusion lists
            recently_researched = self.get_recently_researched_tickers(cooldown_minutes=15)
            
            # Get current portfolio positions
            portfolio_symbols = set()
            try:
                positions = self.trading_client.get_all_positions()
                portfolio_symbols = {p.symbol for p in positions if float(p.qty) > 0}
            except Exception as e:
                logging.debug(f"Could not get portfolio positions: {e}")
            
            # Get symbols with pending orders
            pending_order_symbols = set()
            try:
                orders = list(self.trading_client.get_orders())
                from alpaca.trading.enums import OrderStatus
                open_statuses = {
                    OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW,
                    OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL,
                    OrderStatus.PENDING_REPLACE, OrderStatus.PENDING_REVIEW
                }
                pending_order_symbols = {order.symbol for order in orders if order.status in open_statuses}
            except Exception as e:
                logging.debug(f"Could not get pending orders: {e}")
            
            # Create exclusion set
            excluded_symbols = set(recently_researched) | portfolio_symbols | pending_order_symbols
            
            # Filter symbols
            fresh_symbols = [symbol for symbol in all_symbols if symbol not in excluded_symbols]
            
            logging.info(f"🔄 Generated fresh ticker list:")
            logging.info(f"   📊 Total symbols available: {len(all_symbols)}")
            logging.info(f"   ⏰ Recently researched (excluded): {len(recently_researched)}")
            logging.info(f"   💼 Portfolio positions (excluded): {len(portfolio_symbols)}")
            logging.info(f"   📋 Pending orders (excluded): {len(pending_order_symbols)}")
            logging.info(f"   ✅ Fresh symbols available: {len(fresh_symbols)}")
            
            if len(fresh_symbols) < target_count:
                logging.warning(f"⚠️  Only {len(fresh_symbols)} fresh symbols available after exclusions, less than target {target_count}")
            
            # Prioritize quality symbols (large caps, well-known tickers)
            quality_symbols = [
                # Mega caps
                'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'NVDA', 'TSLA', 'META', 'BRK.A', 'BRK.B',
                # Large cap tech
                'NFLX', 'ADBE', 'CRM', 'ORCL', 'CSCO', 'INTC', 'AMD', 'QCOM', 'AVGO', 'TXN',
                # Financials
                'JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'AXP', 'V', 'MA', 'PYPL',
                # Healthcare
                'JNJ', 'PFE', 'MRK', 'ABT', 'TMO', 'UNH', 'CVS', 'ABBV', 'BMY', 'LLY',
                # Consumer
                'PG', 'KO', 'PEP', 'WMT', 'HD', 'MCD', 'SBUX', 'NKE', 'DIS', 'COST',
                # Industrials
                'BA', 'CAT', 'GE', 'MMM', 'HON', 'UPS', 'RTX', 'LMT', 'NOC', 'GD',
                # Energy
                'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'MPC', 'VLO', 'PSX', 'OXY', 'WMB',
                # REITs
                'AMT', 'CCI', 'EQIX', 'PLD', 'PSA', 'WELL', 'VTR', 'ARE', 'DLR', 'IRM',
                # ETFs
                'SPY', 'QQQ', 'IWM', 'VTI', 'VOO', 'VEA', 'VWO', 'BND', 'AGG', 'GLD'
            ]
            
            # Filter quality symbols and add to front of list
            available_quality = [symbol for symbol in quality_symbols if symbol in fresh_symbols]
            remaining_fresh = [symbol for symbol in fresh_symbols if symbol not in quality_symbols]
            
            # Combine: quality first, then random selection from remaining
            final_list = available_quality[:target_count//2]  # Use half target for quality
            if len(final_list) < target_count:
                remaining_needed = target_count - len(final_list)
                # Add random selection from remaining symbols
                import random
                if len(remaining_fresh) > remaining_needed:
                    final_list.extend(random.sample(remaining_fresh, remaining_needed))
                else:
                    final_list.extend(remaining_fresh)
            
            logging.info(f"🔄 Generated fresh ticker list:")
            logging.info(f"   📊 Total symbols available: {len(all_symbols)}")
            logging.info(f"   ⏰ Recently researched (excluded): {len(recently_researched)}")
            logging.info(f"   💼 Portfolio positions (excluded): {len(portfolio_symbols)}")
            logging.info(f"   📋 Pending orders (excluded): {len(pending_order_symbols)}")
            logging.info(f"   ✅ Fresh symbols available: {len(fresh_symbols)}")
            logging.info(f"   🎯 Selected for analysis: {len(final_list)}")
            
            return final_list[:target_count]
            
        except Exception as e:
            logging.error(f"❌ Failed to generate fresh ticker list: {e}")
            # Fallback to basic list
            basic_fallback = ['SPY', 'QQQ', 'IWM', 'VTI', 'VOO', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']
            return basic_fallback
    
    def _get_safe_timestamp(self, timestamp):
        """Safely convert timestamp to ISO format"""
        if timestamp is None:
            return datetime.now(timezone.utc).isoformat()
        
        try:
            if isinstance(timestamp, str):
                # Handle various string formats
                return datetime.fromisoformat(str(timestamp).replace('Z', '+00:00')).isoformat()
            elif hasattr(timestamp, 'isoformat'):
                # Handle datetime objects
                return timestamp.isoformat()
            else:
                # Fallback to current time
                return datetime.now(timezone.utc).isoformat()
        except Exception:
            # If all else fails, use current time
            return datetime.now(timezone.utc).isoformat()
    
    def execute_trade(self, analysis: Dict) -> bool:
        """Execute trade based on analysis with comprehensive position and risk management"""
        try:
            symbol = analysis['symbol']
            signal = analysis['signal']
            price = analysis['price']
            signal_strength = analysis.get('signal_strength', 'WEAK')
            
            if not signal:
                return False
            
            # Get portfolio value for percentage-based calculations
            portfolio_value = self.get_portfolio_total_value()
            
            # Check for pending orders first
            if self.has_pending_orders(symbol):
                logging.info(f"🚫 Skipping {symbol}: Pending order exists")
                return False
            
            # Check cooldown period for BUY signals to prevent excessive repeat trades
            if signal == 'BUY' and self.is_in_cooldown(symbol, cooldown_minutes=15):
                logging.info(f"⏰ Skipping {symbol}: In 15-minute cooldown period")
                return False
            
            # For SELL signals, check if we actually own the stock
            if signal == 'SELL':
                try:
                    position = self.trading_client.get_open_position(symbol)
                    if not position or float(position.qty) <= 0:
                        logging.info(f"🚫 Cannot SELL {symbol}: No position found (qty: {float(position.qty) if position else 0})")
                        return False
                    # For sells, use actual position quantity (or portion of it)
                    max_sellable = int(float(position.qty))
                    desired_quantity = int(self.trade_amount / price)
                    # Check if this is a position sell with specific quantity
                    if analysis.get('position_sell_qty'):
                        quantity = min(analysis['position_sell_qty'], max_sellable)
                    else:
                        quantity = min(desired_quantity, max_sellable)
                except Exception as e:
                    logging.info(f"🚫 Cannot SELL {symbol}: Position check failed - {e}")
                    return False
            else:
                # For BUY signals, use intelligent position sizing
                current_position_qty = self.get_current_position_size(symbol)
                
                # Calculate intelligent quantity based on signal strength and portfolio percentage
                quantity = self.calculate_position_size(symbol, signal_strength, price, portfolio_value)
                
                # Check position concentration limits
                within_limits, adjusted_quantity, position_percentage = self.check_position_limits(
                    symbol, quantity, price, portfolio_value
                )
                
                if not within_limits:
                    if adjusted_quantity <= 0:
                        logging.info(f"🚫 Skipping {symbol}: Would exceed 10% position limit (currently {position_percentage:.1f}%)")
                        return False
                    else:
                        logging.info(f"⚠️ Reducing {symbol} quantity from {quantity} to {adjusted_quantity} shares (position limit: {position_percentage:.1f}%)")
                        quantity = adjusted_quantity
                
                # Add position size context to logging
                if current_position_qty > 0:
                    new_total_qty = current_position_qty + quantity
                    logging.info(f"📊 {symbol} Position: Currently {current_position_qty} shares, adding {quantity} → {new_total_qty} total")
            
            if quantity <= 0:
                return False
            
            side = OrderSide.BUY if signal == 'BUY' else OrderSide.SELL
            
            market_order_data = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=side,
                time_in_force=TimeInForce.DAY
            )

            # Ensure we don't overspend while orders are pending
            order_value = price * quantity
            if side == OrderSide.BUY:
                effective_cash, reserved_cash = self.get_effective_cash_available()
                if order_value > effective_cash:
                    max_affordable_qty = int(effective_cash // price)
                    if max_affordable_qty <= 0:
                        logging.info(f"🚫 Skipping {symbol} BUY: insufficient effective cash (${effective_cash:,.2f}) after reserving pending orders")
                        return False
                    logging.info(
                        f"⚠️ Reducing {symbol} quantity from {quantity} to {max_affordable_qty} to respect reserved cash "
                        f"(effective cash ${effective_cash:,.2f}, reserved ${reserved_cash:,.2f})"
                    )
                    quantity = max_affordable_qty
                    order_value = price * quantity
            
            order = self.trading_client.submit_order(order_data=market_order_data)

            # Reserve cash for open/pending BUY orders to prevent double-spend until filled/cancelled
            if side == OrderSide.BUY:
                self._cash_reserves[str(order.id)] = order_value
                logging.info(
                    f"💵 Reserved ${order_value:,.2f} for pending BUY {symbol}; "
                    f"total reserved now ${self.get_reserved_cash():,.2f}"
                )
            
            # Log to database if available
            if self.db.is_available() and self.session_id:
                trade_data = {
                    'session_id': self.session_id,
                    'alpaca_order_id': str(order.id) if order.id else None,
                    'symbol': symbol,
                    'side': side.upper(),
                    'quantity': quantity,
                    'order_price': price,
                    'signal_time': self._get_safe_timestamp(analysis.get('timestamp')),
                    'order_time': datetime.now(timezone.utc).isoformat(),
                    'sma_fast': analysis['sma_fast'],
                    'sma_slow': analysis['sma_slow'],
                    'rsi': analysis['rsi'],
                    'signal_strength': analysis['signal_strength'],
                    'status': 'SUBMITTED'
                }
                self.db.log_trade(self.session_id, trade_data)
            
            self.trades_executed += 1
            
            # Mark trade for cooldown tracking
            self.mark_recent_trade(symbol, signal)
            
            # Store trade details for session summary (access parent scope)
            if hasattr(self, '_current_trades_details'):
                trade_detail = {
                    'symbol': symbol,
                    'action': signal,
                    'quantity': quantity,
                    'price': price,
                    'total_value': price * quantity,
                    'rsi': analysis['rsi'],
                    'signal_strength': analysis['signal_strength'],
                    'ai_insight': analysis.get('ai_insight', '')
                }
                self._current_trades_details.append(trade_detail)
            
            # Print prominent trade execution summary
            print("\n" + "✅"*20 + " TRADE EXECUTED " + "✅"*20)
            print(f"💰 ACTION: {signal} {quantity} shares of {symbol}")
            print(f"💵 PRICE: ${price:.2f} per share")
            print(f"📊 TOTAL VALUE: ${price * quantity:,.2f}")
            
            # Show position sizing context for buys
            if signal == 'BUY':
                trade_percentage = (price * quantity / portfolio_value) * 100
                print(f"📊 POSITION SIZING: {trade_percentage:.2f}% of portfolio (${portfolio_value:,.0f})")
                current_pos_qty = self.get_current_position_size(symbol)
                if current_pos_qty > 0:
                    new_total = current_pos_qty + quantity
                    total_value = new_total * price
                    total_percentage = (total_value / portfolio_value) * 100
                    print(f"📈 TOTAL POSITION: {new_total} shares = {total_percentage:.2f}% of portfolio")
            
            print(f"\n📈 TECHNICAL ANALYSIS:")
            print(f"   RSI: {analysis['rsi']:.1f} | SMA Fast: ${analysis['sma_fast']:.2f} | SMA Slow: ${analysis['sma_slow']:.2f}")
            print(f"   Signal Strength: {analysis['signal_strength']}")
            
            # Show position validation for sells
            if signal == 'SELL':
                try:
                    position = self.trading_client.get_open_position(symbol)
                    remaining_shares = float(position.qty) - quantity
                    print(f"📊 POSITION: Had {float(position.qty)} shares, selling {quantity}, keeping {remaining_shares}")
                except:
                    pass
            if 'ai_insight' in analysis:
                print(f"   🧠 AI Insight: {analysis['ai_insight']}")
            print(f"\n🎯 WHY THIS TRADE:")
            if signal == 'BUY':
                for line in format_buy_rationale(price, analysis['sma_fast'], analysis['sma_slow'], analysis['rsi'], signal_strength):
                    print(f"   • {line}")

                if self.get_current_position_size(symbol) > 0:
                    print(f"   • POSITION: Adding to existing position (concentration managed)")
            else:
                for line in format_sell_rationale(price, analysis['sma_fast'], analysis['sma_slow'], analysis['rsi']):
                    print(f"   • {line}")
            print(f"\n💼 Order ID: {order.id}")
            print("✅" + "="*58 + "✅")
            
            # Also log for database
            logging.info(f"✅ TRADE: {signal} {quantity} {symbol} @ ${price:.2f} (${price * quantity:,.2f}) - Order: {order.id}")
            
            return True
            
        except Exception as e:
            logging.error(f"❌ Trade failed for {analysis['symbol']}: {e}")
            self.errors_count += 1
            return False
    
    def run_analysis(self, max_symbols: int = 50, max_trades: int = 3, use_ai: bool = False):
        """Run trading analysis with optional AI enhancement"""
        # Determine AI usage: check for rate limits, then use provided parameter
        if use_ai is not None:
            # Explicit parameter provided - respect rate limits
            ai_enabled = use_ai and not self.rate_limit_detected
        else:
            # Auto-determine: try to re-enable if appropriate, otherwise use current state
            if self._should_retry_ai():
                ai_enabled = self.ai.is_configured
            else:
                ai_enabled = self.ai.is_configured and not self.rate_limit_detected
        
        rate_limit_status = " (RATE LIMITED)" if self.rate_limit_detected else ""
        logging.info(f"🔍 Starting analysis (max {max_symbols} symbols, max {max_trades} trades, AI: {ai_enabled}{rate_limit_status})")
        

        trades_executed = 0
        opportunities = 0
        ai_enhanced_trades = 0
        symbols_skipped_orders = 0
        symbols_skipped_cooldown = 0
        position_sells_executed = 0
        trades_executed_details = []  # Track trade details for summary
        self._current_trades_details = trades_executed_details  # Make accessible to execute_trade
        opportunities_not_traded = []  # Track signals found but not executed with reasons
        
        # Track reasons for no trades
        no_trade_reasons = {
            'no_signal': 0,  # No buy/sell signal detected
            'weak_signal': 0,  # Signal too weak
            'conflicted_signal': 0,  # AI conflicts with technical analysis
            'no_data': 0,  # No market data available
            'max_trades_reached': 0,  # Already hit max trades
        }
        
        # Perform portfolio analysis before trading
        logging.info("📊 Pre-trading portfolio analysis...")
        portfolio_analysis = self.analyze_portfolio()
        if portfolio_analysis:
            self.execute_portfolio_actions(portfolio_analysis)
        
        # Analyze current positions for selling opportunities
        logging.info("\n🔍 Analyzing current positions for selling...")
        positions_analyzed = 0
        positions_skipped_cooldown = 0
        
        # Get initial position count for cooldown tracking
        try:
            all_positions = self.trading_client.get_all_positions()
            total_positions = len([p for p in all_positions if float(p.qty) > 0 and float(p.market_value) >= 50])
            positions_in_cooldown = sum(1 for p in all_positions 
                                      if float(p.qty) > 0 and float(p.market_value) >= 50 
                                      and self.is_position_sell_in_cooldown(p.symbol, cooldown_minutes=30))
            positions_analyzed = total_positions - positions_in_cooldown
            positions_skipped_cooldown = positions_in_cooldown
            
            if positions_skipped_cooldown > 0:
                logging.info(f"   ⏰ {positions_skipped_cooldown} positions in sell-analysis cooldown (30 minutes)")
        except:
            pass
        
        sell_candidates = self.analyze_current_positions_for_selling(use_ai=ai_enabled)
        
        # Execute position sells before looking for new buys
        for candidate in sell_candidates:
            if position_sells_executed >= max_trades:
                logging.info(f"   ⏹️  Max trades reached, remaining sell candidates queued")
                break
                
            logging.info(f"\n💰 EXECUTING POSITION SELL: {candidate['symbol']}")
            logging.info(f"   📊 Type: {candidate['sell_type']} ({candidate['sell_qty']}/{candidate['current_qty']} shares)")
            logging.info(f"   🎯 Confidence: {candidate['confidence']}%")
            logging.info(f"   💹 Current P&L: {candidate['unrealized_plpc']:+.1f}%")
            
            # Create sell analysis for execute_trade
            sell_analysis = {
                'symbol': candidate['symbol'],
                'signal': 'SELL',
                'signal_strength': 'STRONG' if candidate['confidence'] >= 70 else 'MEDIUM',
                'price': candidate['price'],
                'rsi': candidate['technical_analysis']['rsi'],
                'sma_fast': candidate['technical_analysis']['sma_fast'],
                'sma_slow': candidate['technical_analysis']['sma_slow'],
                'ai_insight': candidate.get('ai_reason', ''),
                'position_sell_qty': candidate['sell_qty']  # Override quantity for position sells
            }
            
            if self.execute_trade(sell_analysis):
                position_sells_executed += 1
                trades_executed += 1
                logging.info(f"   ✅ Position sell executed successfully")
            else:
                logging.info(f"   ❌ Position sell failed")
            
            time.sleep(1)  # Rate limiting
        
        # Update max_trades for remaining buy opportunities
        remaining_trades = max_trades - trades_executed
        if remaining_trades <= 0:
            logging.info(f"\n🏁 All {max_trades} trades used for position management")
            # Still do the final summary - no symbol analysis done
            print(f"📈 Symbols Analyzed: 0 researched, 0 skipped (pending orders), 0 skipped (cooldown)")
            if position_sells_executed > 0:
                print(f"💰 Position Management: {position_sells_executed} position sells executed")
            logging.info(f"📈 Symbols Analyzed: 0 researched, 0 skipped (pending orders), 0 skipped (cooldown)")
            if position_sells_executed > 0:
                logging.info(f"💰 Position Management: {position_sells_executed} position sells executed")
            
            return
        
        logging.info(f"\n🔍 Looking for new opportunities ({remaining_trades} trades remaining)...")
        max_trades = remaining_trades  # Update for the buy loop
        
        # Get AI-recommended tickers based on portfolio analysis (if ticker selection AI is enabled)
        # Note: This check is independent of ai_enabled to allow fine-grained control
        if portfolio_analysis and self.ai.is_configured and self.use_ai_for_ticker_selection and not self.rate_limit_detected:
            logging.info("🧠 Getting AI-recommended tickers based on portfolio analysis...")
            try:
                symbols = self.get_ai_recommended_tickers(portfolio_analysis)
            except Exception as e:
                if self._detect_rate_limit_error(str(e)) or self._detect_ai_failure_error(str(e)):
                    self._handle_rate_limit_error(e)
                logging.warning(f"⚠️ AI ticker recommendation failed: {e}")
                symbols = self.get_all_us_symbols()
                symbols = symbols[:max_symbols]
        else:
            if not self.use_ai_for_ticker_selection:
                logging.info("📊 Using standard ticker list (AI ticker selection disabled)")
            symbols = self.get_all_us_symbols()
            symbols = symbols[:max_symbols]
        
        if not symbols:
            logging.error("❌ No symbols available")
            return
        
        # Create AI market summary if enabled
        # Note: This check is independent of ai_enabled to allow fine-grained control  
        if self.ai.is_configured and self.use_ai_for_market_summary and not self.rate_limit_detected:
            try:
                import asyncio
                market_summary = asyncio.run(self.ai.create_market_summary(symbols[:5]))
                
                logging.info("\n" + "="*60)
                logging.info("🧠 AI MARKET ANALYSIS SUMMARY")
                logging.info("="*60)
                logging.info(f"📊 Overall Sentiment: {market_summary.get('overall_sentiment', 'neutral').upper()}")
                if 'key_trends' in market_summary:
                    logging.info(f"🔍 Key Trends: {', '.join(market_summary['key_trends'])}")
                if 'risk_factors' in market_summary:
                    logging.info(f"⚠️  Risk Factors: {', '.join(market_summary['risk_factors'])}")
                logging.info("="*60)
                
            except Exception as e:
                logging.warning(f"⚠️  AI market summary failed: {e}")
        elif not self.use_ai_for_market_summary:
            logging.info("📊 AI market summary disabled")
        
        for i, symbol in enumerate(symbols, 1):
            try:
                self.symbols_processed += 1
                
                if i % 10 == 0:
                    skipped_total = symbols_skipped_orders + symbols_skipped_cooldown
                    logging.info(f"📊 Progress: {i}/{len(symbols)} symbols ({skipped_total} skipped), {opportunities} opportunities, {trades_executed} trades")
                
                # Skip research if there are pending orders for this symbol
                if self.has_pending_orders(symbol):
                    symbols_skipped_orders += 1
                    logging.debug(f"⏭️  {symbol}: Skipping research - pending order exists")
                    continue
                
                # Note: Cooldown filtering now happens at ticker selection stage
                # This check is kept as a safety net for edge cases
                if self.is_in_research_cooldown(symbol, cooldown_minutes=15):
                    symbols_skipped_cooldown += 1
                    logging.debug(f"⏭️  {symbol}: Skipping research - in research cooldown period (safety check)")
                    continue
                
                analysis = self.analyze_symbol(symbol, use_ai=ai_enabled)
                if not analysis:
                    print(f"   ⏭️  {symbol}: ❌ Insufficient market data (needs {self.sma_slow} bars minimum)")
                    no_trade_reasons['no_data'] += 1
                    continue
                    
                if analysis['signal']:
                    opportunities += 1
                    
                    # Check if we should skip due to weak/conflicted signals
                    if analysis['signal_strength'] == "WEAK":
                        no_trade_reasons['weak_signal'] += 1
                        opportunities_not_traded.append({
                            'symbol': symbol,
                            'reason': f"Signal too weak ({analysis['signal_strength']})"
                        })
                        print(f"   ⏭️  {symbol}: ⚠️  {analysis['signal']} signal too weak (RSI: {analysis['rsi']:.1f}, needs < 25 or > 75)")
                        continue
                    elif analysis['signal_strength'] == "CONFLICTED":
                        no_trade_reasons['conflicted_signal'] += 1
                        opportunities_not_traded.append({
                            'symbol': symbol,
                            'reason': "AI conflicts with technical signal"
                        })
                        print(f"   ⏭️  {symbol}: ⚠️  AI conflicts with technical {analysis['signal']} signal")
                        continue
                    
                    # Enhanced logging with AI insights
                    ai_info = ""
                    if analysis.get('ai_insight'):
                        ai_info = f" | {analysis['ai_insight']}"
                        if analysis['signal_strength'] == "AI_ENHANCED":
                            ai_enhanced_trades += 1
                    
                    # Enhanced decision logging
                    logging.info(f"\n🔍 {symbol} ANALYSIS COMPLETE:")
                    logging.info(f"   📊 Signal: {analysis['signal']} ({analysis['signal_strength']})")
                    logging.info(f"   💰 Price: ${analysis['price']:.2f}")
                    logging.info(f"   📈 RSI: {analysis['rsi']:.1f} | SMA Fast: ${analysis['sma_fast']:.2f} | SMA Slow: ${analysis['sma_slow']:.2f}")
                    if analysis.get('ai_insight'):
                        logging.info(f"   🧠 AI Insight: {analysis['ai_insight']}")
                    
                    if trades_executed < max_trades:
                        logging.info(f"   ➡️  Executing {analysis['signal']} trade...")
                        if self.execute_trade(analysis):
                            trades_executed += 1
                        time.sleep(1)  # Rate limiting
                    else:
                        logging.info(f"   ⏸️  Signal detected but max trades reached ({max_trades})")
                        no_trade_reasons['max_trades_reached'] += 1
                        opportunities_not_traded.append({
                            'symbol': symbol,
                            'reason': f"Max trades reached ({max_trades})"
                        })
                else:
                    # No signal detected - show which criteria failed
                    no_trade_reasons['no_signal'] += 1
                    rsi = analysis['rsi']
                    sma_fast = analysis['sma_fast']
                    sma_slow = analysis['sma_slow']
                    
                    # Determine what failed
                    failed_criteria = []
                    if sma_fast <= sma_slow:
                        failed_criteria.append(f"SMA bearish (Fast ${sma_fast:.2f} ≤ Slow ${sma_slow:.2f})")
                    if rsi >= self.rsi_buy_threshold:
                        failed_criteria.append(f"RSI not oversold ({rsi:.1f} ≥ {self.rsi_buy_threshold})")
                    
                    # Check sell criteria too
                    if sma_fast >= sma_slow and rsi <= self.rsi_sell_threshold:
                        failed_criteria = [f"RSI not overbought ({rsi:.1f} ≤ {self.rsi_sell_threshold})"]
                    
                    failure_msg = ", ".join(failed_criteria) if failed_criteria else f"No clear trend (RSI: {rsi:.1f})"
                    print(f"   ⏭️  {symbol}: ⊗ {failure_msg}")
                
                time.sleep(0.1)  # API rate limiting
                
            except Exception as e:
                logging.error(f"❌ Error with {symbol}: {e}")
                self.errors_count += 1
        
        # Enhanced completion summary
        ai_summary = f", {ai_enhanced_trades} AI-enhanced" if ai_enabled else ""
        # Print detailed trades executed in this session
        if trades_executed_details:
            print("\n" + "="*60)
            print(f"💼 TRADES EXECUTED THIS SESSION ({len(trades_executed_details)})")
            print("="*60)
            total_session_value = 0
            for i, trade in enumerate(trades_executed_details, 1):
                print(f"\n🔢 TRADE #{i}:")
                print(f"   🎢 {trade['action']} {trade['quantity']} shares of {trade['symbol']}")
                print(f"   💰 Price: ${trade['price']:.2f} | Total: ${trade['total_value']:,.2f}")
                print(f"   📈 RSI: {trade['rsi']:.1f} | Signal: {trade['signal_strength']}")
                if trade['ai_insight']:
                    print(f"   🧠 AI: {trade['ai_insight'][:60]}{'...' if len(trade['ai_insight']) > 60 else ''}")
                total_session_value += trade['total_value']
            print(f"\n💵 TOTAL SESSION TRADE VALUE: ${total_session_value:,.2f}")
            print("="*60)
        elif trades_executed == 0:
            print("\n🚫 NO TRADES EXECUTED THIS SESSION")
            
            # Show detailed reasons why no trades were executed
            reasons = []
            if no_trade_reasons['no_signal'] > 0:
                reasons.append(f"{no_trade_reasons['no_signal']} symbols had no buy/sell signals (RSI not in buy/sell zones)")
            if no_trade_reasons['weak_signal'] > 0:
                reasons.append(f"{no_trade_reasons['weak_signal']} signals were too weak to act on")
            if no_trade_reasons['conflicted_signal'] > 0:
                reasons.append(f"{no_trade_reasons['conflicted_signal']} had AI/technical conflicts")
            if no_trade_reasons['no_data'] > 0:
                reasons.append(f"{no_trade_reasons['no_data']} symbols had insufficient data")
            if no_trade_reasons['max_trades_reached'] > 0:
                reasons.append(f"{no_trade_reasons['max_trades_reached']} opportunities found after max trades reached")
            
            if reasons:
                print("   📋 Reasons:")
                for reason in reasons:
                    print(f"      • {reason}")
            else:
                print("   Market conditions did not meet trading criteria")

            if opportunities_not_traded:
                print("\n   🎯 Opportunities not traded:")
                for item in opportunities_not_traded:
                    print(f"      • {item['symbol']}: {item['reason']}")
            
            # Show trading thresholds for reference
            print(f"\n   ⚙️  Current Trading Criteria:")
            print(f"      • Buy Signal: RSI < {self.rsi_buy_threshold} AND Fast SMA > Slow SMA")
            print(f"      • Sell Signal: RSI > {self.rsi_sell_threshold} AND Fast SMA < Slow SMA")
            print(f"      • Minimum Signal Strength: MEDIUM or higher")
        
        # Print session trade summary
        print("\n" + "="*60)
        print("🏁 TRADING SESSION SUMMARY")
        print("="*60)
        print(f"📊 Analysis Results: {opportunities} opportunities identified")
        print(f"💼 Trades Executed: {trades_executed} trades")
        if ai_enhanced_trades > 0:
            print(f"🧠 AI-Enhanced Trades: {ai_enhanced_trades}")
        symbols_researched = len(symbols) - symbols_skipped_orders - symbols_skipped_cooldown
        print(f"📈 Symbols Analyzed: {symbols_researched} researched, {symbols_skipped_orders} skipped (pending orders), {symbols_skipped_cooldown} skipped (cooldown)")
        if position_sells_executed > 0:
            print(f"💰 Position Management: {position_sells_executed} position sells executed")
        
        # Show position analysis stats if we have positions
        try:
            all_positions = self.trading_client.get_all_positions()
            total_positions = len([p for p in all_positions if float(p.qty) > 0 and float(p.market_value) >= 50])
            if total_positions > 0:
                positions_in_cooldown = sum(1 for p in all_positions 
                                          if float(p.qty) > 0 and float(p.market_value) >= 50 
                                          and self.is_position_sell_in_cooldown(p.symbol, cooldown_minutes=30))
                positions_analyzed = total_positions - positions_in_cooldown
                print(f"📊 Position Analysis: {positions_analyzed} analyzed, {positions_in_cooldown} skipped (sell cooldown)")
        except:
            pass
        
        # Store loop stats for continuous mode tracking
        self._last_loop_trades = trades_executed
        self._last_loop_opportunities = opportunities
        self._last_loop_trade_details = trades_executed_details
        
        # Also log for database
        logging.info("\n" + "="*60)
        logging.info("🏁 TRADING SESSION SUMMARY")  
        logging.info("="*60)
        logging.info(f"📊 Analysis Results: {opportunities} opportunities identified")
        logging.info(f"💼 Trades Executed: {trades_executed} trades")
        if ai_enhanced_trades > 0:
            logging.info(f"🧠 AI-Enhanced Trades: {ai_enhanced_trades}")
        symbols_researched = len(symbols) - symbols_skipped_orders - symbols_skipped_cooldown
        logging.info(f"📈 Symbols Analyzed: {symbols_researched} researched, {symbols_skipped_orders} skipped (pending orders), {symbols_skipped_cooldown} skipped (cooldown)")
        if trades_executed == 0 and opportunities_not_traded:
            logging.info("🎯 Opportunities not traded:")
            for item in opportunities_not_traded:
                logging.info(f"   • {item['symbol']}: {item['reason']}")
        
        # Final portfolio analysis
        logging.info("\n📊 POST-TRADING PORTFOLIO STATUS:")
        final_portfolio = self.analyze_portfolio()
        if final_portfolio:
            logging.info(f"💰 Total Value: ${final_portfolio['total_value']:,.2f}")
            logging.info(f"📈 Unrealized P&L: ${final_portfolio['total_unrealized_pnl']:,.2f}")
            logging.info(f"🎯 Win Rate: {final_portfolio['win_rate']:.1f}%")
            logging.info(f"📊 Total Positions: {final_portfolio['total_positions']}")
            logging.info(f"💵 Cash: ${final_portfolio['cash_available']:,.2f} ({final_portfolio['cash_percentage']:.1f}%)")
        logging.info("="*60)
    
    def show_database_status(self):
        """Show database status and recent sessions"""
        if not self.db.is_available():
            logging.info("📊 No database connection - running in local mode only")
            return
        
        # Get database info
        db_info = self.db.get_database_info()
        db_health = "🟢 DB OK"
        if hasattr(self.db, "last_error") and self.db.last_error:
            # Extract which operation failed for clearer status
            error_detail = ""
            if "research_cooldown" in self.db.last_error:
                error_detail = " (cooldown queries failing)"
            elif "status 400" in self.db.last_error:
                error_detail = " (HTTP 400)"
            db_health = f"🔴 DB Degraded{error_detail}"
        
        print("\n" + "="*60)
        print("📊 DATABASE STATUS")
        print("="*60)
        print(f"Connection: {'✅ Connected' if db_info['available'] else '❌ Disconnected'}")
        print(f"Tables: {'✅ Exist' if db_info['tables_exist'] else '❌ Missing'}")
        print(f"Schema Version: {db_info['schema_version'] or 'Unknown'}")
        print(f"Total Sessions: {db_info['total_sessions']}")
        print(f"Total Trades: {db_info['total_trades']}")
        print(f"Health: {db_health}")
        
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
            
            # Log what we actually fetched from Alpaca for debugging
            logging.debug(f"Fetched from Alpaca: {len(positions)} positions, cash=${float(account.cash):,.2f}, portfolio_value=${float(account.portfolio_value):,.2f}")
            if positions:
                position_symbols = [p.symbol for p in positions]
                logging.debug(f"Position symbols from Alpaca: {position_symbols}")
            
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
            
            reserved_cash = self.get_reserved_cash()
            effective_cash = max(portfolio_data['account']['cash'] - reserved_cash, 0)

            portfolio_analysis = {
                'total_positions': len(positions),
                'total_value': portfolio_data['account']['portfolio_value'],
                'total_unrealized_pnl': total_unrealized_pnl,
                'cash_available': effective_cash,
                'reserved_cash': reserved_cash,
                'cash_percentage': (effective_cash / portfolio_data['account']['portfolio_value']) * 100 if portfolio_data['account']['portfolio_value'] > 0 else 0,
                'win_rate': win_rate,
                'concentration_risk': top_5_concentration,
                'largest_position': position_weights[0] if position_weights else ('N/A', 0),
                'winners': len(winners),
                'losers': len(losers),
                'positions': portfolio_data['positions']
            }
            
            # Print detailed portfolio summary for visibility
            print("\n" + "="*60)
            print("📊 PORTFOLIO ANALYSIS SUMMARY")
            print("="*60)
            
            # Also log for database/file logging
            logging.info("\n" + "="*60)
            logging.info("📊 PORTFOLIO ANALYSIS SUMMARY")
            logging.info("="*60)
            # Print metrics for visibility
            print(f"💰 Total Portfolio Value: ${portfolio_analysis['total_value']:,.2f}")
            print(f"💵 Cash Available: ${portfolio_analysis['cash_available']:,.2f} ({portfolio_analysis['cash_percentage']:.1f}%)")
            if portfolio_analysis.get('reserved_cash', 0) > 0:
                print(f"💰 Reserved for Open Buys: ${portfolio_analysis['reserved_cash']:,.2f}")
            print(f"📈 Unrealized P&L: ${portfolio_analysis['total_unrealized_pnl']:,.2f}")
            print(f"🎯 Win Rate: {portfolio_analysis['win_rate']:.1f}% ({portfolio_analysis['winners']} winners, {portfolio_analysis['losers']} losers)")
            print(f"⚠️  Concentration Risk: {portfolio_analysis['concentration_risk']:.1f}% in top 5 positions")
            
            # Also log
            logging.info(f"💰 Total Portfolio Value: ${portfolio_analysis['total_value']:,.2f}")
            logging.info(f"💵 Cash Available: ${portfolio_analysis['cash_available']:,.2f} ({portfolio_analysis['cash_percentage']:.1f}%)")
            if portfolio_analysis.get('reserved_cash', 0) > 0:
                logging.info(f"💰 Reserved for Open Buys: ${portfolio_analysis['reserved_cash']:,.2f}")
            logging.info(f"📈 Unrealized P&L: ${portfolio_analysis['total_unrealized_pnl']:,.2f}")
            logging.info(f"🎯 Win Rate: {portfolio_analysis['win_rate']:.1f}% ({portfolio_analysis['winners']} winners, {portfolio_analysis['losers']} losers)")
            logging.info(f"⚠️  Concentration Risk: {portfolio_analysis['concentration_risk']:.1f}% in top 5 positions")
            
            if portfolio_analysis['largest_position'][0] != 'N/A':
                logging.info(f"🔝 Largest Position: {portfolio_analysis['largest_position'][0]} ({portfolio_analysis['largest_position'][1]:.1f}%)")
            
            # Show recommendations based on analysis
            logging.info("\n🎯 PORTFOLIO RECOMMENDATIONS:")
            if portfolio_analysis['concentration_risk'] > 60:
                logging.info("   ⚠️  HIGH CONCENTRATION RISK - Diversification needed")
            if portfolio_analysis['cash_percentage'] > 20:
                logging.info(f"   💰 EXCESS CASH - {portfolio_analysis['cash_percentage']:.1f}% available for deployment")
            if portfolio_analysis['win_rate'] < 30:
                logging.info("   📉 LOW WIN RATE - Focus on quality, defensive positions")
            if len(portfolio_analysis.get('high_concentration_positions', [])) > 0:
                logging.info(f"   🎯 {len(portfolio_analysis['high_concentration_positions'])} positions >15% of portfolio")
            logging.info("="*60)
            
            return portfolio_analysis
            
        except Exception as e:
            logging.error(f"❌ Portfolio analysis failed: {e}")
            return None
    
    def analyze_current_positions_for_selling(self, use_ai: bool = False):
        """Analyze current positions for potential selling opportunities"""
        try:
            logging.info("📊 Analyzing current positions for selling opportunities...")
            
            positions = self.trading_client.get_all_positions()
            if not positions:
                logging.info("   📭 No positions to analyze")
                return []
            
            sell_candidates = []
            
            for position in positions:
                symbol = position.symbol
                current_qty = float(position.qty)
                unrealized_plpc = float(position.unrealized_plpc) * 100
                market_value = float(position.market_value)
                current_price = float(position.current_price) if position.current_price else 0
                
                # Skip positions too small to sell
                if current_qty <= 0 or market_value < 50:
                    continue
                
                # Check if this position's sell analysis is in cooldown
                if self.is_position_sell_in_cooldown(symbol, cooldown_minutes=30):
                    logging.debug(f"⏭️  {symbol}: Skipping sell analysis - in 30-minute cooldown")
                    continue
                
                logging.info(f"🔍 Analyzing position: {symbol} ({current_qty} shares, {unrealized_plpc:+.1f}%)")
                
                # Run technical analysis on the held position
                analysis = self.analyze_symbol(symbol, use_ai=use_ai)
                if not analysis:
                    continue
                
                sell_reasons = []
                confidence = 0
                
                # Technical analysis for selling
                if analysis['signal'] == 'SELL':
                    sell_reasons.append(f"Technical SELL signal (RSI: {analysis['rsi']:.1f})")
                    confidence += 40
                
                # Profit-taking logic
                if unrealized_plpc > 25:  # More than 25% gain
                    sell_reasons.append(f"Strong profit (+{unrealized_plpc:.1f}%)")
                    confidence += 30
                elif unrealized_plpc > 10:  # More than 10% gain
                    sell_reasons.append(f"Moderate profit (+{unrealized_plpc:.1f}%)")
                    confidence += 15
                
                # Stop-loss logic
                if unrealized_plpc < -8:  # More than 8% loss
                    sell_reasons.append(f"Stop loss (-{abs(unrealized_plpc):.1f}%)")
                    confidence += 35
                elif unrealized_plpc < -5:  # More than 5% loss
                    sell_reasons.append(f"Defensive sell (-{abs(unrealized_plpc):.1f}%)")
                    confidence += 20
                
                # Concentration risk (position too large)
                portfolio_value = float(self.trading_client.get_account().portfolio_value)
                position_weight = abs(market_value) / portfolio_value * 100
                if position_weight > 12:  # More than 12% of portfolio
                    sell_reasons.append(f"Concentration risk ({position_weight:.1f}% of portfolio)")
                    confidence += 25
                
                # RSI overbought condition for held positions
                if analysis['rsi'] > 75:
                    sell_reasons.append(f"Overbought (RSI: {analysis['rsi']:.1f})")
                    confidence += 20
                
                # AI enhancement
                ai_reason = ""
                if analysis.get('ai_insight') and 'sell' in analysis.get('ai_insight', '').lower():
                    ai_reason = f"AI suggests selling: {analysis['ai_insight']}"
                    confidence += 25
                
                # Only consider selling if we have good reasons
                if sell_reasons and confidence >= 30:
                    # Calculate sell quantity (partial or full)
                    if confidence >= 70 or unrealized_plpc < -8:  # Full sell for high confidence or stop loss
                        sell_qty = int(current_qty)
                        sell_type = "FULL"
                    elif confidence >= 50:  # Partial sell for medium confidence
                        sell_qty = max(1, int(current_qty * 0.5))  # Sell 50%
                        sell_type = "PARTIAL (50%)"
                    else:  # Small partial sell for lower confidence
                        sell_qty = max(1, int(current_qty * 0.25))  # Sell 25%
                        sell_type = "PARTIAL (25%)"
                    
                    sell_candidate = {
                        'symbol': symbol,
                        'current_qty': int(current_qty),
                        'sell_qty': sell_qty,
                        'sell_type': sell_type,
                        'price': current_price,
                        'unrealized_plpc': unrealized_plpc,
                        'position_weight': position_weight,
                        'confidence': confidence,
                        'reasons': sell_reasons,
                        'ai_reason': ai_reason,
                        'technical_analysis': {
                            'signal': analysis['signal'],
                            'rsi': analysis['rsi'],
                            'sma_fast': analysis['sma_fast'],
                            'sma_slow': analysis['sma_slow']
                        }
                    }
                    
                    sell_candidates.append(sell_candidate)
                    
                    logging.info(f"   🎯 SELL CANDIDATE: {symbol} - {sell_type} ({confidence}% confidence)")
                    for reason in sell_reasons:
                        logging.info(f"      • {reason}")
                    if ai_reason:
                        logging.info(f"      🧠 {ai_reason}")
                else:
                    logging.info(f"   ✅ {symbol}: Hold (confidence: {confidence}%, reasons: {len(sell_reasons)})")
                
                # Mark that we've analyzed this position (regardless of outcome)
                self.mark_position_sell_analysis(symbol)
            
            if sell_candidates:
                logging.info(f"\n📋 POSITION ANALYSIS COMPLETE: {len(sell_candidates)} sell candidates identified")
            else:
                logging.info(f"\n📋 POSITION ANALYSIS COMPLETE: No selling opportunities found")
            
            return sell_candidates
            
        except Exception as e:
            logging.error(f"❌ Position analysis failed: {e}")
            return []
    
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
            
            logging.info("\n" + "-"*40)
            logging.info(f"📋 PORTFOLIO ACTION: {side.upper()} {quantity} {symbol}")
            logging.info(f"🎯 Reason: {reason}")
            logging.info(f"💼 Order Status: Submitted")
            logging.info("-"*40)
            
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

    def run_continuous_loop(self, max_symbols: int = 30, max_trades: int = 2, loop_delay: int = 300, summary_interval: int = 50, use_ai: bool = None):
        """Run trading bot in continuous loop with periodic summaries"""
        loop_count = 0
        total_trades = 0
        total_opportunities = 0
        loop_performance = []
        ticker_positions = {}  # Track cumulative positions per ticker
        ticker_transactions = {}  # Track all transactions per ticker
        start_time = datetime.now(timezone.utc)
        
        print("\n" + "="*60)
        print("🔄 CONTINUOUS TRADING MODE ACTIVATED")
        print("="*60)
        print(f"🕰️ Loop Delay: {loop_delay} seconds")
        print(f"📊 Summary Every: {summary_interval} loops")
        print(f"💼 Max Trades Per Loop: {max_trades}")
        print(f"📈 Max Symbols Per Loop: {max_symbols}")
        print(f"\n🛑 Press Ctrl+C to stop gracefully")
        print("="*60)
        
        try:
            while True:
                loop_count += 1
                loop_start = datetime.now(timezone.utc)
                
                print(f"\n🔄 LOOP #{loop_count} - {loop_start.strftime('%H:%M:%S')}")
                print("-" * 40)
                
                try:
                    # Start session for this loop
                    self.start_session()
                    
                    # Run analysis
                    ai_enabled = use_ai if use_ai is not None else self.ai.is_configured
                    result = self.run_analysis(max_symbols=max_symbols, max_trades=max_trades, use_ai=ai_enabled)
                    
                    # Track performance
                    loop_end = datetime.now(timezone.utc)
                    loop_duration = (loop_end - loop_start).total_seconds()
                    
                    # Get loop stats and trade details
                    loop_trades = getattr(self, '_last_loop_trades', 0)
                    loop_opportunities = getattr(self, '_last_loop_opportunities', 0)
                    loop_trade_details = getattr(self, '_last_loop_trade_details', [])
                    
                    # Update ticker tracking
                    for trade in loop_trade_details:
                        symbol = trade['symbol']
                        action = trade['action']
                        quantity = trade['quantity']
                        price = trade['price']
                        total_value = trade['total_value']
                        reasoning = f"RSI: {trade['rsi']:.1f}, {trade['signal_strength']}"
                        if trade['ai_insight']:
                            reasoning += f", AI: {trade['ai_insight'][:30]}{'...' if len(trade['ai_insight']) > 30 else ''}"
                        
                        # Initialize ticker tracking if new
                        if symbol not in ticker_positions:
                            ticker_positions[symbol] = {
                                'net_shares': 0,
                                'total_invested': 0,
                                'avg_price': 0,
                                'total_buys': 0,
                                'total_sells': 0,
                                'buy_value': 0,
                                'sell_value': 0,
                                'last_reasoning': '',
                                'first_loop': loop_count,
                                'last_loop': loop_count
                            }
                            ticker_transactions[symbol] = []
                        
                        # Update position tracking
                        pos = ticker_positions[symbol]
                        pos['last_loop'] = loop_count
                        pos['last_reasoning'] = reasoning
                        
                        if action == 'BUY':
                            pos['net_shares'] += quantity
                            pos['total_buys'] += quantity
                            pos['buy_value'] += total_value
                            pos['total_invested'] += total_value
                        else:  # SELL
                            pos['net_shares'] -= quantity
                            pos['total_sells'] += quantity
                            pos['sell_value'] += total_value
                            pos['total_invested'] -= total_value
                        
                        # Calculate average price for current position
                        if pos['net_shares'] > 0 and pos['buy_value'] > 0:
                            pos['avg_price'] = pos['buy_value'] / pos['total_buys'] if pos['total_buys'] > 0 else price
                        
                        # Store transaction
                        ticker_transactions[symbol].append({
                            'loop': loop_count,
                            'action': action,
                            'quantity': quantity,
                            'price': price,
                            'value': total_value,
                            'reasoning': reasoning,
                            'timestamp': loop_start
                        })
                    
                    total_trades += loop_trades
                    total_opportunities += loop_opportunities
                    
                    loop_stats = {
                        'loop_number': loop_count,
                        'timestamp': loop_start,
                        'duration': loop_duration,
                        'trades': loop_trades,
                        'opportunities': loop_opportunities,
                        'symbols_analyzed': max_symbols
                    }
                    loop_performance.append(loop_stats)
                    
                    print(f"✅ Loop #{loop_count} complete: {loop_trades} trades, {loop_opportunities} opportunities ({loop_duration:.1f}s)")
                    
                    # Show AI status at the end of each loop
                    self._show_ai_status(loop_count)
                    
                    # End session
                    self.end_session()
                    
                    # Show summary every N loops
                    if loop_count % summary_interval == 0:
                        self._show_performance_summary(loop_count, loop_performance, total_trades, total_opportunities, start_time, ticker_positions, ticker_transactions)
                        
                        # Keep only recent performance data (last 100 loops) to manage memory
                        if len(loop_performance) > 100:
                            loop_performance = loop_performance[-100:]
                    
                except Exception as e:
                    print(f"❌ Loop #{loop_count} failed: {e}")
                    logging.error(f"Loop #{loop_count} failed: {e}")
                    
                    # End session even if failed
                    try:
                        self.end_session()
                    except:
                        pass
                
                # Wait before next loop
                if loop_delay > 0:
                    print(f"🕰️ Waiting {loop_delay} seconds until next loop...")
                    time.sleep(loop_delay)
                    
        except KeyboardInterrupt:
            print(f"\n\n🛑 CONTINUOUS MODE STOPPED BY USER")
            print(f"📈 Final Stats: {loop_count} loops, {total_trades} trades, {total_opportunities} opportunities")
            self._show_performance_summary(loop_count, loop_performance, total_trades, total_opportunities, start_time, ticker_positions, ticker_transactions, final=True)
            
    def _show_ai_status(self, loop_count: int):
        """Show AI provider status and health check"""
        if not self.ai or not self.ai.is_configured:
            print("\n🤖 AI STATUS: Not configured")
            return
            
        print("\n" + "="*50)
        print(f"🤖 AI PROVIDER STATUS - Loop #{loop_count}")
        print("="*50)
        
        # Check each provider status
        providers_status = {
            'openai': {'name': 'OpenAI GPT', 'emoji': '🟢'},
            'google': {'name': 'Google Gemini', 'emoji': '🟡'},
            'huggingface': {'name': 'Hugging Face', 'emoji': '🔴'},
            'openrouter': {'name': 'OpenRouter', 'emoji': '🟢'},
            'mistral': {'name': 'Mistral AI', 'emoji': '🟢'},
            'cohere': {'name': 'Cohere', 'emoji': '🟢'}
        }
        
        # Get failure information from AI agent
        failed_providers = set()
        failure_counts = {}
        if hasattr(self.ai, 'failed_providers'):
            failed_providers = self.ai.failed_providers
        if hasattr(self.ai, 'provider_failure_count'):
            failure_counts = self.ai.provider_failure_count
        
        # Get rate limit status
        rate_limit_info = ""
        if self.rate_limit_detected:
            rate_limit_info = " (RATE LIMITED)"
            
        # Database health indicator (surface persistent DB failures alongside AI health)
        db_health = "🟢 DB OK"
        if hasattr(self.db, "last_error") and self.db.last_error:
            # Extract which operation failed for clearer status
            error_detail = ""
            if "research_cooldown" in self.db.last_error:
                error_detail = " (cooldown queries failing)"
            elif "status 400" in self.db.last_error or "status 404" in self.db.last_error:
                error_detail = f" ({self.db.last_error.split(':')[0]})"
            db_health = f"🔴 DB Failing{error_detail}"
        elif not self.db.is_available():
            db_health = "🟡 DB Unavailable"

        # Check AI agent status
        ai_health = "🟢 Healthy" if self.ai.is_configured and not self.rate_limit_detected else "🟡 Limited"
        if self.rate_limit_detected:
            ai_health = "🔴 Rate Limited"
            
        print(f"Overall AI Health: {ai_health}{rate_limit_info} | {db_health}")
        
        # Show individual providers
        for provider_key, provider_info in providers_status.items():
            if provider_key == 'huggingface':
                status = "🔴 Disabled (API deprecated)"
            elif provider_key in failed_providers and provider_key in failure_counts:
                # Show rate limit or failure info
                fail_count = failure_counts[provider_key]
                status = f"🟡 Rate Limited ({fail_count} failures)"
            elif provider_key == 'google' and hasattr(self.ai, 'daily_request_count'):
                daily_count = getattr(self.ai, 'daily_request_count', 0)
                status = f"🟡 Active ({daily_count}/200 daily requests)"
            elif provider_key in ['openai', 'openrouter', 'mistral', 'cohere']:
                status = "🟢 Active" if not self.rate_limit_detected else "🟡 Standby"
            else:
                status = "🟢 Available"
                
            print(f"{provider_info['name']}: {status}")
            
        # Show current provider priority if available
        try:
            if hasattr(self.ai, 'provider_priority'):
                current_provider = getattr(self.ai, 'current_provider', 'Auto')
                print(f"Current Provider: {current_provider}")
        except:
            pass
            
        # Show request stats if available
        try:
            if hasattr(self.ai, 'total_requests'):
                total_requests = getattr(self.ai, 'total_requests', 0)
                successful_requests = getattr(self.ai, 'successful_requests', 0)
                success_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 0
                print(f"Session Stats: {successful_requests}/{total_requests} requests ({success_rate:.1f}% success)")
        except:
            pass
            
        print("="*50)

    def _show_performance_summary(self, loop_count: int, performance_data: list, total_trades: int, total_opportunities: int, start_time: datetime, ticker_positions: dict = None, ticker_transactions: dict = None, final: bool = False):
        """Show detailed performance summary with ticker analysis"""
        current_time = datetime.now(timezone.utc)
        total_runtime = (current_time - start_time).total_seconds()
        
        summary_title = "FINAL PERFORMANCE SUMMARY" if final else f"PERFORMANCE SUMMARY - LOOP {loop_count}"
        print("\n" + "="*70)
        print(f"📈 {summary_title}")
        print("="*70)
        
        # Overall stats
        print(f"🕰️ Total Runtime: {total_runtime/3600:.1f} hours ({total_runtime/60:.1f} minutes)")
        print(f"🔄 Loops Completed: {loop_count}")
        print(f"💼 Total Trades: {total_trades}")
        print(f"📊 Total Opportunities: {total_opportunities}")
        print(f"🎯 Trade Rate: {(total_trades/loop_count):.2f} trades per loop")
        print(f"🔍 Opportunity Rate: {(total_opportunities/loop_count):.2f} opportunities per loop")
        
        # Recent performance (last 10 loops)
        if len(performance_data) >= 10:
            recent_data = performance_data[-10:]
            recent_trades = sum(loop['trades'] for loop in recent_data)
            recent_opportunities = sum(loop['opportunities'] for loop in recent_data)
            avg_duration = sum(loop['duration'] for loop in recent_data) / len(recent_data)
            
            print(f"\n🔥 RECENT PERFORMANCE (Last 10 loops):")
            print(f"   💼 Trades: {recent_trades} ({recent_trades/10:.2f} per loop)")
            print(f"   📊 Opportunities: {recent_opportunities} ({recent_opportunities/10:.2f} per loop)")
            print(f"   ⏱️ Avg Duration: {avg_duration:.1f} seconds per loop")
        
        # Portfolio status
        try:
            portfolio = self.analyze_portfolio()
            if portfolio:
                print(f"\n💰 CURRENT PORTFOLIO STATUS:")
                print(f"   Total Value: ${portfolio['total_value']:,.2f}")
                print(f"   Unrealized P&L: ${portfolio['total_unrealized_pnl']:,.2f}")
                print(f"   Win Rate: {portfolio['win_rate']:.1f}%")
                print(f"   Cash: {portfolio['cash_percentage']:.1f}%")
                print(f"   Concentration Risk: {portfolio['concentration_risk']:.1f}%")
        except Exception as e:
            print(f"   Portfolio analysis failed: {e}")
        
        # Ticker positions analysis
        if ticker_positions and len(ticker_positions) > 0:
            print(f"\n📈 TICKER POSITIONS SUMMARY ({len(ticker_positions)} tickers):")
            print("-" * 70)
            
            # Sort by absolute position value
            sorted_tickers = sorted(ticker_positions.items(), 
                                  key=lambda x: abs(x[1]['total_invested']), reverse=True)
            
            for symbol, pos in sorted_tickers:
                net_shares = pos['net_shares']
                total_invested = pos['total_invested']
                avg_price = pos['avg_price']
                buys = pos['total_buys']
                sells = pos['total_sells']
                reasoning = pos['last_reasoning']
                loops_span = f"Loop {pos['first_loop']}-{pos['last_loop']}" if pos['first_loop'] != pos['last_loop'] else f"Loop {pos['first_loop']}"
                
                position_type = "🟢 LONG" if net_shares > 0 else "🔴 SHORT" if net_shares < 0 else "⚪ CLOSED"
                
                print(f"\n🎢 {symbol:6s} {position_type}")
                print(f"   📊 Position: {net_shares:+.0f} shares @ ${avg_price:.2f} avg = ${total_invested:+,.2f}")
                print(f"   📈 Activity: {buys} buys, {sells} sells over {loops_span}")
                print(f"   🧠 Last Reason: {reasoning}")
                
                # Show recent transactions for this ticker
                if ticker_transactions and symbol in ticker_transactions:
                    recent_trades = ticker_transactions[symbol][-3:]  # Last 3 trades
                    if len(ticker_transactions[symbol]) > 3:
                        print(f"   📉 Recent Trades (last 3 of {len(ticker_transactions[symbol])}):")  
                    else:
                        print(f"   📉 All Trades:")
                    
                    for trade in recent_trades:
                        action_emoji = "🟢" if trade['action'] == 'BUY' else "🔴"
                        print(f"      {action_emoji} Loop {trade['loop']:2d}: {trade['action']} {trade['quantity']:2.0f} @ ${trade['price']:.2f} (${trade['value']:,.2f})")
        
        # Trading activity timeline (if not final summary)
        if not final and len(performance_data) >= 5:
            print(f"\n📉 RECENT LOOP ACTIVITY:")
            for loop_data in performance_data[-5:]:
                timestamp = loop_data['timestamp'].strftime('%H:%M:%S')
                print(f"   Loop {loop_data['loop_number']:3d} ({timestamp}): {loop_data['trades']} trades, {loop_data['opportunities']} opportunities")
        
        # Performance metrics summary
        if perf_monitor:
            summary = perf_monitor.get_summary()
            
            # Show top 5 slowest functions
            if summary['functions']:
                print(f"\n⚡ PERFORMANCE METRICS (Top Slowest Functions):")
                sorted_funcs = sorted(
                    summary['functions'].items(),
                    key=lambda x: x[1]['avg_time'],
                    reverse=True
                )[:5]
                
                for func_name, stats in sorted_funcs:
                    avg_ms = stats['avg_time'] * 1000
                    print(f"   {func_name}: {avg_ms:.1f}ms avg ({stats['calls']} calls)")
            
            # Show API call stats
            if summary['apis']:
                print(f"\n🌐 API CALL PERFORMANCE:")
                sorted_apis = sorted(
                    summary['apis'].items(),
                    key=lambda x: x[1]['calls'],
                    reverse=True
                )[:5]
                
                for endpoint, stats in sorted_apis:
                    avg_ms = stats['avg_time'] * 1000
                    error_rate = f" ({stats['error_rate']:.1f}% errors)" if stats['errors'] > 0 else ""
                    print(f"   {endpoint}: {avg_ms:.1f}ms avg ({stats['calls']} calls){error_rate}")
        
        print("="*70)
        if not final:
            print(f"🚀 Continuing trading loop...")

def main():
    """Main bot execution with option for continuous mode"""
    import sys
    import argparse
    
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser(description='Enhanced Trading Bot with AI')
        parser.add_argument('--continuous', '-c', action='store_true', 
                          help='Run in continuous mode (default: single session)')
        parser.add_argument('--delay', '-d', type=int, default=300,
                          help='Seconds between loops in continuous mode (default: 300)')
        parser.add_argument('--max-symbols', type=int, default=30,
                          help='Maximum symbols to analyze per loop (default: 30)')
        parser.add_argument('--max-trades', type=int, default=2,
                          help='Maximum trades to execute per loop (default: 2)')
        parser.add_argument('--summary-interval', type=int, default=50,
                          help='Show summary every N loops (default: 50)')
        
        # AI configuration flags
        parser.add_argument('--no-ai-ticker-analysis', action='store_true',
                          help='Disable AI analysis for individual tickers (use only technical indicators)')
        parser.add_argument('--no-ai-ticker-selection', action='store_true',
                          help='Disable AI-based ticker selection (use standard ticker list)')
        parser.add_argument('--no-ai-market-summary', action='store_true',
                          help='Disable AI market sentiment summaries')
        parser.add_argument('--no-ai', action='store_true',
                          help='Disable ALL AI features (pure technical analysis mode)')
        
        # AI preset modes
        parser.add_argument('--ai-selection-only', action='store_true',
                          help='Use AI for ticker selection only (fastest AI mode - recommended)')
        
        args = parser.parse_args()
        
        bot = SmartTradingBot()
        
        # Configure AI settings based on command-line arguments
        if args.no_ai:
            # Disable all AI features
            bot.configure_ai_usage(
                ticker_analysis=False,
                ticker_selection=False,
                market_summary=False
            )
        elif args.ai_selection_only:
            # AI selects tickers based on portfolio, but uses technical analysis only
            # This is the fastest AI mode - AI picks which stocks to look at, technical analysis decides buy/sell
            print("🎯 AI Selection Only Mode: AI picks tickers, technical analysis decides trades")
            bot.configure_ai_usage(
                ticker_analysis=False,      # No AI per-ticker analysis (technical only)
                ticker_selection=True,       # AI selects which tickers to analyze
                market_summary=False         # No market summary (faster)
            )
        else:
            # Configure individual AI features
            bot.configure_ai_usage(
                ticker_analysis=not args.no_ai_ticker_analysis,
                ticker_selection=not args.no_ai_ticker_selection,
                market_summary=not args.no_ai_market_summary
            )
        
        # Show setup instructions if database not available
        bot.show_database_setup()
        
        # Show database status
        bot.show_database_status()
        
        if args.continuous:
            print("🔄 Starting in CONTINUOUS mode...")
            print(f"⏱️  Loop delay: {args.delay} seconds ({args.delay/60:.1f} minutes)")
            bot.run_continuous_loop(
                max_symbols=args.max_symbols, 
                max_trades=args.max_trades, 
                loop_delay=args.delay, 
                summary_interval=args.summary_interval
            )
        else:
            print("🏁 Running SINGLE session (use --continuous for loop mode)...")
            # Start session
            bot.start_session()
            
            try:
                # Run analysis with AI if configured
                use_ai = bot.ai.is_configured
                if use_ai:
                    logging.info("🧠 AI-enhanced analysis enabled")
                bot.run_analysis(max_symbols=args.max_symbols, max_trades=args.max_trades, use_ai=use_ai)
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