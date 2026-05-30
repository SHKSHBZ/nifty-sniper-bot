"""
volume_engine.py
================
Institutional Volume & OI Alignment Engine.
Implements PDF Topic 6: Volume and Open Interest.

Detects Volume Spikes and verifies if Volume confirms the price move.
Prevents entry on low-volume fakeouts.
"""

from collections import deque

class VolumeEngine:
    def __init__(self, window=20):
        self.volume_history = deque(maxlen=window)

    def update(self, volume):
        self.volume_history.append(volume)

    def is_volume_spike(self, current_volume, threshold=2.0):
        """Returns True if current volume is 'threshold' times the average."""
        if len(self.volume_history) < 5:
            return False
        
        avg_vol = sum(self.volume_history) / len(self.volume_history)
        if avg_vol == 0: return False
        
        return current_volume >= (avg_vol * threshold)

    def get_participation_score(self, current_volume):
        """Calculates a participation score based on relative volume."""
        if not self.volume_history: return 0.0
        avg_vol = sum(self.volume_history) / len(self.volume_history)
        if avg_vol == 0: return 0.0
        
        score = current_volume / avg_vol
        return min(10.0, score) # Cap at 10.0
