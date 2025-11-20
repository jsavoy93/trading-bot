"""
Migration Management Command
Handles database schema migrations for the trading bot.
"""
import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Add current directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from src.database.migration_system import MigrationGenerator
from src.database.simple_rest import simple_rest

def check_database_status():
    """Check current database status"""
    print("🔍 Checking database status...")
    
    if not simple_rest.is_available():
        print("❌ Database not available. Check your Supabase credentials.")
        return False
    
    db_info = simple_rest.get_database_info()
    
    print(f"✅ Database connection: OK")
    print(f"📊 Tables exist: {'Yes' if db_info['tables_exist'] else 'No'}")
    print(f"📋 Schema version: {db_info['schema_version'] or 'None'}")
    print(f"🗂️  Total sessions: {db_info['total_sessions']}")
    print(f"💼 Total trades: {db_info['total_trades']}")
    
    return db_info['tables_exist']

def generate_migration():
    """Generate new migration files"""
    print("🔧 Generating migration files...")
    
    generator = MigrationGenerator()
    
    # Create the migration
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
    
    return migration_file

def show_migration_instructions(migration_file):
    """Show instructions for running migrations"""
    print("\n" + "="*70)
    print("📋 MIGRATION INSTRUCTIONS")
    print("="*70)
    print("1. Go to https://supabase.com/dashboard")
    print("2. Select your project")
    print("3. Navigate to 'SQL Editor'")
    print("4. Create a new query")
    print(f"5. Copy and paste the contents of: {migration_file}")
    print("6. Run the query")
    print("7. Verify tables are created")
    print("8. Restart your trading bot")
    print("="*70)
    
    # Show the SQL file content for easy copying
    if Path(migration_file).exists():
        print("\n📄 MIGRATION SQL (copy this to Supabase):")
        print("="*70)
        with open(migration_file, 'r') as f:
            content = f.read()
            # Show first few lines and last few lines if too long
            lines = content.split('\n')
            if len(lines) > 50:
                print('\n'.join(lines[:25]))
                print(f"\n... ({len(lines) - 50} lines omitted) ...\n")
                print('\n'.join(lines[-25:]))
            else:
                print(content)
        print("="*70)

def validate_migration():
    """Validate that migration was applied successfully"""
    print("🔍 Validating migration...")
    
    if not simple_rest.is_available():
        print("❌ Cannot validate - database not available")
        return False
    
    db_info = simple_rest.get_database_info()
    
    if not db_info['tables_exist']:
        print("❌ Migration not applied - tables don't exist")
        print("💡 Make sure you ran the SQL script in Supabase SQL Editor")
        return False
    
    schema_version = db_info['schema_version']
    if schema_version is None or schema_version == 0:
        print("⚠️  Tables exist but no schema version found")
        print("💡 This might be OK if you created tables manually")
    else:
        print(f"✅ Migration applied successfully - Schema version: {schema_version}")
    
    print(f"📊 Database ready with {db_info['total_sessions']} sessions and {db_info['total_trades']} trades")
    return True

def main():
    """Main migration command"""
    parser = argparse.ArgumentParser(description="Trading Bot Database Migration Tool")
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Status command
    subparsers.add_parser('status', help='Check database status')
    
    # Generate command
    subparsers.add_parser('generate', help='Generate migration files')
    
    # Apply command (shows instructions)
    subparsers.add_parser('apply', help='Show migration apply instructions')
    
    # Validate command
    subparsers.add_parser('validate', help='Validate migration was applied')
    
    # Full setup command
    subparsers.add_parser('setup', help='Complete setup process')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    print("🗄️  Trading Bot Migration Tool")
    print("="*50)
    
    if args.command == 'status':
        check_database_status()
    
    elif args.command == 'generate':
        migration_file = generate_migration()
        show_migration_instructions(migration_file)
    
    elif args.command == 'apply':
        # Find the latest migration file
        migrations_dir = Path("migrations")
        if migrations_dir.exists():
            migration_files = list(migrations_dir.glob("*.sql"))
            migration_files = [f for f in migration_files if f.name != "rollback.sql" and f.name != "seed_data.sql"]
            if migration_files:
                latest_migration = max(migration_files, key=lambda x: x.name)
                show_migration_instructions(latest_migration)
            else:
                print("❌ No migration files found. Run 'generate' first.")
        else:
            print("❌ No migrations directory found. Run 'generate' first.")
    
    elif args.command == 'validate':
        if validate_migration():
            print("🎉 Database is ready for trading bot!")
        else:
            print("⚠️  Database needs setup. Run 'setup' command.")
    
    elif args.command == 'setup':
        print("🚀 Complete database setup process...")
        
        # Step 1: Check status
        print("\n📋 Step 1: Checking current status")
        tables_exist = check_database_status()
        
        if tables_exist:
            print("✅ Database already set up!")
            return
        
        # Step 2: Generate migration
        print("\n📋 Step 2: Generating migration files")
        migration_file = generate_migration()
        
        # Step 3: Show instructions
        print("\n📋 Step 3: Apply migration")
        show_migration_instructions(migration_file)
        
        print("\n📋 Step 4: After applying the SQL, run:")
        print("python migrate.py validate")

if __name__ == "__main__":
    main()