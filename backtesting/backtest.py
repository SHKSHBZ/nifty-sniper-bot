import os
import yaml
import pandas as pd
import numpy as np
import logging
from datetime import datetime

# Import project modules
from agents.pso_swarm import PSOSwarm
from rl.qlearning import QLearningAgent
from rl.policy import TradingPolicy
from risk.manager import RiskManager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BacktestEngine")

class BacktestEngine:
    def __init__(self, config, historical_csv_path):
        self.config = config
        self.data = pd.read_csv(historical_csv_path)
        self.data['timestamp'] = pd.to_datetime(self.data['timestamp'])
        
        # Initialize Core Layers
        self.swarm = PSOSwarm(
            num_agents=config.get('num_agents', 500),
            config=config,
            use_gpu=config.get('use_gpu', False)
        )
        self.rl_agent = QLearningAgent(config=config)
        self.policy = TradingPolicy(self.rl_agent, self.swarm)
        self.risk_manager = RiskManager(config)
        
        # Metrics tracking
        self.trades = []
        self.equity_curve = []
        self.initial_capital = 100000  # Default 1 Lakh INR
        self.current_capital = self.initial_capital

    def run(self):
        logger.info(f"Starting backtest on {len(self.data)} historical candles...")
        
        # Slippage/Fee config
        slip_pct = self.config.get('slippage_percent', 0.05) / 100.0
        lot_size = self.config.get('lot_size', 50)
        
        for i in range(20, len(self.data)): 
            row = self.data.iloc[i]
            
            # 1. State Extraction (8-feature)
            current_state = self._get_refined_state(i, row)
            
            # 2. Unified Decision (TradingPolicy)
            # This handles both RL action and Swarm updates internally
            self.swarm.update_velocity_position() # Update swarm
            proposal, action_idx = self.policy.select_best_proposal(current_state)
            
            # 3. Execution (Option Premium Simulation)
            pnl = 0
            if action_idx > 0: # Agent decided to trade
                can_trade, reason = self.risk_manager.can_trade(proposal[3])
                if can_trade:
                    # In backtest, we simulate entry with slippage
                    entry_price = row['close'] * (1 + slip_pct)
                    target = entry_price * (1 + proposal[1] * 0.01)
                    stop = entry_price * (1 - proposal[2] * 0.01)
                    qty = int(proposal[3] * lot_size)
                    
                    if i + 1 < len(self.data):
                        next_row = self.data.iloc[i+1]
                        if next_row['high'] >= target:
                            pnl = (target - entry_price) * qty
                            # Deduct fee (Upstox: ~20 INR per order)
                            pnl -= 40 # Round trip fee
                            self._log_trade(row['timestamp'], "BUY", entry_price, target, "TARGET", pnl)
                        elif next_row['low'] <= stop:
                            pnl = (stop - entry_price) * qty
                            pnl -= 40
                            self._log_trade(row['timestamp'], "BUY", entry_price, stop, "STOP", pnl)
            
            # 4. RL Update (Learning)
            reward = self.rl_agent.calculate_reward(pnl, slip_pct*100, 0.1, 0.005, action_idx)
            self.rl_agent.update(current_state, action_idx, reward, current_state, i == len(self.data)-1)

        self._report()

    def _get_refined_state(self, i, row):
        """Helper to compute indicators for backtest state."""
        prev_row = self.data.iloc[i-1]
        price_change = row['close'] - prev_row['close']
        trend = (row['close'] - self.data.iloc[i-10]['close']) / 10.0
        atr = self.data.iloc[i-20:i]['high'].max() - self.data.iloc[i-20:i]['low'].min()
        
        delta = self.data['close'].iloc[i-14:i+1].diff()
        up = delta.clip(lower=0).mean()
        down = -delta.clip(upper=0).mean()
        rs = up / (down + 0.0001)
        rsi = 100 - (100 / (1 + rs))
        
        m_expiry = (375 - (i % 375)) / 375 
        # Position Flag: Check if the last trade is still open
        has_pos = 1.0 if len(self.trades) > 0 and self.trades[-1]['reason'] not in ["TARGET", "STOP", "END_OF_BAR"] else 0.0

        return [
            np.clip((price_change/10 + 1)/2, 0, 1), 
            np.clip(row['volume'] / (self.data.iloc[i-20:i]['volume'].mean() + 1), 0, 1),
            np.clip((trend + 10) / 20, 0, 1),
            np.clip(atr / 50, 0, 1),
            0.01, 
            np.clip(m_expiry, 0, 1),
            np.clip(rsi / 100, 0, 1),
            has_pos
        ]

    def _log_trade(self, time, side, entry, exit, reason, pnl):
        self.trades.append({
            'timestamp': time,
            'side': side,
            'entry': entry,
            'exit': exit,
            'reason': reason,
            'pnl': pnl
        })
        self.current_capital += pnl
        self.equity_curve.append(self.current_capital)
        self.risk_manager.update_pnl(pnl)

    def _report(self):
        df_trades = pd.DataFrame(self.trades)
        if df_trades.empty:
            logger.info("No trades were executed during the backtest.")
            return

        win_rate = (df_trades['pnl'] > 0).mean() * 100
        total_pnl = df_trades['pnl'].sum()
        max_drawdown = (pd.Series(self.equity_curve).cummax() - self.equity_curve).max()
        
        logger.info("--- BACKTEST REPORT ---")
        logger.info(f"Total Trades: {len(df_trades)}")
        logger.info(f"Win Rate: {win_rate:.2f}%")
        logger.info(f"Total P&L: {total_pnl:.2f} INR")
        logger.info(f"Max Drawdown: {max_drawdown:.2f} INR")
        logger.info(f"Final Capital: {self.current_capital:.2f} INR")
        
        # Save results
        df_trades.to_csv("historical/backtest_results.csv", index=False)

if __name__ == "__main__":
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    
    # Use the sample file if it exists, otherwise it will fail
    csv_path = "historical/nifty_50_7d.csv"
    if os.path.exists(csv_path):
        engine = BacktestEngine(config, csv_path)
        engine.run()
    else:
        logger.error(f"Backtest data not found at {csv_path}. Run historical/downloader.py first.")
