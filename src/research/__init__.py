"""Offline research tooling for the trading-bot.

This namespace is strictly READ-ONLY with respect to production data.

Modules in this namespace:
- Do not import SmartBot, brokerage clients, or live order-submission paths.
- Do not import or write to ``trading_bot.db`` in a way that could mutate it.
  Any DB access must use a ``?mode=ro`` URI.
- Do not submit orders, place trades, or call Alpaca trading endpoints.
- May read historical market data via Alpaca ``StockHistoricalDataClient`` for
  offline labeling.

The point of this namespace is to give audit/research questions a home that
is structurally separate from the live trading path.
"""
