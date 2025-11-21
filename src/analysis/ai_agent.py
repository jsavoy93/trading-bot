"""
AI Agent System for Trading Bot
Provides intelligent analysis, research, and decision-making capabilities.
"""
import os
import logging
import json
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
import requests
from dotenv import load_dotenv

@dataclass
class ArticleSummary:
    """Article summary data structure"""
    title: str
    url: str
    summary: str
    sentiment: str  # positive, negative, neutral
    relevance_score: float
    key_points: List[str]
    trading_signals: List[str]
    timestamp: datetime

@dataclass
class TradingInsight:
    """Trading insight from AI analysis"""
    symbol: str
    recommendation: str  # buy, sell, hold
    confidence: float
    reasoning: str
    risk_factors: List[str]
    price_targets: Dict[str, float]
    time_horizon: str
    sources: List[str]

class AITradingAgent:
    """AI-powered trading research and analysis agent"""
    
    def __init__(self):
        load_dotenv()
        
        # AI API Configuration
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.google_api_key = os.getenv("GOOGLE_AI_API_KEY")
        self.ai_provider = os.getenv("AI_PROVIDER", "openai").lower()
        self.news_api_key = os.getenv("NEWS_API_KEY") 
        self.polygon_api_key = os.getenv("POLYGON_API_KEY")
        
        # Agent configuration
        self.max_articles_per_search = 10
        self.sentiment_threshold = 0.6
        self.confidence_threshold = 0.7
        
        # Initialize AI client
        self.ai_client = None
        self.is_configured = False
        self._init_ai_client()
        
        # Initialize components
        self._setup_logging()
    
    def _init_ai_client(self):
        """Initialize AI client based on provider preference"""
        if self.ai_provider == "google" and self.google_api_key:
            self._init_google_ai()
        elif self.ai_provider == "openai" and self.openai_api_key:
            self._init_openai()
        elif self.openai_api_key:  # Fallback to OpenAI
            self.ai_provider = "openai"
            self._init_openai()
        elif self.google_api_key:  # Fallback to Google
            self.ai_provider = "google"
            self._init_google_ai()
        
        self.is_configured = bool(self.ai_client)
    
    def _init_openai(self):
        """Initialize OpenAI client"""
        try:
            import openai
            self.ai_client = openai.OpenAI(api_key=self.openai_api_key)
            logging.info("✅ AI Agent initialized with OpenAI GPT")
        except ImportError:
            logging.error("❌ OpenAI package not available")
    
    def _init_google_ai(self):
        """Initialize Google AI client"""
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.google_api_key)
            self.ai_client = genai.GenerativeModel('gemini-pro')
            logging.info("✅ AI Agent initialized with Google Gemini Flash")
        except ImportError:
            logging.error("❌ Google GenerativeAI package not available")
    
    def _call_ai_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Synchronous AI API call for both providers"""
        if not self.ai_client:
            return None
            
        try:
            if self.ai_provider == "openai":
                return self._call_openai_sync(prompt, max_tokens)
            elif self.ai_provider == "google":
                return self._call_google_sync(prompt, max_tokens)
            else:
                logging.error(f"Unknown AI provider: {self.ai_provider}")
                return None
                
        except Exception as e:
            logging.error(f"AI API call failed: {e}")
            return None
    
    def _call_openai_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call OpenAI GPT API synchronously"""
        response = self.ai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional financial analyst providing concise, actionable insights in JSON format."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=max_tokens,
            temperature=0.3
        )
        
        return response.choices[0].message.content.strip()
    
    def _call_google_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call Google Gemini Flash API synchronously"""
        system_prompt = "You are a professional financial analyst providing concise, actionable insights in JSON format."
        full_prompt = f"{system_prompt}\n\n{prompt}"
        
        response = self.ai_client.generate_content(
            full_prompt,
            generation_config={
                'max_output_tokens': max_tokens,
                'temperature': 0.3,
            }
        )
        return response.text.strip() if response.text else None
    
    async def _call_ai_async(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Asynchronous AI API call for both providers"""
        if not self.ai_client:
            return None
            
        try:
            if self.ai_provider == "openai":
                return await self._call_openai_async(prompt, max_tokens)
            elif self.ai_provider == "google":
                return await self._call_google_async(prompt, max_tokens)
            else:
                logging.error(f"Unknown AI provider: {self.ai_provider}")
                return None
                
        except Exception as e:
            logging.error(f"AI API call failed: {e}")
            return None
    
    async def _call_openai_async(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call OpenAI GPT API asynchronously"""
        # For async, we need to use AsyncOpenAI
        try:
            import openai
            async_client = openai.AsyncOpenAI(api_key=self.openai_api_key)
            response = await async_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a professional financial analyst providing concise, actionable insights in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.3
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            logging.error(f"OpenAI async call failed: {e}")
            return None
    
    async def _call_google_async(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call Google Gemini Flash API asynchronously"""
        import asyncio
        
        def sync_generate():
            system_prompt = "You are a professional financial analyst providing concise, actionable insights in JSON format."
            full_prompt = f"{system_prompt}\n\n{prompt}"
            
            response = self.ai_client.generate_content(
                full_prompt,
                generation_config={
                    'max_output_tokens': max_tokens,
                    'temperature': 0.3,
                }
            )
            return response.text.strip() if response.text else None
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, sync_generate)
        return result
    
    def _setup_logging(self):
        """Setup AI agent logging"""
        self.logger = logging.getLogger("AITradingAgent")
        handler = logging.FileHandler("ai_agent.log")
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
    
    async def research_symbol(self, symbol: str, lookback_days: int = 7) -> Dict[str, Any]:
        """Comprehensive research on a trading symbol"""
        self.logger.info(f"🔍 Starting research on {symbol}")
        
        research_results = {
            "symbol": symbol,
            "research_date": datetime.now().isoformat(),
            "news_analysis": None,
            "sentiment_analysis": None,
            "technical_insights": None,
            "ai_recommendation": None,
            "risk_assessment": None
        }
        
        try:
            # 1. Gather news and articles
            articles = await self._fetch_news(symbol, lookback_days)
            
            # 2. Analyze sentiment from news
            sentiment_analysis = await self._analyze_sentiment(articles)
            
            # 3. Get technical analysis insights
            technical_insights = await self._get_technical_insights(symbol)
            
            # 4. Generate AI recommendation
            ai_recommendation = await self._generate_recommendation(
                symbol, articles, sentiment_analysis, technical_insights
            )
            
            # 5. Assess risks
            risk_assessment = await self._assess_risks(symbol, articles, technical_insights)
            
            research_results.update({
                "news_analysis": {"articles": len(articles), "summaries": [self._summarize_article(a) for a in articles[:5]]},
                "sentiment_analysis": sentiment_analysis,
                "technical_insights": technical_insights,
                "ai_recommendation": ai_recommendation,
                "risk_assessment": risk_assessment
            })
            
            self.logger.info(f"✅ Research completed for {symbol}")
            
        except Exception as e:
            self.logger.error(f"❌ Research failed for {symbol}: {e}")
            research_results["error"] = str(e)
        
        return research_results
    
    async def _fetch_news(self, symbol: str, days: int) -> List[Dict]:
        """Fetch recent news articles about the symbol"""
        articles = []
        
        try:
            # News API search
            if self.news_api_key:
                url = f"https://newsapi.org/v2/everything"
                params = {
                    "q": f"{symbol} stock OR {symbol} earnings OR {symbol} company",
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": self.max_articles_per_search,
                    "from": (datetime.now() - timedelta(days=days)).isoformat(),
                    "apiKey": self.news_api_key
                }
                
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    articles.extend(data.get("articles", []))
            
            # Polygon news (if available)
            if self.polygon_api_key:
                url = f"https://api.polygon.io/v2/reference/news"
                params = {
                    "ticker": symbol,
                    "limit": 10,
                    "apikey": self.polygon_api_key
                }
                
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    articles.extend(data.get("results", []))
            
            self.logger.info(f"📰 Found {len(articles)} articles for {symbol}")
            
        except Exception as e:
            self.logger.warning(f"⚠️ News fetch failed for {symbol}: {e}")
        
        return articles[:self.max_articles_per_search]
    
    def _summarize_article(self, article: Dict) -> ArticleSummary:
        """Create a summary of an article with AI enhancement if available"""
        try:
            title = article.get("title", "")
            url = article.get("url", "")
            content = article.get("description", "") or article.get("content", "")
            
            if self.is_configured and self.ai_client:
                # Use AI for enhanced analysis
                return self._ai_summarize_article(title, content, url)
            else:
                # Fallback to simple keyword-based analysis
                return self._simple_summarize_article(title, content, url)
            
        except Exception as e:
            self.logger.warning(f"Article summary failed: {e}")
            return None
    
    def _simple_summarize_article(self, title: str, content: str, url: str) -> ArticleSummary:
        """Simple keyword-based article analysis"""
        positive_words = ["bullish", "growth", "profit", "beat", "strong", "up", "gain", "buy"]
        negative_words = ["bearish", "loss", "miss", "weak", "down", "fall", "sell", "risk"]
        
        content_lower = content.lower()
        pos_score = sum(1 for word in positive_words if word in content_lower)
        neg_score = sum(1 for word in negative_words if word in content_lower)
        
        if pos_score > neg_score:
            sentiment = "positive"
        elif neg_score > pos_score:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        
        return ArticleSummary(
            title=title,
            url=url,
            summary=content[:200] + "..." if len(content) > 200 else content,
            sentiment=sentiment,
            relevance_score=0.8,
            key_points=[title],
            trading_signals=[],
            timestamp=datetime.now()
        )
    
    def _ai_summarize_article(self, title: str, content: str, url: str) -> ArticleSummary:
        """AI-powered article analysis"""
        try:
            prompt = f"""
            Analyze this financial news article and provide:
            1. Sentiment: positive, negative, or neutral
            2. Key trading points (max 3)  
            3. Relevance score (0-1)
            4. Brief summary (max 100 words)
            
            Title: {title}
            Content: {content[:500]}
            
            Respond in JSON format:
            {{
                "sentiment": "positive/negative/neutral",
                "key_points": ["point1", "point2", "point3"],
                "relevance_score": 0.8,
                "summary": "brief summary",
                "trading_signals": ["signal1", "signal2"]
            }}
            """
            
            response = self._call_ai_sync(prompt)
            if response:
                try:
                    import json
                    ai_analysis = json.loads(response)
                    
                    return ArticleSummary(
                        title=title,
                        url=url,
                        summary=ai_analysis.get("summary", content[:200]),
                        sentiment=ai_analysis.get("sentiment", "neutral"),
                        relevance_score=ai_analysis.get("relevance_score", 0.5),
                        key_points=ai_analysis.get("key_points", [title]),
                        trading_signals=ai_analysis.get("trading_signals", []),
                        timestamp=datetime.now()
                    )
                except json.JSONDecodeError:
                    pass
            
            # Fallback to simple analysis if AI fails
            return self._simple_summarize_article(title, content, url)
            
        except Exception as e:
            self.logger.warning(f"AI article analysis failed: {e}")
            return self._simple_summarize_article(title, content, url)
    
    async def _analyze_sentiment(self, articles: List[Dict]) -> Dict[str, Any]:
        """Analyze overall sentiment from articles"""
        if not articles:
            return {"overall_sentiment": "neutral", "confidence": 0.0, "article_count": 0}
        
        sentiments = []
        for article in articles:
            summary = self._summarize_article(article)
            if summary:
                sentiments.append(summary.sentiment)
        
        if not sentiments:
            return {"overall_sentiment": "neutral", "confidence": 0.0, "article_count": 0}
        
        # Calculate overall sentiment
        positive_count = sentiments.count("positive")
        negative_count = sentiments.count("negative")
        neutral_count = sentiments.count("neutral")
        
        total = len(sentiments)
        
        if positive_count > negative_count and positive_count > neutral_count:
            overall = "positive"
            confidence = positive_count / total
        elif negative_count > positive_count and negative_count > neutral_count:
            overall = "negative"
            confidence = negative_count / total
        else:
            overall = "neutral"
            confidence = neutral_count / total
        
        return {
            "overall_sentiment": overall,
            "confidence": confidence,
            "article_count": total,
            "sentiment_breakdown": {
                "positive": positive_count,
                "negative": negative_count,
                "neutral": neutral_count
            }
        }
    
    async def _get_technical_insights(self, symbol: str) -> Dict[str, Any]:
        """Get technical analysis insights"""
        # This would integrate with your existing technical analysis
        # For now, return placeholder data
        return {
            "trend": "bullish",
            "support_level": 100.0,
            "resistance_level": 120.0,
            "rsi": 45.0,
            "sma_signal": "buy",
            "volume_trend": "increasing"
        }
    
    async def _generate_recommendation(self, symbol: str, articles: List[Dict], 
                                     sentiment: Dict, technical: Dict) -> TradingInsight:
        """Generate AI-powered trading recommendation"""
        
        if self.is_configured and self.ai_client:
            return await self._ai_generate_recommendation(symbol, articles, sentiment, technical)
        else:
            return self._simple_generate_recommendation(symbol, articles, sentiment, technical)
    
    def _simple_generate_recommendation(self, symbol: str, articles: List[Dict], 
                                      sentiment: Dict, technical: Dict) -> TradingInsight:
        """Simple rule-based recommendation"""
        recommendation = "hold"
        confidence = 0.5
        reasoning = "Neutral signals from multiple factors"
        
        # Factor in sentiment
        if sentiment["overall_sentiment"] == "positive" and sentiment["confidence"] > 0.6:
            recommendation = "buy"
            confidence += 0.2
            reasoning = f"Positive sentiment ({sentiment['confidence']:.1%}) from news analysis"
        elif sentiment["overall_sentiment"] == "negative" and sentiment["confidence"] > 0.6:
            recommendation = "sell"
            confidence += 0.2
            reasoning = f"Negative sentiment ({sentiment['confidence']:.1%}) from news analysis"
        
        # Factor in technical analysis
        if technical.get("sma_signal") == "buy" and technical.get("rsi", 50) < 70:
            if recommendation == "buy":
                confidence += 0.2
            elif recommendation == "hold":
                recommendation = "buy"
                confidence += 0.1
        
        confidence = min(confidence, 1.0)
        
        return TradingInsight(
            symbol=symbol,
            recommendation=recommendation,
            confidence=confidence,
            reasoning=reasoning,
            risk_factors=["Market volatility", "Economic conditions"],
            price_targets={"short_term": 105.0, "long_term": 110.0},
            time_horizon="1-3 months",
            sources=[f"{len(articles)} news articles", "Technical analysis"]
        )
    
    async def _ai_generate_recommendation(self, symbol: str, articles: List[Dict], 
                                        sentiment: Dict, technical: Dict) -> TradingInsight:
        """AI-powered trading recommendation"""
        try:
            # Prepare context for AI
            news_summary = f"{len(articles)} articles with {sentiment['overall_sentiment']} sentiment ({sentiment['confidence']:.1%} confidence)"
            tech_summary = f"RSI: {technical.get('rsi', 'N/A')}, SMA Signal: {technical.get('sma_signal', 'N/A')}"
            
            prompt = f"""
            As a professional financial analyst, provide a trading recommendation for {symbol}.
            
            Market Data:
            - News Analysis: {news_summary}
            - Technical Analysis: {tech_summary}
            
            Provide your analysis in JSON format:
            {{
                "recommendation": "buy/sell/hold",
                "confidence": 0.85,
                "reasoning": "detailed reasoning for the recommendation",
                "risk_factors": ["factor1", "factor2", "factor3"],
                "price_targets": {{"short_term": 105.0, "long_term": 110.0}},
                "time_horizon": "time frame for the recommendation"
            }}
            
            Consider market conditions, sentiment, technical indicators, and risk management.
            """
            
            response = await self._call_ai_async(prompt)
            if response:
                try:
                    import json
                    ai_analysis = json.loads(response)
                    
                    return TradingInsight(
                        symbol=symbol,
                        recommendation=ai_analysis.get("recommendation", "hold"),
                        confidence=min(ai_analysis.get("confidence", 0.5), 1.0),
                        reasoning=ai_analysis.get("reasoning", "AI-generated analysis"),
                        risk_factors=ai_analysis.get("risk_factors", ["Market volatility"]),
                        price_targets=ai_analysis.get("price_targets", {"short_term": 100.0, "long_term": 105.0}),
                        time_horizon=ai_analysis.get("time_horizon", "1-3 months"),
                        sources=[f"{len(articles)} news articles", "Technical analysis", f"AI ({self.ai_provider})"]
                    )
                except json.JSONDecodeError:
                    logging.warning("AI returned invalid JSON, falling back to simple recommendation")
            
            # Fallback to simple recommendation if AI fails
            return self._simple_generate_recommendation(symbol, articles, sentiment, technical)
            
        except Exception as e:
            logging.error(f"AI recommendation generation failed: {e}")
            return self._simple_generate_recommendation(symbol, articles, sentiment, technical)
    
    async def _assess_risks(self, symbol: str, articles: List[Dict], technical: Dict) -> Dict[str, Any]:
        """Assess trading risks"""
        risks = []
        risk_level = "medium"
        
        # News-based risks
        if len(articles) == 0:
            risks.append("Limited news coverage - information asymmetry risk")
        
        # Technical risks
        rsi = technical.get("rsi", 50)
        if rsi > 70:
            risks.append("Overbought conditions - potential pullback")
            risk_level = "high"
        elif rsi < 30:
            risks.append("Oversold conditions - potential bounce")
        
        # Market risks
        risks.extend([
            "General market volatility",
            "Sector-specific risks",
            "Regulatory changes"
        ])
        
        return {
            "risk_level": risk_level,
            "identified_risks": risks,
            "mitigation_strategies": [
                "Use stop-loss orders",
                "Position sizing",
                "Diversification"
            ]
        }
    
    async def create_market_summary(self, symbols: List[str]) -> Dict[str, Any]:
        """Create a comprehensive market summary"""
        self.logger.info(f"📊 Creating market summary for {len(symbols)} symbols")
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "symbols_analyzed": len(symbols),
            "overall_sentiment": "neutral",
            "top_opportunities": [],
            "risk_alerts": [],
            "market_insights": []
        }
        
        try:
            # Analyze each symbol
            symbol_analyses = []
            for symbol in symbols[:5]:  # Limit for demo
                analysis = await self.research_symbol(symbol, lookback_days=3)
                symbol_analyses.append(analysis)
            
            # Aggregate insights
            positive_sentiment = 0
            negative_sentiment = 0
            
            for analysis in symbol_analyses:
                if analysis.get("sentiment_analysis"):
                    sentiment = analysis["sentiment_analysis"]["overall_sentiment"]
                    if sentiment == "positive":
                        positive_sentiment += 1
                    elif sentiment == "negative":
                        negative_sentiment += 1
            
            # Determine overall market sentiment
            if positive_sentiment > negative_sentiment:
                summary["overall_sentiment"] = "positive"
            elif negative_sentiment > positive_sentiment:
                summary["overall_sentiment"] = "negative"
            
            # Generate insights
            summary["market_insights"] = [
                f"Analyzed {len(symbol_analyses)} symbols",
                f"Positive sentiment: {positive_sentiment} symbols",
                f"Negative sentiment: {negative_sentiment} symbols",
                "AI-powered analysis complete"
            ]
            
            self.logger.info("✅ Market summary created")
            
        except Exception as e:
            self.logger.error(f"❌ Market summary failed: {e}")
            summary["error"] = str(e)
        
        return summary
    
    def is_configured(self) -> bool:
        """Check if AI agent is properly configured"""
        return bool(self.openai_api_key or self.news_api_key)
    
    def get_configuration_status(self) -> Dict[str, bool]:
        """Get configuration status for different services"""
        return {
            "openai_api": bool(self.openai_api_key),
            "news_api": bool(self.news_api_key),
            "polygon_api": bool(self.polygon_api_key),
            "basic_functionality": True  # Basic features work without APIs
        }

# Global AI agent instance
ai_agent = AITradingAgent()