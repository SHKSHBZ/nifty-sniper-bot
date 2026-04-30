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

from tactics.base import Tactic, TacticConfig, TacticState, TacticSignal


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

    dte_min: int = 2
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
