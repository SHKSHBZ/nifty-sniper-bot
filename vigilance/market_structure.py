"""
market_structure.py
===================
Price Action Market Structure Engine.
Implements PDF Topic 2: Trend Identification.

Tracks Swing Highs and Swing Lows to identify HH/HL and LH/LL patterns.
Classifies the market into Uptrend, Downtrend, or Sideways.
"""

from collections import deque
from datetime import datetime

class MarketStructureEngine:
    def __init__(self, window=3):
        self.window = window
        self.price_history = []
        self.swing_highs = deque(maxlen=5)
        self.swing_lows = deque(maxlen=5)
        self.trend = "SIDEWAYS"

    def update(self, ts, spot):
        self.price_history.append({'ts': ts, 'price': spot})
        if len(self.price_history) > 100:
            self.price_history.pop(0)
        
        self._detect_swings()
        self._update_trend()

    def _detect_swings(self):
        """Identifies peaks and troughs in the price history."""
        if len(self.price_history) < (self.window * 2 + 1):
            return

        # Check if the point at index -window-1 is a swing high/low
        target_idx = -self.window - 1
        target_price = self.price_history[target_idx]['price']
        
        # Check High
        is_high = True
        for i in range(1, self.window + 1):
            if self.price_history[target_idx - i]['price'] >= target_price or \
               self.price_history[target_idx + i]['price'] > target_price:
                is_high = False
                break
        
        if is_high:
            self.swing_highs.append(target_price)

        # Check Low
        is_low = True
        for i in range(1, self.window + 1):
            if self.price_history[target_idx - i]['price'] <= target_price or \
               self.price_history[target_idx + i]['price'] < target_price:
                is_low = False
                break
        
        if is_low:
            self.swing_lows.append(target_price)

    def _update_trend(self):
        """Classifies the trend based on HH/HL logic."""
        if len(self.swing_highs) < 2 or len(self.swing_lows) < 2:
            self.trend = "SIDEWAYS"
            return

        h1, h2 = self.swing_highs[-2], self.swing_highs[-1]
        l1, l2 = self.swing_lows[-2], self.swing_lows[-1]

        if h2 > h1 and l2 > l1:
            self.trend = "UPTREND"
        elif h2 < h1 and l2 < l1:
            self.trend = "DOWNTREND"
        else:
            self.trend = "SIDEWAYS"

    def get_structure(self):
        return {
            "trend": self.trend,
            "last_high": self.swing_highs[-1] if self.swing_highs else None,
            "last_low": self.swing_lows[-1] if self.swing_lows else None,
            "is_uptrend": self.trend == "UPTREND",
            "is_downtrend": self.trend == "DOWNTREND"
        }
