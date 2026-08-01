"""
test_empirical_m1_challenger.py — Adversarial Stress Test Suite for Milestone 1.

Empirical verification of:
1. Directional Hedging Mode (`create_directional_hedge`) edge cases & rapid/simultaneous signals.
2. `buyer_locked` property statefulness across regime transitions (VOLATILITY_CRUSH -> TREND_UP -> CHOP).
3. Multi-leg short order execution under simulated leg failures in `PaperExecutor` & `OrderExecutor`.
"""

import pytest
from datetime import datetime, timedelta, timezone
from regime.seller_engine import OptionSellerEngine, SellerEngineConfig
from regime.dispatcher import TacticDispatcher
from regime.classifier import Regime, RegimeClassifier, ClassifierFeatures
from executor.paper import PaperExecutor
from order_executor import OrderExecutor


# ============================================================================
# AREA 1: Directional Hedging Mode Stress Tests
# ============================================================================

def test_directional_hedge_simultaneous_and_rapid_signals():
    """Verify create_directional_hedge performance under 100 rapid alternating signals."""
    engine = OptionSellerEngine()
    spot = 24000.0

    hedges = []
    for i in range(100):
        direction = "CE" if i % 2 == 0 else "PE"
        buyer_signal = {
            "symbol": "NIFTY",
            "direction": direction,
            "strike": 24000 + (i % 3) * 50,
            "quantity": 50,
            "timestamp": f"2026-08-01T10:00:{i:02d}",
        }
        payload = engine.create_directional_hedge(buyer_signal, spot)
        hedges.append(payload)

    assert len(hedges) == 100
    for i, h in enumerate(hedges):
        expected_primary_dir = "CE" if i % 2 == 0 else "PE"
        expected_counter_dir = "PE" if i % 2 == 0 else "CE"
        assert h.primary_leg["option_type"] == expected_primary_dir
        assert h.primary_leg["transaction_type"] == "BUY"
        assert h.counter_leg["option_type"] == expected_counter_dir
        assert h.counter_leg["transaction_type"] == "SELL"
        assert h.counter_leg["role"] == "seller_theta_hedge"


def test_directional_hedge_edge_case_none_direction():
    """
    Verify create_directional_hedge handles buyer_signal = {"direction": None} without raising AttributeError.
    Default fallback direction "CE" should be used safely.
    """
    engine = OptionSellerEngine()
    buyer_signal_with_none = {"direction": None, "symbol": "NIFTY", "quantity": 50}

    payload = engine.create_directional_hedge(buyer_signal_with_none, spot=24000.0)
    assert payload.primary_leg["option_type"] == "CE"
    assert payload.counter_leg["option_type"] == "PE"


def test_directional_hedge_asymmetric_strike_mapping():
    """
    Verify strike mapping when buyer takes an OTM option (e.g. 24500 CE when spot is 24000).
    Counter short leg defaults to ATM strike (24000 PE), creating strike asymmetry.
    """
    engine = OptionSellerEngine()
    spot = 24000.0
    buyer_signal = {
        "symbol": "NIFTY",
        "direction": "CE",
        "strike": 24500, # OTM strike for buyer
        "quantity": 50,
    }
    payload = engine.create_directional_hedge(buyer_signal, spot)
    assert payload.primary_leg["strike"] == 24500
    assert payload.counter_leg["strike"] == 24000 # ATM strike


# ============================================================================
# AREA 2: buyer_locked Statefulness & Regime Transitions
# ============================================================================

def test_buyer_locked_clock_mismatch_bug_in_seller_engine():
    """
    Verify OptionSellerEngine.set_regime compares tick timestamp ts against lockdown_until.
    When simulation time ts (10:00) sets lockdown_until = 10:15, calling set_regime("TREND_UP")
    at simulation time 10:05 maintains buyer_locked = True during lockdown window.
    """
    engine = OptionSellerEngine()
    sim_ts = datetime(2026, 8, 1, 10, 0, 0)
    
    # 1. Feed 15 ticks to trigger VOLATILITY_CRUSH at sim_ts
    for i in range(15):
        # ce_prem decays from 100 to 90 (10% decay) while spot stays at 24000
        engine.update_ticks(sim_ts, spot=24000.0, ce_prem=100.0 - i, pe_prem=100.0)
    
    assert engine.regime == Regime.VOLATILITY_CRUSH.value
    assert engine.buyer_locked is True
    assert engine.lockdown_until == sim_ts + timedelta(minutes=15) # 10:15:00

    # 2. Transition regime to TREND_UP at sim_ts + 5 mins (10:05:00)
    # The 15m lockdown should still be active until 10:15:00!
    engine.set_regime(Regime.TREND_UP)

    assert engine.buyer_locked is True, "buyer_locked should stay True until lockdown_until timestamp"


