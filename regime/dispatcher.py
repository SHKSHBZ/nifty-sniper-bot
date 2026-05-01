"""
TacticDispatcher — the single entry-point the live bot calls instead of
SignalEngine.evaluate(...) directly.

Responsibilities:
    1. Build a regime-classifier feature snapshot from per-tick state.
    2. Classify the regime.
    3. Route to the right tactic (RANGE -> existing OI-Wall MR;
       TREND_UP/DOWN -> Trend Pullback; TREND_*_GAP -> ORB; etc.).
    4. Return a UNIFIED legacy-shaped signal dict so main.py only needs
       a one-line change. The dispatcher converts new TacticSignal
       objects back to the legacy dict format the existing _scan_for_entries
       loop already understands.

Modes (read from project_config.json -> "engine_mode"):
    "legacy"   default — calls SignalEngine.evaluate exactly as today.
    "regime"   active — uses classifier+router+tactics; falls through
               to SignalEngine when router says OI_WALL_MEAN_REVERSION.

This means flipping the system on/off is a single config-file change.
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Optional

from regime.classifier import (
    RegimeClassifier, ClassifierConfig, ClassifierFeatures, Regime,
)
from regime.router import StrategyRouter, Tactic
from regime.indicators import IndicatorTracker
from tactics import (
    TacticState, TacticSignal,
    TrendPullbackTactic, BullishORBTactic, BearishORBTactic,
)

log = logging.getLogger("dispatcher")


def _legacy_signal_no_trade(reason: str) -> dict:
    return {
        "direction": None,
        "reasons": [reason],
        "dte_risk": "N/A",
        "dte_days": 99,
        "is_expiry_day": False,
        "score": 0,
    }


def _legacy_signal_from_tactic(
    sig: TacticSignal, regime: Regime, dte: int, is_expiry: bool
) -> dict:
    """Convert a new-style TacticSignal back to the legacy dict format
    that main.py's _scan_for_entries already consumes."""
    return {
        "direction": sig.direction,
        "reasons": [
            f"[{regime.value}] {sig.reason}",
            f"tactic_sl_pct={sig.sl_pct:.2f} tp_pct={sig.tp_pct:.2f} "
            f"time_stop={sig.time_stop_min}m",
        ],
        "dte_risk": "EXTREME" if dte <= 3 else ("HIGH" if dte <= 7 else "MODERATE"),
        "dte_days": dte,
        "is_expiry_day": is_expiry,
        "score": 5,
        # Pass-through extras the bot can use if it cares
        "tactic_name": "",     # filled by dispatcher
        "tactic_sl_pct": sig.sl_pct,
        "tactic_tp_pct": sig.tp_pct,
        "tactic_time_stop_min": sig.time_stop_min,
        "tactic_strike_offset": sig.strike_offset,
        "use_hybrid_trail": sig.use_hybrid_trail,
    }


