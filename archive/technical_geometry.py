import logging
from datetime import datetime

logger = logging.getLogger("TechnicalGeometry")

class TechnicalGeometry:
    """
    Manages Intra-day Chart Geometry:
    - 15/30-minute Opening Range Breakout (ORB)
    - Dynamic Support and Resistance (Psychological & MAs)
    - Morning Gaps
    """
    def __init__(self):
        self.opening_range_high = -1.0
        self.opening_range_low = float('inf')
        self.orb_complete = False
        
        self.prev_day_close = 0.0
        self.morning_gap = 0.0
        
        # Keep track of active rejections
        self.active_resistance = None
        self.active_support = None

    def process_tick(self, ltp, timestamp=None):
        """
        Processes real-time ticks to form the Opening Range.
        Expected to be called sequentially during market hours.
        """
        if timestamp is None:
            timestamp = datetime.now()
            
        current_time = timestamp.time()
        start_time = datetime.strptime("09:15:00", "%H:%M:%S").time()
        orb_end_time = datetime.strptime("09:45:00", "%H:%M:%S").time()
        
        # 1. Opening Range Formation
        if start_time <= current_time < orb_end_time:
            if ltp > self.opening_range_high:
                self.opening_range_high = ltp
            if ltp < self.opening_range_low:
                self.opening_range_low = ltp
        # 2. ORB Completion Trigger
        elif current_time >= orb_end_time and not self.orb_complete:
            if self.opening_range_high != -1.0 and self.opening_range_low != float('inf'):
                self.orb_complete = True
                logger.info(f"Opening Range locked: High={self.opening_range_high:.2f}, Low={self.opening_range_low:.2f}")

    def check_orb_breakout(self, ltp):
        """
        Returns 1 for Bullish ORB, -1 for Bearish ORB, 0 for neutral.
        """
        if not self.orb_complete:
            return 0
            
        # Needs a convincing breakout (e.g., 5 points past the ORB)
        buffer = 5.0 
        
        if ltp > (self.opening_range_high + buffer):
            return 1 # Bullish Breakout
        elif ltp < (self.opening_range_low - buffer):
            return -1 # Bearish Breakdown
        return 0

    def get_nearest_psychological_level(self, ltp, index_name="NIFTY"):
        """
        Dynamic Support/Resistance Mapping based on 'hard' round numbers.
        Nifty respects 500s / 1000s deeply. Sensex respects 1000s.
        """
        step = 500 if "Nifty 50" in index_name else 1000
        lower_bound = (int(ltp) // step) * step
        upper_bound = lower_bound + step
        
        dist_down = ltp - lower_bound
        dist_up = upper_bound - ltp
        
        return {
            'support': lower_bound,
            'resistance': upper_bound,
            'dist_to_support': dist_down,
            'dist_to_resistance': dist_up
        }

    def detect_gap_and_go(self, open_price, prev_close):
        """
        Validates massive overnight gaps that trap retail traders.
        """
        self.prev_day_close = prev_close
        self.morning_gap = open_price - prev_close
        
        gap_percent = (self.morning_gap / prev_close) * 100
        if gap_percent > 0.6:
            return 1, f"MASSIVE GAP UP ({gap_percent:.2f}%)"
        if gap_percent < -0.6:
            return -1, f"MASSIVE GAP DOWN ({gap_percent:.2f}%)"
            
        return 0, "FLAT OPEN"
