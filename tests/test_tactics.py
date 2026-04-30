"""
Unit tests for the four tactics in `tactics/`.

Each tactic gets a focused suite that:
  - Confirms a clean SETUP fires the signal
  - Confirms each gate (VIX / PCR / time / DTE / regime / etc.) blocks the
    signal independently when violated
  - Confirms in-position state correctly suppresses fresh entries

No real market data is needed — we construct TacticState objects directly.
"""
from __future__ import annotations

import sys
from datetime import datetime, time, date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tactics import (  # noqa: E402
    TacticState,
    VWAPHybridTactic,
    TrendPullbackTactic,
    BullishORBTactic,
    BearishORBTactic,
)
from tactics.vwap_hybrid import VWAPHybridConfig  # noqa: E402
from tactics.trend_pullback import TrendPullbackConfig  # noqa: E402
from tactics.bullish_orb import BullishORBConfig  # noqa: E402
from tactics.bearish_orb import BearishORBConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — fixtures
# ---------------------------------------------------------------------------

def _ts(hour: int, minute: int = 0, d=date(2026, 4, 21)) -> datetime:
    return datetime.combine(d, time(hour, minute))


def _vwap_long_state(**overrides) -> TacticState:
    """A clean LONG (CE) setup for VWAPHybridTactic."""
    base = TacticState(
        ts=_ts(11, 0),
        spot=24800.0,
        futures=24800.0,
        dte=3,
        day_open=25000.0,
        day_high=25050.0,
        day_low=24800.0,        # we are right at LoD
        prev_day_close=24990.0,
        vwap=25000.0,
        atr_5m=30.0,
        bar_open=24795.0,
        bar_high=24820.0,
        bar_low=24800.0,        # touches LoD
        bar_close=24815.0,      # close > prev_mid
        bar_volume=100_000,
        prev_bar_open=24820.0,
        prev_bar_high=24830.0,
        prev_bar_low=24795.0,
        prev_bar_close=24805.0,
        focus_pcr=1.05,         # not bearish
        ce_oi_change=0.0,
        pe_oi_change=600_000,   # support being defended
        vix_level=15.0,         # below 18 -> CE allowed
        regime="RANGE",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _trend_long_state(**overrides) -> TacticState:
    base = TacticState(
        ts=_ts(11, 0),
        spot=24850.0,
        futures=24850.0,
        dte=3,
        vwap=24800.0,
        ema9_5m=24820.0,
        atr_5m=30.0,
        adx_15m=28.0,
        range_ratio=1.4,
        bar_open=24830.0,
        bar_high=24855.0,
        bar_low=24825.0,
        bar_close=24850.0,
        prev_bar_close=24830.0,
        recent_5m_lows=(24840.0, 24818.0, 24838.0),  # one within 0.1% of EMA9
        ce_oi_change=300_000,
        pe_oi_change=600_000,    # ratio 2.0 ✓ AND magnitude >= 500k ✓
        vix_level=18.0,
        regime="TREND_UP",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _bullish_orb_state(**overrides) -> TacticState:
    base = TacticState(
        ts=_ts(10, 0),
        spot=25150.0,
        futures=25150.0,
        dte=3,
        day_open=25080.0,
        day_high=25180.0,
        prev_day_close=24930.0,    # gap = (25080-24930)/24930 = 0.6% > 0.5% ✓
        or_high=25100.0,
        or_low=25070.0,
        or_volume_avg=80_000,
        atr_5m=40.0,
        bar_open=25130.0,
        bar_high=25180.0,
        bar_low=25115.0,
        bar_close=25150.0,         # close > OR_high ✓
        bar_volume=130_000,        # 1.625x avg vol > 1.5 ✓
        prev_bar_close=25120.0,    # prev close > OR_high ✓ (confirmation)
        vix_level=18.0,
        vix_chg_15m=0.05,
        regime="TREND_UP_GAP",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _bearish_orb_state(**overrides) -> TacticState:
    base = TacticState(
        ts=_ts(10, 0),
        spot=24800.0,
        futures=24800.0,
        dte=3,
        day_open=24850.0,
        day_low=24780.0,
        prev_day_close=25000.0,    # gap = (24850-25000)/25000 = -0.6% > 0.5% down ✓
        or_high=24880.0,
        or_low=24830.0,
        or_volume_avg=80_000,
        atr_5m=40.0,
        bar_open=24820.0,
        bar_high=24830.0,
        bar_low=24790.0,
        bar_close=24800.0,         # close < OR_low ✓
        bar_volume=130_000,
        prev_bar_close=24820.0,    # prev close < OR_low ✓
        vix_level=22.0,
        vix_chg_15m=0.10,
        regime="TREND_DOWN_GAP",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ---------------------------------------------------------------------------
# VWAP Hybrid
# ---------------------------------------------------------------------------

class TestVWAPHybrid:
    def setup_method(self):
        self.t = VWAPHybridTactic()

    def test_clean_long_setup_fires(self):
        sig = self.t.evaluate(_vwap_long_state())
        assert sig is not None
        assert sig.action == "enter"
        assert sig.direction == "CE"

    def test_in_position_blocks_signal(self):
        st = _vwap_long_state(is_in_position=True)
        assert self.t.evaluate(st) is None

    def test_outside_session_blocks_signal(self):
        st = _vwap_long_state(ts=_ts(9, 45))
        assert self.t.evaluate(st) is None

    def test_high_vix_blocks_long(self):
        st = _vwap_long_state(vix_level=20.0)
        assert self.t.evaluate(st) is None

    def test_bearish_pcr_blocks_long(self):
        st = _vwap_long_state(focus_pcr=0.80)
        assert self.t.evaluate(st) is None

    def test_no_pe_oi_buildup_blocks_long(self):
        st = _vwap_long_state(pe_oi_change=0)
        assert self.t.evaluate(st) is None

    def test_low_dte_blocks_signal(self):
        st = _vwap_long_state(dte=1)
        assert self.t.evaluate(st) is None

    def test_no_extension_blocks_signal(self):
        # Place spot at VWAP — no extension
        st = _vwap_long_state(spot=25000.0, bar_close=25010.0)
        assert self.t.evaluate(st) is None

    def test_no_reclaim_blocks_signal(self):
        # close below midpoint of prev candle
        st = _vwap_long_state(bar_close=24800.0)
        assert self.t.evaluate(st) is None


# ---------------------------------------------------------------------------
# Trend Pullback
# ---------------------------------------------------------------------------

class TestTrendPullback:
    def setup_method(self):
        self.t = TrendPullbackTactic()

    def test_clean_long_setup_fires(self):
        sig = self.t.evaluate(_trend_long_state())
        assert sig is not None
        assert sig.action == "enter"
        assert sig.direction == "CE"

    def test_wrong_regime_blocks_signal(self):
        st = _trend_long_state(regime="RANGE")
        assert self.t.evaluate(st) is None

    def test_low_adx_blocks_signal(self):
        st = _trend_long_state(adx_15m=18.0)
        assert self.t.evaluate(st) is None

    def test_high_vix_blocks_signal(self):
        st = _trend_long_state(vix_level=25.0)
        assert self.t.evaluate(st) is None

    def test_weak_oi_bias_blocks_signal(self):
        st = _trend_long_state(pe_oi_change=200_000, ce_oi_change=200_000)  # ratio=1.0
        assert self.t.evaluate(st) is None

    def test_below_vwap_blocks_long(self):
        st = _trend_long_state(spot=24700.0, ema9_5m=24750.0,
                                recent_5m_lows=(24710.0, 24700.0, 24750.0))
        assert self.t.evaluate(st) is None

    def test_no_pullback_blocks_signal(self):
        # All recent lows far above EMA9
        st = _trend_long_state(recent_5m_lows=(24900.0, 24905.0, 24910.0))
        assert self.t.evaluate(st) is None

    def test_short_setup_fires_in_trend_down(self):
        st = TacticState(
            ts=_ts(11, 0),
            spot=24750.0,
            dte=3,
            vwap=24800.0,
            ema9_5m=24780.0,
            atr_5m=30.0,
            adx_15m=28.0,
            bar_open=24770.0,
            bar_high=24775.0,
            bar_low=24745.0,
            bar_close=24750.0,
            prev_bar_close=24770.0,
            recent_5m_highs=(24760.0, 24782.0, 24762.0),  # touched EMA9
            ce_oi_change=600_000,
            pe_oi_change=300_000,    # ratio 2.0 + magnitude
            vix_level=18.0,
            regime="TREND_DOWN",
        )
        sig = self.t.evaluate(st)
        assert sig is not None
        assert sig.direction == "PE"


# ---------------------------------------------------------------------------
# Bullish ORB
# ---------------------------------------------------------------------------

class TestBullishORB:
    def setup_method(self):
        self.t = BullishORBTactic()

    def test_clean_setup_fires(self):
        sig = self.t.evaluate(_bullish_orb_state())
        assert sig is not None
        assert sig.action == "enter"
        assert sig.direction == "CE"
        assert sig.qty_pct_of_intended == 0.50  # inverted pyramid lot 1

    def test_no_gap_blocks_signal(self):
        st = _bullish_orb_state(day_open=24990.0)   # gap < 0.5%
        assert self.t.evaluate(st) is None

    def test_high_vix_blocks_signal(self):
        st = _bullish_orb_state(vix_level=23.0)
        assert self.t.evaluate(st) is None

    def test_after_window_blocks_signal(self):
        st = _bullish_orb_state(ts=_ts(10, 35))   # past 10:30
        assert self.t.evaluate(st) is None

    def test_low_volume_blocks_signal(self):
        st = _bullish_orb_state(bar_volume=80_000)   # 1.0x, below 1.5x
        assert self.t.evaluate(st) is None

    def test_no_confirmation_blocks_signal(self):
        st = _bullish_orb_state(prev_bar_close=25080.0)  # prev didn't close above OR_high
        assert self.t.evaluate(st) is None

    def test_pyramid_lot_2_fires(self):
        st = _bullish_orb_state(
            is_in_position=True,
            open_position_direction="CE",
            open_position_lots_added=0,
            spot=25115.0,           # +15 pts past OR_high; with ATR=40, 0.25*ATR=10 ✓
        )
        sig = self.t.evaluate(st)
        assert sig is not None
        assert sig.action == "add_lot"
        assert sig.qty_pct_of_intended == 0.30

    def test_pyramid_lot_3_fires(self):
        st = _bullish_orb_state(
            is_in_position=True,
            open_position_direction="CE",
            open_position_lots_added=1,
            spot=25130.0,           # +30 pts past OR_high; 0.5*ATR=20 ✓
        )
        sig = self.t.evaluate(st)
        assert sig is not None
        assert sig.action == "add_lot"
        assert sig.qty_pct_of_intended == 0.20

    def test_max_adds_blocks_further_pyramiding(self):
        st = _bullish_orb_state(
            is_in_position=True,
            open_position_direction="CE",
            open_position_lots_added=2,
            spot=25200.0,
        )
        assert self.t.evaluate(st) is None


# ---------------------------------------------------------------------------
# Bearish ORB
# ---------------------------------------------------------------------------

class TestBearishORB:
    def setup_method(self):
        self.t = BearishORBTactic()

    def test_clean_setup_fires(self):
        sig = self.t.evaluate(_bearish_orb_state())
        assert sig is not None
        assert sig.action == "enter"
        assert sig.direction == "PE"

    def test_no_gap_down_blocks_signal(self):
        st = _bearish_orb_state(day_open=25010.0)   # actually gap UP
        assert self.t.evaluate(st) is None

    def test_high_vix_blocks_signal(self):
        st = _bearish_orb_state(vix_level=26.0)
        assert self.t.evaluate(st) is None

    def test_after_tighter_window_blocks_signal(self):
        st = _bearish_orb_state(ts=_ts(10, 20))   # past 10:15
        assert self.t.evaluate(st) is None

    def test_no_breakdown_blocks_signal(self):
        st = _bearish_orb_state(bar_close=24840.0)  # close above OR_low
        assert self.t.evaluate(st) is None

    def test_pyramid_lot_2_fires(self):
        st = _bearish_orb_state(
            is_in_position=True,
            open_position_direction="PE",
            open_position_lots_added=0,
            spot=24815.0,           # -15 pts below OR_low; 0.25*ATR=10 ✓
        )
        sig = self.t.evaluate(st)
        assert sig is not None
        assert sig.action == "add_lot"

    def test_max_adds_blocks(self):
        st = _bearish_orb_state(
            is_in_position=True,
            open_position_direction="PE",
            open_position_lots_added=2,
            spot=24700.0,
        )
        assert self.t.evaluate(st) is None
