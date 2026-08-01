import logging
import time
import pandas as pd
import os
from datetime import datetime

logger = logging.getLogger(__name__)

class PaperExecutor:
    """
    Simulates a broker for paper trading.
    Handles virtual order execution, position tracking, and P&L calculation.
    """
    def __init__(self, config, initial_state=None):
        self.config = config
        self.initial_capital = config.get('initial_capital', 500000)
        
        if initial_state:
            # Load carry-forward state
            self.balances = {
                'NSE_INDEX|Nifty 50': initial_state.get('nifty_bal', self.initial_capital),
                'NSE_INDEX|Nifty Bank': initial_state.get('bank_bal', self.initial_capital)
            }
            self.total_pnls = {
                'NSE_INDEX|Nifty 50': initial_state.get('nifty_pnl', 0.0),
                'NSE_INDEX|Nifty Bank': initial_state.get('bank_pnl', 0.0)
            }
            self.trades_history_count = initial_state.get('total_trades', 0)
            self.positions = initial_state.get('open_positions') or {}
        else:
            # Default initialization
            self.balances = {
                'NSE_INDEX|Nifty 50': self.initial_capital,
                'NSE_INDEX|Nifty Bank': self.initial_capital
            }
            self.total_pnls = {
                'NSE_INDEX|Nifty 50': 0.0,
                'NSE_INDEX|Nifty Bank': 0.0
            }
            self.trades_history_count = 0
            self.positions = {} # {instrument_key: {'qty': 0, 'avg_price': 0}}
            
        self.trades_history = []
        self.slippage_percent = config.get('slippage_percent', 0.05) / 100.0
        self.latency_ms = config.get('latency_ms', 50) / 1000.0
        
        # Performance tracking
        self.equity_curve = [] # List of (timestamp, equity)

    def place_order(self, symbol, side, quantity, price, order_type='MARKET', is_option=False):
        """
        Simulates order placement. 
        - Index Futures: 10% Margin
        - Options Buying: 100% Premium (Full Price * Quantity)
        """
        # Determine parent index for wallet tracking
        parent_index = 'NSE_INDEX|Nifty 50' if 'NIFTY' in symbol else 'NSE_INDEX|Nifty Bank'
        balance = self.balances.get(parent_index, self.initial_capital)
        
        # 2. Margin Check
        pos = self.positions.get(symbol, {'qty': 0})
        is_closing = (side == 'BUY' and pos.get('qty', 0) < 0) or (side == 'SELL' and pos.get('qty', 0) > 0)
        if not is_closing:
            total_value = price * quantity
            margin_required = total_value if is_option else (total_value * 0.10)
            
            if margin_required > balance:
                logger.warning(f"MARGIN REJECTED ({symbol}): Required {margin_required:.2f}, Balance {balance:.2f}")
                return None

        # 3. Simulate Network Latency
        if self.latency_ms > 0:
            time.sleep(self.latency_ms)

        # 4. Apply Slippage
        execution_price = price
        if side == 'BUY':
            execution_price *= (1 + self.slippage_percent)
        else:
            execution_price *= (1 - self.slippage_percent)

        # 5. Process Execution
        order_id = f"PAPER_{int(time.time() * 1000)}"
        timestamp = datetime.now().isoformat()

        self._update_position(symbol, side, quantity, execution_price, is_option, parent_index)
        
        trade_info = {
            'order_id': order_id,
            'timestamp': timestamp,
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'price': execution_price,
            'status': 'FILLED'
        }
        self.trades_history.append(trade_info)
        
        logger.info(f"PAPER_ORDER FILLED: {side} {quantity} {symbol} @ {execution_price:.2f}")
        return trade_info

    def _update_position(self, symbol, side, quantity, price, is_option, parent_index):
        if symbol not in self.positions:
            self.positions[symbol] = {'qty': 0, 'avg_price': 0.0, 'is_option': is_option, 'parent': parent_index}

        pos = self.positions[symbol]
        balance = self.balances.get(parent_index, self.initial_capital)
        total_pnl = self.total_pnls.get(parent_index, 0.0)
        
        margin_factor = 1.0 if is_option else 0.10
        
        # Standard Long/Short model
        if side == 'BUY':
            if pos['qty'] < 0: # Closing a short
                closed_qty = min(abs(pos['qty']), quantity)
                realized_pnl = (pos['avg_price'] - price) * closed_qty
                balance += (pos['avg_price'] * closed_qty * margin_factor) + realized_pnl 
                total_pnl += realized_pnl
                pos['qty'] += quantity
            else: # Opening long
                new_qty = pos['qty'] + quantity
                pos['avg_price'] = (pos['avg_price'] * pos['qty'] + price * quantity) / new_qty
                pos['qty'] = new_qty
                balance -= (price * quantity * margin_factor) 
        else: # SELL
            if pos['qty'] > 0: # Closing a long
                closed_qty = min(pos['qty'], quantity)
                realized_pnl = (price - pos['avg_price']) * closed_qty
                balance += (pos['avg_price'] * closed_qty * margin_factor) + realized_pnl 
                total_pnl += realized_pnl
                pos['qty'] -= quantity
            else: # Opening short
                new_qty = pos['qty'] - quantity
                pos['avg_price'] = (pos['avg_price'] * abs(pos['qty']) + price * quantity) / abs(new_qty)
                pos['qty'] = new_qty
                balance -= (price * quantity * margin_factor) 

        self.balances[parent_index] = balance
        self.total_pnls[parent_index] = total_pnl
        if pos['qty'] == 0:
            pos['avg_price'] = 0.0

    def get_portfolio_value(self, current_prices):
        """Calculates current total equity across all wallets."""
        total_equity = 0
        for symbol, balance in self.balances.items():
            unrealized_pnl = 0
            pos = self.positions.get(symbol, {'qty': 0, 'avg_price': 0})
            if pos['qty'] != 0 and symbol in current_prices:
                unrealized_pnl = (current_prices[symbol] - pos['avg_price']) * pos['qty']
            total_equity += balance + unrealized_pnl
            
        self.equity_curve.append((datetime.now().isoformat(), total_equity))
        return total_equity

    def get_summary(self):
        return {
            'balances': self.balances,
            'total_pnls': self.total_pnls,
            'open_positions': {s: p for s, p in self.positions.items() if p['qty'] != 0},
            'trade_count': self.trades_history_count + len(self.trades_history)
        }

    def get_current_capital(self, symbol=None):
        """Returns current capital in the wallet (Balance + Unrealized)."""
        if symbol:
            balance = self.balances.get(symbol, self.initial_capital)
            pos = self.positions.get(symbol, {'qty': 0, 'avg_price': 0})
            unrealized = 0 # Simple check
            return balance + unrealized
        # Return total for the Nifty wallet (Primary)
        return self.balances.get('NSE_INDEX|Nifty 50', self.initial_capital)

    def get_total_pnl(self):
        """Returns today's total realized PnL across all wallets."""
        return sum(self.total_pnls.values())

    def place_iron_condor(self, symbols, lots):
        """
        Executes a 4-leg Iron Condor as a single strategic unit.
        Places legs with a small simulated execution delay.
        """
        success = True
        legs = [
            (symbols['sell_call'], 'SELL', lots * symbols['lot_size']),
            (symbols['buy_call'],  'BUY',  lots * symbols['lot_size']),
            (symbols['sell_put'],  'SELL', lots * symbols['lot_size']),
            (symbols['buy_put'],   'BUY',  lots * symbols['lot_size'])
        ]
        
        for symbol, side, qty in legs:
            # For options, we assume a mid-price fill if no price provided
            # In live, this would be a market or limit order.
            trade = self.place_order(symbol, side, qty, price=0, is_option=True)
            if not trade:
                success = False
                logger.error(f"FAILED TO FILL IRON CONDOR LEG: {symbol} {side}")
            time.sleep(0.1) # 100ms standard execution delay
            
        return success

    def place_short_straddle(self, symbols, lots, ce_price=0, pe_price=0):
        """
        Executes a 2-leg Short Straddle (sell CE + sell PE) as a strategic unit.
        If any leg fails, executes compensating rollback to close filled legs and prevent naked short exposure.
        """
        lot_size = symbols.get('lot_size', 50)
        qty = lots * lot_size
        legs = [
            (symbols['ce'], 'SELL', qty, ce_price),
            (symbols['pe'], 'SELL', qty, pe_price)
        ]
        results = []
        success = True
        for symbol, side, leg_qty, px in legs:
            trade = self.place_order(symbol, side, leg_qty, price=px, is_option=True)
            if not trade:
                success = False
                logger.error(f"FAILED TO FILL SHORT STRADDLE LEG: {symbol} {side}")
                results.append(None)
                break
            results.append(trade)
            if self.latency_ms > 0:
                time.sleep(0.1)

        while len(results) < len(legs):
            results.append(None)

        if not success:
            logger.warning("Short Straddle leg failure detected; initiating compensating rollback...")
            for idx, trade in enumerate(results):
                if trade is not None:
                    sym, side, leg_qty, px = legs[idx]
                    rollback_side = 'BUY' if side == 'SELL' else 'SELL'
                    rollback_px = trade.get('price', px)
                    self.place_order(sym, rollback_side, leg_qty, price=rollback_px, is_option=True)

        return {"success": success, "trades": results}

    def place_short_strangle(self, symbols, lots, ce_price=0, pe_price=0):
        """
        Executes a 2-leg Short Strangle (sell OTM CE + sell OTM PE) as a strategic unit.
        If any leg fails, executes compensating rollback to close filled legs and prevent naked short exposure.
        """
        lot_size = symbols.get('lot_size', 50)
        qty = lots * lot_size
        legs = [
            (symbols['otm_ce'], 'SELL', qty, ce_price),
            (symbols['otm_pe'], 'SELL', qty, pe_price)
        ]
        results = []
        success = True
        for symbol, side, leg_qty, px in legs:
            trade = self.place_order(symbol, side, leg_qty, price=px, is_option=True)
            if not trade:
                success = False
                logger.error(f"FAILED TO FILL SHORT STRANGLE LEG: {symbol} {side}")
                results.append(None)
                break
            results.append(trade)
            if self.latency_ms > 0:
                time.sleep(0.1)

        while len(results) < len(legs):
            results.append(None)

        if not success:
            logger.warning("Short Strangle leg failure detected; initiating compensating rollback...")
            for idx, trade in enumerate(results):
                if trade is not None:
                    sym, side, leg_qty, px = legs[idx]
                    rollback_side = 'BUY' if side == 'SELL' else 'SELL'
                    rollback_px = trade.get('price', px)
                    self.place_order(sym, rollback_side, leg_qty, price=rollback_px, is_option=True)

        return {"success": success, "trades": results}

    def save_results(self):
        """Saves trade history and equity curve to CSV files."""
        if not self.trades_history:
            logger.warning("No trades to save.")
            return

        trades_df = pd.DataFrame(self.trades_history)
        equity_df = pd.DataFrame(self.equity_curve, columns=['timestamp', 'equity'])

        trades_path = self.config.get('paper_trades_csv', 'data/paper_trades.csv')
        equity_path = self.config.get('paper_equity_csv', 'data/paper_equity.csv')

        # Ensure directory exists
        os.makedirs(os.path.dirname(trades_path), exist_ok=True)

        trades_df.to_csv(trades_path, index=False)
        equity_df.to_csv(equity_path, index=False)

        logger.info(f"Paper trading results saved: {trades_path} and {equity_path}")
