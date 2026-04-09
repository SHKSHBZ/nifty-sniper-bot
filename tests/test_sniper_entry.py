"""
tests/test_sniper_entry.py
==========================
Comprehensive test suite for the Sniper Entry Logic v3.0

Tests the three-gate entry system:
  Gate 1: Spot Sustain Check
  Gate 2: Focus Zone PCR
  Gate 3: OI Build-Up Confirmation
"""

import unittest
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_engine import (
    SignalEngine, check_sustain, interpret_focus_pcr,
    check_oi_confirmation, classify_dte_risk
)


# ==========================================================================
# Test Helpers
# ==========================================================================

def build_spot_history(base_price, count=20, interval_seconds=60):
    """Generate spot history with a consistent price (simulates price sitting at a level)."""
    now = datetime.now()
    history = []
    for i in range(count):
        t = now - timedelta(seconds=(count - 1 - i) * interval_seconds)
        history.append({'time': t, 'spot': base_price})
    return history


def build_spot_history_varied(prices, interval_seconds=60):
    """Generate spot history with specified prices (simulates price movement)."""
    now = datetime.now()
    history = []
    count = len(prices)
    for i, price in enumerate(prices):
        t = now - timedelta(seconds=(count - 1 - i) * interval_seconds)
        history.append({'time': t, 'spot': price})
    return history


# ==========================================================================
# Gate 1: Spot Sustain Engine Tests
# ==========================================================================

class TestSustainCheck(unittest.TestCase):
    """Gate 1: Verify price sustain logic at support/resistance levels."""

    def test_sustain_confirmed_at_support(self):
        """Price stays within 0.15% of support for 3 consecutive 5m candles → PASS."""
        support = 22400
        # 15 readings all at 22410 (within 0.15% of 22400)
        history = build_spot_history(22410, count=15)
        self.assertTrue(check_sustain(history, support))

    def test_sustain_confirmed_at_resistance(self):
        """Price stays within 0.15% of resistance for 3 consecutive 5m candles → PASS."""
        resistance = 22700
        history = build_spot_history(22695, count=15)
        self.assertTrue(check_sustain(history, resistance))

    def test_sustain_fails_insufficient_data(self):
        """Not enough history (only 5 readings, need 11) → FAIL."""
        support = 22400
        history = build_spot_history(22410, count=5)
        self.assertFalse(check_sustain(history, support))

    def test_sustain_fails_price_left_zone(self):
        """Price was far away 10 minutes ago, only recently arrived → FAIL."""
        support = 22400
        # idx -1 = 22410 ✓, idx -6 = 22410 ✓, idx -11 = 22500 ✗ (0.44% away)
        prices = [22500] * 5 + [22410] * 10
        history = build_spot_history_varied(prices)
        self.assertFalse(check_sustain(history, support))

    def test_sustain_passes_at_edge_of_proximity(self):
        """Price exactly at the proximity boundary → PASS."""
        support = 22400
        # 0.15% of ~22433 ≈ 33.6 points → 22433 is 33 away from 22400
        edge_price = 22433  # dist = 33/22433 = 0.00147 ≈ 0.147% < 0.15%
        history = build_spot_history(edge_price, count=15)
        self.assertTrue(check_sustain(history, support))

    def test_sustain_fails_beyond_proximity(self):
        """Price slightly beyond the 0.15% boundary → FAIL."""
        support = 22400
        far_price = 22440  # dist = 40/22440 = 0.00178 = 0.178% > 0.15%
        history = build_spot_history(far_price, count=15)
        self.assertFalse(check_sustain(history, support))

    def test_sustain_with_empty_history(self):
        """Empty spot history → FAIL gracefully."""
        self.assertFalse(check_sustain([], 22400))

    def test_sustain_gap_in_middle(self):
        """Price was in zone, left briefly at 5m ago, back now → FAIL."""
        support = 22400
        # 15 readings: in zone, then out at index -6, then back
        prices = [22410] * 4 + [22500] + [22410] * 5 + [22500] + [22410] * 4
        history = build_spot_history_varied(prices)
        # idx -1 = 22410 ✓, idx -6 = 22410 ✓, idx -11 = 22500 ✗
        # Actually let me be precise: idx -1=prices[14]=22410, idx -6=prices[9]=22410, idx -11=prices[4]=22500
        self.assertFalse(check_sustain(history, support))


