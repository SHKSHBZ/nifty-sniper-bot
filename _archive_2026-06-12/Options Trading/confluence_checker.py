import logging
from datetime import datetime

logger = logging.getLogger("ConfluenceChecker")

class ConfluenceChecker:
    """
    Professional 5-factor confluence for 9% Path.
    Minimum 4/5 required for Iron Condor entry.
    """
    def __init__(self, config):
        self.config = config
        self.vix_threshold = config.get('vix_neutral_max', 15.0)

    def get_score(self, current_price, india_vix, ema9, ema21, pcr, oi_buildup_neutral, max_pain_distance, rsi_14):
        """
        Calculates the score (0-5) based on neutral/range-bound criteria.
        Returns score and a list of detailed reasons.
        """
        score = 0
        details = {}

        # 1. Regime: VIX Stability
        vix_pass = india_vix < self.vix_threshold
        if vix_pass: score += 1
        details['VIX'] = {'val': india_vix, 'pass': vix_pass, 'msg': f"VIX Stable" if vix_pass else f"VIX High ({india_vix})"}

        # 2. EMA Trend: Sideways (EMA9 and EMA21 proximity)
        ema_diff_percent = abs(ema9 - ema21) / current_price * 100
        ema_pass = ema_diff_percent < 0.25 # Less than 0.25% gap
        if ema_pass: score += 1
        details['Trend'] = {'val': "SIDEWAYS" if ema_pass else "TRENDING", 'pass': ema_pass, 'msg': "Neutral" if ema_pass else "Trending"}

        # 3. PCR: Put-Call Ratio Balance
        pcr_pass = 0.85 <= pcr <= 1.15
        if pcr_pass: score += 1
        details['PCR'] = {'val': pcr, 'pass': pcr_pass, 'msg': "Balanced" if pcr_pass else f"Skewed ({pcr})"}

        # 4. OI Buildup: No directional breakout
        oi_pass = oi_buildup_neutral
        if oi_pass: score += 1
        details['OI'] = {'val': "NEUTRAL" if oi_pass else "BREAKOUT", 'pass': oi_pass, 'msg': "Balanced" if oi_pass else "Skewed OI"}

        # 5. Max Pain: Proximity to Expiry Pin
        mp_pass = abs(max_pain_distance) <= 150
        if mp_pass: score += 1
        details['MaxPain'] = {'val': max_pain_distance, 'pass': mp_pass, 'msg': "Near Pin" if mp_pass else "Far from Pin"}

        return score, details

    def get_market_regime(self, current_price, ema9, ema21, rsi_14):
        """
        Identifies the Trend Regime:
        - 1: Bullish Trend (Strong)
        - -1: Bearish Trend (Strong)
        - 0: Sideways/Neutral
        """
        ema_gap = (ema9 - ema21) / current_price * 100
        
        # Bullish: EMA 9 above 21 with > 0.3% gap and RSI rising
        if ema_gap > 0.3 and rsi_14 > 55:
            return 1, "BULLISH TREND"
            
        # Bearish: EMA 9 below 21 with > 0.3% gap and RSI falling
        if ema_gap < -0.3 and rsi_14 < 45:
            return -1, "BEARISH TREND"
            
        return 0, "NEUTRAL/SIDEWAYS"

    def is_liquidity_safe(self, bid, ask, max_spread_percent=0.5):
        """Ensures the Bid-Ask spread is tight enough for professional entry."""
        if bid == 0 or ask == 0: return False
        spread = (ask - bid) / ask * 100
        return spread <= max_spread_percent

    def detect_rejection(self, current_price, price_history, rsi_14):
        """
        Detects 'Bounce Back' potential:
        - 1: Bullish Rejection (Price bounced off support, RSI rising from <30)
        - -1: Bearish Rejection (Price rejected at resistance, RSI falling from >70)
        """
        if len(price_history) < 10: return 0
        
        # Bullish Rejection (V-Shape bounce)
        if rsi_14 < 35 and current_price > min(price_history[-10:]):
            if current_price > price_history[-2]: # Turning up
                return 1
                
        # Bearish Rejection (Inverted V bounce)
        if rsi_14 > 65 and current_price < max(price_history[-10:]):
            if current_price < price_history[-2]: # Turning down
                return -1
                
        return 0
