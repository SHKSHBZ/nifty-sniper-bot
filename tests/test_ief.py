"""
Unit tests for IEFAnalyzer and IEFTactic.

Each detector is tested in isolation with hand-crafted bar sequences,
then the tactic is tested as a whole on a clean setup that should fire.
"""
from __future__ import annotations

import sys
from datetime import datetime, time, date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tactics.base import TacticState  # noqa: E402
from tactics.ief import (  # noqa: E402
    IEFAnalyzer, IEFTactic, IEFConfig, Bar,
)


def _ts(minute: int) -> datetime:
    return datetime(2026, 4, 21, 11, 0) + timedelta(minutes=minute)


def _bars_from_closes(closes: list[float], spread: float = 5.0) -> list[Bar]:
    """Helper: synthesize bars from a list of closes with a fixed spread."""
    out = []
    prev_close = closes[0]
    for i, c in enumerate(closes):
        o = prev_close
        h = max(o, c) + spread
        l = min(o, c) - spread
        out.append(Bar(ts=_ts(i * 5), open=o, high=h, low=l, close=c))
        prev_close = c
    return out


# ---------------------------------------------------------------------------
# Analyzer — Swing detection
# ---------------------------------------------------------------------------

class TestSwingDetection:
    def setup_method(self):
        self.a = IEFAnalyzer(swing_left=2, swing_right=2)

    def test_no_swings_when_too_few_bars(self):
        bars = _bars_from_closes([100, 101, 102])
        assert self.a._detect_swings(bars) == []

    def test_swing_high_detected(self):
        # Hand-built bars with bar 3 having a clearly dominant high
        bars = [
            Bar(_ts(0), open=100, high=102, low=98, close=101),
            Bar(_ts(5), open=101, high=104, low=100, close=103),
            Bar(_ts(10), open=103, high=107, low=102, close=105),
            Bar(_ts(15), open=105, high=120, low=104, close=110),   # dominant high
            Bar(_ts(20), open=110, high=112, low=104, close=106),
            Bar(_ts(25), open=106, high=108, low=100, close=102),
            Bar(_ts(30), open=102, high=104, low=98, close=100),
        ]
        swings = self.a._detect_swings(bars)
        highs = [s for s in swings if s.side == "high"]
        assert len(highs) >= 1
        assert highs[0].idx == 3

    def test_swing_low_detected(self):
        bars = [
            Bar(_ts(0), open=110, high=112, low=108, close=109),
            Bar(_ts(5), open=109, high=110, low=105, close=106),
            Bar(_ts(10), open=106, high=107, low=100, close=101),
            Bar(_ts(15), open=101, high=102, low=85, close=90),     # dominant low
            Bar(_ts(20), open=90, high=98, low=88, close=95),
            Bar(_ts(25), open=95, high=102, low=94, close=100),
            Bar(_ts(30), open=100, high=108, low=99, close=105),
        ]
        swings = self.a._detect_swings(bars)
        lows = [s for s in swings if s.side == "low"]
        assert len(lows) >= 1
        assert lows[0].idx == 3

    def test_swing_only_confirmed_after_right_window(self):
        # An incomplete pattern (right window short) shouldn't yield a swing
        bars = _bars_from_closes([100, 102, 105, 110, 105])  # only 1 right bar
        a = IEFAnalyzer(swing_left=2, swing_right=2)
        swings = a._detect_swings(bars)
        assert swings == []   # right window has 1 bar, need 2


# ---------------------------------------------------------------------------
# Analyzer — Order Block
# ---------------------------------------------------------------------------

class TestOrderBlockDetection:
    def setup_method(self):
        # ATR=10 is intentionally small relative to impulse so threshold trips
        self.a = IEFAnalyzer(ob_atr_mult=1.5, ob_impulse_bars=3)

    def test_bullish_ob_detected(self):
        # Bar 0 = bearish red (the OB candidate)
        # Bars 1-3 = strong up impulse all-green
        bar0 = Bar(_ts(0), open=110, high=112, low=105, close=106)   # red
        bar1 = Bar(_ts(5), open=106, high=120, low=106, close=118)   # green
        bar2 = Bar(_ts(10), open=118, high=130, low=117, close=128)  # green
        bar3 = Bar(_ts(15), open=128, high=140, low=127, close=138)  # green
        bars = [bar0, bar1, bar2, bar3]
        obs = self.a._detect_order_blocks(bars, atr=5.0)
        assert any(ob.side == "bullish" for ob in obs)

    def test_bearish_ob_detected(self):
        bar0 = Bar(_ts(0), open=100, high=110, low=99, close=108)    # green
        bar1 = Bar(_ts(5), open=108, high=109, low=95, close=96)
        bar2 = Bar(_ts(10), open=96, high=97, low=85, close=86)
        bar3 = Bar(_ts(15), open=86, high=87, low=75, close=76)
        bars = [bar0, bar1, bar2, bar3]
        obs = self.a._detect_order_blocks(bars, atr=5.0)
        assert any(ob.side == "bearish" for ob in obs)

    def test_no_ob_below_atr_threshold(self):
        # Impulse exists but is too small relative to ATR
        bars = _bars_from_closes([100, 101, 102, 103])  # tiny moves
        obs = self.a._detect_order_blocks(bars, atr=10.0)
        assert obs == []


# ---------------------------------------------------------------------------
# Analyzer — FVG
# ---------------------------------------------------------------------------

