"""
spread_executor.py
==================
Implements the Hedging Module from options.json.

When DTE <= 3 (EXTREME theta risk), the bot automatically constructs
Debit Spreads instead of buying naked options:

  Bullish Signal -> Bull Call Spread:
    Buy ATM CE + Sell 1-2 strikes OTM CE

  Bearish Signal -> Bear Put Spread:
    Buy ATM PE + Sell 1-2 strikes OTM PE

This drastically reduces theta decay impact and caps maximum loss.
"""

import logging

logger = logging.getLogger(__name__)


class SpreadCalculator:
    """
    Calculates all debit spread metrics per options.json specification.
    """
    
    def __init__(self, strike_gap=50, lot_size=65):
        self.strike_gap = strike_gap
        self.lot_size = lot_size
    
    def build_bull_call_spread(self, atm_strike, buy_premium, sell_premium_estimate=None):
        """
        Bull Call Spread:
          Buy ATM CE at buy_premium
          Sell (ATM + strike_gap) CE at sell_premium_estimate
          
        If sell_premium_estimate is None, we estimate it as ~60% of buy_premium (typical OTM decay).
        """
        sell_strike = atm_strike + self.strike_gap
        
        if sell_premium_estimate is None:
            sell_premium_estimate = buy_premium * 0.60
        
        net_debit = buy_premium - sell_premium_estimate
        max_loss  = net_debit * self.lot_size
        max_profit = (self.strike_gap - net_debit) * self.lot_size
        breakeven = atm_strike + net_debit
        
        return {
            "spread_type": "BULL_CALL_SPREAD",
            "buy_strike": atm_strike,
            "buy_type": "CE",
            "buy_premium": round(buy_premium, 2),
            "sell_strike": sell_strike,
            "sell_type": "CE",
            "sell_premium": round(sell_premium_estimate, 2),
            "net_debit": round(net_debit, 2),
            "max_loss": round(max_loss, 2),
            "max_profit": round(max_profit, 2),
            "breakeven": round(breakeven, 2),
            "lot_size": self.lot_size,
        }
    
    def build_bear_put_spread(self, atm_strike, buy_premium, sell_premium_estimate=None):
        """
        Bear Put Spread:
          Buy ATM PE at buy_premium
          Sell (ATM - strike_gap) PE at sell_premium_estimate
        """
        sell_strike = atm_strike - self.strike_gap
        
        if sell_premium_estimate is None:
            sell_premium_estimate = buy_premium * 0.60
        
        net_debit = buy_premium - sell_premium_estimate
        max_loss  = net_debit * self.lot_size
        max_profit = (self.strike_gap - net_debit) * self.lot_size
        breakeven = atm_strike - net_debit
        
        return {
            "spread_type": "BEAR_PUT_SPREAD",
            "buy_strike": atm_strike,
            "buy_type": "PE",
            "buy_premium": round(buy_premium, 2),
            "sell_strike": sell_strike,
            "sell_type": "PE",
            "sell_premium": round(sell_premium_estimate, 2),
            "net_debit": round(net_debit, 2),
            "max_loss": round(max_loss, 2),
            "max_profit": round(max_profit, 2),
            "breakeven": round(breakeven, 2),
            "lot_size": self.lot_size,
        }

    def build_spread(self, direction, atm_strike, buy_premium, sell_premium_estimate=None):
        """Auto-dispatch based on signal direction."""
        if direction == "CE":
            return self.build_bull_call_spread(atm_strike, buy_premium, sell_premium_estimate)
        elif direction == "PE":
            return self.build_bear_put_spread(atm_strike, buy_premium, sell_premium_estimate)
        return None


    def simulate_spread_pnl(self, spread, spot_at_expiry):
        """
        Calculate exact P&L of a spread given the settlement price.
        Used in backtesting to evaluate spread performance.
        """
        if spread["spread_type"] == "BULL_CALL_SPREAD":
            buy_payoff  = max(0, spot_at_expiry - spread["buy_strike"])
            sell_payoff = max(0, spot_at_expiry - spread["sell_strike"])
            net_payoff  = buy_payoff - sell_payoff
            pnl = (net_payoff - spread["net_debit"]) * spread["lot_size"]
            
        elif spread["spread_type"] == "BEAR_PUT_SPREAD":
            buy_payoff  = max(0, spread["buy_strike"] - spot_at_expiry)
            sell_payoff = max(0, spread["sell_strike"] - spot_at_expiry)
            net_payoff  = buy_payoff - sell_payoff
            pnl = (net_payoff - spread["net_debit"]) * spread["lot_size"]
        else:
            pnl = 0
        
        return round(pnl, 2)


if __name__ == "__main__":
    calc = SpreadCalculator(strike_gap=50, lot_size=65)
    
    # Example: Nifty at 22500
    bull = calc.build_bull_call_spread(22500, buy_premium=85.0)
    print("--- Bull Call Spread ---")
    for k, v in bull.items():
        print(f"  {k}: {v}")
    
    bear = calc.build_bear_put_spread(22500, buy_premium=90.0)
    print("\n--- Bear Put Spread ---")
    for k, v in bear.items():
        print(f"  {k}: {v}")
    
    # Simulate P&L
    print(f"\nBull spread P&L if Nifty settles at 22550: Rs. {calc.simulate_spread_pnl(bull, 22550)}")
    print(f"Bull spread P&L if Nifty settles at 22450: Rs. {calc.simulate_spread_pnl(bull, 22450)}")
