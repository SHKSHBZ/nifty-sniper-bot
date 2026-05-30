"""
zone_memory.py
==============
Zone Memory & Role Reversal Engine.
Implements TA Pillar 3: History Repeats Itself.

Tracks historical touches and breaks of OI Walls to identify
Level Strength and Role Reversal (Old R -> New S).
"""

from collections import defaultdict
from datetime import datetime, timedelta

class ZoneMemoryEngine:
    def __init__(self, fragility_threshold=4, flip_confirmation_mins=15):
        # Memory of strikes: {strike: [list of timestamps of touches]}
        self.touches = defaultdict(list)
        # Status: {strike: 'SUPPORT' | 'RESISTANCE' | 'FLIPPED'}
        self.level_status = {}
        # Flip confirmation window
        self.flip_confirmation_mins = flip_confirmation_mins
        self.fragility_threshold = fragility_threshold

    def record_touch(self, strike, price, ts=None):
        if ts is None: ts = datetime.now()
        self.touches[strike].append(ts)
        # Cleanup old touches (> 4 hours)
        cutoff = ts - timedelta(hours=4)
        self.touches[strike] = [t for t in self.touches[strike] if t > cutoff]

    def get_level_fragility(self, strike):
        """Returns True if a level has been touched too many times (likely to break)."""
        recent_touches = len(self.touches[strike])
        return recent_touches >= self.fragility_threshold

    def check_role_reversal(self, strike, current_spot, original_type):
        """
        Detects if an old wall has flipped status.
        If original_type was RESISTANCE but spot > strike for 15 mins, it's now SUPPORT.
        """
        # In a real implementation, this would track the 'sustain' above/below the strike.
        # For now, we provide the logic gate.
        if original_type == "RESISTANCE" and current_spot > strike:
            return "SUPPORT_RECLAIMED"
        if original_type == "SUPPORT" and current_spot < strike:
            return "RESISTANCE_RECLAIMED"
        return original_type

    def get_level_strength(self, strike):
        """
        Calculates a strength score. 
        Higher score = More times this level successfully rejected the price.
        """
        return len(self.touches[strike])
