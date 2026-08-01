"""
oi_flow_engine.py — OI Flow Engine v6
======================================
Matches backtest v6 logic exactly (Rs. +93,912 across 39 days).

CORE THEORY:
- PE OI BUILDING  = put writers selling  = BULLISH fuel = CE entry
- CE OI BUILDING  = call writers selling = BEARISH fuel = PE entry
- DUAL CONFIRMATION (PE+ & CE- or CE+ & PE-) = strongest signal

REGIME DETECTION (at 10:30):
- Morning range >= 80 pts = TRENDING → enter immediately at 10:30
- Morning range < 80 pts   = SIDEWAYS → wait for breakout/breakdown

REVERSAL (12:00-14:00):
- Single 30-min window with dual confirmation = enter reversal

EXITS:
- Trade 1: 12:20 (or SL)
- Trade 2: 15:25 (or SL) — 5 min before market close
- Max 2 trades/day
"""

import json
import logging
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path
from typing import Optional

from premium_analyzer import PremiumAnalyzer
from option_seller_bot import OptionSellerBot

log = logging.getLogger("OIFlow")


def _load_opts():
    p = Path(__file__).parent / "Options.json"
    if p.exists():
        return json.loads(p.read_text()).get("configurableParameters", {})
    return {}


OPTS = _load_opts()


