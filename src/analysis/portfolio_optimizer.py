"""
Portfolio Optimization Engine

Implements Markowitz mean-variance optimization to find the
maximum-Sharpe portfolio from current holdings.

Usage:
    optimizer = PortfolioOptimizer()
    result = optimizer.run_optimization_pass(bot)
    # result['rebalance_trades'] contains sorted rebalance suggestions
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PortfolioOptimizer:
    """
    Markowitz mean-variance optimizer targeting maximum Sharpe ratio.

    Works with the existing SmartTradingBot architecture:
      - Reads positions from Alpaca via bot.trading_client
      - Reads price history via bot.get_market_data()
      - Logs rebalance recommendations (does NOT auto-execute trades)
    """

    def __init__(
        self,
        risk_free_rate: float = 0.05,       # Annual T-bill rate
        lookback_days: int = 60,             # Return estimation window
        max_position_weight: float = 0.15,   # Max weight per position
        min_position_weight: float = 0.01,   # Min weight (avoid noise)
        rebalance_threshold: float = 0.05,   # Suggest trade if drift > 5%
    ):
        self.risk_free_rate = risk_free_rate
        self.lookback_days = lookback_days
        self.max_position_weight = max_position_weight
        self.min_position_weight = min_position_weight
        self.rebalance_threshold = rebalance_threshold

        self._last_optimal_weights: Dict[str, float] = {}
        self._last_sharpe: float = 0.0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_returns(
        self, symbols: List[str], bot
    ) -> Optional[pd.DataFrame]:
        """
        Fetch daily close prices via bot.get_market_data() and compute
        log returns.  Returns a DataFrame (rows=days, cols=symbols) or
        None if fewer than 2 symbols have sufficient data.
        """
        prices: Dict[str, np.ndarray] = {}
        for sym in symbols:
            try:
                df = bot.get_market_data(sym)
                if df is not None and len(df) >= self.lookback_days:
                    prices[sym] = df["close"].tail(self.lookback_days).values
            except Exception:
                continue

        if len(prices) < 2:
            return None

        min_len = min(len(v) for v in prices.values())
        price_df = pd.DataFrame(
            {k: v[-min_len:] for k, v in prices.items()}
        )
        log_returns = np.log(price_df / price_df.shift(1)).dropna()
        return log_returns if len(log_returns) >= 20 else None

    def _max_sharpe_weights(
        self, returns_df: pd.DataFrame
    ) -> Tuple[np.ndarray, float]:
        """
        Compute maximum-Sharpe portfolio weights via scipy SLSQP.

        Returns:
            (weights_array, annualised_sharpe_ratio)
        """
        try:
            from scipy.optimize import minimize
        except ImportError:
            logger.error("scipy not installed — run: pip install scipy>=1.10.0")
            n = returns_df.shape[1]
            return np.ones(n) / n, 0.0

        n = returns_df.shape[1]
        mu = returns_df.mean() * 252          # Annualised expected return
        sigma = returns_df.cov() * 252        # Annualised covariance

        def neg_sharpe(w: np.ndarray) -> float:
            port_return = float(np.dot(w, mu))
            port_vol = float(np.sqrt(w @ sigma.values @ w))
            if port_vol < 1e-10:
                return 0.0
            return -(port_return - self.risk_free_rate) / port_vol

        constraints = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
        bounds = tuple(
            (self.min_position_weight, self.max_position_weight)
            for _ in range(n)
        )
        x0 = np.ones(n) / n

        result = minimize(
            neg_sharpe,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )

        if result.success:
            weights = result.x
            sharpe = -result.fun
        else:
            logger.warning("Optimizer did not converge — using equal weights")
            weights = x0
            sharpe = float(-neg_sharpe(x0))

        return weights, sharpe

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_optimal_weights(
        self, symbols: List[str], bot
    ) -> Dict[str, float]:
        """
        Given a list of symbols, return the optimal weight dictionary
        {symbol: weight} that maximises the Sharpe ratio.
        """
        returns_df = self._get_returns(symbols, bot)
        if returns_df is None or returns_df.shape[1] < 2:
            logger.warning(
                "Insufficient data for optimisation — returning equal weights"
            )
            w = 1.0 / len(symbols) if symbols else 0.0
            return {s: w for s in symbols}

        valid_symbols = list(returns_df.columns)
        weights_arr, sharpe = self._max_sharpe_weights(returns_df)

        weights = {
            sym: float(w)
            for sym, w in zip(valid_symbols, weights_arr)
        }
        self._last_optimal_weights = weights
        self._last_sharpe = sharpe

        logger.info(
            f"Optimal weights computed: Sharpe={sharpe:.3f} "
            f"across {len(weights)} symbols"
        )
        return weights

    def compute_current_sharpe(
        self, symbols: List[str], weights: List[float], bot
    ) -> float:
        """Compute Sharpe ratio for the given portfolio weights."""
        returns_df = self._get_returns(symbols, bot)
        if returns_df is None:
            return 0.0

        w = np.array(weights, dtype=float)
        w = w / w.sum()

        mu = returns_df.mean() * 252
        sigma = returns_df.cov() * 252

        port_return = float(np.dot(w, mu))
        port_vol = float(np.sqrt(w @ sigma.values @ w))
        if port_vol < 1e-10:
            return 0.0
        return (port_return - self.risk_free_rate) / port_vol

    def get_rebalance_trades(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
        portfolio_value: float,
        prices: Dict[str, float],
    ) -> List[Dict]:
        """
        Compute the trades needed to move from current_weights to
        target_weights.  Only includes a trade when the absolute weight
        drift exceeds self.rebalance_threshold.

        Returns a list of dicts sorted by drift (largest first):
            symbol, action, delta_shares, current_weight, target_weight, drift
        """
        trades = []
        all_symbols = set(current_weights) | set(target_weights)

        for sym in all_symbols:
            cw = current_weights.get(sym, 0.0)
            tw = target_weights.get(sym, 0.0)
            drift = abs(tw - cw)

            if drift < self.rebalance_threshold:
                continue

            delta_value = (tw - cw) * portfolio_value
            price = prices.get(sym, 0.0)
            if price <= 0:
                continue

            delta_shares = int(abs(delta_value) / price)
            if delta_shares < 1:
                continue

            trades.append(
                {
                    "symbol": sym,
                    "action": "BUY" if delta_value > 0 else "SELL",
                    "delta_shares": delta_shares,
                    "current_weight": round(cw, 4),
                    "target_weight": round(tw, 4),
                    "drift": round(drift, 4),
                }
            )

        trades.sort(key=lambda x: x["drift"], reverse=True)
        return trades

    def run_optimization_pass(self, bot) -> Dict:
        """
        Main entry point called from run_continuous_loop().

        1. Reads current open positions from Alpaca.
        2. Computes current portfolio Sharpe.
        3. Optimises for maximum Sharpe.
        4. Logs rebalance suggestions.
        5. Returns result dict (does NOT auto-execute trades).

        Returns:
            {
                'current_sharpe': float,
                'optimal_sharpe': float,
                'optimal_weights': {symbol: weight},
                'rebalance_trades': [...],
                'symbols': [...]
            }
        """
        try:
            positions = bot.trading_client.get_all_positions()
            if not positions or len(positions) < 2:
                logger.info(
                    "Portfolio optimisation skipped — need >= 2 open positions"
                )
                return {}

            portfolio_value = bot.get_portfolio_total_value()
            if portfolio_value <= 0:
                return {}

            symbols = [p.symbol for p in positions]
            market_values = {
                p.symbol: float(p.market_value) for p in positions
            }
            prices = {
                p.symbol: float(p.current_price)
                for p in positions
                if p.current_price
            }
            current_weights = {
                sym: val / portfolio_value
                for sym, val in market_values.items()
            }

            # Current Sharpe
            cw_list = [current_weights.get(s, 0.0) for s in symbols]
            current_sharpe = self.compute_current_sharpe(symbols, cw_list, bot)

            # Optimal weights
            optimal_weights = self.compute_optimal_weights(symbols, bot)
            optimal_sharpe = self._last_sharpe

            # Rebalance trades
            rebalance_trades = self.get_rebalance_trades(
                current_weights, optimal_weights, portfolio_value, prices
            )

            logger.info(
                f"Portfolio Optimisation: "
                f"current Sharpe={current_sharpe:.3f}  "
                f"optimal Sharpe={optimal_sharpe:.3f}  "
                f"({len(rebalance_trades)} rebalance suggestions)"
            )
            for trade in rebalance_trades:
                logger.info(
                    f"  Rebalance: {trade['action']} {trade['symbol']} "
                    f"({trade['current_weight']*100:.1f}% → "
                    f"{trade['target_weight']*100:.1f}%, "
                    f"drift {trade['drift']*100:.1f}%)"
                )

            return {
                "current_sharpe": current_sharpe,
                "optimal_sharpe": optimal_sharpe,
                "optimal_weights": optimal_weights,
                "rebalance_trades": rebalance_trades,
                "symbols": symbols,
            }

        except Exception as e:
            logger.error(f"Portfolio optimisation failed: {e}")
            return {}
