"""
Database migration system for maintaining schema synchronization.
"""
import os
import logging
from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy import text, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from database import db_manager

# Migration tracking table
MigrationBase = declarative_base()

class MigrationHistory(MigrationBase):
    """Track applied migrations"""
    __tablename__ = "migration_history"
    
    id = Column(Integer, primary_key=True)
    version = Column(String(50), nullable=False, unique=True)
    description = Column(String(255), nullable=False)
    applied_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    sql_content = Column(Text, nullable=True)

class MigrationManager:
    """Manages database migrations with version control"""
    
    def __init__(self):
        self.migrations = self._load_migrations()
        self._migration_table_ready = False
    
    def _ensure_migration_table(self):
        """Create migration tracking table if it doesn't exist"""
        if self._migration_table_ready:
            return True
            
        try:
            if not db_manager.is_available():
                raise RuntimeError("Database is not available")
                
            MigrationBase.metadata.create_all(bind=db_manager.engine)
            self._migration_table_ready = True
            logging.info("Migration tracking table ready")
            return True
        except Exception as e:
            logging.error(f"Failed to create migration tracking table: {e}")
            raise
    
    def _load_migrations(self) -> List[Dict[str, Any]]:
        """Load all available migrations in order"""
        return [
            {
                "version": "001_initial_schema",
                "description": "Create initial trading bot schema",
                "sql": """
                -- Initial schema creation is handled by SQLAlchemy models
                -- This migration serves as a baseline marker
                SELECT 1;
                """
            },
            {
                "version": "002_add_indexes",
                "description": "Add performance indexes",
                "sql": """
                -- Add indexes for better query performance
                CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, signal_time);
                CREATE INDEX IF NOT EXISTS idx_trades_session_status ON trades(session_id, status);
                CREATE INDEX IF NOT EXISTS idx_market_data_symbol_time ON market_data(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_error_logs_session_time ON error_logs(session_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_performance_metrics_date ON performance_metrics(date);
                """
            },
            {
                "version": "003_add_learning_features",
                "description": "Add machine learning support columns",
                "sql": """
                -- Add columns for enhanced learning capabilities
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS confidence_score FLOAT;
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS prediction_accuracy FLOAT;
                ALTER TABLE market_data ADD COLUMN IF NOT EXISTS pattern_detected VARCHAR(50);
                ALTER TABLE market_data ADD COLUMN IF NOT EXISTS anomaly_score FLOAT;
                
                -- Create learning patterns table
                CREATE TABLE IF NOT EXISTS learning_patterns (
                    id SERIAL PRIMARY KEY,
                    pattern_name VARCHAR(100) NOT NULL,
                    pattern_type VARCHAR(50) NOT NULL,
                    success_rate FLOAT DEFAULT 0.0,
                    total_occurrences INTEGER DEFAULT 0,
                    successful_trades INTEGER DEFAULT 0,
                    pattern_data JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_learning_patterns_type ON learning_patterns(pattern_type);
                CREATE INDEX IF NOT EXISTS idx_learning_patterns_success ON learning_patterns(success_rate);
                """
            },
            {
                "version": "004_add_risk_management",
                "description": "Add risk management tracking",
                "sql": """
                -- Add risk management columns
                ALTER TABLE trading_sessions ADD COLUMN IF NOT EXISTS max_position_size FLOAT;
                ALTER TABLE trading_sessions ADD COLUMN IF NOT EXISTS risk_tolerance FLOAT;
                ALTER TABLE trading_sessions ADD COLUMN IF NOT EXISTS stop_loss_percentage FLOAT;
                
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS risk_score FLOAT;
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS position_size_ratio FLOAT;
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss_price FLOAT;
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS take_profit_price FLOAT;
                
                -- Create risk events table
                CREATE TABLE IF NOT EXISTS risk_events (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER REFERENCES trading_sessions(id),
                    event_type VARCHAR(50) NOT NULL,
                    symbol VARCHAR(10),
                    severity VARCHAR(20) DEFAULT 'MEDIUM',
                    description TEXT NOT NULL,
                    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP,
                    action_taken TEXT
                );
                
                CREATE INDEX IF NOT EXISTS idx_risk_events_session ON risk_events(session_id);
                CREATE INDEX IF NOT EXISTS idx_risk_events_type ON risk_events(event_type);
                """
            }
        ]
    
    def get_applied_migrations(self) -> List[str]:
        """Get list of already applied migration versions"""
        try:
            if not db_manager.is_available():
                logging.warning("Database not available - no migration history available")
                return []
                
            self._ensure_migration_table()
            
            with db_manager.get_session() as session:
                result = session.execute(
                    text("SELECT version FROM migration_history ORDER BY applied_at")
                )
                return [row[0] for row in result.fetchall()]
        except Exception as e:
            logging.warning(f"Could not fetch migration history: {e}")
            return []
    
    def apply_migration(self, migration: Dict[str, Any]) -> bool:
        """Apply a single migration"""
        try:
            with db_manager.get_session() as session:
                # Execute the migration SQL
                session.execute(text(migration["sql"]))
                
                # Record the migration
                session.execute(
                    text("""
                    INSERT INTO migration_history (version, description, sql_content)
                    VALUES (:version, :description, :sql_content)
                    """),
                    {
                        "version": migration["version"],
                        "description": migration["description"],
                        "sql_content": migration["sql"]
                    }
                )
                
                session.commit()
                logging.info(f"Applied migration: {migration['version']} - {migration['description']}")
                return True
                
        except Exception as e:
            logging.error(f"Failed to apply migration {migration['version']}: {e}")
            return False
    
    def migrate(self) -> bool:
        """Apply all pending migrations"""
        if not db_manager.is_available():
            logging.warning("Database not available - skipping migrations")
            return False
            
        try:
            self._ensure_migration_table()
        except Exception as e:
            logging.error(f"Cannot ensure migration table: {e}")
            return False
            
        applied_migrations = self.get_applied_migrations()
        pending_migrations = [
            migration for migration in self.migrations
            if migration["version"] not in applied_migrations
        ]
        
        if not pending_migrations:
            logging.info("No pending migrations")
            return True
        
        logging.info(f"Applying {len(pending_migrations)} pending migrations")
        
        success = True
        for migration in pending_migrations:
            if not self.apply_migration(migration):
                success = False
                break
        
        if success:
            logging.info("All migrations applied successfully")
        else:
            logging.error("Migration process failed")
        
        return success
    
    def rollback_migration(self, version: str) -> bool:
        """Rollback a specific migration (basic implementation)"""
        # Note: This is a basic implementation
        # In production, you'd want proper rollback scripts
        try:
            with db_manager.get_session() as session:
                session.execute(
                    text("DELETE FROM migration_history WHERE version = :version"),
                    {"version": version}
                )
                session.commit()
                logging.warning(f"Rolled back migration record: {version}")
                logging.warning("Note: Actual schema changes not reversed - manual intervention may be required")
                return True
        except Exception as e:
            logging.error(f"Failed to rollback migration {version}: {e}")
            return False

# Global migration manager
migration_manager = MigrationManager()

def run_migrations():
    """Run all pending migrations"""
    return migration_manager.migrate()

def get_migration_status():
    """Get current migration status"""
    applied = migration_manager.get_applied_migrations()
    total = len(migration_manager.migrations)
    return {
        "applied_count": len(applied),
        "total_count": total,
        "applied_migrations": applied,
        "is_up_to_date": len(applied) == total
    }