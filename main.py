import time
import json
import sys
import logging
from datetime import datetime, date, time as dtime, timedelta
from pathlib import Path

from upstox_auth import UpstoxAuth
from data_fetcher import DataFetcher
from signal_engine import SignalEngine
from telegram_notifier import TelegramNotifier
from regime import TacticDispatcher
from regime.market_hours import (
    IST, MARKET_OPEN, MARKET_CLOSE, ENTRY_WINDOW_OPEN, FORCE_FLAT_TIME,
    next_market_open,
)
from journal import JournalRecorder, write_daily_report, analyze_trade
from journal.models import ExecutedTrade

# Create logs directory
Path("logs").mkdir(exist_ok=True)
todays_date = datetime.now(IST).strftime("%Y-%m-%d")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(f"logs/sniper_bot_{todays_date}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("LiveBot")

# Determine which index is running based on config passed
index_str = "SENSEX" if (len(sys.argv) > 1 and "sensex" in sys.argv[1].lower()) else "NIFTY"

PORTFOLIO_FILE = Path(f"data/paper_portfolio_{index_str}.json")

class LiveOrchestrator:
    def __init__(self, config_file="project_config.json"):
        print("="*60)
        print(f"[*] PURE OPTIONS BUYER [LIVE PAPER MODE] | Config: {config_file}")
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

        # --- Regime dispatcher + Journal (config-flagged) ---
        self.engine_mode = self.config.get("engine_mode", "legacy")
        self.dispatcher = TacticDispatcher(mode=self.engine_mode)
        self.journal_enabled = self.config.get("journal_enabled", True)
        self.journal = JournalRecorder() if self.journal_enabled else None
        self._journal_day_started = False
        logger.info(f"[OK] Engine mode: {self.engine_mode}, "
                    f"Journal: {'ON' if self.journal_enabled else 'OFF'}")

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
        # Wait until the market is open (handles pre-market / post-market /
        # weekend startups gracefully).
        self._wait_until_market_open()
        try:
            while True:
                now = datetime.now(IST)
                t = now.time()

                # Lazy-start the day's journal once we have a real spot tick
                self._maybe_start_journal_day(now)

                # Feed the dispatcher's indicator tracker on every loop
                if self.engine_mode == "regime":
                    spot = self.fetcher.get_spot()
                    if spot > 0:
                        self.dispatcher.on_spot_tick(now, spot)

                # Force Time Gate exit at 14:30 IST
                if t >= FORCE_FLAT_TIME:
                    if self.portfolio["open_position"]:
                        self._close_position("Time Exit (14:30 EOD)")
                    if t >= MARKET_CLOSE:
                        logger.info("Market Closed (15:30 IST). Shutting down.")
                        self._finalize_journal_day()
                        break

                if self.portfolio["open_position"]:
                    self._monitor_position(now)
                    time.sleep(3) # Turbo query loop: 1 hit per 3s = 0.33/sec (Safe via API)
                else:
                    self._scan_for_entries(now)
                    sleep_secs = 60 - datetime.now(IST).second
                    time.sleep(max(1, sleep_secs))

        except KeyboardInterrupt:
            logger.info("Bot manually stopped by trader.")
            self._finalize_journal_day()

    # --- Market-time gating -------------------------------------------

    def _wait_until_market_open(self) -> None:
        """
        If the bot started before 09:15 IST, sleep until then.
        If after 15:30 IST or on a weekend, sleep until the next trading
        day's 09:15 IST. Operator can interrupt with Ctrl+C at any time.
        """
        first_print = True
        while True:
            now_ist = datetime.now(IST)
            target = next_market_open(now_ist)

            if target is None:
                # Inside the trading session right now — proceed immediately
                if not first_print:
                    logger.info(
                        f"Market is open. Continuing event loop "
                        f"(IST {now_ist.strftime('%H:%M:%S')})."
                    )
                return

            secs_to_target = (target - now_ist).total_seconds()
            if secs_to_target <= 0:
                return  # safety: target already passed in the time we computed it

            # Sleep in chunks so the operator can Ctrl+C at any time.
            # Smaller chunks closer to open so we don't oversleep.
            if secs_to_target > 3600:
                chunk = 600       # 10-min chunks when far away
            elif secs_to_target > 600:
                chunk = 60        # 1-min chunks when within an hour
            else:
                chunk = 10        # 10-second chunks when within 10 minutes

            kind = "Weekend" if now_ist.weekday() >= 5 else (
                "Pre-market" if now_ist.time() < MARKET_OPEN else "Post-market"
            )
            logger.info(
                f"{kind}. Now IST {now_ist.strftime('%a %H:%M:%S')}. "
                f"Waiting until next open: {target.strftime('%a %Y-%m-%d %H:%M IST')} "
                f"(in {secs_to_target/60:.0f} min)."
            )
            first_print = False
            time.sleep(min(chunk, secs_to_target))

    # _next_market_open lives in regime/market_hours.py for testability.

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

        # Journal: record path tick (HOL approximated by close since live
        # quote is a single value per call — refine if intra-tick OHLC available)
        if self.journal is not None and self._journal_day_started:
            try:
                tactic_name = pos.get('tactic_name', 'oi_wall_mean_reversion')
                self.journal.on_path_tick(
                    tactic_name, now, live_premium, live_premium, live_premium,
                )
            except Exception as e:
                logger.debug(f"Journal on_path_tick failed: {e}")

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
        # `now` is already an IST tz-aware datetime supplied by run()
        if now.time() < ENTRY_WINDOW_OPEN: return

        spot = self.fetcher.get_spot()
        sup = self.fetcher.get_support()
        focus_pcr = self.fetcher.get_focus_pcr()

        if spot == 0 or sup == 0: return

        # Dispatcher abstracts: legacy mode -> SignalEngine.evaluate; regime
        # mode -> classifier+router+tactics with fallback to SignalEngine for
        # RANGE regime. Returns the same legacy-shaped dict either way.
        signal = self.dispatcher.evaluate(
            ts=now,
            fetcher=self.fetcher,
            engine=self.engine,
            in_position=False,
        )

        if signal['direction']:
            direction = signal['direction']
            # We enforce Delta > 0.40 rule here
            # Buy ITM or slightly ATM depending on the wall
            atm_strike = int(round(spot / self.strike_step) * self.strike_step)
            
            # Find the best valid strike from the Greeks map
            best_strike = atm_strike
            live_premium = self.fetcher.get_option_ltp(best_strike, direction)
            
            # Guard: Reject if premium data is missing or zero
            if live_premium <= 0:
                logger.info(f"Signal Generated ({direction}) but premium for {best_strike} is Rs.{live_premium}. Stale data. Skipping.")
                return

            greeks = self.fetcher.get_strike_greeks(best_strike, direction)

            delta = greeks.get('delta', 0)
            
            # API Fallback: If Upstox fails to provide Live Greeks for this contract, assume theoretical ATM delta.
            if delta == 0:
                delta = 0.50
                
            if delta < 0.35: # Allowing slight leniency if actual ATM drops dropping live
                logger.info(f"Signal Generated ({direction}) but ATM Delta is too low ({delta:.2f}). Rejecting.")
                return

            # Paper Fill — defer to tactic-prescribed sl/tp if dispatcher
            # supplied them, otherwise use legacy defaults.
            is_expiry = signal['is_expiry_day']
            sl_pct = signal.get('tactic_sl_pct') or (0.20 if is_expiry else 0.30)
            tgt_pct = signal.get('tactic_tp_pct') or (0.35 if is_expiry else 0.50)
            time_stop_min = signal.get('tactic_time_stop_min',
                                        45 if is_expiry else 120)

            sl_prem = live_premium * (1 - sl_pct)
            tgt_prem = live_premium * (1 + tgt_pct)

            qty = self.lot_size # Dynamic Lot Size from Config
            tactic_name = signal.get('tactic_name', 'oi_wall_mean_reversion')

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
                "tsl_active": False,
                "tactic_name": tactic_name,
                "sl_pct": sl_pct,
                "tp_pct": tgt_pct,
                "time_stop_min": time_stop_min,
            }
            self.save_portfolio()

            # Journal: record entry
            if self.journal is not None and self._journal_day_started:
                try:
                    self.journal.on_entry(
                        tactic=tactic_name,
                        direction=direction,
                        strike=best_strike,
                        entry_ts=now,
                        entry_premium=live_premium,
                        qty_lots=qty // self.lot_size,
                        sl_pct=sl_pct,
                        tp_pct=tgt_pct,
                        time_stop_min=time_stop_min,
                        regime_at_entry=signal['reasons'][0] if signal['reasons'] else "",
                        entry_state={
                            "spot": spot,
                            "support": self.fetcher.get_support(),
                            "resistance": self.fetcher.get_resistance(),
                            "focus_pcr": focus_pcr,
                            "vix": self.fetcher.get_india_vix(),
                            "delta": delta,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Journal on_entry failed: {e}")

            msg = (f"🚀 PAPER TRADE ENTERED\n"
                   f"Type: BUY {best_strike} {direction}\n"
                   f"Entry: Rs. {live_premium:.2f}\n"
                   f"Target: Rs. {tgt_prem:.2f} | SL: Rs. {sl_prem:.2f}\n"
                   f"Reason: {signal['reasons'][0]}\n"
                   f"Delta: {delta:.2f}")
            logger.info(msg)
            self.telegram.send_message(msg)
        else:
            reason_summary = signal['reasons'][0] if signal['reasons'] else "No signal"
            logger.info(f"Scanning... FocusPCR: {focus_pcr:.2f} | S:{sup} R:{res} | Spot: {spot:.0f} | {reason_summary}")


    def _close_position(self, reason, exit_price=None):
        pos = self.portfolio["open_position"]
        if exit_price is None:
            exit_price = self.fetcher.get_option_ltp(pos['strike'], pos['opt_type'])

        pnl = (exit_price - pos['entry_price']) * pos['qty']

        # Deduct Brokerage (Paper)
        pnl -= 60.0

        self.portfolio["capital"] += pnl

        exit_time = datetime.now()
        record = {
            "entry_time": pos['entry_time'],
            "exit_time": exit_time.isoformat(),
            "trade_type": pos['trade_type'],
            "strike": pos['strike'],
            "entry_price": pos['entry_price'],
            "exit_price": exit_price,
            "pnl": pnl,
            "reason": reason
        }
        self.portfolio["trade_history"].append(record)

        # Journal: record exit before clearing open_position
        if self.journal is not None and self._journal_day_started:
            try:
                tactic_name = pos.get('tactic_name', 'oi_wall_mean_reversion')
                self.journal.on_exit(
                    tactic=tactic_name,
                    exit_ts=exit_time,
                    exit_premium=exit_price,
                    exit_reason=reason,
                    net_pnl=pnl,
                )
            except Exception as e:
                logger.warning(f"Journal on_exit failed: {e}")

        self.portfolio["open_position"] = None
        self.save_portfolio()

        msg = (f"🏁 PAPER TRADE CLOSED\n"
               f"Reason: {reason}\n"
               f"Exit Price: Rs. {exit_price:.2f}\n"
               f"P&L: Rs. {pnl:.2f}\n"
               f"New Capital: Rs. {self.portfolio['capital']:.2f}")
        logger.info(msg)
        self.telegram.send_message(msg)

    # --- Journal day lifecycle -----------------------------------------

    def _maybe_start_journal_day(self, now: datetime) -> None:
        if self.journal is None or self._journal_day_started:
            return
        # Wait until we have a real spot tick before bootstrapping the day
        spot = self.fetcher.get_spot()
        if spot <= 0:
            return
        try:
            self.journal.start_day(now.date())
            # Reset dispatcher's per-day state too
            prev_close = self._lookup_prev_day_close()
            self.dispatcher.reset_for_new_day(now.date(), prev_close)
            self._journal_day_started = True
            logger.info(f"[JOURNAL] Day started for {now.date()} "
                        f"(prev_close={prev_close:.1f}, spot={spot:.1f})")
        except Exception as e:
            logger.warning(f"Journal start_day failed: {e}")

    def _finalize_journal_day(self) -> None:
        if self.journal is None or not self._journal_day_started:
            return
        try:
            realized = sum(
                t.get("pnl", 0.0) for t in self.portfolio["trade_history"]
                if t.get("exit_time", "")[:10] == datetime.now().date().isoformat()
            )
            day_record = self.journal.end_day(
                realized_pnl=realized,
                cumulative_pnl=self.portfolio.get("capital", 0.0),
            )
            # Run analyzer on each trade so the journal has rich narratives
            for t in day_record.trades:
                try:
                    analyze_trade(t)
                except Exception as e:
                    logger.debug(f"analyze_trade failed: {e}")
            out_dir = Path("reports/journal")
            path = write_daily_report(day_record, out_dir)
            logger.info(f"[JOURNAL] Wrote {path}")
            self._journal_day_started = False
        except Exception as e:
            logger.warning(f"Journal finalize failed: {e}")

    def _lookup_prev_day_close(self) -> float:
        """Best-effort previous-day close lookup from portfolio history.
        For paper bot that's been running, uses the last known close. If
        unavailable, returns 0 and the dispatcher will treat gaps as 0.
        """
        # In a more complete implementation we would persist EOD close
        # in the portfolio. For now use the latest spot as a proxy if
        # nothing better is available.
        return float(self.fetcher.get_spot() or 0.0)


if __name__ == "__main__":
    target_config = sys.argv[1] if len(sys.argv) > 1 else "project_config.json"
    bot = LiveOrchestrator(config_file=target_config)
    bot.run()
