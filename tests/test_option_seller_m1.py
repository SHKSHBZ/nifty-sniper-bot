"""
test_option_seller_m1.py — Comprehensive Unit Tests for Milestone 1:
Multi-Regime Execution, Volatility Crush Mode, Directional Hedging Mode,
Short Premium Strategies, and SELL Order Execution.

Run:
    $env:PYTHONPATH="."; .venv\\Scripts\\python.exe -m pytest tests/test_option_seller_m1.py -v
"""

from __future__ import annotations

from datetime import datetime, time, date, timedelta
import pytest

from regime.classifier import RegimeClassifier, ClassifierFeatures, Regime
from regime.router import StrategyRouter, Tactic
from regime.dispatcher import TacticDispatcher
from regime.seller_engine import OptionSellerEngine, SellerEngineConfig
from tactics.base import TacticState
from tactics.seller_tactics import (
    ShortStraddleTactic,
    ShortStraddleConfig,
    ShortStrangleTactic,
    ShortStrangleConfig,
    get_atm_strike,
    get_straddle_strikes,
    get_strangle_strikes,
)
from order_executor import OrderExecutor, OrderResult, LIMIT_BUFFER_PCT, SL_PCT, TARGET_PCT
from executor.paper import PaperExecutor


# ---------------------------------------------------------------------------
# Test Fixtures / Dummy Objects
# ---------------------------------------------------------------------------

class DummyAuthManager:
    def get_headers(self):
        return {"Authorization": "Bearer dummy_token"}


class DummyFetcher:
    def __init__(self, spot: float = 24425.0):
        self._spot = spot

    def get_spot(self):
        return self._spot

    def get_support(self):
        return 24400

    def get_resistance(self):
        return 24500

    def get_focus_pcr(self):
        return 1.0

    def get_oi_pattern(self):
        return {"ce_oi_change": 0, "pe_oi_change": 0}

    def get_spot_history(self):
        return [self._spot]

    def get_india_vix(self):
        return 15.0

    def get_expiry_date(self):
        return "2026-04-30"


# ---------------------------------------------------------------------------
# 1. Volatility Crush Mode & Regime Classification Tests
# ---------------------------------------------------------------------------

def test_volatility_crush_regime_classification():
    classifier = RegimeClassifier()
    # Features simulating straddle decay > 3% with range-bound spot
    f = ClassifierFeatures(
        ts=datetime(2026, 4, 23, 11, 0),
        price=24400.0,
        straddle_decay_15m=0.04,   # 4% decay (> 3%)
        spot_range_pts=20.0,        # 20 pts range (<= 30)
    )
    regime = classifier.classify(f)
    assert regime == Regime.VOLATILITY_CRUSH


def test_volatility_crush_router_and_dispatcher():
    router = StrategyRouter()
    decision = router.route(Regime.VOLATILITY_CRUSH)
    assert decision.tactic == Tactic.SHORT_STRADDLE
    assert decision.reason != ""

    dispatcher = TacticDispatcher()
    # Feed features triggering VOLATILITY_CRUSH
    f = ClassifierFeatures(
        ts=datetime(2026, 4, 23, 11, 0),
        price=24400.0,
        straddle_decay_15m=0.035,
        spot_range_pts=15.0,
    )
    regime = dispatcher.classifier.classify(f)
    assert regime == Regime.VOLATILITY_CRUSH
    assert dispatcher.buyer_locked is True


def test_option_seller_engine_decay_tracking():
    engine = OptionSellerEngine(SellerEngineConfig(straddle_decay_threshold=0.03, spot_range_max_pts=30.0))
    ts_start = datetime(2026, 4, 23, 11, 0)

    # Feed 15 ticks with decaying straddle premium (from 200 down to 180 = 10% decay) and range-bound spot (24400 - 24410)
    for i in range(15):
        t = ts_start + timedelta(minutes=i)
        spot = 24400.0 + (i % 5)
        ce_prem = 100.0 - (i * 0.7)
        pe_prem = 100.0 - (i * 0.7)
        regime = engine.update_ticks(t, spot, ce_prem, pe_prem)

    assert regime == Regime.VOLATILITY_CRUSH.value
    assert engine.buyer_locked is True
    assert engine.seller_activated is True
    assert engine.last_decay_pct > 0.03


