#!/usr/bin/env python3
"""
Enhanced Portfolio Dashboard
Comprehensive portfolio analysis with performance metrics and insights
"""

import os
import sys
import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
from core.smart_bot import SmartTradingBot

class PortfolioDashboard:
    def __init__(self):
        load_dotenv()
        self.bot = SmartTradingBot()
        
    def get_portfolio_summary(self):
        """Get current portfolio positions and account info"""
        try:
            # Get account info
            account = self.bot.trading_client.get_account()
            positions = self.bot.trading_client.get_all_positions()
            
            portfolio_data = {
                'account': {
                    'portfolio_value': float(account.portfolio_value),
                    'cash': float(account.cash),
                    'buying_power': float(account.buying_power),
                    'day_trade_buying_power': float(account.daytrading_buying_power),
                },
                'positions': []
            }
            
            total_market_value = 0
            total_unrealized_pnl = 0
            
            for pos in positions:
                position_data = {
                    'symbol': pos.symbol,
                    'qty': float(pos.qty),
                    'market_value': float(pos.market_value),
                    'cost_basis': float(pos.cost_basis),
                    'unrealized_pl': float(pos.unrealized_pl),
                    'unrealized_plpc': float(pos.unrealized_plpc) * 100,
                    'current_price': float(pos.current_price) if pos.current_price else 0,
                    'avg_entry_price': float(pos.avg_entry_price) if pos.avg_entry_price else 0,
                    'side': pos.side.value if pos.side else 'long'
                }
                
                portfolio_data['positions'].append(position_data)
                total_market_value += position_data['market_value']
                total_unrealized_pnl += position_data['unrealized_pl']
            
            portfolio_data['totals'] = {
                'total_positions': len(positions),
                'total_market_value': total_market_value,
                'total_unrealized_pnl': total_unrealized_pnl,
                'total_unrealized_pnl_pct': (total_unrealized_pnl / (total_market_value - total_unrealized_pnl)) * 100 if total_market_value != total_unrealized_pnl else 0
            }
            
            return portfolio_data
            
        except Exception as e:
            print(f"❌ Error getting portfolio data: {e}")
            return None
    
    def analyze_performance(self, portfolio_data):
        """Analyze portfolio performance and risk metrics"""
        if not portfolio_data:
            return None
            
        positions = portfolio_data['positions']
        
        # Performance analysis
        winners = [p for p in positions if p['unrealized_pl'] > 0]
        losers = [p for p in positions if p['unrealized_pl'] < 0]
        
        analysis = {
            'performance': {
                'total_positions': len(positions),
                'winning_positions': len(winners),
                'losing_positions': len(losers),
                'win_rate': (len(winners) / len(positions)) * 100 if positions else 0,
                'avg_winner': sum(p['unrealized_pl'] for p in winners) / len(winners) if winners else 0,
                'avg_loser': sum(p['unrealized_pl'] for p in losers) / len(losers) if losers else 0,
                'largest_winner': max(winners, key=lambda x: x['unrealized_pl']) if winners else None,
                'largest_loser': min(losers, key=lambda x: x['unrealized_pl']) if losers else None,
            },
            'risk': {
                'concentration_risk': self._calculate_concentration_risk(positions),
                'sector_exposure': self._analyze_sector_exposure(positions),
                'position_sizes': [abs(p['market_value']) for p in positions]
            }
        }
        
        return analysis
    
    def _calculate_concentration_risk(self, positions):
        """Calculate portfolio concentration risk"""
        if not positions:
            return {}
            
        total_value = sum(abs(p['market_value']) for p in positions)
        position_weights = [(p['symbol'], abs(p['market_value']) / total_value * 100) for p in positions]
        position_weights.sort(key=lambda x: x[1], reverse=True)
        
        top_5_concentration = sum(weight for _, weight in position_weights[:5])
        
        return {
            'top_5_concentration': top_5_concentration,
            'largest_position': position_weights[0] if position_weights else ('N/A', 0),
            'position_weights': position_weights[:10]  # Top 10 positions
        }
    
    def _analyze_sector_exposure(self, positions):
        """Basic sector analysis (simplified)"""
        # This is a simplified version - in reality you'd use a more sophisticated sector mapping
        tech_symbols = ['AAPL', 'GOOGL', 'MSFT', 'NVDA', 'META', 'FNGU', 'CHPT']
        finance_symbols = ['KKR', 'WSFS', 'V', 'NDAQ']
        energy_symbols = ['ENB', 'WTI', 'DRIP']
        crypto_symbols = ['BITI', 'IREN']
        
        sectors = {'Technology': 0, 'Financial': 0, 'Energy': 0, 'Crypto': 0, 'Other': 0}
        
        for pos in positions:
            symbol = pos['symbol']
            value = abs(pos['market_value'])
            
            if symbol in tech_symbols:
                sectors['Technology'] += value
            elif symbol in finance_symbols:
                sectors['Financial'] += value
            elif symbol in energy_symbols:
                sectors['Energy'] += value
            elif symbol in crypto_symbols:
                sectors['Crypto'] += value
            else:
                sectors['Other'] += value
        
        return sectors
    
    def get_ai_insights(self, portfolio_data, analysis):
        """Get AI-powered portfolio insights"""
        if not self.bot.ai or not self.bot.ai.is_configured:
            return "AI insights not available - AI not configured"
            
        try:
            # Prepare portfolio summary for AI
            top_positions = sorted(portfolio_data['positions'], 
                                 key=lambda x: abs(x['market_value']), reverse=True)[:10]
            
            position_summary = []
            for pos in top_positions:
                position_summary.append(f"{pos['symbol']}: ${pos['market_value']:.0f} ({pos['unrealized_plpc']:.1f}%)")
            
            portfolio_summary = {
                'total_value': portfolio_data['account']['portfolio_value'],
                'total_pnl': portfolio_data['totals']['total_unrealized_pnl'],
                'win_rate': analysis['performance']['win_rate'],
                'top_positions': position_summary[:5],
                'concentration': analysis['risk']['concentration_risk']['top_5_concentration']
            }
            
            prompt = f"""
            Analyze this portfolio performance and provide insights:
            
            Portfolio Value: ${portfolio_summary['total_value']:,.2f}
            Unrealized P&L: ${portfolio_summary['total_pnl']:,.2f}
            Win Rate: {portfolio_summary['win_rate']:.1f}%
            Top 5 Concentration: {portfolio_summary['concentration']:.1f}%
            
            Top Positions:
            {chr(10).join(portfolio_summary['top_positions'])}
            
            Provide:
            1. Performance assessment
            2. Risk analysis
            3. Diversification recommendations
            4. Action items
            
            Be concise and actionable (max 150 words).
            """
            
            insights = self.bot.ai._call_ai_sync(prompt, max_tokens=200)
            return insights if insights else "AI analysis failed"
            
        except Exception as e:
            return f"AI insights error: {e}"
    
    def print_dashboard(self):
        """Print comprehensive portfolio dashboard"""
        print("📊 PORTFOLIO DASHBOARD")
        print("=" * 80)
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Get portfolio data
        portfolio_data = self.get_portfolio_summary()
        if not portfolio_data:
            print("❌ Unable to retrieve portfolio data")
            return
            
        # Account Summary
        account = portfolio_data['account']
        totals = portfolio_data['totals']
        
        print(f"\n💰 ACCOUNT SUMMARY")
        print("-" * 50)
        print(f"Portfolio Value:     ${account['portfolio_value']:>12,.2f}")
        print(f"Cash Available:      ${account['cash']:>12,.2f}")
        print(f"Buying Power:        ${account['buying_power']:>12,.2f}")
        print(f"Total Unrealized P&L: ${totals['total_unrealized_pnl']:>11,.2f}")
        print(f"Total Return:        {totals['total_unrealized_pnl_pct']:>12.2f}%")
        
        # Position Summary
        positions = portfolio_data['positions']
        print(f"\n📈 POSITIONS ({len(positions)} total)")
        print("-" * 80)
        print(f"{'Symbol':<8} {'Qty':<8} {'Value':<12} {'P&L':<12} {'%':<8} {'Price':<10}")
        print("-" * 80)
        
        # Sort by absolute market value
        sorted_positions = sorted(positions, key=lambda x: abs(x['market_value']), reverse=True)
        
        for pos in sorted_positions:
            qty_str = f"{pos['qty']:.0f}" if pos['qty'] == int(pos['qty']) else f"{pos['qty']:.2f}"
            print(f"{pos['symbol']:<8} {qty_str:<8} ${pos['market_value']:<11,.2f} "
                  f"${pos['unrealized_pl']:<11,.2f} {pos['unrealized_plpc']:<7.2f}% "
                  f"${pos['current_price']:<9.2f}")
        
        # Performance Analysis
        analysis = self.analyze_performance(portfolio_data)
        if analysis:
            perf = analysis['performance']
            
            print(f"\n📊 PERFORMANCE METRICS")
            print("-" * 50)
            print(f"Win Rate:            {perf['win_rate']:>12.1f}%")
            print(f"Winning Positions:   {perf['winning_positions']:>12}")
            print(f"Losing Positions:    {perf['losing_positions']:>12}")
            print(f"Avg Winner:          ${perf['avg_winner']:>11,.2f}")
            print(f"Avg Loser:           ${perf['avg_loser']:>11,.2f}")
            
            if perf['largest_winner']:
                winner = perf['largest_winner']
                print(f"Best Performer:      {winner['symbol']} (${winner['unrealized_pl']:.2f})")
            
            if perf['largest_loser']:
                loser = perf['largest_loser']
                print(f"Worst Performer:     {loser['symbol']} (${loser['unrealized_pl']:.2f})")
            
            # Risk Analysis
            risk = analysis['risk']
            concentration = risk['concentration_risk']
            
            print(f"\n⚠️  RISK ANALYSIS")
            print("-" * 50)
            print(f"Top 5 Concentration: {concentration['top_5_concentration']:>12.1f}%")
            print(f"Largest Position:    {concentration['largest_position'][0]} "
                  f"({concentration['largest_position'][1]:.1f}%)")
            
            # Sector breakdown
            sectors = risk['sector_exposure']
            total_sector_value = sum(sectors.values())
            
            print(f"\n🏢 SECTOR ALLOCATION")
            print("-" * 30)
            for sector, value in sorted(sectors.items(), key=lambda x: x[1], reverse=True):
                if value > 0:
                    pct = (value / total_sector_value) * 100
                    print(f"{sector:<15} {pct:>6.1f}%")
        
        # AI Insights
        print(f"\n🤖 AI PORTFOLIO INSIGHTS")
        print("-" * 50)
        ai_insights = self.get_ai_insights(portfolio_data, analysis)
        print(ai_insights)
        
        # Action Items
        print(f"\n✅ RECOMMENDED ACTIONS")
        print("-" * 50)
        
        if analysis:
            # Generate action items based on analysis
            actions = []
            
            if analysis['performance']['win_rate'] < 50:
                actions.append("• Review losing positions - consider stop losses")
            
            if analysis['risk']['concentration_risk']['top_5_concentration'] > 50:
                actions.append("• Consider diversifying - high concentration risk")
            
            if len([p for p in positions if abs(p['unrealized_plpc']) > 20]) > 0:
                actions.append("• Review positions with >20% moves")
            
            if account['cash'] / account['portfolio_value'] > 0.2:
                actions.append("• High cash position - consider investment opportunities")
            
            if not actions:
                actions.append("• Portfolio appears balanced - continue monitoring")
            
            for action in actions:
                print(action)
        
        print(f"\n" + "=" * 80)

def main():
    """Main function"""
    dashboard = PortfolioDashboard()
    dashboard.print_dashboard()

if __name__ == "__main__":
    main()