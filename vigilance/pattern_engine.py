"""
pattern_engine.py
=================
Automated Chart Pattern Recognition.
Implements PDF Topic 3: Chart Patterns.

Identifies Reversal Patterns (Double Tops, Head & Shoulders) 
and Continuation Patterns (Triangles, Flags) using Swing data.
"""

class PatternEngine:
    def __init__(self, proximity_threshold=0.001): # 0.1% for "same level"
        self.proximity_threshold = proximity_threshold

    def detect_reversal(self, highs, lows):
        """Detects Double/Triple Tops and Bottoms."""
        if len(highs) < 2 or len(lows) < 2:
            return None

        # Double Top Check
        if abs(highs[-1] - highs[-2]) / highs[-1] <= self.proximity_threshold:
            return "DOUBLE_TOP"

        # Double Bottom Check
        if abs(lows[-1] - lows[-2]) / lows[-1] <= self.proximity_threshold:
            return "DOUBLE_BOTTOM"
        
        # Head and Shoulders Check (Simplified)
        if len(highs) >= 3:
            # H&S: Left Shoulder (H1), Head (H2), Right Shoulder (H3)
            # Logic: H2 > H1 and H2 > H3
            if highs[-2] > highs[-3] and highs[-2] > highs[-1]:
                return "HEAD_AND_SHOULDERS"
                
        return None

    def detect_continuation(self, highs, lows):
        """Detects Triangles and Flags."""
        if len(highs) < 3 or len(lows) < 3:
            return None

        # Ascending Triangle: Rising Lows + Flat Highs
        flat_highs = abs(highs[-1] - highs[-2]) / highs[-1] <= self.proximity_threshold
        rising_lows = lows[-1] > lows[-2] > lows[-3]
        if flat_highs and rising_lows:
            return "ASCENDING_TRIANGLE"

        # Descending Triangle: Falling Highs + Flat Lows
        flat_lows = abs(lows[-1] - lows[-2]) / lows[-1] <= self.proximity_threshold
        falling_highs = highs[-1] < highs[-2] < highs[-3]
        if flat_lows and falling_highs:
            return "DESCENDING_TRIANGLE"

        return None
