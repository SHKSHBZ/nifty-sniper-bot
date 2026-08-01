"""
test_empirical_challenger_m1.py — Dynamic empirical stress tests for Milestone 1.
Created by Challenger 1 to stress-test Volatility Crush, Strike Selection, and SELL Order Execution.
"""

import math
import os
import pytest
from datetime import datetime, timedelta

from regime.classifier import Regime, RegimeClassifier, ClassifierFeatures, ClassifierConfig
from regime.seller_engine import OptionSellerEngine, SellerEngineConfig
from tactics.seller_tactics import (
    get_atm_strike,
    get_straddle_strikes,
    get_strangle_strikes,
    ShortStraddleTactic,
    ShortStrangleTactic,
    ShortStraddleConfig,
    ShortStrangleConfig,
)
from order_executor import OrderExecutor, OrderResult


# ============================================================================
# 1. VOLATILITY CRUSH & NOISY TICKS STRESS TESTS
# ============================================================================

def test_volatility_crush_boundary_decay():
    """Verify exact threshold behavior for straddle_decay_15m = 0.03 vs 0.03001 vs 0.02999."""
    clf = RegimeClassifier()

    # Case A: Exactly 0.03 (3.0% decay) -> should NOT trigger (> 0.03 required)
    f_30 = ClassifierFeatures(ts=datetime.now(), straddle_decay_15m=0.03, spot_range_pts=20.0)
    assert clf.classify(f_30) != Regime.VOLATILITY_CRUSH

    # Case B: 0.03001 (3.001% decay) -> SHOULD trigger
    f_3001 = ClassifierFeatures(ts=datetime.now(), straddle_decay_15m=0.03001, spot_range_pts=20.0)
    assert clf.classify(f_3001) == Regime.VOLATILITY_CRUSH

    # Case C: 0.02999 (2.999% decay) -> should NOT trigger
    f_2999 = ClassifierFeatures(ts=datetime.now(), straddle_decay_15m=0.02999, spot_range_pts=20.0)
    clf_c = RegimeClassifier()
    assert clf_c.classify(f_2999) != Regime.VOLATILITY_CRUSH


def test_volatility_crush_spot_range_boundary():
    """Verify spot range conditions: spot_range_pts <= 30.0 vs spot_range_15m <= 0.003."""
    clf = RegimeClassifier()

    # Case A: Decay > 3%, spot_range_pts = 30.0 -> VOLATILITY_CRUSH
    f1 = ClassifierFeatures(ts=datetime.now(), straddle_decay_15m=0.04, spot_range_pts=30.0, spot_range_15m=0.004)
    assert clf.classify(f1) == Regime.VOLATILITY_CRUSH

    # Case B: Decay > 3%, spot_range_pts = 30.1, spot_range_15m = 0.0031 -> NOT VOLATILITY_CRUSH
    clf2 = RegimeClassifier()
    f2 = ClassifierFeatures(ts=datetime.now(), straddle_decay_15m=0.04, spot_range_pts=30.1, spot_range_15m=0.0031)
    assert clf2.classify(f2) != Regime.VOLATILITY_CRUSH

    # Case C: Decay > 3%, spot_range_pts = 50.0 (wide pts), BUT spot_range_15m = 0.0025 (<= 0.003 for high spot e.g. 25000)
    # spot_range_15m <= 0.003 OR spot_range_pts <= 30.0
    clf3 = RegimeClassifier()
    f3 = ClassifierFeatures(ts=datetime.now(), straddle_decay_15m=0.04, spot_range_pts=50.0, spot_range_15m=0.0025)
    assert clf3.classify(f3) == Regime.VOLATILITY_CRUSH


def test_seller_engine_noisy_ticks_decay_tracking():
    """Simulate 20 noisy ticks where straddle decays overall by 5% but fluctuates per tick."""
    engine = OptionSellerEngine()
    base_ts = datetime(2026, 3, 27, 10, 0, 0)
    base_spot = 24000.0

    # Ticks 0 to 14: straddle starts at CE 150 + PE 150 = 300
    # Over 15 ticks, premiums drop from 300 to 280 (6.6% decay) with high-frequency noise
    import random
    random.seed(42)

    regimes = []
    for i in range(20):
        ts = base_ts + timedelta(minutes=i)
        # Spot stays within 24000 +- 5 pts (range = 10 pts <= 30 pts)
        spot = base_spot + (i % 3) * 2.0 - 2.0
        # Premium decays from 150 down to ~135 with noise
        decay_factor = 1.0 - (i * 0.004) + (random.uniform(-0.002, 0.002))
        ce = 150.0 * decay_factor
        pe = 150.0 * decay_factor
        regime = engine.update_ticks(ts, spot, ce, pe)
        regimes.append(regime)

    # Should trigger VOLATILITY_CRUSH once 15 ticks accumulate and decay exceeds 3%
    assert Regime.VOLATILITY_CRUSH.value in regimes
    assert engine.buyer_locked is True
    assert engine.seller_activated is True


