"""
seller_tactics.py — Short Premium Strategy Tactics for Option Seller Bot.

Implements:
  - ShortStraddleTactic: Selling ATM Call + Put.
  - ShortStrangleTactic: Selling OTM Call + Put with configurable step offsets.
  - Dynamic strike selection helper functions based on live spot price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from tactics.base import Tactic, TacticConfig, TacticSignal, TacticState

log = logging.getLogger(__name__)


def get_atm_strike(spot: float, step: int = 50) -> int:
    """Calculate dynamic ATM strike rounded to nearest step."""
    if step <= 0:
        step = 50
    return int(round(spot / step) * step)


def get_straddle_strikes(spot: float, step: int = 50) -> tuple[int, int]:
    """Return (ATM_CE_strike, ATM_PE_strike) for Short Straddle."""
    atm = get_atm_strike(spot, step)
    return atm, atm


def get_strangle_strikes(spot: float, step: int = 50, otm_steps: int = 2) -> tuple[int, int]:
    """Return (OTM_CE_strike, OTM_PE_strike) for Short Strangle."""
    atm = get_atm_strike(spot, step)
    ce_strike = atm + (otm_steps * step)
    pe_strike = atm - (otm_steps * step)
    return ce_strike, pe_strike


@dataclass
class ShortStraddleConfig(TacticConfig):
    name: str = "short_straddle"
    strike_step: int = 50
    combined_sl_pct: float = 0.30     # 30% SL on combined premium
    combined_tp_pct: float = 0.50     # 50% TP on combined premium
    time_stop_min: int = 120
    is_short: bool = True


class ShortStraddleTactic(Tactic):
    """
    Short Straddle Tactic: Sells ATM Call + Put simultaneously during
    VOLATILITY_CRUSH or sideways regimes to harvest theta decay.
    """

    def __init__(self, config: Optional[ShortStraddleConfig] = None):
        cfg = config or ShortStraddleConfig()
        super().__init__(cfg)
        self.config: ShortStraddleConfig = cfg

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        if not self.config.enabled:
            return None

        # Session time window check
        if not self.in_session_window(state):
            return None

        # Do not enter if already in position
        if state.is_in_position:
            return None

        spot = state.spot
        if spot <= 0:
            return None

        step = self.config.strike_step
        atm_strike = get_atm_strike(spot, step)

        return TacticSignal(
            action="enter",
            direction="CE",
            strike_offset=0,
            second_direction="PE",
            second_strike_offset=0,
            sl_pct=self.config.combined_sl_pct,
            tp_pct=self.config.combined_tp_pct,
            combined_sl_pct=self.config.combined_sl_pct,
            combined_tp_pct=self.config.combined_tp_pct,
            time_stop_min=self.config.time_stop_min,
            reason=f"Short Straddle ATM ({atm_strike}) [decay/crush]",
        )


@dataclass
class ShortStrangleConfig(TacticConfig):
    name: str = "short_strangle"
    strike_step: int = 50
    otm_steps: int = 2                 # default +2 / -2 steps OTM (e.g. +100 / -100 pts)
    combined_sl_pct: float = 0.30     # 30% SL on combined premium
    combined_tp_pct: float = 0.50     # 50% TP on combined premium
    time_stop_min: int = 120
    is_short: bool = True


class ShortStrangleTactic(Tactic):
    """
    Short Strangle Tactic: Sells OTM Call + Put simultaneously with configurable
    step offsets to harvest premium with wider safety buffer.
    """

    def __init__(self, config: Optional[ShortStrangleConfig] = None):
        cfg = config or ShortStrangleConfig()
        super().__init__(cfg)
        self.config: ShortStrangleConfig = cfg

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        if not self.config.enabled:
            return None

        # Session time window check
        if not self.in_session_window(state):
            return None

        # Do not enter if already in position
        if state.is_in_position:
            return None

        spot = state.spot
        if spot <= 0:
            return None

        step = self.config.strike_step
        otm_steps = self.config.otm_steps
        ce_strike, pe_strike = get_strangle_strikes(spot, step, otm_steps)

        return TacticSignal(
            action="enter",
            direction="CE",
            strike_offset=otm_steps,
            second_direction="PE",
            second_strike_offset=-otm_steps,
            sl_pct=self.config.combined_sl_pct,
            tp_pct=self.config.combined_tp_pct,
            combined_sl_pct=self.config.combined_sl_pct,
            combined_tp_pct=self.config.combined_tp_pct,
            time_stop_min=self.config.time_stop_min,
            reason=f"Short Strangle OTM (CE: {ce_strike}, PE: {pe_strike}) [decay/crush]",
        )
