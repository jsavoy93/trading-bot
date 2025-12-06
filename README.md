# Advanced Trading Bot

A sophisticated algorithmic trading bot with machine learning capabilities, comprehensive database integration, and enterprise-grade migration system.

## 🚀 Quick Start

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your API credentials
   ```

3. **Setup Database** (Optional):
   ```bash
   python scripts/run_migrations.py setup
   # Follow instructions to apply migration in Supabase
   ```

4. **Run Trading Bot**:
   ```bash
   ./run.sh
   # OR use the virtual environment directly:
   .venv/bin/python main.py
   ```

## 📁 Project Structure

```
trading-bot/
├── src/                          # Source code
│   ├── core/                     # Core trading logic
│   │   └── smart_bot.py         # Main trading bot
│   ├── database/                 # Database components
│   │   ├── simple_rest.py       # REST API client
│   │   └── migration_system.py  # Migration generator
│   ├── analysis/                 # Analysis and learning
│   │   ├── learning_engine.py   # ML learning system
│   │   └── performance_dashboard.py # Performance analytics
│   └── utils/                    # Utility functions
├── scripts/                      # Utility scripts
│   ├── performance_analysis.py  # Performance analytics
│   ├── run_continuous.py        # Continuous trading mode
│   └── archive/                 # Archived scripts
├── tests/                        # Test files
│   ├── test_no_trade_reasons.py # No-trade reasons demo
│   ├── test_ticker_criteria.py  # Ticker criteria analysis
│   └── archive/                 # Archived tests
├── migrations/                   # Database migrations
│   ├── 0001_initial_schema.sql  # Schema creation
│   ├── 0002_learning_engine.sql # Learning system
│   ├── 0003_cooldown_persistence_only.sql # Cooldown tables
│   └── rollback/                # Rollback scripts
├── docs/                        # Documentation
│   ├── MIGRATION_GUIDE.md       # Database setup guide
│   └── API_DOCUMENTATION.md     # API reference
├── legacy/                      # Legacy code (archived)
├── main.py                      # Main entry point
├── requirements.txt             # Python dependencies
├── AI_CONFIGURATION.md          # AI feature configuration
├── QUICK_START.md               # Quick start guide
└── README.md                    # This file
```

## 🎯 Features

### Core Trading
- **📊 Market Scanning**: Analyzes all US market symbols (5,000+ stocks)
- **🔍 Technical Analysis**: RSI, SMA indicators with configurable parameters
- **💼 Paper Trading**: Safe testing environment with Alpaca API
- **⚡ Real-time Processing**: Live market data integration

### Database Integration
- **🗄️ PostgreSQL Support**: Full Supabase integration via REST API
- **📋 Migration System**: Version-controlled schema management
- **📈 Performance Tracking**: Comprehensive trade and session logging
- **🧠 Learning Engine**: ML-ready data collection and analysis

### AI-Powered Analysis (Optional)
- **🤖 AI Research**: Automated stock research with news analysis
- **📰 Sentiment Analysis**: Real-time news sentiment evaluation
- **💡 Smart Recommendations**: AI-enhanced trading decisions
- **📋 Market Summaries**: Comprehensive market overview generation

### Enterprise Features
- **🔒 Security**: Row Level Security (RLS) enabled
- **📊 Analytics**: Performance metrics and win rate tracking
- **🛡️ Error Handling**: Comprehensive error logging and recovery
- **🔄 Graceful Fallback**: Works with or without database connection

## 🔧 Configuration

### Environment Variables (.env)
```bash
# Alpaca Trading API
ALPACA_API_KEY=your_alpaca_key
ALPACA_API_SECRET=your_alpaca_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Supabase Database (Optional)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_key

# AI Agent Credentials (Optional)
# Choose one: OpenAI or Google AI
OPENAI_API_KEY=your_openai_api_key
GOOGLE_AI_API_KEY=your_google_ai_key  
AI_PROVIDER=openai  # Options: openai, google

NEWS_API_KEY=your_newsapi_key
POLYGON_API_KEY=your_polygon_key  # Optional
```

### Trading Parameters
Edit `src/core/smart_bot.py` to customize:
- Trade amount per position
- RSI buy/sell thresholds  
- SMA periods (fast/slow)
- Maximum trades per session

## 🗄️ Database Setup

### Option 1: Automated Setup
```bash
./migrate.sh setup
# OR use the virtual environment directly:
.venv/bin/python scripts/run_migrations.py setup
```

### Option 2: Manual Setup
1. Go to [Supabase Dashboard](https://supabase.com/dashboard)
2. Navigate to SQL Editor
3. Run the SQL from `migrations/0001_initial_schema.sql`
4. Validate with: `python scripts/run_migrations.py validate`

### Database Schema
- **trading_sessions**: Session metadata and statistics
- **trades**: Individual trade records with indicators
- **market_data**: Historical price data with calculations
- **error_logs**: Error tracking and debugging
- **performance_metrics**: Analytics and learning data

## 📊 Usage Examples

### Basic Trading Session
```bash
python main.py
```

### Continuous Trading Mode
```bash
# Run continuously with 5-minute delays
python main.py --continuous -d 300