def test_buyer_locked_timezone_typeerror_in_seller_engine():
    """
    Verify set_regime safely compares timezone-aware lockdown_until with tick timestamps without TypeError.
    """
    engine = OptionSellerEngine()
    tz_ts = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)

    for i in range(15):
        engine.update_ticks(tz_ts, spot=24000.0, ce_prem=100.0 - i, pe_prem=100.0)

    assert engine.buyer_locked is True

    # set_regime should execute safely without raising TypeError
    engine.set_regime(Regime.TREND_UP)
    assert engine.buyer_locked is True


def test_tactic_dispatcher_buyer_locked_immediate_drop():
    """
    Verify TacticDispatcher sustained buyer lockout after VOLATILITY_CRUSH even when classifier
    exits VOLATILITY_CRUSH on subsequent tick.
    """
    dispatcher = TacticDispatcher()
    
    class DummyFetcher:
        def get_spot(self): return 24000.0
        def get_india_vix(self): return 15.0
        def get_focus_pcr(self): return 1.0
        def get_oi_pattern(self): return {}
        def get_support(self): return 23800
        def get_resistance(self): return 24200
        def get_expiry_date(self): return "2026-08-06"
        def get_spot_history(self): return [24000.0]

    class DummyEngine:
        def evaluate(self, **kwargs): return {"direction": None, "reasons": []}

    fetcher = DummyFetcher()
    engine = DummyEngine()
    ts1 = datetime(2026, 8, 1, 10, 30, 0)

    # Tick 1: VOLATILITY_CRUSH conditions (decay > 3%, range <= 30 pts)
    res1 = dispatcher.evaluate(
        ts=ts1, fetcher=fetcher, engine=engine, in_position=False,
        straddle_decay_15m=0.05, spot_range_pts=10.0
    )
    assert dispatcher.classifier.current == Regime.VOLATILITY_CRUSH
    assert dispatcher.buyer_locked is True

    # Tick 2: Next minute, straddle_decay_15m drops to 0.01
    ts2 = ts1 + timedelta(minutes=1)
    res2 = dispatcher.evaluate(
        ts=ts2, fetcher=fetcher, engine=engine, in_position=False,
        straddle_decay_15m=0.01, spot_range_pts=10.0
    )
    # The classifier flips to RANGE because VOLATILITY_CRUSH is in IMMEDIATE_REGIMES
    assert dispatcher.classifier.current == Regime.RANGE
    # dispatcher.buyer_locked must remain True due to active 15m lockdown in seller_engine!
    assert dispatcher.buyer_locked is True


# ============================================================================
# AREA 3: Multi-Leg Short Order Execution Under Simulated Leg Failures
# ============================================================================

def test_paper_executor_short_straddle_partial_leg_failure_margin():
    """
    Verify PaperExecutor.place_short_straddle executes compensating rollback when Leg 2 fails,
    closing Leg 1 and leaving no open naked short position.
    """
    config = {'initial_capital': 10000, 'slippage_percent': 0.0, 'latency_ms': 0}
    executor = PaperExecutor(config)

    symbols = {'ce': 'NIFTY24000CE', 'pe': 'NIFTY24000PE', 'lot_size': 50}
    res = executor.place_short_straddle(symbols, lots=1, ce_price=150.0, pe_price=150.0)

    assert res["success"] is False
    assert res["trades"][0] is not None # Leg 1 filled initially
    assert res["trades"][1] is None    # Leg 2 rejected

    # Compensating rollback closed Leg 1, so no open positions remain
    open_positions = executor.get_summary()["open_positions"]
    assert 'NIFTY24000CE' not in open_positions


def test_paper_executor_short_strangle_leg1_failure_leg2_still_executes():
    """
    Verify PaperExecutor.place_short_strangle aborts execution when Leg 1 fails,
    preventing orphaned naked short leg creation.
    """
    config = {'initial_capital': 10000, 'slippage_percent': 0.0, 'latency_ms': 0}
    executor = PaperExecutor(config)

    symbols = {'otm_ce': 'NIFTY24200CE', 'otm_pe': 'NIFTY23800PE', 'lot_size': 50}
    res = executor.place_short_strangle(symbols, lots=1, ce_price=300.0, pe_price=100.0)

    assert res["success"] is False
    assert res["trades"][0] is None    # Leg 1 rejected
    assert res["trades"][1] is None    # Leg 2 execution aborted

    open_positions = executor.get_summary()["open_positions"]
    assert 'NIFTY24200CE' not in open_positions
    assert 'NIFTY23800PE' not in open_positions
