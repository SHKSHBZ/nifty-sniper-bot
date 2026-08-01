"""
oi_flow_engine.py — OI Flow Momentum Tracker v1.1
==================================================
Clean replacement for SignalEngine's 9-gate system.
Two inputs: spot trend (EMA 9/21) + OI delta on locked ITM strikes.

Based on: strategy_config_v1.1.json
Built from: June 11-12 deep analysis sessions
"""

import json
import logging
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path
from typing import Optional

log = logging.getLogger("OIFlow")

# ── Load Options.json params ──
def _load_opts():
    p = Path(__file__).parent / "Options.json"
    if p.exists():
        return json.loads(p.read_text()).get("configurableParameters", {})
    return {}

OPTS = _load_opts()


class OIFlowEngine:
    """Minimal options buyer using spot trend + OI velocity only."""

    def __init__(self, config: dict):
        self.config = config
        strat = config.get("strategy", {})

        # ── EMA ──
        self.ema_fast = int(OPTS.get("oi_flow_ema_fast", 9))
        self.ema_slow = int(OPTS.get("oi_flow_ema_slow", 21))
        self.candle_minutes = int(OPTS.get("oi_flow_candle_minutes", 3))
        self._spot_candles: deque = deque(maxlen=200)
        self._ema_fast_val: float = 0.0
        self._ema_slow_val: float = 0.0
        self._last_candle_ts: Optional[datetime] = None
        self._cross_up: bool = False
        self._cross_down: bool = False

        # ── OI tracking ──
        self._oi_snapshots: deque = deque(maxlen=10)  # 5-min lookback at 60s polls
        self._oi_lookback = int(OPTS.get("oi_flow_oi_lookback_minutes", 5))

        # ── Position state ──
        self.position: Optional[dict] = None  # active position or None
        self.direction: Optional[str] = None   # "CE" or "PE" or None
        self.locked_strikes: list = []          # 3 ITM strikes at entry
        self.entry_time: Optional[datetime] = None
        self.entry_premiums: dict = {}          # strike -> entry ltp
        self.peak_oi_covering_delta: float = 0.0
        self.partial_exit_done: bool = False
        self.breakeven_sl: Optional[float] = None

        # ── Session state ──
        self.trades_today: int = 0
        self.daily_pnl_mtm: float = 0.0
        self.consecutive_losses: int = 0
        self.last_exit_time: Optional[datetime] = None
        self.last_exit_direction: Optional[str] = None

        # ── Risk params ──
        self.max_trades = int(OPTS.get("oi_flow_max_trades_per_day", 4))
        self.max_daily_loss = float(OPTS.get("oi_flow_max_daily_loss", 8000))
        self.max_hold_minutes = int(OPTS.get("oi_flow_max_hold_minutes", 180))
        self.emergency_sl_pct = float(OPTS.get("oi_flow_emergency_sl_pct", 25)) / 100.0
        self.velocity_drop_pct = float(OPTS.get("oi_flow_velocity_drop_pct", 20)) / 100.0
        self.flat_threshold = int(OPTS.get("oi_flow_flat_threshold_nifty", 50000))
        self.flat_max_snapshots = int(OPTS.get("oi_flow_flat_max_snapshots", 3))
        self.entry_window_start = OPTS.get("oi_flow_entry_start", "09:45")
        self.entry_window_end = OPTS.get("oi_flow_entry_end", "14:30")
        self.total_capital = float(OPTS.get("oi_flow_capital_per_trade", 30000))
        self.lot_size = int(strat.get("nifty_lot_size", 65))
        self.strike_step = int(strat.get("nifty_strike_step", 50))
        self.consecutive_loss_breaker = int(OPTS.get("oi_flow_consecutive_loss_breaker", 3))

        # ── Decay tracking ──
        self._premium_history: dict = {}  # strike -> deque of (ts, ltp)

        # ── Swing structure for direction switch ──
        self._swing_highs: deque = deque(maxlen=30)
        self._swing_lows: deque = deque(maxlen=30)
        self._spot_history: deque = deque(maxlen=60)

        # ── Data fetcher reference (set by main.py) ──
        self.fetcher = None

        log.info(f"OIFlowEngine v1.1 ready | EMA={self.ema_fast}/{self.ema_slow} "
                 f"| Candle={self.candle_minutes}m | MaxTrades={self.max_trades} "
                 f"| DailyLoss={self.max_daily_loss} | Capital={self.total_capital}")

    # ═══════════════════════════════════════════════════════════════
    # SPOT + EMA
    # ═══════════════════════════════════════════════════════════════

    def update_spot(self, spot: float, ts: datetime) -> dict:
        """Feed spot price. EMA updated every 1-min close. Returns empty dict."""
        self._spot_history.append((ts, spot))

        # Swing structure
        if len(self._spot_history) >= 3:
            prices = [s[1] for s in list(self._spot_history)[-3:]]
            if prices[1] > prices[0] and prices[1] > prices[2]:
                self._swing_highs.append((ts, prices[1]))
            if prices[1] < prices[0] and prices[1] < prices[2]:
                self._swing_lows.append((ts, prices[1]))

        # Build 1-min candles — skip duplicates (same minute)
        ts_minute = ts.replace(second=0, microsecond=0)
        if self._last_candle_ts is not None and ts_minute <= self._last_candle_ts:
            return {}

        if self._last_candle_ts is None:
            self._last_candle_ts = ts_minute
            self._spot_candles.append(spot)
            return {}

        # Close previous candle and update EMA (every 1-min close)
        close_price = spot  # current minute's spot = candle close

        k_fast = 2 / (self.ema_fast + 1)
        k_slow = 2 / (self.ema_slow + 1)

        if self._ema_fast_val == 0:
            self._ema_fast_val = close_price
            self._ema_slow_val = close_price
        else:
            prev_fast = self._ema_fast_val
            prev_slow = self._ema_slow_val
            self._ema_fast_val = close_price * k_fast + self._ema_fast_val * (1 - k_fast)
            self._ema_slow_val = close_price * k_slow + self._ema_slow_val * (1 - k_slow)

            # Detect crossover
            cross_up = (self._ema_fast_val > self._ema_slow_val and
                        prev_fast <= prev_slow and prev_fast > 0)
            cross_down = (self._ema_fast_val < self._ema_slow_val and
                          prev_fast >= prev_slow and prev_fast > 0)

            if cross_up:
                self._cross_up = True
                self._cross_down = False
            elif cross_down:
                self._cross_down = True
                self._cross_up = False

        self._spot_candles.append(close_price)
        self._last_candle_ts = ts_minute

        return {}

    @property
    def ema_fast_val(self) -> float:
        return self._ema_fast_val

    @property
    def ema_slow_val(self) -> float:
        return self._ema_slow_val

    # ═══════════════════════════════════════════════════════════════
    # OI TRACKING
    # ═══════════════════════════════════════════════════════════════

    def update_oi(self, oi_snapshot: dict, ts: datetime):
        """
        oi_snapshot: {strike: {'ce_oi': int, 'pe_oi': int}, ...}
        Called every ~60s by main.py.
        """
        self._oi_snapshots.append((ts, oi_snapshot))

    def get_oi_delta(self, direction: str, strikes: list) -> float:
        """Sum OI change across strikes over lookback window."""
        if len(self._oi_snapshots) < 2:
            return 0.0

        now_ts, now_data = self._oi_snapshots[-1]
        ref_ts = now_ts - timedelta(minutes=self._oi_lookback)

        # Find closest snapshot to ref_ts
        ref_data = None
        for ts, snap in reversed(self._oi_snapshots):
            if ts <= ref_ts:
                ref_data = snap
                break
        if ref_data is None:
            ref_data = self._oi_snapshots[0][1]

        oi_key = "ce_oi" if direction == "CE" else "pe_oi"
        delta = 0.0
        for s in strikes:
            s_int = int(s)
            now_oi = now_data.get(s_int, {}).get(oi_key, 0)
            ref_oi = ref_data.get(s_int, {}).get(oi_key, 0)
            delta += (now_oi - ref_oi)

        return delta

    # ═══════════════════════════════════════════════════════════════
    # PREMIUM TRACKING (for decay detection)
    # ═══════════════════════════════════════════════════════════════

    def update_premiums(self, premiums: dict, ts: datetime):
        """premiums: {strike: ce_ltp or pe_ltp}"""
        for strike, ltp in premiums.items():
            if strike not in self._premium_history:
                self._premium_history[strike] = deque(maxlen=30)
            self._premium_history[strike].append((ts, ltp))

    def detect_decay(self, strikes: list) -> bool:
        """True if premiums bleeding >0.15%/min across 2+ strikes for 3+ min."""
        decay_count = 0
        for s in strikes:
            hist = self._premium_history.get(s)
            if not hist or len(hist) < 5:
                continue
            recent = list(hist)[-5:]
            first_price = recent[0][1]
            last_price = recent[-1][1]
            if first_price <= 0:
                continue
            minutes = max(1, (recent[-1][0] - recent[0][0]).total_seconds() / 60)
            decay_pct_per_min = ((first_price - last_price) / first_price * 100) / minutes
            if decay_pct_per_min > 0.15:
                decay_count += 1
        return decay_count >= 2

    # ═══════════════════════════════════════════════════════════════
    # STRIKE SELECTION
    # ═══════════════════════════════════════════════════════════════

    def select_itm_strikes(self, spot: float, direction: str, fetcher=None) -> list:
        """Pick 3 ITM strikes with delta 0.50-0.70."""
        atm = round(spot / self.strike_step) * self.strike_step

        if direction == "CE":
            # ITM for CE = strikes BELOW spot
            candidates = [atm - i * self.strike_step for i in range(1, 6)]
        else:
            # ITM for PE = strikes ABOVE spot
            candidates = [atm + i * self.strike_step for i in range(1, 6)]

        # Filter by delta if greeks available
        selected = []
        for s in candidates:
            delta = self._get_delta(s, direction, fetcher)
            if delta is None or (0.50 <= delta <= 0.70):
                selected.append(s)
            if len(selected) >= 3:
                break

        # Fallback: take nearest 3 ITM
        if len(selected) < 3:
            selected = candidates[:3]

        return sorted(selected, reverse=(direction == "CE"))

    def _get_delta(self, strike: int, opt_type: str, fetcher) -> Optional[float]:
        """Get delta from data_fetcher greeks map."""
        if fetcher is None:
            return None
        try:
            greeks = fetcher.get_greeks_map()
            key = f"{strike}_{opt_type}"
            return greeks.get(key, {}).get("delta")
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════
    # ENTRY LOGIC
    # ═══════════════════════════════════════════════════════════════

    def check_entry(self, spot: float, ts: datetime, fetcher=None) -> Optional[dict]:
        """Returns entry signal dict or None."""
        if self.position is not None:
            return None  # already in a position

        # Time gate
        t_str = ts.strftime("%H:%M")
        if t_str < self.entry_window_start or t_str > self.entry_window_end:
            return None

        # Daily limits
        if self.trades_today >= self.max_trades:
            return None
        if self.daily_pnl_mtm <= -self.max_daily_loss:
            return None

        # Cooldown after exit
        if self.last_exit_time and (ts - self.last_exit_time).total_seconds() < 900:
            return None

        # Decay check
        if self.position is None and self.locked_strikes:
            if self.detect_decay(self.locked_strikes):
                return None

        # Direction from EMA crossover
        direction = None
        if self._cross_up:
            direction = "CE"
            self._cross_up = False
        elif self._cross_down:
            direction = "PE"
            self._cross_down = False

        if direction is None:
            return None

        # Trend filter — only trade in direction of broader trend
        if self._ema_fast_val > 0 and self._ema_slow_val > 0:
            if direction == "PE" and self._ema_fast_val > self._ema_slow_val:
                return None  # block PE in bull trend
            if direction == "CE" and self._ema_fast_val < self._ema_slow_val:
                return None  # block CE in bear trend

        # Pick ITM strikes
        strikes = self.select_itm_strikes(spot, direction, fetcher)
        if len(strikes) < 1:
            return None

        # OI confirmation — check OPPOSITE side's OI
        # CE entry: PE OI BUILDING (put writers selling → bullish fuel)
        # PE entry: CE OI BUILDING (call writers selling → bearish fuel)
        confirm_direction = "PE" if direction == "CE" else "CE"
        oi_delta = self.get_oi_delta(confirm_direction, strikes)
        if oi_delta < self.flat_threshold:  # opposite side not building enough
            log.info(f"[OI-Flow] {direction} signal: {confirm_direction} OI delta={oi_delta:+.0f} "
                     f"(need >{self.flat_threshold}) — rejected")
            return None

        # Halve position size if consecutive losses
        size_mult = 0.5 if self.consecutive_losses >= self.consecutive_loss_breaker else 1.0

        return {
            "direction": direction,
            "strikes": strikes,
            "spot": spot,
            "oi_delta": oi_delta,
            "size_multiplier": size_mult,
            "timestamp": ts.isoformat(),
        }

    # ═══════════════════════════════════════════════════════════════
    # EXIT LOGIC — checks opposite side's OI
    # ═══════════════════════════════════════════════════════════════

    def _confirm_dir(self) -> str:
        """Return the OI direction that confirms our trade.
        CE trade → watch PE OI. PE trade → watch CE OI."""
        return "PE" if self.direction == "CE" else "CE"

    def check_exit(self, spot: float, ts: datetime, premiums: dict) -> Optional[str]:
        """Returns exit reason or None. Checks opposite side's OI velocity."""
        if self.position is None:
            return None

        direction = self.position["direction"]
        strikes = self.position["strikes"]
        entry_time = self.position["entry_time"]
        entry_avg = self.position["entry_avg_premium"]
        confirm_dir = self._confirm_dir()  # opposite side's OI

        # 1. Emergency SL — basket premium -25%
        current_avg = sum(premiums.get(s, 0) for s in strikes) / len(strikes)
        if current_avg > 0 and (current_avg - entry_avg) / entry_avg <= -self.emergency_sl_pct:
            return f"EMERGENCY SL: basket -{self.emergency_sl_pct*100:.0f}%"

        # 2. Max hold time
        hold_mins = (ts - entry_time).total_seconds() / 60
        if hold_mins >= self.max_hold_minutes:
            return f"MAX HOLD: {self.max_hold_minutes}min reached"

        # 3. Expiry day EOD (14:30+)
        if ts.strftime("%H:%M") >= "14:30":
            if ts.weekday() == 3:
                return "EOD: Expiry gamma chaos avoidance"

        # 4. Velocity drop — opposite side OI building slows
        oi_delta = self.get_oi_delta(confirm_dir, strikes)
        if self.peak_oi_covering_delta > 0:
            if oi_delta < self.peak_oi_covering_delta * (1 - self.velocity_drop_pct):
                if hold_mins >= 5:
                    return f"VELOCITY DROP: {confirm_dir} OI {oi_delta:+.0f} vs peak {self.peak_oi_covering_delta:+.0f}"

        # 5. Flatline — opposite side OI flat for N snapshots
        flat_count = 0
        for snap_ts, snap_data in list(self._oi_snapshots)[-self.flat_max_snapshots:]:
            d = 0.0
            oi_key = "ce_oi" if confirm_dir == "CE" else "pe_oi"
            ref_ts = snap_ts - timedelta(minutes=5)
            ref_data = None
            for rts, rsnap in self._oi_snapshots:
                if rts <= ref_ts:
                    ref_data = rsnap
                else:
                    break
            if ref_data:
                for s in strikes:
                    d += snap_data.get(int(s), {}).get(oi_key, 0) - ref_data.get(int(s), {}).get(oi_key, 0)
            if abs(d) < self.flat_threshold:
                flat_count += 1
        if flat_count >= self.flat_max_snapshots and hold_mins >= 10:
            return f"FLATLINE: {confirm_dir} OI flat for {flat_count} snapshots"

        # 6. EMA reversal
        if direction == "CE" and self._cross_down:
            return "EMA REVERSAL: 9 crossed below 21"
        if direction == "PE" and self._cross_up:
            return "EMA REVERSAL: 9 crossed above 21"

        return None

    # ═══════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT
    # ═══════════════════════════════════════════════════════════════

    def open_position(self, signal: dict, premiums: dict, ts: datetime):
        """Called by main.py when entry is confirmed."""
        direction = signal["direction"]
        strikes = signal["strikes"]

        self.position = {
            "direction": direction,
            "strikes": strikes,
            "entry_time": ts,
            "entry_premiums": premiums.copy(),
            "entry_avg_premium": sum(premiums.values()) / len(premiums),
            "partial_exit_done": False,
            "lots_per_strike": {},  # filled by main.py
        }
        self.direction = direction
        self.locked_strikes = strikes
        self.entry_time = ts
        self.entry_premiums = premiums.copy()
        self.peak_oi_covering_delta = self.get_oi_delta(self._confirm_dir(), strikes)
        self.partial_exit_done = False
        self.breakeven_sl = None
        self.trades_today += 1

        log.info(f"[OI-Flow] OPENED {direction} | Strikes={strikes} "
                 f"| Premiums={premiums} | Trade #{self.trades_today}")

    def close_position(self, reason: str, exit_premiums: dict, ts: datetime):
        """Called by main.py on exit. Returns P&L summary."""
        if self.position is None:
            return None

        direction = self.position["direction"]
        entry_prems = self.position["entry_premiums"]
        pnl_per_strike = {}
        total_pnl = 0.0

        for strike, exit_ltp in exit_premiums.items():
            entry_ltp = entry_prems.get(strike, exit_ltp)
            pnl = (exit_ltp - entry_ltp) * self.lot_size
            pnl_per_strike[strike] = pnl
            total_pnl += pnl

        # Session tracking
        self.daily_pnl_mtm += total_pnl
        if total_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        self.last_exit_time = ts
        self.last_exit_direction = direction

        result = {
            "direction": direction,
            "reason": reason,
            "pnl_per_strike": pnl_per_strike,
            "total_pnl": total_pnl,
            "hold_minutes": (ts - self.entry_time).total_seconds() / 60,
        }

        log.info(f"[OI-Flow] CLOSED {direction} | Reason={reason} "
                 f"| P&L={total_pnl:+.0f} | ConsLoss={self.consecutive_losses}")

        # Reset position state
        self.position = None
        self.direction = None
        self.locked_strikes = []
        self.entry_time = None
        self.entry_premiums = {}
        self.peak_oi_covering_delta = 0.0
        self.partial_exit_done = False
        self.breakeven_sl = None

        return result

    def check_partial_exit(self, ts: datetime) -> bool:
        """True if OI covering accelerating (panic phase)."""
        if self.position is None or self.partial_exit_done:
            return False

        direction = self.position["direction"]
        strikes = self.position["strikes"]

        # Compute 30-min rolling average OI delta
        deltas = []
        for snap_ts, snap_data in list(self._oi_snapshots)[-30:]:
            d = 0.0
            oi_key = "ce_oi" if direction == "CE" else "pe_oi"
            ref_ts = snap_ts - timedelta(minutes=5)
            ref_data = None
            for rts, rsnap in self._oi_snapshots:
                if rts <= ref_ts:
                    ref_data = rsnap
                else:
                    break
            if ref_data:
                for s in strikes:
                    d += snap_data.get(int(s), {}).get(oi_key, 0) - ref_data.get(int(s), {}).get(oi_key, 0)
            deltas.append(d)

        if len(deltas) < 10:
            return False

        avg_delta = sum(deltas) / len(deltas)
        current_delta = self.get_oi_delta(direction, strikes)

        # Trigger if current > 3x average
        if avg_delta < 0 and current_delta < avg_delta * 3:
            self.partial_exit_done = True
            self.breakeven_sl = self.position["entry_avg_premium"]
            log.info(f"[OI-Flow] PARTIAL EXIT signal: OI accel {current_delta:+.0f} vs avg {avg_delta:+.0f}")
            return True

        return False

    # ═══════════════════════════════════════════════════════════════
    # POSITION SIZING (6:3:2)
    # ═══════════════════════════════════════════════════════════════

    def compute_lot_allocation(self, premiums: dict, size_mult: float = 1.0) -> dict:
        """
        premiums: {strike: ltp} for the 3 selected strikes (sorted deep->shallow ITM)
        Returns: {strike: lots} using 6:3:2 ratio.
        """
        strikes = list(premiums.keys())
        if len(strikes) != 3:
            return {s: 1 for s in strikes}

        capital = self.total_capital * size_mult
        ratio = [6, 3, 2]
        ratio_sum = sum(ratio)

        allocation = {}
        remaining_capital = capital

        for i, strike in enumerate(strikes):
            target = capital * ratio[i] / ratio_sum
            premium = premiums[strike]
            cost_per_lot = premium * self.lot_size
            max_lots = max(1, int(target / cost_per_lot)) if cost_per_lot > 0 else 0
            actual_lots = min(max_lots, int(remaining_capital / cost_per_lot)) if cost_per_lot > 0 else 0
            actual_lots = max(1, actual_lots)  # at least 1 lot per strike
            allocation[strike] = actual_lots
            remaining_capital -= actual_lots * cost_per_lot

        # Residual to deepest ITM (first strike)
        if remaining_capital > 0 and strikes:
            premium = premiums[strikes[0]]
            cost_per_lot = premium * self.lot_size
            extra_lots = int(remaining_capital / cost_per_lot)
            if extra_lots > 0:
                allocation[strikes[0]] += extra_lots

        return allocation

    # ═══════════════════════════════════════════════════════════════
    # STATE PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    def save_state(self, filepath: str):
        """Persist bot state for crash recovery."""
        state = {
            "position": self.position,
            "direction": self.direction,
            "locked_strikes": self.locked_strikes,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_premiums": self.entry_premiums,
            "peak_oi_covering_delta": self.peak_oi_covering_delta,
            "partial_exit_done": self.partial_exit_done,
            "breakeven_sl": self.breakeven_sl,
            "trades_today": self.trades_today,
            "daily_pnl_mtm": self.daily_pnl_mtm,
            "consecutive_losses": self.consecutive_losses,
            "last_exit_time": self.last_exit_time.isoformat() if self.last_exit_time else None,
            "last_exit_direction": self.last_exit_direction,
            "ema_fast": self._ema_fast_val,
            "ema_slow": self._ema_slow_val,
        }
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(filepath).write_text(json.dumps(state, indent=2, default=str))

    def load_state(self, filepath: str):
        """Restore bot state after crash."""
        p = Path(filepath)
        if not p.exists():
            return
        try:
            state = json.loads(p.read_text())
            self.position = state.get("position")
            self.direction = state.get("direction")
            self.locked_strikes = state.get("locked_strikes", [])
            self.entry_time = datetime.fromisoformat(state["entry_time"]) if state.get("entry_time") else None
            self.entry_premiums = state.get("entry_premiums", {})
            self.peak_oi_covering_delta = state.get("peak_oi_covering_delta", 0.0)
            self.partial_exit_done = state.get("partial_exit_done", False)
            self.breakeven_sl = state.get("breakeven_sl")
            self.trades_today = state.get("trades_today", 0)
            self.daily_pnl_mtm = state.get("daily_pnl_mtm", 0.0)
            self.consecutive_losses = state.get("consecutive_losses", 0)
            self.last_exit_time = datetime.fromisoformat(state["last_exit_time"]) if state.get("last_exit_time") else None
            self.last_exit_direction = state.get("last_exit_direction")
            self._ema_fast_val = state.get("ema_fast", 0.0)
            self._ema_slow_val = state.get("ema_slow", 0.0)
            log.info(f"[OI-Flow] State restored: trades={self.trades_today}, pnl={self.daily_pnl_mtm:.0f}")
        except Exception as e:
            log.warning(f"[OI-Flow] State restore failed: {e}")

    # ═══════════════════════════════════════════════════════════════
    # MAIN TICK — called by main.py every ~3-60s
    # ═══════════════════════════════════════════════════════════════

    def tick(self, spot: float, oi_snapshot: dict, premiums: dict, ts: datetime,
             fetcher=None) -> Optional[dict]:
        """
        Main entry point. Called every loop iteration.
        Returns action dict: {'action': 'entry'/'exit'/'partial_exit', ...} or None.
        """
        self.fetcher = fetcher

        # Update spot + EMA
        self.update_spot(spot, ts)

        # Update OI
        self.update_oi(oi_snapshot, ts)

        # Update premiums
        self.update_premiums(premiums, ts)

        # If in position, check exits first
        if self.position is not None:
            # Update peak OI
            current_oi = self.get_oi_delta(self.position["direction"], self.position["strikes"])
            if current_oi < self.peak_oi_covering_delta:
                self.peak_oi_covering_delta = current_oi

            # Check partial exit
            if self.check_partial_exit(ts):
                return {"action": "partial_exit", "position": self.position}

            # Check full exit
            exit_reason = self.check_exit(spot, ts, premiums)
            if exit_reason:
                return {"action": "exit", "reason": exit_reason, "position": self.position}

            return None  # hold

        # No position — check entry
        signal = self.check_entry(spot, ts, fetcher)
        if signal:
            return {"action": "entry", "signal": signal, "premiums": premiums}

        return None
