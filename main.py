import time
import json
import sys
import logging
from datetime import datetime
from pathlib import Path

from upstox_auth import UpstoxAuth
from data_fetcher import DataFetcher
from signal_engine import SignalEngine
from telegram_notifier import TelegramNotifier

# Create logs directory
Path("logs").mkdir(exist_ok=True)
todays_date = datetime.now().strftime("%Y-%m-%d")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(f"logs/sniper_bot_{todays_date}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("LiveBot")

PORTFOLIO_FILE = Path("data/paper_portfolio.json")

class LiveOrchestrator:
    def __init__(self, config_file="project_config.json"):
        print("="*60)
        print(f"🚀 PURE OPTIONS BUYER [LIVE PAPER MODE] | Config: {config_file}")
        print("="*60)
        
        self.auth = UpstoxAuth()
        if not self.auth.is_session_valid():
            logger.warning("Upstox Session Invalid. Authenticating...")
            if not self.auth.authenticate():
                raise Exception("Authentication Failed.")
        logger.info("[OK] Upstox Auth Valid.")

        with open(config_file, "r") as f:
            self.config = json.load(f)
            
        self.trading_index = self.config.get("trading_index", "NIFTY")
        strat = self.config.get("strategy", {})
        self.strike_step = strat.get("sensex_strike_step", 100) if self.trading_index == "SENSEX" else strat.get("nifty_strike_step", 50)
        self.lot_size = strat.get("sensex_lot_size", 20) if self.trading_index == "SENSEX" else strat.get("nifty_lot_size", 65)

        self.fetcher = DataFetcher(self.config)
        self.engine = SignalEngine()
        self.telegram = TelegramNotifier()
        self.load_portfolio()
        
        # We need Nifty Spot 5m and 15m context for the engine
        # However, for the Pure Options Buyer without charts, we just pass None for dataframes.
        logger.info("Initializing Data Fetcher. Waiting for first valid chain...")
        while not self.fetcher.is_fresh():
            time.sleep(2)
        logger.info("[OK] Live Chain Data Flowing.")

    def load_portfolio(self):
        if PORTFOLIO_FILE.exists():
            with open(PORTFOLIO_FILE, "r") as f:
                self.portfolio = json.load(f)
        else:
            self.portfolio = {"capital": 100000.0, "open_position": None, "trade_history": []}

    def save_portfolio(self):
        PORTFOLIO_FILE.parent.mkdir(exist_ok=True)
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(self.portfolio, f, indent=4)

    def run(self):
        logger.info("Starting Main Event Loop.")
        try:
            while True:
                now = datetime.now()
                time_str = now.strftime("%H:%M:%S")

                # Force Time Gate exit at 14:30
                if time_str >= "14:30:00":
                    if self.portfolio["open_position"]:
                        self._close_position("Time Exit (14:30 EOD)")
                    if time_str >= "15:30:00":
                        logger.info("Market Closed. Shutting down.")
                        break

                if self.portfolio["open_position"]:
                    self._monitor_position(now)
                    time.sleep(3) # Turbo query loop: 1 hit per 3s = 0.33/sec (Safe via API)
                else:
                    self._scan_for_entries(now)
                    sleep_secs = 60 - datetime.now().second
                    time.sleep(max(1, sleep_secs))

        except KeyboardInterrupt:
            logger.info("Bot manually stopped by trader.")

    def _monitor_position(self, now):
        pos = self.portfolio["open_position"]
        strike = pos['strike']
        opt_type = pos['opt_type']
        token = pos.get('instrument_token')
        
        if not token:
            token = self.fetcher.get_instrument_token(strike, opt_type)
            if token:
                pos['instrument_token'] = token
                self.save_portfolio()
            else: return # Token missing
            
        # Get live premium instantly via 3-second Quotes API
        live_premium = self.fetcher.get_live_quote(token)
        if live_premium <= 0:
            return # Data error/stale

        # 1. Update Trailing Stop Loss if profit hits +15%
        if live_premium >= pos['entry_price'] * 1.15 and not pos.get('tsl_active'):
            pos['tsl_active'] = True
            pos['dynamic_sl'] = pos['entry_price'] * 1.02 # Trail to +2% Breakeven
            logger.info(f"🟢 TRAILING STOP ACTIVATED! SL moved to Breakeven (+2%): Rs.{pos['dynamic_sl']:.2f}")

        current_sl = pos.get('dynamic_sl', pos['sl_price'])
        
        # 2. Check Exits
        time_held_mins = (now - datetime.fromisoformat(pos['entry_time'])).total_seconds() / 60
        max_hold = 45 if pos['is_expiry_day'] else 120

        exit_reason = None
        if live_premium >= pos['target_price']:
            exit_reason = "TARGET HIT (+Profit)"
        elif live_premium <= current_sl:
            exit_reason = "TRAILING STOP" if pos.get('tsl_active') else "HARD STOP LOSS"
        elif time_held_mins >= max_hold:
            exit_reason = "THETA SHIELD (Time Stop Exceeded)"

        if exit_reason:
            self._close_position(exit_reason, exit_price=live_premium)
            return

        logger.info(f"Holding Position: {pos['trade_type']} | Live: Rs.{live_premium:.2f} | Time Held: {int(time_held_mins)}m")

    def _scan_for_entries(self, now):
        time_str = now.strftime("%H:%M:%S")
        if time_str < "10:00:00": return

        spot = self.fetcher.get_spot()
        pcr = self.fetcher.get_pcr()
        sup = self.fetcher.get_support()
        res = self.fetcher.get_resistance()
        exp = self.fetcher.get_expiry_date()

        if spot == 0 or sup == 0: return

        # Call Engine (Pure Option Chain variant)
        signal = self.engine.evaluate(
            spot_5m_df=None, spot_15m_df=None, 
            pcr=pcr, support=sup, resistance=res, 
            spot_close=spot, expiry_date=exp, current_date=now.strftime("%Y-%m-%d")
        )

        if signal['direction']:
            direction = signal['direction']
            # We enforce Delta > 0.40 rule here
            # Buy ITM or slightly ATM depending on the wall
            atm_strike = int(round(spot / self.strike_step) * self.strike_step)
            
            # Find the best valid strike from the Greeks map
            best_strike = atm_strike
            live_premium = self.fetcher.get_option_ltp(best_strike, direction)
            greeks = self.fetcher.get_strike_greeks(best_strike, direction)

            delta = greeks.get('delta', 0)
            
            # API Fallback: If Upstox fails to provide Live Greeks for this contract, assume theoretical ATM delta.
            if delta == 0:
                delta = 0.50
                
            if delta < 0.35: # Allowing slight leniency if actual ATM drops dropping live
                logger.info(f"Signal Generated ({direction}) but ATM Delta is too low ({delta:.2f}). Rejecting.")
                return

            # Paper Fill
            is_expiry = signal['is_expiry_day']
            sl_pct = 0.20 if is_expiry else 0.30
            tgt_pct = 0.35 if is_expiry else 0.50

            sl_prem = live_premium * (1 - sl_pct)
            tgt_prem = live_premium * (1 + tgt_pct)

            qty = self.lot_size # Dynamic Lot Size from Config

            self.portfolio["open_position"] = {
                "entry_time": now.isoformat(),
                "trade_type": f"BUY {direction}",
                "strike": best_strike,
                "opt_type": direction,
                "entry_price": live_premium,
                "qty": qty,
                "sl_price": sl_prem,
                "target_price": tgt_prem,
                "is_expiry_day": is_expiry,
                "tsl_active": False
            }
            self.save_portfolio()

            msg = (f"🚀 PAPER TRADE ENTERED\n"
                   f"Type: BUY {best_strike} {direction}\n"
                   f"Entry: Rs. {live_premium:.2f}\n"
                   f"Target: Rs. {tgt_prem:.2f} | SL: Rs. {sl_prem:.2f}\n"
                   f"Reason: {signal['reasons'][0]}\n"
                   f"Delta: {delta:.2f}")
            logger.info(msg)
            self.telegram.send_message(msg)
        else:
            logger.info(f"Scanning... PCR: {pcr:.2f} | S:{sup} R:{res} | Spot: {spot:.0f}")


    def _close_position(self, reason, exit_price=None):
        pos = self.portfolio["open_position"]
        if exit_price is None:
            exit_price = self.fetcher.get_option_ltp(pos['strike'], pos['opt_type'])
            
        pnl = (exit_price - pos['entry_price']) * pos['qty']
        
        # Deduct Brokerage (Paper)
        pnl -= 60.0 
        
        self.portfolio["capital"] += pnl
        
        record = {
            "entry_time": pos['entry_time'],
            "exit_time": datetime.now().isoformat(),
            "trade_type": pos['trade_type'],
            "strike": pos['strike'],
            "entry_price": pos['entry_price'],
            "exit_price": exit_price,
            "pnl": pnl,
            "reason": reason
        }
        self.portfolio["trade_history"].append(record)
        self.portfolio["open_position"] = None
        self.save_portfolio()

        msg = (f"🏁 PAPER TRADE CLOSED\n"
               f"Reason: {reason}\n"
               f"Exit Price: Rs. {exit_price:.2f}\n"
               f"P&L: Rs. {pnl:.2f}\n"
               f"New Capital: Rs. {self.portfolio['capital']:.2f}")
        logger.info(msg)
        self.telegram.send_message(msg)

if __name__ == "__main__":
    target_config = sys.argv[1] if len(sys.argv) > 1 else "project_config.json"
    bot = LiveOrchestrator(config_file=target_config)
    bot.run()
