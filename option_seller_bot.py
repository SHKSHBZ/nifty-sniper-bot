"""
Option Seller Bot — Theta Harvester
====================================
A standalone module that profits from Theta Decay through two modes:

1. VOLATILITY CRUSH MODE:
   Activated when the directional buyer bot is locked out (theta_lockdown).
   Sells ATM Straddles or OTM Strangles to harvest premium decay in
   sideways/choppy markets.

2. DIRECTIONAL HEDGING MODE:
   Activated simultaneously with the Option Buyer bot's directional trade.
   When the buyer goes Long CE, the Seller shorts the opposing PE
   (and vice versa). The losing side's premium decays rapidly, generating
   additional profit on top of the directional trade.

Risk Rules (per AGENTS.md):
   - Hard SL on combined premium spikes (e.g., premium rises 50% above entry)
   - Individual leg hard SL (e.g., any single leg doubles from entry)
   - Spot range breakout invalidation (exits if Spot breaks out of the
     defined sideways range, signalling the end of consolidation)
   - Force close at 15:15 IST
   - True Premium Ceiling: Maps the historical premium resistance at entry.
     If the premium of any sold leg approaches this ceiling, exit immediately.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from collections import deque

log = logging.getLogger("option_seller")


class OptionSellerBot:
    """
    Theta Harvester that profits from premium decay in two modes:
    Volatility Crush (standalone) and Directional Hedging (paired).
    """

    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION
    # ═══════════════════════════════════════════════════════════════

    def __init__(self, config: dict):
        strat = config.get("strategy", {})
        trading_idx = config.get("trading_index", "NIFTY")

        if trading_idx == "SENSEX":
            self.lot_size = int(strat.get("sensex_lot_size", 20))
            self.strike_step = int(strat.get("sensex_strike_step", 100))
        else:
            self.lot_size = int(strat.get("nifty_lot_size", 65))
            self.strike_step = int(strat.get("nifty_strike_step", 50))

        # ── Risk Parameters ──
        self.combined_sl_pct = float(config.get("seller_combined_sl_pct", 0.50))  # Exit if combined premium rises 50%
        self.single_leg_sl_pct = float(config.get("seller_leg_sl_pct", 1.00))     # Exit if any single leg doubles
        self.spot_breakout_pts = float(config.get("seller_spot_breakout_pts", 40)) # Exit if Spot breaks 40pts from range
        self.max_sell_trades = int(config.get("seller_max_trades_per_day", 2))
        self.force_close_time = config.get("seller_force_close_time", "15:15")

        # ── Position State ──
        self.sell_position: Optional[dict] = None
        self.hedge_position: Optional[dict] = None  # For Directional Hedging Mode
        self.sell_trades_today: int = 0
        self.daily_sell_pnl: float = 0.0

        # ── Tracking ──
        self._sideways_range_high: float = 0.0
        self._sideways_range_low: float = float('inf')
        self._recent_spots_seller = deque(maxlen=30)  # 30-tick window to define the range

        log.info(f"OptionSellerBot initialized | LotSize={self.lot_size} | "
                 f"StrikeStep={self.strike_step} | CombinedSL={self.combined_sl_pct*100}%")

    # ═══════════════════════════════════════════════════════════════
    # MODE 1: VOLATILITY CRUSH — SELL STRADDLE/STRANGLE
    # ═══════════════════════════════════════════════════════════════

    def check_crush_entry(self, spot: float, ts: datetime, premiums: dict,
                          is_theta_lockdown: bool, analyzer=None) -> Optional[dict]:
        """
        Called by the main engine loop when the directional buyer bot is 
        locked down due to Volatility Crush.
        
        Returns an entry signal dict if conditions are met, else None.
        """
        if not is_theta_lockdown:
            return None

        if self.sell_position is not None:
            return None  # Already in a sell position

        if self.sell_trades_today >= self.max_sell_trades:
            return None

        t_str = ts.strftime("%H:%M")
        if t_str >= self.force_close_time:
            return None  # Too late in the day

        # Track the sideways range
        self._recent_spots_seller.append(spot)
        if len(self._recent_spots_seller) < 10:
            return None  # Need at least 10 ticks to define the range

        self._sideways_range_high = max(self._recent_spots_seller)
        self._sideways_range_low = min(self._recent_spots_seller)
        range_width = self._sideways_range_high - self._sideways_range_low

        # Only sell if the range is truly tight (< 30 points = sideways chop)
        if range_width > 30:
            return None

        # Determine ATM strike
        atm = round(spot / self.strike_step) * self.strike_step

        # Strategy selection: Straddle (ATM) vs Strangle (OTM)
        # Use Strangle if range is very narrow (< 15 pts), else Straddle
        if range_width < 15:
            # Short Strangle: Sell OTM CE and OTM PE (1 step away)
            sell_ce_strike = atm + self.strike_step
            sell_pe_strike = atm - self.strike_step
            strategy = "SHORT_STRANGLE"
        else:
            # Short Straddle: Sell ATM CE and ATM PE
            sell_ce_strike = atm
            sell_pe_strike = atm
            strategy = "SHORT_STRADDLE"

        # Get premiums for the strikes we want to sell
        ce_key = f"{sell_ce_strike}_CE"
        pe_key = f"{sell_pe_strike}_PE"
        ce_premium = premiums.get(ce_key, 0)
        pe_premium = premiums.get(pe_key, 0)

        if ce_premium <= 0 or pe_premium <= 0:
            return None  # No valid premium data

        combined_premium = ce_premium + pe_premium

        # Map premium ceilings using analyzer if available
        ce_ceiling = 0.0
        pe_ceiling = 0.0
        if analyzer:
            ce_levels = analyzer.get_premium_historical_levels(sell_ce_strike, "CE")
            pe_levels = analyzer.get_premium_historical_levels(sell_pe_strike, "PE")
            ce_ceiling = ce_levels.get("resistance", ce_premium * 2)
            pe_ceiling = pe_levels.get("resistance", pe_premium * 2)

        return {
            "action": "sell_entry",
            "mode": "VOLATILITY_CRUSH",
            "strategy": strategy,
            "ce_strike": sell_ce_strike,
            "pe_strike": sell_pe_strike,
            "ce_premium": ce_premium,
            "pe_premium": pe_premium,
            "combined_premium": combined_premium,
            "ce_ceiling": ce_ceiling,
            "pe_ceiling": pe_ceiling,
            "spot": spot,
            "range_high": self._sideways_range_high,
            "range_low": self._sideways_range_low,
            "reason": f"{strategy}: Selling during Volatility Crush | "
                      f"Range={self._sideways_range_low:.0f}-{self._sideways_range_high:.0f} "
                      f"({range_width:.0f}pts) | "
                      f"CE@{ce_premium:.1f} + PE@{pe_premium:.1f} = {combined_premium:.1f}",
            "timestamp": ts.isoformat(),
        }

    # ═══════════════════════════════════════════════════════════════
    # MODE 2: DIRECTIONAL HEDGING — SHORT OPPOSING LEG
    # ═══════════════════════════════════════════════════════════════

    def open_directional_hedge(self, buyer_direction: str, buyer_strikes: list,
                               spot: float, ts: datetime, premiums: dict,
                               analyzer=None) -> Optional[dict]:
        """
        Called immediately when the directional buyer bot opens a trade.
        Shorts the opposing leg to capture theta decay on the losing side.
        
        Example: Buyer goes Long CE -> Seller shorts PE (decaying premium).
        """
        if self.hedge_position is not None:
            return None  # Already hedging

        atm = round(spot / self.strike_step) * self.strike_step

        if buyer_direction == "CE":
            # Buyer is bullish -> Short the PE (PE premium will decay as spot rises)
            hedge_direction = "PE"
            hedge_strike = atm + self.strike_step  # OTM PE for safety
        else:
            # Buyer is bearish -> Short the CE (CE premium will decay as spot falls)
            hedge_direction = "CE"
            hedge_strike = atm - self.strike_step  # OTM CE for safety

        hedge_key = f"{hedge_strike}_{hedge_direction}"
        hedge_premium = premiums.get(hedge_key, 0)

        if hedge_premium <= 0:
            return None

        # Map the premium ceiling (resistance) for the sold option
        hedge_ceiling = hedge_premium * 2  # Default: 2x
        if analyzer:
            levels = analyzer.get_premium_historical_levels(hedge_strike, hedge_direction)
            hedge_ceiling = levels.get("resistance", hedge_premium * 2)

        self.hedge_position = {
            "mode": "DIRECTIONAL_HEDGE",
            "hedge_direction": hedge_direction,
            "hedge_strike": hedge_strike,
            "hedge_key": hedge_key,
            "entry_premium": hedge_premium,
            "entry_spot": spot,
            "entry_time": ts,
            "premium_ceiling": hedge_ceiling,
            "buyer_direction": buyer_direction,
        }

        reason = (f"HEDGE SELL: Short {hedge_direction} {hedge_strike} @ {hedge_premium:.1f} "
                  f"(opposing Buyer's {buyer_direction} trade)")
        log.info(f"[Seller] {reason}")
        print(f"[Seller] {reason}")

        return {
            "action": "hedge_sell",
            "hedge_direction": hedge_direction,
            "hedge_strike": hedge_strike,
            "hedge_premium": hedge_premium,
            "premium_ceiling": hedge_ceiling,
            "reason": reason,
        }

    # ═══════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT — OPEN / CLOSE
    # ═══════════════════════════════════════════════════════════════

    def open_sell_position(self, signal: dict, ts: datetime):
        """Opens a Volatility Crush sell position (Straddle or Strangle)."""
        self.sell_position = {
            "mode": signal["mode"],
            "strategy": signal["strategy"],
            "ce_strike": signal["ce_strike"],
            "pe_strike": signal["pe_strike"],
            "entry_ce_premium": signal["ce_premium"],
            "entry_pe_premium": signal["pe_premium"],
            "entry_combined": signal["combined_premium"],
            "ce_ceiling": signal.get("ce_ceiling", signal["ce_premium"] * 2),
            "pe_ceiling": signal.get("pe_ceiling", signal["pe_premium"] * 2),
            "entry_spot": signal["spot"],
            "range_high": signal["range_high"],
            "range_low": signal["range_low"],
            "entry_time": ts,
            "peak_combined": signal["combined_premium"],  # Track the highest combined for TSL
        }
        self.sell_trades_today += 1

        log.info(f"[Seller] OPEN {signal['strategy']} | "
                 f"CE {signal['ce_strike']}@{signal['ce_premium']:.1f} | "
                 f"PE {signal['pe_strike']}@{signal['pe_premium']:.1f} | "
                 f"Combined={signal['combined_premium']:.1f}")

    def close_sell_position(self, reason: str, premiums: dict, ts: datetime) -> dict:
        """Closes the Volatility Crush sell position and calculates PnL."""
        pos = self.sell_position
        if pos is None:
            return {"total_pnl": 0.0}

        ce_key = f"{pos['ce_strike']}_CE"
        pe_key = f"{pos['pe_strike']}_PE"

        exit_ce = premiums.get(ce_key, pos["entry_ce_premium"])
        exit_pe = premiums.get(pe_key, pos["entry_pe_premium"])

        # Option Seller PnL: (Entry Premium - Exit Premium) * lots * lot_size
        # Profit when premium DECAYS (exit < entry)
        ce_pnl = (pos["entry_ce_premium"] - exit_ce) * 1 * self.lot_size
        pe_pnl = (pos["entry_pe_premium"] - exit_pe) * 1 * self.lot_size
        total_pnl = ce_pnl + pe_pnl

        self.daily_sell_pnl += total_pnl
        hold_mins = (ts - pos["entry_time"]).total_seconds() / 60

        result = {
            "mode": "VOLATILITY_CRUSH",
            "strategy": pos["strategy"],
            "ce_pnl": ce_pnl,
            "pe_pnl": pe_pnl,
            "total_pnl": total_pnl,
            "hold_minutes": hold_mins,
            "reason": reason,
            "entry_combined": pos["entry_combined"],
            "exit_combined": exit_ce + exit_pe,
        }

        log.info(f"[Seller] CLOSE {pos['strategy']} | {reason} | "
                 f"CE P&L={ce_pnl:+.0f} | PE P&L={pe_pnl:+.0f} | "
                 f"Total={total_pnl:+.0f} | Hold={hold_mins:.1f}m")
        print(f"[Seller] CLOSE {pos['strategy']} | P&L={total_pnl:+.0f} | {reason}")

        self.sell_position = None
        return result

    def close_hedge_position(self, premiums: dict, ts: datetime, reason: str = "") -> dict:
        """Closes the directional hedge position."""
        pos = self.hedge_position
        if pos is None:
            return {"total_pnl": 0.0}

        exit_premium = premiums.get(pos["hedge_key"], pos["entry_premium"])

        # Seller PnL: Entry - Exit (profit if premium dropped)
        pnl = (pos["entry_premium"] - exit_premium) * 1 * self.lot_size
        hold_mins = (ts - pos["entry_time"]).total_seconds() / 60

        if not reason:
            reason = "Buyer closed directional trade"

        result = {
            "mode": "DIRECTIONAL_HEDGE",
            "hedge_direction": pos["hedge_direction"],
            "hedge_strike": pos["hedge_strike"],
            "total_pnl": pnl,
            "hold_minutes": hold_mins,
            "reason": reason,
            "entry_premium": pos["entry_premium"],
            "exit_premium": exit_premium,
        }

        log.info(f"[Seller] CLOSE HEDGE {pos['hedge_direction']} {pos['hedge_strike']} | "
                 f"{reason} | P&L={pnl:+.0f}")
        print(f"[Seller] CLOSE HEDGE {pos['hedge_direction']} {pos['hedge_strike']} | P&L={pnl:+.0f}")

        self.daily_sell_pnl += pnl
        self.hedge_position = None
        return result

    # ═══════════════════════════════════════════════════════════════
    # RISK ENGINE — EXIT LOGIC
    # ═══════════════════════════════════════════════════════════════

    def check_sell_exit(self, spot: float, ts: datetime, premiums: dict) -> Optional[dict]:
        """
        Monitors the Volatility Crush sell position for exit conditions.
        Returns an exit signal if any risk threshold is breached.
        """
        if self.sell_position is None:
            return None

        pos = self.sell_position
        t_str = ts.strftime("%H:%M")

        # ── Force Close at 15:15 ──
        if t_str >= self.force_close_time:
            return {"action": "sell_exit", "reason": f"FORCE_CLOSE: {self.force_close_time}"}

        ce_key = f"{pos['ce_strike']}_CE"
        pe_key = f"{pos['pe_strike']}_PE"
        current_ce = premiums.get(ce_key, pos["entry_ce_premium"])
        current_pe = premiums.get(pe_key, pos["entry_pe_premium"])
        current_combined = current_ce + current_pe

        # Track the lowest combined premium (our profit target)
        if current_combined < pos.get("min_combined", pos["entry_combined"]):
            pos["min_combined"] = current_combined

        # ── 1. Combined Premium Spike SL ──
        # If combined premium has RISEN by more than X% from entry, we're losing
        entry_combined = pos["entry_combined"]
        if entry_combined > 0:
            combined_rise_pct = (current_combined - entry_combined) / entry_combined
            if combined_rise_pct > self.combined_sl_pct:
                return {
                    "action": "sell_exit",
                    "reason": f"COMBINED SL: Premium spiked {combined_rise_pct*100:.1f}% "
                              f"({entry_combined:.1f} -> {current_combined:.1f})"
                }

        # ── 2. Single Leg Explosion SL ──
        # If either CE or PE leg has individually spiked beyond threshold
        ce_rise = (current_ce - pos["entry_ce_premium"]) / max(pos["entry_ce_premium"], 1)
        pe_rise = (current_pe - pos["entry_pe_premium"]) / max(pos["entry_pe_premium"], 1)

        if ce_rise > self.single_leg_sl_pct:
            return {
                "action": "sell_exit",
                "reason": f"CE LEG SL: CE premium spiked {ce_rise*100:.1f}% "
                          f"({pos['entry_ce_premium']:.1f} -> {current_ce:.1f})"
            }
        if pe_rise > self.single_leg_sl_pct:
            return {
                "action": "sell_exit",
                "reason": f"PE LEG SL: PE premium spiked {pe_rise*100:.1f}% "
                          f"({pos['entry_pe_premium']:.1f} -> {current_pe:.1f})"
            }

        # ── 3. Premium Ceiling Breach (True Premium Resistance) ──
        if pos.get("ce_ceiling") and current_ce >= pos["ce_ceiling"]:
            return {
                "action": "sell_exit",
                "reason": f"CE CEILING BREACH: CE hit structural resistance {pos['ce_ceiling']:.1f}"
            }
        if pos.get("pe_ceiling") and current_pe >= pos["pe_ceiling"]:
            return {
                "action": "sell_exit",
                "reason": f"PE CEILING BREACH: PE hit structural resistance {pos['pe_ceiling']:.1f}"
            }

        # ── 4. Spot Range Breakout Invalidation ──
        # If Spot breaks out of the sideways range, volatility is expanding -> exit
        range_high = pos.get("range_high", spot + 100)
        range_low = pos.get("range_low", spot - 100)

        if spot > range_high + self.spot_breakout_pts:
            return {
                "action": "sell_exit",
                "reason": f"BREAKOUT UP: Spot {spot:.1f} broke above range "
                          f"{range_high:.1f} + {self.spot_breakout_pts}pts"
            }
        if spot < range_low - self.spot_breakout_pts:
            return {
                "action": "sell_exit",
                "reason": f"BREAKDOWN: Spot {spot:.1f} broke below range "
                          f"{range_low:.1f} - {self.spot_breakout_pts}pts"
            }

        # ── 5. Take Profit: Premium decayed by 40%+ ──
        if entry_combined > 0:
            decay_pct = (entry_combined - current_combined) / entry_combined
            if decay_pct >= 0.40:
                return {
                    "action": "sell_exit",
                    "reason": f"TP: Combined premium decayed {decay_pct*100:.1f}% "
                              f"({entry_combined:.1f} -> {current_combined:.1f})"
                }

        return None

    def check_hedge_exit(self, spot: float, ts: datetime, premiums: dict) -> Optional[dict]:
        """
        Monitors the directional hedge for exit conditions.
        The hedge is usually closed when the buyer closes their position,
        but this provides independent risk protection.
        """
        if self.hedge_position is None:
            return None

        pos = self.hedge_position
        t_str = ts.strftime("%H:%M")

        if t_str >= self.force_close_time:
            return {"action": "hedge_exit", "reason": f"FORCE_CLOSE: {self.force_close_time}"}

        current_premium = premiums.get(pos["hedge_key"], pos["entry_premium"])

        # ── Premium spike SL ──
        entry_prem = pos["entry_premium"]
        if entry_prem > 0:
            rise_pct = (current_premium - entry_prem) / entry_prem
            if rise_pct > self.single_leg_sl_pct:
                return {
                    "action": "hedge_exit",
                    "reason": f"HEDGE SL: {pos['hedge_direction']} premium spiked {rise_pct*100:.1f}% "
                              f"({entry_prem:.1f} -> {current_premium:.1f})"
                }

        # ── Premium ceiling breach ──
        if pos.get("premium_ceiling") and current_premium >= pos["premium_ceiling"]:
            return {
                "action": "hedge_exit",
                "reason": f"HEDGE CEILING: {pos['hedge_direction']} hit structural resistance "
                          f"{pos['premium_ceiling']:.1f}"
            }

        # ── Take Profit: Premium decayed by 50%+ ──
        if entry_prem > 0:
            decay_pct = (entry_prem - current_premium) / entry_prem
            if decay_pct >= 0.50:
                return {
                    "action": "hedge_exit",
                    "reason": f"HEDGE TP: Premium decayed {decay_pct*100:.1f}% "
                              f"({entry_prem:.1f} -> {current_premium:.1f})"
                }

        return None

    # ═══════════════════════════════════════════════════════════════
    # DAILY RESET
    # ═══════════════════════════════════════════════════════════════

    def reset_daily(self):
        """Reset all daily counters for a new trading session."""
        self.sell_position = None
        self.hedge_position = None
        self.sell_trades_today = 0
        self.daily_sell_pnl = 0.0
        self._sideways_range_high = 0.0
        self._sideways_range_low = float('inf')
        self._recent_spots_seller.clear()
        log.info("[Seller] Daily state reset.")
