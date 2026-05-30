"""
candle_engine.py
================
Real-time Candlestick Pattern Recognition.
Implements PDF Topic 5: Candlestick Analysis.

Identifies Hammer, Shooting Star, and Engulfing patterns to act
as high-precision entry triggers.
"""

class CandleEngine:
    def __init__(self):
        pass

    def get_pattern(self, open_p, high_p, low_p, close_p, prev_candle=None):
        """
        Detects patterns in the current candle.
        prev_candle: {'open': f, 'high': f, 'low': f, 'close': f}
        """
        body = abs(close_p - open_p)
        candle_range = high_p - low_p
        if candle_range == 0: return None

        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p
        
        is_bullish = close_p > open_p
        is_bearish = close_p < open_p

        # 1. Hammer (Bullish Reversal)
        # Small body, long lower wick, little/no upper wick
        if lower_wick > (body * 2) and upper_wick < (candle_range * 0.1):
            return "HAMMER"

        # 2. Shooting Star (Bearish Reversal)
        # Small body, long upper wick, little/no lower wick
        if upper_wick > (body * 2) and lower_wick < (candle_range * 0.1):
            return "SHOOTING_STAR"

        # 3. Engulfing Patterns (Requires previous candle)
        if prev_candle:
            p_open, p_close = prev_candle['open'], prev_candle['close']
            p_body = abs(p_close - p_open)
            
            # Bullish Engulfing
            if is_bullish and p_close < p_open: # Prev was bearish
                if open_p <= p_close and close_p >= p_open:
                    return "BULLISH_ENGULFING"
            
            # Bearish Engulfing
            if is_bearish and p_close > p_open: # Prev was bullish
                if open_p >= p_close and close_p <= p_open:
                    return "BEARISH_ENGULFING"

        # 4. Marubozu (Conviction)
        if body > (candle_range * 0.9):
            return "BULLISH_MARUBOZU" if is_bullish else "BEARISH_MARUBOZU"

        return None
