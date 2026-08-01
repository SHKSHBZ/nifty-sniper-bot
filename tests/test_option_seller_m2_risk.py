"""
tests/test_option_seller_m2_risk.py — Unit and Integration Tests for Milestone 2
(Strict Risk Management & Premium Floor Engine for Option Seller Bot).

Tests:
1. Individual leg premium spike SL triggers (CE and PE).
2. Combined straddle/strangle premium spike SL triggers.
3. Pillar 4 structural resistance ceiling breach exit.
4. Spot range breakout invalidation (simulating trend breakout / volatility spike).
5. True Premium Floor tracking & dynamic trailing stop-loss for theta decay profit locking.
6. 15-Minute Straddle Decay Chop Filter evaluation.
7. OptionSellerEngine integration with RiskManager.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from risk.seller_risk import (
    OptionSellerRiskManager,
    TruePremiumFloorTracker,
    SellerPositionState,
    RiskEvaluationResult,
)
from regime.seller_engine import OptionSellerEngine, SellerEngineConfig
from premium_analyzer import PremiumAnalyzer


class MockPremiumAnalyzer:
    """Mock PremiumAnalyzer providing deterministic historical levels."""

    def __init__(self, ce_ceiling: float = 150.0, ce_floor: float = 20.0, pe_ceiling: float = 140.0, pe_floor: float = 15.0):
        self.ce_ceiling = ce_ceiling
        self.ce_floor = ce_floor
        self.pe_ceiling = pe_ceiling
        self.pe_floor = pe_floor

    def get_premium_historical_levels(self, strike: int, opt_type: str) -> dict:
        if opt_type.upper() == "CE":
            return {"support": self.ce_floor, "resistance": self.ce_ceiling}
        else:
            return {"support": self.pe_floor, "resistance": self.pe_ceiling}


# -----------------------------------------------------------------------------
# 1. Individual & Combined Leg Premium Spike SL Triggers
# -----------------------------------------------------------------------------

def test_individual_ce_leg_premium_spike_sl():
    """Verify short CE leg exits when LTP expands >= 30% above entry premium."""
    rm = OptionSellerRiskManager(default_leg_sl_pct=0.30)
    rm.register_position(
        position_id="pos_straddle_1",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # Safe LTPs
    res_safe = rm.evaluate_position("pos_straddle_1", live_spot=24000.0, ce_ltp=110.0, pe_ltp=110.0)
    assert not res_safe.should_exit

    # CE spikes by +35% (135 >= 100 * 1.30 = 130)
    res_spike = rm.evaluate_position("pos_straddle_1", live_spot=24000.0, ce_ltp=135.0, pe_ltp=90.0)
    assert res_spike.should_exit
    assert res_spike.exit_reason == "LEG_PREMIUM_SPIKE_CE"
    assert res_spike.exit_legs == ["CE"]


def test_individual_pe_leg_premium_spike_sl():
    """Verify short PE leg exits when LTP expands >= 30% above entry premium."""
    rm = OptionSellerRiskManager(default_leg_sl_pct=0.30)
    rm.register_position(
        position_id="pos_straddle_2",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # PE spikes by +40% (140 >= 100 * 1.30 = 130)
    res_spike = rm.evaluate_position("pos_straddle_2", live_spot=24000.0, ce_ltp=90.0, pe_ltp=140.0)
    assert res_spike.should_exit
    assert res_spike.exit_reason == "LEG_PREMIUM_SPIKE_PE"
    assert res_spike.exit_legs == ["PE"]


def test_combined_premium_spike_sl():
    """Verify combined short straddle exits when CE+PE >= EntryCredit * (1 + CombinedSL_pct)."""
    rm = OptionSellerRiskManager(default_combined_sl_pct=0.30, default_leg_sl_pct=0.50)
    rm.register_position(
        position_id="pos_straddle_comb",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # Combined entry credit = 200. Max allowed = 200 * 1.30 = 260.
    # CE=125 (+25%), PE=140 (+40%). Individual legs < 50%, but combined 265 >= 260.
    res = rm.evaluate_position("pos_straddle_comb", live_spot=24000.0, ce_ltp=125.0, pe_ltp=140.0)
    assert res.should_exit
    assert res.exit_reason == "COMBINED_PREMIUM_SPIKE"
    assert res.exit_legs == ["CE", "PE"]


def test_single_leg_spike_sl():
    """Verify single leg hedge position exits on leg premium spike."""
    rm = OptionSellerRiskManager(default_leg_sl_pct=0.30)
    rm.register_position(
        position_id="pos_hedge_pe",
        strategy_type="short_leg",
        spot=24000.0,
        single_leg_strike=24000,
        single_leg_type="PE",
        single_leg_entry_premium=80.0,
    )

    # Single PE spikes from 80 to 110 (+37.5% >= 30%)
    res = rm.evaluate_position("pos_hedge_pe", live_spot=24000.0, single_leg_ltp=110.0)
    assert res.should_exit
    assert res.exit_reason == "LEG_PREMIUM_SPIKE_PE"
    assert res.exit_legs == ["PE"]


# -----------------------------------------------------------------------------
# 2. Pillar 4 Structural Resistance Ceiling Breach Exit
# -----------------------------------------------------------------------------

def test_pillar4_ceiling_breach_exit():
    """Verify immediate exit when short option leg breaches historical ceiling resistance level."""
    mock_analyzer = MockPremiumAnalyzer(ce_ceiling=150.0, ce_floor=20.0)
    rm = OptionSellerRiskManager(premium_analyzer=mock_analyzer, default_leg_sl_pct=1.0) # High leg SL to test ceiling breach
    rm.register_position(
        position_id="pos_straddle_ceiling",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # CE premium expands to 155.0 (breaches ceiling of 150.0)
    res = rm.evaluate_position("pos_straddle_ceiling", live_spot=24000.0, ce_ltp=155.0, pe_ltp=80.0)
    assert res.should_exit
    assert res.exit_reason == "CEILING_BREACH_CE"
    assert res.exit_legs == ["CE"]


def test_ceiling_override_breach():
    """Verify ceiling breach using explicit ceiling override."""
    rm = OptionSellerRiskManager(default_leg_sl_pct=1.0)
    rm.register_position(
        position_id="pos_strangle_override",
        strategy_type="short_strangle",
        spot=24000.0,
        ce_strike=24100,
        ce_entry_premium=50.0,
        pe_strike=23900,
        pe_entry_premium=50.0,
        ce_ceiling_override=75.0,
    )

    res = rm.evaluate_position("pos_strangle_override", live_spot=24000.0, ce_ltp=76.0, pe_ltp=45.0)
    assert res.should_exit
    assert res.exit_reason == "CEILING_BREACH_CE"


# -----------------------------------------------------------------------------
# 3. Spot Range Breakout Invalidation
# -----------------------------------------------------------------------------

def test_spot_range_breakout_high():
    """Verify force-close of short position when live spot breaches range high (trend breakout / vol spike)."""
    rm = OptionSellerRiskManager(default_spot_range_pts=30.0)
    rm.register_position(
        position_id="pos_breakout_1",
        strategy_type="short_straddle",
        spot=24000.0,
        spot_range_high=24030.0,
        spot_range_low=23970.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # Spot at 24025 (within range) -> safe
    res_in = rm.evaluate_position("pos_breakout_1", live_spot=24025.0, ce_ltp=95.0, pe_ltp=95.0)
    assert not res_in.should_exit

    # Spot breaks out to 24035 (> 24030) -> force-close!
    res_breakout = rm.evaluate_position("pos_breakout_1", live_spot=24035.0, ce_ltp=110.0, pe_ltp=75.0)
    assert res_breakout.should_exit
    assert res_breakout.exit_reason == "SPOT_RANGE_BREAKOUT_HIGH"
    assert "CE" in res_breakout.exit_legs and "PE" in res_breakout.exit_legs


def test_spot_range_breakout_low():
    """Verify force-close of short position when live spot breaches range low."""
    rm = OptionSellerRiskManager(default_spot_range_pts=30.0)
    rm.register_position(
        position_id="pos_breakout_2",
        strategy_type="short_straddle",
        spot=24000.0,
        spot_range_high=24030.0,
        spot_range_low=23970.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # Spot drops to 23960 (< 23970) -> force-close!
    res_breakout = rm.evaluate_position("pos_breakout_2", live_spot=23960.0, ce_ltp=70.0, pe_ltp=115.0)
    assert res_breakout.should_exit
    assert res_breakout.exit_reason == "SPOT_RANGE_BREAKOUT_LOW"


# -----------------------------------------------------------------------------
# 4. True Premium Floor Tracking & Dynamic Trailing Stop
# -----------------------------------------------------------------------------

def test_true_premium_floor_tracker_lifecycle():
    """Verify TruePremiumFloorTracker tracks lowest_seen_premium and triggers trailing SL."""
    mock_analyzer = MockPremiumAnalyzer(ce_ceiling=150.0, ce_floor=25.0)
    tracker = TruePremiumFloorTracker(premium_analyzer=mock_analyzer, trail_sl_pct=0.15)
    
    init_res = tracker.initialize_position(strike=24000, opt_type="CE", entry_premium=100.0)
    assert init_res["floor_premium"] == 25.0
    assert init_res["ceiling_premium"] == 150.0
    assert tracker.lowest_seen_premium == 100.0

    # Step 1: Premium decays to 80.0
    update1 = tracker.update_ltp(80.0)
    assert update1["lowest_seen_premium"] == 80.0
    assert update1["trailing_sl_price"] == pytest.approx(80.0 * 1.15, rel=1e-3) # 92.0
    assert not update1["trailing_sl_triggered"]

    # Step 2: Premium decays further to 60.0
    update2 = tracker.update_ltp(60.0)
    assert update2["lowest_seen_premium"] == 60.0
    assert update2["trailing_sl_price"] == pytest.approx(60.0 * 1.15, rel=1e-3) # 69.0
    assert not update2["trailing_sl_triggered"]

    # Step 3: Premium bounces up to 65.0 (below trailing SL 69.0) -> no trigger
    update3 = tracker.update_ltp(65.0)
    assert update3["lowest_seen_premium"] == 60.0 # lowest seen remains 60
    assert not update3["trailing_sl_triggered"]

    # Step 4: Premium bounces up to 70.0 (>= 69.0 trailing SL) -> trailing SL triggers!
    update4 = tracker.update_ltp(70.0)
    assert update4["trailing_sl_triggered"]


def test_trailing_stop_exit_in_risk_manager():
    """Verify OptionSellerRiskManager triggers exit when dynamic trailing stop is hit."""
    rm = OptionSellerRiskManager(default_leg_sl_pct=0.50, default_trail_sl_pct=0.15)
    rm.register_position(
        position_id="pos_trail_test",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # 1. Decay tick: CE drops to 50.0 (lowest seen CE = 50.0, trailing SL CE = 57.5)
    rm.evaluate_position("pos_trail_test", live_spot=24000.0, ce_ltp=50.0, pe_ltp=90.0)
    pos = rm.get_position("pos_trail_test")
    assert pos.ce_floor_tracker.lowest_seen_premium == 50.0

    # 2. Bounce tick: CE expands to 58.0 (>= 57.5) -> triggers TRAILING_STOP_CE!
    res = rm.evaluate_position("pos_trail_test", live_spot=24000.0, ce_ltp=58.0, pe_ltp=90.0)
    assert res.should_exit
    assert res.exit_reason == "TRAILING_STOP_CE"
    assert res.exit_legs == ["CE"]


# -----------------------------------------------------------------------------
# 5. 15-Minute Straddle Decay Chop Filter
# -----------------------------------------------------------------------------

def test_15m_straddle_decay_chop_filter():
    """Verify 15-minute rolling straddle decay calculation vs spot range bounds."""
    rm = OptionSellerRiskManager()
    
    # 15 ticks of straddles decaying from 200 down to 180 (10% decay) while spot ranges 24000 to 24015 (15 pts range)
    spots = [24000.0 + (i % 5) * 3 for i in range(15)] # range [24000, 24012] -> 12 pts
    straddle_prems = [200.0 - i * 1.5 for i in range(15)] # 200 -> 179

    res = rm.evaluate_straddle_decay_chop(
        recent_straddle_prems=straddle_prems,
        recent_spots=spots,
        threshold_decay_pct=0.03,
        max_spot_range_pts=30.0,
    )

    assert res["is_volatility_crush"]
    assert res["decay_pct"] > 0.03
    assert res["spot_range"] <= 30.0


# -----------------------------------------------------------------------------
# 6. OptionSellerEngine Integration
# -----------------------------------------------------------------------------

def test_seller_engine_risk_manager_integration():
    """Verify OptionSellerEngine registers position and evaluates risk correctly."""
    mock_analyzer = MockPremiumAnalyzer()
    engine = OptionSellerEngine(premium_analyzer=mock_analyzer)

    engine.register_position(
        position_id="engine_pos_1",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    assert "engine_pos_1" in engine.risk_manager.active_positions

    # Evaluate normal market tick -> no exits
    exits_normal = engine.evaluate_active_positions(live_spot=24005.0, ce_ltp=95.0, pe_ltp=95.0)
    assert len(exits_normal) == 0

    # Evaluate breakout tick -> exit triggered
    exits_breakout = engine.evaluate_active_positions(live_spot=24040.0, ce_ltp=120.0, pe_ltp=60.0)
    assert len(exits_breakout) == 1
    assert exits_breakout[0].exit_reason == "SPOT_RANGE_BREAKOUT_HIGH"
