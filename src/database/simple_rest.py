"""
Backward-compatibility shim. All database access now goes through SQLiteDB.
"""
from database.sqlite_db import SQLiteDB, sqlite_db

# Keep the old names so existing imports don't break
SimpleSupabaseREST = SQLiteDB
simple_rest = sqlite_db
