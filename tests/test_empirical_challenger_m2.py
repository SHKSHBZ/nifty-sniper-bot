"""
test_empirical_challenger_m2.py — Empirical Stress Tests for Milestone 2
(Strict Risk Management & Premium Floor Engine) by Challenger 2.

Focus Areas:
1. Fallback behavior when PremiumAnalyzer has no historical CSV data on disk & corrupted CSV handling.
2. Edge cases: zero spot range (spot_range_high == spot_range_low), ill-formed position states,
   zero entry premiums, NaN/Inf premiums, evaluation precedence, and multi-position cross-contamination.
3. TacticDispatcher.on_spot_tick premium ingestion and risk engine forwarding.
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from risk.seller_risk import (
    OptionSellerRiskManager,
    TruePremiumFloorTracker,
    SellerPositionState,
    RiskEvaluationResult,
)
from regime.seller_engine import OptionSellerEngine, SellerEngineConfig
from regime.dispatcher import TacticDispatcher
from premium_analyzer import PremiumAnalyzer
import pandas as pd


# ============================================================================
# Section 1: PremiumAnalyzer Missing / Corrupted Data Fallback Tests
# ============================================================================

def test_fallback_when_premium_analyzer_has_no_csv_data():
    """
    Test OptionSellerRiskManager & TruePremiumFloorTracker when PremiumAnalyzer points
    to an empty directory with no historical CSV logs on disk.
    """
    with tempfile.TemporaryDirectory() as empty_dir:
        analyzer = PremiumAnalyzer(logs_dir=empty_dir, index="nifty")
        
        # Verify get_premium_historical_levels returns Nones
        levels = analyzer.get_premium_historical_levels(24000, "CE")
        assert levels["support"] is None
        assert levels["resistance"] is None

        risk_mgr = OptionSellerRiskManager(premium_analyzer=analyzer)
        pos = risk_mgr.register_position(
            position_id="pos_no_csv",
            strategy_type="short_straddle",
            spot=24000.0,
            ce_strike=24000,
            ce_entry_premium=100.0,
            pe_strike=24000,
            pe_entry_premium=100.0,
        )

        # Floor trackers should have None for support/resistance floor/ceiling
        assert pos.ce_floor_tracker.ceiling_premium is None
        assert pos.ce_floor_tracker.floor_premium is None
        assert pos.pe_floor_tracker.ceiling_premium is None
        assert pos.pe_floor_tracker.floor_premium is None

        # 1. Normal tick within bounds should NOT trigger exit or exception
        res = risk_mgr.evaluate_position("pos_no_csv", live_spot=24000.0, ce_ltp=100.0, pe_ltp=100.0)
        assert not res.should_exit

        # 2. Percentage-based leg spike SL should still function (+30% by default -> LTP 131.0)
        res_spike = risk_mgr.evaluate_position("pos_no_csv", live_spot=24000.0, ce_ltp=131.0, pe_ltp=100.0)
        assert res_spike.should_exit
        assert res_spike.exit_reason == "LEG_PREMIUM_SPIKE_CE"

        # 3. Dynamic trailing SL on lowest_seen_premium should still function without historical CSV
        risk_mgr.close_position("pos_no_csv")
        pos2 = risk_mgr.register_position(
            position_id="pos_trail_no_csv",
            strategy_type="short_straddle",
            spot=24000.0,
            ce_strike=24000,
            ce_entry_premium=100.0,
            pe_strike=24000,
            pe_entry_premium=100.0,
        )
        # Decay CE premium to 50.0 (lowest seen becomes 50.0)
        _ = risk_mgr.evaluate_position("pos_trail_no_csv", live_spot=24000.0, ce_ltp=50.0, pe_ltp=90.0)
        assert pos2.ce_floor_tracker.lowest_seen_premium == 50.0

        # Trailing SL price = 50.0 * 1.15 = 57.5. Rebound to 58.0 should trigger TRAILING_STOP_CE
        res_trail = risk_mgr.evaluate_position("pos_trail_no_csv", live_spot=24000.0, ce_ltp=58.0, pe_ltp=90.0)
        assert res_trail.should_exit
        assert res_trail.exit_reason == "TRAILING_STOP_CE"


def test_fallback_with_explicit_ceiling_override_when_no_csv_data():
    """
    Test that passing explicit ceiling overrides (ce_ceiling_override / pe_ceiling_override)
    properly sets historical ceiling even when PremiumAnalyzer has no CSV data on disk.
    """
    with tempfile.TemporaryDirectory() as empty_dir:
        analyzer = PremiumAnalyzer(logs_dir=empty_dir, index="nifty")
        risk_mgr = OptionSellerRiskManager(premium_analyzer=analyzer)

        pos = risk_mgr.register_position(
            position_id="pos_override",
            strategy_type="short_straddle",
            spot=24000.0,
            ce_strike=24000,
            ce_entry_premium=100.0,
            pe_strike=24000,
            pe_entry_premium=100.0,
            ce_ceiling_override=120.0,
            pe_ceiling_override=125.0,
        )

        assert pos.ce_floor_tracker.ceiling_premium == 120.0
        assert pos.pe_floor_tracker.ceiling_premium == 125.0

        # Breaching ceiling override triggers CEILING_BREACH_CE
        res = risk_mgr.evaluate_position("pos_override", live_spot=24000.0, ce_ltp=121.0, pe_ltp=90.0)
        assert res.should_exit
        assert res.exit_reason == "CEILING_BREACH_CE"


def test_corrupted_csv_timestamp_dateparseerror_vulnerability():
    """
    FINDING 1: PremiumAnalyzer._load_week_data() vulnerability to corrupted CSV timestamps.
    `pd.to_datetime` without errors='coerce' causes DateParseError when invalid timestamp string is present.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        log_path = Path(temp_dir) / "focus_zone_nifty_expiry_20260801.csv"
        with open(log_path, "w") as f:
            f.write("timestamp,strike,ce_ltp,pe_ltp,spot\n")
            f.write("invalid_time_string,24000,100.0,100.0,24000.0\n")

        analyzer = PremiumAnalyzer(logs_dir=temp_dir, index="nifty")
        # Verify DateParseError is raised when loading corrupted timestamp CSV
        with pytest.raises(Exception) as exc_info:
            analyzer.get_premium_historical_levels(24000, "CE")
        assert "DateParseError" in str(exc_info.type) or "datetime" in str(exc_info.value).lower()