class OIFlowEngine:
    """Exact match to backtest v6 logic."""

    def __init__(self, config: dict):
        self.config = config
        strat = config.get("strategy", {})

        # ── Fixed strikes (locked at market open) ──
        self.ce_fixed_strikes: list = []
        self.pe_fixed_strikes: list = []
        self._strikes_locked: bool = False
        self.open_spot: Optional[float] = None

        # ── Morning range tracking ──
        self.morning_high: float = 0.0
        self.morning_low: float = float('inf')
        self.morning_range: float = 0.0
        self._regime_decided: bool = False
        self.regime: str = "pending"  # "trending", "sideways", "pending"
        self.oi_bias: str = "neutral"  # "bullish", "bearish", "neutral"
        self._breakout_fired: bool = False
        self._breakout_confirmed_dir: Optional[str] = None
        self._breakout_price: Optional[float] = None
        self.gann = None

        # ── Configuration Parameters ──
        self.regime_time = config.get("entry_time", "10:30")
        self.snap2_time = config.get("snap2_time", "10:00")
        self.snap1_time = config.get("snap1_time", "09:30")

        # ── OI snapshots at specific times ──
        self.oi_at_snap1: Optional[tuple] = None
        self.oi_at_snap2: Optional[tuple] = None
        self.oi_at_entry: Optional[tuple] = None
        self._oi_afternoon: deque = deque(maxlen=20)  # (ts, pe, ce) tuples

        # ── Position state ──
        self.position: Optional[dict] = None  # Current active Option Buyer trade
        self.ic_position: Optional[dict] = None # Current active Iron Condor trade
        self.ic_fired_today: bool = False # Prevent multiple ICs per day
        self.direction: Optional[str] = None
        self.locked_strikes: list = []
        self.entry_time: Optional[datetime] = None
        self.entry_premiums: dict = {}
        self._entry_sl_prices: dict = {}  # strike -> SL price

        # ── Session state ──
        self.trades_today: int = 0
        self.losses_today: int = 0
        self.daily_pnl_mtm: float = 0.0
        self.consecutive_losses: int = 0
        self.last_exit_time: Optional[datetime] = None

        # ── Risk params ──
        self.max_trades = int(OPTS.get("oi_flow_max_trades_per_day", 2))
        self.max_daily_loss = float(OPTS.get("oi_flow_max_daily_loss", 15000))
        self.max_hold_minutes = int(OPTS.get("oi_flow_max_hold_minutes", 30))
        self.trailing_sl_pct = config.get("trailing_sl_pct", 0.25)
        
        trading_idx = config.get("trading_index", "NIFTY")
        if trading_idx == "SENSEX":
            self.lot_size = int(strat.get("sensex_lot_size", 20))
            self.strike_step = int(strat.get("sensex_strike_step", 100))
        else:
            self.lot_size = int(strat.get("nifty_lot_size", 65))
            self.strike_step = int(strat.get("nifty_strike_step", 50))
            
        self.signal_threshold = int(OPTS.get("oi_flow_signal_threshold", 500000))
        self.min_range = int(OPTS.get("oi_flow_min_range", 80))
        self.use_structural_sl = config.get("use_structural_sl", True)

        # ── EMA params ──
        self.ema_fast = int(OPTS.get("oi_flow_ema_fast", 9))
        self.ema_slow = int(OPTS.get("oi_flow_ema_slow", 21))
        self.ema_macro = 200
        self._ema_fast_val: float = 0.0
        self._ema_slow_val: float = 0.0
        self._ema_macro_val: float = 0.0
        self._last_ema_ts: Optional[datetime] = None

        # ── Premium EMA tracking ──
        self.prem_ema_fast_period = int(config.get("prem_ema_fast", 5))
        self.prem_ema_slow_period = int(config.get("prem_ema_slow", 9))
        self._prem_ema_fast: dict = {}  # strike_key -> ema value
        self._prem_ema_slow: dict = {}  # strike_key -> ema value
        self._last_prem_ema_ts: Optional[datetime] = None

        # ── Theta Decay / Volatility Compression Filter ──
        self._theta_lockdown_until: Optional[datetime] = None
        self._recent_spots = deque(maxlen=15)
        self._recent_ces = deque(maxlen=15)
        self._recent_pes = deque(maxlen=15)

        # ── Option Seller Bot (Theta Harvester) ──
        self.seller_bot = OptionSellerBot(config)
        self._last_theta_ts: Optional[datetime] = None

        self.fetcher = None
        self.last_trade_peak_spot: float = 0.0
        self.last_trade_trough_spot: float = float('inf')

        # ── Dynamic Gann levels ──
        self.ce_trigger: float = 0.0
        self.ce_target: float = 0.0
        self.ce_sl: float = 0.0
        self.pe_trigger: float = 0.0
        self.pe_sl: float = 0.0

        # ── Tape-Reading Engine (Pillars 1-4) ──
        self.analyzer = PremiumAnalyzer(index=config.get("trading_index", "NIFTY"))
        self._cached_spot_levels = None
        self._cached_spot_ts = None

        # ── Daily Bias & Reversal Flip variables ──
        self.daily_bias: Optional[str] = None
        self.bias_desc: str = ""
        self.flip_triggered: bool = False
        self.max_spot_in_trade: float = 0.0
        self.min_spot_in_trade: float = float('inf')
        self.reversal_exit_spot: Optional[float] = None

        log.info(f"OIFlowEngine v6 ready | Thresh={self.signal_threshold} | "
                 f"MinRange={self.min_range} | MaxTrades={self.max_trades}")

    # ═══════════════════════════════════════════════════════════════
    # FIXED STRIKES
    # ═══════════════════════════════════════════════════════════════

    def lock_strikes(self, spot: float):
        if self._strikes_locked:
            return
        atm = round(spot / self.strike_step) * self.strike_step
        step = self.strike_step
        self.ce_fixed_strikes = [atm - 3 * step, atm - 2 * step, atm - step]
        self.pe_fixed_strikes = [atm + step, atm + 2 * step, atm + 3 * step]
        self._strikes_locked = True
        self.open_spot = spot
        self.morning_low = spot  # reset for morning tracking
        
        # Initialize Daily Bias based on Gap vs Previous Close
        if self.gann and hasattr(self.gann, 'base_price') and self.gann.base_price > 0:
            if spot > self.gann.base_price:
                self.daily_bias = "CE"
                self.bias_desc = "BULLISH (Gap Up)"
            else:
                self.daily_bias = "PE"
                self.bias_desc = "BEARISH (Gap Down)"
            log.info(f"[BIAS] Official Prev Close: {self.gann.base_price:.2f} | Open: {spot:.2f} | Bias: {self.bias_desc}")
        else:
            self.daily_bias = "CE" # Fallback if base_price not loaded
        
        # Lock regime as trending for pure Gann level breakouts from 09:20 AM
        self.regime = "trending"
        self._regime_decided = True
        
        # Map dynamic triggers relative to opening spot
        if self.gann:
            active = self.gann.get_active_levels(spot)
            self.ce_trigger = active["ce_trigger"]
            self.ce_target = active["ce_target"]
            self.ce_sl = active["ce_sl"]
            self.pe_trigger = active["pe_trigger"]
            self.pe_target = active["pe_target"]
            self.pe_sl = active["pe_sl"]
            log.info(f"[GANN] Dynamic levels mapped: "
                     f"CE_Trigger={self.ce_trigger:.2f}, Target={self.ce_target:.2f}, SL={self.ce_sl:.2f} | "
                     f"PE_Trigger={self.pe_trigger:.2f}, Target={self.pe_target:.2f}, SL={self.pe_sl:.2f}")

        log.info(f"[OI-Flow] Strikes locked | Spot={spot:.0f} | "
                 f"CE={self.ce_fixed_strikes} PE={self.pe_fixed_strikes}")

    def _get_fixed_oi(self, oi_snapshot: dict) -> tuple:
        pe_sum = sum(oi_snapshot.get(int(s), {}).get("pe_oi", 0)
                     for s in self.pe_fixed_strikes)
        ce_sum = sum(oi_snapshot.get(int(s), {}).get("ce_oi", 0)
                     for s in self.ce_fixed_strikes)
        return pe_sum, ce_sum

    # ═══════════════════════════════════════════════════════════════
    # TICK — main entry point
    # ═══════════════════════════════════════════════════════════════

    def tick(self, spot: float, ts: datetime, oi_snapshot: dict,
               premiums: dict, fetcher=None) -> list[dict]:
        """
        Called every ~60s. Returns a list of signal dictionaries.
        """
        signals = []
        if not self._strikes_locked:
            self.lock_strikes(spot)
            return signals

        # ── Update EMA ──
        self._update_ema(spot, ts)
        self._update_premium_emas(premiums, ts)

        t_str = ts.strftime("%H:%M")
        
        # Calculate LIVE ATM tracking strikes on every tick
        atm = round(spot / self.strike_step) * self.strike_step
        live_ce = [atm - 3 * self.strike_step, atm - 2 * self.strike_step, atm - self.strike_step]
        live_pe = [atm + self.strike_step, atm + 2 * self.strike_step, atm + 3 * self.strike_step]

        # ── Theta Decay / Volatility Compression Filter ──
        if t_str >= "09:20":
            ce_prem_sum = sum(premiums.get(f"{int(s)}_CE", 0) for s in live_ce)
            pe_prem_sum = sum(premiums.get(f"{int(s)}_PE", 0) for s in live_pe)
            
            # Sample every ~60 seconds
            if not self._last_theta_ts or (ts - self._last_theta_ts).total_seconds() >= 55:
                self._last_theta_ts = ts
                self._recent_spots.append(spot)
                self._recent_ces.append(ce_prem_sum)
                self._recent_pes.append(pe_prem_sum)
                
                # Check for Volatility Crush / Premium Compression over the last 15 minutes
                if len(self._recent_spots) == 15:
                    spot_min, spot_max = min(self._recent_spots), max(self._recent_spots)
                    spot_range = spot_max - spot_min
                    
                    # Calculate peak straddle premium over the window
                    straddles = [c + p for c, p in zip(self._recent_ces, self._recent_pes)]
                    peak_straddle = max(straddles)
                    current_straddle = straddles[-1]
                    
                    # If combined premium decayed by > 3% in 15 mins without a directional spot breakout
                    straddle_decay_pct = (peak_straddle - current_straddle) / max(peak_straddle, 1)
                    if straddle_decay_pct > 0.03 and spot_range < 30:
                        self._theta_lockdown_until = ts + timedelta(minutes=5)
                        msg = f"[{t_str}] VOLATILITY CRUSH: Straddle decayed {straddle_decay_pct*100:.1f}%. Locking out entries."
                        log.info(msg)
                        print(msg)
                        
        # ── Track morning range (till entry_time) ──
        t_str = ts.strftime("%H:%M")

        # ── Capture OI at key times ──
        self._capture_oi_snapshots(oi_snapshot, ts, t_str)

        # ── Check exit (always check, position might exist) ──
        if self.position:
            # Track peak/trough spot for 15-point reversal
            p_dir = self.position["direction"]
            if p_dir == "CE":
                if spot > self.max_spot_in_trade:
                    self.max_spot_in_trade = spot
            else:
                if spot < self.min_spot_in_trade:
                    self.min_spot_in_trade = spot

            exit_action = self._check_exit(spot, ts, premiums, fetcher)
            if exit_action:
                signals.append(exit_action)

        if self.ic_position:
            ic_exit_action = self._check_ic_exit(spot, ts, premiums, fetcher)
            if ic_exit_action:
                signals.append(ic_exit_action)

        # ── Check for Reversal Flip (12pt Confirmation) ──
        if self.position is None and self.reversal_exit_spot is not None:
            if self.daily_bias == "CE" and spot <= self.reversal_exit_spot - 12.0:
                log.info(f"[{t_str}] 12pt CONFIRMATION -> FLIPPING TO PE")
                self.daily_bias = "PE"
                self.flip_triggered = True
                self.reversal_exit_spot = None
            elif self.daily_bias == "PE" and spot >= self.reversal_exit_spot + 12.0:
                log.info(f"[{t_str}] 12pt CONFIRMATION -> FLIPPING TO CE")
                self.daily_bias = "CE"
                self.flip_triggered = True
                self.reversal_exit_spot = None

        # ── Check entry ──
        if self.position is None and self._regime_decided:
            entry_action = self._check_entry(spot, ts, oi_snapshot, premiums, fetcher)
            if entry_action:
                signals.append(entry_action)
                
        if self.ic_position is None and self._regime_decided and self.regime == "sideways":
            ic_entry_action = self._check_ic_entry(spot, ts, premiums, fetcher)
            if ic_entry_action:
                signals.append(ic_entry_action)

        # ═══ OPTION SELLER BOT ═══
        # Mode 1: Volatility Crush — sell straddle/strangle during theta lockdown
        is_locked = bool(self._theta_lockdown_until and ts < self._theta_lockdown_until)
        if self.seller_bot.sell_position is None and is_locked:
            crush_signal = self.seller_bot.check_crush_entry(
                spot, ts, premiums, is_locked, self.analyzer
            )
            if crush_signal:
                self.seller_bot.open_sell_position(crush_signal, ts)
                signals.append(crush_signal)

        # Monitor active sell position for exit
        if self.seller_bot.sell_position is not None:
            sell_exit = self.seller_bot.check_sell_exit(spot, ts, premiums)
            if sell_exit:
                result = self.seller_bot.close_sell_position(
                    sell_exit["reason"], premiums, ts
                )
                sell_exit.update(result)
                signals.append(sell_exit)

        # Monitor active hedge for exit
        if self.seller_bot.hedge_position is not None:
            hedge_exit = self.seller_bot.check_hedge_exit(spot, ts, premiums)
            if hedge_exit:
                result = self.seller_bot.close_hedge_position(
                    premiums, ts, hedge_exit["reason"]
                )
                hedge_exit.update(result)
                signals.append(hedge_exit)

        return signals

    # ═══════════════════════════════════════════════════════════════
    # OI CAPTURE
    # ═══════════════════════════════════════════════════════════════

    def _capture_oi_snapshots(self, oi_snapshot: dict, ts: datetime, t_str: str):
        pe_sum, ce_sum = self._get_fixed_oi(oi_snapshot)
        if t_str >= self.snap1_time and self.oi_at_snap1 is None:
            self.oi_at_snap1 = (pe_sum, ce_sum)
        if t_str >= self.snap2_time and self.oi_at_snap2 is None:
            self.oi_at_snap2 = (pe_sum, ce_sum)
        if t_str >= self.regime_time and self.oi_at_entry is None:
            self.oi_at_entry = (pe_sum, ce_sum)
        
        # Keep rolling afternoon snapshot for divergence
        if t_str > self.regime_time:
            minute = int(t_str.split(":")[1])
            if minute % 30 == 0:
                self._oi_afternoon.append((ts, pe_sum, ce_sum))

    # ═══════════════════════════════════════════════════════════════
    # REGIME DETECTION (at 10:30)
    # REGIME DETECTION
    # ═══════════════════════════════════════════════════════════════

    def _decide_regime(self, spot: float):
        if self.morning_range >= self.min_range:
            self.regime = "trending"
            # Compute OI net bias (snap2 -> entry)
            if self.oi_at_snap2 and self.oi_at_entry:
                pe_d = self.oi_at_entry[0] - self.oi_at_snap2[0]
                ce_d = self.oi_at_entry[1] - self.oi_at_snap2[1]
                net = pe_d - ce_d
                log.info(f"[OI-Flow] TRENDING | Range={self.morning_range:.0f} | "
                         f"PE_d={pe_d/1e3:+.0f}K CE_d={ce_d/1e3:+.0f}K NET={net/1e3:+.0f}K")
        else:
            self.regime = "sideways"
            # Compute OI net bias (snap2 -> entry)
            if self.oi_at_snap2 and self.oi_at_entry:
                pe_d = self.oi_at_entry[0] - self.oi_at_snap2[0]
                ce_d = self.oi_at_entry[1] - self.oi_at_snap2[1]
                net = pe_d - ce_d
                # ── EXTREME OI REVERSALS (Fade the trend based on OI shift) ──
                # DISABLED REVERSAL MODULE per user request
                if False and abs(net) > self.signal_threshold * 1.5:
                    pass
                elif net > self.signal_threshold:
                    self.oi_bias = "bullish"
                elif net < -self.signal_threshold:
                    self.oi_bias = "bearish"
                log.info(f"[OI-Flow] SIDEWAYS | Range={self.morning_range:.0f} | "
                         f"Bias={self.oi_bias} | "
                         f"Range=[{self.morning_low:.0f}-{self.morning_high:.0f}]")

    # ═══════════════════════════════════════════════════════════════
    # ENTRY LOGIC
    # ═══════════════════════════════════════════════════════════════

    def _check_entry(self, spot: float, ts: datetime, oi_snapshot: dict, premiums: dict, fetcher=None) -> Optional[dict]:
        if self.position is not None:
            return None
        # Determine if any entry trigger is physically breached on this tick (before checking safety filters)
        potential_signal = None

        if self.losses_today >= 3:
            if potential_signal:
                log.info(f"[OI-Flow] Trigger {potential_signal} breached but entry blocked by: 3-Loss Daily Limit.")
            return None

        if self._theta_lockdown_until and ts < self._theta_lockdown_until:
            if potential_signal:
                log.info(f"[OI-Flow] Trigger {potential_signal} breached but entry blocked by: 15-Min Cooldown.")
            return None

        # --- NIFTY MASTER OI FILTER ---
        nifty_bias = None
        try:
            import pandas as pd
            import os
            date_str = ts.strftime("%Y-%m-%d")
            nifty_csv = f"C:/Users/shaik/OneDrive/Desktop/New folder/nifty-sniper-bot/logs/macro_NIFTY_{date_str}.csv"
            if os.path.exists(nifty_csv):
                if not hasattr(self, 'nifty_macro_df') or getattr(self, 'nifty_macro_date', None) != date_str:
                    self.nifty_macro_df = pd.read_csv(nifty_csv)
                    self.nifty_macro_df['timestamp'] = pd.to_datetime(self.nifty_macro_df['timestamp'])
                    self.nifty_macro_date = date_str
                
                df = self.nifty_macro_df
                past_df = df[df['timestamp'] <= ts]
                if not past_df.empty:
                    last_row = past_df.iloc[-1]
                    ce_chg = last_row.get("CE-OI-Chg", 0)
                    pe_chg = last_row.get("PE-OI-Chg", 0)
                    if ce_chg > pe_chg * 1.2:
                        nifty_bias = "BEARISH"
                    elif pe_chg > ce_chg * 1.2:
                        nifty_bias = "BULLISH"
        except Exception as e:
            pass


        # VIX Momentum Filter: Block entries if VIX is below 12.5
        if fetcher:
            try:
                vix = fetcher.get_india_vix()
                if vix > 0 and vix < 12.5:
                    if potential_signal:
                        log.info(f"[OI-Flow] Trigger {potential_signal} breached but entry blocked by: Low VIX ({vix:.2f} < 12.5).")
                    return None
            except Exception as e:
                log.warning(f"[VIX] Failed to fetch VIX in entry check: {e}")

        t_str = ts.strftime("%H:%M")
        if t_str < "09:15":
            return None

        # ── VIX-Driven Volatility Size Scaling ──
        size_multiplier = 1.0
        if fetcher:
            try:
                vix = fetcher.get_india_vix()
                if vix > 0:
                    if vix < 12.0:
                        size_multiplier = 0.5
                    elif vix > 15.0:
                        size_multiplier = 1.5
            except Exception as e:
                log.warning(f"[SIZE] Failed to fetch VIX: {e}")

        direction = None
        reason = ""
        strength = "single"
        position_target = None
        sl_spot = None

        # ── Daily Bias & Reversal Flip Entry Engine ──
        # Bypass Gann if structural S/R is enabled
        
        # Pillar 1: Structural Price S/R Mapping
        if self._cached_spot_levels is None or (ts - self._cached_spot_ts).total_seconds() > 300:
            self._cached_spot_levels = self.analyzer.get_structural_spot_levels(window=20)
            self._cached_spot_ts = ts
            
        supports = self._cached_spot_levels.get("support", [])
        resistances = self._cached_spot_levels.get("resistance", [])
        
        active_res = None
        active_sup = None
        
        for res in sorted(resistances):
            if res >= spot:
                active_res = res
                break
        
        for sup in sorted(supports, reverse=True):
            if sup <= spot:
                active_sup = sup
                break
                
        if active_res is None or active_sup is None:
            if not self.gann or not self.ce_trigger or not self.ce_sl:
                return None
            active_res = self.ce_trigger
            active_sup = self.ce_sl

        atm = round(spot / self.strike_step) * self.strike_step
        step = self.strike_step

        # --- VIX Strike Selection Logic ---
        vix_is_low = False
        if fetcher:
            try:
                vix = fetcher.get_india_vix()
                if vix > 0 and vix < 12.5:
                    vix_is_low = True
            except:
                pass
                
        # Define strikes based on VIX
        if self.daily_bias == "CE":
            ce_strikes = [atm - 3 * step, atm - 2 * step, atm - step] if vix_is_low else [atm - 2 * step, atm - step, atm]
        else:
            pe_strikes = [atm + 3 * step, atm + 2 * step, atm + step] if vix_is_low else [atm + 2 * step, atm + step, atm]

        if not self.flip_triggered:
            if self.daily_bias == "CE":
                # --- PREMIUM DRIVEN ENTRY ---
                prem_levels = self.analyzer.get_premium_historical_levels(ce_strikes[0], "CE")
                prem_floor = prem_levels.get("support", 0)
                current_prem = premiums.get(f"{ce_strikes[0]}_CE", 0)
                
                # Enter strictly if Premium is at or below its historical support floor (+5% buffer)
                if current_prem > 0 and current_prem <= (prem_floor * 1.05):
                    oi_status = self.analyzer.get_live_oi_change(atm, window_minutes=15)
                    if oi_status == "REVERSAL_CONFIRMED":
                        direction = "CE"
                        position_target = active_res
                        reason = f"PILLAR ENTRY: CE Premium near Floor ({current_prem:.1f} vs {prem_floor:.1f}) & OI Reversal"
                        self.max_spot_in_trade = spot
                        
            elif self.daily_bias == "PE":
                prem_levels = self.analyzer.get_premium_historical_levels(pe_strikes[0], "PE")
                prem_floor = prem_levels.get("support", 0)
                current_prem = premiums.get(f"{pe_strikes[0]}_PE", 0)
                
                # Enter strictly if Premium is at or below its historical support floor (+5% buffer)
                if current_prem > 0 and current_prem <= (prem_floor * 1.05):
                    oi_status = self.analyzer.get_live_oi_change(atm, window_minutes=15)
                    if oi_status == "REVERSAL_CONFIRMED":
                        direction = "PE"
                        position_target = active_sup
                        reason = f"PILLAR ENTRY: PE Premium near Floor ({current_prem:.1f} vs {prem_floor:.1f}) & OI Reversal"
                        self.min_spot_in_trade = spot
        else:
            if self.daily_bias == "CE":
                direction = "CE"
                position_target = active_res
                reason = f"FLIP ENTRY: Buy CE at {spot:.1f} (Tgt {active_res:.1f})"
                self.max_spot_in_trade = spot
                self.flip_triggered = False
            elif self.daily_bias == "PE":
                direction = "PE"
                position_target = active_sup
                reason = f"FLIP ENTRY: Buy PE at {spot:.1f} (Tgt {active_sup:.1f})"
                self.min_spot_in_trade = spot
                self.flip_triggered = False

        if direction is None:
            return None
            
        # ENFORCE NIFTY MASTER FILTER
        if nifty_bias == "BEARISH" and direction == "CE":
            log.info(f"[OI-Flow] CE Entry blocked because Nifty Master OI is BEARISH.")
            return None
        if nifty_bias == "BULLISH" and direction == "PE":
            log.info(f"[OI-Flow] PE Entry blocked because Nifty Master OI is BULLISH.")
            return None

        strikes = ce_strikes if direction == "CE" else pe_strikes

        # Pillar 4 Setup: Map the Premium target and True Premium Floor
        premium_target_price = 0.0
        premium_floor_sl = 0.0
        if direction == "CE" and active_res:
            premium_target_price = self.analyzer.get_premium_at_spot_level(strikes[0], "CE", active_res)
            prem_levels = self.analyzer.get_premium_historical_levels(strikes[0], "CE")
            premium_floor_sl = prem_levels.get("support", 0.0)
        elif direction == "PE" and active_sup:
            premium_target_price = self.analyzer.get_premium_at_spot_level(strikes[0], "PE", active_sup)
            prem_levels = self.analyzer.get_premium_historical_levels(strikes[0], "PE")
            premium_floor_sl = prem_levels.get("support", 0.0)
            
        return {
            "action": "entry",
            "direction": direction,
            "strikes": strikes,
            "spot": spot,
            "signal_strength": strength,
            "reason": reason,
            "target": position_target,
            "premium_target_price": premium_target_price,
            "premium_floor_sl": premium_floor_sl,
            "sl_spot": sl_spot,
            "timestamp": ts.isoformat(),
            "size_multiplier": size_multiplier,
        }


    # ═══════════════════════════════════════════════════════════════
    # EXIT LOGIC
    # ═══════════════════════════════════════════════════════════════

    def _check_exit(self, spot: float, ts: datetime,
                    premiums: dict, fetcher=None) -> Optional[dict]:
        if self.position is None:
            return None

        direction = self.position["direction"]
        strikes = self.position["strikes"]
        entry_time = self.position["entry_time"]
        entry_avg = self.position.get("entry_avg_premium", 0)
        max_avg = self.position.get("max_avg_premium", entry_avg)
        trade_num = self.position.get("trade_num", 1)
        hold_mins = (ts - entry_time).total_seconds() / 60

        # 1. Premium Structural Support Stop-Loss (The True Floor)
        # Instead of arbitrary spot distance, exit if the premium breaks its structural floor
        premium_floor = self.position.get("premium_floor_sl", 0)
        if premium_floor > 0 and premiums:
            # We track the ATM strike's premium value
            atm_strike = strikes[0]
            current_atm_prem = premiums.get(f"{int(atm_strike)}_{direction}", 0)
            if current_atm_prem > 0 and current_atm_prem < premium_floor:
                self.reversal_exit_spot = spot
                return {"action": "exit", "reason": f"PREMIUM S/R EXIT: {direction} Premium broke structural floor ({premium_floor:.1f})"}
                
        # 1.5 Back-up Spot Reversal Stop (Widened from 15 to 25 to allow Premium S/R to breathe)
        structural_rev = 75.0 if self.strike_step == 100 else 25.0
        if direction == "CE" and spot <= self.max_spot_in_trade - structural_rev:
            self.reversal_exit_spot = spot
            return {"action": "exit", "reason": f"25-PT REVERSAL EXIT: Spot dropped 25pts from peak ({self.max_spot_in_trade:.1f} -> {spot:.1f})"}
        elif direction == "PE" and spot >= self.min_spot_in_trade + structural_rev:
            self.reversal_exit_spot = spot
            return {"action": "exit", "reason": f"25-PT REVERSAL EXIT: Spot rose 25pts from trough ({self.min_spot_in_trade:.1f} -> {spot:.1f})"}

        # ── Pillar 2 & Pillar 4: Structural Target and Live OI Exit ──
        if self._cached_spot_levels:
            supports = self._cached_spot_levels.get("support", [])
            resistances = self._cached_spot_levels.get("resistance", [])
            atm = round(spot / self.strike_step) * self.strike_step
            
            # Check if we are approaching a structural target
            if direction == "CE":
                for res in sorted(resistances):
                    if spot >= res - 5.0 and spot <= res + 5.0:
                        # Pillar 2: Live OI Check at Target
                        oi_status = self.analyzer.get_live_oi_change(atm, window_minutes=15)
                        if oi_status == "REVERSAL_CONFIRMED":
                            # Resistance is holding. EXIT immediately.
                            return {"action": "exit", "reason": f"PILLAR EXIT: CE Hit Structural Res {res:.1f} and Live OI confirms Reversal (Ceiling)."}
                        elif oi_status == "BREAKOUT_WARNING":
                            # Breakout imminent. Widen trailing SL to survive shakeout.
                            self.trailing_sl_pct = 0.35 # Widen to 35%
                            log.info(f"CE at Resistance {res:.1f} but Breakout Imminent. Widening TSL to survive shakeout.")
                        break
            elif direction == "PE":
                for sup in sorted(supports, reverse=True):
                    if spot <= sup + 5.0 and spot >= sup - 5.0:
                        # Pillar 2: Live OI Check at Target
                        oi_status = self.analyzer.get_live_oi_change(atm, window_minutes=15)
                        if oi_status == "BREAKOUT_WARNING": # Support holding (PE writers)
                            return {"action": "exit", "reason": f"PILLAR EXIT: PE Hit Structural Sup {sup:.1f} and Live OI confirms Support."}
                        elif oi_status == "REVERSAL_CONFIRMED":
                            # Breakdown imminent. Widen TSL.
                            self.trailing_sl_pct = 0.35
                            log.info(f"PE at Support {sup:.1f} but Breakdown Imminent. Widening TSL to survive shakeout.")
                        break

        # ── Pillar 4: Premium Take-Profit Target ──
        target_prem = self.position.get("premium_target_price")
        if target_prem and premiums:
            current_avg = sum(premiums.get(f"{int(s)}_{direction}", 0) for s in strikes) / max(len(strikes), 1)
            if current_avg >= target_prem:
                # Target Hit! Tighten TSL to lock it in instead of exiting instantly
                self.trailing_sl_pct = 0.05
                log.info(f"PILLAR 4: Premium hit target {target_prem:.1f}! Tightening TSL to 5% to ride the breakout.")
                # We do not return exit immediately, we let the tight 5% TSL catch the peak.

        # 2. Premium Protections (Breakeven & TSL)
        if entry_avg > 0 and premiums:
            current_avg = sum(premiums.get(f"{int(s)}_{direction}", 0) for s in strikes) / max(len(strikes), 1)
            if current_avg > max_avg:
                self.position["max_avg_premium"] = current_avg
                max_avg = current_avg
            
            # Option Premium Breakeven Stop Loss (Once premium rises by +15%, lock SL to entry)
            is_breakeven = self.position.get("breakeven_activated", False)
            if not is_breakeven and current_avg >= entry_avg * 1.15:
                self.position["breakeven_activated"] = True
                log.info(f"[OI-Flow] Premium +15% profit reached. Breakeven SL activated.")
            # OPTION 3: Take-Profit Scaling Simulation
            # Once spot moves 10pts (Nifty) or 30pts (Sensex) in our favor, lock SL to a small profit.
            # This mathematically simulates scaling out 6 lots while letting 5 run, without breaking core order logic.
            is_scaled = self.position.get("tp_scaled", False)
            tp_target = 30.0 if self.strike_step == 100 else 10.0
            entry_spot = self.position.get("entry_spot", spot)
            
            if not is_scaled:
                if (direction == "CE" and spot >= entry_spot + tp_target) or \
                   (direction == "PE" and spot <= entry_spot - tp_target):
                    self.position["tp_scaled"] = True
                    self.position["breakeven_activated"] = True
                    # Set minimum exit premium to lock in cash equivalent of 6 lots taking profit
                    self.position["locked_exit_premium"] = entry_avg * 1.05
                    log.info(f"[OI-Flow] Take-Profit Scaling Target Reached! Locking SL to +5% to simulate partial exit.")
            
            if self.position.get("tp_scaled", False):
                min_exit = self.position.get("locked_exit_premium", entry_avg)
                if current_avg <= min_exit:
                    self.reversal_exit_spot = spot
                    return {"action": "exit", "reason": "TP SCALING STOP: Locked in profit after hitting Target."}

            if self.position.get("breakeven_activated", False):
                if current_avg <= entry_avg:
                    self.reversal_exit_spot = spot # Treat BE exit as reversal trigger to allow flip
                    return {"action": "exit", "reason": "Premium Breakeven SL hit"}

            # Runner Break-Even Stop Loss (after scale out)
            if self.position.get("tier1_done"):
                if current_avg <= entry_avg + 5:
                    self.reversal_exit_spot = spot # Treat runner BE exit as reversal trigger to allow flip
                    return {"action": "exit", "reason": "RUNNER: Stopped at Break-Even"}
                            
            # Options Buyer Premium TSL (ALWAYS ON)
            if current_avg > 0 and (current_avg - max_avg) / max_avg <= -self.trailing_sl_pct:
                self.reversal_exit_spot = spot # Treat TSL hit as reversal trigger to allow flip
                return {"action": "exit", "reason": f"TSL: -{self.trailing_sl_pct*100:.0f}% from peak"}

        # 3. Target Hit (Full Exit) - DISABLED per user request to ride the 12 EMA trend

        # 2. Max hold check disabled per user request to allow holding trade until SL/TSL is hit.
        pass

        return None

    def _check_ic_entry(self, spot: float, ts: datetime, premiums: dict, fetcher=None) -> Optional[dict]:
        t_str = ts.strftime("%H:%M")
        if self.ic_fired_today or t_str < "10:30" or t_str > "13:30":
            return None
            
        atm = round(spot / self.strike_step) * self.strike_step
        ce_short_k = atm + 200
        ce_long_k = atm + 300
        pe_short_k = atm - 200
        pe_long_k = atm - 300
        
        # Get premiums
        ce_short_e = premiums.get(f"{ce_short_k}_CE", 0)
        ce_long_e = premiums.get(f"{ce_long_k}_CE", 0)
        pe_short_e = premiums.get(f"{pe_short_k}_PE", 0)
        pe_long_e = premiums.get(f"{pe_long_k}_PE", 0)
        
        if min(ce_short_e, ce_long_e, pe_short_e, pe_long_e) <= 0:
            return None
            
        net_credit = (ce_short_e - ce_long_e) + (pe_short_e - pe_long_e)
        if net_credit < 15.0:
            return None
            
        return {
            "action": "entry_ic",
            "reason": f"IRON CONDOR Entry | Credit: {net_credit:.1f}",
            "strikes": {
                "ce_short": ce_short_k,
                "ce_long": ce_long_k,
                "pe_short": pe_short_k,
                "pe_long": pe_long_k
            },
            "entry_credit": net_credit
        }

    def _check_ic_exit(self, spot: float, ts: datetime, premiums: dict, fetcher=None) -> Optional[dict]:
        if not self.ic_position:
            return None
            
        strikes = self.ic_position["strikes"]
        entry_credit = self.ic_position["entry_credit"]
        
        ce_short_k = strikes["ce_short"]
        ce_long_k = strikes["ce_long"]
        pe_short_k = strikes["pe_short"]
        pe_long_k = strikes["pe_long"]
        
        ce_short_n = premiums.get(f"{ce_short_k}_CE", 0)
        ce_long_n = premiums.get(f"{ce_long_k}_CE", 0)
        pe_short_n = premiums.get(f"{pe_short_k}_PE", 0)
        pe_long_n = premiums.get(f"{pe_long_k}_PE", 0)
        
        if min(ce_short_n, ce_long_n, pe_short_n, pe_long_n) <= 0:
            return None
            
        close_cost = (ce_short_n - ce_long_n) + (pe_short_n - pe_long_n)
        
        tp_threshold = entry_credit * 0.50  # Take profit at 50% cost
        sl_threshold = entry_credit * 2.50  # Stop loss at 250% cost
        
        t_str = ts.strftime("%H:%M")
        
        if t_str >= "15:15":
            return {"action": "exit_ic", "reason": "FORCE_CLOSE: 15:15", "close_cost": close_cost}
        if close_cost <= tp_threshold:
            return {"action": "exit_ic", "reason": "TP: 50% Credit Reached", "close_cost": close_cost}
        if close_cost >= sl_threshold:
            return {"action": "exit_ic", "reason": "SL: 150% Loss Hit", "close_cost": close_cost}
            
        return None

    # ═══════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT
    # ═══════════════════════════════════════════════════════════════

    def open_position(self, signal: dict, premiums: dict, ts: datetime, lots: dict = None):
        direction = signal["direction"]
        strikes = signal["strikes"]
        trade_num = self.trades_today + 1

        self.position = {
            "direction": direction,
            "strikes": strikes,
            "entry_time": ts,
            "entry_premiums": premiums.copy(),
            "entry_avg_premium": sum(premiums.values()) / max(len(premiums), 1),
            "max_avg_premium": sum(premiums.values()) / max(len(premiums), 1),
            "trade_num": trade_num,
            "signal_strength": signal.get("signal_strength", "single"),
            "target": signal.get("target"),
            "premium_target_price": signal.get("premium_target_price", 0.0),
            "premium_floor_sl": signal.get("premium_floor_sl", 0.0),
            "sl_spot": signal.get("sl_spot"),
            "tier1_done": False,
            "entry_spot": signal.get("spot", spot if 'spot' in locals() else 24000.0),
            "lots_per_strike": lots or {s: 1 for s in strikes}
        }
        self.direction = direction
        self.locked_strikes = strikes
        self.entry_time = ts
        self.entry_premiums = premiums.copy()
        self.trades_today += 1

        log.info(f"[OI-Flow] OPEN {direction} | Trade #{trade_num} | "
                 f"Strikes={strikes} | Premiums={premiums}")

        # Mode 2: Directional Hedging — short the opposing leg simultaneously
        spot_val = signal.get("spot", 24000.0)
        self.seller_bot.open_directional_hedge(
            direction, strikes, spot_val, ts, premiums, self.analyzer
        )

    def close_position(self, reason: str, exit_premiums: dict,
                       ts: datetime) -> Optional[dict]:
        if self.position is None:
            return None

        direction = self.position["direction"]
        entry_prems = self.position["entry_premiums"]
        pnl_per_strike = {}
        total_pnl = 0.0

        for strike, exit_ltp in exit_premiums.items():
            entry_ltp = entry_prems.get(strike, exit_ltp)
            lots_count = self.position.get("lots_per_strike", {}).get(strike, 1)
            
            # Fallback to intrinsic value if backtest data is missing (exit_ltp is 0)
            spot = exit_premiums.get("spot", self.position.get("entry_spot", 24000.0))
            if not exit_ltp or exit_ltp <= 0:
                if direction == "CE":
                    exit_ltp = max(0.5, spot - strike)
                else:
                    exit_ltp = max(0.5, strike - spot)
                    
            pnl = (exit_ltp - entry_ltp) * lots_count * self.lot_size
            pnl_per_strike[strike] = pnl
            total_pnl += pnl

        self.daily_pnl_mtm += total_pnl
        if total_pnl < 0:
            self.consecutive_losses += 1
            self.losses_today += 1
        else:
            self.consecutive_losses = 0

        # Record peak or trough spot for re-entry validation
        if direction == "CE":
            self.last_trade_peak_spot = self.position.get("peak_spot", 0.0)
        else:
            self.last_trade_trough_spot = self.position.get("trough_spot", float('inf'))

        self.last_exit_time = ts
        from datetime import timedelta
        self._theta_lockdown_until = ts + timedelta(minutes=15)

        result = {
            "direction": direction,
            "reason": reason,
            "pnl_per_strike": pnl_per_strike,
            "total_pnl": total_pnl,
            "hold_minutes": (ts - self.entry_time).total_seconds() / 60,
        }

        log.info(f"[OI-Flow] CLOSE {direction} | {reason} | "
                 f"P&L={total_pnl:+.0f}")

        # Close the Seller Bot's directional hedge along with the buyer's position
        if self.seller_bot.hedge_position is not None:
            hedge_result = self.seller_bot.close_hedge_position(
                exit_premiums, ts, "Buyer closed directional trade"
            )
            result["hedge_pnl"] = hedge_result.get("total_pnl", 0.0)
            result["total_pnl"] = total_pnl + result["hedge_pnl"]

        self.position = None
        self.direction = None
        self.locked_strikes = []
        self.entry_time = None
        self.entry_premiums = {}
        self._breakout_confirmed_dir = None
        self._breakout_price = 0.0

        return result

    def open_ic_position(self, signal: dict, premiums: dict, ts: datetime, lots: int):
        strikes = signal["strikes"]
        self.ic_position = {
            "entry_time": ts,
            "strikes": strikes,
            "entry_premiums": premiums.copy(),
            "entry_credit": signal["entry_credit"],
            "lots": lots
        }
        self.ic_fired_today = True
        log.info(f"[OI-Flow] OPEN IRON CONDOR | Credit={signal['entry_credit']:.1f} | Lots={lots}")

    def close_ic_position(self, signal: dict, premiums: dict, ts: datetime) -> Optional[dict]:
        if not self.ic_position:
            return None
            
        entry_credit = self.ic_position["entry_credit"]
        lots = self.ic_position["lots"]
        close_cost = signal["close_cost"]
        
        # P&L calculation: Net profit = (Credit received - Cost to close) * Lots * Lot_Size
        gross_pnl = (entry_credit - close_cost) * lots * self.lot_size
        
        # Subtract Brokerage (8 legs per Iron Condor round trip -> 8 * 30 = Rs. 240)
        net_pnl = gross_pnl - 240
        
        self.daily_pnl_mtm += net_pnl
        
        result = {
            "direction": "IRON_CONDOR",
            "reason": signal["reason"],
            "total_pnl": net_pnl,
            "hold_minutes": (ts - self.ic_position["entry_time"]).total_seconds() / 60,
        }
        
        log.info(f"[OI-Flow] CLOSE IRON CONDOR | {signal['reason']} | P&L={net_pnl:+.0f}")
        
        self.ic_position = None
        return result

    # ═══════════════════════════════════════════════════════════════
    # POSITION SIZING (6:3:2)
    # ═══════════════════════════════════════════════════════════════

    def compute_lot_allocation(self, premiums: dict,
                               size_mult: float = 1.0) -> dict:
        strikes = list(premiums.keys())
        if len(strikes) != 3:
            return {s: 1 for s in strikes}

        # User specifically requested NO capital limits. 
        # We buy exactly 6 lots, 3 lots, and 2 lots.
        ratio = [6, 3, 2]
        allocation = {}
        for i, strike in enumerate(strikes):
            allocation[strike] = max(1, int(ratio[i] * size_mult))
            
        return allocation

    # ═══════════════════════════════════════════════════════════════
    # EMA (monitoring only)
    # ═══════════════════════════════════════════════════════════════

    def _update_ema(self, spot: float, ts: datetime):
        ts_min = ts.replace(second=0, microsecond=0)
        
        if self._last_ema_ts and ts_min <= self._last_ema_ts:
            return
        self._last_ema_ts = ts_min

        if self._ema_fast_val == 0:
            self._ema_fast_val = spot
            self._ema_slow_val = spot
            self._ema_macro_val = spot
        else:
            kf = 2 / (self.ema_fast + 1)
            ks = 2 / (self.ema_slow + 1)
            km = 2 / (self.ema_macro + 1)
            self._ema_fast_val = spot * kf + self._ema_fast_val * (1 - kf)
            self._ema_slow_val = spot * ks + self._ema_slow_val * (1 - ks)
            self._ema_macro_val = spot * km + self._ema_macro_val * (1 - km)

    def _update_premium_emas(self, premiums: dict, ts: datetime):
        ts_min = ts.replace(second=0, microsecond=0)
        if self._last_prem_ema_ts and ts_min <= self._last_prem_ema_ts:
            return
        self._last_prem_ema_ts = ts_min

        kf = 2 / (self.prem_ema_fast_period + 1)
        ks = 2 / (self.prem_ema_slow_period + 1)

        for key, price in premiums.items():
            if price <= 0:
                continue
            if key not in self._prem_ema_fast:
                self._prem_ema_fast[key] = price
                self._prem_ema_slow[key] = price
            else:
                self._prem_ema_fast[key] = price * kf + self._prem_ema_fast[key] * (1 - kf)
                self._prem_ema_slow[key] = price * ks + self._prem_ema_slow[key] * (1 - ks)

    @property
    def ema_fast_val(self) -> float:
        return self._ema_fast_val

    @property
    def ema_slow_val(self) -> float:
        return self._ema_slow_val

    @property
    def ema_trend(self) -> str:
        if self._ema_fast_val > self._ema_slow_val:
            return "bull"
        elif self._ema_fast_val < self._ema_slow_val:
            return "bear"
        return "neutral"

    # ═══════════════════════════════════════════════════════════════
    # STATE PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    def save_state(self, path: str = "state/oi_flow_state.json"):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "trades_today": self.trades_today,
            "daily_pnl_mtm": self.daily_pnl_mtm,
            "consecutive_losses": self.consecutive_losses,
            "regime": self.regime,
            "oi_bias": self.oi_bias,
            "morning_high": self.morning_high,
            "morning_low": self.morning_low,
            "morning_range": self.morning_range,
            "_regime_decided": self._regime_decided,
            "_breakout_fired": self._breakout_fired,
            "ce_fixed_strikes": self.ce_fixed_strikes,
            "pe_fixed_strikes": self.pe_fixed_strikes,
            "_strikes_locked": self._strikes_locked,
            "open_spot": self.open_spot,
            "ce_trigger": self.ce_trigger,
            "ce_target": self.ce_target,
            "ce_sl": self.ce_sl,
            "pe_trigger": self.pe_trigger,
            "pe_target": self.pe_target,
            "pe_sl": self.pe_sl,
            "losses_today": self.losses_today,
        }
        p.write_text(json.dumps(state, indent=2))

    def load_state(self, path: str = "state/oi_flow_state.json"):
        p = Path(path)
        if not p.exists():
            return
        state = json.loads(p.read_text())
        self.trades_today = state.get("trades_today", 0)
        self.losses_today = state.get("losses_today", 0)
        self.daily_pnl_mtm = state.get("daily_pnl_mtm", 0.0)
        self.consecutive_losses = state.get("consecutive_losses", 0)
        self.regime = state.get("regime", "pending")
        self.oi_bias = state.get("oi_bias", "neutral")
        self.morning_high = state.get("morning_high", 0)
        self.morning_low = state.get("morning_low", float('inf'))
        self.morning_range = state.get("morning_range", 0)
        self._regime_decided = state.get("_regime_decided", False)
        self._breakout_fired = state.get("_breakout_fired", False)
        self.ce_fixed_strikes = state.get("ce_fixed_strikes", [])
        self.pe_fixed_strikes = state.get("pe_fixed_strikes", [])
        self._strikes_locked = state.get("_strikes_locked", False)
        self.open_spot = state.get("open_spot")
        self.ce_trigger = state.get("ce_trigger", 0.0)
        self.ce_target = state.get("ce_target", 0.0)
        self.ce_sl = state.get("ce_sl", 0.0)
        self.pe_trigger = state.get("pe_trigger", 0.0)
        self.pe_target = state.get("pe_target", 0.0)
        self.pe_sl = state.get("pe_sl", 0.0)
        log.info(f"[OI-Flow] State loaded: trades={self.trades_today}")
