"""
Regime-switching layer for the Nifty Sniper Bot.

Three components:
  - RegimeClassifier: labels each 5m bar with a market regime
  - StrategyRouter:   maps regime -> which tactic is armed
  - MasterRiskLayer:  per-trade, daily, and portfolio risk caps

All modules are dependency-light (only pandas + numpy) and side-effect free.
Production wiring into main.py / signal_engine.py is left to the caller; this
package is self-contained and safe to import into a backtest or a live run.
"""

from regime.classifier import RegimeClassifier, Regime, ClassifierFeatures
from regime.router import StrategyRouter, Tactic
from regime.master_risk import MasterRiskLayer, RiskDecision

__all__ = [
    "RegimeClassifier",
    "Regime",
    "ClassifierFeatures",
    "StrategyRouter",
    "Tactic",
    "MasterRiskLayer",
    "RiskDecision",
]
