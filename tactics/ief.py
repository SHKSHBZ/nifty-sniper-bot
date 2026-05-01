"""
IEFTactic — Institutional Equilibrium Framework (SMC / ICT-style).

Implements the user-supplied theory's core mechanics in a fully
algorithmic, prospective way (no hindsight):

  - Swing pivots (rolling fractal-style highs and lows)
  - BoS / CHoCH (close-through-pivot trigger)
  - Order Block (last opposite-side candle before an impulsive move)
  - FVG (3-candle fair-value gap)
  - Golden Zone (0.618 - 0.786 retracement)

ENTRY (long, CE):
    1. A bullish CHoCH occurred recently (within last `choch_lookback` bars):
       price closed above the most recent swing high after a downtrend.
    2. Price has retraced back into the GOLDEN ZONE of the impulse leg
       that produced the CHoCH (0.618-0.786 of the leg).
    3. Either an active bullish ORDER BLOCK or a bullish FVG sits inside
       the golden zone (confluence).
    4. Current 5m candle confirms (close > open AND close > prev close).

ENTRY (short, PE): mirror.

ALL detectors are PROSPECTIVE — a swing pivot is only confirmed `right`
bars after it forms; an order block is only emitted once an impulsive
move has actually completed; a FVG is detected from 3 closed bars.
This avoids the classic SMC pitfall of identifying patterns in
hindsight.

The tactic exposes itself through the standard Tactic interface:
    evaluate(state) -> Optional[TacticSignal]
    gates_for_direction(state, direction) -> dict[gate_name, GateResult]

so it slots into the existing dispatcher / journal / near-miss machinery
with no special handling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from tactics.base import (
    Tactic, TacticConfig, TacticState, TacticSignal, GateResult,
)


# ---------------------------------------------------------------------------
# Internal data structures (the analyzer's output)
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float

    @classmethod
    def from_tuple(cls, t) -> "Bar":
        ts = t[0]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return cls(ts=ts, open=float(t[1]), high=float(t[2]),
                   low=float(t[3]), close=float(t[4]))


@dataclass
class SwingPivot:
    idx: int          # bar-list index where pivot bar sits
    ts: datetime
    price: float
    side: Literal["high", "low"]


@dataclass
class OrderBlock:
    side: Literal["bullish", "bearish"]
    bar_ts: datetime
    high: float
    low: float
    confirmed_at_idx: int     # bar index where the impulse confirmed it


@dataclass
class FVG:
    side: Literal["bullish", "bearish"]
    low: float
    high: float
    bar_ts: datetime          # timestamp of the middle bar


@dataclass
class CHoCHEvent:
    side: Literal["up", "down"]
    idx: int
    ts: datetime
    broken_pivot_price: float
    impulse_start_idx: int     # the swing pivot at the start of the impulse


@dataclass
class IEFAnalysis:
    swings: list[SwingPivot] = field(default_factory=list)
    order_blocks: list[OrderBlock] = field(default_factory=list)
    fvgs: list[FVG] = field(default_factory=list)
    last_choch: Optional[CHoCHEvent] = None


# ---------------------------------------------------------------------------
# Analyzer — stateless: takes bars, returns analysis
# ---------------------------------------------------------------------------

class IEFAnalyzer:
    def __init__(
        self,
        swing_left: int = 3,
        swing_right: int = 3,
        ob_atr_mult: float = 1.5,
        ob_impulse_bars: int = 3,
    ):
        self.swing_left = swing_left
        self.swing_right = swing_right
        self.ob_atr_mult = ob_atr_mult
        self.ob_impulse_bars = ob_impulse_bars

    def analyze(self, bars: list[Bar], atr: float) -> IEFAnalysis:
        if len(bars) < max(self.swing_left + self.swing_right + 1,
                            self.ob_impulse_bars + 1, 3):
            return IEFAnalysis()

        swings = self._detect_swings(bars)
        obs = self._detect_order_blocks(bars, atr)
        fvgs = self._detect_fvgs(bars)
        choch = self._detect_last_choch(bars, swings)

        return IEFAnalysis(swings=swings, order_blocks=obs,
                           fvgs=fvgs, last_choch=choch)

    # ----- Swings (fractal style) -------------------------------------

    def _detect_swings(self, bars: list[Bar]) -> list[SwingPivot]:
        swings: list[SwingPivot] = []
        L, R = self.swing_left, self.swing_right
        # A pivot at index i is confirmed once we've seen R bars after it.
        for i in range(L, len(bars) - R):
            window_left = bars[i - L:i]
            window_right = bars[i + 1:i + 1 + R]
            cb = bars[i]
            if cb.high > max(b.high for b in window_left + window_right):
                swings.append(SwingPivot(idx=i, ts=cb.ts, price=cb.high, side="high"))
            if cb.low < min(b.low for b in window_left + window_right):
                swings.append(SwingPivot(idx=i, ts=cb.ts, price=cb.low, side="low"))
        return swings

    # ----- Order Blocks ----------------------------------------------

    def _detect_order_blocks(self, bars: list[Bar], atr: float) -> list[OrderBlock]:
        if atr <= 0:
            return []
        out: list[OrderBlock] = []
        N = self.ob_impulse_bars
        # OB candidate sits at i-N (the bar before the N-bar impulse ending at i).
        # We need i-N >= 0  =>  i >= N.
        for i in range(N, len(bars)):
            impulse = bars[i - N + 1:i + 1]
            cum_move = impulse[-1].close - impulse[0].open
            if abs(cum_move) < self.ob_atr_mult * atr:
                continue
            # All impulse bars closed in same direction
            if cum_move > 0 and not all(b.close > b.open for b in impulse):
                continue
            if cum_move < 0 and not all(b.close < b.open for b in impulse):
                continue
            # OB candidate is the bar immediately before the impulse
            ob_idx = i - N
            ob_bar = bars[ob_idx]
            if cum_move > 0 and ob_bar.close < ob_bar.open:
                # bullish OB: last red candle before up-impulse
                out.append(OrderBlock(side="bullish", bar_ts=ob_bar.ts,
                                      high=ob_bar.high, low=ob_bar.low,
                                      confirmed_at_idx=i))
            elif cum_move < 0 and ob_bar.close > ob_bar.open:
                # bearish OB: last green candle before down-impulse
                out.append(OrderBlock(side="bearish", bar_ts=ob_bar.ts,
                                      high=ob_bar.high, low=ob_bar.low,
                                      confirmed_at_idx=i))
        return out

    # ----- Fair Value Gaps (3-candle pattern) ------------------------

    def _detect_fvgs(self, bars: list[Bar]) -> list[FVG]:
        out: list[FVG] = []
        for i in range(2, len(bars)):
            b1, b2, b3 = bars[i - 2], bars[i - 1], bars[i]
            if b1.high < b3.low:
                # bullish FVG: gap between bar 1 high and bar 3 low
                out.append(FVG(side="bullish", low=b1.high,
                               high=b3.low, bar_ts=b2.ts))
            elif b1.low > b3.high:
                # bearish FVG
                out.append(FVG(side="bearish", low=b3.high,
                               high=b1.low, bar_ts=b2.ts))
        return out

    # ----- CHoCH detection -------------------------------------------

    def _detect_last_choch(
        self, bars: list[Bar], swings: list[SwingPivot]
    ) -> Optional[CHoCHEvent]:
        """
        CHoCH = first close BEYOND the most recent opposite-side swing pivot,
        AGAINST the prevailing direction.

        We approximate "prevailing direction" as the slope of the last two
        same-side swings. If the last two swing-highs are descending AND the
        most recent swing is a swing-low, trend is DOWN. A close ABOVE the
        most recent swing-high after that = CHoCH up.
        """
        if len(swings) < 3:
            return None

        # Walk forwards in time; when we see a close-through, that's the CHoCH
        for i in range(self.swing_left + self.swing_right + 2, len(bars)):
            close = bars[i].close
            # Find the most recent swing CONFIRMED before bar i (i.e. its idx
            # plus right-window must be <= i)
            recent_swing = None
            for s in reversed(swings):
                if s.idx + self.swing_right < i:
                    recent_swing = s
                    break
            if recent_swing is None:
                continue
            # Check trend direction from the LAST TWO same-side swings
            same_side = [s for s in swings
                         if s.side == recent_swing.side and s.idx + self.swing_right < i]
            if len(same_side) < 2:
                continue
            prev_same = same_side[-2]
            curr_same = same_side[-1]
            if curr_same.side == "high":
                trending_up = curr_same.price > prev_same.price
            else:  # low
                trending_up = curr_same.price > prev_same.price
            # CHoCH up: was trending DOWN, close closes above most recent high
            recent_high = next((s for s in reversed(swings)
                                 if s.side == "high" and s.idx + self.swing_right < i),
                                None)
            recent_low = next((s for s in reversed(swings)
                                if s.side == "low" and s.idx + self.swing_right < i),
                               None)
            if not trending_up and recent_high and close > recent_high.price:
                return CHoCHEvent(side="up", idx=i, ts=bars[i].ts,
                                   broken_pivot_price=recent_high.price,
                                   impulse_start_idx=recent_low.idx if recent_low else 0)
            if trending_up and recent_low and close < recent_low.price:
                return CHoCHEvent(side="down", idx=i, ts=bars[i].ts,
                                   broken_pivot_price=recent_low.price,
                                   impulse_start_idx=recent_high.idx if recent_high else 0)
        return None

    # ----- Helpers ----------------------------------------------------

    @staticmethod
    def golden_zone(swing_start_price: float, swing_end_price: float,
                    fib_low: float = 0.618, fib_high: float = 0.786
                    ) -> tuple[float, float]:
        """
        Returns (low, high) of the retracement zone bounded by the two
        Fibonacci levels (default Golden Zone 0.618-0.786). The zone is
        always (lower-price, higher-price).
        """
        diff = abs(swing_end_price - swing_start_price)
        if swing_end_price > swing_start_price:
            # bullish leg, retracement is below swing_end
            zone_low = swing_end_price - fib_high * diff
            zone_high = swing_end_price - fib_low * diff
        else:
            # bearish leg, retracement is above swing_end
            zone_low = swing_end_price + fib_low * diff
            zone_high = swing_end_price + fib_high * diff
        return (zone_low, zone_high)


# ---------------------------------------------------------------------------
# IEFTactic
# ---------------------------------------------------------------------------

@dataclass
class IEFConfig(TacticConfig):
    name: str = "ief"
    min_history_bars: int = 25         # need enough bars for swings + CHoCH
    choch_lookback_bars: int = 20      # CHoCH must be within this many bars
    require_ob_or_fvg_confluence: bool = True
    # Golden Zone bounds (Phase 11 evidence: stricter 0.618-0.786 was over-
    # rejecting +Rs 15,223 of hypothetical winners; widen to 0.50-0.886).
    fib_low: float = 0.50
    fib_high: float = 0.886
    dte_min: int = 1                    # Phase 11: was 2; relax to 1
    sl_pct: float = 0.30
    tp_pct: float = 0.50
    time_stop_min: int = 120
    strike_offset: int = 1   # 1-strike ITM


class IEFTactic(Tactic):
    config: IEFConfig

    def __init__(self, config: Optional[IEFConfig] = None):
        super().__init__(config or IEFConfig())
        self.analyzer = IEFAnalyzer()

    # ----- Main path --------------------------------------------------

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        cfg = self.config
        if state.is_in_position:
            return None
        if not self.in_session_window(state):
            return None
        if state.dte < cfg.dte_min:
            return None
        if state.atr_5m <= 0:
            return None
        if len(state.recent_5m_bars) < cfg.min_history_bars:
            return None

        bars = [Bar.from_tuple(t) for t in state.recent_5m_bars]
        analysis = self.analyzer.analyze(bars, state.atr_5m)

        if analysis.last_choch is None:
            return None
        choch = analysis.last_choch

        # Ensure CHoCH is recent
        bars_since = (len(bars) - 1) - choch.idx
        if bars_since > cfg.choch_lookback_bars:
            return None
        if bars_since < 1:
            return None  # need at least one bar after CHoCH for confirmation

        # Determine impulse leg endpoints to compute golden zone
        if choch.side == "up":
            # Impulse leg: from swing-low BEFORE the broken-pivot up to the
            # broken-pivot (the swing high that was taken out)
            impulse_start = analysis.swings[0].price if analysis.swings else bars[0].low
            for s in analysis.swings:
                if s.side == "low" and s.idx <= choch.idx:
                    impulse_start = s.price
            zone = self.analyzer.golden_zone(
                impulse_start, choch.broken_pivot_price,
                fib_low=cfg.fib_low, fib_high=cfg.fib_high,
            )
            return self._evaluate_long(state, bars, analysis, choch, zone)
        else:  # down
            impulse_start = analysis.swings[0].price if analysis.swings else bars[0].high
            for s in analysis.swings:
                if s.side == "high" and s.idx <= choch.idx:
                    impulse_start = s.price
            zone = self.analyzer.golden_zone(
                impulse_start, choch.broken_pivot_price,
                fib_low=cfg.fib_low, fib_high=cfg.fib_high,
            )
            return self._evaluate_short(state, bars, analysis, choch, zone)

    def _evaluate_long(
        self,
        state: TacticState,
        bars: list[Bar],
        analysis: IEFAnalysis,
        choch: CHoCHEvent,
        zone: tuple[float, float],
    ) -> Optional[TacticSignal]:
        cfg = self.config
        zone_low, zone_high = zone
        spot = state.spot

        # Price must be back inside the golden zone
        if not (zone_low <= spot <= zone_high):
            return None

        # Confluence: bullish OB or bullish FVG inside the zone
        if cfg.require_ob_or_fvg_confluence:
            confluence = any(
                ob.side == "bullish" and zone_low <= ob.high and ob.low <= zone_high
                for ob in analysis.order_blocks
            ) or any(
                fvg.side == "bullish" and zone_low <= fvg.high and fvg.low <= zone_high
                for fvg in analysis.fvgs
            )
            if not confluence:
                return None

        # Confirmation: current 5m candle is bullish AND closed > prev close
        if state.bar_close <= state.bar_open:
            return None
        if state.bar_close <= state.prev_bar_close:
            return None

        return TacticSignal(
            action="enter",
            direction="CE",
            strike_offset=cfg.strike_offset,
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            reason=(f"IEF LONG: CHoCH up at {choch.ts.strftime('%H:%M')}, "
                    f"price {spot:.0f} in golden zone "
                    f"[{zone_low:.0f},{zone_high:.0f}], confluence + reclaim"),
        )

    def _evaluate_short(
        self,
        state: TacticState,
        bars: list[Bar],
        analysis: IEFAnalysis,
        choch: CHoCHEvent,
        zone: tuple[float, float],
    ) -> Optional[TacticSignal]:
        cfg = self.config
        zone_low, zone_high = zone
        spot = state.spot

        if not (zone_low <= spot <= zone_high):
            return None

        if cfg.require_ob_or_fvg_confluence:
            confluence = any(
                ob.side == "bearish" and zone_low <= ob.high and ob.low <= zone_high
                for ob in analysis.order_blocks
            ) or any(
                fvg.side == "bearish" and zone_low <= fvg.high and fvg.low <= zone_high
                for fvg in analysis.fvgs
            )
            if not confluence:
                return None

        if state.bar_close >= state.bar_open:
            return None
        if state.bar_close >= state.prev_bar_close:
            return None

        return TacticSignal(
            action="enter",
            direction="PE",
            strike_offset=cfg.strike_offset,
            sl_pct=cfg.sl_pct,
            tp_pct=cfg.tp_pct,
            time_stop_min=cfg.time_stop_min,
            reason=(f"IEF SHORT: CHoCH down at {choch.ts.strftime('%H:%M')}, "
                    f"price {spot:.0f} in golden zone "
                    f"[{zone_low:.0f},{zone_high:.0f}], confluence + reject"),
        )

    # ----- Diagnostic gates (for near-miss capture) ------------------

    def gates_for_direction(
        self, state: TacticState, direction: str
    ) -> dict[str, GateResult]:
        cfg = self.config
        gates: dict[str, GateResult] = {}

        gates["session_window"] = GateResult(
            self.in_session_window(state),
            state.ts.time().isoformat(timespec='minutes'),
            f"{cfg.no_entry_before}-{cfg.no_entry_after}",
            f"time {state.ts.time().isoformat(timespec='minutes')} in entry window",
        )
        gates["dte_ok"] = GateResult(
            state.dte >= cfg.dte_min, state.dte, cfg.dte_min,
            f"DTE {state.dte} >= {cfg.dte_min}",
        )
        gates["atr_positive"] = GateResult(
            state.atr_5m > 0, state.atr_5m, 0.0,
        )
        gates["enough_history"] = GateResult(
            len(state.recent_5m_bars) >= cfg.min_history_bars,
            len(state.recent_5m_bars), cfg.min_history_bars,
            f"history bars {len(state.recent_5m_bars)} >= {cfg.min_history_bars}",
        )

        if len(state.recent_5m_bars) < cfg.min_history_bars or state.atr_5m <= 0:
            return gates

        bars = [Bar.from_tuple(t) for t in state.recent_5m_bars]
        analysis = self.analyzer.analyze(bars, state.atr_5m)

        choch = analysis.last_choch
        gates["choch_present"] = GateResult(
            choch is not None,
            "yes" if choch else "no",
            "yes",
        )
        if choch is None:
            return gates

        bars_since = (len(bars) - 1) - choch.idx
        gates["choch_recent"] = GateResult(
            1 <= bars_since <= cfg.choch_lookback_bars,
            bars_since, cfg.choch_lookback_bars,
            f"bars-since-CHoCH {bars_since} within [1, {cfg.choch_lookback_bars}]",
        )

        # Compute golden zone
        if direction == "CE" and choch.side == "up":
            impulse_start = next((s.price for s in reversed(analysis.swings)
                                  if s.side == "low" and s.idx <= choch.idx),
                                 bars[0].low)
            zone = self.analyzer.golden_zone(
                impulse_start, choch.broken_pivot_price,
                fib_low=cfg.fib_low, fib_high=cfg.fib_high,
            )
            gates["choch_direction_match"] = GateResult(
                True, "up", "up", "CHoCH side matches CE direction",
            )
            gates["price_in_golden_zone"] = GateResult(
                zone[0] <= state.spot <= zone[1], state.spot, f"[{zone[0]:.0f},{zone[1]:.0f}]",
                f"spot {state.spot:.0f} in zone [{zone[0]:.0f},{zone[1]:.0f}]",
            )
            confluence = any(
                ob.side == "bullish" and zone[0] <= ob.high and ob.low <= zone[1]
                for ob in analysis.order_blocks
            ) or any(
                fvg.side == "bullish" and zone[0] <= fvg.high and fvg.low <= zone[1]
                for fvg in analysis.fvgs
            )
            gates["ob_or_fvg_confluence"] = GateResult(
                confluence, "yes" if confluence else "no", "yes",
            )
            gates["bullish_close"] = GateResult(
                state.bar_close > state.bar_open, state.bar_close, state.bar_open,
                f"close {state.bar_close:.0f} > open {state.bar_open:.0f}",
            )
            gates["close_higher_than_prev"] = GateResult(
                state.bar_close > state.prev_bar_close,
                state.bar_close, state.prev_bar_close,
            )
        elif direction == "PE" and choch.side == "down":
            impulse_start = next((s.price for s in reversed(analysis.swings)
                                  if s.side == "high" and s.idx <= choch.idx),
                                 bars[0].high)
            zone = self.analyzer.golden_zone(
                impulse_start, choch.broken_pivot_price,
                fib_low=cfg.fib_low, fib_high=cfg.fib_high,
            )
            gates["choch_direction_match"] = GateResult(
                True, "down", "down", "CHoCH side matches PE direction",
            )
            gates["price_in_golden_zone"] = GateResult(
                zone[0] <= state.spot <= zone[1], state.spot, f"[{zone[0]:.0f},{zone[1]:.0f}]",
            )
            confluence = any(
                ob.side == "bearish" and zone[0] <= ob.high and ob.low <= zone[1]
                for ob in analysis.order_blocks
            ) or any(
                fvg.side == "bearish" and zone[0] <= fvg.high and fvg.low <= zone[1]
                for fvg in analysis.fvgs
            )
            gates["ob_or_fvg_confluence"] = GateResult(
                confluence, "yes" if confluence else "no", "yes",
            )
            gates["bearish_close"] = GateResult(
                state.bar_close < state.bar_open, state.bar_close, state.bar_open,
            )
            gates["close_lower_than_prev"] = GateResult(
                state.bar_close < state.prev_bar_close,
                state.bar_close, state.prev_bar_close,
            )
        else:
            # CHoCH side doesn't match desired direction
            gates["choch_direction_match"] = GateResult(
                False,
                choch.side, direction,
                f"CHoCH is {choch.side} but evaluating {direction}",
            )

        return gates
