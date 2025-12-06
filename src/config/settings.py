"""
Centralized configuration management with environment variable validation.
Uses Pydantic for type safety and validation.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional
import os
from pathlib import Path


class TradingBotSettings(BaseSettings):
    """
    Trading bot configuration with automatic validation.
    
    All settings are loaded from environment variables or .env file.
    Missing required variables will cause immediate failure with clear error messages.
    """
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=True,
        extra='ignore'  # Ignore extra fields in .env that aren't defined
    )
    
    # Alpaca Trading API (REQUIRED)
    ALPACA_API_KEY: str = Field(..., description="Alpaca API key for trading")
    ALPACA_API_SECRET: str = Field(..., description="Alpaca API secret for trading")
    ALPACA_BASE_URL: str = Field(
        default="https://paper-api.alpaca.markets",
        description="Alpaca API base URL (paper or live trading)"
    )
    
    # Supabase Database (OPTIONAL)
    SUPABASE_URL: Optional[str] = Field(None, description="Supabase project URL")
    SUPABASE_ANON_KEY: Optional[str] = Field(None, description="Supabase anonymous key")
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = Field(None, description="Supabase service role key")
    
    # AI Provider API Keys (OPTIONAL - bot works without AI)
    OPENAI_API_KEY: Optional[str] = Field(None, description="OpenAI API key for GPT models")
    GOOGLE_API_KEY: Optional[str] = Field(None, description="Google API key for Gemini")
    HUGGINGFACE_API_KEY: Optional[str] = Field(None, description="HuggingFace API key")
    OPENROUTER_API_KEY: Optional[str] = Field(None, description="OpenRouter API key")
    MISTRAL_API_KEY: Optional[str] = Field(None, description="Mistral AI API key")
    COHERE_API_KEY: Optional[str] = Field(None, description="Cohere API key")
    
    # News API (OPTIONAL)
    NEWS_API_KEY: Optional[str] = Field(None, description="NewsAPI key for market news")
    
    # Application Settings
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    ENVIRONMENT: str = Field(default="development", description="Environment (development/production)")
    
    @field_validator('ALPACA_API_KEY')
    @classmethod
    def validate_alpaca_key(cls, v):
        """Validate Alpaca API key format"""
        if not v:
            raise ValueError(
                "❌ ALPACA_API_KEY is required but not set. "
                "Please add it to your .env file or environment variables."
            )
        if len(v) < 20:
            raise ValueError(
                f"❌ ALPACA_API_KEY appears invalid (too short: {len(v)} chars). "
                "Expected at least 20 characters."
            )
        return v
    
    @field_validator('ALPACA_API_SECRET')
    @classmethod
    def validate_alpaca_secret(cls, v):
        """Validate Alpaca API secret format"""
        if not v:
            raise ValueError(
                "❌ ALPACA_API_SECRET is required but not set. "
                "Please add it to your .env file or environment variables."
            )
        if len(v) < 20:
            raise ValueError(
                f"❌ ALPACA_API_SECRET appears invalid (too short: {len(v)} chars). "
                "Expected at least 20 characters."
            )
        return v
    
    @field_validator('ALPACA_BASE_URL')
    @classmethod
    def validate_alpaca_url(cls, v):
        """Validate Alpaca base URL"""
        valid_urls = [
            "https://paper-api.alpaca.markets",
            "https://api.alpaca.markets"
        ]
        if v not in valid_urls:
            raise ValueError(
                f"❌ ALPACA_BASE_URL must be one of {valid_urls}, got: {v}"
            )
        if "paper" in v.lower():
            print("ℹ️  Using PAPER TRADING mode (safe for testing)")
        else:
            print("⚠️  WARNING: Using LIVE TRADING mode - real money at risk!")
        return v
    
    @field_validator('SUPABASE_URL')
    @classmethod
    def validate_supabase_url(cls, v):
        """Validate Supabase URL format if provided"""
        if v and not v.startswith('https://'):
            raise ValueError(
                f"❌ SUPABASE_URL must start with 'https://', got: {v}"
            )
        if v and '.supabase.co' not in v:
            raise ValueError(
                f"❌ SUPABASE_URL doesn't look like a valid Supabase URL: {v}"
            )
        return v
    
    @field_validator('LOG_LEVEL')
    @classmethod
    def validate_log_level(cls, v):
        """Validate logging level"""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(
                f"❌ LOG_LEVEL must be one of {valid_levels}, got: {v}"
            )
        return v_upper
    
    @field_validator('ENVIRONMENT')
    @classmethod
    def validate_environment(cls, v):
        """Validate environment setting"""
        valid_envs = ['development', 'production', 'testing']
        v_lower = v.lower()
        if v_lower not in valid_envs:
            raise ValueError(
                f"❌ ENVIRONMENT must be one of {valid_envs}, got: {v}"
            )
        return v_lower
    
    def has_database(self) -> bool:
        """Check if database is configured"""
        return bool(self.SUPABASE_URL and (self.SUPABASE_ANON_KEY or self.SUPABASE_SERVICE_ROLE_KEY))
    
    def has_ai_provider(self) -> bool:
        """Check if any AI provider is configured"""
        return any([
            self.OPENAI_API_KEY,
            self.GOOGLE_API_KEY,
            self.HUGGINGFACE_API_KEY,
            self.OPENROUTER_API_KEY,
            self.MISTRAL_API_KEY,
            self.COHERE_API_KEY
        ])
    
    def get_configured_ai_providers(self) -> list:
        """Get list of configured AI providers"""
        providers = []
        if self.OPENAI_API_KEY:
            providers.append('OpenAI')
        if self.GOOGLE_API_KEY:
            providers.append('Google Gemini')
        if self.HUGGINGFACE_API_KEY:
            providers.append('HuggingFace')
        if self.OPENROUTER_API_KEY:
            providers.append('OpenRouter')
        if self.MISTRAL_API_KEY:
            providers.append('Mistral')
        if self.COHERE_API_KEY:
            providers.append('Cohere')
        return providers
    
    def print_configuration_summary(self):
        """Print a summary of the loaded configuration"""
        print("\n" + "="*80)
        print("⚙️  CONFIGURATION SUMMARY")
        print("="*80)
        
        # Alpaca Trading
        print(f"📊 Alpaca Trading API:")
        print(f"   Base URL: {self.ALPACA_BASE_URL}")
        print(f"   API Key: {self.ALPACA_API_KEY[:8]}...{self.ALPACA_API_KEY[-4:]}")
        print(f"   Environment: {'🧪 PAPER TRADING' if 'paper' in self.ALPACA_BASE_URL.lower() else '💰 LIVE TRADING'}")
        
        # Database
        print(f"\n🗄️  Database (Supabase):")
        if self.has_database():
            print(f"   Status: ✅ Configured")
            print(f"   URL: {self.SUPABASE_URL}")
        else:
            print(f"   Status: ⚠️  Not configured (optional)")
        
        # AI Providers
        print(f"\n🤖 AI Providers:")
        if self.has_ai_provider():
            providers = self.get_configured_ai_providers()
            print(f"   Status: ✅ Configured ({len(providers)} providers)")
            for provider in providers:
                print(f"   - {provider}")
        else:
            print(f"   Status: ⚠️  Not configured (bot will use technical analysis only)")
        
        # News API
        print(f"\n📰 News API:")
        if self.NEWS_API_KEY:
            print(f"   Status: ✅ Configured")
        else:
            print(f"   Status: ⚠️  Not configured (optional)")
        
        # Application Settings
        print(f"\n🔧 Application Settings:")
        print(f"   Log Level: {self.LOG_LEVEL}")
        print(f"   Environment: {self.ENVIRONMENT}")
        
        print("="*80 + "\n")


# Global settings instance
_settings: Optional[TradingBotSettings] = None


def get_settings(reload: bool = False) -> TradingBotSettings:
    """
    Get the global settings instance.
    
    Args:
        reload: Force reload settings from environment
        
    Returns:
        TradingBotSettings instance
        
    Raises:
        ValidationError: If required settings are missing or invalid
    """
    global _settings
    
    if _settings is None or reload:
        try:
            _settings = TradingBotSettings()
        except Exception as e:
            print("\n" + "="*80)
            print("❌ CONFIGURATION ERROR")
            print("="*80)
            print(f"\n{str(e)}\n")
            print("Please check your .env file or environment variables.")
            print("\nRequired variables:")
            print("  - ALPACA_API_KEY")
            print("  - ALPACA_API_SECRET")
            print("\nOptional variables:")
            print("  - SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY")
            print("  - OPENAI_API_KEY, GOOGLE_API_KEY, etc.")
            print("="*80 + "\n")
            raise
    
    return _settings


def validate_settings() -> bool:
    """
    Validate settings and print summary.
    
    Returns:
        True if settings are valid
        
    Raises:
        ValidationError: If settings are invalid
    """
    try:
        settings = get_settings()
        settings.print_configuration_summary()
        return True
    except Exception:
        return False


# Convenience function for backward compatibility
def load_and_validate_env():
    """Load and validate environment variables (backward compatible)"""
    return validate_settings()
