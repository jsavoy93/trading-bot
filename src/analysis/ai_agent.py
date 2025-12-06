"""
AI Agent System for Trading Bot
Provides intelligent analysis, research, and decision-making capabilities.
"""
import os
import logging
import json
import asyncio
import re
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
import requests
from dotenv import load_dotenv

# Try to import async helpers for better performance
try:
    from utils.async_helpers import run_async_safely, async_retry
    ASYNC_HELPERS_AVAILABLE = True
except ImportError:
    ASYNC_HELPERS_AVAILABLE = False
    logging.debug("Async helpers not available, using standard asyncio patterns")

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
        self.huggingface_api_key = os.getenv("HUGGINGFACE_API_KEY")
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.mistral_api_key = os.getenv("MISTRAL_API_KEY")
        self.cohere_api_key = os.getenv("COHERE_API_KEY")
        
        # Request tracking
        self.total_requests = 0
        self.successful_requests = 0
        self.daily_request_count = 0
        self.current_provider = "Auto"
        self.session_start = datetime.now()
        self.ai_provider = os.getenv("AI_PROVIDER", "openai").lower()
        self.news_api_key = os.getenv("NEWS_API_KEY") 
        self.polygon_api_key = os.getenv("POLYGON_API_KEY")
        
        # Agent configuration
        self.max_articles_per_search = 10
        self.sentiment_threshold = 0.6
        self.confidence_threshold = 0.7
        
        # Initialize AI client with fallback support
        self.ai_client = None
        self.is_configured = False
        self.current_provider = None
        self.failed_providers = set()  # Track failed providers
        self.provider_failure_count = {}  # Track failure counts per provider
        self.last_failure_time = {}  # Track when each provider last failed
        self._init_ai_client()
        
        # Initialize components
        self._setup_logging()
    
    def _init_ai_client(self):
        """Initialize AI client based on provider preference with fallback support"""
        # Reset configuration state
        self.is_configured = False
        self.current_provider = None
        
        # Try providers in order: preferred first, then others
        providers_to_try = []
        
        # Add preferred provider first (if not failed recently)
        if self.ai_provider == "google" and self.google_api_key and not self._is_provider_failed("google"):
            providers_to_try.append(("google", self._init_google_ai))
        elif self.ai_provider == "openai" and self.openai_api_key and not self._is_provider_failed("openai"):
            providers_to_try.append(("openai", self._init_openai))
        elif self.ai_provider == "huggingface" and self.huggingface_api_key and not self._is_provider_failed("huggingface"):
            providers_to_try.append(("huggingface", self._init_huggingface))
        elif self.ai_provider == "openrouter" and self.openrouter_api_key and not self._is_provider_failed("openrouter"):
            providers_to_try.append(("openrouter", self._init_openrouter))
        elif self.ai_provider == "mistral" and self.mistral_api_key and not self._is_provider_failed("mistral"):
            providers_to_try.append(("mistral", self._init_mistral))
        elif self.ai_provider == "cohere" and self.cohere_api_key and not self._is_provider_failed("cohere"):
            providers_to_try.append(("cohere", self._init_cohere))
        
        # Add other available providers as fallbacks
        fallback_providers = [
            ("openai", self._init_openai, self.openai_api_key),
            ("google", self._init_google_ai, self.google_api_key),
            ("huggingface", self._init_huggingface, self.huggingface_api_key),
            ("openrouter", self._init_openrouter, self.openrouter_api_key),
            ("mistral", self._init_mistral, self.mistral_api_key),
            ("cohere", self._init_cohere, self.cohere_api_key)
        ]
        
        for provider_name, init_func, api_key in fallback_providers:
            if api_key and not self._is_provider_failed(provider_name):
                provider_tuple = (provider_name, init_func)
                if provider_tuple not in providers_to_try:
                    providers_to_try.append(provider_tuple)
        
        # Try each provider
        for provider_name, init_func in providers_to_try:
            try:
                logging.info(f"🔄 Attempting to initialize {provider_name.upper()} AI...")
                init_func()
                if self.ai_client:
                    self.current_provider = provider_name
                    self.is_configured = True
                    # Reset failure count on successful init
                    if provider_name in self.provider_failure_count:
                        del self.provider_failure_count[provider_name]
                    if provider_name in self.failed_providers:
                        self.failed_providers.remove(provider_name)
                    logging.info(f"✅ Successfully initialized {provider_name.upper()} AI")
                    return
            except Exception as e:
                logging.warning(f"⚠️  Failed to initialize {provider_name.upper()}: {e}")
                self._mark_provider_failed(provider_name)
        
        self.is_configured = False
        logging.error("❌ No AI providers available")
    
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
            self.ai_client = genai.GenerativeModel('gemini-2.0-flash')
            # Try different model names in order of preference (using correct API names)
            models_to_try = [
                'models/gemini-2.5-flash',
                'models/gemini-2.0-flash', 
                'models/gemini-flash-latest',
                'models/gemini-2.5-flash-lite',
                'models/gemini-2.0-flash-lite'
            ]
            
            for model_name in models_to_try:
                try:
                    self.ai_client = genai.GenerativeModel(model_name)
                    # Test the model with a simple call
                    test_response = self.ai_client.generate_content(
                        "Hello",
                        generation_config={'max_output_tokens': 5, 'temperature': 0.1}
                    )
                    if test_response.text:
                        logging.info(f"✅ AI Agent initialized with Google {model_name}")
                        return
                except Exception as e:
                    logging.debug(f"Model {model_name} failed: {e}")
                    continue
            
            # If all models fail
            logging.error("❌ No compatible Google AI models found")
            self.ai_client = None
            
        except ImportError:
            logging.error("❌ Google GenerativeAI package not available")
    
    def _init_huggingface(self):
        """Initialize Hugging Face client - temporarily disabled due to API changes"""
        logging.warning("⚠️ Hugging Face provider temporarily disabled due to API deprecation")
        self.ai_client = None
        return False
    
    def _init_openrouter(self):
        """Initialize OpenRouter client"""
        try:
            import openai
            # OpenRouter uses OpenAI-compatible API
            self.ai_client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.openrouter_api_key
            )
            self.openrouter_model = "openai/gpt-3.5-turbo"  # Default model
            logging.info("✅ AI Agent initialized with OpenRouter")
        except ImportError:
            logging.error("❌ OpenAI package required for OpenRouter")
            self.ai_client = None
    
    def _init_mistral(self):
        """Initialize Mistral AI client"""
        try:
            from mistralai import Mistral
            self.ai_client = Mistral(api_key=self.mistral_api_key)
            self.mistral_model = "mistral-small-latest"  # Updated model
            logging.info("✅ AI Agent initialized with Mistral AI")
            return True
        except ImportError:
            logging.error("❌ Mistral AI package not available")
            self.ai_client = None
            return False
        except Exception as e:
            logging.error(f"❌ Mistral AI initialization error: {e}")
            self.ai_client = None
            return False
    
    def _init_cohere(self):
        """Initialize Cohere client"""
        try:
            import cohere
            self.ai_client = cohere.Client(self.cohere_api_key)
            self.cohere_model = "command"  # Default model
            logging.info("✅ AI Agent initialized with Cohere")
        except ImportError:
            logging.error("❌ Cohere package not available")
            self.ai_client = None
    
    def _extract_json_from_response(self, response: str) -> tuple[dict, str]:
        """Extract JSON from AI response, handling markdown and extra text"""
        import json
        import re
        
        if not response or not response.strip():
            return None, "Empty response"
        
        cleaned_response = response.strip()
        
        # Remove markdown code blocks / fences
        if cleaned_response.startswith('```json'):
            cleaned_response = cleaned_response[7:]
        elif cleaned_response.startswith('```'):
            cleaned_response = cleaned_response[3:]

        if cleaned_response.endswith('```'):
            cleaned_response = cleaned_response[:-3]

        cleaned_response = cleaned_response.strip()

        # Try to find JSON object boundaries using balanced braces
        first_brace = cleaned_response.find('{')
        if first_brace == -1:
            return None, "No JSON object found"

        brace_count = 0
        last_brace = -1
        for i, char in enumerate(cleaned_response[first_brace:], first_brace):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    last_brace = i
                    break

        if last_brace == -1:
            return None, "No matching closing brace found"

        json_portion = cleaned_response[first_brace:last_brace + 1]

        # Try strict parse, then lenient retry removing trailing commas
        for attempt in range(2):
            try:
                parsed_json = json.loads(json_portion)
                return parsed_json, None
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                if attempt == 0:
                    json_portion = re.sub(r',\s*([}\]])', r'\1', json_portion)
                    continue
                return None, f"JSON parse error: {e}"
    
    def _is_provider_failed(self, provider: str) -> bool:
        """Check if a provider is currently marked as failed"""
        if provider not in self.last_failure_time:
            return False
        
        # Allow retry after 10 minutes
        retry_after = timedelta(minutes=10)
        return datetime.now() - self.last_failure_time[provider] < retry_after
    
    def _mark_provider_failed(self, provider: str):
        """Mark a provider as failed"""
        self.failed_providers.add(provider)
        self.last_failure_time[provider] = datetime.now()
        self.provider_failure_count[provider] = self.provider_failure_count.get(provider, 0) + 1
        logging.warning(f"⚠️  Marked {provider.upper()} as failed (count: {self.provider_failure_count[provider]})")
    
    def try_fallback_provider(self) -> bool:
        """Attempt to switch to a fallback AI provider"""
        if not self.is_configured or not self.current_provider:
            return False
        
        logging.warning(f"🔄 Current provider {self.current_provider.upper()} failing, attempting fallback...")
        
        # Mark current provider as failed
        self._mark_provider_failed(self.current_provider)
        
        # Try to reinitialize with different provider
        old_provider = self.current_provider
        self._init_ai_client()
        
        if self.is_configured and self.current_provider != old_provider:
            logging.info(f"✅ Successfully switched from {old_provider.upper()} to {self.current_provider.upper()}")
            return True
        elif self.is_configured:
            logging.info(f"⚠️  Still using {self.current_provider.upper()} (no better alternative)")
            return True
        else:
            logging.error("❌ No fallback AI providers available")
            return False
    
    def _call_ai_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Synchronous AI API call for all providers"""
        if not self.ai_client or not self.current_provider:
            return None
            
        try:
            if self.current_provider == "openai":
                return self._call_openai_sync(prompt, max_tokens)
            elif self.current_provider == "google":
                return self._call_google_sync(prompt, max_tokens)
            elif self.current_provider == "huggingface":
                return self._call_huggingface_sync(prompt, max_tokens)
            elif self.current_provider == "openrouter":
                return self._call_openrouter_sync(prompt, max_tokens)
            elif self.current_provider == "mistral":
                return self._call_mistral_sync(prompt, max_tokens)
            elif self.current_provider == "cohere":
                return self._call_cohere_sync(prompt, max_tokens)
            else:
                logging.error(f"Unknown AI provider: {self.current_provider}")
                return None
                
        except Exception as e:
            logging.debug(f"AI API call failed: {e}")
            logging.warning(f"AI provider {self.current_provider} failed, trying fallback")
            # Mark provider as failed
            self._mark_provider_failed(self.current_provider)
            # Try fallback provider
            if self.try_fallback_provider():
                # Retry with new provider
                try:
                    return self._call_ai_sync(prompt, max_tokens)
                except:
                    pass
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
        try:
            # Use educational/informational system prompt to avoid safety filters
            system_prompt = "You are an educational financial data researcher providing objective market analysis for learning purposes. Focus on historical data patterns and market indicators in JSON format."
            full_prompt = f"{system_prompt}\n\n{prompt}"
            
            response = self.ai_client.generate_content(
                full_prompt,
                generation_config={
                    'max_output_tokens': max_tokens,
                    'temperature': 0.1,  # Lower temperature for more factual responses
                },
                safety_settings=[
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                ]
            )
            
            # Check if response was blocked by safety filters
            if not response.parts:
                finish_reason = response.candidates[0].finish_reason if response.candidates else 'unknown'
                logging.debug(f"Google AI filtered response - finish_reason: {finish_reason}")
                return None  # Return None instead of raising exception
                
            return response.text.strip() if response.text else None
            
        except Exception as e:
            if "finish_reason" in str(e) or "valid `Part`" in str(e):
                logging.warning(f"Google AI safety filter triggered: {e}")
                return None
            raise e
    
    def _call_huggingface_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call Hugging Face API synchronously using new router endpoint"""
        try:
            headers = {"Authorization": f"Bearer {self.huggingface_api_key}"}
            
            # Use a simple, reliable text generation model
            url = f"{self.hf_endpoint}/models/gpt2"
            
            payload = {
                "inputs": f"Financial analysis request: {prompt}\n\nAnalysis:",
                "parameters": {
                    "max_new_tokens": min(max_tokens, 100),  # GPT2 works better with smaller outputs
                    "temperature": 0.7,
                    "return_full_text": False,
                    "do_sample": True
                }
            }
            
            response = self.ai_client.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if isinstance(result, list) and len(result) > 0:
                    generated_text = result[0].get('generated_text', '').strip()
                    return generated_text if generated_text else None
                return None
            else:
                logging.debug(f"Hugging Face API error: {response.status_code} - {response.text}")
                logging.error(f"Hugging Face API error: {response.status_code}")
                return None
                
        except Exception as e:
            logging.debug(f"Hugging Face API error: {e}")
            logging.error("Hugging Face API error")
            raise e
    
    def _call_openrouter_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call OpenRouter API synchronously"""
        try:
            response = self.ai_client.chat.completions.create(
                model=self.openrouter_model,
                messages=[
                    {"role": "system", "content": "You are a professional financial analyst providing concise, actionable insights in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logging.debug(f"OpenRouter API error: {e}")
            logging.error("OpenRouter API error")
            raise e
    
    def _call_mistral_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call Mistral AI API synchronously"""
        try:
            messages = [
                {"role": "system", "content": "You are a professional financial analyst providing concise, actionable insights in JSON format."},
                {"role": "user", "content": prompt}
            ]
            
            response = self.ai_client.chat.complete(
                model=self.mistral_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logging.debug(f"Mistral AI API error: {e}")
            logging.error("Mistral API rate limit or error")
            raise e
    
    def _call_cohere_sync(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call Cohere API synchronously using Chat API"""
        try:
            response = self.ai_client.chat(
                message=f"You are a professional financial analyst providing concise, actionable insights in JSON format.\n\n{prompt}",
                max_tokens=max_tokens,
                temperature=0.3
            )
            return response.text.strip()
        except Exception as e:
            logging.debug(f"Cohere API error: {e}")
            logging.error("Cohere API rate limit or error")
            raise e
    
    async def _call_ai_async(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Asynchronous AI API call for all providers"""
        if not self.ai_client or not self.current_provider:
            return None
            
        try:
            if self.current_provider == "openai":
                return await self._call_openai_async(prompt, max_tokens)
            elif self.current_provider == "google":
                return await self._call_google_async(prompt, max_tokens)
            else:
                # For other providers, use sync calls in async wrapper
                return await self._call_sync_as_async(prompt, max_tokens)
                
        except Exception as e:
            logging.debug(f"AI API call failed: {e}")
            logging.warning(f"AI provider {self.current_provider} failed, trying fallback")
            # Mark provider as failed
            self._mark_provider_failed(self.current_provider)
            # Try fallback provider
            if self.try_fallback_provider():
                # Retry with new provider
                try:
                    return await self._call_ai_async(prompt, max_tokens)
                except:
                    pass
            return None
    
    async def _call_sync_as_async(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Wrap synchronous calls for async compatibility"""
        def sync_generate():
            try:
                return self._call_ai_sync(prompt, max_tokens)
            except Exception as e:
                if "finish_reason" in str(e) or "safety" in str(e).lower():
                    logging.debug(f"AI safety filter or completion issue: {e}")
                    return None
                raise e
        
        try:
            result = await asyncio.to_thread(sync_generate)
            return result
        except Exception as e:
            logging.debug(f"Async wrapper error: {e}")
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
            logging.debug(f"OpenAI async call failed: {e}")
            return None
    
    async def _call_google_async(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Call Google Gemini Flash API asynchronously"""
        import asyncio
        
        def sync_generate():
            try:
                # Use educational/informational system prompt to avoid safety filters
                system_prompt = "You are an educational financial data researcher providing objective market analysis for learning purposes. Focus on historical data patterns and market indicators in JSON format."
                full_prompt = f"{system_prompt}\n\n{prompt}"
                
                response = self.ai_client.generate_content(
                    full_prompt,
                    generation_config={
                        'max_output_tokens': max_tokens,
                        'temperature': 0.1,  # Lower temperature for more factual responses
                    },
                    safety_settings=[
                        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    ]
                )
                
                # Check if response was blocked by safety filters
                if not response.parts:
                    finish_reason = response.candidates[0].finish_reason if response.candidates else 'unknown'
                    safety_ratings = response.candidates[0].safety_ratings if response.candidates else []
                    logging.warning(f"🚫 Google AI safety filter blocked response - finish_reason: {finish_reason}, safety_ratings: {safety_ratings}")
                    return None  # Return None instead of raising exception
                    
                return response.text.strip() if response.text else None
                
            except Exception as e:
                if "finish_reason" in str(e) or "valid `Part`" in str(e):
                    logging.warning(f"Google AI safety filter triggered: {e}")
                    return None
                raise e
        
        result = await asyncio.to_thread(sync_generate)
        return result
    
    async def analyze_with_context(self, prompt: str, context_type: str = "general") -> Dict[str, Any]:
        """Analyze content with context and return structured response with automatic fallback"""
        max_retries = 2  # Try current provider, then one fallback
        
        # Track request
        self.total_requests += 1
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logging.info(f"🔄 AI fallback attempt {attempt} for {context_type}")
                
                logging.debug(f"🔍 AI analyze_with_context called for {context_type} (provider: {self.current_provider})")
                response_text = await self._call_ai_async(prompt)
                
                if not response_text:
                    error_msg = f"AI returned empty/None response for {context_type} - likely safety filter or API failure"
                    logging.warning(f"⚠️ {error_msg}")
                    
                    # Try fallback on empty response (likely API failure)
                    if attempt < max_retries - 1 and self.try_fallback_provider():
                        continue
                    
                    return {"debug_info": error_msg}
                
                logging.debug(f"✅ AI returned response for {context_type}: {response_text[:200]}...")
                
                # Try to parse as JSON using robust extraction
                parsed_json, error = self._extract_json_from_response(response_text)
                
                if parsed_json:
                    logging.debug(f"✅ Successfully parsed JSON with keys: {list(parsed_json.keys())}")
                    self.successful_requests += 1  # Track successful request
                    return parsed_json
                else:
                    logging.warning(f"⚠️ AI returned non-JSON response for {context_type} ({error}): {response_text[:200]}...")
                    return {"raw_response": response_text, "parse_error": error, "debug_info": f"JSON parse failed for {context_type}"}
                    
            except Exception as e:
                error_msg = f"AI context analysis failed for {context_type}: {type(e).__name__} - {e}"
                logging.error(f"❌ {error_msg}")
                
                # Try fallback on exception
                if attempt < max_retries - 1 and self.try_fallback_provider():
                    continue
                
                return {"debug_info": f"Exception in analyze_with_context: {type(e).__name__} - {str(e)[:200]}"}
        
        # If we get here, all attempts failed
        return {"debug_info": f"All AI providers failed for {context_type} after {max_retries} attempts"}

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
                ai_analysis, error = self._extract_json_from_response(response)
                if ai_analysis:
                    
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
                else:
                    logging.warning(f"⚠️ AI returned invalid JSON for article summary: {error}")
                    # Fallback to basic summary
                    return ArticleSummary(
                        title=title,
                        url=url,
                        summary=content[:200] if content else title,
                        sentiment="neutral",
                        relevance_score=0.5,
                        key_points=[title[:100]],
                        trading_signals=["AI analysis unavailable"],
                        timestamp=datetime.now()
                    )
            
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
            For educational purposes, analyze the market indicators for {symbol}.
            
            Educational Data:
            - News Pattern Analysis: {news_summary}
            - Technical Indicator Study: {tech_summary}
            
            Provide educational market analysis in JSON format:
            {{
                "market_observation": "bullish/bearish/neutral",
                "confidence": 0.85,
                "reasoning": "educational explanation of market patterns",
                "risk_indicators": ["pattern1", "pattern2", "pattern3"],
                "price_patterns": {{"support_level": 105.0, "resistance_level": 110.0}},
                "analysis_timeframe": "timeframe for this educational analysis"
            }}
            
            Focus on market patterns, sentiment indicators, technical analysis, and risk assessment for learning.
            """
            
            response = await self._call_ai_async(prompt)
            if response:
                ai_analysis, error = self._extract_json_from_response(response)
                if ai_analysis:
                    
                    return TradingInsight(
                        symbol=symbol,
                        recommendation=ai_analysis.get("recommendation", "hold").lower(),
                        confidence=min(float(ai_analysis.get("confidence", 0.5)), 1.0),
                        reasoning=ai_analysis.get("reasoning", "AI-generated analysis"),
                        risk_factors=ai_analysis.get("risk_factors", ["Market volatility"]),
                        price_targets=ai_analysis.get("price_targets", {"short_term": 100.0, "long_term": 105.0}),
                        time_horizon=ai_analysis.get("time_horizon", "1-3 months"),
                        sources=[f"{len(articles)} news articles", "Technical analysis", f"AI ({self.ai_provider})"]
                    )
                else:
                    logging.warning(f"⚠️ AI returned invalid JSON for {symbol}: {error}")
                    # Return a conservative fallback
                    return TradingInsight(
                        symbol=symbol,
                        recommendation="hold",
                        confidence=0.3,
                        reasoning="AI analysis failed, defaulting to conservative hold",
                        risk_factors=["AI analysis unavailable", "Market uncertainty"],
                        price_targets={"short_term": 100.0, "long_term": 105.0},
                        time_horizon="1-3 months",
                        sources=[f"{len(articles)} news articles", "Technical analysis", f"AI ({self.ai_provider})"]
                    )
            
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
            "google_ai": bool(self.google_api_key),
            "news_api": bool(self.news_api_key),
            "polygon_api": bool(self.polygon_api_key),
            "basic_functionality": True  # Basic features work without APIs
        }
    
    def get_provider_status(self) -> Dict[str, Any]:
        """Get detailed status of AI providers"""
        return {
            "current_provider": self.current_provider,
            "is_configured": self.is_configured,
            "available_providers": {
                "openai": bool(self.openai_api_key),
                "google": bool(self.google_api_key)
            },
            "failed_providers": list(self.failed_providers),
            "failure_counts": dict(self.provider_failure_count),
            "last_failures": {k: v.isoformat() for k, v in self.last_failure_time.items()}
        }

# Global AI agent instance
ai_agent = AITradingAgent()