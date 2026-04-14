import requests
import threading
import time
import logging
import csv
import os
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
        
        # Sensex & Nifty Configuration
        self.trading_index = config.get("trading_index", "NIFTY")
        if self.trading_index == "SENSEX":
            self.instrument_key = "BSE_INDEX|SENSEX"
            self.strike_step = 100
        else:
            self.instrument_key = "NSE_INDEX|Nifty 50"
            self.strike_step = 50

        self.cache = {
            'pcr': 1.05,
            'max_pain_strike': 25500,
            'max_pain_dist': 0,
            'support_strike': 0,
            'resistance_strike': 0,
            'change_in_ce_oi': 0,
            'change_in_pe_oi': 0,
            'atm_iv': 0,
            'iv_history': [],
            'greeks_map': {},
            'last_update': 0
        }
        self.vix_history = deque(maxlen=20)
        self.spot_history = deque(maxlen=60)  # 60 readings = ~60 minutes of 1-min spot prices
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
        """Fetches the official nearest valid expiry date dynamically from Upstox, adapting to shifted holidays instantly."""
        url = f"{self.base_url}/option/contract"
        params = {"instrument_key": self.instrument_key}
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                expiries = {item.get("expiry") for item in data if item.get("expiry")}
                
                if expiries:
                    today = datetime.now().date()
                    valid_expiries = []
                    for e in expiries:
                        dt = datetime.strptime(e, "%Y-%m-%d").date()
                        # Valid if future, or if today and market is still open (< 15:30)
                        if dt > today or (dt == today and datetime.now().hour < 16):
                            valid_expiries.append(dt)
                            
                    if valid_expiries:
                        return min(valid_expiries).strftime("%Y-%m-%d")
        except Exception as e:
            logger.error(f"Failed to fetch official expiry from Upstox: {e}")

        # Math Fallback (If API Call Fails)
        today = datetime.now().date()
        # Nifty = Thursday (3), Sensex = Friday (4)
        target_day = 4 if self.trading_index == "SENSEX" else 3
        days_ahead = (target_day - today.weekday()) % 7
        
        # If today is expiry day but market is closed, look at next week
        if days_ahead == 0 and datetime.now().hour >= 16:
            days_ahead = 7
            
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
                "instrument_key": self.instrument_key, 
                "expiry_date": expiry_date
            }
            headers = {
                "Authorization": f"Bearer {self.access_token}", 
                "Accept": "application/json"
            }
            
            # Exponential Backoff Retry Loop
            max_retries = 3
            for attempt in range(max_retries):
                resp = requests.get(url, params=params, headers=headers, timeout=10)
                if resp.status_code == 200:
                    break
                if resp.status_code == 401:
                    logger.error("Token Unauthorized (401). Please run upstox_auth.py again.")
                    with self.lock: self.cache['last_update'] = 0
                    return
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt) # Backoff: 1s, 2s
            
            resp.raise_for_status()
            data = resp.json().get("data", [])
            
            # --- PAPER MODE FALLBACK (For 2026 Simulation) ---
            if not data:
                logger.warning(f"API returned empty chain for {expiry_date}. Simulating Macro Data for Paper Mode.")
                with self.lock:
                    self.cache.update({
                        'pcr': 0.95,
                        'focus_pcr': 1.0,
                        'oi_pattern': {'ce_oi_change': 0, 'pe_oi_change': 0},
                        'max_pain_strike': 25300,
                        'max_pain_dist': 10,
                        'spot': self.cache.get('spot', 0),
                        'last_update': time.time()
                    })
                return
            # -------------------------------------------------

            pcr = data[0].get("pcr", 1.0)
            spot = data[0].get("underlying_spot_price", 22500)

            # Track spot price history for Sustain Engine
            self.spot_history.append({'time': datetime.now(), 'spot': spot})

            max_pain_strike, max_pain_dist = self._calculate_max_pain(data)
            support_strike, resistance_strike = self._calculate_support_resistance(data, spot)
            change_ce_oi, change_pe_oi, atm_iv = self._extract_oi_and_iv(data, spot)

            # Calculate Focus Zone metrics (7-strike localized PCR + OI patterns)
            focus_pcr, oi_pattern = self._calculate_focus_zone_metrics(data, spot)

            # Track IV history for percentile calculation
            iv_history = self.cache.get('iv_history', [])
            if atm_iv > 0:
                iv_history.append(atm_iv)
                if len(iv_history) > 30:
                    iv_history = iv_history[-30:]  # Keep last 30 readings

            # Extract full greeks map for Delta > 0.40 filtering + Live LTP
            greeks_map = {}
            ltp_map = {}
            token_map = {}
            for item in data:
                s = item["strike_price"]
                ce_g = item.get("call_options", {}).get("option_greeks", {})
                ce_m = item.get("call_options", {}).get("market_data", {})
                ce_tk = item.get("call_options", {}).get("instrument_key", "")
                
                pe_g = item.get("put_options", {}).get("option_greeks", {})
                pe_m = item.get("put_options", {}).get("market_data", {})
                pe_tk = item.get("put_options", {}).get("instrument_key", "")
                
                greeks_map[f"{s}_CE"] = {'delta': ce_g.get('delta', 0), 'theta': ce_g.get('theta', 0), 'gamma': ce_g.get('gamma', 0)}
                greeks_map[f"{s}_PE"] = {'delta': abs(pe_g.get('delta', 0)), 'theta': pe_g.get('theta', 0), 'gamma': pe_g.get('gamma', 0)}
                
                ltp_map[f"{s}_CE"] = ce_m.get('ltp', 0)
                ltp_map[f"{s}_PE"] = pe_m.get('ltp', 0)
                
                token_map[f"{s}_CE"] = ce_tk
                token_map[f"{s}_PE"] = pe_tk

            with self.lock:
                self.cache.update({
                    'spot': spot,
                    'pcr': pcr,
                    'focus_pcr': focus_pcr,
                    'oi_pattern': oi_pattern,
                    'max_pain_strike': max_pain_strike,
                    'max_pain_dist': max_pain_dist,
                    'support_strike': support_strike,
                    'resistance_strike': resistance_strike,
                    'change_in_ce_oi': change_ce_oi,
                    'change_in_pe_oi': change_pe_oi,
                    'atm_iv': atm_iv,
                    'iv_history': iv_history,
                    'greeks_map': greeks_map,
                    'ltp_map': ltp_map,
                    'token_map': token_map,
                    'last_update': time.time()
                })
            logger.info(
                f"[OK] MACRO | PCR:{pcr:.2f} FocusPCR:{focus_pcr:.2f} | S:{support_strike} R:{resistance_strike} "
                f"| MaxPain:{max_pain_strike} | CE-OI-Chg:{change_ce_oi} PE-OI-Chg:{change_pe_oi} "
                f"| FZ-CE-Chg:{oi_pattern['ce_oi_change']} FZ-PE-Chg:{oi_pattern['pe_oi_change']} | IV:{atm_iv:.1f}"
            )
            
            # --- NEW: Log 7-strike Focus Zone Async ---
            self._log_focus_zone(spot, data)
            
        except Exception as e:
            logger.error(f"Option chain fetch failed: {e}")
            with self.lock:
                self.cache['last_update'] = 0 # Mark as STALE/INVALID on failure

    def _log_focus_zone(self, spot, chain):
        """Asynchronously save 7-strike focus zone data to CSV."""
        try:
            strikes = [item["strike_price"] for item in chain]
            if not strikes: return
            
            atm = min(strikes, key=lambda x: abs(x - spot))
            focus_zone = [atm + (i * self.strike_step) for i in range(-3, 4)]
            
            today = datetime.now().strftime("%Y-%m-%d")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            csv_path = f"logs/focus_zone_{today}.csv"
            
            rows = []
            for item in chain:
                strike = item["strike_price"]
                if strike in focus_zone:
                    ce = item.get("call_options", {})
                    pe = item.get("put_options", {})
                    ce_m = ce.get("market_data", {})
                    pe_m = pe.get("market_data", {})
                    ce_g = ce.get("option_greeks", {})
                    pe_g = pe.get("option_greeks", {})
                    
                    # Position relative to ATM
                    if strike == atm: pos_label = "ATM"
                    elif strike > atm: pos_label = f"+{(strike - atm) // self.strike_step}"
                    else: pos_label = str((strike - atm) // self.strike_step)

                    rows.append({
                        "timestamp": timestamp,
                        "spot": round(spot, 2),
                        "strike": strike,
                        "pos": pos_label,
                        "ce_ltp": ce_m.get("ltp", 0),
                        "ce_oi": ce_m.get("oi", 0),
                        "ce_delta": ce_g.get("delta", 0),
                        "pe_ltp": pe_m.get("ltp", 0),
                        "pe_oi": pe_m.get("oi", 0),
                        "pe_delta": abs(pe_g.get("delta", 0)) # Ensure positive delta notation for comparison
                    })
                    
            if not rows: return
            
            def write_csv():
                try:
                    file_exists_now = os.path.isfile(csv_path)
                    with open(csv_path, 'a', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                        if not file_exists_now:
                            writer.writeheader()
                        writer.writerows(rows)
                except Exception as ex:
                    logger.error(f"Focus zone CSV write failed: {ex}")
            
            # Fire and forget I/O thread
            threading.Thread(target=write_csv, daemon=True).start()
            
        except Exception as e:
            logger.error(f"Failed to process focus zone logging: {e}")

    def _calculate_focus_zone_metrics(self, chain, spot):
        """
        Calculate PCR and OI change patterns from the 7-strike Focus Zone ONLY.
        Focus Zone = ATM ± 3 strikes (7 total).
        
        This gives a hyper-localized view of institutional money flow,
        filtering out the noise from deep ITM/OTM strikes.
        """
        strikes = [item["strike_price"] for item in chain]
        if not strikes:
            return 1.0, {'ce_oi_change': 0, 'pe_oi_change': 0}

        atm = min(strikes, key=lambda x: abs(x - spot))
        focus_zone = [atm + (i * self.strike_step) for i in range(-3, 4)]

        total_ce_oi = 0
        total_pe_oi = 0
        focus_ce_oi_change = 0
        focus_pe_oi_change = 0

        for item in chain:
            strike = item["strike_price"]
            if strike in focus_zone:
                ce_data = item.get("call_options", {}).get("market_data", {})
                pe_data = item.get("put_options", {}).get("market_data", {})

                ce_oi = ce_data.get("oi", 0)
                pe_oi = pe_data.get("oi", 0)
                ce_prev_oi = ce_data.get("prev_oi", ce_oi)
                pe_prev_oi = pe_data.get("prev_oi", pe_oi)

                total_ce_oi += ce_oi
                total_pe_oi += pe_oi
                focus_ce_oi_change += (ce_oi - ce_prev_oi)
                focus_pe_oi_change += (pe_oi - pe_prev_oi)

        # Focus Zone PCR = Total PE OI / Total CE OI
        focus_pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

        oi_pattern = {
            'ce_oi_change': focus_ce_oi_change,
            'pe_oi_change': focus_pe_oi_change,
        }

        return focus_pcr, oi_pattern

    def _calculate_max_pain(self, chain):
        """OPTIMIZED MAX PAIN: O(n) Limited strictly to Focus Zone around ATM."""
        spot = chain[0].get("underlying_spot_price", 22500)
        strikes = [item["strike_price"] for item in chain]
        if not strikes: return 0, 0
        
        atm = min(strikes, key=lambda x: abs(x - spot))
        # Limit the painful loop strictly to 10 strikes around ATM
        focus_strikes = [atm + (i * self.strike_step) for i in range(-5, 6)]
        
        pain = {}
        for s in focus_strikes:
            if s not in strikes: continue
            total_pain = 0.0
            for item in chain: # Calculate only the intrinsic value against focus options
                strike = item["strike_price"]
                call_oi = item.get("call_options", {}).get("market_data", {}).get("oi", 0)
                put_oi = item.get("put_options", {}).get("market_data", {}).get("oi", 0)
                if call_oi > 0 and strike < s:
                    total_pain += call_oi * (s - strike)
                if put_oi > 0 and strike > s:
                    total_pain += put_oi * (strike - s)
            pain[s] = total_pain
            
        min_pain_strike = min(pain, key=pain.get) if pain else atm
        return min_pain_strike, abs(spot - min_pain_strike)

    def _calculate_support_resistance(self, chain, spot):
        """CLUSTER-BASED S/R: Summing OI in 3-strike blocks instead of single nodes."""
        strikes = [item["strike_price"] for item in chain]
        if not strikes: return 0, 0
        
        atm = min(strikes, key=lambda x: abs(x - spot))
        focus_zone = [atm + (i * self.strike_step) for i in range(-5, 6)]
        
        # Build dictionary of OI
        ce_map = {item["strike_price"]: item.get("call_options", {}).get("market_data", {}).get("oi", 0) for item in chain}
        pe_map = {item["strike_price"]: item.get("put_options", {}).get("market_data", {}).get("oi", 0) for item in chain}

        max_ce_cluster, max_pe_cluster = 0, 0
        res_strike, sup_strike = atm, atm
        
        for s in focus_zone:
            # 3-Strike Band (Current + 1 above + 1 below)
            band_ce_oi = ce_map.get(s, 0) + ce_map.get(s + self.strike_step, 0) + ce_map.get(s - self.strike_step, 0)
            band_pe_oi = pe_map.get(s, 0) + pe_map.get(s + self.strike_step, 0) + pe_map.get(s - self.strike_step, 0)
            
            # Resistance is Call OI above Spot
            if s >= atm and band_ce_oi > max_ce_cluster:
                max_ce_cluster = band_ce_oi
                res_strike = s
            # Support is Put OI below Spot
            if s <= atm and band_pe_oi > max_pe_cluster:
                max_pe_cluster = band_pe_oi
                sup_strike = s

        return sup_strike, res_strike

    def _extract_oi_and_iv(self, chain, spot):
        """Extract Change-in-OI totals and ATM IV from the chain."""
        total_ce_oi_change = 0
        total_pe_oi_change = 0
        atm_iv = 0
        atm_strike = min([item['strike_price'] for item in chain], key=lambda x: abs(x - spot))

        for item in chain:
            ce_data = item.get('call_options', {}).get('market_data', {})
            pe_data = item.get('put_options', {}).get('market_data', {})
            total_ce_oi_change += ce_data.get('oi', 0) - ce_data.get('prev_oi', ce_data.get('oi', 0))
            total_pe_oi_change += pe_data.get('oi', 0) - pe_data.get('prev_oi', pe_data.get('oi', 0))

            # Get ATM IV
            if item['strike_price'] == atm_strike:
                ce_greeks = item.get('call_options', {}).get('option_greeks', {})
                pe_greeks = item.get('put_options', {}).get('option_greeks', {})
                ce_iv = ce_greeks.get('iv', 0)
                pe_iv = pe_greeks.get('iv', 0)
                atm_iv = (ce_iv + pe_iv) / 2 if (ce_iv and pe_iv) else max(ce_iv, pe_iv)

        return total_ce_oi_change, total_pe_oi_change, atm_iv

    def _start_updater(self):
        def loop():
            while True:
                self._fetch_chain()
                time.sleep(60) # Every 1 minute for Live Tracking
        threading.Thread(target=loop, daemon=True).start()

    def get_spot(self): return self.cache.get('spot', 0)
    def get_pcr(self): return self.cache.get('pcr', 1.0)
    def get_max_pain_strike(self): return self.cache['max_pain_strike']
    def get_max_pain_dist(self): return self.cache['max_pain_dist']
    def get_expiry_date(self): return self._get_expiry()
    def get_support(self): return self.cache.get('support_strike', 0)
    def get_resistance(self): return self.cache.get('resistance_strike', 0)
    def get_change_in_ce_oi(self): return self.cache.get('change_in_ce_oi', 0)
    def get_change_in_pe_oi(self): return self.cache.get('change_in_pe_oi', 0)
    def get_atm_iv(self): return self.cache.get('atm_iv', 0)
    def get_iv_history(self): return self.cache.get('iv_history', [])
    def get_india_vix(self): return self.vix_history[-1] if self.vix_history else 13.5
    def get_strike_greeks(self, strike, opt_type):
        mapping = self.cache.get('greeks_map', {})
        return mapping.get(f"{float(strike)}_{opt_type}", mapping.get(f"{int(strike)}_{opt_type}", {'delta': 0, 'theta': 0, 'gamma': 0}))
        
    def get_option_ltp(self, strike, opt_type):
        mapping = self.cache.get('ltp_map', {})
        return mapping.get(f"{float(strike)}_{opt_type}", mapping.get(f"{int(strike)}_{opt_type}", 0))
        
    def get_instrument_token(self, strike, opt_type):
        mapping = self.cache.get('token_map', {})
        return mapping.get(f"{float(strike)}_{opt_type}", mapping.get(f"{int(strike)}_{opt_type}", ""))
    def is_fresh(self): return (time.time() - self.cache.get('last_update', 0)) <= 120
    def get_focus_pcr(self): return self.cache.get('focus_pcr', 1.0)
    def get_oi_pattern(self): return self.cache.get('oi_pattern', {'ce_oi_change': 0, 'pe_oi_change': 0})
    def get_spot_history(self): return list(self.spot_history)

    def get_live_quote(self, instrument_key):
        """Turbo Query: 1 Call per 3 seconds is well within the 10/sec API limit."""
        url = f"{self.base_url}/market-quote/quotes"
        params = {"instrument_key": instrument_key}
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                for k, v in data.items():
                    return float(v.get("last_price", 0))
        except:
            pass
        return 0
