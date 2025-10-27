import os
import time
import logging
from datetime import datetime

import requests
import pandas as pd
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

# Load .env from current directory
load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

if not ALPACA_API_KEY or not ALPACA_API_SECRET or not ALPHAVANTAGE_API_KEY:
    raise RuntimeError("Missing one or more API keys. Check your .env file.")


SYMBOLS = ["AAPL", "MSFT", "NVDA"]
BAR_INTERVAL = "60min"
SLEEP_SECONDS = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

trading_client = TradingClient(
    ALPACA_API_KEY,
    ALPACA_API_SECRET,
    paper=True
)

def get_alpha_timeseries(symbol: str, interval: str = "60min") -> pd.DataFrame:
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_INTRADAY",
        "symbol": symbol,
        "interval": interval,
        "apikey": ALPHAVANTAGE_API_KEY,
        "outputsize": "compact"
    }
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    key = f"Time Series ({interval})"
    if key not in data:
        raise RuntimeError(f"Unexpected Alpha Vantage response: {data}")

    df = (
        pd.DataFrame.from_dict(data[key], orient="index")
        .rename(columns={
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. volume": "volume"
        })
        .astype(float)
        .sort_index()
    )
    df.index = pd.to_datetime(df.index)
    return df

def compute_simple_signals(df: pd.DataFrame) -> dict:
    df["sma_fast"] = df["close"].rolling(10).mean()
    df["sma_slow"] = df["close"].rolling(30).mean()

    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    last = df.iloc[-1]
    signal = "HOLD"

    if last["sma_fast"] > last["sma_slow"] and last["rsi"] < 70:
        signal = "BUY"
    if last["sma_fast"] < last["sma_slow"] or last["rsi"] > 75:
        signal = "SELL"

    return {
        "signal": signal,
        "price": last["close"],
        "sma_fast": last["sma_fast"],
        "sma_slow": last["sma_slow"],
        "rsi": last["rsi"]
    }

def get_positions():
    return {p.symbol: p for p in trading_client.get_all_positions()}

def submit_market_order(symbol: str, side: OrderSide, qty: int):
    req = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
    )
    order = trading_client.submit_order(order_data=req)
    logging.info(f"Submitted {side} order: {order}")
    return order

def main_loop():
    while True:
        try:
            logging.info("--- Cycle start ---")
            positions = get_positions()

            for symbol in SYMBOLS:
                try:
                    df = get_alpha_timeseries(symbol, BAR_INTERVAL)
                    sig = compute_simple_signals(df)

                    logging.info(
                        f"{symbol} signal={sig['signal']} "
                        f"price={sig['price']:.2f} rsi={sig['rsi']:.1f}"
                    )

                    has_position = symbol in positions
                    qty = 1  # TODO: smarter sizing

                    if sig["signal"] == "BUY" and not has_position:
                        submit_market_order(symbol, OrderSide.BUY, qty)
                    elif sig["signal"] == "SELL" and has_position:
                        current_qty = abs(int(float(positions[symbol].qty)))
                        submit_market_order(symbol, OrderSide.SELL, current_qty)

                except Exception as e:
                    logging.exception(f"Error handling symbol {symbol}: {e}")

        except Exception as e:
            logging.exception(f"Cycle-level error: {e}")

        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main_loop()
