"""
seller_risk.py — Option Seller Bot Risk Management & Premium Floor Engine (Milestone 2).

Responsibilities:
  1. OptionSellerRiskManager:
     - Individual & Combined Leg Premium Spike Hard Stop Loss.
     - Pillar 4 Structural Resistance Ceiling Breach Exit (via PremiumAnalyzer).
     - Spot Range Breakout Invalidation (force-close on spot range high/low breakout).
  2. TruePremiumFloorTracker (AGENTS.md Rule 3):
     - Maps True Premium Floor (support) and Ceiling (resistance) at trade entry via PremiumAnalyzer.
     - Tracks `lowest_seen_premium` during trade lifecycle.
     - Dynamic trailing stop-loss (LTP >= lowest_seen * (1 + trail_pct)) locking theta decay profits.
  3. 15-Minute Straddle Decay Chop Filter (AGENTS.md Rule 4):
     - Monitors 15-minute rolling straddle decay vs spot range bounds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

log = logging.getLogger(__name__)


class TruePremiumFloorTracker:
    """
    Tracks True Premium Floor (historical support) and Ceiling (historical resistance)
    for option seller legs (AGENTS.md Rule 3), along with lowest_seen_premium and
    dynamic trailing stop-loss for theta decay profit locking.
    """

    def __init__(
        self,
        premium_analyzer: Optional[Any] = None,
        trail_sl_pct: float = 0.15,
    ):
        self.premium_analyzer = premium_analyzer
        self.trail_sl_pct: float = trail_sl_pct
        self.strike: Optional[int] = None
        self.opt_type: Optional[str] = None
        self.entry_premium: float = 0.0
        self.floor_premium: Optional[float] = None
        self.ceiling_premium: Optional[float] = None
        self.lowest_seen_premium: float = float("inf")

    def initialize_position(
        self,
        strike: int,
        opt_type: str,
        entry_premium: float,
        floor_override: Optional[float] = None,
        ceiling_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        At trade entry, map True Premium Floor (historical lowest traded premium for that structure)
        and Ceiling (resistance) via PremiumAnalyzer or explicit overrides.
        """
        self.strike = strike
        self.opt_type = opt_type.upper()
        self.entry_premium = max(entry_premium, 0.001)
        self.lowest_seen_premium = self.entry_premium

        # Query PremiumAnalyzer if available
        if floor_override is not None or ceiling_override is not None:
            self.floor_premium = floor_override
            self.ceiling_premium = ceiling_override
        elif self.premium_analyzer is not None:
            try:
                levels = self.premium_analyzer.get_premium_historical_levels(strike, self.opt_type)
                self.floor_premium = levels.get("support")
                self.ceiling_premium = levels.get("resistance")
            except Exception as err:
                log.warning("Failed to query PremiumAnalyzer for strike %s %s: %s", strike, opt_type, err)
                self.floor_premium = None
                self.ceiling_premium = None
        else:
            self.floor_premium = None
            self.ceiling_premium = None

        return {
            "strike": self.strike,
            "opt_type": self.opt_type,
            "entry_premium": self.entry_premium,
            "floor_premium": self.floor_premium,
            "ceiling_premium": self.ceiling_premium,
            "lowest_seen_premium": self.lowest_seen_premium,
        }

    def update_ltp(self, ltp: float) -> Dict[str, Any]:
        """
        Update tracker state with live option LTP.
        Returns evaluation dict containing:
          - lowest_seen_premium
          - trailing_sl_price
          - trailing_sl_triggered
          - ceiling_breached
          - floor_breached
        """
        if ltp < self.lowest_seen_premium:
            self.lowest_seen_premium = ltp

        # Dynamic trailing stop-loss for option seller:
        # Option premium decays (drops) from entry_premium.
        # Trailing stop price = lowest_seen_premium * (1 + trail_sl_pct).
        # Triggered when LTP expands back up to or above trailing stop price after decay.
        trailing_sl_price = self.lowest_seen_premium * (1.0 + self.trail_sl_pct)
        
        # Trailing SL is only active once premium has decayed below entry premium
        trailing_sl_triggered = (
            self.lowest_seen_premium < self.entry_premium and ltp >= trailing_sl_price
        )

        # Pillar 4 Structural Resistance Ceiling breach: short leg LTP expands beyond historical ceiling
        ceiling_breached = False
        if self.ceiling_premium is not None and self.ceiling_premium > 0:
            ceiling_breached = ltp >= self.ceiling_premium

        # Structural Support / Floor breach: premium breaks below support floor
        floor_breached = False
        if self.floor_premium is not None and self.floor_premium > 0:
            floor_breached = ltp < self.floor_premium

        return {
            "lowest_seen_premium": self.lowest_seen_premium,
            "trailing_sl_price": trailing_sl_price,
            "trailing_sl_triggered": trailing_sl_triggered,
            "ceiling_breached": ceiling_breached,
            "floor_breached": floor_breached,
        }


