"""
Database Migration System for REST API Integration
Generates SQL scripts that can be run in Supabase SQL Editor to keep database in sync.
"""
import os
import json
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Column:
    """Database column definition"""
    name: str
    type: str
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False
    foreign_key: Optional[str] = None
    unique: bool = False

@dataclass
class Table:
    """Database table definition"""
    name: str
    columns: List[Column]
    indexes: List[str] = None
    
    def __post_init__(self):
        if self.indexes is None:
            self.indexes = []

class MigrationGenerator:
    """Generates SQL migration scripts for Supabase"""
    
    def __init__(self, migrations_dir: str = "migrations"):
        self.migrations_dir = Path(migrations_dir)
        self.migrations_dir.mkdir(exist_ok=True)
        self.version_file = self.migrations_dir / "version.json"
        
    def get_current_version(self) -> int:
        """Get current migration version"""
        if self.version_file.exists():
            with open(self.version_file, 'r') as f:
                data = json.load(f)
                return data.get('version', 0)
        return 0
    
    def save_version(self, version: int):
        """Save current migration version"""
        with open(self.version_file, 'w') as f:
            json.dump({
                'version': version,
                'updated_at': datetime.utcnow().isoformat()
            }, f, indent=2)
    
    def define_schema(self) -> List[Table]:
        """Define the current database schema"""
        return [
            Table(
                name="trading_sessions",
                columns=[
                    Column("id", "SERIAL", primary_key=True, nullable=False),
                    Column("session_start", "TIMESTAMP", default="CURRENT_TIMESTAMP", nullable=False),
                    Column("session_end", "TIMESTAMP", nullable=True),
                    Column("bot_version", "VARCHAR(50)", nullable=True),
                    Column("configuration", "JSON", nullable=True),
                    Column("total_symbols_processed", "INTEGER", default="0", nullable=False),
                    Column("total_trades_executed", "INTEGER", default="0", nullable=False),
                    Column("session_pnl", "FLOAT", default="0.0", nullable=False),
                    Column("error_count", "INTEGER", default="0", nullable=False),
                    Column("is_paper_trading", "BOOLEAN", default="true", nullable=False),
                    Column("notes", "TEXT", nullable=True),
                ],
                indexes=[
                    "CREATE INDEX idx_trading_sessions_start ON trading_sessions(session_start DESC);",
                    "CREATE INDEX idx_trading_sessions_paper ON trading_sessions(is_paper_trading);"
                ]
            ),
            Table(
                name="trades",
                columns=[
                    Column("id", "SERIAL", primary_key=True, nullable=False),
                    Column("session_id", "INTEGER", nullable=False, foreign_key="trading_sessions(id)"),
                    Column("alpaca_order_id", "VARCHAR(100)", unique=True, nullable=True),
                    Column("symbol", "VARCHAR(10)", nullable=False),
                    Column("side", "VARCHAR(10)", nullable=False),
                    Column("quantity", "INTEGER", nullable=False),
                    Column("price", "FLOAT", nullable=True),
                    Column("order_price", "FLOAT", nullable=True),
                    Column("signal_time", "TIMESTAMP", nullable=False),
                    Column("order_time", "TIMESTAMP", nullable=False),
                    Column("fill_time", "TIMESTAMP", nullable=True),
                    Column("sma_fast", "FLOAT", nullable=True),
                    Column("sma_slow", "FLOAT", nullable=True),
                    Column("rsi", "FLOAT", nullable=True),
                    Column("signal_strength", "VARCHAR(20)", nullable=True),
                    Column("status", "VARCHAR(20)", default="'PENDING'", nullable=False),
                    Column("pnl", "FLOAT", nullable=True),
                    Column("market_conditions", "JSON", nullable=True),
                ],
                indexes=[
                    "CREATE INDEX idx_trades_session ON trades(session_id);",
                    "CREATE INDEX idx_trades_symbol ON trades(symbol);",
                    "CREATE INDEX idx_trades_status ON trades(status);",
                    "CREATE INDEX idx_trades_signal_time ON trades(signal_time DESC);",
                    "CREATE UNIQUE INDEX idx_trades_alpaca_order ON trades(alpaca_order_id) WHERE alpaca_order_id IS NOT NULL;"
                ]
            ),
            Table(
                name="market_data",
                columns=[
                    Column("id", "SERIAL", primary_key=True, nullable=False),
                    Column("symbol", "VARCHAR(10)", nullable=False),
                    Column("timestamp", "TIMESTAMP", nullable=False),
                    Column("open_price", "FLOAT", nullable=False),
                    Column("high_price", "FLOAT", nullable=False),
                    Column("low_price", "FLOAT", nullable=False),
                    Column("close_price", "FLOAT", nullable=False),
                    Column("volume", "BIGINT", nullable=False),
                    Column("sma_fast", "FLOAT", nullable=True),
                    Column("sma_slow", "FLOAT", nullable=True),
                    Column("rsi", "FLOAT", nullable=True),
                    Column("created_at", "TIMESTAMP", default="CURRENT_TIMESTAMP", nullable=False),
                ],
                indexes=[
                    "CREATE UNIQUE INDEX idx_market_data_symbol_time ON market_data(symbol, timestamp);",
                    "CREATE INDEX idx_market_data_symbol ON market_data(symbol);",
                    "CREATE INDEX idx_market_data_timestamp ON market_data(timestamp DESC);"
                ]
            ),
            Table(
                name="error_logs",
                columns=[
                    Column("id", "SERIAL", primary_key=True, nullable=False),
                    Column("session_id", "INTEGER", nullable=True, foreign_key="trading_sessions(id)"),
                    Column("timestamp", "TIMESTAMP", default="CURRENT_TIMESTAMP", nullable=False),
                    Column("error_type", "VARCHAR(50)", nullable=False),
                    Column("error_message", "TEXT", nullable=False),
                    Column("symbol", "VARCHAR(10)", nullable=True),
                    Column("context", "JSON", nullable=True),
                    Column("stack_trace", "TEXT", nullable=True),
                ],
                indexes=[
                    "CREATE INDEX idx_error_logs_session ON error_logs(session_id);",
                    "CREATE INDEX idx_error_logs_timestamp ON error_logs(timestamp DESC);",
                    "CREATE INDEX idx_error_logs_type ON error_logs(error_type);"
                ]
            ),
            Table(
                name="performance_metrics",
                columns=[
                    Column("id", "SERIAL", primary_key=True, nullable=False),
                    Column("session_id", "INTEGER", nullable=False, foreign_key="trading_sessions(id)"),
                    Column("metric_date", "DATE", nullable=False),
                    Column("total_trades", "INTEGER", default="0", nullable=False),
                    Column("profitable_trades", "INTEGER", default="0", nullable=False),
                    Column("daily_pnl", "FLOAT", default="0.0", nullable=False),
                    Column("win_rate", "FLOAT", nullable=True),
                    Column("avg_profit_per_trade", "FLOAT", nullable=True),
                    Column("max_drawdown", "FLOAT", nullable=True),
                    Column("sharpe_ratio", "FLOAT", nullable=True),
                    Column("volatility", "FLOAT", nullable=True),
                    Column("created_at", "TIMESTAMP", default="CURRENT_TIMESTAMP", nullable=False),
                ],
                indexes=[
                    "CREATE UNIQUE INDEX idx_performance_session_date ON performance_metrics(session_id, metric_date);",
                    "CREATE INDEX idx_performance_date ON performance_metrics(metric_date DESC);"
                ]
            )
        ]
    
    def generate_create_table_sql(self, table: Table) -> str:
        """Generate CREATE TABLE SQL"""
        lines = [f"CREATE TABLE IF NOT EXISTS {table.name} ("]
        
        # Add columns
        column_definitions = []
        for col in table.columns:
            col_def = f"    {col.name} {col.type}"
            
            if col.primary_key:
                col_def += " PRIMARY KEY"
            elif not col.nullable:
                col_def += " NOT NULL"
            
            if col.default:
                col_def += f" DEFAULT {col.default}"
            
            if col.unique and not col.primary_key:
                col_def += " UNIQUE"
            
            column_definitions.append(col_def)
        
        # Add foreign key constraints
        for col in table.columns:
            if col.foreign_key:
                column_definitions.append(f"    FOREIGN KEY ({col.name}) REFERENCES {col.foreign_key}")
        
        lines.append(",\n".join(column_definitions))
        lines.append(");")
        
        return "\n".join(lines)
    
    def generate_migration_sql(self, version: int, tables: List[Table]) -> str:
        """Generate complete migration SQL"""
        sql_parts = []
        
        # Header
        sql_parts.append(f"-- Migration {version:04d}")
        sql_parts.append(f"-- Generated on {datetime.utcnow().isoformat()}")
        sql_parts.append("-- Run this script in Supabase SQL Editor")
        sql_parts.append("")
        
        # Create tables
        for table in tables:
            sql_parts.append(f"-- Create {table.name} table")
            sql_parts.append(self.generate_create_table_sql(table))
            sql_parts.append("")
            
            # Add indexes
            if table.indexes:
                sql_parts.append(f"-- Indexes for {table.name}")
                for index in table.indexes:
                    sql_parts.append(index)
                sql_parts.append("")
        
        # Enable RLS and create policies
        sql_parts.append("-- Enable Row Level Security")
        for table in tables:
            sql_parts.append(f"ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;")
        sql_parts.append("")
        
        sql_parts.append("-- Create policies (allow all operations for authenticated users)")
        for table in tables:
            sql_parts.append(f'CREATE POLICY "Allow all operations" ON {table.name} FOR ALL USING (true);')
        sql_parts.append("")
        
        # Create version tracking table and insert version
        sql_parts.append("-- Version tracking")
        sql_parts.append("""CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);""")
        sql_parts.append("")
        sql_parts.append("ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;")
        sql_parts.append('CREATE POLICY "Allow all operations" ON schema_migrations FOR ALL USING (true);')
        sql_parts.append("")
        sql_parts.append(f"INSERT INTO schema_migrations (version, description) VALUES ({version}, 'Initial schema creation') ON CONFLICT (version) DO NOTHING;")
        
        return "\n".join(sql_parts)
    
    def create_migration(self, description: str = "Schema update") -> str:
        """Create a new migration"""
        current_version = self.get_current_version()
        new_version = current_version + 1
        
        tables = self.define_schema()
        sql_content = self.generate_migration_sql(new_version, tables)
        
        # Save migration file
        migration_file = self.migrations_dir / f"{new_version:04d}_{description.lower().replace(' ', '_')}.sql"
        with open(migration_file, 'w') as f:
            f.write(sql_content)
        
        # Update version
        self.save_version(new_version)
        
        return str(migration_file)
    
    def create_rollback_sql(self, tables: List[Table]) -> str:
        """Generate rollback SQL"""
        sql_parts = []
        sql_parts.append("-- Rollback script")
        sql_parts.append(f"-- Generated on {datetime.utcnow().isoformat()}")
        sql_parts.append("")
        
        # Drop tables in reverse order (to handle foreign keys)
        for table in reversed(tables):
            sql_parts.append(f"DROP TABLE IF EXISTS {table.name} CASCADE;")
        
        sql_parts.append("DROP TABLE IF EXISTS schema_migrations CASCADE;")
        
        return "\n".join(sql_parts)
    
    def generate_seed_data(self) -> str:
        """Generate seed data SQL"""
        return """-- Seed data for testing
-- Run after initial migration

-- Insert test trading session
INSERT INTO trading_sessions (bot_version, configuration, is_paper_trading, notes) 
VALUES (
    '2.1.0',
    '{"sma_fast": 10, "sma_slow": 30, "rsi_period": 14, "trade_amount": 1000}',
    true,
    'Test session for migration validation'
) ON CONFLICT DO NOTHING;

-- You can add more seed data here as needed
"""

