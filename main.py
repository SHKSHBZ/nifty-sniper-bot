import time
import json
import sys
import logging
from datetime import datetime, date, time as dtime, timedelta
from pathlib import Path

from upstox_auth import UpstoxAuth
from data_fetcher import DataFetcher
from signal_engine import SignalEngine
from oi_flow_engine import OIFlowEngine
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
from vigilance import classify_regime

# Create logs directory
Path("logs").mkdir(exist_ok=True)
todays_date = datetime.now(IST).strftime("%Y-%m-%d")

# Determine which bot variant is running based on config arg passed.
# Each variant gets its own log file so parallel bots don't interleave.
index_str = "SENSEX" if (len(sys.argv) > 1 and "sensex" in sys.argv[1].lower()) else "NIFTY"
_is_regime_arg = (len(sys.argv) > 1 and "_regime" in sys.argv[1].lower())
_is_t1_arg     = (len(sys.argv) > 1 and "_t1" in sys.argv[1].lower())
_is_t2_arg     = (len(sys.argv) > 1 and "_t2" in sys.argv[1].lower())
_is_seller_arg = (len(sys.argv) > 1 and "seller" in sys.argv[1].lower())
if _is_seller_arg:   _log_suffix = "_seller"
elif _is_t1_arg:     _log_suffix = "_t1"
elif _is_t2_arg:     _log_suffix = "_t2"
elif _is_regime_arg: _log_suffix = "_regime"
else:                _log_suffix = ""
log_file_path = f"logs/sniper_bot_{index_str}{_log_suffix}_{todays_date}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(log_file_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("LiveBot")

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
        self.engine_mode = self.config.get("engine_mode", "legacy")
        
        # ── Engine selection ──
        if self.engine_mode == "oi_flow":
            self.engine = OIFlowEngine(self.config)
            logger.info("[OK] Engine: OI-Flow v1.1")
        else:
            self.engine = SignalEngine()
            logger.info(f"[OK] Engine mode: {self.engine_mode}")
        
        self.telegram = TelegramNotifier()

        # --- Portfolio path: legacy keeps old name, variants get a suffix ---
        # so multiple engines can run side-by-side on the same index without
        # overwriting each other's portfolios.
        # Seller mode = run iron-condor entry/monitor/close path instead of
        # the buyer scan. Mutually exclusive with single-leg / straddle paths.
        self.seller_mode = bool(self.config.get("seller_mode", False))
        # T1/T2/seller variants take precedence over regime suffix (each is
        # its own bot, though they internally run in regime mode).
        if self.seller_mode:
            self.portfolio_file = Path(f"data/paper_portfolio_{index_str}_seller.json")
        elif "_t1" in config_file.lower():
            self.portfolio_file = Path(f"data/paper_portfolio_{index_str}_t1.json")
        elif "_t2" in config_file.lower():
            self.portfolio_file = Path(f"data/paper_portfolio_{index_str}_t2.json")
        elif self.engine_mode == "regime":
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
                max_pending=50,
            ) if self.journal is not None else None
        )
        self._last_nm_probe_ts: datetime | None = None
        # Publish bot state to disk so the dashboard backend (a separate
        # process) can render live indicators. File-based IPC keeps the
        # bot decoupled from the web layer.
        # Suffix state files with the variant name when running parallel
        # bots, so legacy / regime / t1 / seller don't overwrite each
        # other's state.
        if self.seller_mode:
            state_index = index_str + "_seller"
        elif "_t1" in config_file.lower():
            state_index = index_str + "_t1"
        elif "_t2" in config_file.lower():
            state_index = index_str + "_t2"
        elif self.engine_mode == "regime":
            state_index = index_str + "_regime"
        else:
            state_index = index_str
        self.state_publisher = StatePublisher(
            Path(__file__).parent / "data", state_index,
        )
        self._last_signal: dict | None = None
        # Daily entry cap — block new entries after N completed trades today.
        with open(self.config.get("options_spec_path", "Options.json")) as fh:
            opts = json.load(fh).get("configurableParameters", {})
        self.opts = opts
        self.max_positions_per_day = int(opts.get("maxPositionsPerDay", 6))
        self._positions_today_date = None  # date string the counter belongs to
        self._positions_today_count = 0
        
        # Bootstrap 3TF trend EMAs on startup if enabled.
        # Deferred to run() so the API token and market data are live
        # (bootstrapping pre-market often fails with stale tokens).
        self._3tf_needs_bootstrap = self.config.get(
            "enable_3tf_filters", opts.get("enable_3tf_filters", False)
        )
        # PR 2: drawdown breaker + consecutive-loss regime self-diagnostic.
        # daily_drawdown_pct = -1.5% of session-start cap halts entries for the day.
        self.daily_drawdown_pct = float(opts.get("dailyDrawdownPct", 1.5)) / 100.0
        self.consecutive_loss_threshold = int(opts.get("consecutiveLossThreshold", 2))
        # PR 3 active-management thresholds (read from Options.json so we
        # can tune without code changes).
        self.pcr_shift_exit_threshold = float(opts.get("pcrShiftExitThreshold", 0.10))
        self.adverse_oi_growth_exit_pct = float(opts.get("adverseOiGrowthExitPct", 8.0)) / 100.0
        self.time_stop_minutes = int(opts.get("timeStopMinutes", 30))
        self.time_stop_min_profit_pct = float(opts.get("timeStopMinProfitPct", 0.5)) / 100.0
        self.theta_shield_normal_mins = int(opts.get("thetaShieldNormalMins", 60))
        self.theta_shield_expiry_mins = int(opts.get("thetaShieldExpiryMins", 45))
        self._session_start_capital: float | None = None  # set on first scan of day
        self._consecutive_losses = 0
        self._consecutive_zone_inv = 0   # zone-inv counter: 2 in a row → lock day
        self._sl_hit_count = 0           # SL hit counter for cooldown
        self._cooldown_until: datetime | None = None  # no entries until this time
        self._cooldown_reason: str = ""  # why we're in cooldown
        # _regime_lock: None | 'STOPPED' | 'CE_ONLY' | 'PE_ONLY' | 'NO_TRADE' — set after
        # consecutive-loss diagnostic or zone-inv guard; reset at the start of each new day.
        self._regime_lock: str | None = None
        self._regime_lock_reasons: list[str] = []
        logger.info(f"[OK] Engine mode: {self.engine_mode}, "
                    f"Journal: {'ON' if self.journal_enabled else 'OFF'}, "
                    f"MaxPositionsPerDay: {self.max_positions_per_day}, "
                    f"DailyDrawdown: {self.daily_drawdown_pct*100:.1f}%, "
                    f"ConsecLossThr: {self.consecutive_loss_threshold}")

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
            self.portfolio = {"capital": 600000.0, "open_positions": [], "trade_history": []}
        # Migrate legacy single open_position to list
        if "open_position" in self.portfolio and self.portfolio["open_position"]:
            self.portfolio.setdefault("open_positions", [])
            self.portfolio["open_positions"].append(self.portfolio.pop("open_position"))
        self.portfolio.setdefault("open_positions", [])
        # T2: ensure the straddle slot exists (None if not currently active)
        self.portfolio.setdefault("open_straddle", None)
        # Seller bot: ensure the iron-condor slot exists
        self.portfolio.setdefault("open_iron_condor", None)

    def save_portfolio(self):
        self.portfolio_file.parent.mkdir(exist_ok=True)
        with open(self.portfolio_file, "w") as f:
            json.dump(self.portfolio, f, indent=4)

    def run(self):
        logger.info("Starting Main Event Loop.")
        # Wait until the market is open (handles pre-market / post-market /
        # weekend startups gracefully).
        self._wait_until_market_open()

        # Bootstrap 3TF trend EMAs now that market is open and API is live
        if getattr(self, '_3tf_needs_bootstrap', False):
            try:
                self.engine.tracker_3tf.bootstrap(self.fetcher)
                logger.info("3TF bootstrap completed after market open.")
            except Exception as e:
                logger.error(f"3TF bootstrap failed (will seed on-the-fly): {e}")

        # Bootstrap Gann Square of 9 levels for today
        if self.engine_mode == "oi_flow":
            self._bootstrap_gann_levels()
            self._recover_engine_state_if_needed()

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

                # At market close (15:30 IST): flatten all open positions
                # and shut down.
                if t >= MARKET_CLOSE:
                    for pos in list(self.portfolio.get("open_positions", [])):
                        self._close_position("Market Close (15:30 IST)", pos=pos)
                    if self.portfolio.get("open_straddle"):
                        self._close_straddle("Market Close (15:30 IST)")
                    logger.info("Market Closed (15:30 IST). Shutting down.")
                    self._finalize_journal_day()
                    break

                # ── OI-Flow mode (v1.1) ───────────────────────────
                # Minimal engine: spot trend + OI velocity only.
                if self.engine_mode == "oi_flow":
                    if self.portfolio.get("open_straddle"):
                        self._monitor_straddle(now)
                    self._run_oi_flow_tick(now)
                    
                    is_expiry = (self.fetcher.get_expiry_date() == now.date().isoformat())
                    if self.engine.position is not None or self.portfolio.get("open_straddle"):
                        time.sleep(3)
                    elif is_expiry:
                        time.sleep(5)   # 5-second hyper-fast engine processing on Expiry Day
                    else:
                        time.sleep(15)  # 15-second processing on Normal Days
                    continue

                # ── Seller mode (iron condor only) ────────────────────
                # Runs a completely separate flow from the buyer paths.
                # Skips single-leg / straddle scans entirely.
                if self.seller_mode:
                    if self.portfolio.get("open_iron_condor"):
                        self._monitor_iron_condor(now)
                        time.sleep(3)
                    else:
                        self._scan_for_iron_condor_entry(now)
                        sleep_secs = 60 - datetime.now(IST).second
                        time.sleep(max(1, sleep_secs))
                    continue

                # ── Buyer mode: always monitor open positions, scan for new entries ──
                open_positions = self.portfolio.get("open_positions", [])
                if self.portfolio.get("open_straddle"):
                    self._monitor_straddle(now)
                elif open_positions:
                    self._monitor_all_positions(now)
                
                # Scan for new entries (only if we have room)
                max_concurrent = int(self.opts.get("maxConcurrentPositions", 5))
                if len(open_positions) < max_concurrent:
                    self._scan_for_entries(now)
                
                # Sleep: 3s if holding positions, 60s if idle
                if open_positions or self.portfolio.get("open_straddle"):
                    time.sleep(3)
                else:
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

    def _bootstrap_gann_levels(self):
        """
        Loads yesterday's close price from cache (syncing with API first)
        and initializes the Gann Square of 9 engine.
        """
        try:
            import historical_levels
            from gann_engine import GannSquareOf9
            
            idx = self.trading_index
            logger.info(f"[GANN] Syncing daily cache to get yesterday's close for {idx}...")
            
            # Update cache to make sure we have yesterday's candle
            cached = historical_levels.load_cache(idx)
            from_date, to_date = historical_levels.get_missing_dates(cached, lookback_days=60)
            if from_date and to_date:
                new_rows = self.fetcher.get_historical_daily_ohlc(from_date, to_date)
                if new_rows:
                    cached = historical_levels.merge_and_trim(cached, new_rows, lookback_days=60)
                    historical_levels.save_cache(idx, cached)
                    logger.info(f"[GANN] Daily cache updated with {len(new_rows)} new rows.")
            
            yesterday_close = cached[-1]["close"] if cached else None
            if not yesterday_close or yesterday_close <= 0:
                # Fallback to current spot if cache is empty
                yesterday_close = self.fetcher.get_spot()
                
            if yesterday_close > 0:
                gann = GannSquareOf9(yesterday_close)
                self.engine.gann = gann
                logger.info(f"[GANN] ✅ Gann Square of 9 initialized with Base={yesterday_close:.2f}")
                logger.info(f"[GANN] Buy +45: {gann.levels['buy'][45]:.2f} | Buy +90: {gann.levels['buy'][90]:.2f}")
                logger.info(f"[GANN] Sell -45: {gann.levels['sell'][45]:.2f} | Sell -90: {gann.levels['sell'][90]:.2f}")
            else:
                logger.error("[GANN] Could not retrieve base price for Gann levels")
                self.engine.gann = None
        except Exception as e:
            logger.error(f"[GANN] Failed to initialize Gann Square of 9: {e}")
            self.engine.gann = None

    def _recover_engine_state_if_needed(self):
        """Restores the engine's active position and locked strikes if restarting mid-day."""
        try:
            if self.engine_mode != "oi_flow":
                return
            open_pos = next((p for p in self.portfolio.get("open_positions", []) if p.get("engine") == "oi_flow_v1.1"), None)
            if open_pos:
                logger.info(f"[OI-Flow] Recovery: Restoring open position to engine: {open_pos}")
                entry_time_dt = datetime.fromisoformat(open_pos["entry_time"]).astimezone(IST)
                
                direction = open_pos["direction"]
                strikes = open_pos["strikes"]
                
                self.engine.position = {
                    "direction": direction,
                    "strikes": strikes,
                    "entry_time": entry_time_dt,
                    "entry_premiums": {int(k): float(v) for k, v in open_pos["entry_premiums"].items()},
                    "entry_avg_premium": sum(open_pos["entry_premiums"].values()) / max(len(open_pos["entry_premiums"]), 1),
                    "max_avg_premium": open_pos.get("max_avg_premium", sum(open_pos["entry_premiums"].values()) / max(len(open_pos["entry_premiums"]), 1)),
                    "trade_num": open_pos.get("trade_num", 1),
                    "signal_strength": open_pos.get("signal_strength", "single"),
                    "target": open_pos.get("target"),
                    "sl_spot": open_pos.get("sl_spot"),
                    "tier1_done": open_pos.get("tier1_done", False),
                    "lots_per_strike": {int(k): int(v) for k, v in open_pos.get("lots_per_strike", {}).items()}
                }
                self.engine._strikes_locked = True
                if direction == "CE":
                    self.engine.ce_fixed_strikes = strikes
                    self.engine.pe_fixed_strikes = [s + 200 for s in strikes]
                else:
                    self.engine.pe_fixed_strikes = strikes
                    self.engine.ce_fixed_strikes = [s - 200 for s in strikes]
                self.engine.regime = "trending"
                self.engine._regime_decided = True
        except Exception as e:
            logger.error(f"[OI-Flow] State recovery failed: {e}")

    def _monitor_all_positions(self, now):
        """Monitor all open positions. Closes any that hit SL/TP/time-stop."""
        for pos in list(self.portfolio.get("open_positions", [])):
            self._monitor_single_position(pos, now)

    def _monitor_single_position(self, pos, now):
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

        entry_price = pos['entry_price']
        remaining = pos.get('remaining_lots', pos.get('total_lots', 5))
        total_lots = pos.get('total_lots', 5)
        lots_sold = pos.get('lots_sold', 0)

        # ── Tiered Profit Booking (2026-05-26) ─────────────────────────
        use_pct_scale = self.config.get("enable_3tf_filters", self.opts.get("enable_3tf_filters", False))
        
        if use_pct_scale:
            # ── 5-Lot Partial Exit (Scale-Out) Strategy ──────────────────
            gain_pct = (live_premium - entry_price) / entry_price
            SCALE_TP_PCT = float(self.opts.get("scale_out_tp_percent", 15)) / 100.0
            RUNNER_TP_PCT = float(self.opts.get("scale_out_runner_percent", 30)) / 100.0
            
            # Tier 1: +15% → sell 3 lots, move SL to breakeven
            if not pos.get('tier1_done') and gain_pct >= SCALE_TP_PCT:
                lots_to_sell = min(3, remaining)
                if lots_to_sell > 0:
                    self._partial_close(pos, live_premium, lots_to_sell,
                                        f"SCALE_TP (+{int(SCALE_TP_PCT*100)}%, {lots_to_sell} lots)")
                    pos['tier1_done'] = True
                    pos['remaining_lots'] = pos.get('remaining_lots', total_lots) - lots_to_sell
                    pos['lots_sold'] = pos.get('lots_sold', 0) + lots_to_sell
                    pos['sl_price'] = entry_price  # Move SL to Breakeven
                    pos['tsl_active'] = True
                    logger.info(f"🟢 SCALE-OUT TARGET 1 HIT! 3 lots sold. SL moved to Breakeven: Rs.{entry_price:.2f}")
                    self.save_portfolio()
                    remaining = pos['remaining_lots']
            
            # Tier 2: +30% → sell remaining 2 lots
            if pos.get('tier1_done') and not pos.get('tier2_done') and gain_pct >= RUNNER_TP_PCT:
                lots_to_sell = remaining
                if lots_to_sell > 0:
                    self._partial_close(pos, live_premium, lots_to_sell,
                                        f"RUNNER_TP (+{int(RUNNER_TP_PCT*100)}%, {lots_to_sell} lots)")
                    pos['tier2_done'] = True
                    pos['remaining_lots'] = 0
                    pos['lots_sold'] = pos.get('lots_sold', 0) + lots_to_sell
                    self.save_portfolio()
                    remaining = pos['remaining_lots']
        else:
            pts_from_entry = live_premium - entry_price
            TIER1_PTS = 20
            TIER2_PTS = 35
            TIER1_LOTS = 3
            TIER2_LOTS = 1
            TRAIL_LOTS = 1

            # Tier 1: +20 pts → sell 3 lots
            if not pos.get('tier1_done') and pts_from_entry >= TIER1_PTS:
                lots_to_sell = min(TIER1_LOTS, remaining)
                if lots_to_sell > 0:
                    pnl_tier1 = pts_from_entry * lots_to_sell * self.lot_size
                    self._partial_close(pos, live_premium, lots_to_sell,
                                        f"TIER1 (+{TIER1_PTS}pts, {lots_to_sell} lots)")
                    pos['tier1_done'] = True
                    pos['remaining_lots'] = pos.get('remaining_lots', total_lots) - lots_to_sell
                    pos['lots_sold'] = pos.get('lots_sold', 0) + lots_to_sell
                    self.save_portfolio()
                    remaining = pos['remaining_lots']

            # Tier 2: +35 pts → sell 1 lot
            if pos.get('tier1_done') and not pos.get('tier2_done') and pts_from_entry >= TIER2_PTS:
                lots_to_sell = min(TIER2_LOTS, remaining)
                if lots_to_sell > 0:
                    self._partial_close(pos, live_premium, lots_to_sell,
                                        f"TIER2 (+{TIER2_PTS}pts, {lots_to_sell} lot)")
                    pos['tier2_done'] = True
                    pos['remaining_lots'] = pos.get('remaining_lots', 1) - lots_to_sell
                    pos['lots_sold'] = pos.get('lots_sold', 0) + lots_to_sell
                    self.save_portfolio()
                    remaining = pos['remaining_lots']

            # Trail lot: activate breakeven SL once Tier 2 is done
            if pos.get('tier2_done') and not pos.get('trail_active'):
                pos['trail_active'] = True
                pos['trail_cost'] = entry_price  # breakeven
                pos['sl_price'] = entry_price    # trail stop at cost
                logger.info(f"🟢 TRAIL ACTIVE: last {TRAIL_LOTS} lot at breakeven (Rs.{entry_price:.2f})")
                self.save_portfolio()

        # If no remaining lots, close position
        if pos.get('remaining_lots', 0) <= 0:
            if pos in self.portfolio.get("open_positions", []):
                self.portfolio["open_positions"].remove(pos)
            self.save_portfolio()
            return

        # Journal: record path tick
        if self.journal is not None and self._journal_day_started:
            try:
                tactic_name = pos.get('tactic_name', 'oi_wall_mean_reversion')
                self.journal.on_path_tick(
                    tactic_name, now, live_premium, live_premium, live_premium,
                )
            except Exception as e:
                logger.debug(f"Journal on_path_tick failed: {e}")

        # Probe near-misses on opposite direction
        self._probe_near_misses_in_position(now, pos)

        # 1. Update Trailing Stop Loss if profit hits +15% (legacy)
        if live_premium >= entry_price * 1.15 and not pos.get('tsl_active'):
            pos['tsl_active'] = True
            pos['dynamic_sl'] = entry_price * 1.02
            logger.info(f"🟢 TRAILING STOP ACTIVATED! SL moved to Breakeven (+2%): Rs.{pos['dynamic_sl']:.2f}")

        current_sl = pos.get('dynamic_sl', pos['sl_price'])
        
        # 2. Check Exits for remaining lots
        time_held_mins = (now - datetime.fromisoformat(pos['entry_time'])).total_seconds() / 60
        max_hold = self.theta_shield_expiry_mins if pos.get('is_expiry_day') else self.theta_shield_normal_mins

        exit_reason = None
        if live_premium <= current_sl:
            exit_reason = "TRAILING STOP" if pos.get('tsl_active') else "HARD STOP LOSS"
        elif time_held_mins >= max_hold:
            exit_reason = "THETA SHIELD (Time Stop Exceeded)"
        else:
            active_reason = self._check_active_exits(now, pos, live_premium, time_held_mins)
            if active_reason:
                exit_reason = active_reason

        if exit_reason:
            self._close_position(exit_reason, exit_price=live_premium, pos=pos)
            # ── Zone-inv guard: 2 consecutive → lock for day
            if "ZONE INVALIDATED" in exit_reason:
                self._consecutive_zone_inv += 1
                self._sl_hit_count = 0  # reset SL counter on zone-inv
                if self._consecutive_zone_inv >= 2:
                    self._regime_lock = "NO_TRADE"
                    logger.warning(
                        f"[ZONE-LOCK] {self._consecutive_zone_inv} consecutive zone "
                        f"invalidations — locking for rest of day."
                    )
            # ── SL cooldown: 2 SL hits → pause 20 min + reassess ─────
            elif "STOP LOSS" in exit_reason or "HARD STOP" in exit_reason:
                self._sl_hit_count += 1
                self._consecutive_zone_inv = 0  # reset zone-inv counter on SL
                if self._sl_hit_count >= 2:
                    cooldown_mins = 20
                    self._cooldown_until = now + timedelta(minutes=cooldown_mins)
                    self._cooldown_reason = f"2 SL hits — reassessing S/R, OI, trend"
                    self._sl_hit_count = 0  # reset after triggering cooldown
                    logger.warning(
                        f"[COOLDOWN] {cooldown_mins}min pause starting now. "
                        f"Reassessing: S/R zones, OI flows, trend direction."
                    )
            else:
                self._consecutive_zone_inv = 0
                # Winning trade resets SL counter — market "forgave" us
                self._sl_hit_count = 0
            return

        tier_status = ""
        if not pos.get('tier1_done'):
            tier_status = f" | T1: {pts_from_entry:+.0f}/{TIER1_PTS}pts"
        elif not pos.get('tier2_done'):
            tier_status = f" | T2: {pts_from_entry:+.0f}/{TIER2_PTS}pts"
        elif pos.get('trail_active'):
            tier_status = f" | TRAIL at cost {pos['trail_cost']:.1f}"
        logger.info(f"Holding: {pos['trade_type']} | Live: Rs.{live_premium:.2f} | "
                    f"Remaining: {pos.get('remaining_lots', '?')} lots | "
                    f"Time: {int(time_held_mins)}m{tier_status}")

    # ═══════════════════════════════════════════════════════════════
    # OI-FLOW ENGINE TICK (v1.1)
    # ═══════════════════════════════════════════════════════════════


    def _execute_ic_entry(self, signal: dict, premiums: dict, now):
        strikes = signal["strikes"]
        lots = 12
        self.engine.open_ic_position(signal, premiums, now, lots)
        credit = signal.get("credit", 0.0)
        logger.info(f"[OI-Flow] IRON CONDOR ENTRY | Credit: {credit:.1f} | Lots: {lots}")
        trade = {
            "entry_time": now.isoformat(),
            "trade_type": "IRON_CONDOR",
            "strike": 0,
            "opt_type": "IC",
            "entry_price": credit,
            "qty": lots * self.lot_size,
            "sl_price": credit * 1.5,
            "target_price": credit * 0.5,
        }
        self.portfolio["open_position"] = trade

    def _execute_ic_exit(self, reason: str, entry_credit: float, exit_cost: float, now):
        lots = 12
        if self.engine.ic_position:
            lots = self.engine.ic_position.get("lots", 12)
        pnl = (entry_credit - exit_cost) * lots * self.lot_size
        ic_signal = {"close_cost": exit_cost, "reason": reason}
        self.engine.close_ic_position(ic_signal, {}, now)
        self.portfolio["capital"] += pnl
        if "trade_history" not in self.portfolio:
            self.portfolio["trade_history"] = []
        trade = self.portfolio.get("open_position", {})
        trade.update({
            "exit_time": now.isoformat(),
            "exit_price": exit_cost,
            "pnl": pnl,
            "reason": reason
        })
        self.portfolio["trade_history"].append(trade)
        self.portfolio["open_position"] = None
        logger.info(f"[OI-Flow] IRON CONDOR EXIT | Reason: {reason} | Exit Cost: {exit_cost:.1f} | P&L: Rs.{pnl:.0f}")

    def _run_oi_flow_tick(self, now):
        """Single tick for OI-Flow engine. Called every loop iteration."""
        # Bug #4 fix: Don't lock strikes or process data before 09:20 IST.
        # At 09:15 the OI/IV/PCR/premiums are all zero — let sellers and
        # buyers take their positions first, then lock strikes on real data.
        if now.time() < dtime(9, 20):
            return

        spot = self.fetcher.get_spot()
        if spot <= 0:
            return

        # === EXACT 14:55 EXPIRY LONG STRADDLE LOGIC (Writer Exit Volatility) ===
        now_dt = now.date()
        is_expiry = (self.fetcher.get_expiry_date() == now_dt.isoformat())
        if is_expiry and dtime(14, 55) <= now.time() < dtime(14, 56):
            if not self.portfolio.get("open_straddle") and self.portfolio.get("straddle_executed_today") != now_dt.isoformat():
                logger.info(f"dYY [EXPIRY STRADDLE] 14:55 PM Trigger Activated! Spot: {spot:.1f}")
                signal = {
                    "direction": "CE",
                    "reason": ["14:55_EXPIRY_STRADDLE"],
                    "is_straddle": True,
                    "strike_offset": 0,
                    "second_strike_offset": 0,
                    "combined_sl_pct": 0.50,
                    "tactic_tp_pct": 1.0,
                    "tactic_time_stop_min": 10,  # 10 minute hold to catch the 3:00 PM candle
                    "tactic_name": "expiry_straddle"
                }
                self._open_straddle_position(signal, now, spot)
                if self.portfolio.get("open_straddle"):
                    self.portfolio["straddle_executed_today"] = now_dt.isoformat()
                    self.save_portfolio()

        # Ensure strikes are locked before building OI snapshot
        if not self.engine._strikes_locked:
            self.engine.lock_strikes(spot)

        # Build OI snapshot and premiums for ALL fixed strikes.
        # Bug #1+#5 fix: Previously premiums were only built when a position
        # was already open, so the Theta Shield always saw 0 and locked
        # down permanently. Also keys were integers (24000) but engine
        # looks up strings ("24000_CE"). Now we always populate both.
        oi_snapshot = {}
        premiums = {}
        try:
            ltp_map = self.fetcher.get_ltp_map()
            # Track LIVE ATM strikes on every tick instead of locking old 09:20 strikes
            atm_strike = round(spot / self.engine.strike_step) * self.engine.strike_step
            live_ce = [atm_strike, atm_strike + self.engine.strike_step, atm_strike + 2 * self.engine.strike_step]
            live_pe = [atm_strike, atm_strike - self.engine.strike_step, atm_strike - 2 * self.engine.strike_step]
            all_strikes = live_ce + live_pe
            for s in all_strikes:
                s_int = int(s)
                ce_oi = self._get_option_oi(s_int, "CE")
                pe_oi = self._get_option_oi(s_int, "PE")
                oi_snapshot[s_int] = {"ce_oi": ce_oi, "pe_oi": pe_oi}
                # Always feed premiums for BOTH CE and PE with string keys
                premiums[f"{s_int}_CE"] = ltp_map.get(f"{s_int}_CE", 0)
                premiums[f"{s_int}_PE"] = ltp_map.get(f"{s_int}_PE", 0)

            # Ensure currently open position's dynamic strikes are always populated in the premiums map
            if self.engine.position:
                direction = self.engine.position["direction"]
                for s in self.engine.position["strikes"]:
                    s_int = int(s)
                    premiums[f"{s_int}_{direction}"] = ltp_map.get(f"{s_int}_{direction}", 0)

            # IC position premiums (if active)
            if getattr(self.engine, 'ic_position', None):
                for leg_key, s_val in self.engine.ic_position["strikes"].items():
                    s_int = int(s_val)
                    opt_dir = "CE" if "ce" in leg_key else "PE"
                    premiums[f"{s_int}_{opt_dir}"] = ltp_map.get(f"{s_int}_{opt_dir}", 0)
        except Exception as e:
            logger.warning(f"OI-Flow snapshot build error: {e}")
            return

        # Main tick
        actions = self.engine.tick(spot, now, oi_snapshot, premiums, self.fetcher)

        if not actions:
            return

        for action in actions:
            if action["action"] == "entry":
                self._execute_oi_flow_entry(action, action.get("premiums", {}), now)
            elif action["action"] == "exit":
                self._execute_oi_flow_exit(action["reason"], premiums, now)
            elif action["action"] == "partial_exit":
                self._execute_oi_flow_partial(premiums, now)
            elif action["action"] == "entry_ic":
                self._execute_ic_entry(action, action.get("premiums", {}), now)
            elif action["action"] == "exit_ic":
                self._execute_ic_exit(action["reason"], action.get("credit", 0.0), action.get("exit_cost", 0.0), now)

        # Save state
        try:
            state_file = f"data/oi_flow_state_{self.trading_index}.json"
            self.engine.save_state(state_file)
        except Exception as e:
            logger.warning(f"Failed to save engine state: {e}")

    def _get_option_oi(self, strike: int, opt_type: str) -> int:
        """Get OI for a strike/type from fetcher's oi_map cache."""
        oi_map = self.fetcher.get_oi_map()
        return oi_map.get(f"{strike}_{opt_type}", 0)

    def _execute_oi_flow_entry(self, signal: dict, premiums: dict, now):
        """Place entry orders and record position."""
        direction = signal["direction"]
        strikes = signal["strikes"]
        spot = signal["spot"]
        size_mult = signal.get("size_multiplier", 1.0)

        # Get real premiums from fetcher
        ltp_map = self.fetcher.get_ltp_map()
        real_premiums = {}
        for s in strikes:
            key = f"{int(s)}_{direction}"
            ltp = ltp_map.get(key, 0)
            if ltp <= 0:
                logger.warning(f"[OI-Flow] No LTP for {key}, skipping entry")
                return
            real_premiums[int(s)] = ltp

        # Compute lot allocation
        lots = self.engine.compute_lot_allocation(real_premiums, size_mult)

        # Open position in engine
        self.engine.open_position(signal, real_premiums, now, lots)

        # Record in portfolio
        new_pos = {
            "entry_time": now.isoformat(),
            "trade_type": f"BUY {direction}",
            "strikes": strikes,
            "direction": direction,
            "entry_premiums": real_premiums,
            "lots_per_strike": lots,
            "entry_spot": float(spot),
            "engine": "oi_flow_v1.1",
        }
        self.portfolio.setdefault("open_positions", []).append(new_pos)
        self.save_portfolio()

        # Journal: record entry
        if self.journal is not None and self._journal_day_started:
            try:
                for strike, strike_lots in lots.items():
                    self.journal.on_entry(
                        tactic="oi_flow",
                        direction=direction,
                        strike=int(strike),
                        entry_ts=now,
                        entry_premium=real_premiums[int(strike)],
                        qty_lots=int(strike_lots),
                        sl_pct=0.25,
                        tp_pct=0.50,
                        time_stop_min=30,
                        regime_at_entry="trending",
                        entry_state={"spot": float(spot)}
                    )
            except Exception as e:
                logger.warning(f"Journal on_entry for OI-Flow failed: {e}")

        total_lots = sum(lots.values())
        logger.info(f"[OI-Flow] ENTRY: {direction} | Strikes={strikes} "
                    f"| Premiums={real_premiums} | Lots={lots} | Spot={spot:.0f}")
        self.telegram.send_message(
            f"OI-FLOW ENTRY\n"
            f"Type: BUY {direction} | Strikes: {strikes}\n"
            f"Premiums: {real_premiums}\n"
            f"Lots: {lots} | Spot: {spot:.0f}\n"
            f"OI Delta: {signal.get('oi_delta', 0):+.0f}"
        )

    def _execute_oi_flow_exit(self, reason: str, premiums: dict, now):
        """Close position and record P&L."""
        # Get real exit premiums
        direction = self.engine.position["direction"]
        ltp_map = self.fetcher.get_ltp_map()
        exit_premiums = {}
        for s in self.engine.position["strikes"]:
            key = f"{int(s)}_{direction}"
            exit_premiums[int(s)] = ltp_map.get(key, 0)

        result = self.engine.close_position(reason, exit_premiums, now)
        if result is None:
            return

        # Update portfolio
        for pos in list(self.portfolio.get("open_positions", [])):
            if pos.get("engine") == "oi_flow_v1.1":
                self.portfolio["open_positions"].remove(pos)
                self.portfolio["capital"] += result["total_pnl"]
                # Record in trade history
                self.portfolio.setdefault("trade_history", []).append({
                    "entry_time": pos["entry_time"],
                    "exit_time": now.isoformat(),
                    "trade_type": pos["trade_type"],
                    "strikes": pos["strikes"],
                    "entry_premiums": pos["entry_premiums"],
                    "exit_premiums": exit_premiums,
                    "pnl": result["total_pnl"],
                    "reason": reason,
                    "engine": "oi_flow_v1.1",
                })
                break
        self.save_portfolio()

        # Journal: record exit
        if self.journal is not None and self._journal_day_started:
            try:
                for strike, pnl in result.get("pnl_per_strike", {}).items():
                    self.journal.on_exit(
                        tactic="oi_flow",
                        exit_ts=now,
                        exit_premium=exit_premiums.get(int(strike), 0.0),
                        exit_reason=reason,
                        net_pnl=pnl
                    )
            except Exception as e:
                logger.warning(f"Journal on_exit for OI-Flow failed: {e}")

        logger.info(f"[OI-Flow] EXIT: {direction} | Reason={reason} "
                    f"| P&L=Rs.{result['total_pnl']:+.0f} | "
                    f"Hold={result['hold_minutes']:.0f}min")
        self.telegram.send_message(
            f"OI-FLOW EXIT\n"
            f"Type: {direction} | Reason: {reason}\n"
            f"P&L: Rs.{result['total_pnl']:+,.0f} | "
            f"Hold: {result['hold_minutes']:.0f}min"
        )

    def _execute_oi_flow_partial(self, premiums: dict, now):
        """Execute partial exit (50% scale-out of position)."""
        direction = self.engine.position["direction"]
        ltp_map = self.fetcher.get_ltp_map()

        total_exit_pnl = 0.0
        for s in self.engine.position["strikes"]:
            key = f"{int(s)}_{direction}"
            exit_ltp = ltp_map.get(key, 0)
            entry_ltp = self.engine.entry_premiums.get(s, exit_ltp)
            lots = self.engine.position.get("lots_per_strike", {}).get(s, 0)
            if lots <= 0:
                continue
            exit_lots = max(1, int(lots * 0.5))
            
            pnl = (exit_ltp - entry_ltp) * exit_lots * self.lot_size
            total_exit_pnl += pnl
            
            # Reduce the remaining lots in the engine
            remaining = lots - exit_lots
            if remaining > 0:
                self.engine.position["lots_per_strike"][s] = remaining
            else:
                self.engine.position["lots_per_strike"].pop(s, None)

        self.portfolio["capital"] += total_exit_pnl

        logger.info(f"[OI-Flow] PARTIAL EXIT (50% Scale-Out) | P&L=Rs.{total_exit_pnl:+.0f} | "
                    f"Runner SL moved to breakeven")
        self.telegram.send_message(
            f"OI-FLOW PARTIAL EXIT (50% Target Hit)\n"
            f"Booked: Rs.{total_exit_pnl:+,.0f}\n"
            f"Remaining 50% runner trailing at breakeven"
        )
        self.save_portfolio()

    # ═══════════════════════════════════════════════════════════════

    def _scan_for_entries(self, now):
        # `now` is already an IST tz-aware datetime supplied by run()
        if now.time() < ENTRY_WINDOW_OPEN: return

        # ---------------------------------------------------------------------
        # 3:00 PM EXACT NIFTY STRADDLE (Expiry Day Only)
        # ---------------------------------------------------------------------
        if self.trading_index == "NIFTY":
            now_dt = now.date()
            if self.fetcher.get_expiry_date() == now_dt.isoformat():
                # Trigger strictly between 15:00:00 and 15:00:59
                if dtime(15, 0) <= now.time() < dtime(15, 1):
                    if not self.portfolio.get("open_straddle") and self.portfolio.get("straddle_executed_today") != now_dt.isoformat():
                        spot = self.fetcher.get_spot()
                        if spot > 0:
                            logger.info(f"dYY [EXPIRY STRADDLE] 3:00 PM Trigger Activated! Spot: {spot:.1f}")
                            signal = {
                                "direction": "CE",
                                "reason": ["3_PM_EXPIRY_STRADDLE"],
                                "is_straddle": True,
                                "strike_offset": 0,
                                "second_strike_offset": 0,
                                "combined_sl_pct": 0.50,
                                "tactic_name": "t2_expiry_straddle"
                            }
                            self._open_straddle_position(signal, now, spot)
                            
                            # Only mark as executed if the position successfully opened (premiums were valid)
                            if self.portfolio.get("open_straddle"):
                                self.portfolio["straddle_executed_today"] = now_dt.isoformat()
                                self.save_portfolio()
                            
                            return

        htf_state = "?"  # default — HTF scoring removed in v4.1, kept for log compat

        # ── Cooldown check (SL-triggered tactical pause) ────────────────
        if self._cooldown_until is not None:
            if now < self._cooldown_until:
                remaining = int((self._cooldown_until - now).total_seconds() / 60) + 1
                if remaining % 5 == 0:  # log every 5 min during cooldown
                    logger.info(f"[COOLDOWN] {remaining}min remaining — {self._cooldown_reason}")
                return
            # ── Cooldown expired: reassess market before resuming ────────
            self._cooldown_until = None
            decision = self._reassess_market(now)
            if not decision["can_trade"]:
                # Extend cooldown by 10 min if market still unclear
                self._cooldown_until = now + timedelta(minutes=10)
                self._cooldown_reason = f"reassess: {decision['reason']}"
                logger.warning(f"[COOLDOWN] Extended 10min — {decision['reason']}")
                return
            logger.info(f"[COOLDOWN] Reassessment PASSED — resuming. {decision['reason']}")
            self._cooldown_reason = ""

        # Daily entry cap. Counter resets at the first scan of each new day.
        today_str = now.date().isoformat()
        if self._positions_today_date != today_str:
            self._positions_today_date = today_str
            self._positions_today_count = 0
            # PR 2: also reset session-start cap, loss counter, regime lock
            self._session_start_capital = self.portfolio["capital"]
            self._consecutive_losses = 0
            self._regime_lock = None
            self._consecutive_zone_inv = 0
            self._sl_hit_count = 0
            self._cooldown_until = None
            self._cooldown_reason = ""
            self._regime_lock_reasons = []
            if hasattr(self, "engine") and self.engine is not None:
                self.engine.trades_today = 0
                self.engine.losses_today = 0
                self.engine.last_trade_peak_spot = 0.0
                self.engine.last_trade_trough_spot = float('inf')
            logger.info(f"[DAY-START] cap={self._session_start_capital:.0f}, "
                        f"drawdown breaker armed at -{self.daily_drawdown_pct*100:.1f}%")
        if self._positions_today_count >= self.max_positions_per_day:
            return  # cap reached — silent skip

        # PR 2: regime lock from consecutive-loss diagnostic
        if self._regime_lock == "STOPPED":
            return  # silent skip — already logged when lock was set

        # PR 2: daily drawdown circuit breaker
        if self._session_start_capital is not None:
            loss = self._session_start_capital - self.portfolio["capital"]
            if loss > self.daily_drawdown_pct * self._session_start_capital:
                if self._regime_lock != "STOPPED":  # log once
                    self._regime_lock = "STOPPED"
                    msg = (f"[DRAWDOWN BREAKER] Loss Rs.{loss:,.0f} exceeds "
                           f"{self.daily_drawdown_pct*100:.1f}% of "
                           f"Rs.{self._session_start_capital:,.0f}. Halting entries.")
                    logger.warning(msg)
                    self.telegram.send_message(msg)
                return

        spot = self.fetcher.get_spot()
        sup = self.fetcher.get_support()
        res = self.fetcher.get_resistance()
        focus_pcr = self.fetcher.get_focus_pcr()

        if spot == 0 or sup == 0: return

        # ── Trend-confidence chop guard (added 2026-05-17) ────────────────
        # Block directional entries when the recent spot move is within
        # noise. Score < 1.0 = move is indistinguishable from chop. This
        # gate is bypassed for T1/T2 entries (handled by dispatcher) since
        # they have their own time-gated entry windows independent of trend.
        #
        # OI-AWARE OVERRIDE (added 2026-05-26): When the OI conviction is
        # strong (PE writers >> CE writers for bullish, or CE writers >>
        # PE writers for bearish), the price-based chop guard is overridden.
        # Rationale: OI flows are a leading indicator — smart money
        # positioning can signal a trend before price volatility confirms it.
        if self.engine_mode == "regime":
            try:
                # ── PCR BYPASS ───────────────────────────────────────
                # Two ways to bypass chop guard:
                #  1. PCR data missing (0 or None) → bypass, trust gut
                #  2. PCR in CE range (0.85-1.60) or PE range (0.40-1.15)
                #     → PCR confirms direction, price chop is noise
                current_pcr = self.fetcher.get_focus_pcr()
                
                # CE range: 0.85 - 1.60, PE range: 0.40 - 1.15
                pcr_in_ce_range = 0.85 <= current_pcr <= 1.60
                pcr_in_pe_range = 0.40 <= current_pcr <= 1.15
                
                if current_pcr <= 0:
                    logger.info(
                        f"[CHOP GUARD] PCR data missing (PCR={current_pcr}) — "
                        f"bypassing chop guard. Gut mode. Spot {spot:.1f}"
                    )
                elif pcr_in_ce_range or pcr_in_pe_range:
                    pcr_zone = "CE" if pcr_in_ce_range else "PE"
                    logger.info(
                        f"[CHOP GUARD] PCR={current_pcr:.2f} in {pcr_zone} range "
                        f"(CE:0.85-1.60, PE:0.40-1.15) — "
                        f"bypassing chop guard. PCR confirms direction. Spot {spot:.1f}"
                    )
                else:
                    snap = self.dispatcher.indicators.snapshot()
                    tc_score = float(snap.get("trend_confidence", 0) or 0)
                    tc_dir = snap.get("trend_direction", "FLAT")
                    if 0 < tc_score < 0.5:
                        # ── OI conviction override ──────────────────────────
                        oi_override = False
                        oi_override_dir = None
                        try:
                            oi = self.fetcher.get_oi_pattern()
                            if oi and isinstance(oi, dict):
                                ce_chg = abs(float(oi.get("ce_oi_change", 0) or 0))
                                pe_chg = abs(float(oi.get("pe_oi_change", 0) or 0))
                                # PE writers building faster → bullish conviction
                                if ce_chg > 0 and pe_chg > 0:
                                    pe_ce_ratio = pe_chg / ce_chg
                                    if pe_ce_ratio >= 1.30:
                                        oi_override = True
                                        oi_override_dir = "CE"
                                    elif pe_ce_ratio <= 0.70:
                                        oi_override = True
                                        oi_override_dir = "PE"
                        except Exception:
                            pass

                        if oi_override and oi_override_dir:
                            logger.info(
                                f"[CHOP GUARD] Trend confidence {tc_score:.2f}x ({tc_dir}) "
                                f"OVERRIDDEN — OI conviction {oi_override_dir} "
                                f"(PE/CE OI ratio). Spot {spot:.1f}"
                            )
                        else:
                            logger.info(
                                f"[CHOP GUARD] Trend confidence {tc_score:.2f}x ({tc_dir}) "
                                f"below 0.5 — skipping entry scan. Spot {spot:.1f}"
                            )
                            return
            except Exception as e:
                logger.debug(f"trend-confidence check failed: {e}")

        # Dispatcher: legacy mode -> SignalEngine.evaluate; regime mode -> classifier+router
        signal = self.dispatcher.evaluate(
            ts=now,
            fetcher=self.fetcher,
            engine=self.engine,
            in_position=len(self.portfolio.get("open_positions", [])) > 0,
        )

        # Register any near-misses surfaced by the dispatcher
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

        # T2: straddle signals
        if signal.get('is_straddle'):
            self._open_straddle_position(signal, now, spot)
            return

        if signal['direction']:
            direction = signal['direction']

            # PR 2: regime lock from consecutive-loss or zone-inv diagnostic
            if self._regime_lock == "NO_TRADE":
                return
            if self._regime_lock == "CE_ONLY" and direction == "PE":
                logger.info(f"[REGIME LOCK] PE blocked")
                return
            if self._regime_lock == "PE_ONLY" and direction == "CE":
                logger.info(f"[REGIME LOCK] CE blocked")
                return

            # ── ITM strike selection (2026-05-26) ──────────────────────────
            # Pick 1-2 strikes ITM for higher delta (0.65-0.85) so premium
            # moves faster with spot and theta decay has less relative impact.
            # CE (bullish): 1-2 strikes BELOW ATM → ITM call
            # PE (bearish): 1-2 strikes ABOVE ATM → ITM put
            atm_strike = int(round(spot / self.strike_step) * self.strike_step)
            itm_offset = 1  # try 1 strike ITM first; fall back to 2 if delta too low
            max_attempts = 2

            best_strike = atm_strike
            live_premium = 0.0
            greeks = {}

            for attempt in range(max_attempts):
                if direction == "CE":
                    candidate_strike = atm_strike - ((attempt + 1) * self.strike_step)
                else:  # PE
                    candidate_strike = atm_strike + ((attempt + 1) * self.strike_step)

                candidate_premium = self.fetcher.get_option_ltp(candidate_strike, direction)
                if candidate_premium <= 0:
                    continue
                candidate_greeks = self.fetcher.get_strike_greeks(candidate_strike, direction)
                candidate_delta = abs(float(candidate_greeks.get('delta', 0) or 0))
                if candidate_delta == 0:
                    candidate_delta = 0.50

                # Accept if delta >= 0.55 (meaningful ITM) or this is our last attempt
                if candidate_delta >= 0.55 or attempt == max_attempts - 1:
                    best_strike = candidate_strike
                    live_premium = candidate_premium
                    greeks = candidate_greeks
                    break

            # Fallback: if no ITM strike found, use ATM
            if live_premium <= 0:
                best_strike = atm_strike
                live_premium = self.fetcher.get_option_ltp(best_strike, direction)
                greeks = self.fetcher.get_strike_greeks(best_strike, direction)

            delta = greeks.get('delta', 0)
            
            # API Fallback: If Upstox fails to provide Live Greeks for this contract, assume theoretical ATM delta.
            if delta == 0:
                delta = 0.50
                
            if delta < 0.30: # Allowing slight leniency if actual ATM drops dropping live
                logger.info(f"Signal Generated ({direction}) but ATM Delta is too low ({delta:.2f}). Rejecting.")
                return

            # ── Fixed 5-lot position with tiered profit booking ──
            use_pct_scale = self.config.get("enable_3tf_filters", self.opts.get("enable_3tf_filters", False))
            TOTAL_LOTS = int(self.opts.get("fixed_lots_to_trade", 5)) if use_pct_scale else 5
            size_tag = "FULL" if TOTAL_LOTS >= 5 else "REDUCED"  # v4.1: simplified from HTF scoring
            
            TIER1_PTS = 20
            TIER2_PTS = 35
            TIER1_LOTS = 3
            TIER2_LOTS = 1
            TRAIL_LOTS = 1

            qty = self.lot_size * TOTAL_LOTS
            is_expiry = signal['is_expiry_day']
            
            # ── Tiered SL based on entry premium ──────────────────────
            # Expensive ITM strikes (≥₹100): tight 12% — moves with delta
            # Mid-range (₹70-100): 15%
            # Near ATM (₹40-70): 20%
            # Cheap OTM (<₹40): 25% — needs room for volatility
            if live_premium >= 100:
                sl_pct = 0.12
            elif live_premium >= 70:
                sl_pct = 0.15
            elif live_premium >= 40:
                sl_pct = 0.20
            else:
                sl_pct = 0.25
                
            time_stop_min = signal.get('tactic_time_stop_min', 45 if is_expiry else 120)
            tgt_pct = signal.get('tactic_tp_pct') or (0.35 if is_expiry else 0.50)
            sl_prem = live_premium * (1 - sl_pct)
            tactic_name = signal.get('tactic_name', 'oi_wall_mean_reversion')

            # Increment daily entry counter — checked at top of next scan.
            self._positions_today_count += 1

            # PR 3: snapshot entry-time PCR + focus-zone OI so the
            # active-management exits have a baseline to compare against.
            entry_oi = self.fetcher.get_oi_pattern() or {}
            new_position = {
                "entry_time": now.isoformat(),
                "trade_type": f"BUY {direction}",
                "strike": best_strike,
                "opt_type": direction,
                "entry_price": live_premium,
                "qty": qty,
                "total_lots": TOTAL_LOTS,
                "remaining_lots": TOTAL_LOTS,
                "lots_sold": 0,
                "tier1_done": False,
                "tier2_done": False,
                "trail_active": False,
                "trail_cost": live_premium,  # breakeven level for trail lot
                "sl_price": sl_prem,
                "target_price": None,  # no single target — tiered exits
                "is_expiry_day": is_expiry,
                "tsl_active": False,
                "tactic_name": tactic_name,
                "sl_pct": sl_pct,
                "tp_pct": 0.0,   # not used with tiered exits
                "time_stop_min": time_stop_min,
                "entry_pcr": float(focus_pcr),
                "entry_spot": float(spot),
                "entry_total_ce_oi": float(entry_oi.get("total_ce_oi", 0) or 0),
                "entry_total_pe_oi": float(entry_oi.get("total_pe_oi", 0) or 0),
                "htf_state": htf_state,
                "event_type": signal.get('event_type', 'bos'),
                "event_dir": signal.get('event_dir', direction.lower()),
            }
            self.portfolio.setdefault("open_positions", []).append(new_position)
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

            itm_tag = f"ITM ({abs(atm_strike - best_strike) // self.strike_step} strike{'s' if abs(atm_strike - best_strike) // self.strike_step > 1 else ''} in)" if best_strike != atm_strike else "ATM"
            sup = self.fetcher.get_support()
            res = self.fetcher.get_resistance()
            
            if use_pct_scale:
                SCALE_TP_PCT = float(self.opts.get("scale_out_tp_percent", 15))
                RUNNER_TP_PCT = float(self.opts.get("scale_out_runner_percent", 30))
                HARD_SL_PCT = float(self.opts.get("scale_out_hard_sl_percent", 20))
                
                msg = (f"🚀 PAPER TRADE ENTERED (PriceActionBot)\n"
                       f"Type: BUY {best_strike} {direction} [{itm_tag}]\n"
                       f"Entry: Rs. {live_premium:.2f} x {TOTAL_LOTS} lots | HTF: {htf_state.upper()} ({size_tag})\n"
                       f"Zone: S={sup} → R={res}\n"
                       f"Scale-out: +{SCALE_TP_PCT:.0f}% (Sell 3 lots) → SL to BE\n"
                       f"Runner: +{RUNNER_TP_PCT:.0f}% (Sell 2 lots)\n"
                       f"Stop Loss: -{sl_pct*100:.0f}% (Rs. {sl_prem:.2f})\n"
                       f"Reason: {signal['reasons'][0]}")
            else:
                msg = (f"🚀 PAPER TRADE ENTERED (PriceActionBot)\n"
                       f"Type: BUY {best_strike} {direction} [{itm_tag}]\n"
                       f"Entry: Rs. {live_premium:.2f} x {TOTAL_LOTS} lots | HTF: {htf_state.upper()}\n"
                       f"Zone: S={sup} → R={res}\n"
                       f"SL: Rs. {sl_prem:.2f} | Delta: {delta:.2f}\n"
                       f"Reason: {signal['reasons'][0]}")
            
            logger.info(msg)
            self.telegram.send_message(msg)
        else:
            reason_summary = signal['reasons'][0] if signal['reasons'] else "No signal"
            logger.info(f"Scanning... Spot: {spot:.0f} | HTF: {signal.get('htf_state', '?')} | {reason_summary}")


    def _check_active_exits(self, now, pos, live_premium, time_held_mins):
        """Position-management exits beyond static SL/TP.

        Exit triggers (first to fire wins):
          1. ZONE TARGET — spot reached the opposite S/R boundary.
             CE: spot near resistance → take profit.
             PE: spot near support → take profit.
          2. ZONE INVALIDATION — spot broke through the defending wall.
             CE: spot dropped below support → cut loss.
             PE: spot rose above resistance → cut loss.
          3. THETA / SIDEWAYS GUARD — held too long with no directional
             progress. Spot hasn't moved >0.15% toward target after 45 min
             → theta is eating us, exit.
          4. PCR shift exit — entry-time PCR moved >0.10 against the trade
             AND spot confirms reversal (legacy, kept as safety net).
          5. Adverse OI exit — focus-zone OI built up against position >8%
             AND spot confirms reversal (legacy).
          6. Time-stop scaling — at <0.5% profit after time_stop_minutes,
             exit (legacy).

        Returns: exit_reason string, or None if no trigger fires.
        """
        direction = pos.get('opt_type')
        entry_spot = float(pos.get('entry_spot', 0) or 0)

        try:
            cur_spot = float(self.fetcher.get_spot() or 0)
        except Exception:
            cur_spot = 0.0

        sup = self.fetcher.get_support()
        res = self.fetcher.get_resistance()

        # ── TRIGGER 1: Zone Target ──────────────────────────────────────
        # CE: spot near resistance → profit target reached.
        # PE: spot near support → profit target reached.
        # "Near" = within 0.15% of the boundary.
        if entry_spot > 0 and cur_spot > 0 and sup > 0 and res > 0:
            zone_proximity_pct = 0.0015  # 0.15%
            if direction == 'CE':
                dist_to_res = abs(cur_spot - res) / cur_spot
                if dist_to_res <= zone_proximity_pct:
                    return (f"ZONE TARGET HIT ({direction}: "
                            f"spot {cur_spot:.0f} at resistance {res}, "
                            f"entry {entry_spot:.0f})")
            else:  # PE
                dist_to_sup = abs(cur_spot - sup) / cur_spot
                if dist_to_sup <= zone_proximity_pct:
                    return (f"ZONE TARGET HIT ({direction}: "
                            f"spot {cur_spot:.0f} at support {sup}, "
                            f"entry {entry_spot:.0f})")

        # ── TRIGGER 2: Zone Invalidation ────────────────────────────────
        # CE: spot broke below support → the floor is gone, get out.
        # PE: spot broke above resistance → the ceiling broke, get out.
        # Note: 15% SL should fire BEFORE this on most losing trades.
        # Zone-inv is the last-resort safety net.
        if entry_spot > 0 and cur_spot > 0 and sup > 0 and res > 0:
            if direction == 'CE' and cur_spot < sup:
                return (f"ZONE INVALIDATED ({direction}: "
                        f"spot {cur_spot:.0f} broke support {sup})")
            if direction == 'PE' and cur_spot > res:
                return (f"ZONE INVALIDATED ({direction}: "
                        f"spot {cur_spot:.0f} broke resistance {res})")

        # ── TRIGGER 3: Theta / Sideways Guard ───────────────────────────
        # If we've held for 45+ minutes and spot hasn't moved at least
        # 0.15% toward the target, theta decay is eating premium with
        # no directional edge — exit.
        if entry_spot > 0 and cur_spot > 0 and time_held_mins >= 45:
            if direction == 'CE':
                progress_pct = (cur_spot - entry_spot) / entry_spot
            else:
                progress_pct = (entry_spot - cur_spot) / entry_spot
            if progress_pct < 0.0015:  # less than 0.15% progress
                return (f"THETA GUARD ({time_held_mins:.0f}m: "
                        f"spot moved {progress_pct*100:.2f}% toward target, "
                        f"below 0.15% — sideways chop)")

        # ── TRIGGER 4: PCR shift (legacy, with spot confirmation) ──────
        spot_known = entry_spot > 0 and cur_spot > 0
        if direction == 'CE':
            spot_confirms_reversal = (not spot_known) or cur_spot < entry_spot
        else:
            spot_confirms_reversal = (not spot_known) or cur_spot > entry_spot

        thr = self.pcr_shift_exit_threshold
        try:
            entry_pcr = float(pos.get('entry_pcr', 0) or 0)
            cur_pcr = float(self.fetcher.get_focus_pcr() or 0)
            if entry_pcr > 0 and cur_pcr > 0:
                shift = cur_pcr - entry_pcr
                fired = (direction == 'CE' and shift <= -thr) or \
                        (direction == 'PE' and shift >= thr)
                if fired and spot_confirms_reversal:
                    spot_tag = (f" spot {entry_spot:.0f}->{cur_spot:.0f}"
                                if spot_known else "")
                    return (f"PCR SHIFT EXIT ({direction}: "
                            f"{entry_pcr:.2f} -> {cur_pcr:.2f}, "
                            f"delta {shift:+.2f}){spot_tag}")
        except Exception:
            pass

        # ── TRIGGER 5: adverse OI build (legacy, with spot confirmation)
        try:
            entry_ce = float(pos.get('entry_total_ce_oi', 0) or 0)
            entry_pe = float(pos.get('entry_total_pe_oi', 0) or 0)
            cur_oi = self.fetcher.get_oi_pattern() or {}
            cur_ce = float(cur_oi.get('total_ce_oi', 0) or 0)
            cur_pe = float(cur_oi.get('total_pe_oi', 0) or 0)
            if direction == 'CE' and entry_ce > 0 and cur_ce > 0:
                growth = (cur_ce - entry_ce) / entry_ce
                if growth >= self.adverse_oi_growth_exit_pct and spot_confirms_reversal:
                    spot_tag = (f" spot {entry_spot:.0f}->{cur_spot:.0f}"
                                if spot_known else "")
                    return (f"ADVERSE OI EXIT (CE OI grew {growth*100:+.1f}% "
                            f"- call writers stacking){spot_tag}")
            if direction == 'PE' and entry_pe > 0 and cur_pe > 0:
                growth = (cur_pe - entry_pe) / entry_pe
                if growth >= self.adverse_oi_growth_exit_pct and spot_confirms_reversal:
                    spot_tag = (f" spot {entry_spot:.0f}->{cur_spot:.0f}"
                                if spot_known else "")
                    return (f"ADVERSE OI EXIT (PE OI grew {growth*100:+.1f}% "
                            f"- put writers stacking){spot_tag}")
        except Exception:
            pass

        # ── TRIGGER 6: time-stop scaling (legacy) ───────────────────────
        try:
            entry_price = float(pos.get('entry_price', 0) or 0)
            if entry_price > 0 and time_held_mins >= self.time_stop_minutes:
                gain_pct = (live_premium - entry_price) / entry_price
                if gain_pct < self.time_stop_min_profit_pct:
                    return (f"TIME STOP ({self.time_stop_minutes}m at "
                            f"{gain_pct*100:+.2f}% - no edge realised)")
        except Exception:
            pass

        return None

    def _partial_close(self, pos, exit_price, lots_to_sell, reason):
        """Close a portion of the open position (tiered profit booking)."""
        entry_price = pos['entry_price']
        qty = lots_to_sell * self.lot_size
        pnl = (exit_price - entry_price) * qty - 40.0  # reduced brokerage for partial

        self.portfolio["capital"] += pnl

        exit_time = datetime.now(IST)
        record = {
            "entry_time": pos['entry_time'],
            "exit_time": exit_time.isoformat(),
            "trade_type": f"{pos['trade_type']} (partial {lots_to_sell}/{pos.get('total_lots',5)} lots)",
            "strike": pos['strike'],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "reason": reason
        }
        self.portfolio["trade_history"].append(record)
        self.save_portfolio()

        # Journal: record exit for these lots
        if self.journal is not None and self._journal_day_started:
            try:
                tactic_name = pos.get('tactic_name', 'oi_wall_mean_reversion')
                self.journal.on_exit(
                    tactic_name, exit_time, exit_price,
                    pnl, reason,
                    {k: pos.get(k) for k in ('entry_spot', 'entry_pcr', 'strike', 'opt_type')},
                )
            except Exception as e:
                logger.debug(f"Journal on_exit (partial) failed: {e}")

        # Telegram
        msg = (f"💰 PARTIAL EXIT ({reason})\n"
               f"{pos['strike']} {pos['opt_type']}: Rs.{entry_price:.2f} → Rs.{exit_price:.2f}\n"
               f"Lots sold: {lots_to_sell}/{pos.get('total_lots',5)} | P&L: Rs.{pnl:+,.0f}\n"
               f"Remaining: {pos.get('remaining_lots', 0) - lots_to_sell} lots")
        logger.info(msg)
        self.telegram.send_message(msg)

    def _reassess_market(self, now):
        """Called after SL cooldown expires. Checks S/R, OI, trend.
        Returns {"can_trade": bool, "reason": str}."""
        try:
            spot = self.fetcher.get_spot()
            sup = self.fetcher.get_support()
            res = self.fetcher.get_resistance()
            focus_pcr = self.fetcher.get_focus_pcr()
            oi = self.fetcher.get_oi_pattern() or {}

            if spot <= 0 or sup <= 0 or res <= 0:
                return {"can_trade": False, "reason": "stale data"}

            reasons = []

            # 1. Check: Are S/R zones wide enough to trade?
            zone_width_pct = (res - sup) / spot
            if zone_width_pct < 0.003:  # < 0.3% = too narrow
                reasons.append(f"zone too narrow ({zone_width_pct*100:.1f}%)")

            # 2. Check: Is OI giving clear direction?
            ce_chg = abs(float(oi.get("ce_oi_change", 0) or 0))
            pe_chg = abs(float(oi.get("pe_oi_change", 0) or 0))
            if ce_chg > 0 and pe_chg > 0:
                ratio = pe_chg / ce_chg
                if ratio >= 1.20:
                    reasons.append(f"OI bullish (PE/CE={ratio:.2f}x)")
                elif ratio <= 0.80:
                    reasons.append(f"OI bearish (PE/CE={ratio:.2f}x)")
                else:
                    reasons.append(f"OI neutral (PE/CE={ratio:.2f}x)")
            else:
                reasons.append("OI flows unclear")

            # 3. Check: Is PCR giving clear signal?
            if focus_pcr >= 1.05:
                reasons.append(f"PCR bullish ({focus_pcr:.2f})")
            elif focus_pcr <= 0.90:
                reasons.append(f"PCR bearish ({focus_pcr:.2f})")
            else:
                reasons.append(f"PCR neutral ({focus_pcr:.2f})")

            # 4. Check: Is spot near either wall? (opportunity exists)
            dist_sup = abs(spot - sup) / spot
            dist_res = abs(res - spot) / spot
            near_wall = (dist_sup <= 0.003 or dist_res <= 0.003)
            if not near_wall:
                reasons.append(f"spot mid-zone (S={sup}, R={res}, spot={spot:.0f})")

            # Decision: can trade if at least 2 positive signals and no blockers
            positive_signals = sum(1 for r in reasons if "bullish" in r.lower() or "bearish" in r.lower())
            has_blocker = any("unclear" in r.lower() or "neutral" in r.lower() or "narrow" in r.lower())

            if has_blocker and positive_signals < 1:
                return {"can_trade": False, "reason": "; ".join(reasons)}

            return {"can_trade": True, "reason": f"resume: {'; '.join(reasons)}"}
        except Exception as e:
            return {"can_trade": False, "reason": f"reassess error: {e}"}

    def _close_position(self, reason, exit_price=None, pos=None):
        if pos is None:
            # Legacy: close first open position
            positions = self.portfolio.get("open_positions", [])
            if not positions:
                return
            pos = positions[0]
        if exit_price is None:
            exit_price = self.fetcher.get_option_ltp(pos['strike'], pos['opt_type'])

        # Use remaining lots (after any partial exits)
        remaining = pos.get('remaining_lots', pos.get('total_lots', 5))
        qty = remaining * self.lot_size
        pnl = (exit_price - pos['entry_price']) * qty

        # Deduct Brokerage (Paper)
        pnl -= 60.0

        self.portfolio["capital"] += pnl

        exit_time = datetime.now(IST)
        record = {
            "entry_time": pos['entry_time'],
            "exit_time": exit_time.isoformat(),
            "trade_type": f"{pos['trade_type']} (final {remaining} lots)",
            "strike": pos['strike'],
            "entry_price": pos['entry_price'],
            "exit_price": exit_price,
            "pnl": pnl,
            "reason": reason
        }
        self.portfolio["trade_history"].append(record)

        # Journal: record exit
        if self.journal is not None and self._journal_day_started:
            try:
                self.journal.on_exit(
                    tactic=pos.get('tactic_name', ''),
                    exit_ts=exit_time,
                    exit_premium=exit_price,
                    exit_reason=reason,
                    net_pnl=pnl,
                )
            except Exception as e:
                logger.warning(f"Journal on_exit failed: {e}")

        # Telegram notification
        msg = (f"🏁 PAPER TRADE CLOSED\n"
               f"Type: Final Close for {pos['strike']} {pos['opt_type']}\n"
               f"Exit Reason: {reason}\n"
               f"Exit Price: Rs. {exit_price:.2f} (Entry: Rs. {pos['entry_price']:.2f})\n"
               f"Lots Closed: {remaining}/{pos.get('total_lots', 5)}\n"
               f"P&L (Remaining): Rs. {pnl:+,.2f}\n"
               f"New Capital: Rs. {self.portfolio['capital']:.2f}")
        logger.info(msg)
        self.telegram.send_message(msg)

        # PR 2: consecutive-loss tracking + regime self-diagnostic.
        if pnl < 0:
            self._consecutive_losses += 1
            if (self._consecutive_losses >= self.consecutive_loss_threshold
                    and self._regime_lock is None):
                try:
                    spot_hist = self.fetcher.get_spot_history()
                    # PriceActionBot has _history for compatibility
                    pcr_oi_hist = getattr(self.engine, '_history', deque())
                    reading = classify_regime(spot_hist, pcr_oi_hist)
                    self._regime_lock_reasons = list(reading.reasons)
                    if reading.is_chop:
                        self._regime_lock = "STOPPED"
                    elif reading.is_trending and reading.direction == "CE":
                        self._regime_lock = "CE_ONLY"
                    elif reading.is_trending and reading.direction == "PE":
                        self._regime_lock = "PE_ONLY"
                    diag = (f"[REGIME DIAGNOSTIC] After {self.consecutive_loss_threshold} "
                            f"losses: verdict={reading.verdict}, lock={self._regime_lock}\n"
                            + "\n".join(f"  - {r}" for r in reading.reasons))
                    logger.warning(diag)
                    self.telegram.send_message(diag)
                except Exception as e:
                    logger.warning(f"Regime classifier failed: {e}")
        else:
            self._consecutive_losses = 0

        # Remove this position from open_positions
        if pos in self.portfolio.get("open_positions", []):
            self.portfolio["open_positions"].remove(pos)
        self.save_portfolio()
        return  # avoid falling into the next method

    # ===================================================================
    # T2 Straddle/Strangle: parallel entry / monitor / close path.
    # Lives in self.portfolio["open_straddle"] (separate slot from
    # open_position so single-leg logic stays untouched).
    # ===================================================================

    def _open_straddle_position(self, signal: dict, now, spot: float) -> None:
        """Place both legs of a straddle or strangle simultaneously (paper-mode only).
        Reads premiums fresh from the fetcher so the price reflects
        the actual entry tick, not the dispatcher snapshot.
        """
        atm_strike = int(round(spot / self.strike_step) * self.strike_step)
        
        strike_offset = int(signal.get('strike_offset', 0))
        second_strike_offset = int(signal.get('second_strike_offset', 0))
        
        leg1_strike = atm_strike + (strike_offset * self.strike_step)
        leg2_strike = atm_strike + (second_strike_offset * self.strike_step)
        
        leg1_dir = signal.get('direction', 'CE')
        leg2_dir = signal.get('second_direction', 'PE')

        leg1_premium = self.fetcher.get_option_ltp(leg1_strike, leg1_dir)
        leg2_premium = self.fetcher.get_option_ltp(leg2_strike, leg2_dir)

        if leg1_premium <= 0 or leg2_premium <= 0:
            logger.info(
                f"Double-legged signal received but premiums unavailable "
                f"({leg1_dir}={leg1_premium}, {leg2_dir}={leg2_premium}). "
                f"Skipping."
            )
            return

        # Defensive premium bands (T2 backtest limits). We bypass or extend these limits for custom tactics.
        tactic_name = signal.get('tactic_name', 't2_expiry_straddle')
        is_t2 = ("t2" in tactic_name.lower())
        
        T2_MIN_PREM_PER_LEG = 5.0
        T2_MAX_PREM_PER_LEG = 20.0
        
        if is_t2:
            if not (T2_MIN_PREM_PER_LEG <= leg1_premium <= T2_MAX_PREM_PER_LEG):
                logger.info(
                    f"T2 entry blocked: {leg1_dir} premium {leg1_premium:.2f} "
                    f"drifted out of band [{T2_MIN_PREM_PER_LEG}, {T2_MAX_PREM_PER_LEG}] "
                    f"between gate and entry."
                )
                return
            if not (T2_MIN_PREM_PER_LEG <= leg2_premium <= T2_MAX_PREM_PER_LEG):
                logger.info(
                    f"T2 entry blocked: {leg2_dir} premium {leg2_premium:.2f} "
                    f"drifted out of band [{T2_MIN_PREM_PER_LEG}, {T2_MAX_PREM_PER_LEG}] "
                    f"between gate and entry."
                )
                return

        combined_entry = leg1_premium + leg2_premium
        combined_sl_pct = float(signal.get('combined_sl_pct') or 0.50)
        combined_tp_pct = signal.get('combined_tp_pct')   # may be None
        time_stop_min = int(signal.get('tactic_time_stop_min', 35))

        # Sizing: same Rs.20k/trade rule used by single-leg.
        cost_per_unit = combined_entry * self.lot_size
        if cost_per_unit <= 0:
            logger.warning("Double-legged: cost_per_unit is zero, cannot size.")
            return
        capital_per_trade = 20_000.0
        lots = max(1, int(capital_per_trade // cost_per_unit))
        qty_per_leg = lots * self.lot_size

        self._positions_today_count += 1

        sl_combined_price = combined_entry * (1 - combined_sl_pct)
        tp_combined_price = (combined_entry * (1 + combined_tp_pct)
                             if combined_tp_pct else None)

        self.portfolio["open_straddle"] = {
            "entry_time": now.isoformat(),
            "tactic_name": tactic_name,
            "atm_strike": atm_strike,
            "leg1_dir": leg1_dir,
            "leg1_strike": leg1_strike,
            "leg1_entry_price": leg1_premium,
            "leg2_dir": leg2_dir,
            "leg2_strike": leg2_strike,
            "leg2_entry_price": leg2_premium,
            "combined_entry": combined_entry,
            "combined_sl_price": sl_combined_price,
            "combined_tp_price": tp_combined_price,
            "lots": lots,
            "qty_per_leg": qty_per_leg,
            "is_expiry_day": True,
            "time_stop_min": time_stop_min,
            "spot_at_entry": spot,
        }
        self.save_portfolio()

        logger.info(
            f"[DOUBLE-LEGGED OPENED] {tactic_name} L1={leg1_strike}({leg1_dir})@{leg1_premium:.2f} "
            f"L2={leg2_strike}({leg2_dir})@{leg2_premium:.2f} "
            f"combined={combined_entry:.2f} lots={lots} (qty/leg={qty_per_leg}) "
            f"SL={sl_combined_price:.2f} TP={tp_combined_price}"
        )
        if self.telegram is not None:
            try:
                self.telegram.send_message(
                    f"[{tactic_name.upper()} OPENED]\n"
                    f"Leg 1: {leg1_strike} {leg1_dir} @ {leg1_premium:.2f}\n"
                    f"Leg 2: {leg2_strike} {leg2_dir} @ {leg2_premium:.2f}\n"
                    f"Combined Entry: {combined_entry:.2f}\n"
                    f"Lots: {lots} (Qty/Leg: {qty_per_leg})\n"
                    f"Combined SL: {sl_combined_price:.2f}\n"
                    f"Combined TP: {tp_combined_price}"
                )
            except Exception:
                pass

    def _monitor_straddle(self, now) -> None:
        """Tick combined-premium against SL/TP/time-stop. Force-close at
        15:25 in any case (handled here so we don't need to wait for
        MARKET_CLOSE at 15:30). Also caches last-good LTPs so close-time
        feed glitches don't silently zero out P&L.
        """
        sd = self.portfolio.get("open_straddle")
        if not sd:
            return

        # Force close at 15:25 regardless (T2 spec)
        FORCE_CLOSE = dtime(15, 25)
        if now.time() >= FORCE_CLOSE:
            self._close_straddle("EOD Force Close (15:25)")
            return

        # Fetch live premiums for both legs
        ce_dir = sd["leg1_dir"]; pe_dir = sd["leg2_dir"]
        leg1_strike = sd.get("leg1_strike", sd["atm_strike"])
        leg2_strike = sd.get("leg2_strike", sd["atm_strike"])
        leg1_now = self.fetcher.get_option_ltp(leg1_strike, ce_dir)
        leg2_now = self.fetcher.get_option_ltp(leg2_strike, pe_dir)
        if leg1_now <= 0 or leg2_now <= 0:
            return  # stale tick, skip — but DON'T overwrite last_good cache

        # Cache last-good LTPs (used by _close_straddle if final-tick feed fails)
        sd["leg1_last_good"] = leg1_now
        sd["leg2_last_good"] = leg2_now
        sd["last_good_ts"] = now.isoformat()
        # Save through is cheap and keeps the cache durable across restarts.
        self.save_portfolio()

        combined_now = leg1_now + leg2_now

        # Combined SL
        if combined_now <= sd["combined_sl_price"]:
            self._close_straddle(
                f"Combined SL hit ({combined_now:.2f} <= {sd['combined_sl_price']:.2f})"
            )
            return
        # Combined TP (if set)
        if sd["combined_tp_price"] and combined_now >= sd["combined_tp_price"]:
            self._close_straddle(
                f"Combined TP hit ({combined_now:.2f} >= {sd['combined_tp_price']:.2f})"
            )
            return

        # Time-stop
        entry_t = datetime.fromisoformat(sd["entry_time"])
        held_min = (now - entry_t).total_seconds() / 60
        if held_min >= sd["time_stop_min"]:
            self._close_straddle(f"Time-stop ({held_min:.0f}min)")
            return

    def _close_straddle(self, reason: str) -> None:
        sd = self.portfolio.get("open_straddle")
        if not sd:
            return

        ce_dir = sd["leg1_dir"]; pe_dir = sd["leg2_dir"]
        leg1_strike = sd.get("leg1_strike", sd["atm_strike"])
        leg2_strike = sd.get("leg2_strike", sd["atm_strike"])
        qty = sd["qty_per_leg"]

        leg1_exit = self.fetcher.get_option_ltp(leg1_strike, ce_dir)
        leg2_exit = self.fetcher.get_option_ltp(leg2_strike, pe_dir)

        # Fallback chain when fresh fetch returns 0/missing:
        #   1. Use last-good LTP cached during _monitor_straddle ticks
        #      (always valid in normal operation — monitor runs every 3s)
        #   2. Last resort: use 1% of entry price as a conservative LOSS
        #      estimate (NEVER use entry_price as fallback — that hides
        #      losses on close-tick feed glitches).
        # Always log loud when fallback fires so the trade gets reviewed.
        if leg1_exit <= 0:
            cached = sd.get("leg1_last_good")
            if cached and cached > 0:
                logger.warning(
                    f"[T2 CLOSE FALLBACK] {ce_dir} fresh LTP=0, using "
                    f"last-good={cached:.2f} (cached at {sd.get('last_good_ts')})"
                )
                leg1_exit = float(cached)
            else:
                logger.error(
                    f"[T2 CLOSE FALLBACK] {ce_dir} fresh LTP=0 AND no cache. "
                    f"Marking as 1% of entry (worst-case LOSS estimate). "
                    f"REVIEW THIS TRADE MANUALLY."
                )
                leg1_exit = sd["leg1_entry_price"] * 0.01
        if leg2_exit <= 0:
            cached = sd.get("leg2_last_good")
            if cached and cached > 0:
                logger.warning(
                    f"[T2 CLOSE FALLBACK] {pe_dir} fresh LTP=0, using "
                    f"last-good={cached:.2f} (cached at {sd.get('last_good_ts')})"
                )
                leg2_exit = float(cached)
            else:
                logger.error(
                    f"[T2 CLOSE FALLBACK] {pe_dir} fresh LTP=0 AND no cache. "
                    f"Marking as 1% of entry (worst-case LOSS estimate). "
                    f"REVIEW THIS TRADE MANUALLY."
                )
                leg2_exit = sd["leg2_entry_price"] * 0.01

        leg1_pnl = (leg1_exit - sd["leg1_entry_price"]) * qty
        leg2_pnl = (leg2_exit - sd["leg2_entry_price"]) * qty
        gross_pnl = leg1_pnl + leg2_pnl
        # Brokerage: 2 legs round-trip = 4 orders. Match backtest: Rs.120 paper.
        net_pnl = gross_pnl - 120.0
        self.portfolio["capital"] += net_pnl

        exit_time = datetime.now(IST)
        # Append two trade_history entries (one per leg) for compatibility
        # with the dashboard TradesTable + downstream P&L calc.
        for leg_dir, leg_entry, leg_exit, leg_pnl, leg_strike in (
            (ce_dir, sd["leg1_entry_price"], leg1_exit, leg1_pnl, leg1_strike),
            (pe_dir, sd["leg2_entry_price"], leg2_exit, leg2_pnl, leg2_strike),
        ):
            self.portfolio["trade_history"].append({
                "entry_time": sd["entry_time"],
                "exit_time": exit_time.isoformat(),
                "trade_type": f"BUY {leg_dir} (T2 leg)",
                "strike": leg_strike,
                "entry_price": leg_entry,
                "exit_price": leg_exit,
                "pnl": leg_pnl - 60.0,   # half the brokerage allocated per leg
                "reason": reason,
            })

        logger.info(
            f"[T2 STRADDLE CLOSED] reason={reason} "
            f"{ce_dir}: {sd['leg1_entry_price']:.2f}->{leg1_exit:.2f} "
            f"{pe_dir}: {sd['leg2_entry_price']:.2f}->{leg2_exit:.2f} "
            f"net_pnl=Rs.{net_pnl:.2f}"
        )
        if self.telegram is not None:
            try:
                tag = "[WIN]" if net_pnl > 0 else "[LOSS]"
                self.telegram.send_message(
                    f"{tag} T2 STRADDLE CLOSED\n"
                    f"Reason: {reason}\n"
                    f"Net P&L: Rs.{net_pnl:,.2f}"
                )
            except Exception:
                pass

        self.portfolio["open_straddle"] = None
        self.save_portfolio()

        # PR 2: consecutive-loss tracking + regime self-diagnostic.
        # Wins reset the counter; losses tick it up. Once we hit the
        # threshold (default 2), classify the market and apply a lock
        # for the rest of the day instead of blindly pausing.
        if pnl < 0:
            self._consecutive_losses += 1
            if (self._consecutive_losses >= self.consecutive_loss_threshold
                    and self._regime_lock is None):
                try:
                    spot_hist = self.fetcher.get_spot_history()
                    pcr_oi_hist = self.engine._history
                    reading = classify_regime(spot_hist, pcr_oi_hist)
                    self._regime_lock_reasons = list(reading.reasons)
                    if reading.is_chop:
                        self._regime_lock = "STOPPED"
                    elif reading.is_trending and reading.direction == "CE":
                        self._regime_lock = "CE_ONLY"
                    elif reading.is_trending and reading.direction == "PE":
                        self._regime_lock = "PE_ONLY"
                    else:
                        # UNCLEAR — leave lock unset, will re-check on next loss
                        self._consecutive_losses = 0  # give one more chance
                    diag = (f"[REGIME DIAGNOSTIC] After {self.consecutive_loss_threshold} "
                            f"losses: verdict={reading.verdict}, lock={self._regime_lock}\n"
                            + "\n".join(f"  - {r}" for r in reading.reasons))
                    logger.warning(diag)
                    self.telegram.send_message(diag)
                except Exception as e:
                    logger.warning(f"Regime classifier failed: {e}")
        else:
            self._consecutive_losses = 0

        msg = (f"🏁 PAPER TRADE CLOSED\n"
               f"Reason: {reason}\n"
               f"Exit Price: Rs. {exit_price:.2f}\n"
               f"P&L: Rs. {pnl:.2f}\n"
               f"New Capital: Rs. {self.portfolio['capital']:.2f}")
        logger.info(msg)
        self.telegram.send_message(msg)

    # ===================================================================
    # Seller bot: Iron Condor entry / monitor / close (added 2026-05-17).
    # Standalone flow — runs only when config.seller_mode == True. Skips
    # all single-leg and straddle paths. Uses the regime classifier (via
    # self.dispatcher.classifier._current) for entry gating but does NOT
    # use the dispatcher's tactic routing — seller bot has exactly one
    # tactic (Iron Condor) for v1.
    # ===================================================================

    def _scan_for_iron_condor_entry(self, now: datetime) -> None:
        """Look for Iron Condor entry conditions:
          1. Regime classifier verdict is RANGE (or CHOP, configurable)
          2. Inside the seller entry window (default 10:30-13:30 IST)
          3. ATM CE+PE premiums available for the candidate strikes
          4. Net credit >= configured minimum
        """
        strat = self.config.get("seller_strategy", {})
        gates = self.config.get("seller_entry_gates", {})

        # Window check
        t_now = now.time()
        no_before = dtime.fromisoformat(strat.get("no_entry_before", "10:30:00"))
        no_after = dtime.fromisoformat(strat.get("no_entry_after", "13:30:00"))
        if t_now < no_before or t_now >= no_after:
            return

        # Regime check — needs the classifier to evaluate the latest tick
        required_regime = gates.get("require_regime", "RANGE")
        cur_regime = None
        try:
            cur_regime = self.dispatcher.update_and_get_regime(now, self.fetcher)
        except Exception as e:
            logger.debug(f"[SELLER] Regime classification failed: {e}")
        # Accept both RANGE and CHOP as eligible for IC (low directional bias)
        eligible_regimes = {required_regime, "CHOP"}
        if cur_regime not in eligible_regimes:
            logger.info(
                f"[SELLER] Skip entry: regime={cur_regime} not in "
                f"{eligible_regimes}. Spot={self.fetcher.get_spot():.1f}"
            )
            return

        # VIX gate — added 2026-05-17 after proxy backtest showed High-VIX
        # days (VIX > 16) had a 52% breach rate vs 21% Normal / 1% Low.
        # The asymmetric payoff means even a 50% breach rate destroys edge.
        max_vix = float(gates.get("max_vix_for_entry", 16.0))
        try:
            cur_vix = float(self.fetcher.get_india_vix() or 0)
        except Exception:
            cur_vix = 0.0
        if cur_vix > 0 and cur_vix > max_vix:
            logger.info(
                f"[SELLER] Skip entry: VIX={cur_vix:.2f} above max {max_vix:.2f}. "
                f"High-VIX environment = high breach risk for IC."
            )
            return

        spot = self.fetcher.get_spot()
        if spot <= 0:
            return

        # Strike selection — short strikes at +/- distance from ATM,
        # long strikes wing_width further out.
        atm_strike = int(round(spot / self.strike_step) * self.strike_step)
        wing_width = int(strat.get("wing_width_pts", 100))
        short_dist = int(strat.get("short_strike_distance_pts", 200))

        ce_short_strike = atm_strike + short_dist
        ce_long_strike = ce_short_strike + wing_width
        pe_short_strike = atm_strike - short_dist
        pe_long_strike = pe_short_strike - wing_width

        # Pull live LTPs for all four legs
        ce_short_prem = self.fetcher.get_option_ltp(ce_short_strike, "CE")
        ce_long_prem = self.fetcher.get_option_ltp(ce_long_strike, "CE")
        pe_short_prem = self.fetcher.get_option_ltp(pe_short_strike, "PE")
        pe_long_prem = self.fetcher.get_option_ltp(pe_long_strike, "PE")

        if min(ce_short_prem, ce_long_prem, pe_short_prem, pe_long_prem) <= 0:
            logger.info(
                f"[SELLER] Skip entry: premium unavailable for one or more legs "
                f"(CE {ce_short_strike}={ce_short_prem}, {ce_long_strike}={ce_long_prem}, "
                f"PE {pe_short_strike}={pe_short_prem}, {pe_long_strike}={pe_long_prem})"
            )
            return

        # Net credit per lot (premium collected from shorts minus premium paid for longs)
        net_credit = (ce_short_prem - ce_long_prem) + (pe_short_prem - pe_long_prem)
        min_credit = float(strat.get("min_net_credit_per_lot", 15.0))
        if net_credit < min_credit:
            logger.info(
                f"[SELLER] Skip entry: net credit Rs.{net_credit:.2f} below min "
                f"Rs.{min_credit}. CE side={ce_short_prem - ce_long_prem:.2f}, "
                f"PE side={pe_short_prem - pe_long_prem:.2f}"
            )
            return

        # All checks pass — open the position
        self._open_iron_condor_position(
            now=now,
            spot=spot,
            atm_strike=atm_strike,
            wing_width=wing_width,
            ce_short_strike=ce_short_strike, ce_short_entry=ce_short_prem,
            ce_long_strike=ce_long_strike, ce_long_entry=ce_long_prem,
            pe_short_strike=pe_short_strike, pe_short_entry=pe_short_prem,
            pe_long_strike=pe_long_strike, pe_long_entry=pe_long_prem,
            net_credit=net_credit,
            regime=cur_regime,
        )

    def _open_iron_condor_position(self, now, spot, atm_strike, wing_width,
                                    ce_short_strike, ce_short_entry,
                                    ce_long_strike, ce_long_entry,
                                    pe_short_strike, pe_short_entry,
                                    pe_long_strike, pe_long_entry,
                                    net_credit, regime) -> None:
        """Open a 4-leg Iron Condor position. Paper-mode only for v1."""
        strat = self.config.get("seller_strategy", {})
        risk = self.config.get("risk", {})
        lots = int(risk.get("max_lots_per_trade", 1))
        qty_per_leg = lots * self.lot_size

        # Max loss per lot = wing_width - net_credit (defined-risk property of IC)
        max_loss = wing_width - net_credit
        max_profit = net_credit
        profit_target_pct = float(strat.get("profit_target_pct", 0.50))
        sl_multiplier = float(strat.get("stop_loss_multiplier", 1.5))
        # The "combined" we track is total close-cost. At entry, close-cost = net_credit.
        # We profit when close_cost shrinks below net_credit. Hit profit target when
        # close_cost <= net_credit * (1 - profit_target_pct).
        # We stop out when close_cost >= net_credit * (1 + sl_multiplier).
        profit_target_close_cost = net_credit * (1 - profit_target_pct)
        stop_loss_close_cost = net_credit * (1 + sl_multiplier)

        self.portfolio["open_iron_condor"] = {
            "entry_time": now.isoformat(),
            "tactic_name": strat.get("tactic", "iron_condor_v1"),
            "spot_at_entry": float(spot),
            "atm_strike": int(atm_strike),
            "wing_width": int(wing_width),
            "regime_at_entry": regime,
            # Four legs
            "ce_short_strike": int(ce_short_strike),
            "ce_short_entry": float(ce_short_entry),
            "ce_long_strike": int(ce_long_strike),
            "ce_long_entry": float(ce_long_entry),
            "pe_short_strike": int(pe_short_strike),
            "pe_short_entry": float(pe_short_entry),
            "pe_long_strike": int(pe_long_strike),
            "pe_long_entry": float(pe_long_entry),
            # Per-lot economics
            "net_credit": float(net_credit),
            "max_loss": float(max_loss),
            "max_profit": float(max_profit),
            "profit_target_close_cost": float(profit_target_close_cost),
            "stop_loss_close_cost": float(stop_loss_close_cost),
            # Sizing
            "lots": int(lots),
            "qty_per_leg": int(qty_per_leg),
            # Force-close time
            "force_close_time": strat.get("force_close_time", "15:15:00"),
        }
        self.save_portfolio()

        logger.info(
            f"[IC OPENED] regime={regime} spot={spot:.1f} ATM={atm_strike} "
            f"wing={wing_width}\n"
            f"  CE: short {ce_short_strike}@{ce_short_entry:.2f} / "
            f"long {ce_long_strike}@{ce_long_entry:.2f}\n"
            f"  PE: short {pe_short_strike}@{pe_short_entry:.2f} / "
            f"long {pe_long_strike}@{pe_long_entry:.2f}\n"
            f"  Net credit: Rs.{net_credit:.2f}/lot (lots={lots}, qty/leg={qty_per_leg})\n"
            f"  Max profit: Rs.{max_profit:.2f}/lot | Max loss: Rs.{max_loss:.2f}/lot\n"
            f"  Exits: TP at close-cost <= Rs.{profit_target_close_cost:.2f} "
            f"({profit_target_pct*100:.0f}% of credit); "
            f"SL at close-cost >= Rs.{stop_loss_close_cost:.2f} "
            f"({sl_multiplier:.1f}x credit)"
        )
        if self.telegram is not None:
            try:
                self.telegram.send_message(
                    f"[IC OPENED]\n"
                    f"Regime: {regime}, Spot: {spot:.1f}\n"
                    f"Range: {pe_long_strike}-{ce_long_strike}\n"
                    f"Net credit: Rs.{net_credit:.2f}/lot ({lots} lots)\n"
                    f"Max profit: Rs.{max_profit*qty_per_leg:.0f} | "
                    f"Max loss: Rs.{max_loss*qty_per_leg:.0f}"
                )
            except Exception:
                pass

    def _monitor_iron_condor(self, now: datetime) -> None:
        """Per-tick check on open IC. Closes on TP / SL / force-close time
        / regime flip to TREND."""
        ic = self.portfolio.get("open_iron_condor")
        if not ic:
            return

        # Force close at end-of-day window
        force_close_time = dtime.fromisoformat(ic.get("force_close_time", "15:15:00"))
        if now.time() >= force_close_time:
            self._close_iron_condor(f"EOD Force Close ({force_close_time})")
            return

        # Pull current premiums for all four legs
        ce_short_now = self.fetcher.get_option_ltp(ic["ce_short_strike"], "CE")
        ce_long_now = self.fetcher.get_option_ltp(ic["ce_long_strike"], "CE")
        pe_short_now = self.fetcher.get_option_ltp(ic["pe_short_strike"], "PE")
        pe_long_now = self.fetcher.get_option_ltp(ic["pe_long_strike"], "PE")
        if min(ce_short_now, ce_long_now, pe_short_now, pe_long_now) <= 0:
            return  # stale data, skip

        # Close-cost is what we'd pay to unwind the position right now.
        # = (buy back CE short) - (sell CE long) + (buy back PE short) - (sell PE long)
        close_cost = (ce_short_now - ce_long_now) + (pe_short_now - pe_long_now)

        # Profit target — close-cost shrunk to <= profit_target threshold
        if close_cost <= ic["profit_target_close_cost"]:
            self._close_iron_condor(
                f"PROFIT TARGET (close-cost {close_cost:.2f} <= "
                f"{ic['profit_target_close_cost']:.2f})",
                close_premiums=(ce_short_now, ce_long_now, pe_short_now, pe_long_now),
            )
            return

        # Stop loss — close-cost ballooned past threshold
        if close_cost >= ic["stop_loss_close_cost"]:
            self._close_iron_condor(
                f"STOP LOSS (close-cost {close_cost:.2f} >= "
                f"{ic['stop_loss_close_cost']:.2f})",
                close_premiums=(ce_short_now, ce_long_now, pe_short_now, pe_long_now),
            )
            return

        # Regime flip exit — if classifier moves to a TREND regime, the
        # range thesis is broken; bail out.
        try:
            cur_regime_obj = self.dispatcher.classifier._current
            cur_regime = cur_regime_obj.value if cur_regime_obj else None
            if cur_regime in ("TREND_UP", "TREND_DOWN",
                              "TREND_UP_GAP", "TREND_DOWN_GAP"):
                self._close_iron_condor(
                    f"REGIME FLIP to {cur_regime} (range thesis broken)",
                    close_premiums=(ce_short_now, ce_long_now,
                                    pe_short_now, pe_long_now),
                )
                return
        except Exception:
            pass

        # Periodic status log (every minute on the round-second)
        if now.second < 4:
            pnl_per_lot = ic["net_credit"] - close_cost
            pnl_total = pnl_per_lot * ic["qty_per_leg"]
            logger.info(
                f"[IC HOLDING] close_cost=Rs.{close_cost:.2f} "
                f"(entry credit Rs.{ic['net_credit']:.2f}) → "
                f"P&L Rs.{pnl_total:+.0f} | Spot {self.fetcher.get_spot():.1f}"
            )

    def _close_iron_condor(self, reason: str,
                            close_premiums=None) -> None:
        """Close all 4 legs of the IC. close_premiums is an optional
        (ce_short, ce_long, pe_short, pe_long) tuple; if not supplied,
        re-fetches them.
        """
        ic = self.portfolio.get("open_iron_condor")
        if not ic:
            return

        if close_premiums is None:
            ce_short_now = self.fetcher.get_option_ltp(ic["ce_short_strike"], "CE") \
                or ic["ce_short_entry"]
            ce_long_now = self.fetcher.get_option_ltp(ic["ce_long_strike"], "CE") \
                or ic["ce_long_entry"]
            pe_short_now = self.fetcher.get_option_ltp(ic["pe_short_strike"], "PE") \
                or ic["pe_short_entry"]
            pe_long_now = self.fetcher.get_option_ltp(ic["pe_long_strike"], "PE") \
                or ic["pe_long_entry"]
        else:
            ce_short_now, ce_long_now, pe_short_now, pe_long_now = close_premiums

        qty = ic["qty_per_leg"]
        # Per-leg P&L:
        #   Short legs: P&L = (entry - now) * qty   (profit when premium drops)
        #   Long legs:  P&L = (now - entry) * qty   (profit when premium rises)
        ce_short_pnl = (ic["ce_short_entry"] - ce_short_now) * qty
        ce_long_pnl = (ce_long_now - ic["ce_long_entry"]) * qty
        pe_short_pnl = (ic["pe_short_entry"] - pe_short_now) * qty
        pe_long_pnl = (pe_long_now - ic["pe_long_entry"]) * qty
        gross_pnl = ce_short_pnl + ce_long_pnl + pe_short_pnl + pe_long_pnl
        # Brokerage: 4 legs round-trip = 8 orders. Match buyer paper rate
        # Rs.30/leg from config.slippage.brokerage_per_leg.
        brokerage = float(
            self.config.get("slippage", {}).get("brokerage_per_leg", 30)
        ) * 8
        net_pnl = gross_pnl - brokerage
        self.portfolio["capital"] += net_pnl

        exit_time = datetime.now(IST)
        # Record each leg as a trade_history entry for compatibility with
        # the dashboard TradesTable + downstream P&L calculations.
        for leg_name, strike, entry_prem, exit_prem, leg_pnl in (
            ("SELL CE (IC short)", ic["ce_short_strike"], ic["ce_short_entry"],
             ce_short_now, ce_short_pnl),
            ("BUY CE (IC long)",   ic["ce_long_strike"], ic["ce_long_entry"],
             ce_long_now, ce_long_pnl),
            ("SELL PE (IC short)", ic["pe_short_strike"], ic["pe_short_entry"],
             pe_short_now, pe_short_pnl),
            ("BUY PE (IC long)",   ic["pe_long_strike"], ic["pe_long_entry"],
             pe_long_now, pe_long_pnl),
        ):
            self.portfolio["trade_history"].append({
                "entry_time": ic["entry_time"],
                "exit_time": exit_time.isoformat(),
                "trade_type": leg_name,
                "strike": strike,
                "entry_price": entry_prem,
                "exit_price": exit_prem,
                "pnl": leg_pnl - (brokerage / 4),  # Allocate brokerage evenly
                "reason": reason,
            })

        logger.info(
            f"[IC CLOSED] reason={reason}\n"
            f"  CE short {ic['ce_short_strike']}: "
            f"{ic['ce_short_entry']:.2f} → {ce_short_now:.2f}  P&L Rs.{ce_short_pnl:+,.0f}\n"
            f"  CE long  {ic['ce_long_strike']}: "
            f"{ic['ce_long_entry']:.2f} → {ce_long_now:.2f}  P&L Rs.{ce_long_pnl:+,.0f}\n"
            f"  PE short {ic['pe_short_strike']}: "
            f"{ic['pe_short_entry']:.2f} → {pe_short_now:.2f}  P&L Rs.{pe_short_pnl:+,.0f}\n"
            f"  PE long  {ic['pe_long_strike']}: "
            f"{ic['pe_long_entry']:.2f} → {pe_long_now:.2f}  P&L Rs.{pe_long_pnl:+,.0f}\n"
            f"  Gross P&L Rs.{gross_pnl:+,.0f} − brokerage Rs.{brokerage:.0f} = "
            f"Net Rs.{net_pnl:+,.0f}"
        )
        if self.telegram is not None:
            try:
                tag = "[WIN]" if net_pnl > 0 else "[LOSS]"
                self.telegram.send_message(
                    f"{tag} IRON CONDOR CLOSED\n"
                    f"Reason: {reason}\n"
                    f"Net P&L: Rs.{net_pnl:+,.0f}"
                )
            except Exception:
                pass

        self.portfolio["open_iron_condor"] = None
        self.save_portfolio()

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

            # Get 3TF Trends
            h4_trend, h1_trend, m15_trend = "DOWN", "DOWN", "DOWN"
            h4_ema, h1_ema, m15_ema = 0.0, 0.0, 0.0
            if hasattr(self.engine, "tracker_3tf"):
                try:
                    h4_trend, h1_trend, m15_trend = self.engine.tracker_3tf.get_trends(spot)
                    h4_ema = float(self.engine.tracker_3tf.h4_ema[-1]) if self.engine.tracker_3tf.h4_ema else 0.0
                    h1_ema = float(self.engine.tracker_3tf.h1_ema[-1]) if self.engine.tracker_3tf.h1_ema else 0.0
                    m15_ema = float(self.engine.tracker_3tf.m15_ema[-1]) if self.engine.tracker_3tf.m15_ema else 0.0
                except Exception:
                    pass

            open_positions = self.portfolio.get("open_positions", [])
            in_position = len(open_positions) > 0

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
                "open_positions": open_positions,
                "position_count": len(open_positions),
                "last_signal": self._last_signal,
                "missed_today_count": n_missed,
                "is_market_open": is_market_open,
                "journal_day_started": self._journal_day_started,
                # 2026-05-17 new signals
                "trend_confidence": float(snap.get("trend_confidence", 0) or 0),
                "trend_direction": snap.get("trend_direction", "FLAT"),
                "rejection_at_high": int(snap.get("rejection_at_high", 0) or 0),
                "rejection_at_low": int(snap.get("rejection_at_low", 0) or 0),
                # 3TF Metrics
                "h4_trend": h4_trend,
                "h1_trend": h1_trend,
                "m15_trend": m15_trend,
                "h4_ema": h4_ema,
                "h1_ema": h1_ema,
                "m15_ema": m15_ema,
                "enable_3tf_filters": self.config.get("enable_3tf_filters", self.opts.get("enable_3tf_filters", False)),
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
            path = write_daily_report(day_record, out_dir, index_name=self.config.get("trading_index", "NIFTY"))
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