def test_seller_engine_set_regime_timestamp_bug_investigation():
    """Investigate set_regime() wall-clock datetime.now() vs simulation timestamp behavior."""
    engine = OptionSellerEngine()
    historical_ts = datetime(2026, 3, 27, 10, 0, 0)

    # Fill 15 ticks to trigger VOLATILITY_CRUSH
    for i in range(15):
        engine.update_ticks(
            historical_ts + timedelta(minutes=i),
            spot=24000.0,
            ce_prem=150.0 - i * 1.0, # decays from 300 to 270 (10% decay)
            pe_prem=150.0 - i * 1.0,
        )

    assert engine.regime == Regime.VOLATILITY_CRUSH.value
    assert engine.buyer_locked is True
    assert engine.lockdown_until == historical_ts + timedelta(minutes=29) # 10:14 + 15 min

    # Now call set_regime(Regime.RANGE)
    # With tick timestamp tracking, set_regime maintains buyer_locked = True during lockdown window
    engine.set_regime(Regime.RANGE)
    assert engine.buyer_locked is True


# ============================================================================
# 2. DYNAMIC STRIKE SELECTION EDGE CASE TESTS
# ============================================================================

def test_strike_selection_exact_boundaries():
    """Verify banker's rounding on exact half-step boundaries (e.g. 24425, 24475)."""
    # Step = 50
    # 24425 / 50 = 488.5 -> round half to even -> 488 * 50 = 24400
    assert get_atm_strike(24425.0, 50) == 24400

    # 24475 / 50 = 489.5 -> round half to even -> 490 * 50 = 24500
    assert get_atm_strike(24475.0, 50) == 24500

    # 24525 / 50 = 490.5 -> round half to even -> 490 * 50 = 24500
    assert get_atm_strike(24525.0, 50) == 24500

    # 24575 / 50 = 491.5 -> round half to even -> 492 * 50 = 24600
    assert get_atm_strike(24575.0, 50) == 24600


def test_strike_selection_decimal_spot_prices():
    """Test micro-variations around half-step boundary."""
    # 24424.99 -> rounds down to 24400
    assert get_atm_strike(24424.99, 50) == 24400

    # 24425.01 -> 488.5002 -> rounds up to 24450!
    assert get_atm_strike(24425.01, 50) == 24450

    # Random decimal spot prices
    assert get_atm_strike(24412.37, 50) == 24400
    assert get_atm_strike(24437.89, 50) == 24450
    assert get_atm_strike(24487.63, 50) == 24500


def test_strike_selection_large_spot_and_different_steps():
    """Test SENSEX / large index spot prices with step=100 and step=50."""
    # SENSEX spot = 81234.56, step = 100
    assert get_atm_strike(81234.56, 100) == 81200
    assert get_atm_strike(81250.00, 100) == 81200  # 812.5 -> 812
    assert get_atm_strike(81350.00, 100) == 81400  # 813.5 -> 814

    # Straddle strikes
    ce, pe = get_straddle_strikes(81234.56, 100)
    assert ce == 81200 and pe == 81200

    # Strangle strikes (+2 steps = +200 pts)
    ce_otm, pe_otm = get_strangle_strikes(81234.56, 100, otm_steps=2)
    assert ce_otm == 81400 and pe_otm == 81000


def test_strike_selection_invalid_or_zero_step():
    """Test zero or negative step fallbacks."""
    assert get_atm_strike(24430.0, 0) == 24450   # step <= 0 falls back to 50
    assert get_atm_strike(24430.0, -50) == 24450


# ============================================================================
# 3. ORDER EXECUTOR SELL ORDER LIMIT PRICE & BUFFER STRESS TESTS
# ============================================================================

