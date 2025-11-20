"""
Simple REST API database integration using direct HTTP requests.
This avoids version conflicts with websockets.
"""
import os
import logging
import json
import requests
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict

class SimpleSupabaseREST:
    """Simple Supabase REST API client using direct HTTP requests"""
    
    def __init__(self):
        # Load environment variables
        from dotenv import load_dotenv
        load_dotenv()
        
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
        self.supabase_service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        self.available = False
        self.current_session_id = None
        
        if self.supabase_url and (self.supabase_anon_key or self.supabase_service_key):
            self.rest_url = f"{self.supabase_url}/rest/v1"
            self.api_key = self.supabase_service_key or self.supabase_anon_key
            self.headers = {
                "apikey": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            
            # Test connection
            self._test_connection()
        else:
            logging.warning("Missing Supabase credentials - database unavailable")
    
    def _test_connection(self):
        """Test connection to Supabase"""
        try:
            # Try to ping the API
            response = requests.get(
                f"{self.rest_url}/trading_sessions?select=id&limit=1",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                self.available = True
                logging.info("✅ Simple Supabase REST API connection successful")
            elif response.status_code == 404:
                logging.warning("⚠️  Tables don't exist yet - need to create them manually")
                self.available = False
            elif response.status_code == 406:
                # This can happen with certain Supabase configurations, but connection is still good
                self.available = True
                logging.info("✅ Simple Supabase REST API connection successful (406 response)")
            else:
                logging.warning(f"⚠️  API connection issue: {response.status_code} - {response.text}")
                self.available = False
                
        except Exception as e:
            logging.warning(f"❌ Failed to connect to Supabase REST API: {e}")
            self.available = False
    
    def is_available(self) -> bool:
        return self.available
    
    def create_session(self, bot_version: str = "2.0.0", 
                      configuration: Dict = None,
                      is_paper_trading: bool = True,
                      notes: str = None) -> Optional[int]:
        """Create a new trading session"""
        if not self.available:
            return None
        
        try:
            data = {
                "session_start": datetime.utcnow().isoformat(),
                "bot_version": bot_version,
                "configuration": configuration,
                "is_paper_trading": is_paper_trading,
                "notes": notes,
                "total_symbols_processed": 0,
                "total_trades_executed": 0,
                "session_pnl": 0.0,
                "error_count": 0
            }
            
            response = requests.post(
                f"{self.rest_url}/trading_sessions",
                headers=self.headers,
                json=data,
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                result = response.json()
                if result and len(result) > 0:
                    session_id = result[0]['id']
                    self.current_session_id = session_id
                    logging.info(f"✅ Created session {session_id}")
                    return session_id
            else:
                logging.error(f"Failed to create session: {response.status_code} {response.text}")
                
        except Exception as e:
            logging.error(f"Exception creating session: {e}")
        
        return None
    
    def log_trade(self, session_id: int, trade_data: Dict) -> bool:
        """Log a trade"""
        if not self.available:
            return False
        
        try:
            response = requests.post(
                f"{self.rest_url}/trades",
                headers=self.headers,
                json=trade_data,
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                logging.debug(f"✅ Logged trade for {trade_data.get('symbol', 'unknown')}")
                return True
            else:
                logging.warning(f"Failed to log trade: {response.status_code}")
                
        except Exception as e:
            logging.warning(f"Exception logging trade: {e}")
        
        return False
    
    def update_session(self, session_id: int, updates: Dict) -> bool:
        """Update session data"""
        if not self.available:
            return False
        
        try:
            response = requests.patch(
                f"{self.rest_url}/trading_sessions?id=eq.{session_id}",
                headers=self.headers,
                json=updates,
                timeout=10
            )
            
            if response.status_code in [200, 204]:
                logging.info(f"✅ Updated session {session_id}")
                return True
            else:
                logging.warning(f"Failed to update session: {response.status_code}")
                
        except Exception as e:
            logging.warning(f"Exception updating session: {e}")
        
        return False
    
    def get_sessions(self, limit: int = 10) -> List[Dict]:
        """Get recent sessions"""
        if not self.available:
            return []
        
        try:
            response = requests.get(
                f"{self.rest_url}/trading_sessions?order=session_start.desc&limit={limit}",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            logging.warning(f"Exception getting sessions: {e}")
        
        return []
    
    def check_schema_version(self) -> Optional[int]:
        """Check current database schema version"""
        if not self.available:
            return None
        
        try:
            response = requests.get(
                f"{self.rest_url}/schema_migrations?order=version.desc&limit=1",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0].get('version')
            
            return 0  # No migrations applied yet
                
        except Exception as e:
            logging.debug(f"Could not check schema version: {e}")
            return None
    
    def get_database_info(self) -> Dict[str, Any]:
        """Get database information and health status"""
        info = {
            'available': self.available,
            'schema_version': None,
            'tables_exist': False,
            'total_sessions': 0,
            'total_trades': 0
        }
        
        if not self.available:
            return info
        
        try:
            # Check schema version
            info['schema_version'] = self.check_schema_version()
            
            # Check if main tables exist and get counts
            session_response = requests.get(
                f"{self.rest_url}/trading_sessions?select=id&limit=1",
                headers=self.headers,
                timeout=5
            )
            
            if session_response.status_code == 200:
                info['tables_exist'] = True
                
                # Get session count
                count_response = requests.get(
                    f"{self.rest_url}/trading_sessions?select=id",
                    headers={**self.headers, 'Prefer': 'count=exact'},
                    timeout=5
                )
                if count_response.status_code == 200:
                    count_header = count_response.headers.get('content-range', '')
                    if '/' in count_header:
                        info['total_sessions'] = int(count_header.split('/')[-1])
                
                # Get trade count
                trade_count_response = requests.get(
                    f"{self.rest_url}/trades?select=id",
                    headers={**self.headers, 'Prefer': 'count=exact'},
                    timeout=5
                )
                if trade_count_response.status_code == 200:
                    count_header = trade_count_response.headers.get('content-range', '')
                    if '/' in count_header:
                        info['total_trades'] = int(count_header.split('/')[-1])
            
        except Exception as e:
            logging.debug(f"Error getting database info: {e}")
        
        return info

# Global instance - created when first imported
simple_rest = None

def get_simple_rest():
    """Get the global SimpleSupabaseREST instance, creating it if needed"""
    global simple_rest
    if simple_rest is None:
        simple_rest = SimpleSupabaseREST()
    return simple_rest

# Initialize on import
simple_rest = SimpleSupabaseREST()