@dataclass
class SellerPositionState:
    position_id: str
    symbol: str = "NIFTY"
    strategy_type: str = "short_straddle"   # "short_leg", "short_straddle", "short_strangle"
    entry_spot: float = 0.0
    spot_range_high: float = 0.0
    spot_range_low: float = 0.0
    entry_credit: float = 0.0               # Total credit received across legs
    ce_strike: Optional[int] = None
    ce_entry_premium: float = 0.0
    pe_strike: Optional[int] = None
    pe_entry_premium: float = 0.0
    single_leg_strike: Optional[int] = None
    single_leg_type: Optional[str] = None   # "CE" or "PE"
    single_leg_entry_premium: float = 0.0
    leg_sl_pct: float = 0.30                # Individual leg spike SL % (e.g. +30%)
    combined_sl_pct: float = 0.30           # Combined straddle/strangle SL %
    trail_sl_pct: float = 0.15              # Trailing stop % on lowest_seen_premium
    ce_floor_tracker: Optional[TruePremiumFloorTracker] = None
    pe_floor_tracker: Optional[TruePremiumFloorTracker] = None
    single_floor_tracker: Optional[TruePremiumFloorTracker] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True


@dataclass
class RiskEvaluationResult:
    should_exit: bool
    exit_reason: Optional[str] = None
    exit_legs: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class OptionSellerRiskManager:
    """
    Strict Risk Manager for Option Seller strategies enforcing Requirement R3 & AGENTS.md Rules 3 & 4.
    """

    def __init__(
        self,
        premium_analyzer: Optional[Any] = None,
        default_leg_sl_pct: float = 0.30,
        default_combined_sl_pct: float = 0.30,
        default_trail_sl_pct: float = 0.15,
        default_spot_range_pts: float = 30.0,
    ):
        self.premium_analyzer = premium_analyzer
        self.default_leg_sl_pct = default_leg_sl_pct
        self.default_combined_sl_pct = default_combined_sl_pct
        self.default_trail_sl_pct = default_trail_sl_pct
        self.default_spot_range_pts = default_spot_range_pts
        self.active_positions: Dict[str, SellerPositionState] = {}

    def register_position(
        self,
        position_id: str,
        strategy_type: str,
        spot: float,
        ce_strike: Optional[int] = None,
        ce_entry_premium: float = 0.0,
        pe_strike: Optional[int] = None,
        pe_entry_premium: float = 0.0,
        single_leg_strike: Optional[int] = None,
        single_leg_type: Optional[str] = None,
        single_leg_entry_premium: float = 0.0,
        spot_range_high: Optional[float] = None,
        spot_range_low: Optional[float] = None,
        leg_sl_pct: Optional[float] = None,
        combined_sl_pct: Optional[float] = None,
        trail_sl_pct: Optional[float] = None,
        ce_ceiling_override: Optional[float] = None,
        pe_ceiling_override: Optional[float] = None,
        single_ceiling_override: Optional[float] = None,
        symbol: str = "NIFTY",
    ) -> SellerPositionState:
        """
        Register a new short option position (straddle, strangle, or single leg hedge) and set up risk trackers.
        """
        range_pts = self.default_spot_range_pts
        high_bound = spot_range_high if spot_range_high is not None else spot + range_pts
        low_bound = spot_range_low if spot_range_low is not None else spot - range_pts

        l_sl_pct = leg_sl_pct if leg_sl_pct is not None else self.default_leg_sl_pct
        c_sl_pct = combined_sl_pct if combined_sl_pct is not None else self.default_combined_sl_pct
        t_sl_pct = trail_sl_pct if trail_sl_pct is not None else self.default_trail_sl_pct

        pos = SellerPositionState(
            position_id=position_id,
            symbol=symbol,
            strategy_type=strategy_type,
            entry_spot=spot,
            spot_range_high=high_bound,
            spot_range_low=low_bound,
            ce_strike=ce_strike,
            ce_entry_premium=ce_entry_premium,
            pe_strike=pe_strike,
            pe_entry_premium=pe_entry_premium,
            single_leg_strike=single_leg_strike,
            single_leg_type=single_leg_type.upper() if single_leg_type else None,
            single_leg_entry_premium=single_leg_entry_premium,
            leg_sl_pct=l_sl_pct,
            combined_sl_pct=c_sl_pct,
            trail_sl_pct=t_sl_pct,
        )

        if strategy_type in ("short_straddle", "short_strangle"):
            pos.entry_credit = ce_entry_premium + pe_entry_premium
            if ce_strike is not None:
                pos.ce_floor_tracker = TruePremiumFloorTracker(
                    premium_analyzer=self.premium_analyzer,
                    trail_sl_pct=t_sl_pct,
                )
                pos.ce_floor_tracker.initialize_position(
                    strike=ce_strike,
                    opt_type="CE",
                    entry_premium=ce_entry_premium,
                    ceiling_override=ce_ceiling_override,
                )

            if pe_strike is not None:
                pos.pe_floor_tracker = TruePremiumFloorTracker(
                    premium_analyzer=self.premium_analyzer,
                    trail_sl_pct=t_sl_pct,
                )
                pos.pe_floor_tracker.initialize_position(
                    strike=pe_strike,
                    opt_type="PE",
                    entry_premium=pe_entry_premium,
                    ceiling_override=pe_ceiling_override,
                )
        else:
            pos.entry_credit = single_leg_entry_premium
            if single_leg_strike is not None and single_leg_type:
                pos.single_floor_tracker = TruePremiumFloorTracker(
                    premium_analyzer=self.premium_analyzer,
                    trail_sl_pct=t_sl_pct,
                )
                pos.single_floor_tracker.initialize_position(
                    strike=single_leg_strike,
                    opt_type=single_leg_type,
                    entry_premium=single_leg_entry_premium,
                    ceiling_override=single_ceiling_override,
                )

        self.active_positions[position_id] = pos
        log.info(
            "Registered %s position '%s': spot=%.1f range=[%.1f, %.1f] credit=%.2f",
            strategy_type, position_id, spot, low_bound, high_bound, pos.entry_credit
        )
        return pos

    def get_position(self, position_id: str) -> Optional[SellerPositionState]:
        return self.active_positions.get(position_id)

    def close_position(self, position_id: str) -> bool:
        pos = self.active_positions.get(position_id)
        if pos:
            pos.is_active = False
            del self.active_positions[position_id]
            log.info("Closed position '%s'", position_id)
            return True
        return False

    def evaluate_position(
        self,
        position_id: str,
        live_spot: float,
        ce_ltp: float = 0.0,
        pe_ltp: float = 0.0,
        single_leg_ltp: float = 0.0,
    ) -> RiskEvaluationResult:
        """
        Evaluates risk for an active seller position.
        Checks in strict order:
          1. Spot Range Breakout Invalidation (Spot > SpotRange_High or Spot < SpotRange_Low).
          2. Combined Premium Spike Hard SL (CE_LTP + PE_LTP >= EntryCredit * (1 + CombinedSL_pct)).
          3. Leg Premium Spike Hard SL (LTP >= LegEntry * (1 + LegSL_pct)).
          4. Pillar 4 Structural Resistance Ceiling Breach Exit (LTP >= HistoricalCeiling).
          5. Dynamic Trailing Stop Loss on lowest_seen_premium (LTP >= LowestSeen * (1 + TrailSL_pct)).
        """
        pos = self.active_positions.get(position_id)
        if not pos or not pos.is_active:
            return RiskEvaluationResult(should_exit=False)

        details: Dict[str, Any] = {
            "position_id": position_id,
            "live_spot": live_spot,
            "spot_range_high": pos.spot_range_high,
            "spot_range_low": pos.spot_range_low,
        }

        # -----------------------------------------------------------------
        # 1. Spot Range Breakout Invalidation (per Requirement R3 & AC)
        # -----------------------------------------------------------------
        if live_spot > pos.spot_range_high:
            log.warning(
                "Risk Trigger [SPOT_RANGE_BREAKOUT_HIGH]: live_spot (%.2f) > high_bound (%.2f)",
                live_spot, pos.spot_range_high
            )
            return RiskEvaluationResult(
                should_exit=True,
                exit_reason="SPOT_RANGE_BREAKOUT_HIGH",
                exit_legs=["CE", "PE"] if pos.strategy_type in ("short_straddle", "short_strangle") else [pos.single_leg_type or "ALL"],
                details={**details, "breakout_direction": "UP", "diff": live_spot - pos.spot_range_high},
            )

        if live_spot < pos.spot_range_low:
            log.warning(
                "Risk Trigger [SPOT_RANGE_BREAKOUT_LOW]: live_spot (%.2f) < low_bound (%.2f)",
                live_spot, pos.spot_range_low
            )
            return RiskEvaluationResult(
                should_exit=True,
                exit_reason="SPOT_RANGE_BREAKOUT_LOW",
                exit_legs=["CE", "PE"] if pos.strategy_type in ("short_straddle", "short_strangle") else [pos.single_leg_type or "ALL"],
                details={**details, "breakout_direction": "DOWN", "diff": pos.spot_range_low - live_spot},
            )

        # Multi-leg strategies (Straddle / Strangle)
        if pos.strategy_type in ("short_straddle", "short_strangle"):
            combined_ltp = ce_ltp + pe_ltp
            combined_max_allowed = pos.entry_credit * (1.0 + pos.combined_sl_pct)

            details.update({
                "ce_ltp": ce_ltp,
                "pe_ltp": pe_ltp,
                "combined_ltp": combined_ltp,
                "entry_credit": pos.entry_credit,
                "combined_max_allowed": combined_max_allowed,
            })

            # -----------------------------------------------------------------
            # 2. Combined Premium Spike Hard SL
            # -----------------------------------------------------------------
            if pos.entry_credit > 0 and combined_ltp >= combined_max_allowed:
                log.warning(
                    "Risk Trigger [COMBINED_PREMIUM_SPIKE]: combined_ltp (%.2f) >= max_allowed (%.2f)",
                    combined_ltp, combined_max_allowed
                )
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason="COMBINED_PREMIUM_SPIKE",
                    exit_legs=["CE", "PE"],
                    details=details,
                )

            # -----------------------------------------------------------------
            # 3. Leg Premium Spike Hard SL
            # -----------------------------------------------------------------
            ce_max_allowed = pos.ce_entry_premium * (1.0 + pos.leg_sl_pct)
            if pos.ce_entry_premium > 0 and ce_ltp >= ce_max_allowed:
                log.warning(
                    "Risk Trigger [LEG_PREMIUM_SPIKE_CE]: ce_ltp (%.2f) >= max_allowed (%.2f)",
                    ce_ltp, ce_max_allowed
                )
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason="LEG_PREMIUM_SPIKE_CE",
                    exit_legs=["CE"],
                    details=details,
                )

            pe_max_allowed = pos.pe_entry_premium * (1.0 + pos.leg_sl_pct)
            if pos.pe_entry_premium > 0 and pe_ltp >= pe_max_allowed:
                log.warning(
                    "Risk Trigger [LEG_PREMIUM_SPIKE_PE]: pe_ltp (%.2f) >= max_allowed (%.2f)",
                    pe_ltp, pe_max_allowed
                )
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason="LEG_PREMIUM_SPIKE_PE",
                    exit_legs=["PE"],
                    details=details,
                )

            # Update True Premium Floor & Trailing SL Trackers
            ce_res = pos.ce_floor_tracker.update_ltp(ce_ltp) if pos.ce_floor_tracker else {}
            pe_res = pos.pe_floor_tracker.update_ltp(pe_ltp) if pos.pe_floor_tracker else {}

            details.update({
                "ce_tracker": ce_res,
                "pe_tracker": pe_res,
            })

            # -----------------------------------------------------------------
            # 4. Pillar 4 Structural Resistance Ceiling Breach Exit
            # -----------------------------------------------------------------
            if ce_res.get("ceiling_breached"):
                log.warning(
                    "Risk Trigger [CEILING_BREACH_CE]: CE LTP (%.2f) breached ceiling (%.2f)",
                    ce_ltp, pos.ce_floor_tracker.ceiling_premium if pos.ce_floor_tracker else 0.0
                )
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason="CEILING_BREACH_CE",
                    exit_legs=["CE"],
                    details=details,
                )

            if pe_res.get("ceiling_breached"):
                log.warning(
                    "Risk Trigger [CEILING_BREACH_PE]: PE LTP (%.2f) breached ceiling (%.2f)",
                    pe_ltp, pos.pe_floor_tracker.ceiling_premium if pos.pe_floor_tracker else 0.0
                )
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason="CEILING_BREACH_PE",
                    exit_legs=["PE"],
                    details=details,
                )

            # -----------------------------------------------------------------
            # 5. True Premium Floor Tracking & Dynamic Trailing Stop
            # -----------------------------------------------------------------
            if ce_res.get("trailing_sl_triggered"):
                log.info("Risk Trigger [TRAILING_STOP_CE]: CE LTP (%.2f) hit trailing SL (%.2f)", ce_ltp, ce_res.get("trailing_sl_price"))
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason="TRAILING_STOP_CE",
                    exit_legs=["CE"],
                    details=details,
                )

            if pe_res.get("trailing_sl_triggered"):
                log.info("Risk Trigger [TRAILING_STOP_PE]: PE LTP (%.2f) hit trailing SL (%.2f)", pe_ltp, pe_res.get("trailing_sl_price"))
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason="TRAILING_STOP_PE",
                    exit_legs=["PE"],
                    details=details,
                )

        # Single Leg Position (e.g. Directional Hedge Short Leg)
        else:
            single_max_allowed = pos.single_leg_entry_premium * (1.0 + pos.leg_sl_pct)
            leg_label = pos.single_leg_type or "SINGLE"

            details.update({
                "single_leg_ltp": single_leg_ltp,
                "single_leg_entry_premium": pos.single_leg_entry_premium,
                "single_max_allowed": single_max_allowed,
            })

            # Leg Premium Spike Hard SL
            if pos.single_leg_entry_premium > 0 and single_leg_ltp >= single_max_allowed:
                log.warning(
                    "Risk Trigger [LEG_PREMIUM_SPIKE_%s]: single_ltp (%.2f) >= max_allowed (%.2f)",
                    leg_label, single_leg_ltp, single_max_allowed
                )
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason=f"LEG_PREMIUM_SPIKE_{leg_label}",
                    exit_legs=[leg_label],
                    details=details,
                )

            # Floor / Ceiling / Trailing Stop Tracker
            single_res = pos.single_floor_tracker.update_ltp(single_leg_ltp) if pos.single_floor_tracker else {}
            details["single_tracker"] = single_res

            # Pillar 4 Resistance Ceiling Breach
            if single_res.get("ceiling_breached"):
                log.warning("Risk Trigger [CEILING_BREACH_%s]: single LTP (%.2f) breached ceiling", leg_label, single_leg_ltp)
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason=f"CEILING_BREACH_{leg_label}",
                    exit_legs=[leg_label],
                    details=details,
                )

            # Trailing Stop
            if single_res.get("trailing_sl_triggered"):
                log.info("Risk Trigger [TRAILING_STOP_%s]: single LTP (%.2f) hit trailing SL", leg_label, single_leg_ltp)
                return RiskEvaluationResult(
                    should_exit=True,
                    exit_reason=f"TRAILING_STOP_{leg_label}",
                    exit_legs=[leg_label],
                    details=details,
                )

        return RiskEvaluationResult(should_exit=False, details=details)

    def evaluate_straddle_decay_chop(
        self,
        recent_straddle_prems: List[float],
        recent_spots: List[float],
        threshold_decay_pct: float = 0.03,
        max_spot_range_pts: float = 30.0,
    ) -> Dict[str, Any]:
        """
        AGENTS.md Rule 4: Evaluates 15-minute rolling straddle premium decay vs spot range.
        If straddle decays > 3% while spot remains range-bound (<= 30 pts), signals Volatility Crush / Chop.
        """
        if len(recent_straddle_prems) < 2 or len(recent_spots) < 2:
            return {"is_volatility_crush": False, "decay_pct": 0.0, "spot_range": 0.0}

        spot_min, spot_max = min(recent_spots), max(recent_spots)
        spot_range = spot_max - spot_min

        peak_straddle = max(recent_straddle_prems)
        current_straddle = recent_straddle_prems[-1]

        decay_pct = (peak_straddle - current_straddle) / max(peak_straddle, 1.0)
        is_vol_crush = decay_pct >= threshold_decay_pct and spot_range <= max_spot_range_pts

        return {
            "is_volatility_crush": is_vol_crush,
            "decay_pct": decay_pct,
            "spot_range": spot_range,
            "peak_straddle": peak_straddle,
            "current_straddle": current_straddle,
        }