class MockAuthManager:
    def get_headers(self):
        return {"Authorization": "Bearer mock_token"}


def test_sell_order_limit_buffer_and_rounding():
    """Verify limit_price, sl_price, target_price rounding and buffer application for SELL orders."""
    executor = OrderExecutor(MockAuthManager())

    # Case A: Standard LTP = 100.0, BUY vs SELL comparison
    # BUY: limit = 100 * 1.005 = 100.5, SL = 100 * 0.85 = 85.0, Target = 100 * 1.30 = 130.0
    res_buy = executor.place_option_order(
        symbol="NIFTY", strike=24400, option_type="CE", expiry="2026-03-27",
        quantity=50, transaction_type="BUY", dry_run=True, ltp_override=100.0
    )
    assert res_buy.limit_price == 100.5
    assert res_buy.sl_price == 85.0
    assert res_buy.target_price == 130.0

    # SELL: limit = 100 * 0.995 = 99.5, SL = 100 * 1.15 = 115.0, Target = 100 * 0.70 = 70.0
    res_sell = executor.place_option_order(
        symbol="NIFTY", strike=24400, option_type="CE", expiry="2026-03-27",
        quantity=50, transaction_type="SELL", dry_run=True, ltp_override=100.0
    )
    assert res_sell.limit_price == 99.5
    assert res_sell.sl_price == 115.0
    assert res_sell.target_price == 70.0
    assert res_sell.transaction_type == "SELL"


def test_sell_order_penny_option_edge_case():
    """Test very small LTP (penny options e.g. 0.05, 0.10) for SELL orders."""
    executor = OrderExecutor(MockAuthManager())

    # LTP = 0.10:
    # SELL limit = round(0.10 * 0.995, 1) = 0.1
    # SELL SL = round(0.10 * 1.15, 1) = 0.1
    # SELL target = round(0.10 * 0.70, 1) = 0.1
    res = executor.place_option_order(
        symbol="NIFTY", strike=24400, option_type="CE", expiry="2026-03-27",
        quantity=50, transaction_type="SELL", dry_run=True, ltp_override=0.10
    )
    assert res.limit_price == 0.1
    assert res.sl_price == 0.1
    assert res.target_price == 0.1

    # LTP = 0.05:
    # SELL limit = round(0.05 * 0.995, 1) = round(0.04975, 1) = 0.0!
    res_05 = executor.place_option_order(
        symbol="NIFTY", strike=24400, option_type="CE", expiry="2026-03-27",
        quantity=50, transaction_type="SELL", dry_run=True, ltp_override=0.05
    )
    # Note: limit_price becomes 0.0 for LTP <= 0.05 when rounding to 1 decimal place!
    assert res_05.limit_price == 0.0


def test_sell_order_strike_validation():
    """Verify strike validation for valid and invalid strike multiples."""
    executor = OrderExecutor(MockAuthManager())

    # NIFTY step is 50 -> 24425 is invalid strike!
    with pytest.raises(ValueError, match="Invalid NIFTY strike 24425"):
        executor.place_option_order(
            symbol="NIFTY", strike=24425, option_type="CE", expiry="2026-03-27",
            quantity=50, transaction_type="SELL", dry_run=True, ltp_override=100.0
        )

    # Valid strike 24400 passes
    res = executor.place_option_order(
        symbol="NIFTY", strike=24400, option_type="CE", expiry="2026-03-27",
        quantity=50, transaction_type="SELL", dry_run=True, ltp_override=100.0
    )
    assert res.success is True


def test_multi_leg_short_order_execution():
    """Verify place_multi_leg_short_order executes both CE and PE legs with SELL transaction_type."""
    executor = OrderExecutor(MockAuthManager())

    res = executor.place_multi_leg_short_order(
        symbol="NIFTY",
        ce_strike=24500,
        pe_strike=24300,
        expiry="2026-03-27",
        quantity=50,
        dry_run=True,
        ltp_override_ce=120.0,
        ltp_override_pe=110.0,
    )

    assert "ce" in res and "pe" in res
    assert res["ce"].transaction_type == "SELL"
    assert res["ce"].limit_price == round(120.0 * 0.995, 1) # 119.4
    assert res["pe"].transaction_type == "SELL"
    assert res["pe"].limit_price == round(110.0 * 0.995, 1) # 109.55 -> 109.5 or 109.4
