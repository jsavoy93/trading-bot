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
├── scripts/                      # Automation scripts
│   ├── migrate.py               # Migration CLI
│   └── run_migrations.py        # Migration runner
├── migrations/                   # Database migrations
│   ├── 0001_initial_schema.sql  # Schema creation
│   ├── rollback.sql             # Rollback script
│   └── seed_data.sql            # Test data
├── docs/                        # Documentation
│   └── MIGRATION_GUIDE.md       # Database setup guide
├── legacy/                      # Legacy files (safe to delete)
├── main.py                      # Main entry point
├── requirements.txt             # Python dependencies
├── .env                         # Environment variables
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
./run.sh
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

- [Migration Guide](docs/MIGRATION_GUIDE.md) - Complete database setup
- [Coding Guidelines](.cursorrules) - Development standards
- API documentation in code comments

## 🤝 Contributing

1. Follow the established project structure
2. Update tests for new features
3. Document database schema changes
4. Use the migration system for DB updates

## ⚠️ Disclaimer

This bot is for educational and testing purposes only. Always test thoroughly with paper trading before considering any real money deployment. Past performance does not guarantee future results.

## 📄 License

MIT License - see LICENSE file for details.