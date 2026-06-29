"""
Enhanced trading bot with simple REST API database integration.
Works around package conflicts by using direct HTTP requests.
"""
import os
import time
import logging
import random
import asyncio
import requests
from datetime import datetime, timedelta, timezone, date
from typing import List, Dict, Optional
import pandas as pd
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
import yfinance as yf

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

# Use absolute path for log file
log_path = '/home/ubuntu/.openclaw/workspace/trading-bot/trading_bot.log'

# Create and configure logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Remove any existing handlers
for h in logger.handlers[:]:
    logger.removeHandler(h)

# Custom formatter that converts UTC to Central Time
class CSTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        import datetime
        # Convert UTC to Central Time
        utc_dt = datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc)
        # CST is UTC-6 (or UTC-5 during DST, but we'll use standard)
        cst_dt = utc_dt.astimezone(datetime.timezone(datetime.timedelta(hours=-6)))
        if datefmt:
            return cst_dt.strftime(datefmt)
        return cst_dt.strftime('%Y-%m-%d %H:%M:%S')

# File handler
file_handler = logging.FileHandler(log_path, mode='a')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(CSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))

# Stream handler
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(CSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# Also set root logger
logging.getLogger().setLevel(logging.INFO)

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

        # AI configuration flags
        self.use_ai_for_ticker_analysis = True  # Set to False to disable AI analysis for individual tickers
        self.use_ai_for_ticker_selection = False  # Disabled - use rolling list of all symbols instead
        self.use_ai_for_market_summary = True  # Set to False to disable AI market summaries

        # Stop-Loss Configuration
        self.stop_loss_pct = 8.0  # Hard stop: sell at X% loss (default: 8%)
        self.trailing_stop_pct = 5.0  # Trailing stop: lock in profits when X% above entry (default: 5%)
        self.take_profit_pct = 15.0  # Take profit: auto-sell at X% gain (default: 15%)
        self.enable_stop_loss = True  # Enable/disable stop-loss system

        # Dynamic ATR-based stop-loss
        self.use_atr_stop_loss = True  # Use 2x ATR instead of fixed percentage
        self.atr_stop_multiplier = 2.0  # Stop at current_price - (multiplier * ATR)

        # ATR-based Position Sizing Configuration
        self.enable_atr_sizing = True  # Use ATR for position sizing
        self.risk_per_trade = 0.02  # Risk 2% of portfolio per trade (default)
        self.max_position_pct = 0.05  # Max 5% of portfolio in single position

        # Max Drawdown Protection
        self.enable_drawdown_protection = True  # Enable max drawdown protection
        self.max_drawdown_pct = 10.0  # Pause trading if portfolio drops X% from peak (default: 10%)
        self.peak_portfolio_value = None  # Track peak portfolio value
        self.drawdown_paused = False  # Flag if trading paused due to drawdown

        # Daily/Weekly Loss Limits
        self.enable_daily_loss_limit = True  # Enable daily loss limit
        self.daily_loss_limit_pct = 5.0  # Stop trading if daily loss exceeds X% (default: 5%)
        self.daily_starting_value = None  # Track portfolio value at start of day
        self.daily_loss_pct = 0.0  # Current daily loss percentage
        self.daily_loss_paused = False  # Flag if trading paused due to daily loss
        self.last_reset_date = None  # Track when we last reset daily tracking

        # Telegram Notifications (enabled)
        self.enable_email_notifications = False
        self.enable_telegram_notifications = os.getenv('TELEGRAM_ENABLED', 'true').lower() == 'true'
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', '')

        # Multi-Timeframe Analysis (enabled by default)
        self.enable_multi_timeframe = True  # Require daily AND hourly to agree on signals
        self.hourly_weight = 0.30  # Hourly weight (daily is 0.70)

        # Volume Confirmation (enabled by default)
        self.enable_volume_confirmation = True  # Require volume > 20-day average for signals

        # Filter toggles — loaded from DB so they survive restarts
        # Block BUY signals when these conflicts are detected (if enabled)
        self.enable_mtf_conflict_filter = True   # Block if daily/hourly timeframe signals conflict
        self.enable_vol_downgrade_filter = True  # Block if volume is below average (vol downgrade)
        self.enable_ai_conflict_filter = True    # Block if AI recommendation conflicts with technical signal

        # Load filter toggles from DB (persisted via dashboard settings API)
        self._load_filter_toggles_from_db()

        # Earnings Filter (enabled by default - skip trades N days before earnings)
        self.earnings_days_skip = 3  # Skip trades within this many days before earnings
        self.earnings_cache = {}  # Cache earnings dates to avoid repeated API calls

        # Market Data Request Deduplication - cache API calls within a cycle
        self._market_data_cache = {}  # symbol -> (timestamp, DataFrame)
        self._market_data_cache_ttl = 30  # Cache valid for 30 seconds
        self._cycle_start_time = None  # Track cycle start for cache invalidation
        self._analysis_queue = []  # Queue of symbols to analyze in sequence
        self._analyzed_today = {}  # Track analyzed symbols with timestamps {symbol: datetime}
        self._current_analysis_index = 0  # Current position in the full symbol list

        # SPY Relative Strength Filter (enabled by default)
        self.enable_sp_filter = True  # Only buy stocks outperforming SPY

        # Liquidity Filter (enabled by default) - filter out illiquid stocks
        self.enable_liquidity_filter = True  # Skip stocks with low volume or wide spreads
        self.min_daily_volume = 1000000  # Minimum 1M shares avg daily volume

        # Sector Rotation Filter (enabled by default)
        self.enable_sector_filter = True  # Prefer stocks in strong sectors
        self.sector_rotation_scores = {}  # Cache sector scores

        # Sector Concentration Limit (default 25%)
        self.max_sector_concentration = 0.25  # Max 25% in any single sector

        # Correlation-aware position sizing (default: skip if > 0.7)
        self.max_correlation = 0.7  # Skip if correlation with any position > 0.7

        # Beta-adjusted exposure (default: stop adding if portfolio beta > 1.5)
        self.max_portfolio_beta = 1.5  # Max portfolio beta allowed
        self._portfolio_beta_cache = None  # Cache for portfolio beta

        # Market Regime Classifier
        self.enable_regime_filter = True  # Use ADX-based regime detection
        self.regime_symbol = "SPY"  # Symbol for regime detection
        self.adx_period = 14  # ADX calculation period

        # Position Rotation (enabled by default)
        self.enable_rotation = True  # Sell weak positions to buy stronger ones
        self.rotation_threshold = 20  # New opportunity must be X points better than worst position
        self.trend_threshold = 25.0  # ADX > this = trending
        self.range_threshold = 20.0  # ADX < this = ranging
        self._regime_classifier = None  # Will be initialized lazily
        self._forced_regime = None  # For testing

        # Simple sector mapping for common stocks (expand as needed)
        self.stock_sector_map = {
            # Technology
            'AAPL': 'XLK', 'MSFT': 'XLK', 'GOOGL': 'XLK', 'GOOG': 'XLK', 'META': 'XLK',
            'NVDA': 'XLK', 'AMD': 'XLK', 'INTC': 'XLK', 'CSCO': 'XLK', 'ORCL': 'XLK',
            'ADBE': 'XLK', 'CRM': 'XLK', 'PYPL': 'XLK', 'NFLX': 'XLK', 'AVGO': 'XLK',
            'NOW': 'XLK', 'SNOW': 'XLK', 'PANW': 'XLK', 'CRWD': 'XLK', 'ZS': 'XLK',
            'OKTA': 'XLK', 'DDOG': 'XLK', 'NET': 'XLK', 'MDB': 'XLK', 'TEAM': 'XLK',
            # Financials
            'JPM': 'XLF', 'BAC': 'XLF', 'WFC': 'XLF', 'C': 'XLF', 'GS': 'XLF',
            'MS': 'XLF', 'AXP': 'XLF', 'V': 'XLF', 'MA': 'XLF', 'BLK': 'XLF',
            'SCHW': 'XLF', 'USB': 'XLF', 'PNC': 'XLF', 'TFC': 'XLF', 'COF': 'XLF',
            # Energy
            'XOM': 'XLE', 'CVX': 'XLE', 'COP': 'XLE', 'SLB': 'XLE', 'EOG': 'XLE',
            'MPC': 'XLE', 'VLO': 'XLE', 'PSX': 'XLE', 'OXY': 'XLE', 'HAL': 'XLE',
            # Healthcare
            'JNJ': 'XLV', 'UNH': 'XLV', 'PFE': 'XLV', 'MRK': 'XLV', 'ABBV': 'XLV',
            'TMO': 'XLV', 'DHR': 'XLV', 'BMY': 'XLV', 'AMGN': 'XLV', 'GILD': 'XLV',
            'CVS': 'XLV', 'CI': 'XLV', 'HUM': 'XLV', 'ZTS': 'XLV', 'REGN': 'XLV',
            # Consumer Staples
            'PG': 'XLP', 'KO': 'XLP', 'PEP': 'XLP', 'WMT': 'XLP', 'COST': 'XLP',
            'MDLZ': 'XLP', 'CL': 'XLP', 'EL': 'XLP', 'KMB': 'XLP', 'GIS': 'XLP',
            # Consumer Discretionary
            'AMZN': 'XLY', 'TSLA': 'XLY', 'HD': 'XLY', 'MCD': 'XLY', 'NKE': 'XLY',
            'SBUX': 'XLY', 'TGT': 'XLY', 'LOW': 'XLY', 'TJX': 'XLY', 'BKNG': 'XLY',
            # Materials
            'LIN': 'XLB', 'APD': 'XLB', 'ECL': 'XLB', 'NEM': 'XLB', 'FCX': 'XLB',
            'SHW': 'XLB', 'DOW': 'XLB', 'PPG': 'XLB', 'NUE': 'XLB', 'VMC': 'XLB',
            # Industrials
            'CAT': 'XLI', 'BA': 'XLI', 'HON': 'XLI', 'UPS': 'XLI', 'UNP': 'XLI',
            'GE': 'XLI', 'RTX': 'XLI', 'LMT': 'XLI', 'DE': 'XLI', 'MMM': 'XLI',
            # Real Estate
            'PLD': 'XLRE', 'AMT': 'XLRE', 'EQIX': 'XLRE', 'CCI': 'XLRE', 'SPG': 'XLRE',
            'O': 'XLRE', 'PSA': 'XLRE', 'WELL': 'XLRE', 'AVB': 'XLRE', 'EQR': 'XLRE',
            # Utilities
            'NEE': 'XLU', 'DUK': 'XLU', 'SO': 'XLU', 'D': 'XLU', 'AEP': 'XLU',
            'SRE': 'XLU', 'EXC': 'XLU', 'XEL': 'XLU', 'ED': 'XLU', 'PEG': 'XLU',
            # Communication
            'META': 'XLC', 'GOOG': 'XLC', 'GOOGL': 'XLC', 'NFLX': 'XLC', 'DIS': 'XLC',
            'CMCSA': 'XLC', 'T': 'XLC', 'VZ': 'XLC', 'TMUS': 'XLC', 'CHTR': 'XLC',
        }

        # Yahoo Finance sector mapping (for dynamic lookup)
        # Map Yahoo sector names to our sector ETFs
        self.yahoo_sector_to_etf = {
            'Technology': 'XLK',
            'Financial Services': 'XLF',
            'Energy': 'XLE',
            'Healthcare': 'XLV',
            'Consumer Defensive': 'XLP',
            'Consumer Cyclical': 'XLY',
            'Basic Materials': 'XLB',
            'Industrials': 'XLI',
            'Real Estate': 'XLRE',
            'Utilities': 'XLU',
            'Communication Services': 'XLC',
        }

        # Cache for dynamically fetched sectors
        self._sector_cache = {}
        self._sector_allocation_cache = None  # Cache for sector allocation

        # Rate limit tracking
        self.rate_limit_detected = False
        self.rate_limit_count = 0
        self.last_rate_limit_time = None

        # Cooldown tracking for intelligent position management
        self.recent_trades = {}  # {symbol: timestamp} for tracking recent trades
        self.trade_times = {}  # symbol -> last trade timestamp
        self.position_sell_analysis_times = {}  # symbol -> last sell analysis timestamp
        self.research_times = {}  # symbol -> last research timestamp for AI ticker variety

        # Portfolio optimization engine
        try:
            from analysis.portfolio_optimizer import PortfolioOptimizer
            self.portfolio_optimizer = PortfolioOptimizer(
                risk_free_rate=0.05,
                lookback_days=60,
                max_position_weight=self.max_position_pct * 3,  # ~15% max
                rebalance_threshold=0.05,
            )
            self._last_portfolio_opt_time = None
            self.portfolio_opt_interval_loops = 6  # Run every 6 loops (~30 min at 5-min default)
        except Exception as e:
            logging.warning(f"Portfolio optimizer not available: {e}")
            self.portfolio_optimizer = None

        # Adaptive learning state
        self._symbol_size_multipliers: Dict[str, float] = {}
        self._last_learning_run = None
        self.learning_interval_loops = 12  # Run adaptive learning every 12 loops (~1 hr)
        try:
            from analysis.adaptive_learning import AdaptiveLearningEngine
            self.adaptive_learning = AdaptiveLearningEngine(db=self.db)
        except Exception as e:
            logging.warning(f"Adaptive learning engine not available: {e}")
            self.adaptive_learning = None

        # VIX cache
        self._vix_cache = None
        self._vix_cache_time = None

        # Pending entry tranches tracking  {symbol: tranche_plan_dict}
        self._pending_entry_tranches: Dict[str, dict] = {}

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

    def _load_filter_toggles_from_db(self):
        """
        Load the three filter-toggle settings from the settings DB.
        These are persisted by the dashboard's /api/settings endpoint so they
        survive bot restarts. Silently ignores errors (defaults remain True).
        """
        try:
            import sqlite3
            db_path = Path(__file__).parent.parent.parent / "trading_bot.db"
            conn = sqlite3.connect(str(db_path), timeout=10)
            cur = conn.execute(
                "SELECT key, value FROM settings WHERE key IN "
                "('enable_mtf_conflict_filter', 'enable_vol_downgrade_filter', 'enable_ai_conflict_filter')"
            )
            for row in cur.fetchall():
                key, raw = row[0], row[1]
                val = raw.lower() in ("true", "1", "yes")
                if key == "enable_mtf_conflict_filter":
                    self.enable_mtf_conflict_filter = val
                elif key == "enable_vol_downgrade_filter":
                    self.enable_vol_downgrade_filter = val
                elif key == "enable_ai_conflict_filter":
                    self.enable_ai_conflict_filter = val
            conn.close()
        except Exception as e:
            logging.debug(f"Could not load filter toggles from DB: {e}")

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
            recently_researched = self.get_recently_researched_tickers(cooldown_minutes=240)
            if recently_researched:
                print(f"\n🔍 Recently researched tickers to EXCLUDE ({len(recently_researched)}): {', '.join(recently_researched[:20])}")
                logging.info(f"🔍 Recently researched tickers to EXCLUDE ({len(recently_researched)}): {', '.join(recently_researched[:20])}")
            else:
                print("\n🔍 No recently researched tickers in cooldown period")
                logging.info("🔍 No recently researched tickers in cooldown period")

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

            # Format exclusion list for prompt (comma-separated string)
            exclusion_list = ', '.join(recently_researched) if recently_researched else 'NONE'

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

            ⚠️  ⚠️  ⚠️  MANDATORY EXCLUSION LIST - YOU MUST NOT RECOMMEND ANY OF THESE  ⚠️  ⚠️  ⚠️
            EXCLUSION LIST (tickers analyzed in the last 60 minutes):
            {exclusion_list}

            ⚠️  FAILURE TO FOLLOW THIS INSTRUCTION WILL RESULT IN INVALID RESPONSE.
            ⚠️  Every single ticker in your response MUST be different from the exclusion list above.

            Please suggest NEW symbols for educational analysis considering:
            1. Portfolio diversification patterns
            2. Sector balance opportunities
            3. Market growth sectors (for educational study)
            4. Stability analysis options
            5. Alternative holdings for comparison study

            Return educational data in JSON format:
            {{
                "recommended_tickers": ["30 DIFFERENT ticker symbols - NONE OF WHICH APPEAR IN THE EXCLUSION LIST ABOVE"],
                "reasoning": "Educational analysis approach explanation",
                "focus_areas": ["diversification_study", "sector_analysis", "growth_patterns", ...]
            }}

            Focus on liquid, well-known stocks (Apple, Microsoft, JPM, etc). Avoid penny stocks, ETFs, and recently analyzed symbols.
            Prioritize fresh analysis opportunities not in the recently researched list.
            """

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                response = loop.run_until_complete(self.ai.analyze_with_context(prompt, "portfolio_ticker_selection"))

                if isinstance(response, dict) and 'recommended_tickers' in response:
                    tickers = response['recommended_tickers'][:30]  # Limit to 30
                    reasoning = response.get('reasoning', 'No reasoning provided')
                    focus_areas = response.get('focus_areas', [])

                    # Print ticker selection summary for visibility
                    print("\n" + "="*60)
                    print("🧠 AI TICKER SELECTION SUMMARY")
                    print("="*60)
                    print(f"📋 Strategy: {reasoning}")
                    print(f"🎯 Focus Areas: {', '.join(focus_areas)}")
                    print(f"📊 Recommended Tickers ({len(tickers)}): {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")

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
                    cooldown_filtered_tickers = self.filter_tickers_by_cooldown(tickers, cooldown_minutes=240)

                    # Log which tickers were filtered out to diagnose AI ignoring exclusions
                    filtered_out = set(tickers) - set(cooldown_filtered_tickers)
                    if filtered_out:
                        print(f"\n⚠️  AI IGNORED EXCLUSION LIST! Recommended {len(filtered_out)} tickers in cooldown: {list(filtered_out)[:10]}")
                        logging.warning(f"⚠️  AI recommended {len(filtered_out)} tickers in cooldown (ignored instructions): {list(filtered_out)[:10]}")

                    # If AI ignored exclusions too badly, use fallback instead
                    if len(cooldown_filtered_tickers) < 5:
                        logging.warning(f"⚠️  AI ignored exclusion list! Only {len(cooldown_filtered_tickers)} valid tickers. Using smart fallback.")
                        print(f"\n⚠️  AI failed to respect exclusion list. Using smart fallback ticker selection...")
                        return self._get_smart_fallback_tickers(portfolio_analysis)

                    print(f"📊 AI recommended {len(tickers)} tickers, {len(cooldown_filtered_tickers)} available after filtering")
                    logging.info(f"📊 AI recommended {len(tickers)} tickers, {len(cooldown_filtered_tickers)} available after filtering")

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

            finally:
                loop.close()

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
        recently_researched = self.get_recently_researched_tickers(cooldown_minutes=240)

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
        print(f"📊 Selected Tickers ({len(selected)}): {', '.join(selected[:10])}{'...' if len(selected) > 10 else ''}")
        print("="*60)

        # Filter out tickers in research cooldown before returning
        cooldown_filtered_tickers = self.filter_tickers_by_cooldown(selected, cooldown_minutes=240)

        # If too few tickers after cooldown filtering, get fresh ticker list
        if len(cooldown_filtered_tickers) < 10:
            logging.warning(f"⚠️  Fallback: Only {len(cooldown_filtered_tickers)} tickers after cooldown")
            logging.info("🔄 Generating fresh ticker list excluding cooldown, orders, and portfolio positions...")
            cooldown_filtered_tickers = self._get_fresh_ticker_list(target_count=30)

        logging.info(f"📊 Smart fallback selected {len(selected)} tickers, {len(cooldown_filtered_tickers)} available after filtering")
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

    def rank_by_relative_strength(self, symbols: List[str], lookback_days: int = 20, top_n: int = 150) -> List[str]:
        """
        Score and rank symbols by relative strength vs SPY.

        Composite score (0-100):
          - RS excess return vs SPY 20-day (0-40 pts)
          - Proximity to 52-week high      (0-30 pts)
          - Current volume vs 20-day avg   (0-30 pts)

        Falls back to original order if SPY data unavailable.
        """
        try:
            spy_df = self.get_market_data('SPY')
            if spy_df is None or len(spy_df) < lookback_days:
                return symbols[:top_n]

            spy_start = float(spy_df['close'].iloc[-lookback_days])
            spy_end = float(spy_df['close'].iloc[-1])
            spy_return = (spy_end - spy_start) / spy_start if spy_start else 0.0

            scored = []
            for sym in symbols:
                try:
                    df = self.get_market_data(sym)
                    if df is None or len(df) < max(lookback_days, 30):
                        continue

                    # 1. Relative strength vs SPY
                    stock_start = float(df['close'].iloc[-lookback_days])
                    stock_end = float(df['close'].iloc[-1])
                    stock_return = (stock_end - stock_start) / stock_start if stock_start else 0.0
                    rs_excess = stock_return - spy_return
                    rs_score = max(0.0, min(40.0, (rs_excess + 0.10) * 200.0))

                    # 2. Proximity to 52-week high
                    lookback_high = min(252, len(df))
                    high_52w = float(df['high'].tail(lookback_high).max())
                    prox = stock_end / high_52w if high_52w > 0 else 0.0
                    momentum_score = prox * 30.0

                    # 3. Volume surge score
                    avg_vol = float(df['volume'].tail(20).mean())
                    curr_vol = float(df['volume'].iloc[-1])
                    vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1.0
                    vol_score = min(30.0, vol_ratio * 15.0)

                    total = rs_score + momentum_score + vol_score
                    scored.append((sym, total))
                except Exception:
                    continue

            if not scored:
                return symbols[:top_n]

            scored.sort(key=lambda x: x[1], reverse=True)
            ranked = [s for s, _ in scored[:top_n]]
            logging.info(f"RS Ranking: top 5 = {ranked[:5]} ({len(scored)} scored)")
            return ranked
        except Exception as e:
            logging.debug(f"RS ranking failed: {e}")
            return symbols[:top_n]

    def _get_rolling_ticker_list(self, target_count: int = 30) -> List[str]:
        """
        Get a sequential list of tickers - each analyzed once before any repeats.
        This ensures every stock gets analyzed before going back to the beginning.
        """
        # Get all US symbols
        all_symbols = self.get_all_us_symbols()

        # Initialize queue if empty or we've gone through all symbols
        if not self._analysis_queue or self._current_analysis_index >= len(self._analysis_queue):
            # Get current portfolio and pending to exclude
            portfolio_symbols = set()
            try:
                positions = self.trading_client.get_all_positions()
                portfolio_symbols = {p.symbol for p in positions if float(p.qty) > 0}
            except Exception:
                pass

            pending_symbols = set()
            try:
                orders = list(self.trading_client.get_orders())
                pending_symbols = {o.symbol for o in orders if o.status in [
                    OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW,
                    OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL
                ]}
            except Exception:
                pass

            excluded = portfolio_symbols | pending_symbols

            # Create queue excluding portfolio and pending
            candidate_queue = [s for s in all_symbols if s not in excluded]

            # RS-rank the first 300 candidates so the highest-RS symbols are
            # analyzed first each cycle (trades go to the best opportunities)
            if len(candidate_queue) > 50:
                try:
                    top_candidates = candidate_queue[:300]
                    remainder = candidate_queue[300:]
                    ranked_top = self.rank_by_relative_strength(top_candidates, lookback_days=20, top_n=150)
                    already_ranked = set(ranked_top)
                    remaining_candidates = [s for s in top_candidates if s not in already_ranked]
                    candidate_queue = ranked_top + remaining_candidates + remainder
                    logging.info(f"RS-ranked queue: top 150 prioritized")
                except Exception as e:
                    logging.debug(f"RS ranking in queue failed: {e}")

            self._analysis_queue = candidate_queue
            self._current_analysis_index = 0
            logging.info(f"🔄 Starting new analysis cycle: {len(self._analysis_queue)} symbols in queue")

        # Get next batch from queue
        remaining = len(self._analysis_queue) - self._current_analysis_index
        if remaining <= 0:
            # Restart cycle
            self._current_analysis_index = 0
            remaining = len(self._analysis_queue)

        batch_size = min(target_count, remaining)
        batch = self._analysis_queue[self._current_analysis_index:self._current_analysis_index + batch_size]
        self._current_analysis_index += batch_size

        logging.info(f"📊 Analysis queue: {self._current_analysis_index}/{len(self._analysis_queue)} complete, returning {len(batch)} symbols")

        return batch

    def save_analysis_to_db(self, symbol: str, analysis: Dict):
        """Save analysis result to SQLite database"""
        self._analyzed_today[symbol] = {
            'analyzed_at': datetime.now(timezone.utc),
            'signal': analysis.get('signal', 'HOLD'),
            'total_score': analysis.get('total_score', 50),
            'rsi': analysis.get('rsi'),
            'price': analysis.get('price'),
        }
        self.db.save_analysis_result(symbol, analysis)
        logging.info(f"💾 Saved {symbol}: {analysis.get('signal')} score={analysis.get('total_score', 50)}")

    def get_analysis_status(self) -> Dict:
        """Get current analysis status for dashboard"""
        total_in_queue = len(self._analysis_queue)
        progress = self._current_analysis_index
        analyzed_count = len(self._analyzed_today)

        # Get recent analyses
        recent = sorted(self._analyzed_today.items(), key=lambda x: x[1]['analyzed_at'], reverse=True)[:50]

        return {
            'queue_total': total_in_queue,
            'queue_progress': progress,
            'analyzed_today': analyzed_count,
            'recent_analyses': [
                {'symbol': s, **data} for s, data in recent
            ]
        }

    def get_vix_regime(self) -> dict:
        """
        Fetch the current VIX level and classify the volatility regime.
        Results are cached for 15 minutes to avoid hammering yfinance.

        Returns:
            Dict with keys:
              vix_level        (float)
              regime           ('LOW' | 'NORMAL' | 'HIGH' | 'EXTREME')
              should_reduce_sizing (bool)
              size_multiplier  (float, 0.5 - 1.0)
        """
        _default = {
            'vix_level': 20.0,
            'regime': 'NORMAL',
            'should_reduce_sizing': False,
            'size_multiplier': 1.0
        }
        try:
            now = datetime.now(timezone.utc)
            # Return cached value if fresh (15-minute TTL)
            if (hasattr(self, '_vix_cache')
                    and self._vix_cache
                    and (now - self._vix_cache_time).total_seconds() < 900):
                return self._vix_cache

            import yfinance as yf
            hist = yf.Ticker("^VIX").history(period="5d")
            if hist.empty:
                return _default

            vix = float(hist['Close'].iloc[-1])

            if vix < 15:
                regime, reduce, mult = 'LOW', False, 1.0
            elif vix < 25:
                regime, reduce, mult = 'NORMAL', False, 1.0
            elif vix < 35:
                regime, reduce, mult = 'HIGH', True, 0.75
            else:
                regime, reduce, mult = 'EXTREME', True, 0.50

            result = {
                'vix_level': round(vix, 1),
                'regime': regime,
                'should_reduce_sizing': reduce,
                'size_multiplier': mult
            }
            self._vix_cache = result
            self._vix_cache_time = now
            return result
        except Exception as e:
            logging.debug(f"VIX check failed: {e}")
            return _default

    def get_market_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Get market data for analysis with request deduplication"""
        # Check if we have a valid cached result
        now = datetime.now(timezone.utc)

        # Start new cycle if needed
        if self._cycle_start_time is None:
            self._cycle_start_time = now

        # Invalidate cache if more than TTL seconds have passed since cycle start
        cache_age = (now - self._cycle_start_time).total_seconds()
        if cache_age > self._market_data_cache_ttl:
            self._market_data_cache.clear()
            self._cycle_start_time = now

        # Return cached data if available
        if symbol in self._market_data_cache:
            cached_time, cached_df = self._market_data_cache[symbol]
            if cached_df is not None:
                logging.debug(f"�_cache Hit: {symbol}")
                return cached_df.copy()  # Return a copy to prevent mutation

        # Fetch fresh data
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
                self._market_data_cache[symbol] = (now, None)
                return None

            bars = barset.data[symbol]
            if not bars:
                self._market_data_cache[symbol] = (now, None)
                return None

            df = pd.DataFrame([{
                'timestamp': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume
            } for bar in bars])

            # Cache the result
            self._market_data_cache[symbol] = (now, df)

            return df

        except Exception as e:
            logging.debug(f"Data fetch failed for {symbol}: {e}")
            self._market_data_cache[symbol] = (now, None)
            return None

    def check_liquidity(self, symbol: str, min_volume: int = 1000000, max_spread_pct: float = 0.3) -> tuple:
        """
        Check if stock meets liquidity criteria.

        Args:
            symbol: Stock symbol
            min_volume: Minimum average daily volume (default: 1M)
            max_spread_pct: Maximum bid-ask spread percentage (default: 0.3%)

        Returns:
            (passes: bool, avg_volume: float, spread_pct: float, reason: str)
        """
        try:
            # Get recent data for volume calculation
            df = self.get_market_data(symbol)
            if df is None or len(df) < 20:
                return (True, 0, 0, "Insufficient data - skipping liquidity check")

            # Calculate average daily volume (last 20 days)
            avg_volume = df['volume'].tail(20).mean()

            # Get latest quote for spread calculation
            # Note: Alpaca basic data doesn't include bid/ask, so we estimate spread using high-low
            # Real implementation would need premium data or another source
            latest = df.iloc[-1]
            price = latest['close']

            if price <= 0:
                return (True, 0, 0, "Invalid price - skipping liquidity check")

            # Estimate spread using high-low as proxy (wider = more volatile/less liquid)
            high_low_spread = ((latest['high'] - latest['low']) / price) * 100

            # Check volume requirement
            if avg_volume < min_volume:
                return (False, avg_volume, high_low_spread, f"Volume {avg_volume/1e6:.1f}M < {min_volume/1e6:.0f}M minimum")

            # Check spread requirement (using high-low as proxy)
            if high_low_spread > max_spread_pct * 3:  # Multiply since high-low is typically larger than bid-ask
                return (False, avg_volume, high_low_spread, f"Spread {high_low_spread:.1f}% too wide")

            return (True, avg_volume, high_low_spread, "Passes liquidity check")

        except Exception as e:
            logging.debug(f"Liquidity check failed for {symbol}: {e}")
            return (True, 0, 0, "Check failed - allowing")  # Don't filter if check fails

    def get_volatility_tier(self, symbol: str) -> str:
        """
        Classify stock into volatility tier based on ATR%.

        Returns:
            'low' - Low volatility (< 2% ATR) - good for mean-reversion
            'mid' - Medium volatility (2-5% ATR) - standard strategy
            'high' - High volatility (> 5% ATR) - momentum strategy
        """
        try:
            df = self.get_market_data(symbol)
            if df is None or len(df) < 20:
                return 'mid'  # Default to mid if no data

            df = self.calculate_indicators(df)
            atr_pct = df['ATR_pct'].iloc[-1] if len(df) > 0 else 0

            if pd.isna(atr_pct) or atr_pct <= 0:
                return 'mid'

            if atr_pct < 2.0:
                return 'low'
            elif atr_pct > 5.0:
                return 'high'
            else:
                return 'mid'

        except Exception as e:
            logging.debug(f"Volatility tier check failed for {symbol}: {e}")
            return 'mid'

    # Major sector ETFs for rotation tracking
    SECTOR_ETFS = {
        'XLK': 'Technology',
        'XLF': 'Financials',
        'XLE': 'Energy',
        'XLV': 'Healthcare',
        'XLP': 'Consumer Staples',
        'XLY': 'Consumer Discretionary',
        'XLB': 'Materials',
        'XLI': 'Industrials',
        'XLRE': 'Real Estate',
        'XLU': 'Utilities',
        'XLC': 'Communication',
    }

    def get_sector_rotation_scores(self, lookback_days: int = 20) -> dict:
        """
        Calculate relative strength scores for major sector ETFs.

        Returns:
            dict of {symbol: score} where positive = outperforming, negative = underperforming
        """
        scores = {}
        try:
            # Get SPY as benchmark
            spy = self.get_market_data('SPY')
            if spy is None or len(spy) < lookback_days:
                return {k: 0 for k in self.SECTOR_ETFS}

            spy_current = spy['close'].iloc[-1]
            spy_past = spy['close'].iloc[-lookback_days]
            spy_return = ((spy_current - spy_past) / spy_past) * 100 if spy_past > 0 else 0

            # Calculate each sector's return vs SPY
            for symbol in self.SECTOR_ETFS.keys():
                try:
                    df = self.get_market_data(symbol)
                    if df is None or len(df) < lookback_days:
                        scores[symbol] = 0
                        continue

                    current = df['close'].iloc[-1]
                    past = df['close'].iloc[-lookback_days]
                    sector_return = ((current - past) / past) * 100 if past > 0 else 0

                    # Score = sector return - SPY return (positive = outperforming)
                    scores[symbol] = sector_return - spy_return

                except Exception:
                    scores[symbol] = 0

            return scores

        except Exception as e:
            logging.debug(f"Sector rotation calculation failed: {e}")
            return {k: 0 for k in self.SECTOR_ETFS}

    def scan_catalysts(self, symbol: str) -> dict:
        """
        Scan for trading catalysts in a stock.

        Returns dict with:
        - has_volume_surge: bool (>2x avg volume)
        - is_52_week_high: bool
        - has_gap_up: bool (gap > 1% up from previous close)
        - catalyst_score: int (0-25, boosts signal if positive)
        """
        try:
            df = self.get_market_data(symbol)
            if df is None or len(df) < 30:
                return {'has_volume_surge': False, 'is_52_week_high': False,
                        'has_gap_up': False, 'catalyst_score': 0, 'catalysts': []}

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else None

            catalysts = []
            catalyst_score = 0

            # 1. Volume surge (>2x 20-day average)
            avg_volume = df['volume'].tail(20).mean()
            if latest['volume'] > avg_volume * 2:
                catalysts.append('volume_surge')
                catalyst_score += 8

            # 2. 52-week high (within 1% of 52-week high)
            high_52w = df['high'].tail(252).max() if len(df) >= 252 else df['high'].max()
            if latest['close'] >= high_52w * 0.99:
                catalysts.append('52_week_high')
                catalyst_score += 10

            # 3. Gap up (>1% gap from previous close)
            if prev is not None:
                gap_pct = ((latest['open'] - prev['close']) / prev['close']) * 100
                if gap_pct > 1:
                    catalysts.append('gap_up')
                    catalyst_score += 7

            # Cap catalyst score at 25
            catalyst_score = min(25, catalyst_score)

            return {
                'has_volume_surge': 'volume_surge' in catalysts,
                'is_52_week_high': '52_week_high' in catalysts,
                'has_gap_up': 'gap_up' in catalysts,
                'catalyst_score': catalyst_score,
                'catalysts': catalysts,
                'volume_ratio': latest['volume'] / avg_volume if avg_volume > 0 else 1,
                'distance_from_52w_high': ((high_52w - latest['close']) / high_52w * 100) if high_52w > 0 else 0
            }

        except Exception as e:
            logging.debug(f"Catalyst scan failed for {symbol}: {e}")
            return {'has_volume_surge': False, 'is_52_week_high': False,
                    'has_gap_up': False, 'catalyst_score': 0, 'catalysts': []}

    def get_hourly_market_data(self, symbol: str, lookback_hours: int = 168) -> Optional[pd.DataFrame]:
        """Fetch hourly market data for multi-timeframe analysis"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(hours=lookback_hours)

            # Get 1-hour timeframe bars
            request = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Hour,
                start=start_date,
                end=end_date
            )

            barset = self.data_client.get_stock_bars(request)

            if not barset or symbol not in barset.data:
                return None

            bars = barset.data[symbol]
            if not bars or len(bars) < 24:  # Need at least 24 hours of data
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
            logging.debug(f"Hourly data fetch failed for {symbol}: {e}")
            return None

    def analyze_multi_timeframe(self, symbol: str, use_ai: bool = False) -> Optional[Dict]:
        """Analyze symbol using multiple timeframes (daily + hourly)"""
        try:
            # Get daily data
            df_daily = self.get_market_data(symbol)
            if df_daily is None or len(df_daily) < self.sma_slow:
                return None

            df_daily = self.calculate_indicators(df_daily)

            # Get hourly data
            df_hourly = self.get_hourly_market_data(symbol)
            hourly_indicators = None
            if df_hourly is not None and len(df_hourly) >= self.sma_slow:
                df_hourly = self.calculate_indicators(df_hourly)
                hourly_indicators = df_hourly.iloc[-1]

            latest = df_daily.iloc[-1]

            if pd.isna(latest[f'SMA_{self.sma_fast}']) or pd.isna(latest['RSI']):
                return None

            sma_fast_daily = latest[f'SMA_{self.sma_fast}']
            sma_slow_daily = latest[f'SMA_{self.sma_slow}']
            rsi_daily = latest['RSI']
            price = latest['close']

            # Analyze daily timeframe
            daily_signal = None
            if sma_fast_daily > sma_slow_daily and rsi_daily < self.rsi_buy_threshold:
                daily_signal = "BUY"
            elif sma_fast_daily < sma_slow_daily and rsi_daily > self.rsi_sell_threshold:
                daily_signal = "SELL"

            # Analyze hourly timeframe (if available)
            hourly_signal = None
            if hourly_indicators is not None:
                sma_fast_hourly = hourly_indicators[f'SMA_{self.sma_fast}']
                sma_slow_hourly = hourly_indicators[f'SMA_{self.sma_slow}']
                rsi_hourly = hourly_indicators['RSI']

                if not pd.isna(sma_fast_hourly) and not pd.isna(rsi_hourly):
                    if sma_fast_hourly > sma_slow_hourly and rsi_hourly < self.rsi_buy_threshold:
                        hourly_signal = "BUY"
                    elif sma_fast_hourly < sma_slow_hourly and rsi_hourly > self.rsi_sell_threshold:
                        hourly_signal = "SELL"

            # Multi-timeframe signal generation
            # Require BOTH daily AND hourly to agree (weighted: daily 70%, hourly 30%)
            signal = None
            signal_strength = "WEAK"

            if daily_signal and hourly_signal:
                # Both timeframes must agree
                if daily_signal == hourly_signal:
                    signal = daily_signal
                    # Strength based on how strong the signals are
                    if daily_signal == "BUY":
                        rsi_score = (30 - rsi_daily) / 30  # Lower RSI = stronger
                        signal_strength = "STRONG" if rsi_daily < 25 else "MEDIUM"
                    else:
                        rsi_score = (rsi_daily - 70) / 30  # Higher RSI = stronger
                        signal_strength = "STRONG" if rsi_daily > 75 else "MEDIUM"
                else:
                    # Conflicting signals - no trade
                    signal = "HOLD"
                    signal_strength = "CONFLICTED"
            elif daily_signal and not hourly_indicators:
                # No hourly data - use daily only but mark as weaker
                signal = daily_signal
                signal_strength = "DAILY_ONLY"
            else:
                signal = "HOLD"

            # Track multi-timeframe conflict & volume downgrade (before downstream filters)
            mtf_conflict_blocked = (
                daily_signal is not None
                and hourly_signal is not None
                and daily_signal != hourly_signal
            )
            volume_confirmed = True
            volume_ratio = 1.0
            volume_downgrade = False
            if self.enable_volume_confirmation and signal in ["BUY", "SELL"]:
                vol_confirmed, volume_ratio, _ = self.check_volume_confirmation(df_daily)
                if not vol_confirmed:
                    if signal_strength in ["STRONG", "MEDIUM"]:
                        signal_strength = "WEAK_VOLUME"
                        volume_downgrade = True
                    volume_confirmed = False

            # AI enhancement (if enabled globally, configured, and enabled at ticker level)
            ai_insight = None
            ai_research = None  # Define unconditionally so it's in scope for the filter_results block below
            if use_ai and self.ai.is_configured and self.use_ai_for_ticker_analysis and signal in ["BUY", "SELL"]:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    ai_research = loop.run_until_complete(self.ai.research_symbol(symbol, lookback_days=2))
                    loop.close()

                    if ai_research and ai_research.get('ai_recommendation'):
                        ai_rec = ai_research['ai_recommendation']
                        ai_signal = ai_rec.recommendation.upper()

                        if ai_signal == signal and ai_rec.confidence > 0.7:
                            signal_strength = "AI_ENHANCED"
                            ai_insight = f"AI confirms {signal} with {ai_rec.confidence:.1%} confidence"
                        elif ai_signal != signal:
                            signal_strength = "CONFLICTED"
                            ai_insight = f"AI suggests {ai_signal} vs technical {signal}"

                except Exception as e:
                    if self._detect_rate_limit_error(str(e)) or self._detect_ai_failure_error(str(e)):
                        self._handle_rate_limit_error(e)
                    logging.debug(f"AI analysis failed for {symbol}: {e}")

            # ── Build filter_results (all downstream filter results) ─────────────────
            ai_rec_data = ai_research.get('ai_recommendation') if ai_research else None
            ai_signal_raw = ai_rec_data.recommendation.upper() if ai_rec_data else None
            ai_conflict_blocked_flag = (
                ai_signal_raw is not None
                and ai_signal_raw != signal
                and signal in ['BUY', 'SELL']
            )
            ai_enhanced_flag = (
                ai_signal_raw is not None
                and ai_signal_raw == signal
                and (ai_rec_data.confidence or 0) > 0.7
            )

            filter_results = {
                'multi_timeframe_conflict': {
                    'passed': not mtf_conflict_blocked,
                    'blocked': mtf_conflict_blocked,
                    'reason': f"Daily {daily_signal} vs hourly {hourly_signal} conflict" if mtf_conflict_blocked else None,
                    'daily_signal': str(daily_signal) if daily_signal else None,
                    'hourly_signal': str(hourly_signal) if hourly_signal else None,
                },
                'volume_downgrade': {
                    'passed': not volume_downgrade,
                    'blocked': volume_downgrade,
                    'reason': f"Volume {float(volume_ratio):.2f}x avg" if volume_downgrade else None,
                    'volume_ratio': float(volume_ratio) if pd.notna(volume_ratio) else None,
                },
                'ai_conflict': {
                    'passed': not ai_conflict_blocked_flag,
                    'blocked': ai_conflict_blocked_flag,
                    'reason': f"AI recommends {ai_signal_raw} vs {signal}" if ai_conflict_blocked_flag else None,
                    'ai_signal': ai_signal_raw,
                    'ai_confidence': float(ai_rec_data.confidence) if ai_rec_data else None,
                    'enhanced': ai_enhanced_flag,
                },
            }

            # ── Apply filter blocks (only if signal is BUY and filter is enabled) ────────
            if signal == "BUY":
                blocked_by_filter = None
                if self.enable_mtf_conflict_filter and mtf_conflict_blocked:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    blocked_by_filter = "multi_timeframe_conflict"
                    logging.info(f"⏸ {symbol}: MTF conflict block (daily={daily_signal} vs hourly={hourly_signal})")
                elif self.enable_vol_downgrade_filter and volume_downgrade:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    blocked_by_filter = "volume_downgrade"
                    logging.info(f"⏸ {symbol}: Volume downgrade block ({float(volume_ratio):.2f}x avg)")
                elif self.enable_ai_conflict_filter and ai_conflict_blocked_flag:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    blocked_by_filter = "ai_conflict"
                    ai_rec_data = ai_research.get('ai_recommendation') if ai_research else None
                    ai_sig = ai_rec_data.recommendation.upper() if ai_rec_data else None
                    logging.info(f"⏸ {symbol}: AI conflict block (AI={ai_sig} vs tech={signal})")

            filter_results, blocked_by, blocked_count = self._finalize_filter_results(filter_results)

            # ── Compute total_score from available indicators (needed for buy_criteria) ──
            # RSI score: 0-100, 50=neutral; oversold positive, overbought negative
            rsi_score_daily = 50 + (50 - rsi_daily) * 1.5 if rsi_daily < 50 else 50 - (rsi_daily - 50) * 1.5
            rsi_score_daily = max(0, min(100, rsi_score_daily))
            # SMA score: based on trend direction and strength
            sma_pct_diff = ((sma_fast_daily - sma_slow_daily) / sma_slow_daily) * 100 if sma_slow_daily > 0 else 0
            _sma_score_daily = min(30, sma_pct_diff * 5) if sma_pct_diff > 0 else max(-30, sma_pct_diff * 5)
            # MACD score: use MACD histogram if available
            macd_hist = df_daily['MACD_histogram'].iloc[-1] if 'MACD_histogram' in df_daily.columns else None
            _macd_score_daily = min(30, float(macd_hist) * 50) if macd_hist is not None and pd.notna(macd_hist) else 0
            # BB score: how close to lower/upper band
            bb_position = df_daily['BB_width'].iloc[-1] if 'BB_width' in df_daily.columns else 0.15
            _bb_score_daily = 25 - abs(bb_position - 0.10) * 100 if pd.notna(bb_position) else 15
            # Total score: blend of components (centered at 0, positive=bullish, negative=bearish)
            total_score = rsi_score_daily + _sma_score_daily + _macd_score_daily + _bb_score_daily - 50

            # Build buy_criteria for storage
            buy_criteria = [
                {
                    'name': 'Score ≥ 65',
                    'passed': bool(total_score >= 65),
                    'detail': f'{total_score:.0f}/100',
                },
                {
                    'name': 'RSI not overbought',
                    'passed': bool(rsi_daily < 70),
                    'detail': f'{rsi_daily:.1f}',
                },
                {
                    'name': 'SMA uptrend',
                    'passed': bool(sma_fast_daily > sma_slow_daily),
                    'detail': 'uptrend' if sma_fast_daily > sma_slow_daily else 'downtrend',
                },
                {
                    'name': 'MACD positive',
                    'passed': bool(macd_hist is not None and macd_hist > 0),
                    'detail': f'{macd_hist:.3f}' if macd_hist is not None else 'N/A',
                },
            ]
            if volume_ratio is not None:
                buy_criteria.append({
                    'name': 'Volume ≥ avg',
                    'passed': bool(float(volume_ratio) >= 1.0),
                    'detail': f'{float(volume_ratio):.2f}x',
                })

            failed_criteria = [c['name'] for c in buy_criteria if not c['passed']]
            passes_all_buy_criteria = len(failed_criteria) == 0

            analysis_result = {
                'symbol': symbol,
                'price': price,
                'sma_fast': sma_fast_daily,
                'sma_slow': sma_slow_daily,
                'rsi': rsi_daily,
                'signal': signal,
                'signal_strength': signal_strength,
                'total_score': total_score,
                'rsi_score': rsi_score_daily,
                'sma_score': _sma_score_daily,
                'macd_score': _macd_score_daily,
                'bb_score': _bb_score_daily,
                'buy_criteria': buy_criteria,
                'passes_all_buy_criteria': passes_all_buy_criteria,
                'filter_results': filter_results,
                'blocked_by': blocked_by,
                'blocked_count': blocked_count,
                'timestamp': latest.get('timestamp', datetime.now(timezone.utc)),
                'multi_timeframe': True,
                'volume_confirmed': volume_confirmed,
                'volume_ratio': volume_ratio
            }

            # Add hourly data to result if available
            if hourly_indicators is not None:
                analysis_result['hourly_rsi'] = hourly_indicators['RSI']
                analysis_result['hourly_signal'] = hourly_signal

            if ai_insight and self.use_ai_for_ticker_analysis:
                analysis_result['ai_insight'] = ai_insight

            self.mark_recent_research(symbol)
            return analysis_result

        except Exception as e:
            logging.debug(f"Multi-timeframe analysis failed for {symbol}: {e}")
            return None

    def check_earnings_calendar(self, symbol: str, days_ahead: int = 3) -> tuple:
        """
        Check if stock has earnings coming up within specified days.
        Uses Yahoo Finance free API.

        Args:
            symbol: Stock symbol
            days_ahead: Number of days to look ahead

        Returns:
            (has_earnings: bool, earnings_date: datetime or None, days_until: int or None)
        """
        try:
            ticker = yf.Ticker(symbol)
            earnings_dates = ticker.earnings_dates

            if earnings_dates is None or earnings_dates.empty:
                return (False, None, None)

            # Get the next earnings date
            now = datetime.now()
            for date in earnings_dates.index:
                if isinstance(date, str):
                    # Parse string format
                    earnings_date = pd.to_datetime(date)
                else:
                    earnings_date = date

                # Skip past earnings
                if earnings_date <= now:
                    continue

                # Check if within the window
                days_until = (earnings_date - now).days

                if days_until <= days_ahead:
                    return (True, earnings_date, days_until)

            return (False, None, None)

        except Exception as e:
            logging.debug(f"Earnings check failed for {symbol}: {e}")
            return (False, None, None)

    def _finalize_filter_results(self, filter_results: Dict) -> tuple:
        """
        Compute blocked_by and blocked_count from a populated filter_results dict.
        Called by both analyze_multi_timeframe and analyze_symbol before they
        build their respective analysis_result dicts.

        Returns (filter_results, blocked_by, blocked_count).
        blocked_by is the first filter that blocked, or None.
        """
        blocking_filters = [
            name for name, res in filter_results.items()
            if res.get('blocked') is True
        ]
        blocked_count = len(blocking_filters)
        blocked_by = blocking_filters[0] if blocking_filters else None
        return filter_results, blocked_by, blocked_count

    def check_sp_relative_strength(self, symbol: str, df: pd.DataFrame, lookback_days: int = 20) -> tuple:
        """
        Check if stock is outperforming SPY over the lookback period.

        Args:
            symbol: Stock symbol
            df: Stock DataFrame with price data
            lookback_days: Number of days to compare (default: 20)

        Returns:
            (outperforming: bool, stock_return: float, spy_return: float, spread: float)
        """
        try:
            # Guard against None or empty DataFrame
            if df is None or df.empty or 'close' not in df.columns:
                return (True, 0, 0, 0)

            # Calculate stock return
            if len(df) < lookback_days:
                return (True, 0, 0, 0)  # Not enough data, don't filter

            current_price = df['close'].iloc[-1]
            past_price = df['close'].iloc[-lookback_days]

            if pd.isna(current_price) or pd.isna(past_price) or past_price <= 0:
                return (True, 0, 0, 0)

            stock_return = ((current_price - past_price) / past_price) * 100

            # Get SPY data (use cache if available for this cycle)
            spy_symbol = "SPY"
            if spy_symbol in self._market_data_cache:
                # Use cached SPY data if available
                cached_time, spy_df = self._market_data_cache.get(spy_symbol, (None, None))
                if spy_df is not None and len(spy_df) >= lookback_days:
                    spy_current = spy_df['close'].iloc[-1]
                    spy_past = spy_df['close'].iloc[-lookback_days]
                    if not pd.isna(spy_current) and not pd.isna(spy_past) and spy_past > 0:
                        spy_return = ((spy_current - spy_past) / spy_past) * 100
                        spread = stock_return - spy_return
                        return (spread > 0, stock_return, spy_return, spread)

            # Fallback to yfinance if no cache
            spy = yf.Ticker("SPY")
            spy_hist = spy.history(period=f"{lookback_days+10}d")

            if spy_hist.empty or len(spy_hist) < lookback_days:
                return (True, 0, 0, 0)  # Can't get SPY data, don't filter

            spy_current = spy_hist['Close'].iloc[-1]
            spy_past = spy_hist['Close'].iloc[-lookback_days]

            if pd.isna(spy_current) or pd.isna(spy_past) or spy_past <= 0:
                return (True, 0, 0, 0)

            spy_return = ((spy_current - spy_past) / spy_past) * 100

            # Spread: positive = stock outperforming SPY
            spread = stock_return - spy_return

            # Stock is outperforming if spread > 0
            return (spread > 0, stock_return, spy_return, spread)

        except Exception as e:
            logging.debug(f"SPY relative strength check failed for {symbol}: {e}")
            return (True, 0, 0, 0)  # Don't filter if check fails

    def check_volume_confirmation(self, df: pd.DataFrame, min_volume_ratio: float = 1.0) -> tuple:
        """
        Check if volume confirms the price movement.

        Args:
            df: DataFrame with volume data
            min_volume_ratio: Minimum volume / volume_sma_20 ratio (default: 1.0 = above average)

        Returns:
            (confirmed: bool, volume_ratio: float, volume_sma: float)
        """
        if df is None or len(df) < 20:
            return False, 0.0, 0.0

        latest = df.iloc[-1]
        volume = latest.get('volume', 0)
        volume_sma = latest.get('volume_sma_20', 0)

        if volume_sma is None or volume_sma == 0:
            return False, 0.0, 0.0

        volume_ratio = volume / volume_sma
        confirmed = volume_ratio >= min_volume_ratio

        return confirmed, volume_ratio, volume_sma

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

        # MACD (12, 26, 9)
        ema_12 = df['close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema_12 - ema_26
        df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_histogram'] = df['MACD'] - df['MACD_signal']

        # Bollinger Bands (20, 2) - for mean-reversion signals
        df['BB_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + (2 * bb_std)
        df['BB_lower'] = df['BB_middle'] - (2 * bb_std)
        df['BB_width'] = (df['BB_upper'] - df['BB_lower']) / df['BB_middle']  # Bandwidth

        # ATR (Average True Range) - for volatility-based position sizing
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = true_range.rolling(window=14).mean()
        df['ATR_pct'] = (df['ATR'] / df['close']) * 100  # ATR as percentage of price

        # Volume SMA - for volume confirmation
        df['volume_sma_20'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_sma_20']  # Current volume vs 20-day average

        # VWAP (Volume Weighted Average Price) - for entry timing
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
        # Fill NaN values in VWAP using forward fill then backward fill
        df['vwap'] = df['vwap'].ffill().bfill()
        df['vwap_distance'] = ((df['close'] - df['vwap']) / df['vwap']) * 100  # Distance from VWAP in %

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

            # RSI-based score (contributes to total)
            rsi_score = 0
            if rsi < 30:
                rsi_score = 25 * (1 - rsi / 30)
            elif rsi < 50:
                rsi_score = 12.5 * (1 - (rsi - 30) / 20)
            elif rsi < 70:
                rsi_score = -12.5 * ((rsi - 50) / 20)
            else:
                rsi_score = -25 * min(1, (rsi - 70) / 30)

            # SMA Score: based on how far fast is above/below slow
            sma_score = 0
            if sma_fast > sma_slow:
                sma_pct = ((sma_fast - sma_slow) / sma_slow) * 100
                sma_score = min(25, sma_pct * 5)  # Max 25 at 5% separation
            elif sma_fast < sma_slow:
                sma_pct = ((sma_slow - sma_fast) / sma_slow) * 100
                sma_score = -min(25, sma_pct * 5)
            else:
                sma_score = 0

            # MACD Score: normalize by price so high-priced stocks don't always max out.
            # A histogram equal to 0.5% of price earns the maximum +25.
            macd_hist = latest.get('MACD_histogram', 0)
            if pd.notna(macd_hist) and price > 0:
                macd_score = max(-25, min(25, (macd_hist / price) * 5000))
            else:
                macd_score = 0

            # Bollinger Band Score: based on position within bands
            bb_score = 0
            if pd.notna(bb_lower) and pd.notna(bb_middle) and price > 0 and pd.notna(bb_upper):
                bb_position = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
                # Lower band = bullish (25), Upper = bearish (-25)
                bb_score = max(-25, min(25, 25 - (bb_position * 50)))

            # Get volatility tier and adjust scoring
            # Low volatility = mean-reversion works better
            # High volatility = momentum works better
            atr_pct = latest.get('ATR_pct', 0)
            volatility_tier = 'mid'
            if pd.notna(atr_pct):
                if atr_pct < 2.0:
                    volatility_tier = 'low'
                    # Boost mean-reversion signals for low volatility
                    rsi_score *= 1.3  # Stronger weight on RSI for low-vol
                elif atr_pct > 5.0:
                    volatility_tier = 'high'
                    # Boost momentum signals for high volatility
                    sma_score *= 1.3  # Stronger weight on SMA/momentum for high-vol

            # Catalyst scanner - boost scores for stocks with catalysts
            catalyst_data = self.scan_catalysts(symbol)
            catalyst_score = catalyst_data.get('catalyst_score', 0)

            # Total Score (0-100 scale, 50 = neutral) - daily component
            daily_score = rsi_score + sma_score + macd_score + bb_score + catalyst_score
            daily_score = daily_score

            # Multi-timeframe blending: blend daily (70%) with hourly (30%)
            # Hourly score uses RSI + SMA on 1-hour bars for intraday confirmation
            hourly_score = None
            if self.enable_multi_timeframe:
                try:
                    df_hourly = self.get_hourly_market_data(symbol, lookback_hours=120)
                    if df_hourly is not None and len(df_hourly) >= self.sma_slow:
                        df_hourly = self.calculate_indicators(df_hourly)
                        h = df_hourly.iloc[-1]
                        h_rsi = h.get('RSI', float('nan'))
                        h_sma_fast = h.get(f'SMA_{self.sma_fast}', float('nan'))
                        h_sma_slow = h.get(f'SMA_{self.sma_slow}', float('nan'))
                        if pd.notna(h_rsi) and pd.notna(h_sma_fast) and pd.notna(h_sma_slow):
                            # Simplified 0-100 score from hourly indicators
                            h_rsi_score = 0
                            if h_rsi < 30:
                                h_rsi_score = 25 * (1 - h_rsi / 30)
                            elif h_rsi > 70:
                                h_rsi_score = -25 * ((h_rsi - 70) / 30)
                            h_sma_score = 0
                            if h_sma_fast > h_sma_slow:
                                h_sma_pct = ((h_sma_fast - h_sma_slow) / h_sma_slow) * 100
                                h_sma_score = min(25, h_sma_pct * 5)
                            elif h_sma_fast < h_sma_slow:
                                h_sma_pct = ((h_sma_slow - h_sma_fast) / h_sma_slow) * 100
                                h_sma_score = -min(25, h_sma_pct * 5)
                            hourly_score = h_rsi_score + h_sma_score
                except Exception as e:
                    logging.debug(f"Hourly score failed for {symbol}: {e}")

            if hourly_score is not None:
                total_score = daily_score * (1 - self.hourly_weight) + hourly_score * self.hourly_weight
                logging.debug(
                    f"MTF blend {symbol}: daily={daily_score:.1f} hourly={hourly_score:.1f} "
                    f"→ blended={total_score:.1f}"
                )
            else:
                total_score = daily_score

            # Determine signal based on score
            # BUY threshold: 65+, SELL threshold: 35-
            if total_score >= 50:
                signal = "BUY"
                if total_score >= 65:
                    signal_strength = "STRONG"
                else:
                    signal_strength = "MEDIUM"
            elif total_score <= -50:
                signal = "SELL"
                if total_score <= 20:
                    signal_strength = "STRONG"
                else:
                    signal_strength = "MEDIUM"
            else:
                signal = "HOLD"
                signal_strength = "WEAK"

            # Apply market regime modifiers (if enabled)
            regime_info = None
            regime_blocked = False
            if self.enable_regime_filter:
                try:
                    regime_info = self.get_current_market_regime()
                    mods = regime_info.get('modifiers', {})

                    if regime_info.get('regime') in ['TRENDING_BULLISH', 'TRENDING_BEARISH']:
                        # In trending markets, be more selective about BUY signals
                        # Require stronger RSI for buy signals in trending
                        if signal == "BUY" and rsi >= mods.get('rsi_buy_threshold', 30):
                            # But only block if we have data and signal is weak
                            if total_score < 65:
                                signal = "HOLD"
                                signal_strength = "WEAK"
                                logging.debug(f"📊 {symbol}: Blocked by regime filter ({regime_info['regime']}, RSI: {rsi:.1f})")
                                regime_blocked = True

                    # Add regime info to result
                    if regime_info:
                        analysis_result['_regime'] = regime_info.get('regime')
                        analysis_result['_regime_adx'] = regime_info.get('adx')
                    filter_results['regime_filter'] = {
                        'passed': not regime_blocked,
                        'blocked': regime_blocked,
                        'reason': f"{regime_info.get('regime')} market" if regime_blocked else None,
                        'regime': regime_info.get('regime') if regime_info else None,
                        'adx': regime_info.get('adx') if regime_info else None,
                    }

                except Exception as e:
                    logging.debug(f"Regime check failed for {symbol}: {e}")
                    filter_results['regime_filter'] = {'passed': True, 'blocked': False, 'reason': None}

            # Earnings filter: skip BUY signals near earnings
            earnings_warning = None
            if signal == "BUY" and self.earnings_days_skip > 0:
                has_earnings, earnings_date, days_until = self.check_earnings_calendar(symbol, self.earnings_days_skip)
                if has_earnings:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    earnings_warning = f"Earnings in {days_until} days - skipping buy"
                    logging.info(f"⚠️ {symbol}: {earnings_warning}")
                    earnings_blocked = True
                    filter_results["earnings_filter"] = {
                        "passed": not earnings_blocked,
                        "blocked": earnings_blocked,
                        "reason": earnings_warning,
                        "days_until": days_until,
                    }

            # SPY Relative Strength filter: only buy stocks outperforming SPY
            sp_warning = None
            sp_blocked = False
            if signal == "BUY" and self.enable_sp_filter:
                # Need to recalculate indicators to get the dataframe
                df = self.calculate_indicators(df.copy())
                outperforming, stock_ret, spy_ret, spread = self.check_sp_relative_strength(symbol, df)
                if not outperforming:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    sp_warning = f"Underperforming SPY ({spread:+.1f}% spread)"
                    logging.info(f"⚠️ {symbol}: {sp_warning} (Stock: {stock_ret:+.1f}%, SPY: {spy_ret:+.1f}%)")

                    sp_blocked = True
                    filter_results["sp_relative_strength"] = {
                        "passed": not sp_blocked,
                        "blocked": sp_blocked,
                        "reason": sp_warning,
                        "spread": spread,
                    }
            # ================================================
            # NEW: Wire up additional signal filters (Phases 6.2, 7.1, 7.2, 7.3)
            # ================================================

            # Volume Confirmation filter - require above-average volume for BUY signals
            volume_warning = None
            volume_blocked = False
            if signal == "BUY" and self.enable_volume_confirmation:
                volume_ratio = latest.get('volume_ratio', 1.0)
                if pd.notna(volume_ratio) and volume_ratio < 1.0:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    volume_warning = f"Below-average volume ({volume_ratio:.2f}x avg)"
                    logging.info(f"📊 {symbol}: Volume confirmation failed ({volume_ratio:.2f}x avg volume)")
                    volume_blocked = True
            filter_results["volume_confirmation"] = {
                "passed": not volume_blocked,
                "blocked": volume_blocked,
                "reason": volume_warning,
                "volume_ratio": float(volume_ratio) if pd.notna(volume_ratio) else None,
            }

            if signal == "BUY":
                try:
                    from src.analysis.trading_windows import is_trading_allowed
                    allowed, time_reason = is_trading_allowed()
                    if not allowed:
                        signal = "HOLD"
                        signal_strength = "WEAK"
                        logging.info(f"⏰ {symbol}: Blocked by trading window ({time_reason})")
                except Exception as e:
                    logging.debug(f"Trading window check failed: {e}")
            trading_window_blocked = True
            filter_results["trading_window"] = {
                "passed": not trading_window_blocked,
                "blocked": trading_window_blocked,
                "reason": trading_window_warning,
            }

            # News Sentiment filter (Phase 7.1) - skip if strongly negative
            news_warning = None
            news_blocked = False
            if signal == "BUY":
                try:
                    from src.analysis.news_sentiment import filter_signal_by_sentiment
                    allowed, news_score, news_reason = filter_signal_by_sentiment(symbol, signal)
                    if not allowed:
                        signal = "HOLD"
                        signal_strength = "WEAK"
                        news_warning = f"Negative news sentiment ({news_reason})"
                        news_blocked = True
                        logging.info(f"📰 {symbol}: {news_warning}")
                except Exception as e:
                    logging.debug(f"News sentiment check failed: {e}")

            # Short Interest filter (Phase 7.2) - skip if high short + bearish
            filter_results["news_sentiment"] = {
                "passed": not news_blocked,
                "blocked": news_blocked,
                "reason": news_warning,
                "news_score": news_score,
            }

            short_warning = None
            short_blocked = False
            if signal == "BUY":
                try:
                    from src.analysis.short_interest import filter_by_short_interest
                    allowed, squeeze_score, short_reason = filter_by_short_interest(symbol, signal)
                    if not allowed:
                        signal = "HOLD"
                        signal_strength = "WEAK"
                        short_warning = f"Short interest warning ({short_reason})"
                        short_blocked = True
                        logging.info(f"📉 {symbol}: {short_warning}")
                except Exception as e:
                    logging.debug(f"Short interest check failed: {e}")

            filter_results["short_interest"] = {
                "passed": not short_blocked,
                "blocked": short_blocked,
                "reason": short_warning,
                "squeeze_score": squeeze_score,
            }

            insider_score = 0
            if signal == "BUY":
                try:
                    from src.analysis.insider_trading import get_insider_score
                    insider_score = get_insider_score(symbol)
                    if insider_score > 50:
                        # Boost signal strength for strong insider buying
                        total_score = total_score + 10
                        logging.info(f"📋 {symbol}: Insider score {insider_score} - signal boosted")
                except Exception as e:
                    logging.debug(f"Insider check failed: {e}")
            filter_results['insider_trading'] = {
                'passed': True,  # not a blocking filter
                'blocked': False,
                'reason': None,
                'insider_score': insider_score,
                'boosted': insider_boosted,
            }

            # ================================================
            # End new filters
            # ================================================

            # Ensure ai_research is always defined for blocked_by computation below
            ai_research = None

            # AI enhancement (if enabled globally, configured, and enabled at ticker level)
            if use_ai and self.ai.is_configured and self.use_ai_for_ticker_analysis and signal:
                try:
                    # Get AI research (non-blocking)
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
                    if self._detect_rate_limit_error(str(e)) or self._detect_ai_failure_error(str(e)):
                        self._handle_rate_limit_error(e)
                        # Don't break the analysis, just skip AI for this symbol
                    logging.debug(f"AI analysis failed for {symbol}: {e}")

            ANALYSIS_RESULT_PLACEHOLDER = True
            # ── Populate filter_results with all downstream filter results ──────────
            # These were tracked via local variables as each filter ran;
            # now we assemble them into the filter_results dict.
            if 'filter_results' not in locals() or not filter_results:
                filter_results = {}

            # mtf conflict
            filter_results['multi_timeframe_conflict'] = {
                'passed': not mtf_conflict_blocked,
                'blocked': mtf_conflict_blocked,
                'reason': f"Daily {daily_signal} vs hourly {hourly_signal} conflict" if mtf_conflict_blocked else None,
                'daily_signal': str(daily_signal) if daily_signal else None,
                'hourly_signal': str(hourly_signal) if hourly_signal else None,
            }
            # volume downgrade
            filter_results['volume_downgrade'] = {
                'passed': not volume_downgrade,
                'blocked': volume_downgrade,
                'reason': f"Volume {float(volume_ratio):.2f}x avg" if volume_downgrade else None,
                'volume_ratio': float(volume_ratio) if pd.notna(volume_ratio) else None,
            }
            # ai conflict / enhancement
            ai_rec_data = ai_research.get('ai_recommendation') if ai_research else None
            ai_signal_raw = ai_rec_data.recommendation.upper() if ai_rec_data else None
            ai_conflict_blocked = (
                ai_signal_raw is not None
                and ai_signal_raw != signal
                and signal in ['BUY', 'SELL']
            )
            ai_enhanced_flag = (
                ai_signal_raw is not None
                and ai_signal_raw == signal
                and (ai_rec_data.confidence or 0) > 0.7
            )
            filter_results['ai_conflict'] = {
                'passed': not ai_conflict_blocked,
                'blocked': ai_conflict_blocked,
                'reason': f"AI recommends {ai_signal_raw} vs {signal}" if ai_conflict_blocked else None,
                'ai_signal': ai_signal_raw,
                'ai_confidence': float(ai_rec_data.confidence) if ai_rec_data else None,
                'enhanced': ai_enhanced_flag,
            }

            # ── Apply filter blocks (only if signal is BUY and filter is enabled) ────────
            if signal == "BUY":
                if self.enable_mtf_conflict_filter and mtf_conflict_blocked:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    logging.info(f"⏸ {symbol}: MTF conflict block (daily={daily_signal} vs hourly={hourly_signal})")
                elif self.enable_vol_downgrade_filter and volume_downgrade:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    logging.info(f"⏸ {symbol}: Volume downgrade block ({float(volume_ratio):.2f}x avg)")
                elif self.enable_ai_conflict_filter and ai_conflict_blocked:
                    signal = "HOLD"
                    signal_strength = "WEAK"
                    logging.info(f"⏸ {symbol}: AI conflict block (AI={ai_signal_raw} vs tech={signal})")

            filter_results, blocked_by, blocked_count = self._finalize_filter_results(filter_results)

            analysis_result = {
                'symbol': symbol,
                'price': price,
                'sma_fast': sma_fast,
                'sma_slow': sma_slow,
                'rsi': rsi,
                'macd': latest.get('MACD', 0),
                'macd_signal': latest.get('MACD_signal', 0),
                'macd_histogram': latest.get('MACD_histogram', 0),
                'bb_upper': latest.get('BB_upper', 0),
                'bb_middle': latest.get('BB_middle', 0),
                'bb_lower': latest.get('BB_lower', 0),
                'signal': signal,
                'signal_strength': signal_strength,
                'total_score': total_score,
                'rsi_score': rsi_score_daily,
                'sma_score': _sma_score_daily,
                'macd_score': _macd_score_daily,
                'bb_score': _bb_score_daily,
                'total_score': total_score,
                'rsi_score': rsi_score,
                'sma_score': sma_score,
                'macd_score': macd_score,
                'bb_score': bb_score,
                'volatility_tier': volatility_tier,
                'atr_pct': atr_pct if pd.notna(atr_pct) else 0,
                'catalyst_score': catalyst_score,
                'catalysts': catalyst_data.get('catalysts', []),
                'earnings_warning': earnings_warning,
                'sp_warning': sp_warning,
                'filter_results': filter_results,
                'blocked_by': blocked_by,
                'blocked_count': blocked_count,
                'timestamp': latest.get('timestamp', datetime.now(timezone.utc))
            }

            # Only use AI insight if enabled at ticker level
            if ai_insight and self.use_ai_for_ticker_analysis:
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
        except:
            return 0.0

    def get_current_position_value(self, symbol: str) -> float:
        """Get current position market value for a symbol"""
        try:
            position = self.trading_client.get_open_position(symbol)
            return float(position.market_value) if position else 0.0
        except:
            return 0.0

    def has_pending_orders(self, symbol: str) -> bool:
        """Check if there are pending orders for a symbol"""
        try:
            # Get all orders and filter for open/pending ones

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
        except:
            return 100000.0  # Default fallback

    def check_drawdown_protection(self) -> tuple:
        """
        Check if portfolio has exceeded max drawdown threshold.

        Returns:
            (shouldause:_p bool, current_drawdown: float, peak_value: float)
        """
        if not self.enable_drawdown_protection:
            return False, 0.0, self.peak_portfolio_value or 0.0

        try:
            current_value = self.get_portfolio_total_value()

            # Update peak value if current is higher
            if self.peak_portfolio_value is None or current_value > self.peak_portfolio_value:
                self.peak_portfolio_value = current_value
                self.drawdown_paused = False  # Reset pause if we hit new high
                return False, 0.0, current_value

            # Calculate drawdown from peak
            drawdown = (self.peak_portfolio_value - current_value) / self.peak_portfolio_value * 100

            # Check if we should pause
            if drawdown >= self.max_drawdown_pct:
                self.drawdown_paused = True
                logging.warning(f"🛑 MAX DRAWDOWN TRIGGERED: {drawdown:.1f}% (max: {self.max_drawdown_pct}%)")
                logging.warning(f"   Peak: ${self.peak_portfolio_value:,.2f} | Current: ${current_value:,.2f}")
                logging.warning(f"   ⚠️ TRADING PAUSED - Manual resume required")
                return True, drawdown, self.peak_portfolio_value

            return False, drawdown, self.peak_portfolio_value

        except Exception as e:
            logging.debug(f"Error checking drawdown: {e}")
            return False, 0.0, self.peak_portfolio_value or 0.0

    def send_trade_notification(self, trade_type: str, symbol: str, quantity: int,
                                price: float, side: str, analysis: Dict = None) -> bool:
        """
        Send Telegram notification when a trade is executed.

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not self.enable_telegram_notifications:
            return False

        if not self.telegram_chat_id:
            logging.warning("⚠️ Telegram notifications enabled but no chat ID configured")
            return False

        try:
            # Build message
            emoji = "🟢" if side.upper() == "BUY" else "🔴"

            message = f"""
{emoji} *TRADE EXECUTED*

*Side:* {side.upper()}
*Symbol:* {symbol}
*Shares:* {quantity}
*Price:* ${price:,.2f}
*Total:* ${price * quantity:,.2f}
"""

            if analysis and analysis.get('rsi'):
                message += f"""
📊 *Technical:*
• RSI: {analysis.get('rsi', 0):.1f}
• SMA Fast: ${analysis.get('sma_fast', 0):.2f}
• SMA Slow: ${analysis.get('sma_slow', 0):.2f}
• Signal: {analysis.get('signal', 'N/A')}
"""

            message += f"""
💼 *Portfolio:* ${self.get_portfolio_total_value():,.2f}
⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""

            # Send via Telegram Bot API directly

            bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
            if bot_token:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                data = {
                    'chat_id': self.telegram_chat_id,
                    'text': message,
                    'parse_mode': 'Markdown'
                }
                resp = requests.post(url, json=data, timeout=10)
                if resp.status_code == 200:
                    logging.info(f"📱 Telegram notification sent for {symbol}")
                    return True
                else:
                    logging.warning(f"⚠️ Telegram API error: {resp.text}")
                    return False
            else:
                logging.warning(f"⚠️ Telegram notifications enabled but no bot token")
                return False

        except Exception as e:
            logging.warning(f"⚠️ Failed to send trade notification: {e}")
            return False

    def send_summary_notification(self, loop_count: int, total_trades: int, portfolio_value: float,
                                unrealized_pl: float, positions: int, summary_type: str = "periodic",
                                symbols_analyzed: int = 0) -> bool:
        """
        Send a periodic summary notification via Telegram.

        Args:
            loop_count: Current loop number
            total_trades: Trades executed this session
            portfolio_value: Current portfolio value
            unrealized_pl: Unrealized P&L
            positions: Number of open positions
            summary_type: "periodic" or "session_end"
            symbols_analyzed: Number of symbols analyzed

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not self.enable_telegram_notifications:
            return False

        if not self.telegram_chat_id:
            return False

        try:
            if summary_type == "session_end":
                title = "🔄 SESSION ENDED"
            else:
                title = "📊 BOT SUMMARY"

            # Get portfolio beta
            beta = self.get_portfolio_beta()
            beta_status = "⚠️" if beta > 1.5 else "✅"

            message = f"""
{title}

*Portfolio:* ${portfolio_value:,.2f}
*Unrealized P&L:* ${unrealized_pl:,.2f}
*Positions:* {positions}
*Symbols Analyzed:* {symbols_analyzed}
*Trades:* {total_trades}
*Win Rate:* {self.get_win_rate():.1f}%
*{beta_status} Beta:* {beta:.2f}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""

            # Send via Telegram

            bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
            if bot_token:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                data = {
                    'chat_id': self.telegram_chat_id,
                    'text': message,
                    'parse_mode': 'Markdown'
                }
                resp = requests.post(url, json=data, timeout=10)
                if resp.status_code == 200:
                    logging.info(f"📱 Summary notification sent")
                    return True

            return False

        except Exception as e:
            logging.debug(f"Summary notification error: {e}")
            return False

    def get_win_rate(self) -> float:
        """Get current win rate from trades"""
        try:
            if self.db.is_available():
                trades = self.db.get_all_trades()
                if trades:
                    winners = sum(1 for t in trades if t.get('pnl', 0) > 0)
                    return (winners / len(trades)) * 100
        except:
            pass
        return 0.0

    def check_daily_loss_limit(self) -> tuple:
        """
        Check if daily loss limit has been exceeded.

        Returns:
            (should_pause: bool, daily_loss_pct: float, starting_value: float)
        """
        if not self.enable_daily_loss_limit:
            return False, 0.0, self.daily_starting_value or 0.0

        try:
            from datetime import datetime, date
            current_date = date.today()
            current_value = self.get_portfolio_total_value()

            # Reset daily tracking if it's a new day
            if self.last_reset_date != current_date:
                self.daily_starting_value = current_value
                self.last_reset_date = current_date
                self.daily_loss_paused = False
                logging.info(f"📅 New day reset - Starting Value: ${current_value:,.2f}")
                return False, 0.0, current_value

            # If we don't have a starting value, set it
            if self.daily_starting_value is None or self.daily_starting_value == 0:
                self.daily_starting_value = current_value
                return False, 0.0, current_value

            # Calculate daily loss/gain percentage
            daily_change = (current_value - self.daily_starting_value) / self.daily_starting_value * 100

            # Only care about losses (negative changes)
            if daily_change < 0:
                daily_loss = abs(daily_change)
                self.daily_loss_pct = daily_loss

                if daily_loss >= self.daily_loss_limit_pct:
                    self.daily_loss_paused = True
                    logging.warning(f"🛑 DAILY LOSS LIMIT TRIGGERED: {daily_loss:.1f}% (max: {self.daily_loss_limit_pct}%)")
                    logging.warning(f"   Starting: ${self.daily_starting_value:,.2f} | Current: ${current_value:,.2f}")
                    logging.warning(f"   ⚠️ TRADING PAUSED FOR TODAY - Resume tomorrow")
                    return True, daily_loss, self.daily_starting_value

                return False, daily_loss, self.daily_starting_value
            else:
                # We're in profit today - reset loss tracking
                self.daily_loss_pct = 0
                return False, 0.0, self.daily_starting_value

        except Exception as e:
            logging.debug(f"Error checking daily loss limit: {e}")
            return False, self.daily_loss_pct, self.daily_starting_value or 0.0

    def calculate_position_size(self, symbol: str, signal_strength: str, price: float, portfolio_value: float) -> int:
        """
        Calculate position size using ATR-based volatility sizing.

        ATR-based sizing normalizes risk across different stocks:
        - High volatility stocks get smaller positions
        - Low volatility stocks get larger positions
        - All positions risk the same percentage of portfolio
        """

        # Get ATR for volatility calculation
        atr_pct = None
        try:
            df = self.get_market_data(symbol)
            if df is not None and len(df) >= 14:
                df = self.calculate_indicators(df)
                atr_pct = df['ATR_pct'].iloc[-1]
        except Exception as e:
            logging.debug(f"Could not get ATR for {symbol}: {e}")

        if self.enable_atr_sizing and atr_pct and atr_pct > 0:
            # ATR-based sizing: risk same % on each trade regardless of volatility
            # Position size = (Risk Amount / ATR%) * (1 / Price)
            risk_amount = portfolio_value * self.risk_per_trade

            # Calculate quantity based on ATR risk
            # If stock moves ATR% against us, we lose risk_per_trade %
            quantity = int(risk_amount / (price * (atr_pct / 100)))

            logging.debug(f"📊 {symbol} ATR: {atr_pct:.2f}% → Quantity: {quantity} (risk: {self.risk_per_trade*100:.1f}%)")
        else:
            # Fallback: use signal strength allocation if ATR unavailable
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
            logging.debug(f"📊 {symbol} Fallback sizing → Quantity: {quantity} (allocation: {base_allocation*100:.1f}%)")

        # Minimum and maximum constraints
        min_quantity = 1
        max_quantity = int(portfolio_value * self.max_position_pct / price)  # Max X% of portfolio
        quantity = max(min_quantity, min(quantity, max_quantity))

        # VIX volatility regime: reduce position size in HIGH / EXTREME vol environments
        try:
            vix_info = self.get_vix_regime()
            if vix_info.get('should_reduce_sizing'):
                vix_mult = vix_info.get('size_multiplier', 1.0)
                quantity = max(min_quantity, int(quantity * vix_mult))
                logging.debug(
                    f"VIX={vix_info['vix_level']} ({vix_info['regime']}): "
                    f"Reduced {symbol} size by {(1 - vix_mult) * 100:.0f}%"
                )
        except Exception:
            pass

        # Apply per-symbol size multiplier from adaptive learning
        if hasattr(self, '_symbol_size_multipliers') and symbol in self._symbol_size_multipliers:
            multiplier = self._symbol_size_multipliers[symbol]
            quantity = max(min_quantity, int(quantity * multiplier))
            logging.debug(f"Applied learned size multiplier {multiplier:.2f}x for {symbol}")

        return quantity

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
            return datetime.utcnow() - last_trade_time.replace(tzinfo=None) < cooldown_period

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
            return datetime.utcnow() - last_analysis_time.replace(tzinfo=None) < cooldown_period

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
            return datetime.utcnow() - last_research_time.replace(tzinfo=None) < cooldown_period

        # Fallback to in-memory
        if symbol not in self.research_times:
            return False

        last_research_time = self.research_times[symbol]
        cooldown_period = timedelta(minutes=cooldown_minutes)

        return datetime.now() - last_research_time < cooldown_period

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

    def get_recently_researched_tickers(self, cooldown_minutes: int = 15) -> List[str]:
        """Get list of tickers researched within the cooldown period (from database if available)"""
        recently_researched = []
        cutoff_time = datetime.utcnow() - timedelta(minutes=cooldown_minutes)

        # Try to get from database first
        if self.db.is_available():
            try:
                headers = {
                    "apikey": self.db.api_key,
                    "Authorization": f"Bearer {self.db.api_key}",
                    "Content-Type": "application/json"
                }

                # Query all research cooldowns within the time window
                cutoff_iso = cutoff_time.isoformat()
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
                    return recently_researched
                else:
                    print(f"   ⚠️  Database query failed with status {response.status_code}, using memory fallback")
                    logging.debug(f"Failed to query research cooldowns from database: {response.status_code}")
            except Exception as e:
                print(f"   ⚠️  Database error: {str(e)[:50]}, using memory fallback")
                logging.debug(f"Error querying research cooldowns from database: {e}")
        else:
            print(f"   i️  Database not available, checking in-memory cooldowns only")

        # Fallback to in-memory if database unavailable
        if not hasattr(self, 'research_times'):
            return []

        cutoff_time_local = datetime.now() - timedelta(minutes=cooldown_minutes)
        for ticker, research_time in self.research_times.items():
            if research_time > cutoff_time_local:
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
        from datetime import datetime
        research_time = datetime.utcnow()  # Use UTC to match database timestamps

        logging.debug(f"   🔖 Marking {symbol} as researched ({research_time.strftime('%H:%M:%S')} UTC)")

        # Store in database if available
        if self.db.is_available():
            success = self.db.set_research_cooldown(symbol, research_time)
            if success:
                logging.debug(f"✅ Stored research cooldown for {symbol} in database")
            else:
                print(f"   ⚠️  Failed to store {symbol} cooldown in database")
                logging.debug(f"⚠️ Failed to store research cooldown for {symbol}, using memory")
                # Fallback to memory
                self.research_times[symbol] = research_time
        else:
            # Use in-memory storage
            self.research_times[symbol] = research_time

        # Periodically clean up old cooldowns (every 100 research entries)
        if len(self.research_times) % 100 == 0:
            if self.db.is_available():
                # Clean up database entries older than 7 days
                self.db.cleanup_old_cooldowns(days_old=7)
            else:
                # Clean up in-memory entries older than 24 hours
                cutoff_time = datetime.now() - timedelta(hours=24)
                old_entries = [k for k, v in self.research_times.items() if v < cutoff_time]
                for old_symbol in old_entries:
                    del self.research_times[old_symbol]
                if old_entries:
                    logging.info(f"🧹 Cleaned up {len(old_entries)} old research entries (>24h)")

    def _get_fresh_ticker_list(self, target_count: int = 30) -> List[str]:
        """Generate a fresh list of tickers excluding cooldown, orders, and portfolio positions"""
        try:
            # Get all available symbols
            all_symbols = self.get_all_us_symbols()
            if not all_symbols:
                logging.error("❌ No symbols available from API")
                return []

            # Get exclusion lists
            recently_researched = self.get_recently_researched_tickers(cooldown_minutes=240)

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

    def get_sector_allocation(self, use_cache: bool = True) -> dict:
        """
        Calculate current sector allocation percentages.
        Uses only hardcoded map for performance - doesn't do Yahoo lookups.

        Args:
            use_cache: If True, uses cached allocation if available (for performance)

        Returns:
            dict of {sector_etf: percentage_of_portfolio}
        """
        # Use cached allocation if available
        if use_cache and hasattr(self, '_sector_allocation_cache') and self._sector_allocation_cache is not None:
            return self._sector_allocation_cache

        try:
            positions = self.trading_client.get_all_positions()
            portfolio_value = self.get_portfolio_total_value()

            if portfolio_value <= 0:
                return {}

            sector_values = {}

            for position in positions:
                symbol = position.symbol
                market_value = abs(float(position.market_value))

                # Use ONLY hardcoded map for allocation (fast, no API calls)
                sector = self.stock_sector_map.get(symbol)
                if sector:
                    sector_values[sector] = sector_values.get(sector, 0) + market_value

            # Cache the result
            self._sector_allocation_cache = {sector: value / portfolio_value for sector, value in sector_values.items()}
            return self._sector_allocation_cache

        except Exception as e:
            logging.debug(f"Error calculating sector allocation: {e}")
            return {}

    def get_sector_for_symbol(self, symbol: str) -> str:
        """
        Get sector ETF for a symbol, using hardcoded map first, then Yahoo Finance.

        Returns:
            Sector ETF code (XLK, XLF, etc.) or None if unavailable
        """
        # First check hardcoded map
        sector = self.stock_sector_map.get(symbol)
        if sector:
            return sector

        # Check cache
        if symbol in self._sector_cache:
            return self._sector_cache[symbol]

        # Try Yahoo Finance for unmapped stocks (with timeout)
        try:

            # Create a timer to timeout the Yahoo lookup
            result = {'sector': None}

            def lookup():
                try:
                    ticker = yf.Ticker(symbol)
                    info = ticker.info
                    result['sector'] = info.get('sector')
                except:
                    pass

            t = Timer(3.0, lookup)  # 3 second timeout
            t.start()
            t.join()

            yahoo_sector = result['sector']

            if yahoo_sector:
                # Map Yahoo sector name to our ETF
                sector = self.yahoo_sector_to_etf.get(yahoo_sector)
                if sector:
                    self._sector_cache[symbol] = sector
                    logging.debug(f"📊 {symbol}: Mapped via Yahoo Finance -> {yahoo_sector} -> {sector}")
                    return sector

        except Exception as e:
            logging.debug(f"Failed to get sector from Yahoo Finance for {symbol}: {e}")

        # Cache the negative result too
        self._sector_cache[symbol] = None
        return None

    def invalidate_sector_cache(self):
        """Clear sector allocation cache after a trade."""
        self._sector_allocation_cache = None

    def check_sector_concentration(self, symbol: str, position_size_dollars: float) -> tuple:
        """
        Check if adding this position would exceed sector concentration limit.

        Args:
            symbol: Stock symbol to check
            position_size_dollars: Dollar value of new position

        Returns:
            (passes: bool, current_pct: float, projected_pct: float, reason: str)
        """
        try:
            portfolio_value = self.get_portfolio_total_value()
            if portfolio_value <= 0:
                return (True, 0, 0, "No portfolio value")

            # Get sector for this symbol (uses hardcoded map first, then Yahoo Finance)
            sector = self.get_sector_for_symbol(symbol)
            if not sector:
                return (True, 0, 0, "Unknown sector - allowing")

            # Get current sector allocation
            sector_alloc = self.get_sector_allocation()
            current_pct = sector_alloc.get(sector, 0)

            # Calculate projected allocation
            projected_value = position_size_dollars
            projected_pct = (projected_value / portfolio_value) + current_pct

            # Check against limit
            if projected_pct > self.max_sector_concentration:
                return (False, current_pct * 100, projected_pct * 100,
                        f"Sector {self.SECTOR_ETFS.get(sector, sector)} at {current_pct*100:.1f}% → would be {projected_pct*100:.1f}% (limit: {self.max_sector_concentration*100:.0f}%)")

            return (True, current_pct * 100, projected_pct * 100, "Passes concentration check")

        except Exception as e:
            logging.debug(f"Sector concentration check failed: {e}")
            return (True, 0, 0, "Check failed - allowing")

    def check_correlation_risk(self, symbol: str) -> tuple:
        """
        Check correlation with existing positions.

        Returns:
            (passes: bool, max_correlation: float, correlated_symbols: list, reason: str)
        """
        try:
            # Get historical data for the candidate symbol
            df_candidate = self.get_market_data(symbol)
            if df_candidate is None or len(df_candidate) < 30:
                return (True, 0, [], "Insufficient data for correlation check")

            # Get recent returns
            candidate_returns = df_candidate['close'].pct_change().dropna().tail(30)

            # Get existing positions
            positions = self.trading_client.get_all_positions()

            max_corr = 0
            correlated = []

            for position in positions:
                pos_symbol = position.symbol
                if pos_symbol == symbol:
                    continue

                # Skip if too small
                if abs(float(position.market_value)) < 1000:
                    continue

                # Get historical data for existing position
                df_pos = self.get_market_data(pos_symbol)
                if df_pos is None or len(df_pos) < 30:
                    continue

                pos_returns = df_pos['close'].pct_change().dropna().tail(30)

                # Align the series
                if len(candidate_returns) != len(pos_returns):
                    continue

                # Calculate correlation
                corr = candidate_returns.corr(pos_returns)

                if pd.notna(corr) and abs(corr) > max_corr:
                    max_corr = abs(corr)
                    if max_corr > self.max_correlation:
                        correlated.append((pos_symbol, corr))

            if max_corr > self.max_correlation:
                corr_str = ", ".join([f"{s}({c:.2f})" for s, c in correlated[:3]])
                return (False, max_corr, correlated,
                        f"High correlation with {corr_str} (max: {max_corr:.2f} > {self.max_correlation})")

            return (True, max_corr, [], f"Correlation check passed (max: {max_corr:.2f})")

        except Exception as e:
            logging.debug(f"Correlation check failed: {e}")
            return (True, 0, [], "Check failed - allowing")

    def get_portfolio_beta(self) -> float:
        """
        Calculate portfolio beta relative to SPY.

        Returns:
            Portfolio beta (weighted by position size)
        """
        # Use cached value if available
        if self._portfolio_beta_cache is not None:
            return self._portfolio_beta_cache

        try:
            # Get SPY data
            df_spy = self.get_market_data('SPY')
            if df_spy is None or len(df_spy) < 30:
                return 1.0  # Default to beta 1 if no data

            spy_returns = df_spy['close'].pct_change().dropna().tail(30)

            # Get positions
            positions = self.trading_client.get_all_positions()
            portfolio_value = self.get_portfolio_total_value()

            if portfolio_value <= 0:
                return 1.0

            weighted_beta = 0

            for position in positions:
                symbol = position.symbol
                market_value = abs(float(position.market_value))
                weight = market_value / portfolio_value

                # Get stock data
                df_stock = self.get_market_data(symbol)
                if df_stock is None or len(df_stock) < 30:
                    continue

                stock_returns = df_stock['close'].pct_change().dropna().tail(30)

                # Align with SPY returns
                if len(stock_returns) != len(spy_returns):
                    continue

                # Calculate beta (covariance / variance)
                covariance = stock_returns.cov(spy_returns)
                spy_variance = spy_returns.var()

                if spy_variance > 0:
                    beta = covariance / spy_variance
                    if pd.notna(beta):
                        weighted_beta += weight * beta

            self._portfolio_beta_cache = weighted_beta
            return weighted_beta

        except Exception as e:
            logging.debug(f"Portfolio beta calculation failed: {e}")
            return 1.0

    def check_beta_exposure(self) -> tuple:
        """
        Check if portfolio beta exceeds limit.

        Returns:
            (passes: bool, current_beta: float, reason: str)
        """
        portfolio_beta = self.get_portfolio_beta()

        if portfolio_beta > self.max_portfolio_beta:
            return (False, portfolio_beta,
                    f"Portfolio beta {portfolio_beta:.2f} exceeds limit {self.max_portfolio_beta}")

        return (True, portfolio_beta, f"Portfolio beta {portfolio_beta:.2f} within limits")

    def get_regime_classifier(self):
        """Get or create the market regime classifier."""
        if self._regime_classifier is None:
            from src.analysis.market_regime import MarketRegimeClassifier
            self._regime_classifier = MarketRegimeClassifier(
                db=self.db,
                regime_symbol=self.regime_symbol,
                adx_period=self.adx_period,
                trend_threshold=self.trend_threshold,
                range_threshold=self.range_threshold
            )
        return self._regime_classifier

    def get_current_market_regime(self, force_refresh: bool = False) -> Dict:
        """
        Get current market regime.

        Returns:
            Dict with regime, adx, plus_di, minus_di, etc.
        """
        if self._forced_regime:
            # Return forced regime for testing
            classifier = self.get_regime_classifier()
            mods = classifier.get_strategy_modifiers(self._forced_regime)
            return {
                'regime': self._forced_regime,
                'adx': 0,
                'plus_di': 0,
                'minus_di': 0,
                'symbol': self.regime_symbol,
                'timestamp': datetime.now(timezone.utc),
                'modifiers': mods,
                'forced': True
            }

        if not self.enable_regime_filter:
            # Return neutral if disabled
            return {
                'regime': 'TRANSITIONING',
                'adx': 22,  # Middle value
                'plus_di': 0,
                'minus_di': 0,
                'symbol': self.regime_symbol,
                'timestamp': datetime.now(timezone.utc),
                'modifiers': {
                    'rsi_buy_threshold': 30,
                    'rsi_sell_threshold': 70,
                    'position_size_multiplier': 1.0,
                    'stop_loss_multiplier': 1.0
                }
            }

        classifier = self.get_regime_classifier()
        result = classifier.calculate_current_regime()
        result['modifiers'] = classifier.get_strategy_modifiers(result['regime'])
        self._current_regime = result
        return result

    def execute_trade(self, analysis: Dict) -> bool:
        """Execute trade based on analysis with comprehensive position and risk management"""
        try:
            symbol = analysis['symbol']
            signal = analysis['signal']
            price = analysis['price']
            signal_strength = analysis.get('signal_strength', 'WEAK')

            if not signal:
                return False

            # STRICT: Block all trades if using margin (cash negative)
            try:
                account = self.trading_client.get_account()
                cash = float(account.cash)
                if cash < 0:
                    logging.warning(f"🚫 MARGIN DETECTED - Cash: ${cash:.2f}. Blocking all trades until cash is positive.")
                    return False
            except Exception as e:
                logging.debug(f"Could not check account cash: {e}")

            # Get portfolio value for percentage-based calculations
            portfolio_value = self.get_portfolio_total_value()

            # Check for pending orders first
            if self.has_pending_orders(symbol):
                logging.info(f"🚫 Skipping {symbol}: Pending order exists")
                return False

            # Check cooldown period for BUY signals to prevent excessive repeat trades
            if signal == 'BUY' and self.is_in_cooldown(symbol, cooldown_minutes=240):
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

                # Check sector concentration limits (Phase 3.1)
                position_size_dollars = quantity * price
                passes_sector_check, current_pct, projected_pct, sector_reason = self.check_sector_concentration(
                    symbol, position_size_dollars
                )

                if not passes_sector_check:
                    logging.info(f"🚫 Skipping {symbol}: {sector_reason}")
                    # Log this for visibility
                    return False

                # Check correlation with existing positions (Phase 3.2)
                passes_corr, max_corr, correlated, corr_reason = self.check_correlation_risk(symbol)
                if not passes_corr:
                    logging.info(f"🚫 Skipping {symbol}: {corr_reason}")
                    return False

                # Check portfolio beta exposure (Phase 3.3)
                passes_beta, current_beta, beta_reason = self.check_beta_exposure()
                if not passes_beta:
                    # Log prominently - this is a risk management block
                    logging.warning(f"🛑 BLOCKED BY BETA RULE: {symbol} - {beta_reason}")
                    return False

                # Add position size context to logging
                if current_position_qty > 0:
                    new_total_qty = current_position_qty + quantity
                    logging.info(f"📊 {symbol} Position: Currently {current_position_qty} shares, adding {quantity} → {new_total_qty} total")

                # Position scaling: split into entry tranches and only buy Tranche 1 now
                if quantity >= 3:
                    try:
                        from analysis.position_scaling.position_scaling import calculate_entry_tranches
                        total_score = analysis.get('total_score', 75)
                        tranche_plan = calculate_entry_tranches(symbol, quantity, signal_strength=int(total_score))
                        tranche_1 = tranche_plan['tranches'][0]
                        quantity = tranche_1['qty']  # Only execute first tranche immediately
                        # Store remaining tranches for deferred execution
                        tranche_plan['entry_price'] = price
                        tranche_plan['created_at'] = datetime.now(timezone.utc)
                        # Mark tranche 1 as pending-fill (will be marked filled after order)
                        self._pending_entry_tranches[symbol] = tranche_plan
                        logging.info(
                            f"Tranche entry: buying {quantity}/{tranche_plan['total_qty']} shares now "
                            f"(remaining tranches deferred)"
                        )
                    except Exception as e:
                        logging.debug(f"Position scaling failed for {symbol}: {e}")

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
                    'signal_time': self._get_safe_timestamp(analysis.get('timestamp')),
                    'order_time': datetime.now(timezone.utc).isoformat(),
                    'sma_fast': analysis['sma_fast'],
                    'sma_slow': analysis['sma_slow'],
                    'rsi': analysis['rsi'],
                    'signal_strength': analysis['signal_strength'],
                    'status': 'SUBMITTED'
                }
                self.db.log_trade(self.session_id, trade_data)

            # Mark tranche 1 as filled now that the order is submitted
            if signal == 'BUY' and symbol in self._pending_entry_tranches:
                plan = self._pending_entry_tranches[symbol]
                tranches = plan.get('tranches', [])
                if tranches:
                    tranches[0]['status'] = 'filled'
                    tranches[0]['filled_price'] = price

            # Send email notification
            self.send_trade_notification(
                trade_type=signal,
                symbol=symbol,
                quantity=quantity,
                price=price,
                side=side.name,
                analysis=analysis
            )

            self.trades_executed += 1

            # Invalidate sector allocation cache after trade
            self.invalidate_sector_cache()
            self._portfolio_beta_cache = None  # Invalidate beta cache after trade

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
                print(f"   • BULLISH: Price (${price:.2f}) above short-term average (${analysis['sma_fast']:.2f})")
                print(f"   • RSI ({analysis['rsi']:.1f}): {'OVERSOLD - Good entry point' if analysis['rsi'] < 30 else 'NEUTRAL - Momentum building' if analysis['rsi'] < 70 else 'STRONG MOMENTUM'}")

                # Explain position sizing logic
                allocation_map = {'AI_ENHANCED': '2.5%', 'STRONG': '2.0%', 'MEDIUM': '1.5%', 'WEAK': '1.0%', 'CONFLICTED': '0.5%'}
                target_allocation = allocation_map.get(signal_strength, '1.0%')
                print(f"   • SIZING: {signal_strength} signal → {target_allocation} portfolio allocation")

                if self.get_current_position_size(symbol) > 0:
                    print(f"   • POSITION: Adding to existing position (concentration managed)")
            else:
                print(f"   • BEARISH: Price (${price:.2f}) below short-term average (${analysis['sma_fast']:.2f})")
                print(f"   • RSI ({analysis['rsi']:.1f}): {'OVERBOUGHT - Good exit point' if analysis['rsi'] > 70 else 'NEUTRAL - Weakness showing' if analysis['rsi'] > 30 else 'OVERSOLD'}")
            print(f"\n💼 Order ID: {order.id}")
            print("✅" + "="*58 + "✅")

            # Also log for database
            logging.info(f"✅ TRADE: {signal} {quantity} {symbol} @ ${price:.2f} (${price * quantity:,.2f}) - Order: {order.id}")

            return True

        except Exception as e:
            logging.error(f"❌ Trade failed for {analysis['symbol']}: {e}")
            self.errors_count += 1
            return False

    def _execute_pending_entry_tranches(self):
        """
        Check pending entry tranches (tranches 2 and 3) created by execute_trade()
        and execute any whose triggers are now satisfied.

        Called once per continuous loop iteration so it doesn't block the main
        analysis path.
        """
        if not self._pending_entry_tranches:
            return

        from analysis.position_scaling.position_scaling import check_tranche_triggers

        completed_symbols = []

        for symbol, plan in list(self._pending_entry_tranches.items()):
            try:
                df = self.get_market_data(symbol)
                if df is None:
                    continue

                df = self.calculate_indicators(df)
                latest = df.iloc[-1]

                current_price = float(latest['close'])
                current_rsi = float(latest.get('RSI', 50))
                vwap = float(latest.get('vwap', current_price))
                vol_ratio = float(latest.get('volume_ratio', 1.0))

                entry_price = plan.get('entry_price', current_price)
                created_at = plan.get('created_at')
                if created_at:
                    hours_since = (
                        datetime.now(timezone.utc) - created_at
                    ).total_seconds() / 3600
                else:
                    hours_since = 0

                current_data = {
                    'price': current_price,
                    'vwap': vwap,
                    'rsi': current_rsi,
                    'volume_ratio': vol_ratio,
                    'hours_since_entry': hours_since,
                    'entry_price': entry_price,
                }

                to_execute = check_tranche_triggers(plan, current_data)

                for tranche in to_execute:
                    qty = tranche.get('qty', 0)
                    if qty < 1:
                        continue
                    try:
                        order = self.trading_client.submit_order(
                            order_data=MarketOrderRequest(
                                symbol=symbol,
                                qty=qty,
                                side=OrderSide.BUY,
                                time_in_force=TimeInForce.DAY,
                            )
                        )
                        tranche['status'] = 'filled'
                        logging.info(
                            f"Tranche {tranche['tranche_num']} executed: "
                            f"BUY {qty} {symbol} @ ~${current_price:.2f} "
                            f"({tranche['trigger']})"
                        )
                    except Exception as e:
                        logging.debug(f"Tranche order failed for {symbol}: {e}")

                # Remove plan if all tranches are done (filled or skipped)
                if all(t['status'] != 'pending' for t in plan.get('tranches', [])):
                    completed_symbols.append(symbol)

            except Exception as e:
                logging.debug(f"Tranche check failed for {symbol}: {e}")

        for sym in completed_symbols:
            self._pending_entry_tranches.pop(sym, None)

    def run_analysis(self, max_symbols: int = 50, max_trades: int = 3, use_ai: bool = False):
        """Run trading analysis with optional AI enhancement"""
        # Clear market data cache for fresh analysis cycle
        self._market_data_cache.clear()
        self._cycle_start_time = datetime.now(timezone.utc)

        # Log market regime at start of analysis
        if self.enable_regime_filter:
            try:
                regime = self.get_current_market_regime()
                logging.info(f"📊 Market Regime: {regime.get('regime', 'UNKNOWN')} (ADX: {regime.get('adx', 0):.1f})")
            except Exception as e:
                logging.debug(f"Could not get regime: {e}")

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

        # Track reasons for no trades
        no_trade_reasons = {
            'no_signal': 0,  # No buy/sell signal detected
            'weak_signal': 0,  # Signal too weak
            'conflicted_signal': 0,  # AI conflicts with technical analysis
            'no_data': 0,  # No market data available
            'max_trades_reached': 0,  # Already hit max trades
            'drawdown_protection': 0,  # Paused due to max drawdown
        }

        # Check max drawdown protection BEFORE trading
        if self.enable_drawdown_protection:
            should_pause, current_drawdown, peak_value = self.check_drawdown_protection()
            if should_pause:
                logging.warning(f"🛑 TRADING PAUSED: Max drawdown {current_drawdown:.1f}% exceeded (max: {self.max_drawdown_pct}%)")
                logging.warning(f"   Peak: ${peak_value:,.2f} | Current: ${self.get_portfolio_total_value():,.2f}")
                print(f"\n🛑 MAX DRAWDOWN TRIGGERED - TRADING PAUSED")
                print(f"   Current Drawdown: {current_drawdown:.1f}%")
                print(f"   Max Allowed: {self.max_drawdown_pct}%")
                print(f"   Peak Portfolio: ${peak_value:,.2f}")
                print(f"   Current Portfolio: ${self.get_portfolio_total_value():,.2f}")
                print(f"\n   ⚠️ Trading will remain paused until portfolio recovers to new peak")
                no_trade_reasons['drawdown_protection'] = 1
                # Return early - no trading allowed
                return {
                    'trades_executed': 0,
                    'opportunities': 0,
                    'reasons': no_trade_reasons,
                    'paused': True,
                    'drawdown': current_drawdown
                }
            elif current_drawdown > 0:
                logging.info(f"📉 Current Drawdown: {current_drawdown:.1f}% (Peak: ${peak_value:,.2f})")

        # Check daily loss limit BEFORE trading
        if self.enable_daily_loss_limit:
            should_pause_daily, daily_loss, starting_value = self.check_daily_loss_limit()
            if should_pause_daily:
                logging.warning(f"🛑 TRADING PAUSED: Daily loss {daily_loss:.1f}% exceeded (max: {self.daily_loss_limit_pct}%)")
                logging.warning(f"   Starting: ${starting_value:,.2f} | Current: ${self.get_portfolio_total_value():,.2f}")
                print(f"\n🛑 DAILY LOSS LIMIT TRIGGERED - TRADING PAUSED FOR TODAY")
                print(f"   Daily Loss: {daily_loss:.1f}%")
                print(f"   Max Allowed: {self.daily_loss_limit_pct}%")
                print(f"   Starting Value: ${starting_value:,.2f}")
                print(f"   Current Value: ${self.get_portfolio_total_value():,.2f}")
                print(f"\n   ⚠️ Trading will resume tomorrow")
                no_trade_reasons['drawdown_protection'] = 1
                return {
                    'trades_executed': 0,
                    'opportunities': 0,
                    'reasons': no_trade_reasons,
                    'paused': True,
                    'daily_loss': daily_loss
                }
            elif daily_loss > 0:
                logging.info(f"📊 Daily Loss: {daily_loss:.1f}% (Starting: ${starting_value:,.2f})")

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

        # Position Rotation: Check if we should sell weak positions to buy better opportunities
        if self.enable_rotation and remaining_trades > 0:
            try:
                # First, collect some buy candidates to compare
                temp_candidates = []
                symbols = self._get_rolling_ticker_list(20)  # Quick scan of 20 symbols

                for symbol in symbols[:20]:
                    if self.has_pending_orders(symbol):
                        continue
                    if self.is_in_research_cooldown(symbol, cooldown_minutes=240):
                        continue

                    analysis = self.analyze_symbol(symbol, use_ai=False)
                    if analysis and analysis.get('signal') == 'BUY' and analysis.get('total_score', 0) >= 60:
                        temp_candidates.append(analysis)

                # Evaluate rotation
                if temp_candidates:
                    rotation_sells = self.evaluate_rotation(temp_candidates)

                    if rotation_sells:
                        rotation = rotation_sells[0]
                        logging.info(f"\n🔄 ROTATION TRIGGERED: Selling {rotation['symbol']} → Buying {rotation['replace_with']}")

                        # Execute the sell
                        sell_analysis = {
                            'symbol': rotation['symbol'],
                            'signal': 'SELL',
                            'signal_strength': 'MEDIUM',
                            'price': 0,
                            'rsi': 50,
                            'sma_fast': 0,
                            'sma_slow': 0,
                            'ai_insight': rotation['reason'],
                            'position_sell_qty': rotation['sell_qty']
                        }

                        if self.execute_trade(sell_analysis):
                            trades_executed += 1
                            remaining_trades -= 1
                            logging.info(f"   ✅ Rotation sell executed")

                            # Now execute the buy
                            buy_analysis = None
                            for c in temp_candidates:
                                if c.get('symbol') == rotation['replace_with']:
                                    buy_analysis = c
                                    break

                            if buy_analysis and remaining_trades > 0:
                                if self.execute_trade(buy_analysis):
                                    trades_executed += 1
                                    logging.info(f"   ✅ Rotation buy executed")
                                remaining_trades -= 1

                            time.sleep(1)
            except Exception as e:
                logging.debug(f"Rotation evaluation failed: {e}")

        logging.info(f"\n🔍 Looking for new opportunities ({remaining_trades} trades remaining)...")
        max_trades = remaining_trades  # Update for the buy loop

        # Get tickers - use rolling list of all US symbols (no AI)
        # Filter out: portfolio positions, pending orders, and recently researched (60-min cooldown)
        logging.info(f"🔍 Ticker selection: ai_enabled={ai_enabled}, is_configured={self.ai.is_configured}, use_ai_sel={self.use_ai_for_ticker_selection}")
        if ai_enabled and self.ai.is_configured and self.use_ai_for_ticker_selection:
            # AI-based selection (rarely used now)
            logging.info("🧠 Getting AI-recommended tickers based on portfolio analysis...")
            try:
                symbols = self.get_ai_recommended_tickers(portfolio_analysis)
            except Exception as e:
                logging.warning(f"⚠️ AI ticker recommendation failed: {e}")
                symbols = self._get_rolling_ticker_list(max_symbols)
        else:
            # Rolling ticker list - just get all symbols not in cooldown
            symbols = self._get_rolling_ticker_list(max_symbols)

        if not symbols:
            logging.error("❌ No symbols available")
            return

        # Create AI market summary if enabled
        if ai_enabled and self.ai.is_configured and self.use_ai_for_market_summary:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                market_summary = loop.run_until_complete(self.ai.create_market_summary(symbols[:5]))
                loop.close()

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

        for i, symbol in enumerate(symbols, 1):
            try:
                logging.info(f"📊 Analyzing {symbol}")
                self.symbols_processed += 1

                if i % 10 == 0:
                    skipped_total = symbols_skipped_orders + symbols_skipped_cooldown
                    logging.info(f"📊 Progress: {i}/{len(symbols)} symbols ({skipped_total} skipped), {opportunities} opportunities, {trades_executed} trades")

                # Skip research if there are pending orders for this symbol
                if self.has_pending_orders(symbol):
                    symbols_skipped_orders += 1
                    logging.debug(f"⏭️  {symbol}: Skipping research - pending order exists")
                    continue

                # Sequential analysis - no cooldown needed
                # Use multi-timeframe analysis if enabled
                if self.enable_multi_timeframe:
                    analysis = self.analyze_multi_timeframe(symbol, use_ai=ai_enabled)
                else:
                    analysis = self.analyze_symbol(symbol, use_ai=ai_enabled)

                # Save analysis result
                if analysis:
                    self.save_analysis_to_db(symbol, analysis)

                # Apply liquidity filter - skip illiquid stocks for BUY signals
                if analysis and analysis.get('signal') == 'BUY' and self.enable_liquidity_filter:
                    passes_liquidity, avg_vol, spread, reason = self.check_liquidity(symbol, self.min_daily_volume)
                    if not passes_liquidity:
                        analysis['signal'] = 'HOLD'
                        analysis['signal_strength'] = 'WEAK'
                        analysis['liquidity_warning'] = reason
                        logging.debug(f"⏭️ {symbol}: Failed liquidity filter - {reason}")

                # Apply sector rotation filter - prefer stocks in strong sectors
                if analysis and analysis.get('signal') == 'BUY' and self.enable_sector_filter:
                    # Check if this is a sector ETF we're analyzing
                    sector = self.get_sector_for_symbol(symbol)
                    if sector and sector in self.sector_rotation_scores:
                        sector_score = self.sector_rotation_scores.get(sector, 0)
                        # If sector is underperforming SPY by > 2%, downgrade signal
                        if sector_score < -2:
                            analysis['signal'] = 'HOLD'
                            analysis['signal_strength'] = 'WEAK'
                            analysis['sector_warning'] = f"Sector {self.SECTOR_ETFS.get(sector, sector)} underperforming ({sector_score:+.1f}% vs SPY)"
                            logging.debug(f"⏭️ {symbol}: Sector {sector} underperforming - {sector_score:+.1f}%")
                if not analysis:
                    # Check why - no data vs no signal
                    df = self.get_market_data(symbol)
                    if df is None or len(df) < self.sma_slow:
                        logging.debug(f"   ⏭️  {symbol}: ❌ No market data (needs {self.sma_slow} bars)")
                        no_trade_reasons['no_data'] += 1
                    else:
                        # Has data but no signal (RSI not in buy/sell zone)
                        df = self.calculate_indicators(df)
                        latest = df.iloc[-1]
                        rsi = latest['RSI']
                        sma_fast = latest[f'SMA_{self.sma_fast}']
                        sma_slow = latest[f'SMA_{self.sma_slow}']
                        macd = latest.get('MACD', 0)
                        macd_sig = latest.get('MACD_signal', 0)
                        if pd.isna(macd): macd = 0
                        if pd.isna(macd_sig): macd_sig = 0

                        # Get other indicators for one-line summary
                        bb_upper = latest.get('BB_upper', 0)
                        bb_lower = latest.get('BB_lower', 0)
                        vwap_dist = latest.get('vwap_distance', 0)
                        if pd.isna(vwap_dist): vwap_dist = 0
                        price = latest.get('close', 0)

                        # Calculate BB position
                        if bb_upper and bb_lower and price > 0:
                            bb_pos = ((price - bb_lower) / (bb_upper - bb_lower)) * 100
                        else:
                            bb_pos = 50

                        # Handle missing RSI
                        if pd.isna(rsi):
                            rsi = 50  # Neutral

                        # Calculate signal score from scratch (0-100, 50=neutral)
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
                        if sma_fast > sma_slow:
                            sma_pct = ((sma_fast - sma_slow) / sma_slow) * 100
                            sma_score = min(25, sma_pct * 5)
                        elif sma_fast < sma_slow:
                            sma_pct = ((sma_slow - sma_fast) / sma_slow) * 100
                            sma_score = -min(25, sma_pct * 5)

                        macd_hist_val = latest.get('MACD_histogram', 0)
                        if pd.notna(macd_hist_val) and price > 0:
                            macd_score = max(-25, min(25, (macd_hist_val / price) * 5000))
                        else:
                            macd_score = 0

                        bb_score = 25 - (bb_pos / 2)  # Lower band = bullish (+25), upper = bearish (-25)

                        # Calculate regime_score based on market regime
                        regime_score = 0
                        try:
                            if hasattr(self, '_current_regime'):
                                regime = self._current_regime.get('regime', 'UNKNOWN')
                                adx = self._current_regime.get('adx', 0)
                                if regime == 'TRENDING_BULLISH':
                                    regime_score = 10  # Boost in bullish trend
                                elif regime == 'TRENDING_BEARISH':
                                    regime_score = -10  # Penalty in bearish trend
                                elif regime == 'RANGING':
                                    regime_score = 0
                                # Strong trend (ADX > 25) modifies score
                                if adx > 25:
                                    regime_score = regime_score * (adx / 25)
                        except:
                            pass

                        # Catalyst score - check for gap ups and momentum
                        catalyst_score = 0
                        try:
                            if len(df) >= 2:
                                prev_close = df.iloc[-2]['close']
                                gap = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                                if gap > 5:
                                    catalyst_score = 15  # Strong gap up
                                elif gap > 2:
                                    catalyst_score = 8   # Moderate gap up
                                # Volume surge check
                                if len(df) >= 20:
                                    avg_vol = df['volume'].tail(20).mean()
                                    curr_vol = df.iloc[-1]['volume']
                                    if curr_vol > avg_vol * 2:
                                        catalyst_score += 10
                        except:
                            pass

                        # Calculate total score with regime and catalyst
                        total = rsi_score + sma_score + macd_score + bb_score + regime_score + catalyst_score

                        # Build buy_criteria for this analysis
                        vol_ratio = latest.get('volume_ratio', None)
                        macd_hist = latest.get('MACD_histogram', 0)
                        buy_criteria = [
                            {
                                'name': 'Score ≥ 65',
                                'passed': bool(total >= 65),
                                'detail': f'{total:.0f}/100',
                            },
                            {
                                'name': 'RSI not overbought',
                                'passed': bool(rsi < 70),
                                'detail': f'{rsi:.1f}',
                            },
                            {
                                'name': 'SMA uptrend',
                                'passed': bool(sma_fast > sma_slow),
                                'detail': 'uptrend' if sma_fast > sma_slow else 'downtrend',
                            },
                            {
                                'name': 'MACD positive',
                                'passed': bool(macd_hist is not None and macd_hist > 0),
                                'detail': f'{macd_hist:.3f}' if macd_hist is not None else 'N/A',
                            },
                        ]
                        if vol_ratio is not None:
                            buy_criteria.append({
                                'name': 'Volume ≥ avg',
                                'passed': bool(float(vol_ratio) >= 1.0),
                                'detail': f'{float(vol_ratio):.2f}x',
                            })

                        failed_criteria = [c['name'] for c in buy_criteria if not c['passed']]
                        passes_all = len(failed_criteria) == 0

                        # One-line summary: Symbol | Price | RSI | MACD | BB% | VWAP% | Score
                        logging.info(f"   📊 {symbol}: ${price:.2f} | RSI:{rsi:.0f} | MACD:{macd:+.2f} | BB:{bb_pos:.0f}% | VWAP:{vwap_dist:+.1f}% | Score:{total:.0f}/100")
                        self.db.save_analysis_result(symbol, {
                            'price': price, 'total_score': int(total), 'signal': 'HOLD',
                            'rsi': rsi, 'rsi_score': rsi_score, 'sma_score': sma_score,
                            'macd_score': macd_score, 'bb_score': bb_score,
                            'vwap_score': vwap_dist, 'regime_score': regime_score,
                            'catalyst_score': catalyst_score,
                            'buy_criteria': buy_criteria,
                            'passes_all_buy_criteria': passes_all,
                        })
                        no_trade_reasons['no_signal'] += 1
                    continue

                if analysis and analysis['signal'] in ['BUY', 'SELL']:
                    opportunities += 1

                    # Check if we should skip due to weak signals
                    if analysis['signal_strength'] == "WEAK":
                        no_trade_reasons['weak_signal'] += 1
                        score = analysis.get('total_score', 50)
                        rsi = analysis.get('rsi', 50)
                        macd = analysis.get('macd', 0)
                        price = analysis.get('price', 0)
                        bb_pos = 50
                        vwap_dist = analysis.get('vwap_distance', 0)
                        if pd.isna(vwap_dist): vwap_dist = 0
                        logging.info(f"   📊 {symbol}: ${price:.2f} | RSI:{rsi:.0f} | MACD:{macd:+.2f} | BB:{bb_pos:.0f}% | VWAP:{vwap_dist:+.1f}% | Score:{score:.0f}/100 | ⚠️ WEAK")
                        continue
                    elif analysis['signal_strength'] == "CONFLICTED":
                        no_trade_reasons['conflicted_signal'] += 1
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

                    # Log new Phase 2 metrics
                    vol_tier = analysis.get('volatility_tier', 'mid')
                    atr = analysis.get('atr_pct', 0)
                    cat_score = analysis.get('catalyst_score', 0)
                    catalysts = analysis.get('catalysts', [])
                    cat_str = f" ({', '.join(catalysts)})" if catalysts else ""
                    logging.info(f"   🎯 Vol: {vol_tier} (ATR: {atr:.1f}%) | 💥 Catalyst: +{cat_score}{cat_str}")

                    # Log liquidity/sector warnings if present
                    if analysis.get('liquidity_warning'):
                        logging.info(f"   💧 {analysis['liquidity_warning']}")
                    if analysis.get('sector_warning'):
                        logging.info(f"   🏛️  {analysis['sector_warning']}")

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

                elif analysis:
                    # Has analysis but no actionable signal (HOLD)
                    score = analysis.get('total_score', 50)
                    rsi = analysis.get('rsi', 50)
                    macd = analysis.get('macd', 0)
                    price = analysis.get('price', 0)
                    bb_pos = 50
                    vwap_dist = analysis.get('vwap_distance', 0)
                    if pd.isna(vwap_dist): vwap_dist = 0

                    # Add new Phase 2 metrics to log
                    vol_tier = analysis.get('volatility_tier', 'mid')
                    atr = analysis.get('atr_pct', 0)
                    cat_score = analysis.get('catalyst_score', 0)
                    logging.info(f"   📊 {symbol}: ${price:.2f} | RSI:{rsi:.0f} | MACD:{macd:+.2f} | BB:{bb_pos:.0f}% | VWAP:{vwap_dist:+.1f}% | Score:{score:.0f}/100 | Vol:{vol_tier}({atr:.1f}%) Cat:+{cat_score}")
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

                    # Add multi-timeframe info if enabled
                    mt_msg = ""
                    if analysis.get('multi_timeframe') and analysis.get('hourly_signal'):
                        mt_msg = f" (Hourly: {analysis['hourly_signal']}, RSI: {analysis.get('hourly_rsi', 'N/A'):.1f})"
                    elif analysis.get('multi_timeframe') and not analysis.get('hourly_signal'):
                        mt_msg = " (Hourly: No signal)"

                    print(f"   ⏭️  {symbol}: ⊗ {failure_msg}{mt_msg}")

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

            # Log portfolio beta (Phase 3.3)
            portfolio_beta = self.get_portfolio_beta()
            if portfolio_beta > self.max_portfolio_beta:
                logging.warning(f"⚠️  Portfolio beta {portfolio_beta:.2f} exceeds limit {self.max_portfolio_beta} - BUY trades will be blocked!")
            else:
                logging.info(f"📈 Portfolio beta: {portfolio_beta:.2f} (limit: {self.max_portfolio_beta})")

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
            print(f"📈 Unrealized P&L: ${portfolio_analysis['total_unrealized_pnl']:,.2f}")
            print(f"🎯 Win Rate: {portfolio_analysis['win_rate']:.1f}% ({portfolio_analysis['winners']} winners, {portfolio_analysis['losers']} losers)")
            print(f"⚠️  Concentration Risk: {portfolio_analysis['concentration_risk']:.1f}% in top 5 positions")

            # Also log
            logging.info(f"💰 Total Portfolio Value: ${portfolio_analysis['total_value']:,.2f}")
            logging.info(f"💵 Cash Available: ${portfolio_analysis['cash_available']:,.2f} ({portfolio_analysis['cash_percentage']:.1f}%)")
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

                logging.debug(f"🔍 Analyzing position: {symbol} ({current_qty} shares, {unrealized_plpc:+.1f}%)")

                # Run technical analysis on the held position (use multi-timeframe if enabled)
                if self.enable_multi_timeframe:
                    analysis = self.analyze_multi_timeframe(symbol, use_ai=use_ai)
                else:
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

                # Stop-loss logic (using configurable values)
                if self.enable_stop_loss:
                    # Hard stop-loss: panic sell at configured percentage
                    if unrealized_plpc < -self.stop_loss_pct:
                        sell_reasons.append(f"🛑 HARD STOP-LOSS (-{abs(unrealized_plpc):.1f}% < -{self.stop_loss_pct}%)")
                        confidence = 100  # Force sell
                    # Take profit: lock in gains at configured percentage
                    elif unrealized_plpc > self.take_profit_pct:
                        sell_reasons.append(f"🎯 TAKE PROFIT (+{unrealized_plpc:.1f}% > +{self.take_profit_pct}%)")
                        confidence = 90
                    # Defensive sell: warning level before hard stop
                    elif unrealized_plpc < -(self.stop_loss_pct * 0.6):
                        sell_reasons.append(f"⚠️ Defensive warning (-{abs(unrealized_plpc):.1f}% < -{self.stop_loss_pct * 0.6:.1f}%)")
                        confidence += 25

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

    def get_position_scores(self) -> list:
        """Get scores for all current positions"""
        try:
            positions = self.trading_client.get_all_positions()
            if not positions:
                return []

            position_scores = []
            for position in positions:
                symbol = position.symbol
                current_qty = float(position.qty)
                market_value = float(position.market_value)

                if current_qty <= 0 or market_value < 50:
                    continue

                # Get technical analysis
                analysis = self.analyze_symbol(symbol, use_ai=False)
                if not analysis:
                    continue

                position_scores.append({
                    'symbol': symbol,
                    'score': analysis.get('total_score', 50),
                    'qty': current_qty,
                    'market_value': market_value,
                    'unrealized_plpc': float(position.unrealized_plpc) * 100,
                    'analysis': analysis
                })

            # Sort by score (lowest first - worst positions)
            position_scores.sort(key=lambda x: x['score'])
            return position_scores

        except Exception as e:
            logging.debug(f"Error getting position scores: {e}")
            return []

    def evaluate_rotation(self, buy_candidates: list) -> list:
        """Evaluate if we should rotate from weak positions to better opportunities"""
        if not self.enable_rotation or not buy_candidates:
            return []

        try:
            # Get current position scores
            position_scores = self.get_position_scores()
            if not position_scores:
                return []

            # Get worst position
            worst_position = position_scores[0]
            worst_score = worst_position['score']

            logging.info(f"\n🔄 ROTATION ANALYSIS:")
            logging.info(f"   📉 Worst position: {worst_position['symbol']} (score: {worst_score:.1f})")

            rotation_sells = []

            # Check each buy candidate
            for candidate in buy_candidates:
                opp_score = candidate.get('total_score', 0)
                score_diff = opp_score - worst_score

                logging.info(f"   📈 {candidate['symbol']}: score {opp_score:.1f} (diff: {score_diff:+.1f} vs worst)")

                # If opportunity is significantly better than worst position
                if score_diff >= self.rotation_threshold:
                    # Only rotate if the worst position isn't already a winner
                    if worst_position['unrealized_plpc'] > -5:  # Not losing more than 5%
                        rotation_sells.append({
                            'symbol': worst_position['symbol'],
                            'sell_qty': worst_position['qty'],
                            'reason': f"Rotate to {candidate['symbol']} (score diff: {score_diff:.1f})",
                            'replace_with': candidate['symbol'],
                            'replace_score': opp_score
                        })
                        logging.info(f"      ✅ ROTATE: Sell {worst_position['symbol']} → Buy {candidate['symbol']}")
                        break  # Only rotate one position at a time

            return rotation_sells

        except Exception as e:
            logging.debug(f"Rotation evaluation failed: {e}")
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
                    # Calculate stop loss price
                    if self.use_atr_stop_loss:
                        # Use ATR-based stop: current_price - (multiplier * ATR)
                        df = self.get_market_data(position['symbol'])
                        if df is not None and len(df) >= 14:
                            df = self.calculate_indicators(df)
                            atr = df['ATR'].iloc[-1] if 'ATR' in df.columns else None
                            if atr and pd.notna(atr) and atr > 0:
                                stop_price = round(position['current_price'] - (self.atr_stop_multiplier * atr), 2)
                                stop_type = "ATR"
                            else:
                                # Fallback to percentage if ATR unavailable
                                stop_price = round(position['current_price'] * (1 - (self.stop_loss_pct / 100)), 2)
                                stop_type = "pct"
                        else:
                            # Fallback
                            stop_price = round(position['current_price'] * (1 - (self.stop_loss_pct / 100)), 2)
                            stop_type = "pct"
                    else:
                        # Use fixed percentage
                        stop_price = round(position['current_price'] * (1 - (self.stop_loss_pct / 100)), 2)
                        stop_type = "pct"

                    # Check if we already have a stop order
                    if self.has_active_stop_order(position['symbol']):
                        logging.info(f"⏭️ {position['symbol']}: Already has stop-loss order, skipping")
                        continue

                    # Place actual stop-loss order
                    success = self._place_stop_loss_order(
                        position['symbol'],
                        int(position['qty']),
                        stop_price
                    )

                    if success:
                        stop_method = f"({self.atr_stop_multiplier}x ATR)" if stop_type == "ATR" else f"({self.stop_loss_pct}%)"
                        actions_taken.append(f"Stop loss set for {position['symbol']} at ${stop_price:.2f} {stop_method}")

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

    def has_active_stop_order(self, symbol: str) -> bool:
        """Check if there's already an active stop-loss order for this symbol"""
        try:
            orders = list(self.trading_client.get_orders())

            # Check for open/stop orders
            active_statuses = {
                OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW,
                OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL,
                OrderStatus.PENDING_REPLACE, OrderStatus.PENDING_REVIEW
            }

            for order in orders:
                if (order.symbol == symbol and
                    order.status in active_statuses and
                    order.side == OrderSide.SELL):
                    # Check if this is a stop or stop-limit order
                    if hasattr(order, 'order_type') and 'stop' in str(order.order_type).lower():
                        return True
                    # Also check order class type if available
                    if hasattr(order, 'stop_price') and order.stop_price:
                        return True
            return False
        except Exception as e:
            logging.debug(f"Could not check existing orders: {e}")
            return False

    def has_pending_orders(self, symbol: str) -> bool:
        """Check if there are any pending orders for this symbol (that might hold shares)"""
        try:
            orders = list(self.trading_client.get_orders())

            active_statuses = {
                OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW,
                OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL,
                OrderStatus.PENDING_REPLACE, OrderStatus.PENDING_REVIEW
            }

            for order in orders:
                if order.symbol == symbol and order.status in active_statuses:
                    return True
            return False
        except Exception as e:
            logging.debug(f"Could not check pending orders: {e}")
            return False
            return False

    def _place_stop_loss_order(self, symbol: str, quantity: int, stop_price: float) -> bool:
        """Place an actual stop-loss order using Alpaca's OTO (one-triggers-other)"""
        try:
            # First check if we already have a stop order
            if self.has_active_stop_order(symbol):
                logging.info(f"⏭️ {symbol}: Already has active stop order, skipping")
                return False

            # Check if there are pending orders that might hold shares
            if self.has_pending_orders(symbol):
                logging.info(f"⏭️ {symbol}: Has pending orders, skipping stop-loss (shares may be locked)")
                return False

            # Get current position to verify we have shares
            try:
                positions = self.trading_client.get_all_positions()
                position = None
                for p in positions:
                    if p.symbol == symbol:
                        position = p
                        break

                if not position:
                    logging.warning(f"⚠️ {symbol}: No position found")
                    return False

                qty = int(abs(float(position.qty)))
                if qty <= 0:
                    logging.warning(f"⚠️ {symbol}: No position to set stop-loss on")
                    return False

                # Check if shares are available for trading (not held for pending orders)
                # Use the position's market_value and avg_entry_price to verify available shares
                try:
                    # Get fresh position data
                    positions = self.trading_client.get_all_positions()
                    position_data = None
                    for p in positions:
                        if p.symbol == symbol:
                            position_data = p
                            break

                    if not position_data:
                        logging.info(f"⏭️ {symbol}: Position no longer exists")
                        return False

                    # Check if position has available shares (not locked)
                    # If market_value is 0 or very small, shares might be locked
                    if float(position_data.market_value) <= 0:
                        logging.info(f"⏭️ {symbol}: No available shares (possibly held for orders)")
                        return False

                except Exception as e:
                    logging.warning(f"⚠️ {symbol}: Could not verify position availability: {e}")
            except Exception as e:
                logging.warning(f"⚠️ {symbol}: Could not get position: {e}")
                return False

            # Place a stop-loss order (sell if price drops below stop_price)
            from alpaca.trading.requests import StopOrderRequest

            stop_order = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                stop_price=stop_price,
                time_in_force=TimeInForce.DAY
            )

            order = self.trading_client.submit_order(order_data=stop_order)

            logging.info(f"🛑 STOP-LOSS ORDER PLACED: {symbol} - Stop: ${stop_price:.2f}, Qty: {qty}")
            return True

        except Exception as e:
            logging.error(f"❌ Failed to place stop-loss for {symbol}: {e}")
            return False

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

            # Send email notification for portfolio actions
            try:
                price = float(order.fill_price) if order.fill_price else 0
            except:
                price = 0
            self.send_trade_notification(
                trade_type=side.upper(),
                symbol=symbol,
                quantity=quantity,
                price=price,
                side=side.upper(),
                analysis={'reason': reason}
            )

            return True

        except Exception as e:
            logging.error(f"❌ Portfolio order failed for {symbol}: {e}")
            return False

    def run_continuous_loop(self, max_symbols: int = 30, max_trades: int = 2, loop_delay: int = 300, summary_interval: int = 30, use_ai: bool = None):
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

                # STRICT: Check for margin usage at start of each loop
                try:
                    account = self.trading_client.get_account()
                    cash = float(account.cash)
                    if cash < 0:
                        print(f"⚠️  MARGIN WARNING: Cash is negative (${cash:.2f}). No new BUY trades will execute!")
                except:
                    pass

                print(f"\n🔄 LOOP #{loop_count} - {loop_start.strftime('%H:%M:%S')}")
                print("-" * 40)

                # Log market regime and VIX at start of each loop
                if self.enable_regime_filter:
                    try:
                        regime = self.get_current_market_regime()
                        print(f"📊 Market Regime: {regime.get('regime', 'UNKNOWN')} (ADX: {regime.get('adx', 0):.1f})")
                    except Exception as e:
                        logging.debug(f"Could not get regime: {e}")

                try:
                    vix_info = self.get_vix_regime()
                    vix_indicator = ("🟢" if vix_info['regime'] == 'LOW'
                                     else "🟡" if vix_info['regime'] == 'NORMAL'
                                     else "🟠" if vix_info['regime'] == 'HIGH'
                                     else "🔴")
                    print(f"{vix_indicator} VIX: {vix_info['vix_level']} ({vix_info['regime']})"
                          + (" - sizing reduced" if vix_info['should_reduce_sizing'] else ""))
                except Exception as e:
                    logging.debug(f"Could not display VIX: {e}")

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

                    # Execute any pending entry tranches (tranche 2 & 3 follow-ups)
                    try:
                        self._execute_pending_entry_tranches()
                    except Exception as e:
                        logging.debug(f"Pending tranche execution failed: {e}")

                    # Portfolio optimisation (every N loops)
                    if (self.portfolio_optimizer is not None
                            and loop_count % self.portfolio_opt_interval_loops == 0):
                        try:
                            opt_result = self.portfolio_optimizer.run_optimization_pass(self)
                            if opt_result and opt_result.get('rebalance_trades'):
                                print(
                                    f"📊 Portfolio Optimisation: "
                                    f"Sharpe {opt_result['current_sharpe']:.3f} → "
                                    f"{opt_result['optimal_sharpe']:.3f} "
                                    f"({len(opt_result['rebalance_trades'])} suggestions)"
                                )
                        except Exception as e:
                            logging.debug(f"Portfolio optimisation failed in loop: {e}")

                    # Adaptive learning (every N loops)
                    try:
                        if (loop_count % self.learning_interval_loops == 0
                                and hasattr(self, 'adaptive_learning')
                                and self.adaptive_learning is not None):
                            changes = self.adaptive_learning.run_analysis_and_apply(self)
                            if changes:
                                print(f"🧠 Adaptive learning updated {len(changes)} parameters")
                    except Exception as e:
                        logging.debug(f"Adaptive learning failed in loop: {e}")

                    # Show summary every N loops
                    if loop_count % summary_interval == 0:
                        self._show_performance_summary(loop_count, loop_performance, total_trades, total_opportunities, start_time, ticker_positions, ticker_transactions)

                        # Send Telegram summary notification
                        try:
                            account = self.trading_client.get_account()
                            portfolio_value = float(account.portfolio_value)
                            positions = len(self.trading_client.get_all_positions())
                            # Calculate unrealized P&L
                            unrealized_pl = sum(float(p.unrealized_pl) for p in self.trading_client.get_all_positions())
                            self.send_summary_notification(loop_count, total_trades, portfolio_value, unrealized_pl, positions)
                        except Exception as e:
                            logging.debug(f"Could not send summary: {e}")

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

        # Check AI agent status
        ai_health = "🟢 Healthy" if self.ai.is_configured and not self.rate_limit_detected else "🟡 Limited"
        if self.rate_limit_detected:
            ai_health = "🔴 Rate Limited"

        print(f"Overall AI Health: {ai_health}{rate_limit_info}")

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
        parser.add_argument('--delay', '-d', type=int, default=10,
                          help='Seconds between loops in continuous mode (default: 10)')
        parser.add_argument('--max-symbols', type=int, default=30,
                          help='Maximum symbols to analyze per loop (default: 30)')
        parser.add_argument('--max-trades', type=int, default=2,
                          help='Maximum trades to execute per loop (default: 2)')
        parser.add_argument('--summary-interval', type=int, default=30,
                          help='Show/send summary every N loops (default: 30 = ~5 min)')
        parser.add_argument('--logs', '-l', type=int, default=0,
                          help='Show last N lines of logs (default: 0 = off, use 50 for last 50 lines)')
        parser.add_argument('--tail', '-t', action='store_true',
                          help='Tail the log file continuously (like tail -f)')

        # Stop-Loss Configuration
        parser.add_argument('--stop-loss', type=float, default=8.0,
                          help='Stop-loss percentage (default: 8.0)')
        parser.add_argument('--take-profit', type=float, default=15.0,
                          help='Take profit percentage (default: 15.0)')
        parser.add_argument('--no-stop-loss', action='store_true',
                          help='Disable stop-loss system')

        # ATR-based Position Sizing
        parser.add_argument('--risk-per-trade', type=float, default=2.0,
                          help='Risk percentage per trade (default: 2.0)')
        parser.add_argument('--max-position', type=float, default=5.0,
                          help='Max position size as %% of portfolio (default: 5.0)')
        parser.add_argument('--no-atr-sizing', action='store_true',
                          help='Disable ATR-based position sizing (use fixed instead)')

        # Max Drawdown Protection
        parser.add_argument('--max-drawdown', type=float, default=10.0,
                          help='Max drawdown %% before pausing trading (default: 10.0)')
        parser.add_argument('--no-drawdown-protection', action='store_true',
                          help='Disable max drawdown protection')

        # Daily Loss Limit
        parser.add_argument('--daily-loss-limit', type=float, default=5.0,
                          help='Daily loss %% limit - stop trading if exceeded (default: 5.0)')
        parser.add_argument('--no-daily-loss-limit', action='store_true',
                          help='Disable daily loss limit')

        # AI configuration flags
        parser.add_argument('--ai-ticker-analysis', action='store_true', default=False,
                          help='Enable AI to read articles for each ticker (slower, more detailed)')
        parser.add_argument('--ai-ticker-selection', action='store_true', default=False,
                          help='Enable AI-based ticker selection (default: off - use rolling list)')
        parser.add_argument('--no-ai-ticker-selection', action='store_true',
                          help='Disable AI-based ticker selection (use rolling list)')
        parser.add_argument('--ai-market-summary', action='store_true', default=False,
                          help='Enable AI market summary (reads 5 tickers for sentiment)')
        parser.add_argument('--no-ai-market-summary', action='store_true',
                          help='Disable AI market sentiment summaries')
        parser.add_argument('--no-ai', action='store_true',
                          help='Disable ALL AI features (pure technical analysis mode)')

        # Multi-timeframe analysis
        parser.add_argument('--multi-timeframe', action='store_true', default=True,
                          help='Use multi-timeframe analysis (daily + hourly)')
        parser.add_argument('--no-multi-timeframe', action='store_true', default=False,
                          help='Disable multi-timeframe analysis (use daily only)')

        # Volume confirmation
        parser.add_argument('--no-volume-confirmation', action='store_true', default=False,
                          help='Disable volume confirmation for signals')

        # Earnings filter
        parser.add_argument('--earnings-filter', type=int, default=3,
                          help='Days before earnings to skip trades (default: 3, 0 to disable)')

        # SPY Relative Strength filter
        parser.add_argument('--no-sp-filter', action='store_true', default=False,
                          help='Disable SPY relative strength filter (buy stocks outperforming SPY)')

        # Liquidity filter
        parser.add_argument('--no-liquidity-filter', action='store_true', default=False,
                          help='Disable liquidity filter (skip low-volume stocks)')
        parser.add_argument('--min-volume', type=int, default=1000000,
                          help='Minimum daily volume for liquidity filter (default: 1M)')

        # Sector rotation filter
        parser.add_argument('--no-sector-filter', action='store_true', default=False,
                          help='Disable sector rotation filter')

        # Correlation check
        parser.add_argument('--no-correlation-check', action='store_true', default=False,
                          help='Disable correlation check with existing positions')
        parser.add_argument('--max-correlation', type=float, default=0.7,
                          help='Maximum correlation allowed (default: 0.7)')

        # Beta exposure check
        parser.add_argument('--no-beta-check', action='store_true', default=False,
                          help='Disable portfolio beta check')
        parser.add_argument('--max-beta', type=float, default=1.5,
                          help='Maximum portfolio beta allowed (default: 1.5)')

        # ATR-based stop-loss
        parser.add_argument('--no-atr-stop', action='store_true', default=False,
                          help='Disable ATR-based stop-loss (use fixed percentage)')
        parser.add_argument('--atr-stop-multiplier', type=float, default=2.0,
                          help='ATR multiplier for stop-loss (default: 2.0)')

        # Historical data download
        parser.add_argument('--download-historical', type=str, default=None,
                          help='Download historical OHLCV data for symbol(s). Can be single symbol (AAPL) or comma-separated (AAPL,MSFT,GOOG)')
        parser.add_argument('--historical-years', type=int, default=3,
                          help='Number of years of historical data to download (default: 3)')

        # Data pipeline commands
        parser.add_argument('--backfill', action='store_true', default=False,
                          help='Run full backfill for all portfolio symbols')
        parser.add_argument('--backfill-symbol', type=str, default=None,
                          help='Backfill a single symbol')
        parser.add_argument('--sync', action='store_true', default=False,
                          help='Run incremental daily sync')
        parser.add_argument('--data-health', action='store_true', default=False,
                          help='Show data quality report')
        parser.add_argument('--list-gaps', action='store_true', default=False,
                          help='Show missing data ranges')
        parser.add_argument('--create-tables', action='store_true', default=False,
                          help='Create OHLCV database tables (prints SQL)')

        # Market Regime options
        parser.add_argument('--show-regime', action='store_true', default=False,
                          help='Show current market regime (ADX-based)')
        parser.add_argument('--regime-symbol', type=str, default='SPY',
                          help='Symbol for regime detection (default: SPY)')
        parser.add_argument('--adx-period', type=int, default=14,
                          help='ADX calculation period (default: 14)')
        parser.add_argument('--trend-threshold', type=float, default=25.0,
                          help='ADX threshold for trending (default: 25)')
        parser.add_argument('--range-threshold', type=float, default=20.0,
                          help='ADX threshold for ranging (default: 20)')
        parser.add_argument('--force-regime', type=str, default=None, choices=['TRENDING_BULLISH', 'TRENDING_BEARISH', 'RANGING', 'TRANSITIONING'],
                          help='Override regime detection for testing')
        parser.add_argument('--no-regime-filter', action='store_true', default=False,
                          help='Disable regime filtering')

        args = parser.parse_args()

        # Handle regime display
        if args.show_regime:
            from src.analysis.market_regime import MarketRegimeClassifier
            from src.database.simple_rest import SimpleSupabaseREST

            db = SimpleSupabaseREST()
            classifier = MarketRegimeClassifier(
                db=db,
                regime_symbol=args.regime_symbol,
                adx_period=args.adx_period,
                trend_threshold=args.trend_threshold,
                range_threshold=args.range_threshold
            )

            print(f"\\n📊 Market Regime for {args.regime_symbol}")
            print("=" * 40)

            result = classifier.calculate_current_regime()
            print(f"Regime:     {result['regime']}")
            print(f"ADX:        {result['adx']:.1f}")
            print(f"+DI:        {result['plus_di']:.1f}")
            print(f"-DI:        {result['minus_di']:.1f}")

            mods = classifier.get_strategy_modifiers(result['regime'])
            print(f"\\nStrategy Modifiers:")
            print(f"  Description: {mods['description']}")
            print(f"  RSI Buy Threshold: {mods['rsi_buy_threshold']}")
            print(f"  RSI Sell Threshold: {mods['rsi_sell_threshold']}")
            print(f"  Position Size: {mods['position_size_multiplier']}x")
            print(f"  Stop Loss Multiplier: {mods['stop_loss_multiplier']}x")
            return

        # Handle historical data download request
        if args.download_historical or args.backfill or args.backfill_symbol or args.sync or args.data_health or args.list_gaps or args.create_tables:
            from src.data.historical_pipeline import HistoricalDataPipeline
            from src.database.simple_rest import SimpleSupabaseREST

            # Initialize pipeline
            pipeline = HistoricalDataPipeline()

            # Try to get Supabase connection
            try:
                db = SimpleSupabaseREST()
                if db.is_available():
                    pipeline.db = db
            except:
                pass

            # Create tables
            if args.create_tables:
                print("📋 Creating OHLCV tables in Supabase...")
                pipeline.create_database_tables()
                return

            # Download single symbol(s)
            if args.download_historical:
                print(f"📥 Downloading historical data...")
                symbols = [s.strip() for s in args.download_historical.split(',')]

                for symbol in symbols:
                    print(f"   Downloading {symbol}...")
                    df = pipeline.fetch_daily_ohlcv(symbol)
                    if df is not None:
                        pipeline.save_to_csv(df, symbol, 'daily')
                        min_date = df['date'].min()
                        max_date = df['date'].max()
                        print(f"   ✓ {symbol}: {len(df)} days ({min_date} to {max_date})")
                    else:
                        print(f"   ✗ {symbol}: Failed to download")

                print("📥 Historical data download complete!")
                return

            # Backfill single symbol
            if args.backfill_symbol:
                print(f"🔄 Backfilling {args.backfill_symbol}...")
                success = pipeline.sync_symbol(args.backfill_symbol, years=args.historical_years)
                if success:
                    print(f"✅ Backfill complete for {args.backfill_symbol}")
                else:
                    print(f"❌ Backfill failed for {args.backfill_symbol}")
                return

            # Backfill all portfolio symbols
            if args.backfill:
                print("🔄 Running full backfill for portfolio...")
                try:
                    positions = self.trading_client.get_all_positions()
                    symbols = list(set([p.symbol for p in positions]))
                except:
                    symbols = []

                if symbols:
                    results = pipeline.backfill_symbols(symbols, years=args.historical_years, delay=0.5)
                    success = sum(1 for v in results.values() if v)
                    print(f"📥 Backfill complete: {success}/{len(symbols)} symbols")
                else:
                    print("❌ No positions found")
                return

            # Daily sync
            if args.sync:
                print("🔄 Running daily sync...")
                results = pipeline.daily_sync()
                success = sum(1 for v in results.values() if v)
                print(f"📥 Daily sync complete: {success} symbols updated")
                return

            # Data health check
            if args.data_health:
                print("📊 Data Health Report")
                print("=" * 50)

                # Get all downloaded symbols
                import os
                import glob
                files = glob.glob(f"{pipeline.data_dir}/*_daily_*.csv")
                symbols = list(set([os.path.basename(f).split('_')[0] for f in files]))

                for symbol in symbols[:20]:  # Limit to 20
                    health = pipeline.check_data_health(symbol)
                    status = "✅" if health['status'] == 'healthy' else "⚠️"
                    print(f"{status} {symbol}: {health.get('total_days', 0)} days, {health.get('date_range', 'N/A')}")
                    if health.get('issues'):
                        print(f"   Issues: {', '.join(health['issues'])}")
                return

            # List gaps
            if args.list_gaps:
                print("📋 Data Gaps")
                print("=" * 50)

                import os
                import glob
                files = glob.glob(f"{pipeline.data_dir}/*_daily_*.csv")
                symbols = list(set([os.path.basename(f).split('_')[0] for f in files]))

                for symbol in symbols[:20]:
                    gaps = pipeline.check_data_gaps(symbol)
                    if gaps and 'error' not in gaps[0]:
                        print(f"⚠️ {symbol}: {len(gaps)} gaps found")
                        for gap in gaps[:3]:
                            print(f"   {gap['start']} to {gap['end']}: {gap['missing_days']} days")
                return

        args = parser.parse_args()

        # Handle log viewing options
        log_file = 'trading_bot.log'
        if args.logs > 0 or args.tail:
            import os
            if os.path.exists(log_file):
                if args.tail:
                    # Tail mode - continuous follow
                    print(f"📜 Tailing {log_file} (Ctrl+C to stop)...\n")
                    try:
                        with open(log_file, 'r') as f:
                            # Seek to end of file
                            f.seek(0, 2)
                            while True:
                                line = f.readline()
                                if not line:
                                    time.sleep(0.5)
                                else:
                                    print(line.rstrip())
                    except KeyboardInterrupt:
                        print("\n👋 Log tailing stopped")
                        sys.exit(0)
                else:
                    # Show last N lines
                    with open(log_file, 'r') as f:
                        lines = f.readlines()
                    last_n = args.logs
                    print(f"📜 Last {last_n} lines of {log_file}:\n")
                    for line in lines[-last_n:]:
                        print(line.rstrip())
                    sys.exit(0)
            else:
                print(f"❌ Log file not found: {log_file}")
                sys.exit(1)

        bot = SmartTradingBot()

        # Configure AI settings based on command-line arguments
        if args.no_ai:
            # Disable all AI features
            bot.configure_ai_usage(
                ticker_analysis=False,
                ticker_selection=False,
                market_summary=False
            )
        else:
            # Configure individual AI features
            # Default: ticker analysis OFF (skip article reading), ticker selection OFF (use rolling list), market summary OFF
            # Use --ai-ticker-analysis to enable article reading
            # Use --ai-ticker-selection to enable AI ticker selection (not recommended)
            enable_ticker_analysis = getattr(args, 'ai_ticker_analysis', False)
            enable_market_summary = getattr(args, 'ai_market_summary', False)
            # Default to False for ticker selection - use rolling list instead
            enable_ticker_selection = getattr(args, 'ai_ticker_selection', False)
            bot.configure_ai_usage(
                ticker_analysis=enable_ticker_analysis,
                ticker_selection=enable_ticker_selection,
                market_summary=enable_market_summary
            )

        # Configure Stop-Loss settings
        if hasattr(args, 'stop_loss'):
            bot.stop_loss_pct = args.stop_loss
            bot.take_profit_pct = args.take_profit if hasattr(args, 'take_profit') else 15.0
            bot.enable_stop_loss = not args.no_stop_loss if hasattr(args, 'no_stop_loss') else True
            print(f"🛑 Stop-Loss: {'Enabled' if bot.enable_stop_loss else 'Disabled'}")
            if bot.enable_stop_loss:
                print(f"   Hard Stop: {bot.stop_loss_pct}% | Take Profit: {bot.take_profit_pct}%")

        # Configure ATR-based Position Sizing
        if hasattr(args, 'risk_per_trade'):
            bot.risk_per_trade = args.risk_per_trade / 100  # Convert to decimal
            bot.max_position_pct = args.max_position / 100  # Convert to decimal
            bot.enable_atr_sizing = not args.no_atr_sizing if hasattr(args, 'no_atr_sizing') else True
            print(f"📊 ATR Position Sizing: {'Enabled' if bot.enable_atr_sizing else 'Disabled'}")
            if bot.enable_atr_sizing:
                print(f"   Risk/Trade: {args.risk_per_trade}% | Max Position: {args.max_position}%")

        # Configure Max Drawdown Protection
        if hasattr(args, 'max_drawdown'):
            bot.max_drawdown_pct = args.max_drawdown
            bot.enable_drawdown_protection = not args.no_drawdown_protection if hasattr(args, 'no_drawdown_protection') else True
            print(f"📉 Max Drawdown Protection: {'Enabled' if bot.enable_drawdown_protection else 'Disabled'}")
            if bot.enable_drawdown_protection:
                print(f"   Max Drawdown: {args.max_drawdown}%")

        # Configure Daily Loss Limit
        if hasattr(args, 'daily_loss_limit'):
            bot.daily_loss_limit_pct = args.daily_loss_limit
            bot.enable_daily_loss_limit = not args.no_daily_loss_limit if hasattr(args, 'no_daily_loss_limit') else True
            print(f"📊 Daily Loss Limit: {'Enabled' if bot.enable_daily_loss_limit else 'Disabled'}")
            if bot.enable_daily_loss_limit:
                print(f"   Max Daily Loss: {args.daily_loss_limit}%")

        # Multi-timeframe analysis
        if hasattr(args, 'no_multi_timeframe') and args.no_multi_timeframe:
            bot.enable_multi_timeframe = False

        if bot.enable_multi_timeframe:
            print(f"📊 Multi-Timeframe: Enabled (Daily 70% + Hourly 30%)")

        # Volume confirmation
        if hasattr(args, 'no_volume_confirmation') and args.no_volume_confirmation:
            bot.enable_volume_confirmation = False

        if bot.enable_volume_confirmation:
            print(f"📊 Volume Confirmation: Enabled (volume > 20-day avg required)")

        # Earnings filter
        if hasattr(args, 'earnings_filter'):
            bot.earnings_days_skip = args.earnings_filter
            if bot.earnings_days_skip > 0:
                print(f"📅 Earnings Filter: Enabled (skip {bot.earnings_days_skip} days before earnings)")
            else:
                print(f"📅 Earnings Filter: Disabled")

        # SPY Relative Strength filter
        if hasattr(args, 'no_sp_filter') and args.no_sp_filter:
            bot.enable_sp_filter = False

        if bot.enable_sp_filter:
            print(f"📈 SPY Filter: Enabled (only buy stocks outperforming SPY)")

        # Liquidity filter
        if hasattr(args, 'no_liquidity_filter') and args.no_liquidity_filter:
            bot.enable_liquidity_filter = False
        else:
            if hasattr(args, 'min_volume'):
                bot.min_daily_volume = args.min_volume

        if bot.enable_liquidity_filter:
            print(f"💧 Liquidity Filter: Enabled (min volume: {bot.min_daily_volume/1e6:.1f}M)")

        # Sector rotation filter
        if hasattr(args, 'no_sector_filter') and args.no_sector_filter:
            bot.enable_sector_filter = False

        if bot.enable_sector_filter:
            # Pre-calculate sector scores
            bot.sector_rotation_scores = bot.get_sector_rotation_scores()
            print(f"🏛️ Sector Filter: Enabled (prefer strong sectors)")

        # Correlation check
        if hasattr(args, 'no_correlation_check') and args.no_correlation_check:
            bot.max_correlation = 1.0  # Disable by setting to max
        else:
            if hasattr(args, 'max_correlation'):
                bot.max_correlation = args.max_correlation

        if bot.max_correlation < 1.0:
            print(f"📊 Correlation Check: Enabled (max correlation: {bot.max_correlation})")

        # Beta exposure check
        if hasattr(args, 'no_beta_check') and args.no_beta_check:
            bot.max_portfolio_beta = 99.0  # Disable by setting very high
        else:
            if hasattr(args, 'max_beta'):
                bot.max_portfolio_beta = args.max_beta

        if bot.max_portfolio_beta < 99.0:
            print(f"📈 Beta Check: Enabled (max portfolio beta: {bot.max_portfolio_beta})")

        # ATR-based stop-loss
        if hasattr(args, 'no_atr_stop') and args.no_atr_stop:
            bot.use_atr_stop_loss = False
            print(f"🛑 Stop-Loss: Using fixed {bot.stop_loss_pct}% (ATR disabled)")
        else:
            if hasattr(args, 'atr_stop_multiplier'):
                bot.atr_stop_multiplier = args.atr_stop_multiplier
            print(f"🛑 Stop-Loss: Using {bot.atr_stop_multiplier}x ATR")

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