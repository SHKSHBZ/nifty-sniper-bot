"""
StrategyRouter — maps a Regime to the tactic (and option direction) that should
be armed, and tells the caller whether an open position needs forced exit
because the regime flipped against it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from regime.classifier import Regime


class Tactic(str, Enum):
    OI_WALL_MEAN_REVERSION = "OI_WALL_MEAN_REVERSION"          # existing bot
    OI_TREND_PULLBACK = "OI_TREND_PULLBACK"                    # JSON #3 fixed
    BULLISH_LAUNCHPAD = "BULLISH_LAUNCHPAD"                    # JSON #4 fixed
    BEARISH_LAUNCHPAD = "BEARISH_LAUNCHPAD"                    # mirror
    DEBIT_SPREAD = "DEBIT_SPREAD"                              # spread_executor.py
    NO_TRADE = "NO_TRADE"


@dataclass
class RouteDecision:
    tactic: Tactic
    direction: Optional[str]        # "CE" | "PE" | None
    reason: str
    force_exit_open_positions: bool = False


# Fixed mapping table — keep in sync with strategy_regime_master.json
_ROUTE_TABLE: dict[Regime, RouteDecision] = {
    Regime.TREND_UP_GAP:   RouteDecision(Tactic.BULLISH_LAUNCHPAD, "CE", "gap-up ORB"),
    Regime.TREND_DOWN_GAP: RouteDecision(Tactic.BEARISH_LAUNCHPAD, "PE", "gap-down ORB"),
    Regime.TREND_UP:       RouteDecision(Tactic.OI_TREND_PULLBACK, "CE", "trend day, pullback entry"),
    Regime.TREND_DOWN:     RouteDecision(Tactic.OI_TREND_PULLBACK, "PE", "trend day, pullback entry"),
    Regime.RANGE:          RouteDecision(Tactic.OI_WALL_MEAN_REVERSION, None, "range day, fade OI walls"),
    Regime.EXPIRY:         RouteDecision(Tactic.OI_WALL_MEAN_REVERSION, None, "0-DTE, tight params via OI walls"),
    Regime.CHOP:           RouteDecision(Tactic.NO_TRADE, None, "chop — sit out"),
    Regime.NO_TRADE:       RouteDecision(Tactic.NO_TRADE, None, "event/VIX halt"),
    Regime.WAIT:           RouteDecision(Tactic.NO_TRADE, None, "gap day, awaiting morning lock"),
}

# Which open-position directions are *incompatible* with each new regime.
# If the open direction is in the set, the router will flag force_exit.
_HOSTILE_TO_OPEN = {
    Regime.TREND_UP_GAP:   {"PE"},
    Regime.TREND_DOWN_GAP: {"CE"},
    Regime.TREND_UP:       {"PE"},
    Regime.TREND_DOWN:     {"CE"},
    Regime.NO_TRADE:       {"CE", "PE"},   # flatten everything
    # RANGE / EXPIRY / CHOP / WAIT are neutral — don't force exit
}


class StrategyRouter:
    """
    Pure function over (regime, open_position_direction).
    No state of its own — the classifier already holds regime state.
    """

    def route(
        self,
        regime: Regime,
        open_direction: Optional[str] = None,
    ) -> RouteDecision:
        base = _ROUTE_TABLE.get(regime, _ROUTE_TABLE[Regime.NO_TRADE])

        force_exit = False
        if open_direction is not None:
            hostile = _HOSTILE_TO_OPEN.get(regime, set())
            if open_direction in hostile:
                force_exit = True

        return RouteDecision(
            tactic=base.tactic,
            direction=base.direction,
            reason=base.reason,
            force_exit_open_positions=force_exit,
        )
