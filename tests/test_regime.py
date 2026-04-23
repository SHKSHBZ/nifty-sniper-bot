"""
Unit tests for the regime-switching layer.

These tests run without pandas where possible (router + risk are pure Python)
and use synthetic ClassifierFeatures for the classifier — no real market data
needed, no network.

Run:
    pytest tests/test_regime.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, time, date, timedelta
from pathlib import Path

import pytest

# Put repo root on sys.path so `regime` imports resolve when running via pytest
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.classifier import (  # noqa: E402
    RegimeClassifier,
    ClassifierFeatures,
    ClassifierConfig,
    Regime,
)
from regime.router import StrategyRouter, Tactic  # noqa: E402
from regime.master_risk import (  # noqa: E402
    MasterRiskLayer,
    RiskConfig,
    DenyReason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(hour: int, minute: int = 0, d: date = date(2026, 4, 23)) -> datetime:
    return datetime.combine(d, time(hour, minute))


def _base_features(**overrides) -> ClassifierFeatures:
    defaults = dict(
        ts=_ts(11, 0),
        gap_pct=0.0,
        or_range_pct=0.002,
        avg_or_range_pct=0.0025,
        adx_15m=15.0,
        range_ratio=1.0,
        vwap_slope_30m=0.0,
        dist_from_vwap_pct=0.001,
        price=25_000.0,
        vwap=25_000.0,
        or_high=25_100.0,
        or_low=24_900.0,
        vix_level=15.0,
        vix_chg_15m=0.0,
        dte=3,
        event_flag=False,
        prev_day_close=25_000.0,
    )
    defaults.update(overrides)
    return ClassifierFeatures(**defaults)


# ---------------------------------------------------------------------------
# Classifier tests
# ---------------------------------------------------------------------------

class TestClassifierRawLogic:
    """Tests for the pure classification logic (no hysteresis)."""

    def setup_method(self):
        self.c = RegimeClassifier()

    def test_event_flag_forces_no_trade(self):
        f = _base_features(event_flag=True, adx_15m=30.0, range_ratio=1.5)
        assert self.c._raw_classify(f) == Regime.NO_TRADE

    def test_vix_spike_forces_no_trade(self):
        f = _base_features(vix_level=30.0)
        assert self.c._raw_classify(f) == Regime.NO_TRADE

    def test_vix_rapid_change_forces_no_trade(self):
        f = _base_features(vix_chg_15m=0.25)
        assert self.c._raw_classify(f) == Regime.NO_TRADE

    def test_zero_dte_is_expiry(self):
        f = _base_features(dte=0)
        assert self.c._raw_classify(f) == Regime.EXPIRY

    def test_gap_up_breakout_trend_up_gap(self):
        f = _base_features(
            ts=_ts(10, 0),
            gap_pct=0.008,
            or_range_pct=0.003,
            price=25_200,
            or_high=25_100,
            vwap_slope_30m=0.0008,
        )
        assert self.c._raw_classify(f) == Regime.TREND_UP_GAP

    def test_gap_down_breakdown_trend_down_gap(self):
        f = _base_features(
            ts=_ts(10, 0),
            gap_pct=-0.008,
            or_range_pct=0.003,
            price=24_700,
            or_low=24_800,
            vwap_slope_30m=-0.0008,
        )
        assert self.c._raw_classify(f) == Regime.TREND_DOWN_GAP

    def test_strong_adx_positive_slope_trend_up(self):
        f = _base_features(
            adx_15m=30.0,
            range_ratio=1.5,
            vwap_slope_30m=0.001,
            price=25_050,
            vwap=25_000,
        )
        assert self.c._raw_classify(f) == Regime.TREND_UP

    def test_strong_adx_negative_slope_trend_down(self):
        f = _base_features(
            adx_15m=30.0,
            range_ratio=1.5,
            vwap_slope_30m=-0.001,
            price=24_950,
            vwap=25_000,
        )
        assert self.c._raw_classify(f) == Regime.TREND_DOWN

    def test_low_adx_tight_range_is_chop(self):
        f = _base_features(
            adx_15m=12.0,
            range_ratio=0.5,
            dist_from_vwap_pct=0.0005,
        )
        assert self.c._raw_classify(f) == Regime.CHOP

    def test_default_fallback_is_range(self):
        f = _base_features(adx_15m=20.0, range_ratio=1.0)
        assert self.c._raw_classify(f) == Regime.RANGE

    def test_gap_before_10am_is_wait(self):
        f = _base_features(ts=_ts(9, 45), gap_pct=0.008)
        assert self.c._raw_classify(f) == Regime.WAIT


class TestClassifierHysteresis:
    """Sustain-window behavior — a candidate regime must hold for N min."""

    def test_no_flip_before_sustain(self):
        c = RegimeClassifier(ClassifierConfig(sustain_min=15))
        # Lock into RANGE at morning
        c.classify(_base_features(ts=_ts(10, 15)))
        assert c.current == Regime.RANGE

        # Trend candidate appears but only sustains 10 min — no flip
        c.classify(_base_features(
            ts=_ts(10, 30), adx_15m=30, range_ratio=1.5,
            vwap_slope_30m=0.001, price=25_050, vwap=25_000,
        ))
        c.classify(_base_features(
            ts=_ts(10, 40), adx_15m=30, range_ratio=1.5,
            vwap_slope_30m=0.001, price=25_050, vwap=25_000,
        ))
        assert c.current == Regime.RANGE

    def test_flip_after_sustain(self):
        c = RegimeClassifier(ClassifierConfig(sustain_min=15))
        c.classify(_base_features(ts=_ts(10, 15)))
        assert c.current == Regime.RANGE

        trend_kwargs = dict(
            adx_15m=30, range_ratio=1.5, vwap_slope_30m=0.001,
            price=25_050, vwap=25_000,
        )
        c.classify(_base_features(ts=_ts(10, 30), **trend_kwargs))
        c.classify(_base_features(ts=_ts(10, 40), **trend_kwargs))
        c.classify(_base_features(ts=_ts(10, 46), **trend_kwargs))  # 16m >= 15m
        assert c.current == Regime.TREND_UP

    def test_no_trade_overrides_immediately(self):
        c = RegimeClassifier(ClassifierConfig(sustain_min=15))
        c.classify(_base_features(ts=_ts(10, 15)))
        assert c.current == Regime.RANGE

        c.classify(_base_features(ts=_ts(10, 20), vix_level=30.0))
        assert c.current == Regime.NO_TRADE  # no sustain required


# ---------------------------------------------------------------------------
# Router tests
# ---------------------------------------------------------------------------

class TestStrategyRouter:

    def setup_method(self):
        self.r = StrategyRouter()

    def test_trend_up_gap_routes_to_bullish_launchpad(self):
        d = self.r.route(Regime.TREND_UP_GAP)
        assert d.tactic == Tactic.BULLISH_LAUNCHPAD
        assert d.direction == "CE"

    def test_trend_down_gap_routes_to_bearish_launchpad(self):
        d = self.r.route(Regime.TREND_DOWN_GAP)
        assert d.tactic == Tactic.BEARISH_LAUNCHPAD
        assert d.direction == "PE"

    def test_range_routes_to_existing_mean_reversion(self):
        d = self.r.route(Regime.RANGE)
        assert d.tactic == Tactic.OI_WALL_MEAN_REVERSION

    def test_expiry_routes_to_debit_spread(self):
        d = self.r.route(Regime.EXPIRY)
        assert d.tactic == Tactic.DEBIT_SPREAD

    def test_chop_routes_to_no_trade(self):
        d = self.r.route(Regime.CHOP)
        assert d.tactic == Tactic.NO_TRADE

    def test_ce_open_hostile_to_trend_down(self):
        d = self.r.route(Regime.TREND_DOWN, open_direction="CE")
        assert d.force_exit_open_positions is True

    def test_pe_open_compatible_with_trend_down(self):
        d = self.r.route(Regime.TREND_DOWN, open_direction="PE")
        assert d.force_exit_open_positions is False

    def test_no_trade_force_exits_everything(self):
        d_ce = self.r.route(Regime.NO_TRADE, open_direction="CE")
        d_pe = self.r.route(Regime.NO_TRADE, open_direction="PE")
        assert d_ce.force_exit_open_positions
        assert d_pe.force_exit_open_positions


# ---------------------------------------------------------------------------
# Master risk tests
# ---------------------------------------------------------------------------

class TestMasterRiskLayer:

    def setup_method(self):
        self.risk = MasterRiskLayer(RiskConfig(
            capital=100_000,
            risk_pct_per_trade=0.01,
            daily_loss_halt_pct=0.03,
            max_trades_per_day=4,
            max_consecutive_losses=2,
            max_concurrent_positions=1,
        ))
        self.risk.reset_for_new_day(date(2026, 4, 23))

    def test_before_day_reset_raises(self):
        r = MasterRiskLayer()
        with pytest.raises(RuntimeError):
            r.can_enter(_ts(11, 0))

    def test_allows_entry_in_window(self):
        d = self.risk.can_enter(_ts(11, 0))
        assert d.allow
        assert d.reason == DenyReason.OK

    def test_blocks_before_10am(self):
        d = self.risk.can_enter(_ts(9, 45))
        assert not d.allow
        assert d.reason == DenyReason.OUTSIDE_ENTRY_WINDOW

    def test_blocks_after_1430(self):
        d = self.risk.can_enter(_ts(14, 45))
        assert not d.allow
        assert d.reason == DenyReason.OUTSIDE_ENTRY_WINDOW

    def test_blocks_on_event_blackout(self):
        d = self.risk.can_enter(_ts(11, 0), event_blackout=True)
        assert not d.allow
        assert d.reason == DenyReason.EVENT_BLACKOUT

    def test_blocks_on_no_trade_regime(self):
        d = self.risk.can_enter(_ts(11, 0), tactic_is_no_trade=True)
        assert not d.allow

    def test_max_positions_open_blocks_entry(self):
        self.risk.on_position_open()
        d = self.risk.can_enter(_ts(11, 30))
        assert not d.allow
        assert d.reason == DenyReason.MAX_POSITIONS_OPEN

    def test_daily_loss_halt_triggers_at_threshold(self):
        # Lose 3.5% in one trade -> should halt
        self.risk.on_position_open()
        self.risk.record_trade_close(pnl=-3_500)
        d = self.risk.can_enter(_ts(12, 0))
        assert not d.allow
        assert d.reason == DenyReason.DAILY_LOSS_HALT

    def test_consecutive_losses_halts(self):
        for _ in range(2):  # max_consecutive_losses = 2
            self.risk.on_position_open()
            self.risk.record_trade_close(pnl=-100)
        d = self.risk.can_enter(_ts(12, 0))
        assert not d.allow
        assert d.reason == DenyReason.MAX_CONSEC_LOSSES

    def test_winning_trade_resets_consec_losses(self):
        self.risk.on_position_open()
        self.risk.record_trade_close(pnl=-100)
        self.risk.on_position_open()
        self.risk.record_trade_close(pnl=+200)
        assert self.risk.snapshot()["consecutive_losses"] == 0

    def test_position_sizing_basic(self):
        lots = self.risk.position_size_lots(
            sl_premium_points=20.0, lot_size=65
        )
        # risk_budget = 100_000 * 0.01 = 1_000
        # qty_units = 1_000 / 20 = 50
        # qty_lots = 50 // 65 = 0 (too tight)
        assert lots == 0

    def test_position_sizing_normal(self):
        lots = self.risk.position_size_lots(
            sl_premium_points=5.0, lot_size=65
        )
        # 1_000 / 5 = 200 units -> 200 // 65 = 3 lots
        assert lots == 3

    def test_expiry_day_multiplier_halves_size(self):
        lots = self.risk.position_size_lots(
            sl_premium_points=5.0, lot_size=65, expiry_day_multiplier=0.5
        )
        assert lots == 1  # int(3 * 0.5)

    def test_force_flat_trigger(self):
        assert not self.risk.force_flat_now(_ts(15, 0))
        assert self.risk.force_flat_now(_ts(15, 10))
        assert self.risk.force_flat_now(_ts(15, 25))


# ---------------------------------------------------------------------------
# End-to-end scenario: a gap-up day
# ---------------------------------------------------------------------------

class TestIntegrationGapUpDay:
    """
    Simulates the classifier + router + risk working together on a gap-up day.
    No real market data — just synthetic features stepping through the session.
    """

    def test_gap_up_day_flow(self):
        classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
        router = StrategyRouter()
        risk = MasterRiskLayer()
        risk.reset_for_new_day(date(2026, 4, 23))

        # 09:45 — gap-up is still WAIT (before 10:00, gap present)
        f = _base_features(ts=_ts(9, 45), gap_pct=0.008)
        regime = classifier.classify(f)
        assert regime == Regime.WAIT
        assert router.route(regime).tactic == Tactic.NO_TRADE
        assert not risk.can_enter(_ts(9, 45)).allow

        # 10:15 — gap-up confirmed + breakout above OR high + positive VWAP slope
        f = _base_features(
            ts=_ts(10, 15),
            gap_pct=0.008,
            or_range_pct=0.003,
            price=25_150,
            or_high=25_100,
            vwap_slope_30m=0.0008,
        )
        regime = classifier.classify(f)
        assert regime == Regime.TREND_UP_GAP

        decision = router.route(regime)
        assert decision.tactic == Tactic.BULLISH_LAUNCHPAD
        assert decision.direction == "CE"

        entry_gate = risk.can_enter(
            _ts(10, 15),
            tactic_is_no_trade=(decision.tactic == Tactic.NO_TRADE),
        )
        assert entry_gate.allow

        # Position opens
        risk.on_position_open()

        # 11:30 — trend reverses to down. Open CE should be force-exited.
        f = _base_features(
            ts=_ts(11, 30), adx_15m=30, range_ratio=1.4,
            vwap_slope_30m=-0.0008, price=24_900, vwap=25_000,
        )
        classifier.classify(f)  # candidate forms, not yet sustained
        for minute in (45, 55):
            f2 = _base_features(
                ts=_ts(11, minute), adx_15m=30, range_ratio=1.4,
                vwap_slope_30m=-0.0008, price=24_900, vwap=25_000,
            )
            classifier.classify(f2)
        # 11:55 is 25 min after 11:30 candidate start — flip should have happened
        # Feed one more bar to consolidate the flip
        classifier.classify(_base_features(
            ts=_ts(12, 0), adx_15m=30, range_ratio=1.4,
            vwap_slope_30m=-0.0008, price=24_900, vwap=25_000,
        ))
        assert classifier.current == Regime.TREND_DOWN

        # Router tells us to force-exit the open CE
        exit_decision = router.route(classifier.current, open_direction="CE")
        assert exit_decision.force_exit_open_positions is True
