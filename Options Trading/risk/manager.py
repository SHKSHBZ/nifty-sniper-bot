import numpy as np
import logging

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, config):
        # Mandatory limits from specification
        self.MAX_DAILY_LOSS = config.get('max_daily_loss', -5000)
        self.MAX_TRADES_DAY = config.get('max_trades_day', 100)
        self.MAX_POSITION_SIZE = config.get('max_position_size', 50)
        self.MAX_DRAWDOWN = config.get('max_drawdown', -10000)
        
        # State tracking
        self.daily_pnl = 0.0
        self.trade_count = 0
        self.current_drawdown = 0.0
        self.peak_pnl = 0.0
        
        # Kelly Criterion parameters
        self.win_rate = 0.55  # Initial estimate, should be updated dynamically
        self.win_loss_ratio = 1.5 # Initial estimate

    def update_pnl(self, trade_pnl):
        """Updates P&L and checks for circuit breakers."""
        self.daily_pnl += trade_pnl
        self.trade_count += 1
        
        # Update drawdown
        if self.daily_pnl > self.peak_pnl:
            self.peak_pnl = self.daily_pnl
        
        self.current_drawdown = self.daily_pnl - self.peak_pnl
        
        logger.info(f"P&L Updated: {self.daily_pnl:.2f}, Drawdown: {self.current_drawdown:.2f}, Trades: {self.trade_count}")

    def can_trade(self, proposed_size, current_vix=None):
        """
        Main safety check. Returns (bool, reason).
        """
        # 1. Daily Loss Limit
        if self.daily_pnl <= self.MAX_DAILY_LOSS:
            return False, "DAILY_LOSS_LIMIT_REACHED"
        
        # 2. Max Trades per Day
        if self.trade_count >= self.MAX_TRADES_DAY:
            return False, "MAX_TRADES_LIMIT_REACHED"
        
        # 3. Emergency Drawdown Stop
        if self.current_drawdown <= self.MAX_DRAWDOWN:
            return False, "MAX_DRAWDOWN_LIMIT_REACHED"
            
        # 4. Position Size Cap
        if proposed_size > self.MAX_POSITION_SIZE:
            return False, "MAX_POSITION_SIZE_EXCEEDED"

        return True, "OK"

    def calculate_kelly_size(self, capital, win_rate=None, win_loss_ratio=None):
        """
        Kelly Criterion: f* = p/a - q/b 
        Simplified: f* = (p * (b + 1) - 1) / b
        where p = win_rate, b = win_loss_ratio
        """
        p = win_rate or self.win_rate
        b = win_loss_ratio or self.win_loss_ratio
        
        f_star = (p * (b + 1) - 1) / b
        # Apply a 'fractional Kelly' for safety (e.g., Half-Kelly)
        f_star = max(0, f_star * 0.5)
        
        # Calculate lot size (assuming Nifty lot size 50, but can be dynamic)
        lot_size = 50
        max_capital_at_risk = capital * f_star
        # Simplified lot calculation, should be refined based on option premium
        recommended_lots = int(max_capital_at_risk / (lot_size * 100)) # dummy divisor
        
        return min(recommended_lots, self.MAX_POSITION_SIZE)

class CircuitBreaker:
    """Extra layer for immediate stops."""
    def __init__(self, risk_manager):
        self.rm = risk_manager
        self.is_active = False

    def check(self):
        if self.rm.daily_pnl <= self.rm.MAX_DAILY_LOSS:
            self.is_active = True
            logger.critical("CIRCUIT BREAKER: Daily loss limit triggered. Halting all trades.")
            return True
        if self.rm.current_drawdown <= self.rm.MAX_DRAWDOWN:
            self.is_active = True
            logger.critical("CIRCUIT BREAKER: Max drawdown triggered. Halting all trades.")
            return True
        return False

if __name__ == "__main__":
    # Test stub
    config = {'max_daily_loss': -5000, 'max_trades_day': 100, 'max_position_size': 50}
    rm = RiskManager(config)
    
    can_trade, reason = rm.can_trade(10)
    print(f"Can trade 10 lots? {can_trade} ({reason})")
    
    rm.update_pnl(-6000)
    can_trade, reason = rm.can_trade(10)
    print(f"Can trade after loss? {can_trade} ({reason})")
