# AI Agent Configuration Guide

## Overview

The trading bot includes an advanced AI agent that can:
- Research individual stocks with real-time news analysis
- Create comprehensive market summaries
- Perform sentiment analysis on news articles
- Provide intelligent trading recommendations
- Enhance technical analysis with fundamental insights

## Required API Keys

### 1. AI Provider (Choose One)

#### Option A: OpenAI API
- **Purpose**: Powers the core AI analysis and decision-making with GPT models
- **Get API Key**: https://platform.openai.com/api-keys
- **Environment Variables**: `OPENAI_API_KEY`, `AI_PROVIDER=openai`
- **Cost**: Pay-per-use (typically $0.002 per 1K tokens for GPT-3.5-turbo)

#### Option B: Google AI (Gemini)
- **Purpose**: Powers the core AI analysis with Google's Gemini models (Flash/Pro)
- **Get API Key**: https://aistudio.google.com/app/apikey
- **Environment Variables**: `GOOGLE_AI_API_KEY`, `AI_PROVIDER=google`
- **Cost**: Free tier available (15 requests per minute), very cost-effective

### 2. News API (Required for news analysis)
- **Purpose**: Fetches real-time news articles for sentiment analysis
- **Get API Key**: https://newsapi.org/register
- **Environment Variable**: `NEWS_API_KEY`
- **Cost**: Free tier available (1000 requests/day)

### 3. Polygon API (Optional - enhances financial data)
- **Purpose**: Additional financial data and news sources
- **Get API Key**: https://polygon.io/
- **Environment Variable**: `POLYGON_API_KEY`
- **Cost**: Free tier available (5 API calls per minute)

## Setup Instructions

### Step 1: Install AI Dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Configure API Keys
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your API keys:
   ```env
   # AI Agent Credentials - Choose one AI provider
   # Option A: OpenAI
   OPENAI_API_KEY=your-openai-api-key-here
   AI_PROVIDER=openai
   
   # Option B: Google AI (Gemini Flash)
   GOOGLE_AI_API_KEY=your-google-ai-api-key-here
   AI_PROVIDER=google
   
   # Required for news analysis
   NEWS_API_KEY=your-newsapi-key-here
   POLYGON_API_KEY=your-polygon-api-key-here  # Optional
   ```

### Step 3: Validate Configuration
```bash
python scripts/setup_ai.py
```

This script will:
- Test all API connections
- Validate AI agent functionality
- Run a quick analysis test

## AI Features

### 1. Symbol Research
The AI agent can research individual stocks by:
- Fetching recent news articles
- Analyzing sentiment (positive/negative/neutral)
- Extracting key insights and trends
- Providing trading recommendations

### 2. Market Summaries
Creates comprehensive market summaries including:
- Overall market sentiment
- Key news themes
- Notable stock movements
- Risk factors and opportunities

### 3. Enhanced Analysis
The AI enhances technical analysis by:
- Confirming technical signals with fundamental analysis
- Identifying potential catalysts or risks
- Providing context for market movements
- Suggesting position sizing and timing

## Usage

### Enabling AI in the Trading Bot

The AI agent is automatically integrated into the main trading bot. When configured, it will:

1. **Enhance symbol analysis** with AI insights
2. **Generate market summaries** at the start of each trading session
3. **Provide intelligent recommendations** for trade signals

### Manual AI Agent Usage

You can also use the AI agent directly:

```python
from src.analysis.ai_agent import AITradingAgent
import asyncio

async def example():
    agent = AITradingAgent()
    
    # Research a specific symbol
    research = await agent.research_symbol("AAPL")
    print(research)
    
    # Create market summary
    summary = await agent.create_market_summary(["AAPL", "GOOGL", "MSFT"])
    print(summary)

# Run the example
asyncio.run(example())
```

## Configuration Options

### AI Model Selection
The AI agent uses GPT-3.5-turbo by default for cost efficiency. You can modify the model in `src/analysis/ai_agent.py`:

```python
# For higher quality analysis (more expensive)
model="gpt-4"

# For faster responses (lower quality)
model="gpt-3.5-turbo-1106"
```

### News Sources
Configure news sources and filters in the AI agent:

```python
# In AITradingAgent.__init__()
self.news_sources = ['bloomberg', 'reuters', 'financial-times']
self.news_categories = ['business', 'technology']
```

## Cost Management

### OpenAI Costs
- GPT-3.5-turbo: ~$0.002 per 1K tokens
- Typical analysis: 500-1500 tokens per request
- Daily cost for 100 symbols: ~$0.20-0.60

### News API Costs
- Free tier: 1000 requests/day
- Paid plans start at $449/month for higher limits

### Optimization Tips
1. **Batch requests** when possible
2. **Use shorter prompts** for routine analysis
3. **Cache results** for repeated queries
4. **Monitor usage** with API dashboards

## Troubleshooting

### Common Issues

#### "OpenAI API key not found"
- Ensure `OPENAI_API_KEY` is set in your `.env` file
- Check that `.env` file is in the project root
- Verify the API key is valid and has credits

#### "News API rate limit exceeded"
- Free tier is limited to 1000 requests/day
- Consider upgrading to a paid plan
- Implement request throttling in the code

#### "AI analysis is empty or failed"
- Check API key validity
- Verify internet connection
- Check OpenAI service status
- Review error logs for specific issues

### Debug Mode
Enable detailed logging by setting:
```env
LOG_LEVEL=DEBUG
```

This will show detailed AI request/response information.

## Security Notes

### API Key Security
- Never commit API keys to version control
- Use environment variables only
- Rotate keys regularly
- Monitor usage for unexpected spikes

### Data Privacy
- News data is fetched from public sources
- AI prompts may be logged by OpenAI
- No personal trading data is sent to AI services
- Consider data retention policies

## Performance Tips

### Async Operations
The AI agent uses async operations for better performance:
- Multiple API calls run concurrently
- Non-blocking analysis operations
- Efficient resource utilization

### Caching Strategy
Consider implementing caching for:
- Recent news articles (avoid duplicate fetches)
- AI analysis results (cache for 15-30 minutes)
- Market summaries (cache until market close)

## Support

If you encounter issues:
1. Run `python scripts/setup_ai.py` to validate configuration
2. Check the logs for detailed error messages
3. Verify API key validity and quotas
4. Review the troubleshooting section above

The AI features are optional - the trading bot will work normally without them if you prefer to focus on technical analysis only.