# ---------------------------------------------------------------------------
# 2. Directional Hedging Mode Tests
# ---------------------------------------------------------------------------

def test_directional_hedging_mode_long_ce():
    engine = OptionSellerEngine()
    buyer_signal = {
        "symbol": "NIFTY",
        "direction": "CE",
        "strike": 24400,
        "quantity": 50,
        "timestamp": "2026-04-23T11:00:00",
    }
    hedged_payload = engine.create_directional_hedge(buyer_signal, spot=24430.0)

    assert hedged_payload.is_hedged is True
    assert hedged_payload.primary_leg["option_type"] == "CE"
    assert hedged_payload.primary_leg["transaction_type"] == "BUY"
    assert hedged_payload.counter_leg["option_type"] == "PE"
    assert hedged_payload.counter_leg["transaction_type"] == "SELL"
    assert hedged_payload.counter_leg["strike"] == 24450  # rounded ATM to step=50


def test_directional_hedging_mode_long_pe():
    engine = OptionSellerEngine()
    buyer_signal = {
        "symbol": "NIFTY",
        "direction": "PE",
        "strike": 24400,
        "quantity": 50,
    }
    hedged_payload = engine.create_directional_hedge(buyer_signal, spot=24410.0)

    assert hedged_payload.is_hedged is True
    assert hedged_payload.primary_leg["option_type"] == "PE"
    assert hedged_payload.counter_leg["option_type"] == "CE"
    assert hedged_payload.counter_leg["transaction_type"] == "SELL"
    assert hedged_payload.counter_leg["strike"] == 24400


# ---------------------------------------------------------------------------
# 3. Short Premium Strategy Core Tests (Straddle / Strangle Tactics)
# ---------------------------------------------------------------------------

def test_dynamic_strike_selection_helpers():
    assert get_atm_strike(24424.0, step=50) == 24400
    assert get_atm_strike(24426.0, step=50) == 24450
    assert get_straddle_strikes(24430.0, step=50) == (24450, 24450)
    assert get_strangle_strikes(24430.0, step=50, otm_steps=2) == (24550, 24350)


def test_short_straddle_tactic_execution():
    tactic = ShortStraddleTactic(ShortStraddleConfig(no_entry_before=time(9, 15)))
    state = TacticState(
        ts=datetime(2026, 4, 23, 11, 0),
        spot=24430.0,
        is_in_position=False,
    )
    sig = tactic.evaluate(state)
    assert sig is not None
    assert sig.action == "enter"
    assert sig.direction == "CE"
    assert sig.second_direction == "PE"
    assert sig.strike_offset == 0
    assert sig.second_strike_offset == 0
    assert sig.is_straddle is True
    assert sig.combined_sl_pct == 0.30


def test_short_strangle_tactic_execution():
    tactic = ShortStrangleTactic(ShortStrangleConfig(no_entry_before=time(9, 15), otm_steps=2))
    state = TacticState(
        ts=datetime(2026, 4, 23, 11, 0),
        spot=24430.0,
        is_in_position=False,
    )
    sig = tactic.evaluate(state)
    assert sig is not None
    assert sig.action == "enter"
    assert sig.direction == "CE"
    assert sig.second_direction == "PE"
    assert sig.strike_offset == 2
    assert sig.second_strike_offset == -2
    assert sig.is_straddle is True


def test_option_seller_engine_payload_generators():
    engine = OptionSellerEngine()
    straddle = engine.generate_short_straddle(spot=24430.0, quantity=50)
    assert straddle["strategy"] == "short_straddle"
    assert straddle["atm_strike"] == 24450
    assert straddle["ce_leg"]["transaction_type"] == "SELL"
    assert straddle["pe_leg"]["transaction_type"] == "SELL"

    strangle = engine.generate_short_strangle(spot=24430.0, otm_steps=2, quantity=50)
    assert strangle["strategy"] == "short_strangle"
    assert strangle["ce_strike"] == 24550
    assert strangle["pe_strike"] == 24350
    assert strangle["ce_leg"]["transaction_type"] == "SELL"
    assert strangle["pe_leg"]["transaction_type"] == "SELL"


