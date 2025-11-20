#!/usr/bin/env python3
"""
Database connection diagnostic tool.
Helps identify and troubleshoot database connectivity issues.
"""
import os
import sys
import socket
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s"
)

def test_network_connectivity(host: str, port: int, timeout: int = 5) -> bool:
    """Test basic network connectivity to host:port"""
    try:
        socket.setdefaulttimeout(timeout)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        print(f"Network test error: {e}")
        return False

def parse_database_url(database_url: str) -> dict:
    """Parse database URL into components"""
    try:
        parsed = urlparse(database_url)
        return {
            "scheme": parsed.scheme,
            "username": parsed.username,
            "password": "***" if parsed.password else None,
            "hostname": parsed.hostname,
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip('/') if parsed.path else None
        }
    except Exception as e:
        return {"error": str(e)}

def main():
    """Run comprehensive database diagnostics"""
    print("🔍 Database Connection Diagnostics")
    print("=" * 50)
    
    # Check environment variables
    print("\n📋 Environment Variables Check:")
    print("-" * 30)
    
    required_vars = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "DATABASE_URL"]
    env_status = {}
    
    for var in required_vars:
        value = os.getenv(var)
        env_status[var] = bool(value)
        status = "✅ Set" if value else "❌ Missing"
        print(f"{var}: {status}")
        if value and var == "DATABASE_URL":
            # Show parsed URL (without password)
            parsed = parse_database_url(value)
            if "error" not in parsed:
                print(f"  Host: {parsed['hostname']}")
                print(f"  Port: {parsed['port']}")
                print(f"  Database: {parsed['database']}")
    
    if not all(env_status.values()):
        print("\n❌ Missing required environment variables")
        print("Please check your .env file")
        return False
    
    # Parse database URL for network testing
    database_url = os.getenv("DATABASE_URL")
    parsed_db = parse_database_url(database_url)
    
    if "error" in parsed_db:
        print(f"\n❌ Invalid DATABASE_URL format: {parsed_db['error']}")
        return False
    
    # Test network connectivity
    print(f"\n🌐 Network Connectivity Test:")
    print("-" * 30)
    
    host = parsed_db["hostname"]
    port = parsed_db["port"]
    
    print(f"Testing connection to {host}:{port}...")
    
    if test_network_connectivity(host, port, timeout=10):
        print("✅ Network connectivity successful")
    else:
        print("❌ Network connectivity failed")
        print("\nPossible issues:")
        print("1. Internet connection problems")
        print("2. Supabase server is down")
        print("3. Firewall blocking connection")
        print("4. DNS resolution issues")
        print("5. IP address not whitelisted in Supabase")
        return False
    
    # Test database connection
    print(f"\n🗄️ Database Connection Test:")
    print("-" * 30)
    
    try:
        # Try importing database components
        from database import db_manager
        
        print("Testing database manager initialization...")
        if db_manager.is_available():
            print("✅ Database manager initialization successful")
        else:
            print("❌ Database manager initialization failed")
            return False
            
        print("Testing actual database connection...")
        if db_manager.test_connection():
            print("✅ Database connection test successful")
        else:
            print("❌ Database connection test failed")
            return False
            
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        print("\nDetailed error information:")
        import traceback
        traceback.print_exc()
        return False
    
    # Test table creation
    print(f"\n📊 Table Creation Test:")
    print("-" * 25)
    
    try:
        from database import init_database
        
        print("Attempting to create database tables...")
        if init_database():
            print("✅ Database tables created/verified successfully")
        else:
            print("❌ Table creation failed")
            return False
            
    except Exception as e:
        print(f"❌ Table creation error: {e}")
        return False
    
    # Final verification
    print(f"\n🎉 All Tests Passed!")
    print("-" * 20)
    print("✅ Environment variables configured")
    print("✅ Network connectivity working")
    print("✅ Database connection successful")
    print("✅ Tables created/verified")
    print("\nYour database is ready for the trading bot!")
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        if not success:
            print(f"\n💡 Suggestions:")
            print("1. Check your internet connection")
            print("2. Verify Supabase project is active")
            print("3. Ensure DATABASE_URL is correct")
            print("4. Try running 'python run_bot.py' to test without database")
        
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("\n\n⏹️ Diagnostics interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)