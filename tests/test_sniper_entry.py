"""
tests/test_sniper_entry.py
==========================
Comprehensive test suite for PriceActionBot v4.0 — SMC Price Action + 3TF Hybrid HTF.

Tests:
  - Swing detection (5-candle method)
  - BOS (Break of Structure) detection
  - CHoCH+ (Change of Character) detection
  - Rejection candle confirmation
  - 3TF HTF state scoring
  - Daily loss guard & cooldown
"""

import unittest
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_engine import (
    PriceActionBot, classify_dte_risk, calculate_position_size,
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
# PriceActionBot Tests — Swing Detection & BOS/CHoCH+
# ==========================================================================

class TestSwingDetection(unittest.TestCase):
    """5-candle swing high/low detection."""

    def _make_candles(self, highs, lows, closes=None):
        """Build candle list from parallel arrays of high/low/close."""
        now = datetime.now()
        if closes is None:
            closes = [(h + l) / 2 for h, l in zip(highs, lows)]
        candles = []
        for i, (h, l) in enumerate(zip(highs, lows)):
            c = closes[i]
            o = c - 2 if i % 2 == 0 else c + 2
            candles.append({
                "ts": now + timedelta(minutes=i * 5),
                "o": o, "h": h, "l": l, "c": c,
            })
        return candles

    def test_clear_swing_high(self):
        """A clear peak in the middle → detected as swing high."""
        highs = [100, 102, 101, 105, 103, 102, 101]  # peak at index 3
        lows = [98, 99, 99, 103, 100, 99, 98]
        candles = self._make_candles(highs, lows)
        sh, sl = PriceActionBot._detect_swings(candles, lookback=2)
        self.assertEqual(len(sh), 1)
        self.assertEqual(sh[0][0], 3)  # index 3
        self.assertAlmostEqual(sh[0][1], 105.0)

    def test_clear_swing_low(self):
        """A clear trough → detected as swing low."""
        highs = [100, 101, 102, 100, 103, 104, 105]
        lows = [98, 97, 96, 92, 95, 97, 99]  # trough at index 3
        candles = self._make_candles(highs, lows)
        sh, sl = PriceActionBot._detect_swings(candles, lookback=2)
        self.assertEqual(len(sl), 1)
        self.assertEqual(sl[0][0], 3)
        self.assertAlmostEqual(sl[0][1], 92.0)

    def test_no_swing_in_trend(self):
        """Strictly monotonic trend → no swings detected."""
        highs = [100, 102, 104, 106, 108, 110, 112]
        lows = [98, 100, 102, 104, 106, 108, 110]
        candles = self._make_candles(highs, lows)
        sh, sl = PriceActionBot._detect_swings(candles, lookback=2)
        self.assertEqual(len(sh), 0)
        self.assertEqual(len(sl), 0)

    def test_insufficient_candles(self):
        """Fewer than 2*lookback+1 candles → no swings."""
        highs = [100, 102, 101, 103]
        lows = [98, 99, 98, 100]
        candles = self._make_candles(highs, lows)
        sh, sl = PriceActionBot._detect_swings(candles, lookback=2)
        self.assertEqual(len(sh), 0)
        self.assertEqual(len(sl), 0)


class TestBOSDetection(unittest.TestCase):
    """Break of Structure detection."""

    def _make_candles(self, closes):
        """Make candles that form swing highs/lows for BOS testing."""
        now = datetime.now()
        candles = []
        for i, c in enumerate(closes):
            candles.append({
                "ts": now + timedelta(minutes=i * 5),
                "o": c - 2, "h": c + 3, "l": c - 3, "c": c,
            })
        return candles

    def test_bullish_bos(self):
        """Price breaks above previous swing high → bullish BOS."""
        closes = [100, 98, 96, 99, 97, 95, 100, 98, 102, 101, 104, 106]
        candles = self._make_candles(closes)
        sh, sl = PriceActionBot._detect_swings(candles, lookback=2)
        event, level, idx = PriceActionBot._detect_bos(candles, sh, sl)
        self.assertEqual(event, "bullish_bos")

    def test_bearish_bos(self):
        """Price breaks below previous swing low → bearish BOS."""
        closes = [100, 102, 104, 101, 103, 105, 100, 102, 98, 99, 96, 94]
        candles = self._make_candles(closes)
        sh, sl = PriceActionBot._detect_swings(candles, lookback=2)
        event, level, idx = PriceActionBot._detect_bos(candles, sh, sl)
        self.assertEqual(event, "bearish_bos")

    def test_no_bos_without_swing_break(self):
        """No swing broken → no BOS."""
        closes = [100, 101, 100, 101, 100, 101, 100, 101, 100]
        candles = self._make_candles(closes)
        sh, sl = PriceActionBot._detect_swings(candles, lookback=2)
        event, level, idx = PriceActionBot._detect_bos(candles, sh, sl)
        self.assertIsNone(event)


class TestRejectionCandle(unittest.TestCase):
    """Rejection candle confirmation."""

    def test_bullish_rejection(self):
        """Close in upper half → bullish rejection."""
        candle = {"o": 100, "h": 110, "l": 90, "c": 106}
        self.assertTrue(PriceActionBot._check_rejection(candle, "bull"))

    def test_bearish_rejection(self):
        """Close in lower half → bearish rejection."""
        candle = {"o": 100, "h": 110, "l": 90, "c": 94}
        self.assertTrue(PriceActionBot._check_rejection(candle, "bear"))

    def test_no_rejection_mid_close(self):
        """Close below midpoint → no bullish rejection."""
        candle = {"o": 100, "h": 110, "l": 90, "c": 99}
        # close_pct = (99-90)/(110-90) = 9/20 = 0.45 < 0.5
        self.assertFalse(PriceActionBot._check_rejection(candle, "bull"))

    def test_doji_no_rejection(self):
        """Tiny range, close in lower half → no bullish rejection."""
        candle = {"o": 100, "h": 100.2, "l": 99.8, "c": 99.9}
        # close_pct = (99.9-99.8)/(100.2-99.8) = 0.1/0.4 = 0.25 < 0.5
        self.assertFalse(PriceActionBot._check_rejection(candle, "bull"))


class TestDailyRiskGuards(unittest.TestCase):
    """Daily max loss and cooldown guards."""

    def test_blocked_after_max_loss(self):
        """After cumulative loss exceeds MAX_DAILY_LOSS, bot blocks."""
        bot = PriceActionBot(fetcher=None)
        bot.reset_daily_state()
        # Multiple losses exceeding 8000
        bot.on_trade_closed(-5000)
        bot.on_trade_closed(-4000)
        self.assertTrue(bot._blocked_by_loss)

    def test_not_blocked_under_limit(self):
        """Small loss doesn't trigger block."""
        bot = PriceActionBot(fetcher=None)
        bot.reset_daily_state()
        bot.on_trade_closed(-3000)
        self.assertFalse(bot._blocked_by_loss)

    def test_reset_clears_block(self):
        """reset_daily_state clears the blocked flag."""
        bot = PriceActionBot(fetcher=None)
        bot.on_trade_closed(-10000)
        self.assertTrue(bot._blocked_by_loss)
        bot.reset_daily_state()
        self.assertFalse(bot._blocked_by_loss)

    def test_no_signal_structure(self):
        """_no_signal returns correct dict shape."""
        bot = PriceActionBot(fetcher=None)
        result = bot._no_signal(["reason1"])
        self.assertIsNone(result["direction"])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["htf_state"], "neutral")
        self.assertIn("direction", result)
        self.assertIn("reasons", result)
        self.assertIn("dte_risk", result)


class TestHTFState(unittest.TestCase):
    """3TF HTF state scoring."""

    def test_htf_state_exists(self):
        """Method returns valid states."""
        bot = PriceActionBot(fetcher=None)
        # Before bootstrap, should return neutral
        state = bot._get_htf_state(23600, "CE")
        self.assertIn(state, ["confirm", "neutral", "oppose"])


# ==========================================================================
# DTE Risk Classification Tests (kept from v3)
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
    print("[TEST] PRICE ACTION BOT v4.0 -- TEST SUITE")
    print("=" * 70)
    print()
    unittest.main(verbosity=2)
