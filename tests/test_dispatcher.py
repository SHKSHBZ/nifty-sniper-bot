"""
Unit tests for IndicatorTracker and TacticDispatcher.

The dispatcher is the runtime glue that lets main.py call ONE method
to get either a legacy SignalEngine signal (RANGE / legacy mode) or a
multi-tactic regime-routed signal. Both modes must produce the same
dict shape so the existing entry-decision code doesn't break.

We use lightweight stubs (not real DataFetcher / SignalEngine) so tests
stay hermetic.
"""
from __future__ import annotations

import sys
from datetime import datetime, time, date
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime import (  # noqa: E402
    TacticDispatcher, IndicatorTracker, Regime,
)


# ---------------------------------------------------------------------------
# IndicatorTracker tests
# ---------------------------------------------------------------------------

class TestIndicatorTracker:
    def setup_method(self):
        self.t = IndicatorTracker()
        self.t.start_day(date(2026, 4, 21), prev_day_close=24500.0)

    def _tick(self, hh, mm, price):
        self.t.on_spot_tick(datetime(2026, 4, 21, hh, mm, 30), price)

    def test_empty_snapshot_is_zeroed(self):
        # No ticks yet (besides start_day) → values should be defaults
        snap = self.t.snapshot()
        assert snap["day_open"] == 0
        assert snap["or_high"] == 0

    def test_first_tick_sets_day_open(self):
        self._tick(9, 16, 24600)
        snap = self.t.snapshot()
        assert snap["day_open"] == 24600
        assert snap["day_high"] == 24600
        assert snap["day_low"] == 24600

    def test_or_window_captures_915_to_929(self):
        for m in (16, 20, 25, 28):
            self._tick(9, m, 24500 + m)
        snap = self.t.snapshot()
        assert snap["or_high"] == 24528
        assert snap["or_low"] == 24516
        # 9:30 tick should NOT be in OR
        self._tick(9, 30, 25000)
        snap = self.t.snapshot()
        assert snap["or_high"] == 24528, "9:30 tick leaked into OR"

    def test_day_high_low_extends(self):
        self._tick(9, 16, 24600)
        self._tick(10, 30, 24700)
        self._tick(11, 30, 24550)
        snap = self.t.snapshot()
        assert snap["day_high"] == 24700
        assert snap["day_low"] == 24550

    def test_5m_bars_finalize(self):
        # Bar 1: 09:15–09:19
        self._tick(9, 16, 24600)
        self._tick(9, 17, 24650)
        self._tick(9, 18, 24590)
        # Bar 2 begins at 09:20 → finalizes bar 1
        self._tick(9, 20, 24620)
        assert len(self.t.bars_5m) == 1
        first = self.t.bars_5m[0]
        assert first.open == 24600
        assert first.high == 24650
        assert first.low == 24590

    def test_ema_updates_after_bar_finalize(self):
        # Push prices increasing across 12 bars to seed EMA — use absolute
        # hour:minute slots so we don't overflow minute=60+
        slots = [(9, 16), (9, 21), (9, 26), (9, 31), (9, 36), (9, 41),
                 (9, 46), (9, 51), (9, 56), (10, 1), (10, 6), (10, 11)]
        for i, (hh, mm) in enumerate(slots):
            self._tick(hh, mm, 24500 + i * 10)
        # Poke a 13th bar to finalize bar #12
        self._tick(11, 0, 24700)
        snap = self.t.snapshot()
        assert snap["ema9_5m"] > 0
        assert snap["ema21_5m"] > 0
        assert snap["atr_5m"] > 0


# ---------------------------------------------------------------------------
# Stubs for TacticDispatcher tests
# ---------------------------------------------------------------------------

class StubFetcher:
    """Mimics DataFetcher's read API just enough for the dispatcher."""

    def __init__(self, **overrides):
        self._values = {
            "spot": 24800.0,
            "support": 24700,
            "resistance": 24900,
            "expiry_date": "2026-04-23",
            "focus_pcr": 1.05,
            "oi_pattern": {"ce_oi_change": 0, "pe_oi_change": 600_000},
            "spot_history": [{"time": datetime(2026, 4, 21, 11, 0), "spot": 24800}],
            "india_vix": 15.0,
        }
        self._values.update(overrides)

    def get_spot(self): return self._values["spot"]
    def get_support(self): return self._values["support"]
    def get_resistance(self): return self._values["resistance"]
    def get_expiry_date(self): return self._values["expiry_date"]
    def get_focus_pcr(self): return self._values["focus_pcr"]
    def get_oi_pattern(self): return self._values["oi_pattern"]
    def get_spot_history(self): return self._values["spot_history"]
    def get_india_vix(self): return self._values["india_vix"]


