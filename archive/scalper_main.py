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

# Determine which index is running based on config passed
index_str = "SENSEX" if (len(sys.argv) > 1 and "sensex" in sys.argv[1].lower()) else "NIFTY"

# Create logs directory
Path("logs").mkdir(exist_ok=True)
todays_date = datetime.now().strftime("%Y-%m-%d")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(f"logs/scalper_{index_str}_{todays_date}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(f"Scalper_{index_str}")

PORTFOLIO_FILE = Path(f"data/scalper_portfolio_{index_str}.json")

class LiveScalperOrchestrator:
    def __init__(self, config_file="project_config.json"):
        print("="*60)
        print(f"[*] PURE OPTIONS SCALPER [LIVE PAPER MODE] | Config: {config_file}")
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
        
        logger.info("Initializing Data Fetcher. Waiting for first valid chain...")
        while not self.fetcher.is_fresh():
            time.sleep(2)
        logger.info("[OK] Live Chain Data Flowing.")

    def load_portfolio(self):
        if PORTFOLIO_FILE.exists():
            with open(PORTFOLIO_FILE, "r") as f:
                self.portfolio = json.load(f)
        else:
            self.portfolio = {"capital": 300000.0, "open_position": None, "trade_history": []}

    def save_portfolio(self):
        PORTFOLIO_FILE.parent.mkdir(exist_ok=True)
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(self.portfolio, f, indent=4)

    def run(self):
        logger.info("Starting Scalper Event Loop.")
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
                    time.sleep(3) # Turbo query loop: 1 hit per 3s
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
            else: return 
            
        live_premium = self.fetcher.get_live_quote(token)
        if live_premium <= 0:
            return 

        # 1. Update Trailing Stop Loss if profit hits +4%
        if live_premium >= pos['entry_price'] * 1.04 and not pos.get('tsl_active'):
            pos['tsl_active'] = True
            pos['dynamic_sl'] = pos['entry_price'] * 1.01 # Trail to +1% Breakeven + Fees
            logger.info(f"🟢 SCALPER TSL! SL moved to +1%: Rs.{pos['dynamic_sl']:.2f}")

        current_sl = pos.get('dynamic_sl', pos['sl_price'])
        
        # 2. Check Exits (Scalper time limit = 15m)
        time_held_mins = (now - datetime.fromisoformat(pos['entry_time'])).total_seconds() / 60
        max_hold = 15 

        exit_reason = None
        if live_premium >= pos['target_price']:
            exit_reason = "⚡ SCALP TARGET HIT"
        elif live_premium <= current_sl:
            exit_reason = "TRAILING STOP" if pos.get('tsl_active') else "HARD STOP LOSS"
        elif time_held_mins >= max_hold:
            exit_reason = "THETA SHIELD (15m Scalp Limit Exceeded)"

        if exit_reason:
            self._close_position(exit_reason, exit_price=live_premium)
            return

        logger.info(f"Scalp Watch: {pos['trade_type']} | Live: Rs.{live_premium:.2f} | Target: Rs.{pos['target_price']:.2f} | Mins: {int(time_held_mins)}")

    def _scan_for_entries(self, now):
        time_str = now.strftime("%H:%M:%S")
        if time_str < "10:00:00": return

        spot = self.fetcher.get_spot()
        sup = self.fetcher.get_support()
        res = self.fetcher.get_resistance()
        exp = self.fetcher.get_expiry_date()
        focus_pcr = self.fetcher.get_focus_pcr()
        oi_pattern = self.fetcher.get_oi_pattern()
        spot_history = self.fetcher.get_spot_history()
        india_vix = self.fetcher.get_india_vix()

        if spot == 0 or sup == 0: return

        # Call SignalEngine with scalp_mode=True
        signal = self.engine.evaluate(
            spot_close=spot, support=sup, resistance=res,
            focus_pcr=focus_pcr, oi_pattern=oi_pattern,
            spot_history=spot_history, india_vix=india_vix,
            expiry_date=exp, current_date=now.strftime("%Y-%m-%d"),
            scalp_mode=True
        )

        if signal['direction']:
            direction = signal['direction']
            atm_strike = int(round(spot / self.strike_step) * self.strike_step)
            best_strike = atm_strike
            live_premium = self.fetcher.get_option_ltp(best_strike, direction)
            
            if live_premium <= 0:
                return

            greeks = self.fetcher.get_strike_greeks(best_strike, direction)
            delta = greeks.get('delta', 0)
            if delta == 0: delta = 0.50
                
            if delta < 0.35: return

            # ⚡ Aggressive Scalping Config
            sl_pct = 0.05  # -5% SL
            tgt_pct = 0.08 # +8% Target

            sl_prem = live_premium * (1 - sl_pct)
            tgt_prem = live_premium * (1 + tgt_pct)

            qty = self.lot_size 

            self.portfolio["open_position"] = {
                "entry_time": now.isoformat(),
                "trade_type": f"BUY {direction}",
                "strike": best_strike,
                "opt_type": direction,
                "entry_price": live_premium,
                "qty": qty,
                "sl_price": sl_prem,
                "target_price": tgt_prem,
                "is_expiry_day": signal['is_expiry_day'],
                "tsl_active": False
            }
            self.save_portfolio()

            msg = (f"⚡ SCALPER TRADE ENTERED\n"
                   f"Type: BUY {best_strike} {direction}\n"
                   f"Entry: Rs. {live_premium:.2f}\n"
                   f"Target: Rs. {tgt_prem:.2f} | SL: Rs. {sl_prem:.2f}\n"
                   f"Reason: {signal['reasons'][0]}\n"
                   f"Delta: {delta:.2f}")
            logger.info(msg)
            self.telegram.send_message(msg)
        else:
            reason_summary = signal['reasons'][0] if signal['reasons'] else "No signal"
            logger.info(f"Scalp Scanning... PCR: {focus_pcr:.2f} | Spot: {spot:.0f} | {reason_summary}")

    def _close_position(self, reason, exit_price=None):
        pos = self.portfolio["open_position"]
        if exit_price is None:
            exit_price = self.fetcher.get_option_ltp(pos['strike'], pos['opt_type'])
            
        pnl = (exit_price - pos['entry_price']) * pos['qty']
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

        msg = (f"⚡ SCALP CLOSED\n"
               f"Reason: {reason}\n"
               f"Exit Price: Rs. {exit_price:.2f}\n"
               f"P&L: Rs. {pnl:.2f}\n"
               f"New Capital: Rs. {self.portfolio['capital']:.2f}")
        logger.info(msg)
        self.telegram.send_message(msg)

if __name__ == "__main__":
    target_config = sys.argv[1] if len(sys.argv) > 1 else "project_config.json"
    bot = LiveScalperOrchestrator(config_file=target_config)
    bot.run()