# Short delay for testing
python main.py -c -d 60 --max-symbols 20
```

### AI Configuration Options

**Full AI Mode (Default)**:
```bash
python main.py -c -d 60
```

**Pure Technical Analysis (No AI)**:
```bash
python main.py --no-ai -c -d 60
```

**Disable Individual Ticker AI Only** (Fastest):
```bash
python main.py --no-ai-ticker-analysis -c -d 60
```

**Custom AI Configuration**:
```bash
# AI ticker selection, but technical-only analysis
python main.py --no-ai-ticker-analysis --no-ai-market-summary -c -d 90
```

See [AI_CONFIGURATION.md](AI_CONFIGURATION.md) for detailed AI configuration options.

### AI Features Setup
```bash
# Configure AI features (optional)
python scripts/setup_ai.py
```

### Database Operations
```bash
# Check database status
./migrate.sh status

# Generate new migration
./migrate.sh generate

# Validate migration applied
./migrate.sh validate
```

### Performance Analysis
The bot automatically tracks:
- Win/loss ratios
- Profit/loss per trade
- RSI effectiveness
- Market timing patterns
- Error frequencies

## 🛠️ Development

### Adding New Features
1. Core trading logic → `src/core/`
2. Database models → `src/database/`
3. Analysis tools → `src/analysis/`
4. Utilities → `src/utils/`

### Database Changes
1. Update schema in `src/database/migration_system.py`
2. Generate migration: `python scripts/run_migrations.py generate`
3. Apply in Supabase SQL Editor
4. Validate: `python scripts/run_migrations.py validate`

## 🚨 Safety Features

- **Paper Trading Only**: No real money at risk
- **Rate Limiting**: Respects API limits
- **Error Recovery**: Graceful handling of network/API issues
- **Database Fallback**: Works without database connection
- **Comprehensive Logging**: Full audit trail

## 📈 Performance

- **Scalable**: Handles 5,000+ symbols efficiently
- **Fast**: Optimized database queries with indexing
- **Reliable**: Network-resilient REST API integration
- **Memory Efficient**: Streaming data processing

## 🔍 Monitoring

The bot provides real-time monitoring of:
- Symbols processed
- Trading opportunities found
- Orders executed
- Error rates
- Performance metrics

## 📚 Documentation

- [Quick Start Guide](QUICK_START.md) - Get started quickly
- [AI Configuration](AI_CONFIGURATION.md) - AI features setup and usage
- [Migration Guide](docs/MIGRATION_GUIDE.md) - Complete database setup
- [Change Log](CHANGELOG.md) - Version history and updates
- Additional docs in `docs/` directory

## 🧪 Testing

```bash
# Test no-trade reasons feature
python tests/test_no_trade_reasons.py

# Test ticker criteria analysis
python tests/test_ticker_criteria.py
```

See `tests/README.md` for more information.

## 🤝 Contributing

1. Follow the established project structure
2. Update tests for new features
3. Document database schema changes
4. Use the migration system for DB updates

## 📦 Repository Organization

- **Active Code**: `src/`, `main.py`, `migrations/`
- **Scripts**: `scripts/` (utilities and helpers)
- **Tests**: `tests/` (active tests and archive)
- **Documentation**: `docs/`, `*.md` files
- **Legacy**: `legacy/` (archived, safe to delete)
- **Archived**: `tests/archive/`, `scripts/archive/` (kept for reference)

## ⚠️ Disclaimer

This bot is for educational and testing purposes only. Always test thoroughly with paper trading before considering any real money deployment. Past performance does not guarantee future results.

## 📄 License

MIT License - see LICENSE file for details.

## 📱 SMS Alerts (Twilio)

You can enable SMS notifications when the bot executes a trade using Twilio.

1. Install the Twilio package:
```bash
pip install twilio
```

2. Set the following environment variables (example):
```bash
export TWILIO_ACCOUNT_SID="your_account_sid"
export TWILIO_AUTH_TOKEN="your_auth_token"
export TWILIO_FROM_NUMBER="+12345556666"   # your Twilio number
export ALERT_PHONE_NUMBER="+15551234567"   # your mobile number
```

3. Send a one-off test SMS and exit:
```bash
python main.py --test-sms
```

4. When running the bot normally, it will send an SMS each time a trade is executed (if Twilio is configured):
```bash
python main.py --continuous -d 300
```

If Twilio is not installed or the env vars are missing, the bot will log a warning and continue running without SMS notifications.