"""
OI-Trend-Pullback Tactic — pullback-to-EMA9 trend follower.

Spec source: strategy_oi_trend_scalper.json

Long (CE) entry — when classifier regime is TREND_UP:
    - Bullish OI bias: pe_oi_change / ce_oi_change >= 1.50 AND pe_oi_change >= 500k
    - Price > VWAP, price > EMA9 (futures 5m)
    - PULLBACK: low of one of last 3 candles touched / came within 0.1% of EMA9
    - RECLAIM: current candle close > prev close AND close > (H+L)/2
    - VIX < 22

Short (PE) entry — mirror for TREND_DOWN.

Strike: 1-strike ITM. SL/TP: ATR-style risk-based but here we use percentage
defaults consistent with production (30% SL / 50% TP / 90 min time stop).

Note: full inverted-pyramid scaling and 2-candle partial exit per spec are
left to the simulator's exit-management layer; this tactic only fires the
ENTRY signal. The simulator handles trailing & partials.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tactics.base import (
    Tactic, TacticConfig, TacticState, TacticSignal, GateResult,
)


@dataclass
class TrendPullbackConfig(TacticConfig):
    name: str = "trend_pullback"

    oi_bias_ratio_min: float = 1.50
    oi_bias_magnitude_min: float = 500_000.0
    pullback_proximity_pct: float = 0.001       # 0.1%
    pullback_lookback_bars: int = 3

    vix_max: float = 22.0
    adx_min_15m: float = 25.0
    range_ratio_min: float = 1.2

    dte_min: int = 2                  # validated config (Phase 10: 6/6 walk-fwd
                                       # passed at this value). Phase 11
                                       # suggested 1 but actual rerun showed
                                       # P&L dropped from +Rs 9,939 to +Rs 4,743
                                       # — revert kept.
    dte_spread_switch: int = 3

    sl_pct: float = 0.30
    tp_pct: float = 0.50
    time_stop_min: int = 90
    strike_offset: int = 1


class TrendPullbackTactic(Tactic):
    """Trend follower armed only when classifier regime is TREND_UP/DOWN."""

    config: TrendPullbackConfig

    def __init__(self, config: Optional[TrendPullbackConfig] = None):
        super().__init__(config or TrendPullbackConfig())

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        cfg = self.config
        if state.is_in_position:
            return None
        if not self.in_session_window(state):
            return None
        if state.dte < cfg.dte_min:
            return None
        if state.vix_level >= cfg.vix_max:
            return None
        if state.adx_15m < cfg.adx_min_15m:
            return None

        # Phase 11 hypothesis "also accept TREND_*_GAP" was tested in a
        # full backtest and HURT performance (+Rs 9,939 -> +Rs 4,743).
        # Keep strict regime match as in the 6/6 walk-forward validated config.
        if state.regime == "TREND_UP":
            return self._evaluate_long(state)
        if state.regime == "TREND_DOWN":
            return self._evaluate_short(state)
        return None

    # ------------------------------------------------------------------

    def _evaluate_long(self, s: TacticState) -> Optional[TacticSignal]:
        cfg = self.config

        # OI bias bullish
        if s.ce_oi_change <= 0:
            return None
        ratio = s.pe_oi_change / s.ce_oi_change if s.ce_oi_change else 0.0
        if ratio < cfg.oi_bias_ratio_min:
            return None
        if s.pe_oi_change < cfg.oi_bias_magnitude_min:
            return None

        # Trend alignment
        if s.spot <= s.vwap:
            return None
        if s.spot <= s.ema9_5m:
            return None

        # Pullback within last N bars
        if not self._pullback_to_ema9(s, cfg, side="long"):
            return None

        # Reclaim: close > prev close AND close > midpoint
        if s.bar_close <= s.prev_bar_close:
            return None
        bar_mid = (s.bar_high + s.bar_low) / 2
        if s.bar_close <= bar_mid:
            return None

        return TacticSignal(
            action="enter",
            direction="CE",
            strike_offset=cfg.strike_offset,
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            reason=(f"Trend-Pullback LONG: ratio={ratio:.2f}, "
                    f"adx={s.adx_15m:.1f}, ema9={s.ema9_5m:.0f}"),
        )

    def _evaluate_short(self, s: TacticState) -> Optional[TacticSignal]:
        cfg = self.config

        if s.pe_oi_change <= 0:
            return None
        ratio = s.ce_oi_change / s.pe_oi_change if s.pe_oi_change else 0.0
        if ratio < cfg.oi_bias_ratio_min:
            return None
        if s.ce_oi_change < cfg.oi_bias_magnitude_min:
            return None

        if s.spot >= s.vwap:
            return None
        if s.spot >= s.ema9_5m:
            return None

        if not self._pullback_to_ema9(s, cfg, side="short"):
            return None

        if s.bar_close >= s.prev_bar_close:
            return None
        bar_mid = (s.bar_high + s.bar_low) / 2
        if s.bar_close >= bar_mid:
            return None

        return TacticSignal(
            action="enter",
            direction="PE",
            strike_offset=cfg.strike_offset,
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            reason=(f"Trend-Pullback SHORT: ratio={ratio:.2f}, "
                    f"adx={s.adx_15m:.1f}, ema9={s.ema9_5m:.0f}"),
        )

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Diagnostic gates
    # ------------------------------------------------------------------

    def gates_for_direction(
        self, s: TacticState, direction: str
    ) -> dict[str, GateResult]:
        cfg = self.config
        gates: dict[str, GateResult] = {}

        gates["dte_ok"] = GateResult(
            s.dte >= cfg.dte_min, s.dte, cfg.dte_min,
            f"DTE {s.dte} >= {cfg.dte_min}",
        )
        gates["session_window"] = GateResult(
            cfg.no_entry_before <= s.ts.time() < cfg.no_entry_after,
            s.ts.time().isoformat(timespec='minutes'),
            f"{cfg.no_entry_before}-{cfg.no_entry_after}",
        )
        gates["vix_ok"] = GateResult(
            s.vix_level < cfg.vix_max, s.vix_level, cfg.vix_max,
            f"VIX {s.vix_level:.2f} < {cfg.vix_max}",
        )
        gates["adx_strong"] = GateResult(
            s.adx_15m >= cfg.adx_min_15m, s.adx_15m, cfg.adx_min_15m,
            f"ADX(15m) {s.adx_15m:.1f} >= {cfg.adx_min_15m}",
        )

        if direction == "CE":
            gates["regime_is_TREND_UP"] = GateResult(
                s.regime == "TREND_UP", s.regime, "TREND_UP",
                f"regime={s.regime}",
            )
            ratio = s.pe_oi_change / s.ce_oi_change if s.ce_oi_change > 0 else 0.0
            gates["oi_bias_ratio"] = GateResult(
                ratio >= cfg.oi_bias_ratio_min, ratio, cfg.oi_bias_ratio_min,
                f"PE/CE OI Δ ratio {ratio:.2f} >= {cfg.oi_bias_ratio_min}",
            )
            gates["oi_bias_magnitude"] = GateResult(
                s.pe_oi_change >= cfg.oi_bias_magnitude_min,
                s.pe_oi_change, cfg.oi_bias_magnitude_min,
                f"PE OI Δ {s.pe_oi_change:.0f} >= {cfg.oi_bias_magnitude_min:.0f}",
            )
            gates["price_above_vwap"] = GateResult(
                s.spot > s.vwap, s.spot, s.vwap,
                f"spot {s.spot:.0f} > vwap {s.vwap:.0f}",
            )
            gates["price_above_ema9"] = GateResult(
                s.spot > s.ema9_5m, s.spot, s.ema9_5m,
                f"spot {s.spot:.0f} > ema9 {s.ema9_5m:.0f}",
            )
            gates["pullback_to_ema9"] = GateResult(
                self._pullback_to_ema9(s, cfg, "long"),
                "yes" if self._pullback_to_ema9(s, cfg, "long") else "no",
                "yes",
                "low touched/within proximity of EMA9 in last 3 bars",
            )
            gates["reclaim_close_gt_prev"] = GateResult(
                s.bar_close > s.prev_bar_close, s.bar_close, s.prev_bar_close,
                f"close {s.bar_close:.0f} > prev close {s.prev_bar_close:.0f}",
            )
            mid = (s.bar_high + s.bar_low) / 2
            gates["close_above_midpoint"] = GateResult(
                s.bar_close > mid, s.bar_close, mid,
                f"close {s.bar_close:.0f} > bar mid {mid:.0f}",
            )
        else:  # PE
            gates["regime_is_TREND_DOWN"] = GateResult(
                s.regime == "TREND_DOWN", s.regime, "TREND_DOWN",
                f"regime={s.regime}",
            )
            ratio = s.ce_oi_change / s.pe_oi_change if s.pe_oi_change > 0 else 0.0
            gates["oi_bias_ratio"] = GateResult(
                ratio >= cfg.oi_bias_ratio_min, ratio, cfg.oi_bias_ratio_min,
                f"CE/PE OI Δ ratio {ratio:.2f} >= {cfg.oi_bias_ratio_min}",
            )
            gates["oi_bias_magnitude"] = GateResult(
                s.ce_oi_change >= cfg.oi_bias_magnitude_min,
                s.ce_oi_change, cfg.oi_bias_magnitude_min,
                f"CE OI Δ {s.ce_oi_change:.0f} >= {cfg.oi_bias_magnitude_min:.0f}",
            )
            gates["price_below_vwap"] = GateResult(
                s.spot < s.vwap, s.spot, s.vwap,
                f"spot {s.spot:.0f} < vwap {s.vwap:.0f}",
            )
            gates["price_below_ema9"] = GateResult(
                s.spot < s.ema9_5m, s.spot, s.ema9_5m,
                f"spot {s.spot:.0f} < ema9 {s.ema9_5m:.0f}",
            )
            gates["pullback_to_ema9"] = GateResult(
                self._pullback_to_ema9(s, cfg, "short"),
                "yes" if self._pullback_to_ema9(s, cfg, "short") else "no",
                "yes",
                "high touched/within proximity of EMA9 in last 3 bars",
            )
            gates["reclaim_close_lt_prev"] = GateResult(
                s.bar_close < s.prev_bar_close, s.bar_close, s.prev_bar_close,
                f"close {s.bar_close:.0f} < prev close {s.prev_bar_close:.0f}",
            )
            mid = (s.bar_high + s.bar_low) / 2
            gates["close_below_midpoint"] = GateResult(
                s.bar_close < mid, s.bar_close, mid,
                f"close {s.bar_close:.0f} < bar mid {mid:.0f}",
            )
        return gates

    @staticmethod
    def _pullback_to_ema9(
        s: TacticState,
        cfg: TrendPullbackConfig,
        side: str,
    ) -> bool:
        if s.ema9_5m <= 0:
            return False
        threshold_dist = cfg.pullback_proximity_pct * s.ema9_5m
        bars_to_check = cfg.pullback_lookback_bars

        if side == "long":
            samples = list(s.recent_5m_lows[-bars_to_check:])
            if not samples:
                return False
            return any(abs(low - s.ema9_5m) <= threshold_dist
                       or low <= s.ema9_5m for low in samples)
        else:
            samples = list(s.recent_5m_highs[-bars_to_check:])
            if not samples:
                return False
            return any(abs(high - s.ema9_5m) <= threshold_dist
                       or high >= s.ema9_5m for high in samples)
