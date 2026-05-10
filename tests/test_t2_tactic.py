"""Unit tests for tactics.t2_expiry_straddle.T2ExpiryStraddleTactic.

The "happy path" test uses inputs matching trade #16 (Jan 20 2026
straddle winner, +Rs.59,290) from reports/expiry_straddle_S2_sl50_trades.csv
to confirm the live tactic reproduces the same dual-leg signal and
combined-SL parameters.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tactics.base import TacticState, TacticSignal   # noqa: E402
from tactics.t2_expiry_straddle import T2ExpiryStraddleTactic, T2Config   # noqa: E402


IST = pytz.timezone("Asia/Kolkata")


def _state(*, hour: int = 14, minute: int = 50,
           dte: int = 0,
           ce_premium: float = 13.90, pe_premium: float = 15.70,
           spot: float = 25348.75) -> TacticState:
    return TacticState(
        ts=datetime(2026, 1, 20, hour, minute, tzinfo=IST),
        spot=spot,
        dte=dte,
        atm_ce_premium=ce_premium,
        atm_pe_premium=pe_premium,
    )


class TestT2HappyPath:
    """Inputs match the Jan 20 2026 winner from the backtest ledger."""

    def setup_method(self):
        self.t = T2ExpiryStraddleTactic()

    def test_jan20_winner_fires(self):
        sig = self.t.evaluate(_state())
        assert sig is not None
        assert sig.action == "enter"

    def test_jan20_winner_returns_straddle(self):
        sig = self.t.evaluate(_state())
        assert sig.is_straddle is True
        assert sig.direction == "CE"
        assert sig.second_direction == "PE"
        assert sig.strike_offset == 0
        assert sig.second_strike_offset == 0

    def test_jan20_winner_carries_combined_risk_params(self):
        sig = self.t.evaluate(_state())
        assert sig.combined_sl_pct == 0.50    # S2 variant: -50% SL
        assert sig.combined_tp_pct is None    # S2: no TP, let it run
        assert sig.time_stop_min == 35        # 14:50 -> 15:25

    def test_reason_string_contains_combined_premium(self):
        sig = self.t.evaluate(_state())
        # CE 13.90 + PE 15.70 = combined 29.60
        assert "29.60" in sig.reason
        assert "13.90" in sig.reason
        assert "15.70" in sig.reason


class TestT2TimeWindowGate:
    def setup_method(self):
        self.t = T2ExpiryStraddleTactic()

    def test_too_early_blocks(self):
        # 14:30 is before the 14:50-15:00 window
        assert self.t.evaluate(_state(hour=14, minute=30)) is None

    def test_too_late_blocks(self):
        assert self.t.evaluate(_state(hour=15, minute=15)) is None

    def test_at_1450_fires(self):
        assert self.t.evaluate(_state(hour=14, minute=50)) is not None

    def test_at_1500_fires(self):
        # 15:00 is the inclusive upper edge
        assert self.t.evaluate(_state(hour=15, minute=0)) is not None


class TestT2ExpiryDayGate:
    def setup_method(self):
        self.t = T2ExpiryStraddleTactic()

    def test_dte_0_fires(self):
        assert self.t.evaluate(_state(dte=0)) is not None

    def test_dte_1_blocks(self):
        # T2 is expiry-day-only
        assert self.t.evaluate(_state(dte=1)) is None

    def test_dte_5_blocks(self):
        assert self.t.evaluate(_state(dte=5)) is None


class TestT2PremiumBandGate:
    def setup_method(self):
        self.t = T2ExpiryStraddleTactic()

    def test_ce_too_low_blocks(self):
        assert self.t.evaluate(_state(ce_premium=4.99)) is None

    def test_ce_too_high_blocks(self):
        assert self.t.evaluate(_state(ce_premium=20.01)) is None

    def test_pe_too_low_blocks(self):
        assert self.t.evaluate(_state(pe_premium=3.0)) is None

    def test_pe_too_high_blocks(self):
        assert self.t.evaluate(_state(pe_premium=25.0)) is None

    def test_at_band_edges_fires(self):
        assert self.t.evaluate(_state(ce_premium=5.0, pe_premium=20.0)) is not None
        assert self.t.evaluate(_state(ce_premium=20.0, pe_premium=5.0)) is not None

    def test_zero_premium_blocks(self):
        # Stale data path — both premiums missing
        assert self.t.evaluate(_state(ce_premium=0.0, pe_premium=0.0)) is None


class TestT2NearMissGates:
    """gates_for_direction surfaces what the missed-tracker would see."""

    def setup_method(self):
        self.t = T2ExpiryStraddleTactic()

    def test_premium_gate_failure_visible(self):
        gates = self.t.gates_for_direction(
            _state(ce_premium=25.0), "CE"
        )
        assert gates["ce_premium"].passed is False
        assert gates["pe_premium"].passed is True
        assert gates["expiry_day"].passed is True
        assert gates["time_window"].passed is True

    def test_t2_is_direction_agnostic(self):
        # Direction-hint gate always passes — T2 is a straddle
        for d in ("CE", "PE"):
            gates = self.t.gates_for_direction(_state(), d)
            assert gates["direction"].passed is True


class TestTacticSignalBackwardCompat:
    """Sanity: existing single-leg signals must still work after the
    second_direction extension."""

    def test_single_leg_signal_is_not_straddle(self):
        sig = TacticSignal(action="enter", direction="CE")
        assert sig.is_straddle is False
        assert sig.second_direction is None
        assert sig.second_strike_offset == 0
        assert sig.combined_sl_pct is None
        assert sig.combined_tp_pct is None

    def test_straddle_signal_marked_correctly(self):
        sig = TacticSignal(
            action="enter", direction="CE",
            second_direction="PE",
            combined_sl_pct=0.50,
        )
        assert sig.is_straddle is True
        assert sig.second_direction == "PE"