# ==========================================================================
# Gate 2: Focus Zone PCR Tests
# ==========================================================================

class TestFocusPCR(unittest.TestCase):
    """Gate 2: Focus Zone PCR interpretation tests."""

    def test_bullish_pcr_high(self):
        """PCR = 1.3 → Heavy Put OI → Bullish."""
        self.assertEqual(interpret_focus_pcr(1.3), "bullish")

    def test_bullish_pcr_threshold(self):
        """PCR = 1.1 → At threshold → Bullish."""
        self.assertEqual(interpret_focus_pcr(1.1), "bullish")

    def test_bearish_pcr_low(self):
        """PCR = 0.6 → Heavy Call OI → Bearish."""
        self.assertEqual(interpret_focus_pcr(0.6), "bearish")

    def test_bearish_pcr_threshold(self):
        """PCR = 0.85 → At threshold → Bearish."""
        self.assertEqual(interpret_focus_pcr(0.85), "bearish")

    def test_neutral_pcr(self):
        """PCR = 0.95 → Balanced → Neutral."""
        self.assertEqual(interpret_focus_pcr(0.95), "neutral")

    def test_neutral_pcr_center(self):
        """PCR = 1.0 → Perfectly balanced → Neutral."""
        self.assertEqual(interpret_focus_pcr(1.0), "neutral")

    def test_neutral_upper_edge(self):
        """PCR = 1.09 → Just below bullish threshold → Neutral."""
        self.assertEqual(interpret_focus_pcr(1.09), "neutral")

    def test_neutral_lower_edge(self):
        """PCR = 0.86 → Just above bearish threshold → Neutral."""
        self.assertEqual(interpret_focus_pcr(0.86), "neutral")


# ==========================================================================
# Gate 3: OI Build-Up Confirmation Tests
# ==========================================================================

class TestOIConfirmation(unittest.TestCase):
    """Gate 3: OI Build-Up vs Unwinding detection tests."""

    def test_ce_confirmed_pe_buildup(self):
        """For CE entry: Put OI increasing → writers defending support → PASS."""
        oi = {'ce_oi_change': 10000, 'pe_oi_change': 50000}
        confirmed, reason = check_oi_confirmation(oi, "CE")
        self.assertTrue(confirmed)
        self.assertIn("Build-Up", reason)

    def test_ce_rejected_pe_unwinding(self):
        """For CE entry: Put OI decreasing → support weakening → FAIL."""
        oi = {'ce_oi_change': 5000, 'pe_oi_change': -30000}
        confirmed, reason = check_oi_confirmation(oi, "CE")
        self.assertFalse(confirmed)
        self.assertIn("Unwinding", reason)

    def test_pe_confirmed_ce_buildup(self):
        """For PE entry: Call OI increasing → writers defending resistance → PASS."""
        oi = {'ce_oi_change': 60000, 'pe_oi_change': -5000}
        confirmed, reason = check_oi_confirmation(oi, "PE")
        self.assertTrue(confirmed)
        self.assertIn("Build-Up", reason)

    def test_pe_rejected_ce_unwinding(self):
        """For PE entry: Call OI decreasing → resistance weakening → FAIL."""
        oi = {'ce_oi_change': -40000, 'pe_oi_change': 10000}
        confirmed, reason = check_oi_confirmation(oi, "PE")
        self.assertFalse(confirmed)
        self.assertIn("Unwinding", reason)

    def test_zero_oi_change_rejected(self):
        """Zero OI change = no conviction → FAIL for both directions."""
        oi = {'ce_oi_change': 0, 'pe_oi_change': 0}
        confirmed_ce, _ = check_oi_confirmation(oi, "CE")
        confirmed_pe, _ = check_oi_confirmation(oi, "PE")
        self.assertFalse(confirmed_ce)
        self.assertFalse(confirmed_pe)

    def test_ce_only_cares_about_pe_oi(self):
        """CE entry should pass even if CE OI is unwinding, as long as PE OI builds up."""
        oi = {'ce_oi_change': -50000, 'pe_oi_change': 30000}
        confirmed, _ = check_oi_confirmation(oi, "CE")
        self.assertTrue(confirmed)

    def test_pe_only_cares_about_ce_oi(self):
        """PE entry should pass even if PE OI is unwinding, as long as CE OI builds up."""
        oi = {'ce_oi_change': 40000, 'pe_oi_change': -20000}
        confirmed, _ = check_oi_confirmation(oi, "PE")
        self.assertTrue(confirmed)


