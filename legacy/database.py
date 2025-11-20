"""
Database connection and configuration for Supabase integration.
"""
import os
import logging
from typing import Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from supabase import create_client, Client
from dotenv import load_dotenv

from models import Base

# Load environment variables
load_dotenv()

class DatabaseManager:
    """Manages database connections and operations with proper error handling"""
    
    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_ANON_KEY")
        self.supabase_service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # Optional
        self.database_url = os.getenv("DATABASE_URL")
        self.database_url_pooled = os.getenv("DATABASE_URL_POOLED")  # Alternative connection
        
        self.supabase: Optional[Client] = None
        self.engine = None
        self.SessionLocal = None
        self._initialized = False
        self._initialization_error = None
    
    def _check_environment_variables(self):
        """Check if required environment variables are set"""
        # Service role key is optional for basic operations
        required_vars = [
            ("SUPABASE_URL", self.supabase_url),
            ("SUPABASE_ANON_KEY", self.supabase_key),
            ("DATABASE_URL", self.database_url)
        ]
        
        missing_vars = [name for name, value in required_vars if not value]
        
        if missing_vars:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing_vars)}. "
                "Please set them in your .env file."
            )
    
    def _setup_database(self):
        """Initialize database connections with proper error handling"""
        if self._initialized:
            return True
            
        if self._initialization_error:
            raise self._initialization_error
        
        try:
            # Check environment variables
            self._check_environment_variables()
            
            # Initialize Supabase client
            self.supabase = create_client(self.supabase_url, self.supabase_key)
            
            # Try different connection methods
            connection_urls = []
            if self.database_url_pooled:
                connection_urls.append(("pooled", self.database_url_pooled))
            if self.database_url:
                connection_urls.append(("direct", self.database_url))
            
            engine_created = False
            for conn_type, url in connection_urls:
                try:
                    logging.info(f"Trying {conn_type} database connection...")
                    
                    # Initialize SQLAlchemy engine with enhanced connection parameters
                    test_engine = create_engine(
                        url,
                        pool_pre_ping=True,  # Verify connections before use
                        pool_recycle=3600,   # Recycle connections after 1 hour
                        echo=False,  # Set to True for SQL debugging
                        connect_args={
                            "connect_timeout": 10,
                            "application_name": "trading_bot",
                            "options": "-c timezone=UTC"
                        }
                    )
                    
                    # Test the connection
                    with test_engine.connect() as conn:
                        conn.execute(text("SELECT 1"))
                        logging.info(f"✅ {conn_type} connection successful")
                        self.engine = test_engine
                        engine_created = True
                        break
                        
                except Exception as e:
                    logging.warning(f"❌ {conn_type} connection failed: {e}")
                    continue
            
            if not engine_created:
                raise RuntimeError("Failed to establish database connection with any method")
            
            # Create session factory
            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )
            
            self._initialized = True
            logging.info("Database connections initialized successfully")
            return True
            
        except Exception as e:
            self._initialization_error = e
            logging.error(f"Failed to setup database connections: {e}")
            raise
    
    def create_tables(self):
        """Create all tables if they don't exist"""
        if not self._initialized:
            raise RuntimeError("Database not initialized")
            
        try:
            # Test connection first
            if not self.test_connection():
                raise RuntimeError("Database connection test failed")
                
            Base.metadata.create_all(bind=self.engine)
            logging.info("Database tables created/verified successfully")
        except SQLAlchemyError as e:
            logging.error(f"Failed to create database tables: {e}")
            raise
        except Exception as e:
            logging.error(f"Unexpected error creating tables: {e}")
            raise
    
    def get_session(self) -> Session:
        """Get a database session with proper error handling"""
        if not self._initialized:
            self._setup_database()
        
        if not self.SessionLocal:
            raise RuntimeError("Database initialization failed")
        return self.SessionLocal()
    
    def is_available(self) -> bool:
        """Check if database is available without raising exceptions"""
        try:
            if not self._initialized:
                self._setup_database()
            
            # Test actual connectivity
            return self.test_connection()
        except Exception as e:
            logging.debug(f"Database availability check failed: {e}")
            return False
    
    def test_connection(self) -> bool:
        """Test database connectivity"""
        if not self._initialized:
            logging.debug("Database not initialized, cannot test connection")
            return False
            
        try:
            # Use a timeout-limited connection test
            session = self.SessionLocal()
            try:
                # Simple connectivity test
                result = session.execute(text("SELECT 1"))
                result.fetchone()
                session.commit()
                logging.debug("Database connection test successful")
                return True
            finally:
                session.close()
        except Exception as e:
            logging.debug(f"Database connection test failed: {e}")
            return False
    
    def execute_migration(self, migration_sql: str) -> bool:
        """Execute a migration SQL script safely"""
        try:
            with self.get_session() as session:
                session.execute(text(migration_sql))
                session.commit()
                logging.info("Migration executed successfully")
                return True
        except SQLAlchemyError as e:
            logging.error(f"Migration failed: {e}")
            return False

# Global database manager instance
db_manager = DatabaseManager()

def get_db() -> Session:
    """Dependency function to get database session"""
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()

def init_database():
    """Initialize database with all tables"""
    try:
        # First check if database is available
        if not db_manager.is_available():
            logging.error("Database is not available")
            return False
            
        # Create tables
        db_manager.create_tables()
        
        # Final verification
        if db_manager.test_connection():
            logging.info("Database initialization completed successfully")
            return True
        else:
            logging.error("Database initialization failed - final connection test failed")
            return False
    except Exception as e:
        logging.error(f"Database initialization failed: {e}")
        return False