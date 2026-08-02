"""
Insider Trading (SEC Form 4) Integration

Monitors SEC EDGAR for insider buying clusters.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

# Rate limiting
_last_request_time = 0
_min_request_interval = 0.1  # 100ms between requests (10/sec max)


def _rate_limit():
    """Apply rate limiting for SEC API."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _min_request_interval:
        time.sleep(_min_request_interval - elapsed)
    _last_request_time = time.time()


def fetch_insider_filings(ticker: str, days_back: int = 90) -> List[Dict]:
    """
    Fetch insider filings from SEC EDGAR.
    
    Args:
        ticker: Stock symbol
        days_back: Number of days to look back
        
    Returns:
        List of insider transactions
    """
    try:
        import yfinance as yf
        
        # Get CIK from yfinance
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        cik = info.get('cik')
        
        if not cik:
            logger.debug(f"No CIK found for {ticker}")
            return []
        
        # Pad CIK to 10 digits
        cik_str = str(cik).zfill(10)
        
        # SEC EDGAR company filings API
        url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
        
        headers = {
            'User-Agent': 'TradingBot info@tradingbot.com'  # SEC requires identification
        }
        
        _rate_limit()
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logger.warning(f"SEC API error for {ticker}: {response.status_code}")
            return []
        
        data = response.json()
        
        # Parse recent filings
        filings = data.get('filings', {}).get('recent', {})
        
        if not filings:
            return []
        
        # Look for Form 4 filings
        form_4_indices = []
        for i, form in enumerate(filings.get('form', [])):
            if form == '4':
                form_4_indices.append(i)
        
        if not form_4_indices:
            return []
        
        # Get date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days_back)
        
        transactions = []
        
        for idx in form_4_indices:
            try:
                filing_date_str = filings.get('filingDate', [None] * (idx + 1))[idx]
                if not filing_date_str:
                    continue
                
                filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                
                if filing_date < start_date:
                    continue
                
                # Get accession number for details
                accession = filings.get('accessionNumber', [None] * (idx + 1))[idx]
                
                # Parse owner information (simplified - would need full filing XML)
                # For now, we use what's available in the index
                owner_name = filings.get('ownerName', [''] * (idx + 1))[idx]
                
                transactions.append({
                    'ticker': ticker.upper(),
                    'insider_name': owner_name,
                    'transaction_type': 'P',  # Assume purchase (Form 4 is often buys we're interested in)
                    'shares': 0,  # Would need to parse filing XML
                    'price': 0,    # Would need to parse filing XML
                    'date': filing_date,
                    'accession_number': accession,
                    'form': '4'
                })
                
            except Exception as e:
                logger.debug(f"Error parsing filing {idx}: {e}")
                continue
        
        logger.info(f"📋 Found {len(transactions)} Form 4 filings for {ticker}")
        return transactions
        
    except Exception as e:
        logger.error(f"Error fetching insider filings for {ticker}: {e}")
        return []


def analyze_insider_activity(transactions: List[Dict]) -> Dict:
    """
    Analyze insider transactions.
    
    Args:
        transactions: List from fetch_insider_filings()
        
    Returns:
        Dict with analysis
    """
    if not transactions:
        return {
            'purchase_count_30d': 0,
            'purchase_count_90d': 0,
            'total_purchase_value_30d': 0,
            'purchase_to_sale_ratio': 0,
            'cluster_detected': False,
            'c_suite_buying': False,
            'insider_score': 0,
            'transactions': []
        }
    
    now = datetime.now(timezone.utc)
    days_30 = now - timedelta(days=30)
    days_90 = now - timedelta(days=90)
    
    # Filter to purchases only
    purchases = [t for t in transactions if t.get('transaction_type', '') == 'P']
    
    # Count in windows
    purchases_30d = [t for t in purchases if t.get('date', now) >= days_30]
    purchases_90d = [t for t in purchases if t.get('date', now) >= days_90]
    
    # Check for cluster (3+ insiders in 30 days)
    unique_insiders_30d = set(t.get('insider_name', '') for t in purchases_30d)
    cluster_detected = len(unique_insiders_30d) >= 3
    
    # Check for C-suite buying
    c_suite_keywords = ['CEO', 'CFO', 'COO', 'PRESIDENT', 'CHIEF']
    c_suite_buying = any(
        any(kw in t.get('insider_name', '').upper() for kw in c_suite_keywords)
        for t in purchases_30d
    )
    
    # Count sales
    sales = [t for t in transactions if t.get('transaction_type', '') == 'S']
    
    # Calculate ratio
    purchase_count = len(purchases_90d)
    sale_count = len(sales)
    purchase_to_sale_ratio = purchase_count / sale_count if sale_count > 0 else float('inf') if purchase_count > 0 else 0
    
    # Estimate value (would need actual share/price data)
    total_value_30d = sum(t.get('shares', 0) * t.get('price', 0) for t in purchases_30d)
    
    # Calculate score
    insider_score = calculate_insider_score({
        'cluster_detected': cluster_detected,
        'c_suite_buying': c_suite_buying,
        'total_purchase_value_30d': total_value_30d,
        'purchase_to_sale_ratio': purchase_to_sale_ratio,
        'purchase_count_90d': purchase_count
    })
    
    return {
        'purchase_count_30d': len(purchases_30d),
        'purchase_count_90d': len(purchases_90d),
        'total_purchase_value_30d': total_value_30d,
        'purchase_to_sale_ratio': purchase_to_sale_ratio,
        'cluster_detected': cluster_detected,
        'c_suite_buying': c_suite_buying,
        'insider_score': insider_score,
        'transactions': transactions,
        'analysis_date': now
    }


def calculate_insider_score(analysis: Dict) -> int:
    """
    Calculate insider score from 0-100.
    
    Args:
        analysis: Output from analyze_insider_activity()
        
    Returns:
        Score from 0 to 100
    """
    score = 0
    
    # Cluster detection (+30)
    if analysis.get('cluster_detected'):
        score += 30
    
    # C-suite buying (+25)
    if analysis.get('c_suite_buying'):
        score += 25
    
    # High value (+20 if > $500k)
    if analysis.get('total_purchase_value_30d', 0) > 500000:
        score += 20
    
    # Purchase to sale ratio (+15 if > 2.0)
    if analysis.get('purchase_to_sale_ratio', 0) > 2.0:
        score += 15
    
    # Sustained interest (+10 if >= 5 purchases in 90 days)
    if analysis.get('purchase_count_90d', 0) >= 5:
        score += 10
    
    return min(100, score)


def filter_by_insider(ticker: str, signal: str) -> Tuple[bool, int, str]:
    """
    Filter signal based on insider activity.
    
    Note: Insider data is ADDITIVE only - never blocks trades.
    
    Args:
        ticker: Stock symbol
        signal: 'BUY' or 'SELL'
        
    Returns:
        (allowed: bool, insider_score: int, reason: str)
    """
    # Always allow - insider is additive only
    transactions = fetch_insider_filings(ticker)
    analysis = analyze_insider_activity(transactions)
    
    score = analysis.get('insider_score', 0)
    
    # Insiders never block - only boost
    return True, score, f"Insider score: {score}"


# Convenience function
def get_insider_score(ticker: str) -> int:
    """Quick way to get insider score for a ticker."""
    transactions = fetch_insider_filings(ticker)
    analysis = analyze_insider_activity(transactions)
    return analysis.get('insider_score', 0)
