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
    IST, MARKET_OPEN, MARKET_CLOSE, ENTRY_WINDOW_OPEN,
    next_market_open,
)
from journal import JournalRecorder, write_daily_report, analyze_trade
from journal.models import ExecutedTrade
from journal.missed_tracker import LiveMissedTracker
from journal.state_publisher import StatePublisher

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

# PORTFOLIO_FILE is set per-instance in LiveOrchestrator.__init__ now,
# so legacy and regime engines can run side-by-side without overwriting
# each other's portfolios. Legacy keeps the old filename (no break for
# the dashboard backend); regime gets a _regime suffix.

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

        # --- Portfolio path: legacy keeps old name, regime gets suffix ---
        # so two engines can run side-by-side on the same index.
        self.engine_mode = self.config.get("engine_mode", "legacy")
        if self.engine_mode == "regime":
            self.portfolio_file = Path(f"data/paper_portfolio_{index_str}_regime.json")
        else:
            self.portfolio_file = Path(f"data/paper_portfolio_{index_str}.json")
        self.load_portfolio()

        # --- Regime dispatcher + Journal (config-flagged) ---
        self.dispatcher = TacticDispatcher(
            mode=self.engine_mode, strike_step=self.strike_step,
        )
        self.journal_enabled = self.config.get("journal_enabled", True)
        self.journal = JournalRecorder() if self.journal_enabled else None
        self._journal_day_started = False
        # Live missed-opportunity tracker. Records near-misses surfaced by
        # the dispatcher and watches the would-be option's premium for the
        # tactic-specified time-stop window. Pure-observation; never affects
        # routing or order placement.
        self.missed_tracker = (
            LiveMissedTracker(
                self.journal, self.fetcher,
                lot_size=self.lot_size,
            ) if self.journal is not None else None
        )
        self._last_nm_probe_ts: datetime | None = None
        # Publish bot state to disk so the dashboard backend (a separate
        # process) can render live indicators. File-based IPC keeps the
        # bot decoupled from the web layer.
        self.state_publisher = StatePublisher(
            Path(__file__).parent / "data", index_str,
        )
        self._last_signal: dict | None = None
        # Daily entry cap — block new entries after N completed trades today.
        with open(self.config.get("options_spec_path", "Options.json")) as fh:
            opts = json.load(fh).get("configurableParameters", {})
        self.max_positions_per_day = int(opts.get("maxPositionsPerDay", 6))
        self._positions_today_date = None  # date string the counter belongs to
        self._positions_today_count = 0
        logger.info(f"[OK] Engine mode: {self.engine_mode}, "
                    f"Journal: {'ON' if self.journal_enabled else 'OFF'}, "
                    f"MaxPositionsPerDay: {self.max_positions_per_day}")

        # We need Nifty Spot 5m and 15m context for the engine
        # However, for the Pure Options Buyer without charts, we just pass None for dataframes.
        logger.info("Initializing Data Fetcher. Waiting for first valid chain...")
        while not self.fetcher.is_fresh():
            time.sleep(2)
        logger.info("[OK] Live Chain Data Flowing.")

    def load_portfolio(self):
        if self.portfolio_file.exists():
            with open(self.portfolio_file, "r") as f:
                self.portfolio = json.load(f)
        else:
            self.portfolio = {"capital": 100000.0, "open_position": None, "trade_history": []}

    def save_portfolio(self):
        self.portfolio_file.parent.mkdir(exist_ok=True)
        with open(self.portfolio_file, "w") as f:
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

                # Tick the live missed-opportunity tracker — polls in-flight
                # follow-ups and finalises any that hit TP/SL/time. Wrapped
                # in try/except inside; never raises here.
                if self.missed_tracker is not None and self._journal_day_started:
                    self.missed_tracker.tick(now)

                # Publish current state for the dashboard backend.
                self._publish_state(now)

                # At market close (15:30 IST): flatten any open position
                # and shut down. New entries are allowed all the way up to
                # the close — no earlier cutoff.
                if t >= MARKET_CLOSE:
                    if self.portfolio["open_position"]:
                        self._close_position("Market Close (15:30 IST)")
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

        # Even while holding a position, surface any near-misses on the
        # OPPOSITE-direction side. Probe at most once a minute.
        self._probe_near_misses_in_position(now, pos)

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

        # Daily entry cap. Counter resets at the first scan of each new day.
        today_str = now.date().isoformat()
        if self._positions_today_date != today_str:
            self._positions_today_date = today_str
            self._positions_today_count = 0
        if self._positions_today_count >= self.max_positions_per_day:
            return  # cap reached — silent skip

        spot = self.fetcher.get_spot()
        sup = self.fetcher.get_support()
        res = self.fetcher.get_resistance()
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

        # Register any near-misses surfaced by the dispatcher with the
        # live missed-tracker. Wrapped to never affect trading flow.
        for nm in signal.get('near_misses', []):
            self._register_near_miss_safely(nm)

        # Capture the latest signal for the dashboard panel.
        self._last_signal = {
            "ts": now.isoformat(),
            "direction": signal.get('direction'),
            "tactic_name": signal.get('tactic_name'),
            "reasons": signal.get('reasons', []),
            "near_miss_count": len(signal.get('near_misses', [])),
        }

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
            sl_pct = signal.get('tactic_sl_pct') or (0.20 if is_expiry else 0.20)
            tgt_pct = signal.get('tactic_tp_pct') or (0.35 if is_expiry else 0.50)
            time_stop_min = signal.get('tactic_time_stop_min',
                                        45 if is_expiry else 120)

            sl_prem = live_premium * (1 - sl_pct)
            tgt_prem = live_premium * (1 + tgt_pct)

            qty = self.lot_size # Dynamic Lot Size from Config
            tactic_name = signal.get('tactic_name', 'oi_wall_mean_reversion')

            # Increment daily entry counter — checked at top of next scan.
            self._positions_today_count += 1

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

        exit_time = datetime.now(IST)
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

    # --- Dashboard state publishing -----------------------------------

    def _publish_state(self, now: datetime) -> None:
        """Write current bot state to disk so the dashboard backend can
        render it. Wrapped in try/except — never raises into trading flow."""
        try:
            snap = {}
            if self.engine_mode == "regime":
                try:
                    snap = self.dispatcher.indicators.snapshot()
                except Exception:
                    snap = {}

            spot = self.fetcher.get_spot()
            try:
                vix = self.fetcher.get_india_vix()
            except Exception:
                vix = 0.0
            try:
                focus_pcr = self.fetcher.get_focus_pcr()
            except Exception:
                focus_pcr = 0.0
            try:
                sup = self.fetcher.get_support()
                res = self.fetcher.get_resistance()
            except Exception:
                sup, res = 0, 0
            try:
                oi = self.fetcher.get_oi_pattern()
                ce_oi = oi.get("ce_oi_change", 0)
                pe_oi = oi.get("pe_oi_change", 0)
            except Exception:
                ce_oi, pe_oi = 0, 0

            regime = "UNKNOWN"
            if self.engine_mode == "regime":
                try:
                    cur = self.dispatcher.classifier._current
                    if cur is not None:
                        regime = cur.value
                except Exception:
                    pass

            pos = self.portfolio.get("open_position")
            in_position = pos is not None

            n_missed = 0
            if self.journal is not None and self._journal_day_started:
                try:
                    n_missed = len(self.journal._day.missed) if self.journal._day else 0
                except Exception:
                    n_missed = 0

            t = now.time()
            is_market_open = MARKET_OPEN <= t < MARKET_CLOSE

            engine_state = {
                "index": self.trading_index,
                "engine_mode": self.engine_mode,
                "regime": regime,
                "spot": float(spot or 0),
                "vwap": float(snap.get("vwap", 0) or 0),
                "ema9_5m": float(snap.get("ema9_5m", 0) or 0),
                "ema21_5m": float(snap.get("ema21_5m", 0) or 0),
                "atr_5m": float(snap.get("atr_5m", 0) or 0),
                "day_open": float(snap.get("day_open", 0) or 0),
                "day_high": float(snap.get("day_high", 0) or 0),
                "day_low": float(snap.get("day_low", 0) or 0),
                "or_high": float(snap.get("or_high", 0) or 0),
                "or_low": float(snap.get("or_low", 0) or 0),
                "focus_pcr": float(focus_pcr or 0),
                "support_strike": int(sup or 0),
                "resistance_strike": int(res or 0),
                "vix_level": float(vix or 0),
                "ce_oi_change": float(ce_oi or 0),
                "pe_oi_change": float(pe_oi or 0),
                "in_position": in_position,
                "open_position": pos,
                "last_signal": self._last_signal,
                "missed_today_count": n_missed,
                "is_market_open": is_market_open,
                "journal_day_started": self._journal_day_started,
            }
            self.state_publisher.write_engine_state(engine_state)

            if self.journal is not None and self._journal_day_started \
                    and self.journal._day is not None:
                self.state_publisher.write_missed_snapshot(
                    list(self.journal._day.missed),
                )
        except Exception as e:
            logger.debug(f"_publish_state failed: {e}")

    # --- Near-miss helpers ---------------------------------------------

    def _register_near_miss_safely(self, nm: dict) -> None:
        if self.missed_tracker is None or not self._journal_day_started:
            return
        try:
            self.missed_tracker.register_near_miss(**nm)
        except Exception as e:
            logger.debug(f"register_near_miss failed: {e}")

    def _probe_near_misses_in_position(self, now: datetime, pos: dict) -> None:
        """While in a position, run a passive near-miss probe once per
        minute. Records OPPOSITE-direction near-misses too so we can see
        what we missed during the hold."""
        if self.missed_tracker is None or not self._journal_day_started:
            return
        if self.engine_mode != "regime":
            return
        if self._last_nm_probe_ts is not None and \
           (now - self._last_nm_probe_ts).total_seconds() < 60:
            return
        self._last_nm_probe_ts = now
        try:
            nms = self.dispatcher.collect_near_misses_only(
                now, self.fetcher,
                in_position=True,
                position_direction=pos.get('opt_type'),
                position_entry_premium=pos.get('entry_price', 0.0),
            )
            for nm in nms:
                self._register_near_miss_safely(nm)
        except Exception as e:
            logger.debug(f"in-position near-miss probe failed: {e}")

    def _finalize_journal_day(self) -> None:
        if self.journal is None or not self._journal_day_started:
            return
        try:
            # Flush the missed-tracker before end_day so finalised
            # hypothetical fields land in today's JSON / Markdown.
            if self.missed_tracker is not None:
                try:
                    n = self.missed_tracker.flush_all(datetime.now(IST))
                    if n:
                        logger.info(f"[JOURNAL] Flushed {n} pending near-miss follow-ups")
                except Exception as e:
                    logger.warning(f"missed-tracker flush_all failed: {e}")

            realized = sum(
                t.get("pnl", 0.0) for t in self.portfolio["trade_history"]
                if t.get("exit_time", "")[:10] == datetime.now(IST).date().isoformat()
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