def test_premium_analyzer_exception_resilience_in_floor_tracker():
    """
    Test that if PremiumAnalyzer.get_premium_historical_levels raises an unexpected Exception,
    TruePremiumFloorTracker catches it, logs warning, and defaults support/resistance to None.
    """
    class BuggyAnalyzer:
        def get_premium_historical_levels(self, strike, opt_type):
            raise RuntimeError("Database connection crashed!")

    tracker = TruePremiumFloorTracker(premium_analyzer=BuggyAnalyzer())
    res = tracker.initialize_position(24000, "CE", 100.0)

    assert res["floor_premium"] is None
    assert res["ceiling_premium"] is None
    assert tracker.lowest_seen_premium == 100.0


# ============================================================================
# Section 2: Edge Cases (Zero Spot Range, Zero Premiums, Precedence, Cross-Contamination)
# ============================================================================

def test_zero_spot_range_edge_case():
    """
    Test OptionSellerRiskManager behavior when spot_range_high == spot_range_low.
    """
    risk_mgr = OptionSellerRiskManager()
    pos = risk_mgr.register_position(
        position_id="zero_range_pos",
        strategy_type="short_straddle",
        spot=24000.0,
        spot_range_high=24000.0,
        spot_range_low=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # Spot exactly at bound (24000.0) -> live_spot > 24000 is False, live_spot < 24000 is False
    res_exact = risk_mgr.evaluate_position("zero_range_pos", live_spot=24000.0, ce_ltp=100.0, pe_ltp=100.0)
    assert not res_exact.should_exit

    # Any deviation above 24000.0 (e.g. 24000.01) MUST trigger SPOT_RANGE_BREAKOUT_HIGH immediately
    res_high = risk_mgr.evaluate_position("zero_range_pos", live_spot=24000.01, ce_ltp=100.0, pe_ltp=100.0)
    assert res_high.should_exit
    assert res_high.exit_reason == "SPOT_RANGE_BREAKOUT_HIGH"

    # Any deviation below 24000.0 (e.g. 23999.99) MUST trigger SPOT_RANGE_BREAKOUT_LOW immediately
    res_low = risk_mgr.evaluate_position("zero_range_pos", live_spot=23999.99, ce_ltp=100.0, pe_ltp=100.0)
    assert res_low.should_exit
    assert res_low.exit_reason == "SPOT_RANGE_BREAKOUT_LOW"


def test_inverted_spot_range_bounds():
    """
    Test when spot_range_low > spot_range_high (inverted bounds).
    """
    risk_mgr = OptionSellerRiskManager()
    risk_mgr.register_position(
        position_id="inverted_range_pos",
        strategy_type="short_straddle",
        spot=24000.0,
        spot_range_high=23900.0,  # high bound lower than low bound
        spot_range_low=24100.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # 24000.0 > 23900.0 (spot_range_high) -> immediately triggers SPOT_RANGE_BREAKOUT_HIGH
    res = risk_mgr.evaluate_position("inverted_range_pos", live_spot=24000.0, ce_ltp=100.0, pe_ltp=100.0)
    assert res.should_exit
    assert res.exit_reason == "SPOT_RANGE_BREAKOUT_HIGH"


def test_zero_entry_premium_trailing_stop_false_positive_finding():
    """
    FINDING 2: False positive TRAILING_STOP trigger on zero entry premium legs.
    When entry_premium is 0.0, initialize_position sets self.entry_premium = 0.001.
    If LTP is evaluated at 0.0, lowest_seen_premium becomes 0.0 and trailing_sl_price becomes 0.0.
    Since lowest_seen (0.0) < entry (0.001) and ltp (0.0) >= trailing_sl_price (0.0),
    trailing_sl_triggered evaluates to True on tick 1 without any price rebound!
    """
    risk_mgr = OptionSellerRiskManager()
    pos = risk_mgr.register_position(
        position_id="zero_prem_pos",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=0.0,
        pe_strike=24000,
        pe_entry_premium=0.0,
    )

    # Evaluate at live_spot=24000.0, ce_ltp=0.0, pe_ltp=0.0
    res = risk_mgr.evaluate_position("zero_prem_pos", live_spot=24000.0, ce_ltp=0.0, pe_ltp=0.0)
    
    # Documenting empirical finding: should_exit fires TRAILING_STOP_CE because 0.0 >= 0.0
    assert res.should_exit is True
    assert res.exit_reason == "TRAILING_STOP_CE"


def test_nan_and_inf_ltp_values_and_precedence():
    """
    FINDING 3: Combined Premium SL has higher evaluation precedence than Leg Premium SL.
    When ce_ltp is float('inf'), combined_ltp is Inf, triggering COMBINED_PREMIUM_SPIKE
    before individual LEG_PREMIUM_SPIKE_CE can be checked.
    """
    risk_mgr = OptionSellerRiskManager()
    risk_mgr.register_position(
        position_id="nan_pos",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # NaN spot: live_spot > high is False, live_spot < low is False in Python NaN comparisons
    res_nan_spot = risk_mgr.evaluate_position("nan_pos", live_spot=float("nan"), ce_ltp=100.0, pe_ltp=100.0)
    assert not res_nan_spot.should_exit

    # Inf LTP for CE: COMBINED_PREMIUM_SPIKE evaluates before LEG_PREMIUM_SPIKE_CE
    res_inf_ce = risk_mgr.evaluate_position("nan_pos", live_spot=24000.0, ce_ltp=float("inf"), pe_ltp=100.0)
    assert res_inf_ce.should_exit
    assert res_inf_ce.exit_reason == "COMBINED_PREMIUM_SPIKE"


def test_ill_formed_position_states():
    """
    Test registration and evaluation of incomplete or ill-formed position states:
    - Missing CE/PE strikes
    - Unknown strategy_type
    - Evaluating non-existent or closed positions
    """
    risk_mgr = OptionSellerRiskManager()

    # 1. Missing strikes in short_straddle
    pos_no_strikes = risk_mgr.register_position(
        position_id="no_strikes",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_entry_premium=100.0,
        pe_entry_premium=100.0,
    )
    assert pos_no_strikes.ce_floor_tracker is None
    assert pos_no_strikes.pe_floor_tracker is None

    res1 = risk_mgr.evaluate_position("no_strikes", live_spot=24000.0, ce_ltp=100.0, pe_ltp=100.0)
    assert not res1.should_exit

    # 2. Unknown strategy type
    pos_unknown = risk_mgr.register_position(
        position_id="unknown_strat",
        strategy_type="exotic_option_spread",
        spot=24000.0,
        single_leg_entry_premium=50.0,
    )
    res_unknown = risk_mgr.evaluate_position("unknown_strat", live_spot=24000.0, single_leg_ltp=70.0)
    assert res_unknown.should_exit
    assert res_unknown.exit_reason == "LEG_PREMIUM_SPIKE_SINGLE"

    # 3. Non-existent position ID
    res_non_exist = risk_mgr.evaluate_position("does_not_exist", live_spot=24000.0)
    assert not res_non_exist.should_exit

    # 4. Closed position ID
    risk_mgr.close_position("no_strikes")
    res_closed = risk_mgr.evaluate_position("no_strikes", live_spot=24000.0)
    assert not res_closed.should_exit


def test_multi_strike_position_cross_contamination_in_evaluate_active_positions():
    """
    FINDING 4: OptionSellerEngine.evaluate_active_positions scalar LTP cross-contamination.
    When multiple positions on DIFFERENT strikes are active (e.g. 24000 ATM Straddle vs 24200 OTM Strangle),
    calling evaluate_active_positions(live_spot, ce_ltp=120.0, pe_ltp=10.0) applies ce_ltp=120.0 to ALL active positions!
    If Position B is an OTM leg with entry_premium=30.0, receiving ce_ltp=120.0 (the ATM LTP) causes Position B
    to falsely trigger LEG_PREMIUM_SPIKE_CE (+300% spike) even if the 24200 CE LTP is actually only 32.0!
    """
    engine = OptionSellerEngine()

    # Position A: ATM Straddle 24000 (CE entry 100.0, PE entry 100.0)
    pos_a = engine.register_position(
        position_id="atm_straddle_24000",
        strategy_type="short_straddle",
        spot=24000.0,
        ce_strike=24000,
        ce_entry_premium=100.0,
        pe_strike=24000,
        pe_entry_premium=100.0,
    )

    # Position B: OTM Strangle 24200/23800 (CE entry 30.0, PE entry 30.0)
    pos_b = engine.register_position(
        position_id="otm_strangle_24200",
        strategy_type="short_strangle",
        spot=24000.0,
        ce_strike=24200,
        ce_entry_premium=30.0,
        pe_strike=23800,
        pe_entry_premium=30.0,
    )

    # Call evaluate_active_positions with ATM leg LTPs (ce_ltp=120.0 for 24000 CE)
    results = engine.evaluate_active_positions(live_spot=24000.0, ce_ltp=120.0, pe_ltp=90.0)

    # Position A (entry 100.0, max allowed 130.0) is safe with ce_ltp=120.0
    # Position B (entry 30.0, max allowed 39.0) receives ce_ltp=120.0 and FALSELY EXITS via LEG_PREMIUM_SPIKE_CE!
    assert len(results) == 1
    assert results[0].details["position_id"] == "otm_strangle_24200"
    assert results[0].exit_reason == "LEG_PREMIUM_SPIKE_CE"


# ============================================================================
# Section 3: TacticDispatcher on_spot_tick Premium Ingestion & Forwarding
# ============================================================================

def test_tactic_dispatcher_on_spot_tick_premium_forwarding():
    """
    Verify TacticDispatcher.on_spot_tick receives ce_prem and pe_prem and forwards
    them to OptionSellerEngine.update_ticks correctly.
    """
    with tempfile.TemporaryDirectory() as empty_dir:
        analyzer = PremiumAnalyzer(logs_dir=empty_dir, index="nifty")
        dispatcher = TacticDispatcher(mode="regime", premium_analyzer=analyzer)

        assert hasattr(dispatcher, "seller_engine")
        assert dispatcher.seller_engine is not None
        assert dispatcher.seller_engine.risk_manager.premium_analyzer == analyzer

        base_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        spot = 24000.0

        # Feed 14 ticks with straddle premium CE=100.0, PE=100.0 (Total=200.0)
        for i in range(14):
            ts = base_time + timedelta(minutes=i)
            dispatcher.on_spot_tick(ts, spot, ce_prem=100.0, pe_prem=100.0)

        assert len(dispatcher.seller_engine._recent_ces) == 14
        assert dispatcher.seller_engine._recent_ces[-1] == 100.0
        assert dispatcher.seller_engine._recent_pes[-1] == 100.0
        assert not dispatcher.buyer_locked

        # Feed 15th tick with decayed premiums CE=90.0, PE=90.0 (Total=180.0 -> 10% decay)
        ts_15 = base_time + timedelta(minutes=14)
        dispatcher.on_spot_tick(ts_15, spot, ce_prem=90.0, pe_prem=90.0)

        # 10% decay > 3% threshold with spot range = 0 pts -> VOLATILITY_CRUSH triggered!
        assert dispatcher.seller_engine.regime == "VOLATILITY_CRUSH"
        assert dispatcher.seller_engine.buyer_locked is True
        assert dispatcher.buyer_locked is True


def test_tactic_dispatcher_update_and_get_regime_forwards_spot_ticks():
    """
    Verify TacticDispatcher.update_and_get_regime updates seller_engine ticks and sets regime.
    """
    class MockFetcher:
        def get_spot(self):
            return 24000.0
        def get_india_vix(self):
            return 14.5
        def get_expiry_date(self):
            return "2026-08-06"

    dispatcher = TacticDispatcher(mode="regime")
    fetcher = MockFetcher()
    ts = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)

    regime_str = dispatcher.update_and_get_regime(ts, fetcher)

    assert isinstance(regime_str, str)
    assert dispatcher.seller_engine.last_ts == ts
    assert len(dispatcher.seller_engine._recent_spots) == 1
    assert dispatcher.seller_engine._recent_spots[0] == 24000.0
