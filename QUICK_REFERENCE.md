# Trading Bot - Quick Reference

## 🚀 Running the Bot

```bash
# Single run with AI disabled (fastest)
python main.py --no-ai

# Single run with full AI
python main.py

# Continuous mode (1 minute delay)
python main.py --continuous --delay 60

# Continuous mode without AI analysis
python main.py -c -d 60 --no-ai-ticker-analysis

# Full continuous mode with all AI features
python main.py -c -d 300
```

## 🎛️ Command Line Options

| Option | Short | Description |
|--------|-------|-------------|
| `--continuous` | `-c` | Run in continuous loop mode |
| `--delay SECONDS` | `-d` | Delay between loops (default: 300s) |
| `--max-symbols NUM` | | Max symbols per loop (default: 30) |
| `--max-trades NUM` | | Max trades per loop (default: 2) |
| `--no-ai` | | Disable all AI features |
| `--ai-selection-only` | | 🌟 AI picks tickers, technical decides trades (RECOMMENDED) |
| `--no-ai-ticker-analysis` | | Disable AI for ticker analysis only |
| `--no-ai-ticker-selection` | | Disable AI ticker selection only |
| `--no-ai-market-summary` | | Disable AI market summary only |

## 🌟 Recommended Modes

### AI Selection Only (FASTEST AI MODE)
```bash
# AI analyzes your portfolio and picks smart tickers
# Then uses technical analysis (RSI, SMA) to decide trades
# No article fetching per ticker = much faster!
python main.py -c -d 60 --ai-selection-only
```

### Pure Technical (NO AI)
```bash
# Fastest mode - no AI at all
python main.py -c -d 60 --no-ai
```

### Full AI (SLOWEST)
```bash
# AI does everything - portfolio analysis, ticker selection, 
# article research, and per-ticker analysis
python main.py -c -d 300
```

## 📊 Useful Scripts

```bash
# Performance analysis
python scripts/performance_analysis.py

# Run without AI (example)
python scripts/example_no_ai_analysis.py

# Test AI connections
python scripts/test_ai_connections.py
```

## 🧪 Testing

```bash
# Test no-trade reasons
python tests/test_no_trade_reasons.py

# Test ticker criteria
python tests/test_ticker_criteria.py
```

## 🗄️ Database Management

```bash
# Apply migration
./migrate.sh

# Generate new migration
python scripts/run_migrations.py generate

# Validate schema
python scripts/run_migrations.py validate
```

## 📁 Key Files

| File | Purpose |
|------|---------|
| `main.py` | Main entry point |
| `src/core/smart_bot.py` | Core trading logic |
| `src/database/simple_rest.py` | Database client |
| `AI_CONFIGURATION.md` | AI setup guide |
| `QUICK_START.md` | Detailed getting started |

## 🔍 Logs Location

Logs are automatically created in the root directory:
- `trading_bot.log` - Main bot log
- `ai_agent.log` - AI operations log

## 📚 Documentation

- [README.md](README.md) - Main documentation
- [QUICK_START.md](QUICK_START.md) - Getting started guide
- [AI_CONFIGURATION.md](AI_CONFIGURATION.md) - AI features setup
- [docs/MIGRATION_GUIDE.md](docs/MIGRATION_GUIDE.md) - Database setup
- [CHANGELOG.md](CHANGELOG.md) - Version history

## 💡 Tips

- Start with `--no-ai` for fastest testing
- Use `--max-symbols 10` for quick tests
- Check `trading_bot.log` for detailed execution info
- Monitor database status at bot startup
- Use continuous mode (`-c`) for live trading simulation

## 🛠️ Troubleshooting

**Import errors?**
```bash
# Make sure you're in the project root
cd /workspaces/trading-bot
python main.py
```

**Database not connecting?**
- Check `.env` file has correct Supabase credentials
- Bot works without database (falls back to in-memory)

**AI features not working?**
- Verify API keys in `.env`
- Try `--no-ai` to disable and test core functionality
- Check `ai_agent.log` for errors

## 🎯 Common Use Cases

**Fast testing:**
```bash
python main.py --no-ai --max-symbols 10
```

**Production-like:**
```bash
python main.py -c -d 300
```

**AI-assisted but faster:**
```bash
python main.py -c -d 60 --no-ai-market-summary
```

**Performance monitoring:**
```bash
python scripts/performance_analysis.py
```
