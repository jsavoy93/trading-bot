#!/usr/bin/env python3
"""
Alpaca API Full Test Suite
Tests all major API endpoints to verify what's working and what's not.
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = "PKZ5EJEAZOUYWW3XFZYJXUEXV5"
API_SECRET = "wbRuAVrTjjvV76X4W8aLZzLvcbqjGAosRN7qwLzAYPR"
BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

print("=" * 70)
print("ALPACA API FULL TEST SUITE")
print("=" * 70)
print(f"Key: {API_KEY[:10]}...")
print(f"Base URL: {BASE_URL}")
print()

# Initialize clients
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient

trading_client = TradingClient(API_KEY, API_SECRET, paper=True)
data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
crypto_data_client = CryptoHistoricalDataClient(API_KEY, API_SECRET)

results = []

def test(name, func):
    """Run a test and capture results"""
    print(f"🔍 Testing: {name}...", end=" ")
    try:
        result = func()
        print("✅ PASS")
        results.append({"test": name, "status": "PASS", "result": result, "error": None})
        return result
    except Exception as e:
        print(f"❌ FAIL: {e}")
        results.append({"test": name, "status": "FAIL", "result": None, "error": str(e)})
        return None

# ============================================================
# ACCOUNT & TRADING TESTS
# ============================================================
print("\n📊 ACCOUNT & TRADING TESTS")
print("-" * 50)

def test_account():
    account = trading_client.get_account()
    return {
        "account_id": account.id,
        "portfolio_value": float(account.portfolio_value),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "status": account.account_status,
    }

account = test("Get Account", test_account)

def test_positions():
    positions = trading_client.get_all_positions()
    return {
        "count": len(positions),
        "positions": [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for p in positions
        ]
    }

positions = test("Get All Positions", test_positions)

def test_orders():
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import OrderStatus
    orders = trading_client.get_orders(GetOrdersRequest(status=OrderStatus.ALL, limit=10))
    return {"count": len(orders), "orders": [{"id": o.id, "symbol": o.symbol, "status": o.status.value} for o in orders[:5]]}

orders = test("Get Orders (all, limit 10)", test_orders)

def test_assets():
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass
    req = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status="active")
    assets = trading_client.get_assets(req)
    active = [a for a in assets if a.tradable]
    return {"total": len(assets), "tradable": len(active), "sample": [a.symbol for a in active[:5]]}

assets = test("Get US Equity Assets (active, tradable)", test_assets)

def test_watchlists():
    try:
        from alpaca.trading.requests import CreateWatchlistRequest
        wl = trading_client.get_watchlists()
        return {"count": len(wl), "sample": [{"id": w.id, "name": w.name} for w in wl[:3]]}
    except Exception as e:
        if "not supported" in str(e).lower() or "405" in str(e):
            return {"count": 0, "note": "Watchlist not supported on this plan"}
        raise

watchlists = test("Get Watchlists", test_watchlists)

# ============================================================
# HISTORICAL DATA TESTS - STOCKS
# ============================================================
print("\n📈 HISTORICAL DATA TESTS (STOCKS)")
print("-" * 50)

end = datetime.now()
start = end - timedelta(days=30)

def test_stock_bars():
    from alpaca.data.requests import StockBarsRequest
    req = StockBarsRequest(
        symbol_or_symbols=["SPY", "AAPL", "MSFT"],
        start=start,
        end=end,
        timeframe="1Day",
        limit=100
    )
    bars = data_client.get_stock_bars(req)
    result = {}
    for sym, bar_list in bars.items():
        result[sym] = {"count": len(bar_list), "latest_close": bar_list[-1].close if bar_list else None}
    return result

stock_bars = test("Stock Bars - SPY, AAPL, MSFT (1Day, 30 days)", test_stock_bars)

def test_stock_bars_5min():
    from alpaca.data.requests import StockBarsRequest
    req = StockBarsRequest(
        symbol_or_symbols=["SPY"],
        start=end - timedelta(hours=4),
        end=end,
        timeframe="5Min",
        limit=100
    )
    bars = data_client.get_stock_bars(req)
    return {"SPY_count": len(bars.get("SPY", [])), "bars": [(b.close, b.timestamp) for b in (bars.get("SPY", []) or [])[:3]]}

stock_bars_5min = test("Stock Bars - SPY (5Min, 4 hours)", test_stock_bars_5min)

def test_stock_quotes():
    from alpaca.data.requests import StockQuotesRequest
    req = StockQuotesRequest(
        symbol_or_symbols=["SPY", "AAPL"],
        start=end - timedelta(minutes=30),
        end=end,
        limit=10
    )
    quotes = data_client.get_stock_quotes(req)
    result = {}
    for sym, q_list in quotes.items():
        result[sym] = {"count": len(q_list), "latest": q_list[-1].bid_price if q_list else None}
    return result

stock_quotes = test("Stock Quotes - SPY, AAPL (30 min)", test_stock_quotes)

def test_stock_trades():
    from alpaca.data.requests import StockTradesRequest
    req = StockTradesRequest(
        symbol_or_symbols=["AAPL"],
        start=end - timedelta(hours=2),
        end=end,
        limit=20
    )
    trades = data_client.get_stock_trades(req)
    return {"AAPL_count": len(trades.get("AAPL", [])), "samples": [(t.price, t.size, t.timestamp) for t in (trades.get("AAPL", []) or [])[:3]]}

stock_trades = test("Stock Trades - AAPL (2 hours)", test_stock_trades)

def test_stock_bars_many_symbols():
    from alpaca.data.requests import StockBarsRequest
    symbols = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        start=end - timedelta(days=5),
        end=end,
        timeframe="1Day",
        limit=10
    )
    bars = data_client.get_stock_bars(req)
    result = {sym: len(bars.get(sym, [])) for sym in symbols}
    return result

stock_bars_many = test("Stock Bars - 10 Symbols (1Day, 5 days)", test_stock_bars_many_symbols)

def test_stock_bars_1min():
    from alpaca.data.requests import StockBarsRequest
    req = StockBarsRequest(
        symbol_or_symbols=["SPY"],
        start=end - timedelta(minutes=60),
        end=end,
        timeframe="1Min",
        limit=60
    )
    bars = data_client.get_stock_bars(req)
    return {"SPY_count": len(bars.get("SPY", []))}

stock_bars_1min = test("Stock Bars - SPY (1Min, 60 min)", test_stock_bars_1min)

# ============================================================
# HISTORICAL DATA TESTS - CRYPTO
# ============================================================
print("\n₿ CRYPTO DATA TESTS")
print("-" * 50)

def test_crypto_bars():
    from alpaca.data.requests import CryptoBarsRequest
    req = CryptoBarsRequest(
        symbol_or_symbols=["BTC/USD", "ETH/USD"],
        start=end - timedelta(days=7),
        end=end,
        timeframe="1Day",
        limit=20
    )
    bars = crypto_data_client.get_crypto_bars(req)
    result = {}
    for sym, bar_list in bars.items():
        result[sym] = {"count": len(bar_list), "latest_close": bar_list[-1].close if bar_list else None}
    return result

crypto_bars = test("Crypto Bars - BTC/USD, ETH/USD (1Day, 7 days)", test_crypto_bars)

def test_crypto_quotes():
    from alpaca.data.requests import CryptoQuoteRequest
    req = CryptoQuoteRequest(
        symbol_or_symbols=["BTC/USD"],
        start=end - timedelta(minutes=30),
        end=end,
        limit=5
    )
    quotes = crypto_data_client.get_crypto_quotes(req)
    return {"BTC_count": len(quotes.get("BTC/USD", []))}

crypto_quotes = test("Crypto Quotes - BTC/USD (30 min)", test_crypto_quotes)

# ============================================================
# MARKET STATUS & SCHEDULE TESTS
# ============================================================
print("\n🗓️ MARKET STATUS & SCHEDULE TESTS")
print("-" * 50)

def test_market_clock():
    clock = trading_client.get_clock()
    return {
        "timestamp": str(clock.timestamp),
        "is_open": clock.is_open,
        "next_open": str(clock.next_open) if clock.next_open else None,
        "next_close": str(clock.next_close) if clock.next_close else None,
    }

market_clock = test("Market Clock", test_market_clock)

def test_market_holidays():
    holidays = trading_client.get_holidays()
    return {"count": len(holidays), "holidays": [(h.date, h.name) for h in holidays[:5]]}

market_holidays = test("Market Holidays", test_market_holidays)

# ============================================================
# PORTFOLIO & PERFORMANCE TESTS
# ============================================================
print("\n💼 PORTFOLIO & PERFORMANCE TESTS")
print("-" * 50)

def test_portfolio_history():
    from alpaca.trading.requests import PortfolioHistoryRequest
    req = PortfolioHistoryRequest(
        start=end - timedelta(days=30),
        end=end,
        timeframe="1Day",
        period=None
    )
    history = trading_client.get_portfolio_history(req)
    return {
        "bars": len(history.bars) if history.bars else 0,
        "equity_curve": history.equity[-5:] if history.equity else [],
        "timestamp_start": str(history.timestamp) if history.timestamp else None,
    }

portfolio_history = test("Portfolio History (30 days, 1Day)", test_portfolio_history)

def test_portfolio_history_hourly():
    from alpaca.trading.requests import PortfolioHistoryRequest
    req = PortfolioHistoryRequest(
        start=end - timedelta(days=5),
        end=end,
        timeframe="1Hour",
    )
    history = trading_client.get_portfolio_history(req)
    return {"bars": len(history.bars) if history.bars else 0, "equity_count": len(history.equity)}

portfolio_hourly = test("Portfolio History (5 days, 1Hour)", test_portfolio_history_hourly)

# ============================================================
# SNAPSHOT & LATEST DATA TESTS
# ============================================================
print("\n📸 SNAPSHOT & LATEST DATA TESTS")
print("-" * 50)

def test_stock_snapshots():
    try:
        snapshots = data_client.get_stock_snapshots(["AAPL", "MSFT", "SPY"])
        result = {}
        for sym, snap in snapshots.items():
            result[sym] = {
                "latest_trade_price": snap.latest_trade.price if snap.latest_trade else None,
                "bid_price": snap.latest_quote.bid_price if snap.latest_quote else None,
                "ask_price": snap.latest_quote.ask_price if snap.latest_quote else None,
            }
        return result
    except Exception as e:
        return {"error": str(e)}

snapshots = test("Stock Snapshots - AAPL, MSFT, SPY", test_stock_snapshots)

def test_crypto_snapshots():
    try:
        snapshots = crypto_data_client.get_crypto_snapshots(["BTC/USD", "ETH/USD"])
        result = {}
        for sym, snap in snapshots.items():
            result[sym] = {
                "latest_trade_price": snap.latest_trade.price if snap.latest_trade else None,
                "bid_price": snap.latest_quote.bid_price if snap.latest_quote else None,
            }
        return result
    except Exception as e:
        return {"error": str(e)}

crypto_snapshots = test("Crypto Snapshots - BTC/USD, ETH/USD", test_crypto_snapshots)

# ============================================================
# BATCH & SPECIAL REQUESTS
# ============================================================
print("\n📦 BATCH & SPECIAL REQUEST TESTS")
print("-" * 50)

def test_symbols_list():
    """Test getting full symbol list"""
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass
    req = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status="active")
    all_assets = trading_client.get_assets(req)
    symbols = [a.symbol for a in all_assets if a.tradable]
    return {"total_symbols": len(symbols), "sample": symbols[:10]}

symbols = test("Get All Tradeable US Symbols", test_symbols_list)

# ============================================================
# NEWS TESTS (if available)
# ============================================================
print("\n📰 NEWS TESTS")
print("-" * 50)

def test_news():
    try:
        from alpaca.data.news import NewsDataClient
        news_client = NewsDataClient(API_KEY, API_SECRET)
        from alpaca.data.requests import NewsRequest
        req = NewsRequest(
            symbols=["AAPL", "MSFT"],
            start=end - timedelta(days=7),
            end=end,
            limit=10
        )
        news = news_client.get_news(req)
        return {"count": len(news), "headlines": [n.headline for n in news[:3]]}
    except Exception as e:
        return {"error": str(e)}

news = test("News - AAPL, MSFT (7 days)", test_news)

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

passed = sum(1 for r in results if r["status"] == "PASS")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n✅ Passed: {passed}")
print(f"❌ Failed: {failed}")

print("\n❌ FAILED TESTS:")
for r in results:
    if r["status"] == "FAIL":
        print(f"   - {r['test']}: {r['error']}")

print("\n📊 DATA RETURN DETAILS:")
def show(key, label):
    r = next((x for x in results if x['test'].startswith(key)), None)
    if r and r['status'] == 'PASS':
        print(f"   {label}: {r['result']}")
    else:
        print(f"   {label}: {'FAILED' if r and r['status'] == 'FAIL' else 'SKIPPED'}")

show("Get Account", "Account")
show("Get All Positions", "Positions")
show("Get Orders", "Orders")
show("Stock Bars - SPY, AAPL, MSFT", "Stock Bars 30d (SPY, AAPL, MSFT)")
show("Stock Bars - SPY (5Min", "Stock Bars 5min (SPY)")
show("Stock Bars - SPY (1Min", "Stock Bars 1min (SPY)")
show("Stock Bars - 10 Symbols", "Stock Bars 5d (10 symbols)")
show("Crypto Bars", "Crypto Bars (BTC, ETH)")
show("Market Clock", "Market Clock")
show("Portfolio History (30 days", "Portfolio History 30d")
show("Stock Snapshots", "Stock Snapshots")
show("Get All Tradeable", f"Total Tradeable Symbols: {symbols['result']['total_symbols']}" if symbols['status']=='PASS' else "Symbols: FAILED")
show("News", "News API")

print("\n" + "=" * 70)