# ---------------------------------------------------------------------------
# 4. Order Executor & Paper Executor SELL Order Tests
# ---------------------------------------------------------------------------

def test_order_executor_sell_limit_discount():
    executor = OrderExecutor(DummyAuthManager())
    # Place a SELL dry-run order with LTP = 100.0
    res = executor.place_option_order(
        symbol="NIFTY",
        strike=24400,
        option_type="CE",
        expiry="2026-04-30",
        quantity=50,
        transaction_type="SELL",
        dry_run=True,
        ltp_override=100.0,
    )
    assert res.success is True
    assert res.transaction_type == "SELL"
    # SELL limit price must be discounted: 100.0 * (1 - 0.005) = 99.5
    assert res.limit_price == round(100.0 * (1 - LIMIT_BUFFER_PCT), 1)
    # SELL stop-loss must be higher: 100.0 * (1 + 0.15) = 115.0
    assert res.sl_price == round(100.0 * (1 + SL_PCT), 1)
    # SELL target must be lower: 100.0 * (1 - 0.30) = 70.0
    assert res.target_price == round(100.0 * (1 - TARGET_PCT), 1)


def test_order_executor_buy_limit_buffer():
    executor = OrderExecutor(DummyAuthManager())
    # Place a BUY dry-run order with LTP = 100.0
    res = executor.place_option_order(
        symbol="NIFTY",
        strike=24400,
        option_type="CE",
        expiry="2026-04-30",
        quantity=50,
        transaction_type="BUY",
        dry_run=True,
        ltp_override=100.0,
    )
    assert res.success is True
    assert res.transaction_type == "BUY"
    # BUY limit price must be buffered above LTP: 100.0 * (1 + 0.005) = 100.5
    assert res.limit_price == round(100.0 * (1 + LIMIT_BUFFER_PCT), 1)
    # BUY stop-loss must be lower: 100.0 * (1 - 0.15) = 85.0
    assert res.sl_price == round(100.0 * (1 - SL_PCT), 1)


def test_order_executor_place_multi_leg_short_order():
    executor = OrderExecutor(DummyAuthManager())
    results = executor.place_multi_leg_short_order(
        symbol="NIFTY",
        ce_strike=24450,
        pe_strike=24450,
        expiry="2026-04-30",
        quantity=50,
        dry_run=True,
        ltp_override_ce=120.0,
        ltp_override_pe=110.0,
    )
    assert "ce" in results and "pe" in results
    assert results["ce"].transaction_type == "SELL"
    assert results["pe"].transaction_type == "SELL"
    assert results["ce"].limit_price == round(120.0 * (1 - LIMIT_BUFFER_PCT), 1)
    assert results["pe"].limit_price == round(110.0 * (1 - LIMIT_BUFFER_PCT), 1)


def test_paper_executor_short_straddle_and_strangle():
    paper = PaperExecutor(config={"initial_capital": 500000})

    straddle_symbols = {
        "ce": "NSE_FO|NIFTY26APR24450CE",
        "pe": "NSE_FO|NIFTY26APR24450PE",
        "lot_size": 50,
    }
    res_straddle = paper.place_short_straddle(straddle_symbols, lots=1, ce_price=100.0, pe_price=100.0)
    assert res_straddle["success"] is True
    assert len(res_straddle["trades"]) == 2
    assert res_straddle["trades"][0]["side"] == "SELL"
    assert res_straddle["trades"][1]["side"] == "SELL"

    strangle_symbols = {
        "otm_ce": "NSE_FO|NIFTY26APR24550CE",
        "otm_pe": "NSE_FO|NIFTY26APR24350PE",
        "lot_size": 50,
    }
    res_strangle = paper.place_short_strangle(strangle_symbols, lots=1, ce_price=60.0, pe_price=50.0)
    assert res_strangle["success"] is True
    assert len(res_strangle["trades"]) == 2
    assert res_strangle["trades"][0]["side"] == "SELL"
    assert res_strangle["trades"][1]["side"] == "SELL"