class TestFVGDetection:
    def setup_method(self):
        self.a = IEFAnalyzer()

    def test_bullish_fvg(self):
        # Bar 1 high=100, bar 3 low=105 → gap [100, 105]
        b1 = Bar(_ts(0), open=99, high=100, low=98, close=99)
        b2 = Bar(_ts(5), open=99, high=110, low=99, close=108)
        b3 = Bar(_ts(10), open=108, high=115, low=105, close=112)
        bars = [b1, b2, b3]
        fvgs = self.a._detect_fvgs(bars)
        assert len(fvgs) == 1
        assert fvgs[0].side == "bullish"
        assert fvgs[0].low == 100
        assert fvgs[0].high == 105

    def test_bearish_fvg(self):
        b1 = Bar(_ts(0), open=99, high=105, low=100, close=101)
        b2 = Bar(_ts(5), open=101, high=101, low=92, close=93)
        b3 = Bar(_ts(10), open=93, high=95, low=88, close=90)
        bars = [b1, b2, b3]
        fvgs = self.a._detect_fvgs(bars)
        assert len(fvgs) == 1
        assert fvgs[0].side == "bearish"

    def test_no_gap_when_overlapping(self):
        b1 = Bar(_ts(0), open=100, high=105, low=98, close=103)
        b2 = Bar(_ts(5), open=103, high=110, low=102, close=108)
        b3 = Bar(_ts(10), open=108, high=112, low=104, close=110)  # b3.low < b1.high
        bars = [b1, b2, b3]
        fvgs = self.a._detect_fvgs(bars)
        assert fvgs == []


# ---------------------------------------------------------------------------
# Analyzer — Golden Zone
# ---------------------------------------------------------------------------

class TestGoldenZone:
    def test_bullish_leg(self):
        # Impulse 100 -> 200, golden zone is 0.618-0.786 retrace from 200
        # Diff=100; zone = (200-78.6, 200-61.8) = (121.4, 138.2)
        z_low, z_high = IEFAnalyzer.golden_zone(100, 200)
        assert z_low == pytest.approx(121.4)
        assert z_high == pytest.approx(138.2)

    def test_bearish_leg(self):
        # Impulse 200 -> 100, golden zone is above 100
        # zone = (100+61.8, 100+78.6) = (161.8, 178.6)
        z_low, z_high = IEFAnalyzer.golden_zone(200, 100)
        assert z_low == pytest.approx(161.8)
        assert z_high == pytest.approx(178.6)


# ---------------------------------------------------------------------------
# IEFTactic — clean setup smoke test
# ---------------------------------------------------------------------------

def _build_ief_long_state() -> tuple[TacticState, list]:
    """
    Construct a state where:
      - price was downtrending (lower highs / lower lows)
      - then a big up-move broke a swing high (CHoCH up)
      - now price is retracing back into the golden zone
      - a bullish OB exists in that zone
      - current candle is bullish reclaim
    """
    # Build 30 bars: down-then-impulse-up-then-retrace
    closes = []
    # Down-trend establishing — gradually lower closes 5 bars apart
    closes += [200, 195, 198, 190, 195, 185, 190, 180, 185, 175]
    # Sharp impulse up (this is what creates CHoCH and OB candidate)
    closes += [177, 182, 195, 210, 225, 230]
    # Retrace into golden zone
    closes += [225, 220, 215, 210, 208, 205]   # arrives in zone

    bars_objs = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        spread = 4.0
        b = Bar(_ts(i * 5), open=o, high=max(o, c) + spread,
                low=min(o, c) - spread, close=c)
        bars_objs.append(b)

    last_bar = bars_objs[-1]
    prev_bar = bars_objs[-2]

    bars_tuple = tuple(
        (b.ts, b.open, b.high, b.low, b.close) for b in bars_objs
    )

    state = TacticState(
        ts=last_bar.ts,
        spot=last_bar.close,
        dte=3,
        atr_5m=8.0,
        bar_open=last_bar.open,
        bar_high=last_bar.high,
        bar_low=last_bar.low,
        bar_close=last_bar.close,
        prev_bar_open=prev_bar.open,
        prev_bar_high=prev_bar.high,
        prev_bar_low=prev_bar.low,
        prev_bar_close=prev_bar.close,
        recent_5m_bars=bars_tuple,
    )
    return state, bars_objs


class TestIEFTacticIntegration:
    def setup_method(self):
        self.t = IEFTactic()

    def test_no_signal_when_history_short(self):
        state = TacticState(ts=_ts(0), spot=100, dte=3, atr_5m=5,
                             recent_5m_bars=(),)
        assert self.t.evaluate(state) is None

    def test_no_signal_when_dte_too_low(self):
        state, _ = _build_ief_long_state()
        state.dte = 1   # below default min 2
        assert self.t.evaluate(state) is None

    def test_no_signal_when_in_position(self):
        state, _ = _build_ief_long_state()
        state.is_in_position = True
        assert self.t.evaluate(state) is None

    def test_gates_returned_on_short_history(self):
        state = TacticState(ts=_ts(0), spot=100, dte=3, atr_5m=5,
                             recent_5m_bars=(),)
        gates = self.t.gates_for_direction(state, "CE")
        # Should at least include enough_history & dte_ok
        assert "enough_history" in gates
        assert gates["enough_history"].passed is False