def main():
    """Generate migration files"""
    print("🔧 Database Migration Generator")
    print("=" * 50)
    
    generator = MigrationGenerator()
    
    # Create initial migration
    migration_file = generator.create_migration("initial_schema")
    print(f"✅ Generated migration: {migration_file}")
    
    # Create rollback script
    tables = generator.define_schema()
    rollback_sql = generator.create_rollback_sql(tables)
    rollback_file = generator.migrations_dir / "rollback.sql"
    with open(rollback_file, 'w') as f:
        f.write(rollback_sql)
    print(f"✅ Generated rollback: {rollback_file}")
    
    # Create seed data
    seed_sql = generator.generate_seed_data()
    seed_file = generator.migrations_dir / "seed_data.sql"
    with open(seed_file, 'w') as f:
        f.write(seed_sql)
    print(f"✅ Generated seed data: {seed_file}")
    
    print("\n📋 Next Steps:")
    print("1. Go to https://supabase.com/dashboard")
    print("2. Navigate to SQL Editor")
    print(f"3. Copy and run the SQL from: {migration_file}")
    print(f"4. Optionally run seed data from: {seed_file}")
    print("5. Restart your trading bot to use the database")
    
    print(f"\n🔄 Current migration version: {generator.get_current_version()}")

if __name__ == "__main__":
    main()