# Database Migration System

This trading bot now includes a comprehensive database migration system that works with the REST API approach to avoid network connectivity issues in containerized environments.

## 🚀 Quick Start

1. **Generate Migration Files**:
   ```bash
   python migrate.py setup
   ```

2. **Apply Migration in Supabase**:
   - Go to [Supabase Dashboard](https://supabase.com/dashboard)
   - Select your project
   - Navigate to SQL Editor
   - Copy and paste the SQL from `migrations/0001_initial_schema.sql`
   - Run the query

3. **Validate Migration**:
   ```bash
   python migrate.py validate
   ```

4. **Run Trading Bot**:
   ```bash
   python smart_bot.py
   ```

## 📋 Available Commands

### Check Database Status
```bash
python migrate.py status
```
Shows current database connection status, schema version, and table counts.

### Generate New Migration
```bash
python migrate.py generate
```
Creates new migration files based on the current schema definition.

### Show Migration Instructions  
```bash
python migrate.py apply
```
Displays instructions for applying the latest migration in Supabase.

### Validate Migration
```bash
python migrate.py validate
```
Checks if the migration was successfully applied.

### Complete Setup Process
```bash
python migrate.py setup
```
Runs through the entire setup process with step-by-step instructions.

## 🗄️ Database Schema

The migration system creates these tables:

### Core Tables
- **`trading_sessions`**: Trading session metadata and statistics
- **`trades`**: Individual trade records with technical indicators
- **`market_data`**: Historical market data with calculated indicators
- **`error_logs`**: Error tracking and debugging information
- **`performance_metrics`**: Performance analytics and metrics

### System Tables
- **`schema_migrations`**: Version tracking for database schema

## 🔧 Schema Management

### Adding New Tables
1. Edit `migration_system.py` in the `define_schema()` method
2. Add your new table definition
3. Run `python migrate.py generate` to create a new migration
4. Apply the migration in Supabase SQL Editor

### Modifying Existing Tables
1. Update the table definition in `define_schema()`
2. Generate a new migration
3. The system will create ALTER TABLE statements (future enhancement)
4. Apply the migration

### Version Control
- Each migration is numbered sequentially (0001, 0002, etc.)
- The `schema_migrations` table tracks applied migrations
- Local `migrations/version.json` tracks the current version

## 🔍 Features

### REST API Integration
- Uses HTTPS requests instead of direct PostgreSQL connections
- Works around network restrictions in containerized environments
- Automatic fallback to local mode if database unavailable

### Schema Validation
- Checks if tables exist before operations
- Validates schema version consistency
- Provides detailed database health information

### Migration Safety
- All migrations use `IF NOT EXISTS` for safety
- Includes rollback scripts
- Row Level Security (RLS) enabled by default
- Comprehensive indexing for performance

### Development Workflow
1. **Development**: Bot works without database (local mode)
2. **Schema Changes**: Update models, generate migrations
3. **Deployment**: Apply migrations in Supabase
4. **Production**: Bot automatically uses database when available

## 📊 Database Features

### Automatic Logging
When database is available, the bot automatically logs:
- Trading sessions with configuration and statistics
- Individual trades with full technical analysis data
- Market data with calculated indicators
- Errors and debugging information
- Performance metrics for analysis

### Performance Tracking
- Session-based P&L tracking
- Win rate calculations
- Trade analysis and patterns
- Historical performance trends

### Learning Capabilities
- Historical data for strategy optimization
- Error pattern analysis
- Market condition correlation
- Performance metric trending

## 🛠️ Troubleshooting

### Database Not Available
- Check environment variables in `.env`
- Verify Supabase credentials are correct
- Ensure tables are created via migration

### Migration Issues
- Check Supabase SQL Editor for errors
- Verify RLS policies are applied
- Run `migrate.py validate` to diagnose

### Connection Problems
- REST API uses HTTPS (port 443) - should work in most environments
- Direct PostgreSQL connections (port 5432) may be blocked
- Bot gracefully falls back to local mode

## 📁 File Structure

```
migrations/
├── 0001_initial_schema.sql    # Main migration
├── rollback.sql               # Rollback script
├── seed_data.sql             # Test data
└── version.json              # Version tracking

migration_system.py           # Migration generator
migrate.py                   # Migration CLI tool
simple_rest.py              # REST API database client
smart_bot.py               # Enhanced trading bot
```

This system provides a robust, production-ready database migration workflow that works reliably with Supabase while maintaining full development flexibility.