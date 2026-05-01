"""
VWAP Hybrid Tactic — VWAP mean-reversion with OI-wall confluence.

Spec source: strategy_vwap_hybrid.json

Long (CE) entry:
    - Price extended below VWAP by >= extension_gap (max(0.3% * price, 2*ATR))
    - Current 5m bar's low touched today's LoD or within 0.15%
    - Bar shows reclaim: close > (prev_high + prev_low) / 2 AND low > prev_low
    - Focus PCR > 0.85 (not bearish)
    - PE OI change > 0 (Put writers defending support)

Short (PE) entry: mirror.

DTE & VIX gates as specified. ITM strike, delta target via 1-strike-ITM offset.
SL 30%, TP 50%, time-stop 120 min (matches production OI-wall defaults).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Optional

from tactics.base import (
    Tactic, TacticConfig, TacticState, TacticSignal, GateResult,
)


@dataclass
class VWAPHybridConfig(TacticConfig):
    name: str = "vwap_hybrid"

    extension_pct_min: float = 0.003     # 0.3% of price
    atr_multiplier: float = 2.0           # OR 2 * ATR
    lod_proximity_pct: float = 0.0015     # 0.15%

    pcr_bullish_lower_bound: float = 0.85   # PCR <= 0.85 => bearish (block CE)
    pcr_bearish_upper_bound: float = 1.10   # PCR >= 1.10 => bullish (block PE)

    vix_max_for_ce: float = 18.0  # CE entries blocked above this
    vix_min_for_pe: float = 18.0  # PE entries blocked below this

    dte_min: int = 2
    dte_spread_switch: int = 3

    sl_pct: float = 0.30
    tp_pct: float = 0.50
    time_stop_min: int = 120
    strike_offset: int = 1   # 1-strike ITM


class VWAPHybridTactic(Tactic):
    """Mean-reversion tactic combining VWAP extension trigger + OI-wall gates."""

    config: VWAPHybridConfig

    def __init__(self, config: Optional[VWAPHybridConfig] = None):
        super().__init__(config or VWAPHybridConfig())

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        cfg = self.config
        if state.is_in_position:
            return None
        if not self.in_session_window(state):
            return None
        if state.dte < cfg.dte_min:
            return None
        if state.atr_5m <= 0 or state.vwap <= 0:
            return None

        extension_gap = max(cfg.extension_pct_min * state.spot,
                            cfg.atr_multiplier * state.atr_5m)

        # Long candidate
        long_sig = self._evaluate_long(state, extension_gap)
        if long_sig:
            return long_sig

        short_sig = self._evaluate_short(state, extension_gap)
        if short_sig:
            return short_sig
        return None

    # ------------------------------------------------------------------
    # Long path
    # ------------------------------------------------------------------
    def _evaluate_long(self, s: TacticState, extension_gap: float) -> Optional[TacticSignal]:
        cfg = self.config

        # VIX gate (Gate 0)
        if s.vix_level >= cfg.vix_max_for_ce:
            return None

        # PCR not bearish
        if s.focus_pcr <= cfg.pcr_bullish_lower_bound:
            return None

        # OI confirmation: PE writers adding (defending support)
        if s.pe_oi_change <= 0:
            return None

        # Location: extended BELOW VWAP
        if s.spot >= s.vwap - extension_gap:
            return None

        # LoD proximity: current candle's low at or near LoD
        if s.day_low > 0:
            dist_to_lod = abs(s.bar_low - s.day_low) / s.spot
            if dist_to_lod > cfg.lod_proximity_pct:
                return None

        # Reclaim: close > midpoint of prev candle AND low > prev_low (failure of lows)
        if s.prev_bar_high <= 0 or s.prev_bar_low <= 0:
            return None
        prev_mid = (s.prev_bar_high + s.prev_bar_low) / 2
        if s.bar_close <= prev_mid:
            return None
        if s.bar_low <= s.prev_bar_low:
            return None

        return TacticSignal(
            action="enter",
            direction="CE",
            strike_offset=cfg.strike_offset,
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            reason=(f"VWAP-MR LONG: ext_gap={extension_gap:.1f}, "
                    f"pcr={s.focus_pcr:.2f}, pe_oi+={s.pe_oi_change:.0f}, "
                    f"vix={s.vix_level:.1f}"),
        )

    # ------------------------------------------------------------------
    # Short path
    # ------------------------------------------------------------------
    def _evaluate_short(self, s: TacticState, extension_gap: float) -> Optional[TacticSignal]:
        cfg = self.config

        if s.vix_level < cfg.vix_min_for_pe:
            return None
        if s.focus_pcr >= cfg.pcr_bearish_upper_bound:
            return None
        if s.ce_oi_change <= 0:
            return None
        if s.spot <= s.vwap + extension_gap:
            return None
        if s.day_high > 0:
            dist_to_hod = abs(s.day_high - s.bar_high) / s.spot
            if dist_to_hod > cfg.lod_proximity_pct:
                return None
        if s.prev_bar_high <= 0 or s.prev_bar_low <= 0:
            return None
        prev_mid = (s.prev_bar_high + s.prev_bar_low) / 2
        if s.bar_close >= prev_mid:
            return None
        if s.bar_high >= s.prev_bar_high:
            return None

        return TacticSignal(
            action="enter",
            direction="PE",
            strike_offset=cfg.strike_offset,
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            reason=(f"VWAP-MR SHORT: ext_gap={extension_gap:.1f}, "
                    f"pcr={s.focus_pcr:.2f}, ce_oi+={s.ce_oi_change:.0f}, "
                    f"vix={s.vix_level:.1f}"),
        )

    # ------------------------------------------------------------------
    # Diagnostic gates — used by the journal to detect near-misses
    # ------------------------------------------------------------------
    def gates_for_direction(
        self, s: TacticState, direction: str
    ) -> dict[str, GateResult]:
        cfg = self.config
        ext_gap = max(cfg.extension_pct_min * s.spot, cfg.atr_multiplier * s.atr_5m)

        gates: dict[str, GateResult] = {}
        # Common
        gates["dte_ok"] = GateResult(
            s.dte >= cfg.dte_min, s.dte, cfg.dte_min,
            f"DTE {s.dte} >= {cfg.dte_min}",
        )
        gates["atr_positive"] = GateResult(
            s.atr_5m > 0, s.atr_5m, 0.0,
            f"ATR {s.atr_5m:.1f}",
        )
        gates["session_window"] = GateResult(
            cfg.no_entry_before <= s.ts.time() < cfg.no_entry_after,
            s.ts.time().isoformat(timespec='minutes'),
            f"{cfg.no_entry_before}-{cfg.no_entry_after}",
        )

        if direction == "CE":
            gates["vix_ok_for_CE"] = GateResult(
                s.vix_level < cfg.vix_max_for_ce, s.vix_level, cfg.vix_max_for_ce,
                f"VIX {s.vix_level:.2f} < {cfg.vix_max_for_ce}",
            )
            gates["pcr_ok_for_CE"] = GateResult(
                s.focus_pcr > cfg.pcr_bullish_lower_bound,
                s.focus_pcr, cfg.pcr_bullish_lower_bound,
                f"focus PCR {s.focus_pcr:.2f} > {cfg.pcr_bullish_lower_bound}",
            )
            gates["pe_oi_buildup"] = GateResult(
                s.pe_oi_change > 0, s.pe_oi_change, 0,
                f"PE OI Δ {s.pe_oi_change:+.0f} > 0",
            )
            gates["price_extended_below_vwap"] = GateResult(
                s.spot < (s.vwap - ext_gap), s.spot - s.vwap, -ext_gap,
                f"spot − vwap = {s.spot - s.vwap:+.1f} < −{ext_gap:.1f}",
            )
            if s.day_low > 0:
                dist = abs(s.bar_low - s.day_low) / s.spot
                gates["lod_proximity"] = GateResult(
                    dist <= cfg.lod_proximity_pct, dist, cfg.lod_proximity_pct,
                    f"bar.low {s.bar_low:.0f} within {cfg.lod_proximity_pct*100:.2f}% of LoD {s.day_low:.0f}",
                )
            if s.prev_bar_high > 0 and s.prev_bar_low > 0:
                prev_mid = (s.prev_bar_high + s.prev_bar_low) / 2
                gates["reclaim_close"] = GateResult(
                    s.bar_close > prev_mid, s.bar_close, prev_mid,
                    f"close {s.bar_close:.0f} > prev mid {prev_mid:.0f}",
                )
                gates["failure_of_lows"] = GateResult(
                    s.bar_low > s.prev_bar_low, s.bar_low, s.prev_bar_low,
                    f"bar.low {s.bar_low:.0f} > prev.low {s.prev_bar_low:.0f}",
                )
        else:  # PE
            gates["vix_ok_for_PE"] = GateResult(
                s.vix_level >= cfg.vix_min_for_pe, s.vix_level, cfg.vix_min_for_pe,
                f"VIX {s.vix_level:.2f} >= {cfg.vix_min_for_pe}",
            )
            gates["pcr_ok_for_PE"] = GateResult(
                s.focus_pcr < cfg.pcr_bearish_upper_bound,
                s.focus_pcr, cfg.pcr_bearish_upper_bound,
                f"focus PCR {s.focus_pcr:.2f} < {cfg.pcr_bearish_upper_bound}",
            )
            gates["ce_oi_buildup"] = GateResult(
                s.ce_oi_change > 0, s.ce_oi_change, 0,
                f"CE OI Δ {s.ce_oi_change:+.0f} > 0",
            )
            gates["price_extended_above_vwap"] = GateResult(
                s.spot > (s.vwap + ext_gap), s.spot - s.vwap, ext_gap,
                f"spot − vwap = {s.spot - s.vwap:+.1f} > +{ext_gap:.1f}",
            )
            if s.day_high > 0:
                dist = abs(s.day_high - s.bar_high) / s.spot
                gates["hod_proximity"] = GateResult(
                    dist <= cfg.lod_proximity_pct, dist, cfg.lod_proximity_pct,
                    f"bar.high {s.bar_high:.0f} within {cfg.lod_proximity_pct*100:.2f}% of HoD {s.day_high:.0f}",
                )
            if s.prev_bar_high > 0 and s.prev_bar_low > 0:
                prev_mid = (s.prev_bar_high + s.prev_bar_low) / 2
                gates["reclaim_close"] = GateResult(
                    s.bar_close < prev_mid, s.bar_close, prev_mid,
                    f"close {s.bar_close:.0f} < prev mid {prev_mid:.0f}",
                )
                gates["failure_of_highs"] = GateResult(
                    s.bar_high < s.prev_bar_high, s.bar_high, s.prev_bar_high,
                    f"bar.high {s.bar_high:.0f} < prev.high {s.prev_bar_high:.0f}",
                )
        return gates
