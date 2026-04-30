"""
Bearish Breakdown Launchpad — Gap-down ORB (mirror of bullish).

Spec source: strategy_bearish_launchpad.json

Differences vs bullish:
    - Gap DOWN >= 0.5%
    - VIX ceiling raised to 25 (bearish days expand vol; some VIX rise OK)
    - VIX_chg_15m ceiling raised to 15%
    - Entry window shorter: 09:30-10:15 (bearish breakdowns tend to be sharper)
    - 1-strike ITM PE
    - Pyramiding mirrors bullish (50/30/20)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Optional

from tactics.base import Tactic, TacticConfig, TacticState, TacticSignal


@dataclass
class BearishORBConfig(TacticConfig):
    name: str = "bearish_orb"

    gap_min_pct: float = 0.005           # 0.5% (down)
    vix_max: float = 25.0                # higher than bullish: bearish days run vol
    vix_chg_15m_max: float = 0.15
    or_volume_ratio_min: float = 1.2
    breakdown_volume_ratio_min: float = 1.5

    entry_window_end: time = time(10, 15)   # tighter than bullish
    no_entry_before: time = time(9, 30)
    no_entry_after: time = time(14, 30)

    pyramid_lot2_atr_threshold: float = 0.25
    pyramid_lot3_atr_threshold: float = 0.50
    max_adds: int = 2

    dte_min: int = 2
    dte_spread_switch: int = 3

    sl_pct: float = 0.30
    tp_pct: float = 0.50
    time_stop_min: int = 90
    strike_offset: int = 1   # 1-strike ITM PE


class BearishORBTactic(Tactic):
    """Gap-down breakdown ORB — fires only on TREND_DOWN_GAP regime."""

    config: BearishORBConfig

    def __init__(self, config: Optional[BearishORBConfig] = None):
        super().__init__(config or BearishORBConfig())

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        if state.is_in_position:
            return self._evaluate_add_lot(state)
        return self._evaluate_entry(state)

    # ------------------------------------------------------------------

    def _evaluate_entry(self, s: TacticState) -> Optional[TacticSignal]:
        cfg = self.config
        t = s.ts.time()
        if t < cfg.no_entry_before or t >= cfg.entry_window_end:
            return None
        if s.dte < cfg.dte_min:
            return None
        if s.prev_day_close <= 0:
            return None
        gap_pct = (s.day_open - s.prev_day_close) / s.prev_day_close
        # Need a DOWN gap of at least gap_min_pct
        if gap_pct > -cfg.gap_min_pct:
            return None
        if s.vix_level >= cfg.vix_max:
            return None
        if abs(s.vix_chg_15m) >= cfg.vix_chg_15m_max:
            return None

        # Gap fill guard — price still BELOW prev_day_close
        if s.spot >= s.prev_day_close:
            return None

        if s.or_high <= 0 or s.or_low <= 0:
            return None

        # Trigger: two consecutive 5m closes BELOW OR_low
        if s.bar_close >= s.or_low:
            return None
        if s.prev_bar_close >= s.or_low:
            return None

        # Volume confirmation
        if s.or_volume_avg > 0:
            if s.bar_volume < cfg.breakdown_volume_ratio_min * s.or_volume_avg:
                return None

        vol_str = (f"{s.bar_volume/s.or_volume_avg:.1f}x"
                   if s.or_volume_avg else "n/a")
        return TacticSignal(
            action="enter",
            direction="PE",
            strike_offset=cfg.strike_offset,
            qty_pct_of_intended=0.50,
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            use_hybrid_trail=True,
            reason=(f"Bearish ORB entry: gap={gap_pct*100:.2f}%, "
                    f"OR_low={s.or_low:.0f}, vol_ratio={vol_str}"),
        )

    # ------------------------------------------------------------------

    def _evaluate_add_lot(self, s: TacticState) -> Optional[TacticSignal]:
        cfg = self.config
        if s.open_position_direction != "PE":
            return None
        if s.open_position_lots_added >= cfg.max_adds:
            return None
        if s.atr_5m <= 0:
            return None

        # Progress = how far below OR_low we've moved
        progress = s.or_low - s.spot
        if progress <= 0:
            return None

        if s.open_position_lots_added == 0 and progress >= cfg.pyramid_lot2_atr_threshold * s.atr_5m:
            return TacticSignal(
                action="add_lot",
                direction="PE",
                strike_offset=cfg.strike_offset,
                qty_pct_of_intended=0.30,
                sl_pct=cfg.sl_pct,
                tp_pct=cfg.tp_pct,
                time_stop_min=cfg.time_stop_min,
                use_hybrid_trail=True,
                reason=f"Bearish ORB pyramid lot2 at -{progress:.0f} pts",
            )
        if s.open_position_lots_added == 1 and progress >= cfg.pyramid_lot3_atr_threshold * s.atr_5m:
            return TacticSignal(
                action="add_lot",
                direction="PE",
                strike_offset=cfg.strike_offset,
                qty_pct_of_intended=0.20,
                sl_pct=cfg.sl_pct,
                tp_pct=cfg.tp_pct,
                time_stop_min=cfg.time_stop_min,
                use_hybrid_trail=True,
                reason=f"Bearish ORB pyramid lot3 at -{progress:.0f} pts",
            )
        return None