class TacticDispatcher:
    def __init__(self, mode: str = "regime"):
        self.mode = mode
        self.classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
        self.router = StrategyRouter()
        self.indicators = IndicatorTracker()
        self.tactics: dict[Tactic, object] = {
            Tactic.OI_TREND_PULLBACK: TrendPullbackTactic(),
            Tactic.BULLISH_LAUNCHPAD: BullishORBTactic(),
            Tactic.BEARISH_LAUNCHPAD: BearishORBTactic(),
        }

    # ----- lifecycle ----------------------------------------------------

    def reset_for_new_day(self, day, prev_day_close: float) -> None:
        self.indicators.start_day(day, prev_day_close)
        self.classifier._current = None     # type: ignore[attr-defined]
        self.classifier._candidate = None   # type: ignore[attr-defined]
        log.info("dispatcher: reset for %s (prev_close=%.1f)", day, prev_day_close)

    # ----- per-tick state ingest ----------------------------------------

    def on_spot_tick(self, ts: datetime, spot: float) -> None:
        self.indicators.on_spot_tick(ts, spot)

    # ----- evaluate -----------------------------------------------------

    def evaluate(
        self,
        *,
        ts: datetime,
        fetcher,                # DataFetcher instance
        engine,                 # legacy SignalEngine
        in_position: bool,
        position_direction: Optional[str] = None,
        position_entry_premium: float = 0.0,
        position_lots_added: int = 0,
    ) -> dict:
        """Returns a legacy-format signal dict (so main.py needs minimal changes)."""

        # Always advance indicators with latest spot
        spot = fetcher.get_spot()
        if spot > 0:
            self.indicators.on_spot_tick(ts, spot)

        # ---- Legacy path: just delegate to SignalEngine ----
        if self.mode == "legacy":
            return self._legacy_call(fetcher, engine, ts)

        # ---- Regime path ----
        snap = self.indicators.snapshot()
        vix = fetcher.get_india_vix()
        focus_pcr = fetcher.get_focus_pcr()
        oi_pattern = fetcher.get_oi_pattern()
        support = fetcher.get_support()
        resistance = fetcher.get_resistance()
        expiry_str = fetcher.get_expiry_date()
        dte = self._compute_dte(expiry_str, ts)
        is_expiry = (dte <= 0)

        # Build classifier features (with what we have — some fields stubbed)
        feat = ClassifierFeatures(
            ts=ts,
            gap_pct=((snap["day_open"] - snap["prev_day_close"]) / snap["prev_day_close"]
                     if snap["prev_day_close"] > 0 else 0.0),
            or_range_pct=((snap["or_high"] - snap["or_low"]) / spot
                          if (snap["or_high"] and snap["or_low"] and spot) else 0.0),
            avg_or_range_pct=0.0025,
            adx_15m=0.0,           # not computed live yet (TODO if needed)
            range_ratio=1.0,        # not computed live yet
            vwap_slope_30m=0.0,     # not computed live yet
            dist_from_vwap_pct=0.0,
            price=spot,
            vwap=spot,              # treat spot as vwap proxy in live until computed
            or_high=snap["or_high"],
            or_low=snap["or_low"],
            vix_level=vix,
            vix_chg_15m=0.0,
            dte=dte,
            event_flag=False,
            prev_day_close=snap["prev_day_close"],
        )
        regime = self.classifier.classify(feat)

        # ---- Route ----
        decision = self.router.route(regime, open_direction=position_direction)

        # If the router demands force-exit on regime change, signal that
        # to the caller via a special direction marker (caller ignores
        # if no position open).
        if in_position and decision.force_exit_open_positions:
            return {
                "direction": None,
                "reasons": [f"[REGIME-FLIP {regime.value}] force-exit open position"],
                "dte_risk": "N/A", "dte_days": dte,
                "is_expiry_day": is_expiry, "score": 0,
                "force_exit": True,
            }

        if decision.tactic == Tactic.NO_TRADE:
            return _legacy_signal_no_trade(
                f"[{regime.value}] router says NO_TRADE: {decision.reason}"
            )

        if decision.tactic == Tactic.OI_WALL_MEAN_REVERSION:
            # Range regime → use the existing live engine (production path)
            return self._legacy_call(fetcher, engine, ts, regime=regime)

        if decision.tactic == Tactic.DEBIT_SPREAD:
            # Spreads are handled by spread_executor.py — not touched here.
            return _legacy_signal_no_trade(
                f"[{regime.value}] DTE<=0 — spread tactic should run; "
                f"dispatcher does not handle naked options on expiry day."
            )

        # ---- New tactic path ----
        tactic = self.tactics.get(decision.tactic)
        if tactic is None:
            return _legacy_signal_no_trade(
                f"tactic {decision.tactic.value} not registered in dispatcher"
            )

        state = self._build_tactic_state(
            ts=ts, snap=snap, spot=spot, vix=vix, focus_pcr=focus_pcr,
            oi_pattern=oi_pattern, support=support, resistance=resistance,
            dte=dte, expiry_str=expiry_str, regime=regime, in_position=in_position,
            position_direction=position_direction,
            position_entry_premium=position_entry_premium,
            position_lots_added=position_lots_added,
        )

        sig = tactic.evaluate(state)
        if sig is None:
            return _legacy_signal_no_trade(
                f"[{regime.value}] {decision.tactic.value} declined entry"
            )

        legacy = _legacy_signal_from_tactic(sig, regime, dte, is_expiry)
        legacy["tactic_name"] = decision.tactic.value
        return legacy

    # ----- helpers ------------------------------------------------------

    def _legacy_call(self, fetcher, engine, ts: datetime,
                     regime: Optional[Regime] = None) -> dict:
        spot = fetcher.get_spot()
        sup = fetcher.get_support()
        res = fetcher.get_resistance()
        focus_pcr = fetcher.get_focus_pcr()
        oi_pattern = fetcher.get_oi_pattern()
        spot_history = fetcher.get_spot_history()
        india_vix = fetcher.get_india_vix()
        sig = engine.evaluate(
            spot_close=spot, support=sup, resistance=res,
            focus_pcr=focus_pcr, oi_pattern=oi_pattern,
            spot_history=spot_history, india_vix=india_vix,
            expiry_date=fetcher.get_expiry_date(),
            current_date=ts.strftime("%Y-%m-%d"),
        )
        if regime is not None and sig.get("direction"):
            sig["reasons"].insert(0, f"[{regime.value}] OI-Wall MR fired")
        sig["tactic_name"] = "oi_wall_mean_reversion"
        return sig

    def _build_tactic_state(
        self, *, ts, snap, spot, vix, focus_pcr, oi_pattern,
        support, resistance, dte, expiry_str, regime,
        in_position, position_direction, position_entry_premium,
        position_lots_added,
    ) -> TacticState:
        return TacticState(
            ts=ts,
            spot=spot,
            futures=spot,    # we don't have futures separately in live yet
            dte=dte,
            expiry_date=expiry_str,
            current_date=ts.strftime("%Y-%m-%d"),
            day_open=snap["day_open"],
            day_high=snap["day_high"],
            day_low=snap["day_low"],
            or_high=snap["or_high"],
            or_low=snap["or_low"],
            or_volume_avg=0.0,    # live spot has no volume
            prev_day_close=snap["prev_day_close"],
            vwap=spot,             # vwap not computed live yet
            ema9_5m=snap["ema9_5m"],
            ema21_5m=snap["ema21_5m"],
            atr_5m=snap["atr_5m"],
            adx_15m=0.0,
            bar_open=snap["bar_open"],
            bar_high=snap["bar_high"],
            bar_low=snap["bar_low"],
            bar_close=snap["bar_close"],
            bar_volume=0.0,
            prev_bar_open=snap["prev_bar_open"],
            prev_bar_high=snap["prev_bar_high"],
            prev_bar_low=snap["prev_bar_low"],
            prev_bar_close=snap["prev_bar_close"],
            recent_5m_lows=snap["recent_5m_lows"],
            recent_5m_highs=snap["recent_5m_highs"],
            support_strike=support,
            resistance_strike=resistance,
            focus_pcr=focus_pcr,
            ce_oi_change=oi_pattern.get("ce_oi_change", 0),
            pe_oi_change=oi_pattern.get("pe_oi_change", 0),
            vix_level=vix,
            vix_chg_15m=0.0,
            regime=regime.value,
            is_in_position=in_position,
            open_position_direction=position_direction,
            open_position_entry_premium=position_entry_premium,
            open_position_lots_added=position_lots_added,
        )

    @staticmethod
    def _compute_dte(expiry_str: Optional[str], ts: datetime) -> int:
        if not expiry_str:
            return 99
        try:
            exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return 99
        return max(0, (exp - ts.date()).days)
