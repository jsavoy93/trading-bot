# Configuration Management

The trading bot uses **Pydantic** for robust environment variable validation with automatic type checking and clear error messages.

## Quick Start

### 1. Copy the Example Configuration
```bash
cp .env.example .env
```

### 2. Edit .env with Your Credentials
```bash
# Required
ALPACA_API_KEY=your_actual_key_here
ALPACA_API_SECRET=your_actual_secret_here
```

### 3. Validate Your Configuration
```bash
python validate_config.py
```

If validation passes, you'll see a summary of your configuration. If it fails, you'll get clear error messages explaining what's wrong.

## Configuration Architecture

### Centralized Settings (`src/config/settings.py`)

All configuration is managed through a single `TradingBotSettings` class that:

✅ **Validates on startup** - Missing or invalid settings cause immediate failure  
✅ **Provides type safety** - All settings are typed (str, int, bool, etc.)  
✅ **Clear error messages** - Tells you exactly what's wrong and how to fix it  
✅ **Single source of truth** - No more scattered `os.getenv()` calls  
✅ **Helper methods** - Check what's configured (`has_database()`, `has_ai_provider()`)  

### Usage in Code

**Old way (don't do this):**
```python
import os
api_key = os.getenv("ALPACA_API_KEY")  # May be None!
if api_key:  # Runtime check needed
    use_api(api_key)
```

**New way (correct):**
```python
from config.settings import get_settings

settings = get_settings()  # Validates on first call
use_api(settings.ALPACA_API_KEY)  # Guaranteed to exist
```

## Required Settings

These **must** be set or the bot will not start:

- `ALPACA_API_KEY` - Your Alpaca API key (minimum 20 characters)
- `ALPACA_API_SECRET` - Your Alpaca API secret (minimum 20 characters)

## Optional Settings

### Trading Settings
- `ALPACA_BASE_URL` - API endpoint (default: `https://paper-api.alpaca.markets`)
  - Paper trading: `https://paper-api.alpaca.markets` (safe!)
  - Live trading: `https://api.alpaca.markets` (real money!)

### Database (Supabase)
- `SUPABASE_URL` - Your Supabase project URL
- `SUPABASE_ANON_KEY` - Supabase anonymous key
- `SUPABASE_SERVICE_ROLE_KEY` - Supabase service role key

**If not configured:** Bot works fine, but without session tracking or persistent cooldowns.

### AI Providers

At least one AI provider key enables AI-powered analysis:

- `OPENAI_API_KEY` - OpenAI GPT models
- `GOOGLE_API_KEY` - Google Gemini models
- `OPENROUTER_API_KEY` - Access to multiple models
- `MISTRAL_API_KEY` - Mistral AI models
- `COHERE_API_KEY` - Cohere models
- `HUGGINGFACE_API_KEY` - HuggingFace models

**If not configured:** Bot uses pure technical analysis (RSI, SMA, etc.)

### News Integration
- `NEWS_API_KEY` - NewsAPI key for market news

**If not configured:** AI analysis won't include recent news context.

### Application Settings
- `LOG_LEVEL` - Logging verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  - Default: `INFO`
- `ENVIRONMENT` - Application environment (development, production, testing)
  - Default: `development`

## Validation Rules

### Alpaca Credentials
- API key and secret must be at least 20 characters
- Clear error if missing or too short

### Alpaca Base URL
- Must be one of the two valid Alpaca endpoints
- Warns if using live trading mode

### Supabase URL
- Must start with `https://`
- Must contain `.supabase.co`
- Only validated if provided (optional)

### Log Level
- Must be valid Python logging level
- Automatically converts to uppercase

### Environment
- Must be `development`, `production`, or `testing`
- Automatically converts to lowercase

## Helper Methods

```python
from config.settings import get_settings

settings = get_settings()

# Check what's configured
if settings.has_database():
    print("Database enabled")

if settings.has_ai_provider():
    providers = settings.get_configured_ai_providers()
    print(f"AI providers: {providers}")

# Print full configuration summary
settings.print_configuration_summary()
```

## Configuration Summary Example

When the bot starts (or when you run `validate_config.py`), you'll see:

```
================================================================================
⚙️  CONFIGURATION SUMMARY
================================================================================
📊 Alpaca Trading API:
   Base URL: https://paper-api.alpaca.markets
   API Key: PKxxxxxx...x123
   Environment: 🧪 PAPER TRADING

🗄️  Database (Supabase):
   Status: ✅ Configured
   URL: https://yourproject.supabase.co

🤖 AI Providers:
   Status: ✅ Configured (2 providers)
   - OpenAI
   - Google Gemini

📰 News API:
   Status: ✅ Configured

🔧 Application Settings:
   Log Level: INFO
   Environment: development
================================================================================
```

## Error Handling

### Missing Required Variable

```bash
$ python main.py

❌ CONFIGURATION ERROR
================================================================================

1 validation error for TradingBotSettings
ALPACA_API_KEY
  ❌ ALPACA_API_KEY is required but not set. Please add it to your .env file...

Please check your .env file or environment variables.

Required variables:
  - ALPACA_API_KEY
  - ALPACA_API_SECRET
================================================================================
```

### Invalid Variable

```bash
❌ ALPACA_API_KEY appears invalid (too short: 5 chars). Expected at least 20 characters.
```

### Invalid URL

```bash
❌ SUPABASE_URL doesn't look like a valid Supabase URL: http://wrong.com
```

## Migration Guide

If you're updating existing code:

### Before
```python
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("ALPACA_API_KEY")
api_secret = os.getenv("ALPACA_API_SECRET")
```

### After
```python
from config.settings import get_settings

settings = get_settings()
api_key = settings.ALPACA_API_KEY
api_secret = settings.ALPACA_API_SECRET
```

### Benefits
- ✅ Validation happens once at startup
- ✅ Type hints work correctly
- ✅ IDE autocomplete for all settings
- ✅ Impossible to forget to check for None
- ✅ Clear errors if misconfigured

## Testing Configuration

Run the validator:
```bash
python validate_config.py
```

This will:
1. Check if .env file exists
2. Validate all settings
3. Show configuration summary
4. Provide recommendations
5. Exit with code 0 (success) or 1 (failure)

Perfect for CI/CD pipelines or pre-deployment checks!

## Best Practices

1. **Never commit .env** - It's in .gitignore for a reason
2. **Use .env.example** - Document all available settings
3. **Validate early** - Call `get_settings()` at startup
4. **Don't scatter config** - Use the centralized settings object
5. **Fail fast** - Let Pydantic catch config errors before the bot runs

## Troubleshooting

### "No module named 'pydantic'"
```bash
pip install -r requirements.txt
```

### "ValidationError" on startup
Run the validator to see detailed errors:
```bash
python validate_config.py
```

### Settings not updating
Reload settings (useful for testing):
```python
settings = get_settings(reload=True)
```

### Environment variables not loading
Make sure .env is in the project root (same directory as main.py)
