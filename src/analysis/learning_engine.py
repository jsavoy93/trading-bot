"""
Performance analysis and learning module for the trading bot.
Analyzes historical performance to improve trading decisions.
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc

from database import db_manager
from models import Trade, MarketData, TradingSession, PerformanceMetrics

class TradingLearningEngine:
    """Analyzes trading performance and provides learning insights"""
    
    def __init__(self):
        self.performance_cache = {}
        self.pattern_cache = {}
    
    def analyze_symbol_performance(self, symbol: str, days: int = 90) -> Dict[str, Any]:
        """Analyze performance for a specific symbol"""
        try:
            with db_manager.get_session() as db:
                start_date = datetime.utcnow() - timedelta(days=days)
                
                trades = db.query(Trade).filter(
                    Trade.symbol == symbol,
                    Trade.signal_time >= start_date,
                    Trade.status == "FILLED"
                ).all()
                
                if not trades:
                    return {"symbol": symbol, "analysis": "insufficient_data"}
                
                # Calculate basic metrics
                total_trades = len(trades)
                winning_trades = len([t for t in trades if (t.pnl or 0) > 0])
                losing_trades = len([t for t in trades if (t.pnl or 0) < 0])
                total_pnl = sum([t.pnl or 0 for t in trades])\n                
                win_rate = winning_trades / total_trades if total_trades > 0 else 0
                
                # Analyze RSI effectiveness
                rsi_analysis = self._analyze_rsi_effectiveness(trades)
                
                # Analyze timing patterns
                timing_analysis = self._analyze_timing_patterns(trades)
                
                # Calculate volatility impact
                volatility_analysis = self._analyze_volatility_impact(symbol, trades, db)\n                
                return {
                    "symbol": symbol,
                    "period_days": days,
                    "total_trades": total_trades,
                    "win_rate": round(win_rate * 100, 2),
                    "total_pnl": round(total_pnl, 2),
                    "avg_pnl_per_trade": round(total_pnl / total_trades, 4),
                    "rsi_analysis": rsi_analysis,
                    "timing_analysis": timing_analysis,
                    "volatility_analysis": volatility_analysis,
                    "recommendation": self._generate_symbol_recommendation(
                        win_rate, total_pnl, total_trades, rsi_analysis
                    )
                }
                
        except Exception as e:
            logging.error(f"Failed to analyze symbol performance for {symbol}: {e}")
            return {"error": str(e)}
    
    def _analyze_rsi_effectiveness(self, trades: List[Trade]) -> Dict[str, Any]:
        """Analyze how effective RSI signals are"""
        rsi_ranges = [
            ("oversold", 0, 30),
            ("neutral_low", 30, 50),
            ("neutral_high", 50, 70),
            ("overbought", 70, 100)
        ]
        
        analysis = {}
        
        for range_name, low, high in rsi_ranges:
            range_trades = [t for t in trades if t.rsi and low <= t.rsi < high]
            if range_trades:
                winning = len([t for t in range_trades if (t.pnl or 0) > 0])
                total = len(range_trades)
                win_rate = winning / total if total > 0 else 0
                avg_pnl = sum([t.pnl or 0 for t in range_trades]) / total
                
                analysis[range_name] = {
                    "trades": total,
                    "win_rate": round(win_rate * 100, 2),
                    "avg_pnl": round(avg_pnl, 4)
                }
        
        # Find best performing RSI range
        best_range = max(analysis.items(), key=lambda x: x[1]["win_rate"]) if analysis else None
        
        return {
            "ranges": analysis,
            "best_performing_range": best_range[0] if best_range else None,
            "recommendation": self._get_rsi_recommendation(analysis)
        }
    
    def _analyze_timing_patterns(self, trades: List[Trade]) -> Dict[str, Any]:
        """Analyze timing patterns in trades"""
        if not trades:
            return {"message": "No trades to analyze"}
        
        # Analyze by hour of day
        hourly_performance = {}
        for trade in trades:
            hour = trade.signal_time.hour
            if hour not in hourly_performance:
                hourly_performance[hour] = {"trades": 0, "total_pnl": 0}
            
            hourly_performance[hour]["trades"] += 1
            hourly_performance[hour]["total_pnl"] += trade.pnl or 0
        
        # Find best performing hours
        best_hours = sorted(
            hourly_performance.items(),
            key=lambda x: x[1]["total_pnl"] / x[1]["trades"],
            reverse=True
        )[:3]
        
        # Analyze by day of week
        daily_performance = {}
        for trade in trades:
            day = trade.signal_time.strftime("%A")
            if day not in daily_performance:
                daily_performance[day] = {"trades": 0, "total_pnl": 0}
            
            daily_performance[day]["trades"] += 1
            daily_performance[day]["total_pnl"] += trade.pnl or 0
        
        return {
            "hourly_performance": hourly_performance,
            "best_hours": [{"hour": h, "avg_pnl": round(data["total_pnl"] / data["trades"], 4)} 
                          for h, data in best_hours],
            "daily_performance": daily_performance
        }
    
    def _analyze_volatility_impact(self, symbol: str, trades: List[Trade], db: Session) -> Dict[str, Any]:
        """Analyze how market volatility affects trade performance"""
        if not trades:
            return {"message": "No trades to analyze"}
        
        volatility_buckets = {"low": [], "medium": [], "high": []}
        
        for trade in trades:
            # Get market data around trade time
            market_data = db.query(MarketData).filter(
                MarketData.symbol == symbol,
                MarketData.timestamp >= trade.signal_time - timedelta(hours=1),
                MarketData.timestamp <= trade.signal_time + timedelta(hours=1)
            ).first()
            
            if market_data:
                # Calculate simple volatility measure
                volatility = abs(market_data.high_price - market_data.low_price) / market_data.close_price
                
                if volatility < 0.02:  # < 2%
                    volatility_buckets["low"].append(trade)
                elif volatility < 0.05:  # 2-5%
                    volatility_buckets["medium"].append(trade)
                else:  # > 5%
                    volatility_buckets["high"].append(trade)
        
        analysis = {}
        for bucket, bucket_trades in volatility_buckets.items():
            if bucket_trades:
                winning = len([t for t in bucket_trades if (t.pnl or 0) > 0])
                total = len(bucket_trades)
                win_rate = winning / total if total > 0 else 0
                avg_pnl = sum([t.pnl or 0 for t in bucket_trades]) / total
                
                analysis[bucket] = {
                    "trades": total,
                    "win_rate": round(win_rate * 100, 2),
                    "avg_pnl": round(avg_pnl, 4)
                }
        
        return analysis
    
    def _generate_symbol_recommendation(self, win_rate: float, total_pnl: float, 
                                      total_trades: int, rsi_analysis: Dict) -> str:
        """Generate recommendation for trading this symbol"""
        if total_trades < 10:
            return "INSUFFICIENT_DATA - Need more trades for reliable analysis"
        
        if win_rate > 0.6 and total_pnl > 0:
            return "STRONG_BUY - Excellent historical performance"
        elif win_rate > 0.5 and total_pnl > 0:
            return "BUY - Good historical performance"
        elif win_rate > 0.4 and total_pnl > -10:
            return "NEUTRAL - Mixed performance, monitor closely"
        else:
            return "AVOID - Poor historical performance"
    
    def _get_rsi_recommendation(self, rsi_analysis: Dict) -> str:
        """Get recommendation based on RSI analysis"""
        if not rsi_analysis:
            return "No RSI data available"
        
        ranges = rsi_analysis.get("ranges", {})
        if not ranges:
            return "Insufficient RSI data"
        
        # Find range with highest win rate
        best_range = max(ranges.items(), key=lambda x: x[1]["win_rate"])
        
        if best_range[1]["win_rate"] > 60:
            return f"Focus on {best_range[0]} RSI signals (>{best_range[1]['win_rate']:.1f}% win rate)"
        else:
            return "RSI signals show mixed effectiveness - consider additional indicators"
    
    def get_overall_performance_insights(self, days: int = 30) -> Dict[str, Any]:
        """Get overall trading performance insights"""
        try:
            with db_manager.get_session() as db:
                start_date = datetime.utcnow() - timedelta(days=days)
                
                # Get all trades in period
                trades = db.query(Trade).filter(
                    Trade.signal_time >= start_date,
                    Trade.status == "FILLED"
                ).all()
                
                if not trades:
                    return {"message": "No trades found in the specified period"}
                
                # Overall metrics
                total_trades = len(trades)
                winning_trades = len([t for t in trades if (t.pnl or 0) > 0])
                total_pnl = sum([t.pnl or 0 for t in trades])
                
                # Symbol performance breakdown
                symbol_performance = {}
                for trade in trades:
                    symbol = trade.symbol
                    if symbol not in symbol_performance:
                        symbol_performance[symbol] = {"trades": 0, "pnl": 0}
                    
                    symbol_performance[symbol]["trades"] += 1
                    symbol_performance[symbol]["pnl"] += trade.pnl or 0
                
                # Top and bottom performers
                top_symbols = sorted(
                    symbol_performance.items(),
                    key=lambda x: x[1]["pnl"],
                    reverse=True
                )[:5]
                
                bottom_symbols = sorted(
                    symbol_performance.items(),
                    key=lambda x: x[1]["pnl"]
                )[:5]
                
                # Strategy effectiveness
                buy_trades = [t for t in trades if t.side == "BUY"]
                sell_trades = [t for t in trades if t.side == "SELL"]
                
                buy_win_rate = len([t for t in buy_trades if (t.pnl or 0) > 0]) / len(buy_trades) if buy_trades else 0
                sell_win_rate = len([t for t in sell_trades if (t.pnl or 0) > 0]) / len(sell_trades) if sell_trades else 0
                
                return {
                    "period_days": days,
                    "total_trades": total_trades,
                    "winning_trades": winning_trades,
                    "win_rate": round(winning_trades / total_trades * 100, 2),
                    "total_pnl": round(total_pnl, 2),
                    "avg_pnl_per_trade": round(total_pnl / total_trades, 4),
                    "top_performing_symbols": [
                        {"symbol": s, "pnl": round(data["pnl"], 2), "trades": data["trades"]}
                        for s, data in top_symbols
                    ],
                    "worst_performing_symbols": [
                        {"symbol": s, "pnl": round(data["pnl"], 2), "trades": data["trades"]}
                        for s, data in bottom_symbols
                    ],
                    "strategy_analysis": {
                        "buy_trades": len(buy_trades),
                        "sell_trades": len(sell_trades),
                        "buy_win_rate": round(buy_win_rate * 100, 2),
                        "sell_win_rate": round(sell_win_rate * 100, 2)
                    },
                    "recommendations": self._generate_overall_recommendations(
                        winning_trades / total_trades, total_pnl, symbol_performance
                    )
                }
                
        except Exception as e:
            logging.error(f"Failed to get performance insights: {e}")
            return {"error": str(e)}
    
    def _generate_overall_recommendations(self, win_rate: float, total_pnl: float, 
                                        symbol_performance: Dict) -> List[str]:
        """Generate overall trading recommendations"""
        recommendations = []
        
        if win_rate < 0.4:
            recommendations.append("LOW_WIN_RATE - Consider revising entry criteria")
        
        if total_pnl < 0:
            recommendations.append("NEGATIVE_PNL - Review position sizing and risk management")
        
        # Check for overconcentration
        total_symbols = len(symbol_performance)
        if total_symbols < 10:
            recommendations.append("LOW_DIVERSIFICATION - Consider trading more symbols")
        
        # Check for symbols with consistent losses
        losing_symbols = [s for s, data in symbol_performance.items() if data["pnl"] < -10]
        if len(losing_symbols) > total_symbols * 0.3:
            recommendations.append(f"HIGH_LOSS_SYMBOLS - Consider avoiding: {', '.join(losing_symbols[:5])}")
        
        if not recommendations:
            recommendations.append("PERFORMANCE_GOOD - Continue current strategy with minor optimizations")
        
        return recommendations
    
    def suggest_position_sizing(self, symbol: str, account_balance: float) -> Dict[str, Any]:
        """Suggest position size based on historical performance"""
        try:
            performance = self.analyze_symbol_performance(symbol, days=60)
            
            if "error" in performance or performance.get("total_trades", 0) < 5:
                return {
                    "suggested_size": account_balance * 0.01,  # 1% default
                    "confidence": "low",
                    "reason": "Insufficient historical data"
                }
            
            win_rate = performance["win_rate"] / 100
            avg_pnl = performance["avg_pnl_per_trade"]
            
            # Kelly Criterion approximation
            if avg_pnl > 0 and win_rate > 0.5:
                kelly_fraction = min(0.05, (win_rate - 0.5) / abs(avg_pnl))  # Cap at 5%
            else:
                kelly_fraction = 0.01  # Conservative 1%
            
            suggested_size = account_balance * kelly_fraction
            
            return {
                "suggested_size": round(suggested_size, 2),
                "kelly_fraction": round(kelly_fraction * 100, 2),
                "confidence": "high" if performance["total_trades"] > 20 else "medium",
                "reason": f"Based on {performance['total_trades']} historical trades"
            }
            
        except Exception as e:
            logging.error(f"Failed to suggest position sizing for {symbol}: {e}")
            return {
                "suggested_size": account_balance * 0.01,
                "confidence": "low",
                "reason": f"Error in calculation: {str(e)}"
            }

# Global learning engine instance
learning_engine = TradingLearningEngine()