class StubEngine:
    """Returns whatever you set as `next_signal`."""

    def __init__(self, signal=None):
        self.next_signal = signal or {
            "direction": None, "reasons": ["legacy stub: no signal"],
            "dte_risk": "MODERATE", "dte_days": 2,
            "is_expiry_day": False, "score": 0,
        }
        self.calls = 0

    def evaluate(self, **kwargs):
        self.calls += 1
        return dict(self.next_signal)


# ---------------------------------------------------------------------------
# TacticDispatcher tests
# ---------------------------------------------------------------------------

class TestDispatcherLegacyMode:
    def test_legacy_mode_just_calls_engine(self):
        d = TacticDispatcher(mode="legacy")
        eng = StubEngine(signal={
            "direction": "CE", "reasons": ["legacy fired"],
            "dte_risk": "MODERATE", "dte_days": 3,
            "is_expiry_day": False, "score": 5,
        })
        sig = d.evaluate(
            ts=datetime(2026, 4, 21, 11, 0),
            fetcher=StubFetcher(),
            engine=eng,
            in_position=False,
        )
        assert sig["direction"] == "CE"
        assert eng.calls == 1
        assert sig["tactic_name"] == "oi_wall_mean_reversion"


class TestDispatcherRegimeMode:
    def setup_method(self):
        self.disp = TacticDispatcher(mode="regime")
        self.disp.reset_for_new_day(date(2026, 4, 21), prev_day_close=24700.0)
        # Seed enough ticks to leave the WAIT/morning gate
        for hh, mm in [(9, 16), (9, 22), (9, 28), (10, 0), (10, 30), (11, 0)]:
            self.disp.on_spot_tick(datetime(2026, 4, 21, hh, mm), 24800)

    def test_no_position_default_routes_to_range_engine(self):
        # With no extension, no gap, default state → RANGE is the fallback
        eng = StubEngine(signal={
            "direction": None, "reasons": ["legacy stub"],
            "dte_risk": "MODERATE", "dte_days": 2,
            "is_expiry_day": False, "score": 0,
        })
        sig = self.disp.evaluate(
            ts=datetime(2026, 4, 21, 11, 0),
            fetcher=StubFetcher(),
            engine=eng,
            in_position=False,
        )
        # RANGE classifier output should have routed to the legacy engine
        assert eng.calls == 1
        assert "RANGE" in sig["reasons"][0] or sig["direction"] is None

    def test_returns_legacy_shape(self):
        sig = self.disp.evaluate(
            ts=datetime(2026, 4, 21, 11, 0),
            fetcher=StubFetcher(), engine=StubEngine(),
            in_position=False,
        )
        # All consumers expect these keys
        for k in ("direction", "reasons", "dte_risk", "dte_days",
                  "is_expiry_day", "score"):
            assert k in sig

    def test_force_exit_when_regime_flips_against_open_position(self):
        # Simulate: classifier locked into TREND_DOWN, position is CE
        # by manually fast-forwarding the classifier
        self.disp.classifier._current = Regime.TREND_DOWN
        sig = self.disp.evaluate(
            ts=datetime(2026, 4, 21, 11, 30),
            fetcher=StubFetcher(),
            engine=StubEngine(),
            in_position=True,
            position_direction="CE",
        )
        assert sig.get("force_exit") is True


class TestDispatcherDTECalc:
    def test_dte_basic(self):
        d = TacticDispatcher(mode="regime")
        ts = datetime(2026, 4, 21)
        assert d._compute_dte("2026-04-23", ts) == 2
        assert d._compute_dte("2026-04-21", ts) == 0
        assert d._compute_dte(None, ts) == 99
        assert d._compute_dte("garbage", ts) == 99


# ---------------------------------------------------------------------------
# Near-miss collection tests
# ---------------------------------------------------------------------------

from tactics.base import (  # noqa: E402
    Tactic, TacticConfig, TacticSignal, TacticState, GateResult,
)


