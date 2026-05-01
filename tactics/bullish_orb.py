"""
Bullish Momentum Launchpad — Gap-up Opening Range Breakout.

Spec source: strategy_bullish_launchpad.json

Setup:
    - Gap up >= 0.5%
    - VIX < 22 AND VIX 15-min change < 10%
    - OR volume >= 1.2 * 20d avg OR volume (proxied here by today's
      OR volume vs typical)
    - Gap not filled at 09:30 (price still > prev_day_close)
    - DTE >= 2

Trigger:
    - First 5m candle after 09:30 that closes above OR_high (= candle 1)
    - Next 5m candle also closes above OR_high (= candle 2, confirmation)
    - Confirmation candle volume >= 1.5 * avg OR candle volume

Entry:
    - At confirmation candle close, BUY 1-strike ITM CE
    - Pyramiding (50/30/20 inverted) is left to the simulator's
      position-management layer; this tactic emits the initial 'enter'
      and subsequent 'add_lot' signals.

Exits the simulator handles using the tactic-prescribed:
    - sl_pct, tp_pct from this signal
    - use_hybrid_trail=True (so simulator uses max(prev_15m_low - 2pts,
      EMA9_5m - 0.5*ATR) for trail)

Failed-breakout fast exit and consolidation exits are handled by the
simulator's exit layer using state inspection — this tactic exposes
'exit' signals when the simulator queries it on subsequent bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Optional

from tactics.base import (
    Tactic, TacticConfig, TacticState, TacticSignal, GateResult,
)


@dataclass
class BullishORBConfig(TacticConfig):
    name: str = "bullish_orb"

    gap_min_pct: float = 0.005           # 0.5%
    vix_max: float = 22.0
    vix_chg_15m_max: float = 0.10        # 10%
    or_volume_ratio_min: float = 1.2
    breakout_volume_ratio_min: float = 1.5

    entry_window_end: time = time(10, 30)   # no entries after 10:30
    no_entry_before: time = time(9, 30)
    no_entry_after: time = time(14, 30)

    # Pyramiding triggers in ATR units (relative to entry premium)
    pyramid_lot2_atr_threshold: float = 0.25
    pyramid_lot3_atr_threshold: float = 0.50
    max_adds: int = 2

    dte_min: int = 2
    dte_spread_switch: int = 3

    sl_pct: float = 0.30
    tp_pct: float = 0.50      # primary TP; pyramid lots get trailed
    time_stop_min: int = 90
    strike_offset: int = 1    # 1-strike ITM CE


class BullishORBTactic(Tactic):
    """Gap-up ORB tactic — fires only on TREND_UP_GAP regime / gap-up days."""

    config: BullishORBConfig

    def __init__(self, config: Optional[BullishORBConfig] = None):
        super().__init__(config or BullishORBConfig())

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        cfg = self.config

        # Pyramiding path — already in position, check for add-lot triggers
        if state.is_in_position:
            return self._evaluate_add_lot(state)

        # Fresh entry path
        return self._evaluate_entry(state)

    # ------------------------------------------------------------------
    # Initial entry
    # ------------------------------------------------------------------

    def _evaluate_entry(self, s: TacticState) -> Optional[TacticSignal]:
        cfg = self.config

        # Time gate — only fire during 09:30-10:30 window
        t = s.ts.time()
        if t < cfg.no_entry_before or t >= cfg.entry_window_end:
            return None

        # DTE gate
        if s.dte < cfg.dte_min:
            return None

        # Pre-conditions
        if s.prev_day_close <= 0:
            return None
        gap_pct = (s.day_open - s.prev_day_close) / s.prev_day_close
        if gap_pct < cfg.gap_min_pct:
            return None
        if s.vix_level >= cfg.vix_max:
            return None
        if abs(s.vix_chg_15m) >= cfg.vix_chg_15m_max:
            return None

        # Gap fill guard — at 09:30 the price is no longer above prev close
        if s.spot <= s.prev_day_close:
            return None

        # Need OR levels
        if s.or_high <= 0 or s.or_low <= 0:
            return None

        # Trigger: current candle closed above OR_high AND prev candle did too
        # (both candles confirm; this fires on the SECOND such candle)
        if s.bar_close <= s.or_high:
            return None
        if s.prev_bar_close <= s.or_high:
            return None

        # Volume confirmation on the breakout (current bar)
        if s.or_volume_avg > 0:
            if s.bar_volume < cfg.breakout_volume_ratio_min * s.or_volume_avg:
                return None

        return TacticSignal(
            action="enter",
            direction="CE",
            strike_offset=cfg.strike_offset,
            qty_pct_of_intended=0.50,   # lot 1 = 50% of intended (inverted pyramid)
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            use_hybrid_trail=True,
            reason=(f"Bullish ORB entry: gap={gap_pct*100:.2f}%, "
                    f"OR_high={s.or_high:.0f}, "
                    f"vol_ratio={s.bar_volume/s.or_volume_avg:.1f}x"
                    if s.or_volume_avg else
                    f"Bullish ORB entry: gap={gap_pct*100:.2f}%"),
        )

    # ------------------------------------------------------------------
    # Pyramid adds (lot 2 and lot 3)
    # ------------------------------------------------------------------

    def _evaluate_add_lot(self, s: TacticState) -> Optional[TacticSignal]:
        cfg = self.config
        if s.open_position_direction != "CE":
            return None
        if s.open_position_lots_added >= cfg.max_adds:
            return None
        if s.atr_5m <= 0:
            return None

        # Distance the spot has moved from entry-spot equivalent — we
        # approximate via current price vs OR_high (entry was at confirmation
        # which was just above OR_high). 0.25*ATR for lot2, 0.50*ATR for lot3.
        progress = s.spot - s.or_high
        if progress <= 0:
            return None

        if s.open_position_lots_added == 0 and progress >= cfg.pyramid_lot2_atr_threshold * s.atr_5m:
            return TacticSignal(
                action="add_lot",
                direction="CE",
                strike_offset=cfg.strike_offset,
                qty_pct_of_intended=0.30,
                sl_pct=cfg.sl_pct,
                tp_pct=cfg.tp_pct,
                time_stop_min=cfg.time_stop_min,
                use_hybrid_trail=True,
                reason=f"Bullish ORB pyramid lot2 at +{progress:.0f} pts",
            )
        if s.open_position_lots_added == 1 and progress >= cfg.pyramid_lot3_atr_threshold * s.atr_5m:
            return TacticSignal(
                action="add_lot",
                direction="CE",
                strike_offset=cfg.strike_offset,
                qty_pct_of_intended=0.20,
                sl_pct=cfg.sl_pct,
                tp_pct=cfg.tp_pct,
                time_stop_min=cfg.time_stop_min,
                use_hybrid_trail=True,
                reason=f"Bullish ORB pyramid lot3 at +{progress:.0f} pts",
            )
        return None

    # ------------------------------------------------------------------
    # Diagnostic gates — only the CE direction is meaningful here
    # ------------------------------------------------------------------
    def gates_for_direction(
        self, s: TacticState, direction: str
    ) -> dict[str, GateResult]:
        if direction != "CE":
            return {}
        cfg = self.config
        gates: dict[str, GateResult] = {}
        gates["dte_ok"] = GateResult(
            s.dte >= cfg.dte_min, s.dte, cfg.dte_min,
            f"DTE {s.dte} >= {cfg.dte_min}",
        )
        t = s.ts.time()
        gates["entry_window"] = GateResult(
            cfg.no_entry_before <= t < cfg.entry_window_end,
            t.isoformat(timespec='minutes'),
            f"{cfg.no_entry_before}-{cfg.entry_window_end}",
            f"time {t.isoformat(timespec='minutes')} in entry window",
        )
        gap = ((s.day_open - s.prev_day_close) / s.prev_day_close
               if s.prev_day_close > 0 else 0.0)
        gates["gap_up_min"] = GateResult(
            gap >= cfg.gap_min_pct, gap, cfg.gap_min_pct,
            f"gap {gap*100:.2f}% >= {cfg.gap_min_pct*100:.2f}%",
        )
        gates["vix_ok"] = GateResult(
            s.vix_level < cfg.vix_max, s.vix_level, cfg.vix_max,
            f"VIX {s.vix_level:.2f} < {cfg.vix_max}",
        )
        gates["vix_chg_ok"] = GateResult(
            abs(s.vix_chg_15m) < cfg.vix_chg_15m_max,
            abs(s.vix_chg_15m), cfg.vix_chg_15m_max,
            f"|VIX Δ15m| {abs(s.vix_chg_15m)*100:.2f}% < {cfg.vix_chg_15m_max*100:.2f}%",
        )
        gates["gap_not_filled"] = GateResult(
            s.spot > s.prev_day_close, s.spot, s.prev_day_close,
            f"spot {s.spot:.0f} still > prev close {s.prev_day_close:.0f}",
        )
        gates["or_levels_present"] = GateResult(
            s.or_high > 0 and s.or_low > 0,
            (s.or_high, s.or_low), "non-zero",
            f"OR_high={s.or_high:.0f} OR_low={s.or_low:.0f}",
        )
        gates["bar_close_above_OR_high"] = GateResult(
            s.bar_close > s.or_high, s.bar_close, s.or_high,
            f"close {s.bar_close:.0f} > OR_high {s.or_high:.0f}",
        )
        gates["prev_close_above_OR_high"] = GateResult(
            s.prev_bar_close > s.or_high, s.prev_bar_close, s.or_high,
            f"prev close {s.prev_bar_close:.0f} > OR_high {s.or_high:.0f}",
        )
        if s.or_volume_avg > 0:
            ratio = s.bar_volume / s.or_volume_avg
            gates["volume_confirmation"] = GateResult(
                ratio >= cfg.breakout_volume_ratio_min,
                ratio, cfg.breakout_volume_ratio_min,
                f"vol ratio {ratio:.2f} >= {cfg.breakout_volume_ratio_min}",
            )
        else:
            gates["volume_confirmation"] = GateResult(
                False, 0, cfg.breakout_volume_ratio_min,
                "or_volume_avg is 0 — no volume data (likely spot-only feed)",
            )
        return gates
