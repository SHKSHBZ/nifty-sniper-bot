"""
vic_engine.py
=============
Velocity of Institutional Conviction (VIC) Engine.
Implements TA Pillar 1: Market Action Discounts Everything.

Calculates the 'Institutional Momentum' by tracking the velocity of 
Open Interest changes, providing a leading signal for price breakouts.
"""

from collections import deque
from datetime import datetime, timedelta

class VICEngine:
    def __init__(self, lookback_minutes=15):
        self.lookback_minutes = lookback_minutes
        # History of (timestamp, focus_pcr, total_put_oi, total_call_oi)
        self.history = deque(maxlen=60) 

    def update(self, ts, focus_pcr, total_put_oi, total_call_oi):
        """Update history with new snapshot."""
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        self.history.append((ts, focus_pcr, total_put_oi, total_call_oi))

    def get_conviction_score(self):
        """
        Calculates a score from -10 to +10.
        + score = Bullish Conviction (Institutional buying/writing puts)
        - score = Bearish Conviction (Institutional selling/writing calls)
        """
        if len(self.history) < 5:
            return 0.0

        now_ts, now_pcr, now_put, now_call = self.history[-1]
        
        # Find the sample closest to lookback_minutes ago
        cutoff = now_ts - timedelta(minutes=self.lookback_minutes)
        ref_sample = None
        for sample in reversed(self.history):
            if sample[0] <= cutoff:
                ref_sample = sample
                break
        
        if not ref_sample:
            ref_sample = self.history[0] # Fallback to oldest

        ref_ts, ref_pcr, ref_put, ref_call = ref_sample
        
        # 1. PCR Velocity (Rate of change in sentiment)
        pcr_delta = now_pcr - ref_pcr
        # A change of 0.1 in PCR over 15 mins is significant
        pcr_score = (pcr_delta / 0.1) * 5.0 

        # 2. OI Shift Velocity (ΔPut vs ΔCall)
        d_put = now_put - ref_put
        d_call = now_call - ref_call
        
        oi_score = 0
        if d_put > 0 and d_call > 0:
            ratio = d_put / d_call
            if ratio > 1.5: oi_score = 5.0  # Put writers 1.5x more aggressive
            elif ratio < 0.6: oi_score = -5.0 # Call writers 1.5x more aggressive
        elif d_put > 0 and d_call <= 0:
            oi_score = 5.0 # Only put writers active
        elif d_call > 0 and d_put <= 0:
            oi_score = -5.0 # Only call writers active

        total_score = pcr_score + oi_score
        return max(-10.0, min(10.0, total_score))

    def get_signal(self):
        score = self.get_conviction_score()
        if score >= 7.0: return "STRONG_BULLISH"
        if score >= 3.0: return "BULLISH"
        if score <= -7.0: return "STRONG_BEARISH"
        if score <= -3.0: return "BEARISH"
        return "NEUTRAL"