# ==========================================================================
# Full Signal Evaluation (End-to-End Integration Tests)
# ==========================================================================

class TestFullSignalEvaluation(unittest.TestCase):
    """End-to-end signal evaluation testing all three gates together."""

    def setUp(self):
        self.engine = SignalEngine()

    def test_ce_entry_all_gates_pass(self):
        """Perfect CE setup: sustained + bullish PCR + PE build-up → BUY CE."""
        signal = self.engine.evaluate(
            spot_close=22410,
            support=22400,
            resistance=22700,
            focus_pcr=1.25,  # Bullish
            oi_pattern={'ce_oi_change': 5000, 'pe_oi_change': 80000},  # PE building
            spot_history=build_spot_history(22410, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertEqual(signal['direction'], "CE")
        self.assertGreater(signal['score'], 0)
        # All 3 gates should show PASS
        pass_count = sum(1 for r in signal['reasons'] if 'PASS' in r)
        self.assertEqual(pass_count, 3, f"Expected 3 PASS gates, got {pass_count}. Reasons: {signal['reasons']}")

    def test_pe_entry_all_gates_pass(self):
        """Perfect PE setup: sustained + bearish PCR + CE build-up → BUY PE."""
        signal = self.engine.evaluate(
            spot_close=22695,
            support=22300,
            resistance=22700,
            focus_pcr=0.65,  # Bearish
            oi_pattern={'ce_oi_change': 90000, 'pe_oi_change': -5000},  # CE building
            spot_history=build_spot_history(22695, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertEqual(signal['direction'], "PE")
        self.assertGreater(signal['score'], 0)

    def test_ce_entry_neutral_pcr_still_passes(self):
        """CE entry with neutral PCR (not bearish) should still pass Gate 2."""
        signal = self.engine.evaluate(
            spot_close=22410,
            support=22400,
            resistance=22700,
            focus_pcr=0.95,  # Neutral (not bearish)
            oi_pattern={'ce_oi_change': 5000, 'pe_oi_change': 50000},
            spot_history=build_spot_history(22410, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertEqual(signal['direction'], "CE")

    def test_no_signal_far_from_walls(self):
        """Price is midway between support and resistance → No signal."""
        signal = self.engine.evaluate(
            spot_close=22550,  # Far from both S=22400 and R=22700
            support=22400,
            resistance=22700,
            focus_pcr=1.25,
            oi_pattern={'ce_oi_change': 5000, 'pe_oi_change': 50000},
            spot_history=build_spot_history(22550, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertIsNone(signal['direction'])
        self.assertEqual(signal['score'], 0)
        self.assertTrue(any('No proximity' in r for r in signal['reasons']))

    def test_sustain_not_met_no_signal(self):
        """Price near support but only just arrived (not sustained) → No signal."""
        # Price was at 22550 for 10 readings, only came to support zone in last 5
        prices = [22550] * 10 + [22410] * 5
        signal = self.engine.evaluate(
            spot_close=22410,
            support=22400,
            resistance=22700,
            focus_pcr=1.25,
            oi_pattern={'ce_oi_change': 5000, 'pe_oi_change': 50000},
            spot_history=build_spot_history_varied(prices),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertIsNone(signal['direction'])
        self.assertTrue(any('PENDING' in r for r in signal['reasons']))

    def test_pcr_conflict_blocks_ce(self):
        """Price sustained at support but PCR is bearish → Gate 2 blocks CE."""
        signal = self.engine.evaluate(
            spot_close=22410,
            support=22400,
            resistance=22700,
            focus_pcr=0.65,  # Bearish → conflicts with CE entry
            oi_pattern={'ce_oi_change': 5000, 'pe_oi_change': 50000},
            spot_history=build_spot_history(22410, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertIsNone(signal['direction'])
        self.assertTrue(any('GATE 2 FAIL' in r for r in signal['reasons']))

    def test_pcr_conflict_blocks_pe(self):
        """Price sustained at resistance but PCR is bullish → Gate 2 blocks PE."""
        signal = self.engine.evaluate(
            spot_close=22695,
            support=22300,
            resistance=22700,
            focus_pcr=1.30,  # Bullish → conflicts with PE entry
            oi_pattern={'ce_oi_change': 60000, 'pe_oi_change': -5000},
            spot_history=build_spot_history(22695, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertIsNone(signal['direction'])
        self.assertTrue(any('GATE 2 FAIL' in r for r in signal['reasons']))

    def test_oi_unwinding_blocks_ce(self):
        """Sustained + bullish PCR but PUT OI unwinding → Gate 3 blocks CE."""
        signal = self.engine.evaluate(
            spot_close=22410,
            support=22400,
            resistance=22700,
            focus_pcr=1.25,
            oi_pattern={'ce_oi_change': 5000, 'pe_oi_change': -30000},  # PE unwinding!
            spot_history=build_spot_history(22410, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertIsNone(signal['direction'])
        self.assertTrue(any('GATE 3 FAIL' in r for r in signal['reasons']))

    def test_oi_unwinding_blocks_pe(self):
        """Sustained + bearish PCR but CALL OI unwinding → Gate 3 blocks PE."""
        signal = self.engine.evaluate(
            spot_close=22695,
            support=22300,
            resistance=22700,
            focus_pcr=0.65,
            oi_pattern={'ce_oi_change': -40000, 'pe_oi_change': 10000},  # CE unwinding!
            spot_history=build_spot_history(22695, count=20),
            expiry_date="2026-04-15",
            current_date="2026-04-10"
        )

        self.assertIsNone(signal['direction'])
        self.assertTrue(any('GATE 3 FAIL' in r for r in signal['reasons']))

    def test_expiry_day_classification(self):
        """Trade on expiry day should flag DTE correctly."""
        signal = self.engine.evaluate(
            spot_close=22410,
            support=22400,
            resistance=22700,
            focus_pcr=1.25,
            oi_pattern={'ce_oi_change': 5000, 'pe_oi_change': 50000},
            spot_history=build_spot_history(22410, count=20),
            expiry_date="2026-04-10",
            current_date="2026-04-10"
        )

        self.assertTrue(signal['is_expiry_day'])
        self.assertEqual(signal['dte_risk'], "EXTREME")
        self.assertEqual(signal['dte_days'], 0)


# ==========================================================================
# DTE Risk Classification Tests
# ==========================================================================

class TestDTERisk(unittest.TestCase):
    """DTE (Days to Expiry) risk classification tests."""

    def test_extreme_dte(self):
        risk, dte = classify_dte_risk("2026-04-12", "2026-04-10")
        self.assertEqual(risk, "EXTREME")
        self.assertEqual(dte, 2)

    def test_high_dte(self):
        risk, dte = classify_dte_risk("2026-04-15", "2026-04-10")
        self.assertEqual(risk, "HIGH")
        self.assertEqual(dte, 5)

    def test_moderate_dte(self):
        risk, dte = classify_dte_risk("2026-04-25", "2026-04-10")
        self.assertEqual(risk, "MODERATE")
        self.assertEqual(dte, 15)

    def test_expiry_day_dte(self):
        risk, dte = classify_dte_risk("2026-04-10", "2026-04-10")
        self.assertEqual(risk, "EXTREME")
        self.assertEqual(dte, 0)

    def test_past_expiry(self):
        """Past expiry date should still return EXTREME with 0 DTE."""
        risk, dte = classify_dte_risk("2026-04-08", "2026-04-10")
        self.assertEqual(risk, "EXTREME")
        self.assertEqual(dte, 0)


# ==========================================================================
# Run Tests
# ==========================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("[TEST] SNIPER ENTRY LOGIC v3.0 -- TEST SUITE")
    print("=" * 70)
    print()
    unittest.main(verbosity=2)
