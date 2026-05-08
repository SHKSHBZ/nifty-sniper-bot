"""Vigilance layer — risk filters and regime self-diagnostics that run
on top of the strategy gates. Lightweight by design; uses only data
already kept in process memory (spot history, SignalEngine PCR/OI deque).
"""
from .regime_classifier import classify_regime, RegimeReading

__all__ = ["classify_regime", "RegimeReading"]
