"""
Tactic implementations for the regime-switching system.

Each tactic is a self-contained class that exposes an `evaluate(state)`
method returning a TacticSignal (or None if the setup isn't present).
The backtester / live engine calls `evaluate` once per 5-minute bar and
acts on returned signals.

Modules:
    base                 — Shared types: TacticState, TacticSignal, Tactic ABC
    vwap_hybrid          — VWAP mean-reversion with OI-wall confluence
    trend_pullback       — Pullback-to-EMA9 trend follower (CE + PE sides)
    bullish_orb          — Gap-up Opening Range Breakout
    bearish_orb          — Gap-down Opening Range Breakdown (mirror)

All tactics consume the same TacticState dict and return the same
TacticSignal shape, so the simulator and live engine can be tactic-
agnostic.
"""

from tactics.base import (
    TacticState,
    TacticSignal,
    TacticConfig,
    Tactic,
)
from tactics.vwap_hybrid import VWAPHybridTactic
from tactics.trend_pullback import TrendPullbackTactic
from tactics.bullish_orb import BullishORBTactic
from tactics.bearish_orb import BearishORBTactic
from tactics.ief import IEFTactic, IEFConfig, IEFAnalyzer

__all__ = [
    "TacticState",
    "TacticSignal",
    "TacticConfig",
    "Tactic",
    "VWAPHybridTactic",
    "TrendPullbackTactic",
    "BullishORBTactic",
    "BearishORBTactic",
    "IEFTactic",
    "IEFConfig",
    "IEFAnalyzer",
]
