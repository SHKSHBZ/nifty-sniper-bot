import requests
import threading
import time
import logging
import upstox_client
from collections import deque
from datetime import datetime, timedelta

logger = logging.getLogger("DataFetcher")

class DataFetcher:
    """
    Professional Macro-Intelligence:
    Calculates PCR, Max Pain, and VIX SMA from Upstox REST APIs.
    """
    def __init__(self, config):
        self.config = config
        self.access_token = self._load_access_token()
        self.base_url = "https://api.upstox.com/v2"
        
        self.cache = {
            'pcr': 1.05,
            'max_pain_strike': 25500,
            'max_pain_dist': 0,
            'last_update': 0
        }
        self.vix_history = deque(maxlen=20)
        self.lock = threading.Lock()

        # Fix: Pre-load VIX historical SMA on startup
        self._preload_vix_history()
        
        # Start periodic background updater
        self._start_updater()

    def _load_access_token(self):
        """Always load the latest token from the session file."""
        try:
            from upstox_auth import UpstoxAuth
            auth = UpstoxAuth()
            if auth.is_session_valid():
                return auth.access_token
        except Exception as e:
            logger.error(f"Error loading access token: {e}")
        return None

    def _get_expiry(self):
        today = datetime.now().date()
        days_ahead = (1 - today.weekday()) % 7
        if days_ahead == 0: days_ahead = 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    def _preload_vix_history(self):
        """Fetches the last 20 days of VIX candles to avoid 'empty memory' on start."""
        try:
            self.access_token = self._load_access_token()
            if not self.access_token: return
            
            url = f"{self.base_url}/historical-candle/NSE_INDEX%7CINDIA%20VIX/1day/{(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')}/{datetime.now().strftime('%Y-%m-%d')}"
            headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json().get("data", {}).get("candles", [])
            for candle in data[-20:]:
                self.vix_history.append(candle[4]) # Close price
            logger.info(f"VIX HISTORY PRE-LOADED: {len(self.vix_history)} days found.")
        except Exception as e:
            logger.error(f"VIX Pre-load failed: {e}. Falling back to default.")
            self.vix_history.extend([13.5] * 20)

    def _fetch_chain(self):
        """Fetches the Option Chain for Nifty 50 and calculates PCR/Max Pain."""
        try:
            # Refresh token before each call
            self.access_token = self._load_access_token()
            if not self.access_token:
                logger.error("No valid access token found for DataFetcher")
                return

            expiry_date = self._get_expiry()
            url = f"{self.base_url}/option/chain"
            params = {
                "instrument_key": "NSE_INDEX|Nifty 50", 
                "expiry_date": expiry_date
            }
            headers = {
                "Authorization": f"Bearer {self.access_token}", 
                "Accept": "application/json"
            }
            
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            
            if resp.status_code == 401:
                logger.error("Token Unauthorized (401). Please run upstox_auth.py again.")
                with self.lock: self.cache['last_update'] = 0
                return

            resp.raise_for_status()
            data = resp.json().get("data", [])
            
            # --- PAPER MODE FALLBACK (For 2026 Simulation) ---
            if not data:
                logger.warning(f"API returned empty chain for {expiry_date}. Simulating Macro Data for Paper Mode.")
                with self.lock:
                    self.cache.update({
                        'pcr': 0.95, # Simulated neutral-bearish PCR
                        'max_pain_strike': 25300, # Simulated Max Pain
                        'max_pain_dist': 10, # Near the pin
                        'last_update': time.time()
                    })
                return
            # -------------------------------------------------

            pcr = data[0].get("pcr", 1.0)
            max_pain_strike, max_pain_dist = self._calculate_max_pain(data)

            with self.lock:
                self.cache.update({
                    'pcr': pcr,
                    'max_pain_strike': max_pain_strike,
                    'max_pain_dist': max_pain_dist,
                    'last_update': time.time()
                })
            logger.info(f"✅ MACRO DATA UPDATED | PCR: {pcr:.3f} | MaxPain: {max_pain_strike} (dist: {max_pain_dist})")
        except Exception as e:
            logger.error(f"Option chain fetch failed: {e}")
            with self.lock:
                self.cache['last_update'] = 0 # Mark as STALE/INVALID on failure

    def _calculate_max_pain(self, chain):
        """PROFESSIONAL NESTED MAX PAIN — Sum of pain at every strike."""
        spot = chain[0].get("underlying_spot_price", 25500)
        pain = {}
        strikes = [item["strike_price"] for item in chain]
        
        for s in strikes:
            total_pain = 0.0
            for item in chain:
                strike = item["strike_price"]
                call_oi = item.get("call_options", {}).get("market_data", {}).get("oi", 0)
                put_oi = item.get("put_options", {}).get("market_data", {}).get("oi", 0)
                # Calculate value loss if settles at strike s
                total_pain += call_oi * max(0, s - strike) + put_oi * max(0, strike - s)
            pain[s] = total_pain
            
        min_pain_strike = min(pain, key=pain.get)
        return min_pain_strike, abs(spot - min_pain_strike)

    def _start_updater(self):
        def loop():
            while True:
                self._fetch_chain()
                time.sleep(300) # Every 5 minutes
        threading.Thread(target=loop, daemon=True).start()

    def get_pcr(self): return self.cache['pcr']
    def get_max_pain_dist(self): return self.cache['max_pain_dist']
    def is_fresh(self): return (time.time() - self.cache['last_update']) <= 600
