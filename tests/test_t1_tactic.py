"""Unit tests for tactics.t1_vix_direction.T1VIXDirectionTactic.

Each gate is tested in isolation. The "happy path" test uses inputs
matching trade #4 (Oct 7, 2024 BUY_PE winner) from the validated backtest
ledger reports/t1_best_config_trades.csv to confirm the live tactic
reproduces the same direction and risk parameters.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tactics.base import TacticState   # noqa: E402
from tactics.t1_vix_direction import T1VIXDirectionTactic, T1Config   # noqa: E402


IST = pytz.timezone("Asia/Kolkata")


def _state(*, hour: int = 10, minute: int = 0,
           vix: float = 14.22, vix_chg: float = 1.14,
           dte: int = 3) -> TacticState:
    return TacticState(
        ts=datetime(2024, 10, 7, hour, minute, tzinfo=IST),
        vix_level=vix,
        vix_chg_today_pct=vix_chg,
        dte=dte,
    )


class TestT1HappyPath:
    """Inputs match the Oct 7 2024 winner from the backtest ledger."""

    def setup_method(self):
        self.t = T1VIXDirectionTactic()

    def test_oct7_winner_fires_pe(self):
        sig = self.t.evaluate(_state())
        assert sig is not None
        assert sig.direction == "PE"     # VIX rising -> NIFTY likely down -> PE
        assert sig.action == "enter"
        assert sig.strike_offset == 0    # ATM only

    def test_oct7_winner_carries_correct_risk_params(self):
        sig = self.t.evaluate(_state())
        assert sig.tp_pct == 0.30
        assert sig.sl_pct == 0.30
        assert sig.time_stop_min == 295   # ~10:30 entry to 15:25 EOD

    def test_vix_falling_fires_ce(self):
        sig = self.t.evaluate(_state(vix=13.23, vix_chg=-1.85, dte=6))
        assert sig is not None
        assert sig.direction == "CE"

    def test_signal_is_not_a_straddle(self):
        sig = self.t.evaluate(_state())
        assert sig.is_straddle is False
        assert sig.second_direction is None


class TestT1TimeWindowGate:
    def setup_method(self):
        self.t = T1VIXDirectionTactic()

    def test_before_window_does_not_fire(self):
        # 09:30 is before the 10:00-10:30 decision window
        assert self.t.evaluate(_state(hour=9, minute=30)) is None

    def test_after_window_does_not_fire(self):
        # 11:00 is after the window
        assert self.t.evaluate(_state(hour=11, minute=0)) is None

    def test_inside_window_fires(self):
        for h, m in [(10, 0), (10, 15), (10, 30)]:
            assert self.t.evaluate(_state(hour=h, minute=m)) is not None


class TestT1VIXBandGate:
    def setup_method(self):
        self.t = T1VIXDirectionTactic()

    def test_vix_too_low_blocks(self):
        assert self.t.evaluate(_state(vix=12.5)) is None   # below 13

    def test_vix_too_high_blocks(self):
        assert self.t.evaluate(_state(vix=18.5)) is None   # above 18

    def test_vix_at_lower_edge_fires(self):
        assert self.t.evaluate(_state(vix=13.0)) is not None

    def test_vix_at_upper_edge_blocks(self):
        # 18 is the EXCLUSIVE upper bound
        assert self.t.evaluate(_state(vix=18.0)) is None


class TestT1VIXChangeGate:
    def setup_method(self):
        self.t = T1VIXDirectionTactic()

    def test_change_below_threshold_blocks(self):
        assert self.t.evaluate(_state(vix_chg=0.3)) is None

    def test_change_at_threshold_fires(self):
        assert self.t.evaluate(_state(vix_chg=0.5)) is not None
        assert self.t.evaluate(_state(vix_chg=-0.5)) is not None

    def test_extreme_change_still_fires(self):
        # Backtest had a +3.36% trade — make sure no upper bound on |chg|
        assert self.t.evaluate(_state(vix_chg=3.36)) is not None


class TestT1DTEGate:
    def setup_method(self):
        self.t = T1VIXDirectionTactic()

    def test_dte_0_blocks(self):
        # Bot's spec says DTE 2-6; T1Config's defaults match that.
        # NB: backtest config used DTE [2,6]; some trades show DTE 0 in
        # the ledger but those came from a wider sweep variant. The
        # production T1Config rejects DTE < 2.
        assert self.t.evaluate(_state(dte=0)) is None

    def test_dte_1_blocks(self):
        assert self.t.evaluate(_state(dte=1)) is None

    def test_dte_2_fires(self):
        assert self.t.evaluate(_state(dte=2)) is not None

    def test_dte_6_fires(self):
        assert self.t.evaluate(_state(dte=6)) is not None

    def test_dte_7_blocks(self):
        assert self.t.evaluate(_state(dte=7)) is None


class TestT1NearMissGates:
    """gates_for_direction is what the live missed-tracker queries."""

    def setup_method(self):
        self.t = T1VIXDirectionTactic()

    def test_near_miss_pe_when_only_chg_fails(self):
        # All gates pass except VIX change is too small
        gates = self.t.gates_for_direction(_state(vix_chg=0.3), "PE")
        failed = [name for name, g in gates.items() if not g.passed]
        # Direction gate also fails because chg=0.3 doesn't satisfy >= +0.5
        # for PE — this is correct, exactly what near-miss tracker uses.
        assert "vix_intraday_chg" in failed

    def test_pe_direction_requires_vix_rising(self):
        gates = self.t.gates_for_direction(_state(vix_chg=-1.5), "PE")
        # vix_chg=-1.5 fails the PE direction gate (PE needs +chg)
        # but vix_intraday_chg passes (|−1.5| >= 0.5)
        assert gates["vix_intraday_chg"].passed is True
        assert gates["direction"].passed is False

    def test_ce_direction_requires_vix_falling(self):
        gates = self.t.gates_for_direction(_state(vix_chg=+1.5), "CE")
        assert gates["vix_intraday_chg"].passed is True
        assert gates["direction"].passed is False