class _StubTactic(Tactic):
    """Tactic with programmable gate results — used to drive near-miss
    collection deterministically."""

    def __init__(self, ce_gates: dict, pe_gates: dict, name: str = "stub"):
        cfg = TacticConfig(name=name)
        super().__init__(cfg)
        self.ce_gates = ce_gates
        self.pe_gates = pe_gates
        self.config.sl_pct = 0.30
        self.config.tp_pct = 0.50
        self.config.time_stop_min = 90
        self.config.strike_offset = 1

    def evaluate(self, state):
        return None  # never fires; we only care about gates

    def gates_for_direction(self, state, direction):
        src = self.ce_gates if direction == "CE" else self.pe_gates
        return {
            name: GateResult(passed=val, value=val, threshold=True,
                              description=f"{name}={val}")
            for name, val in src.items()
        }


class TestNearMissCollection:
    def _state(self):
        return TacticState(
            ts=datetime(2026, 4, 21, 11, 0),
            spot=24800.0, futures=24800.0,
            vwap=24800.0, ema9_5m=24800.0,
            regime="TREND_UP", dte=3,
            vix_level=15.0, focus_pcr=1.0,
        )

    def test_one_gate_fail_yields_one_near_miss(self):
        d = TacticDispatcher(mode="regime")
        d.tactics = {}  # clear default tactics
        # Inject our stub via the existing IEF slot for simplicity
        ce = {"a": True, "b": True, "c": False}
        pe = {"a": True, "b": True, "c": True}
        d.ief_tactic = _StubTactic(ce, pe, name="stub")
        nms = d._collect_near_misses_for_state(self._state(), spot=24800.0)
        assert len(nms) == 1
        assert nms[0]["direction"] == "CE"
        assert nms[0]["blocked_by"] == "c"
        assert nms[0]["sl_pct"] == 0.30
        assert nms[0]["tp_pct"] == 0.50
        assert nms[0]["time_stop_min"] == 90

    def test_all_gates_pass_yields_no_near_miss(self):
        d = TacticDispatcher(mode="regime")
        d.tactics = {}
        d.ief_tactic = _StubTactic({"a": True, "b": True}, {"a": True, "b": True})
        nms = d._collect_near_misses_for_state(self._state(), spot=24800.0)
        # Both directions all-pass -> no near-miss (would have fired)
        assert nms == []

    def test_two_gates_fail_yields_no_near_miss(self):
        d = TacticDispatcher(mode="regime")
        d.tactics = {}
        d.ief_tactic = _StubTactic(
            {"a": False, "b": False, "c": True},
            {"a": True,  "b": True,  "c": True},
        )
        nms = d._collect_near_misses_for_state(self._state(), spot=24800.0)
        assert nms == []

    def test_collect_near_misses_only_uses_fetcher(self):
        """Public entry-point: works without engine, no routing side-effects."""
        d = TacticDispatcher(mode="regime")
        d.reset_for_new_day(date(2026, 4, 21), prev_day_close=24700.0)
        for hh, mm in [(9, 16), (10, 0), (11, 0)]:
            d.on_spot_tick(datetime(2026, 4, 21, hh, mm), 24800)
        d.tactics = {}
        d.ief_tactic = _StubTactic({"a": True, "b": False}, {"a": True, "b": True})
        nms = d.collect_near_misses_only(
            datetime(2026, 4, 21, 11, 0),
            StubFetcher(),
            in_position=True,
            position_direction="CE",
        )
        # CE fails on "b" (1 gate) -> near-miss
        assert any(nm["blocked_by"] == "b" for nm in nms)


class TestDispatcherNearMissesFieldOnSignal:
    def test_evaluate_attaches_near_misses_key_on_no_trade(self):
        d = TacticDispatcher(mode="regime")
        d.reset_for_new_day(date(2026, 4, 21), prev_day_close=24700.0)
        for hh, mm in [(9, 16), (10, 0), (11, 0)]:
            d.on_spot_tick(datetime(2026, 4, 21, hh, mm), 24800)
        sig = d.evaluate(
            ts=datetime(2026, 4, 21, 11, 0),
            fetcher=StubFetcher(), engine=StubEngine(),
            in_position=False,
        )
        assert "near_misses" in sig
        assert isinstance(sig["near_misses"], list)
