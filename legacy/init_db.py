#!/usr/bin/env python3
"""
Database initialization and management script.
Run this script to set up the database and apply migrations.
"""
import os
import sys
import logging
from dotenv import load_dotenv

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def main():
    """Initialize database and run migrations"""
    print("🗄️  Trading Bot Database Initialization")
    print("=" * 50)
    
    # Check required environment variables
    required_vars = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "DATABASE_URL"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("\nPlease set the following in your .env file:")
        print("SUPABASE_URL=your_supabase_project_url")
        print("SUPABASE_ANON_KEY=your_supabase_anon_key")
        print("DATABASE_URL=postgresql://postgres:[password]@[host]:[port]/postgres")
        return False
    
    try:
        # Import after environment variables are checked
        from database import init_database, db_manager
        from migrations import run_migrations, get_migration_status
        
        print("🔗 Testing database connection...")
        
        # Test database availability first
        if not db_manager.is_available():
            print("❌ Database connection failed")
            print("\nPossible issues:")
            print("1. Check your internet connection")
            print("2. Verify DATABASE_URL is correct")
            print("3. Ensure Supabase project is running")
            print("4. Check if IP address is whitelisted in Supabase")
            print("\nYou can still run the trading bot without database using:")
            print("python run_bot.py")
            return False
        
        print("✅ Database connection successful")
        
        if not init_database():
            print("❌ Database initialization failed")
            return False
        
        print("\n📊 Checking migration status...")
        status = get_migration_status()
        print(f"Applied migrations: {status['applied_count']}/{status['total_count']}")
        
        if not status['is_up_to_date']:
            print(f"\n🔄 Running {status['total_count'] - status['applied_count']} pending migrations...")
            if run_migrations():
                print("✅ All migrations applied successfully")
            else:
                print("❌ Migration failed")
                return False
        else:
            print("✅ Database is up to date")
        
        print("\n🎉 Database initialization completed successfully!")
        print("\nYour trading bot is now ready to store and learn from trading data.")
        
        return True
        
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        logging.exception("Database initialization error")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)