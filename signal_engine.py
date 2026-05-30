"""
signal_engine.py
================
Sniper Entry Engine v3.0 — Institutional-Grade Options Entry Logic.

Three-gate entry system:
  Gate 1: Spot Sustain Check — Price must hold near OI wall for 3 consecutive 5m candles.
  Gate 2: Focus Zone PCR — Localized 7-strike PCR must confirm directional bias.
  Gate 3: OI Build-Up Confirmation — Option writers must be actively defending the wall.
"""

import json
from collections import deque
from datetime import datetime, date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Load options.json parameters
# ---------------------------------------------------------------------------
def load_options_config():
    config_path = Path(__file__).parent / "Options.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            return json.load(f)
    return {}

OPTIONS_CONFIG = load_options_config()
PARAMS = OPTIONS_CONFIG.get("configurableParameters", {})

# Extract tunable thresholds from options.json
SL_PCT             = PARAMS.get("premiumStopLossPercent", 30) / 100.0
TARGET_PCT         = PARAMS.get("profitTargetPercent", 50) / 100.0
RISK_PER_TRADE_PCT = PARAMS.get("riskPercent", 1.0) / 100.0
DTE_EXTREME        = PARAMS.get("dteThresholdExtreme", 3)
DTE_HIGH           = PARAMS.get("dteThresholdHigh", 7)

# Proximity: how close spot must be to S/R wall (as decimal fraction)
PROXIMITY_PCT = PARAMS.get("wallProximityTolerancePercent", 0.25) / 100.0

# Sustain parameters
SUSTAIN_TICKS = 1       # Reduced from 3 to 1 to catch fast touches on trend days
SUSTAIN_INTERVAL = 5    # Minutes between each candle close check

# Focus Zone PCR thresholds
FOCUS_PCR_BULLISH_THRESHOLD = 1.1    # PCR above this = bullish (heavy Put writing)
FOCUS_PCR_BEARISH_THRESHOLD = 0.85   # PCR below this = bearish (heavy Call writing)

# Gate 2: PCR must lie inside an *entry band* (Goldilocks zone).
# Below the lower bound  = flow not strong enough → block.
# Above the upper bound  = move is over-extended; entering now = chasing
#                          → block (most reversals happen at PCR extremes).
# Inside the band        = early-enough confirmation → fire.
FOCUS_PCR_CE_ENTRY_LOW  = 0.85   # Widened from 1.00
FOCUS_PCR_CE_ENTRY_HIGH = 1.60   # Widened from 1.30

FOCUS_PCR_PE_ENTRY_LOW  = 0.40   # Widened from 0.50
FOCUS_PCR_PE_ENTRY_HIGH = 1.15   # Widened from 0.95

# Gate 2 vigilance sub-gates — added 2026-05-09 after analysing 2026-05-08
# loss day. Bot took CE at PCR=1.05 while PCR was collapsing (1.15→0.85)
# and CE-OI was building (call writers stacking resistance). Snapshot gates
# missed both. These two checks add TREND awareness on top of the band.
PCR_SLOPE_LOOKBACK_MINUTES = PARAMS.get("pcrSlopeLookbackMinutes", 30)
PCR_SLOPE_MAX_DROP         = PARAMS.get("pcrSlopeMaxDrop", 0.05)
OI_DELTA_RATIO_MAX         = PARAMS.get("oiDeltaRatioMax", 1.5)

# PR 4: pre-Gate-1 context filters.
# VWAP filter — gate CE entries below VWAP, PE entries above VWAP.
# Cheap structural sanity: never long below the average price, never
# short above it.
VWAP_FILTER_ENABLED  = bool(PARAMS.get("vwapFilterEnabled", True))
# Range/chop detector — if 60-min spot range is < this fraction of
# spot, skip directional entries (no edge in tight chop).
CHOP_DETECTOR_ENABLED  = bool(PARAMS.get("chopDetectorEnabled", True))
CHOP_RANGE_LOOKBACK_MIN = int(PARAMS.get("chopRangeLookbackMinutes", 60))
CHOP_RANGE_MAX_PCT      = float(PARAMS.get("chopRangeMaxPct", 0.003))  # Reduced to 0.3%

# ---------------------------------------------------------------------------
# Gate 1: Spot Sustain Check
# ---------------------------------------------------------------------------
def check_sustain(spot_history, level, proximity_pct=None,
                  required_ticks=SUSTAIN_TICKS, tick_interval=SUSTAIN_INTERVAL):
    """
    Verify that the spot price has sustained near a support/resistance level
    for the required number of consecutive 5-minute candle closes.

    This prevents fakeout entries where price briefly spikes to a level
    and immediately reverses.

    Args:
        spot_history: list of {'time': datetime, 'spot': float} (polled every ~60s)
        level: the support or resistance strike price to check against
        proximity_pct: how close the price must be (decimal, e.g. 0.0015 = 0.15%)
        required_ticks: number of consecutive candle closes needed in the zone
        tick_interval: minutes between each candle close check

    Returns:
        bool: True if price has sustained in the zone for required ticks
    """
    if proximity_pct is None:
        proximity_pct = PROXIMITY_PCT

    # Need at least (required_ticks - 1) * interval + 1 readings
    # For 3 ticks at 5m intervals with 1m polling: indices -1, -6, -11 → need 11 readings
    min_readings = (required_ticks - 1) * tick_interval + 1
    if len(spot_history) < min_readings:
        return False

    history = list(spot_history)

    for i in range(required_ticks):
        # Look back at 5-minute intervals: -1, -6, -11
        idx = -(1 + i * tick_interval)
        if abs(idx) > len(history):
            return False

        reading = history[idx]
        spot = reading['spot']
        dist = abs(spot - level) / spot

        if dist > proximity_pct:
            return False

    return True


# ---------------------------------------------------------------------------
# Gate 2: Focus Zone PCR Interpretation
# ---------------------------------------------------------------------------
def interpret_focus_pcr(focus_pcr):
    """
    Interpret the 7-strike Focus Zone PCR.

    Unlike the full-chain PCR which is diluted by deep OTM noise,
    this PCR reflects active institutional positioning near ATM.

    Focus PCR = Total PE OI / Total CE OI (in the 7-strike zone)

    Returns: 'bullish', 'bearish', or 'neutral'
    """
    if focus_pcr >= FOCUS_PCR_BULLISH_THRESHOLD:
        return "bullish"      # Heavy Put OI near ATM → writers defending support
    elif focus_pcr <= FOCUS_PCR_BEARISH_THRESHOLD:
        return "bearish"      # Heavy Call OI near ATM → writers defending resistance
    return "neutral"          # Contested zone, no clear conviction


# ---------------------------------------------------------------------------
# Gate 3: OI Build-Up Confirmation
# ---------------------------------------------------------------------------
def check_oi_confirmation(oi_pattern, direction):
    """
    Verify that option writers are actively building positions that DEFEND
    the support/resistance wall, not unwinding/exiting.

    For CE Entry (Support Bounce):
      → Put writers should be ADDING OI (pe_oi_change > 0)
      → They are confident support will hold

    For PE Entry (Resistance Rejection):
      → Call writers should be ADDING OI (ce_oi_change > 0)
      → They are confident resistance will hold

    Args:
        oi_pattern: dict with 'ce_oi_change' and 'pe_oi_change' (from focus zone)
        direction: 'CE' or 'PE'

    Returns:
        (bool, str): (confirmed, reason)
    """
    ce_change = oi_pattern.get('ce_oi_change', 0)
    pe_change = oi_pattern.get('pe_oi_change', 0)

    if direction == "CE":
        if pe_change > 0:
            return True, f"PUT OI Build-Up (+{pe_change}) → Writers defending support"
        elif pe_change < 0:
            return False, f"PUT OI Unwinding ({pe_change}) → Support weakening"
        else:
            return False, "PUT OI unchanged → No conviction"

    elif direction == "PE":
        if ce_change > 0:
            return True, f"CALL OI Build-Up (+{ce_change}) → Writers defending resistance"
        elif ce_change < 0:
            return False, f"CALL OI Unwinding ({ce_change}) → Resistance weakening"
        else:
            return False, "CALL OI unchanged → No conviction"

    return False, "Unknown direction"


# ---------------------------------------------------------------------------
# DTE Risk Classification
# ---------------------------------------------------------------------------
def classify_dte_risk(expiry_date_str, current_date_str=None):
    expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    if current_date_str:
        current_date = datetime.strptime(current_date_str, "%Y-%m-%d").date()
    else:
        current_date = date.today()

    dte = (expiry - current_date).days
    dte = max(0, dte)

    if dte <= DTE_EXTREME:
        return "EXTREME", dte
    elif dte <= DTE_HIGH:
        return "HIGH", dte
    return "MODERATE", dte


# ---------------------------------------------------------------------------
# Position Sizing
# ---------------------------------------------------------------------------
def calculate_position_size(capital, entry_premium, sl_premium, lot_size=65, is_expiry_day=False):
    risk_per_unit = entry_premium - sl_premium
    if risk_per_unit <= 0: return lot_size

    max_risk_amount = capital * RISK_PER_TRADE_PCT
    max_units = int(max_risk_amount / risk_per_unit)
    lots = max(1, max_units // lot_size)
    qty = lots * lot_size

    if is_expiry_day:
        qty = max(lot_size, int(qty * 0.50))

    return qty


from vigilance.vic_engine import VICEngine
from vigilance.zone_memory import ZoneMemoryEngine
from vigilance.market_structure import MarketStructureEngine
from vigilance.pattern_engine import PatternEngine
from vigilance.candle_engine import CandleEngine
from vigilance.volume_engine import VolumeEngine

class ThreeTimeframeTrendTracker:
    def __init__(self, ema_window=20):
        self.ema_window = ema_window
        self.m15_candles = deque(maxlen=200)
        self.h1_candles = deque(maxlen=200)
        self.h4_candles = deque(maxlen=200)
        
        self.m15_ema = deque(maxlen=200)
        self.h1_ema = deque(maxlen=200)
        self.h4_ema = deque(maxlen=200)
        
        self.active_15m = []
        self.active_1h = []
        self.active_4h = []
        self.is_bootstrapped = False
        
    def bootstrap(self, fetcher):
        print("[*] Bootstrapping 3TF Momentum EMAs...")
        try:
            access_token = fetcher._load_access_token()
            if not access_token:
                raise Exception("Token expired or missing")
                
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date_15m = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
            url_15m = f"{fetcher.base_url}/historical-candle/{fetcher.instrument_key}/15minute/{start_date_15m}/{end_date}"
            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            resp_15m = fetcher.session.get(url_15m, headers=headers, timeout=10)
            
            if resp_15m.status_code == 200:
                candles_15m = resp_15m.json().get("data", {}).get("candles", [])
                candles_15m = sorted(candles_15m, key=lambda x: x[0])
                for c in candles_15m:
                    self.m15_candles.append(float(c[4]))
                print(f"[3TF] Loaded {len(self.m15_candles)} historical 15M candles from Upstox.")
                
            start_date_1h = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
            url_1h = f"{fetcher.base_url}/historical-candle/{fetcher.instrument_key}/1hour/{start_date_1h}/{end_date}"
            resp_1h = fetcher.session.get(url_1h, headers=headers, timeout=10)
            
            if resp_1h.status_code == 200:
                candles_1h = resp_1h.json().get("data", {}).get("candles", [])
                candles_1h = sorted(candles_1h, key=lambda x: x[0])
                for c in candles_1h:
                    self.h1_candles.append(float(c[4]))
                print(f"[3TF] Loaded {len(self.h1_candles)} historical 1H candles from Upstox.")
                
                chunk = []
                for close_p in self.h1_candles:
                    chunk.append(close_p)
                    if len(chunk) == 4:
                        self.h4_candles.append(chunk[-1])
                        chunk = []
                print(f"[3TF] Constructed {len(self.h4_candles)} historical 4H candles.")
                
            self._recalculate_all_emas()
            self.is_bootstrapped = True
            print("[3TF] Bootstrapping completed successfully via Upstox API.")
            
        except Exception as e:
            print(f"[3TF WARNING] Upstox API bootstrap failed: {e}. Attempting local backtesting file...")
            self._bootstrap_from_local_file()
            
    def _bootstrap_from_local_file(self):
        try:
            import pandas as pd
            csv_path = Path("C:/Users/shaik/OneDrive/Desktop/New folder (2)/nifty-sniper-bot/data/NIFTY50_INDEX_1minute.csv")
            if csv_path.exists():
                print(f"[3TF] Reading local CSV: {csv_path.name}")
                df = pd.read_csv(csv_path)
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp").sort_index()
                
                last_dt = df.index.max()
                sub = df.loc[last_dt - timedelta(days=60):]
                
                df_15m = sub["close"].resample("15min").last().dropna()
                df_1h = sub["close"].resample("1h").last().dropna()
                df_4h = sub["close"].resample("4h").last().dropna()
                
                for close_p in df_15m.values:
                    self.m15_candles.append(float(close_p))
                for close_p in df_1h.values:
                    self.h1_candles.append(float(close_p))
                for close_p in df_4h.values:
                    self.h4_candles.append(float(close_p))
                    
                self._recalculate_all_emas()
                self.is_bootstrapped = True
                print(f"[3TF] Bootstrapping completed successfully via local CSV. Loaded {len(self.m15_candles)} 15M, {len(self.h1_candles)} 1H, {len(self.h4_candles)} 4H.")
            else:
                raise Exception("Local CSV file not found")
        except Exception as ex:
            print(f"[3TF WARNING] Local CSV bootstrap failed: {ex}. Falling back to on-the-fly seed.")
            self.is_bootstrapped = False

    def _recalculate_all_emas(self):
        self.m15_ema.clear()
        val = 0.0
        k = 2 / (self.ema_window + 1)
        for c in self.m15_candles:
            val = c if val == 0.0 else c * k + val * (1 - k)
            self.m15_ema.append(val)
            
        self.h1_ema.clear()
        val = 0.0
        for c in self.h1_candles:
            val = c if val == 0.0 else c * k + val * (1 - k)
            self.h1_ema.append(val)
            
        self.h4_ema.clear()
        val = 0.0
        for c in self.h4_candles:
            val = c if val == 0.0 else c * k + val * (1 - k)
            self.h4_ema.append(val)

    def update(self, ts, spot_price):
        if not self.is_bootstrapped:
            self.m15_candles.append(spot_price)
            self.h1_candles.append(spot_price)
            self.h4_candles.append(spot_price)
            self._recalculate_all_emas()
            self.is_bootstrapped = True
            print(f"[3TF INFO] On-the-fly bootstrap complete. Seed price: {spot_price}")
            return
            
        self.active_15m.append((ts, spot_price))
        if ts.minute in [0, 15, 30, 45]:
            close_p = self.active_15m[-1][1]
            self.m15_candles.append(close_p)
            self.active_15m = []
            k = 2 / (self.ema_window + 1)
            prev_ema = self.m15_ema[-1] if self.m15_ema else close_p
            self.m15_ema.append(close_p * k + prev_ema * (1 - k))
            
        self.active_1h.append((ts, spot_price))
        if ts.minute == 0:
            close_p = self.active_1h[-1][1]
            self.h1_candles.append(close_p)
            self.active_1h = []
            k = 2 / (self.ema_window + 1)
            prev_ema = self.h1_ema[-1] if self.h1_ema else close_p
            self.h1_ema.append(close_p * k + prev_ema * (1 - k))
            
            self.active_4h.append(close_p)
            if len(self.active_4h) >= 4:
                close_4h = self.active_4h[-1]
                self.h4_candles.append(close_4h)
                self.active_4h = []
                prev_ema_4h = self.h4_ema[-1] if self.h4_ema else close_4h
                self.h4_ema.append(close_4h * k + prev_ema_4h * (1 - k))

    def get_trends(self, current_spot):
        if not self.is_bootstrapped:
            return "UP", "UP", "UP"
        h4 = "UP" if self.h4_ema and current_spot > self.h4_ema[-1] else "DOWN"
        h1 = "UP" if self.h1_ema and current_spot > self.h1_ema[-1] else "DOWN"
        m15 = "UP" if self.m15_ema and current_spot > self.m15_ema[-1] else "DOWN"
        return h4, h1, m15

# ---------------------------------------------------------------------------
# The Sniper Signal Engine v3.5 (Master Confluence Integrated)
# ---------------------------------------------------------------------------
class SignalEngine:
    def __init__(self):
        # Rolling history of (timestamp, focus_pcr, total_ce_oi, total_pe_oi)
        self._history = deque(maxlen=180)
        # Pillar 1 & 2: Institutional Velocity Engine
        self.vic = VICEngine(lookback_minutes=15)
        # Pillar 3: History & Role Reversal Engine
        self.memory = ZoneMemoryEngine()
        # Topic 2: Market Structure (HH/HL) Engine
        self.structure = MarketStructureEngine(window=3)
        # Topic 3: Pattern Engine
        self.patterns = PatternEngine()
        # Topic 5: Candlestick Engine
        self.candles = CandleEngine()
        # Topic 6: Volume Engine
        self.volume = VolumeEngine(window=20)
        # 3TF Trend Tracker
        self.tracker_3tf = ThreeTimeframeTrendTracker()

    @staticmethod
    def _compute_session_vwap(spot_history):
        """Cheap VWAP approximation. Bot has no per-tick volume data, so we
        use simple average of spot ticks — a reasonable proxy of session
        average price for filter-quality decisions."""
        if not spot_history:
            return None
        spots = [float(s.get('spot', 0) or 0) for s in spot_history if isinstance(s, dict)]
        spots = [s for s in spots if s > 0]
        if len(spots) < 5:
            return None
        return sum(spots) / len(spots)

    @staticmethod
    def _compute_range_pct(spot_history, lookback_minutes):
        """Return (high - low) / spot_now over last `lookback_minutes` ticks.
        Returns None if history is too short for a meaningful read."""
        if not spot_history:
            return None
        spots = [float(s.get('spot', 0) or 0) for s in spot_history if isinstance(s, dict)]
        spots = [s for s in spots if s > 0]
        n = min(lookback_minutes, len(spots))
        if n < 30:  # need at least 30 samples to call it a range
            return None
        window = spots[-n:]
        return (max(window) - min(window)) / window[-1]

    def _push_history(self, now, focus_pcr, oi_pattern):
        ce_oi = oi_pattern.get('total_ce_oi', 0) if isinstance(oi_pattern, dict) else 0
        pe_oi = oi_pattern.get('total_pe_oi', 0) if isinstance(oi_pattern, dict) else 0
        self._history.append((now, float(focus_pcr or 0), float(ce_oi or 0), float(pe_oi or 0)))

    def _lookback_sample(self, now, minutes):
        """Return the oldest sample at least `minutes` ago, or None if not enough history."""
        cutoff = now - timedelta(minutes=minutes)
        for sample in self._history:
            if sample[0] <= cutoff:
                return sample
        return None

    def _check_pcr_slope(self, now, focus_pcr, direction):
        """For CE: PCR must not have dropped > MAX_DROP over lookback window.
        For PE: PCR must not have RISEN > MAX_DROP over the same window.
        Returns (passed: bool, reason: str). Default-pass during warmup."""
        ref = self._lookback_sample(now, PCR_SLOPE_LOOKBACK_MINUTES)
        if ref is None:
            return True, f"PCR slope: warmup (<{PCR_SLOPE_LOOKBACK_MINUTES}m history)"
        ref_pcr = ref[1]
        delta = focus_pcr - ref_pcr
        if direction == "CE":
            if delta < -PCR_SLOPE_MAX_DROP:
                return False, (f"PCR collapsing: {ref_pcr:.2f} → {focus_pcr:.2f} "
                               f"(Δ {delta:+.2f}) over {PCR_SLOPE_LOOKBACK_MINUTES}m. "
                               f"Bears stepping in — block CE.")
            return True, f"PCR slope OK: {ref_pcr:.2f} → {focus_pcr:.2f} (Δ {delta:+.2f})"
        else:  # PE
            if delta > PCR_SLOPE_MAX_DROP:
                return False, (f"PCR rallying: {ref_pcr:.2f} → {focus_pcr:.2f} "
                               f"(Δ {delta:+.2f}) over {PCR_SLOPE_LOOKBACK_MINUTES}m. "
                               f"Bulls stepping in — block PE.")
            return True, f"PCR slope OK: {ref_pcr:.2f} → {focus_pcr:.2f} (Δ {delta:+.2f})"

    def _check_oi_delta_ratio(self, now, oi_pattern, direction):
        """For CE: ΔCE_OI must NOT be more than RATIO × ΔPE_OI (call writing dominant = bearish).
        For PE: inverse. Default-pass during warmup or when both deltas are tiny."""
        ref = self._lookback_sample(now, PCR_SLOPE_LOOKBACK_MINUTES)
        if ref is None:
            return True, f"OI delta: warmup (<{PCR_SLOPE_LOOKBACK_MINUTES}m history)"
        _, _, ref_ce_oi, ref_pe_oi = ref
        cur_ce_oi = oi_pattern.get('total_ce_oi', 0) if isinstance(oi_pattern, dict) else 0
        cur_pe_oi = oi_pattern.get('total_pe_oi', 0) if isinstance(oi_pattern, dict) else 0
        d_ce = cur_ce_oi - ref_ce_oi
        d_pe = cur_pe_oi - ref_pe_oi
        # If neither side is meaningfully changing, no signal — pass.
        if max(abs(d_ce), abs(d_pe)) < 1000:
            return True, f"OI delta: flat (Δce={d_ce:+,.0f}, Δpe={d_pe:+,.0f})"
        if direction == "CE":
            # Bears writing calls faster than puts? Block.
            if d_ce > 0 and d_pe <= 0:
                return False, (f"Call writers stacking, put writers absent: "
                               f"Δce={d_ce:+,.0f}, Δpe={d_pe:+,.0f} — block CE.")
            if d_pe > 0 and d_ce > OI_DELTA_RATIO_MAX * d_pe:
                return False, (f"Call OI build outpacing put OI build "
                               f"({d_ce:+,.0f} > {OI_DELTA_RATIO_MAX}×{d_pe:+,.0f}) — block CE.")
            return True, f"OI delta OK: Δce={d_ce:+,.0f}, Δpe={d_pe:+,.0f}"
        else:  # PE
            if d_pe > 0 and d_ce <= 0:
                return False, (f"Put writers stacking, call writers absent: "
                               f"Δpe={d_pe:+,.0f}, Δce={d_ce:+,.0f} — block PE.")
            if d_ce > 0 and d_pe > OI_DELTA_RATIO_MAX * d_ce:
                return False, (f"Put OI build outpacing call OI build "
                               f"({d_pe:+,.0f} > {OI_DELTA_RATIO_MAX}×{d_ce:+,.0f}) — block PE.")
            return True, f"OI delta OK: Δce={d_ce:+,.0f}, Δpe={d_pe:+,.0f}"

    def _classify_fyers_quadrant(self, strike, opt_type, data_fetcher, lookback_minutes=15):
        """
        Classify option positioning into one of the 4 Fyers Quadrants.
        Returns: 'LONG_BUILDUP', 'SHORT_BUILDUP', 'SHORT_COVERING', 'LONG_UNWINDING', or 'WARMUP'
        """
        history = data_fetcher.get_option_history(strike, opt_type)
        if len(history) < 5:
            return "WARMUP"
            
        now_sample = history[-1]
        
        # Find sample closest to lookback_minutes ago
        cutoff = now_sample['time'] - timedelta(minutes=lookback_minutes)
        ref_sample = None
        for sample in reversed(history):
            if sample['time'] <= cutoff:
                ref_sample = sample
                break
        if not ref_sample:
            ref_sample = history[0]
            
        price_diff = now_sample['ltp'] - ref_sample['ltp']
        oi_diff = now_sample['oi'] - ref_sample['oi']
        
        if oi_diff > 0:
            return "LONG_BUILDUP" if price_diff >= 0 else "SHORT_BUILDUP"
        else:
            return "SHORT_COVERING" if price_diff >= 0 else "LONG_UNWINDING"

    def _check_option_volume_momentum(self, strike, opt_type, data_fetcher):
        """
        Returns True if option premium expansion is supported by healthy volume growth.
        """
        history = data_fetcher.get_option_history(strike, opt_type)
        if len(history) < 5:
            return True
            
        now_sample = history[-1]
        prev_sample = history[-2]
        
        # If premium is rising, confirm volume is also healthy (greater than the rolling average of past volume)
        if now_sample['ltp'] > prev_sample['ltp']:
            past_vols = [s['volume'] for s in list(history)[:-1]]
            avg_past_vol = sum(past_vols) / len(past_vols) if past_vols else 0
            if now_sample['volume'] < avg_past_vol * 0.9:
                return False
        return True

    def evaluate(self, spot_close, support, resistance, focus_pcr, oi_pattern,
                 spot_history, india_vix=15.0, expiry_date=None, current_date=None, scalp_mode=False,
                 now=None, data_fetcher=None):
        """
        Sniper Signal Evaluation v3.5 (Master Confluence Integrated)
        
        Hardcoded Pillars & PDF Topics:
        1. Market Action (VIC Engine): Leading OI conviction signals.
        2. Trend (Institutional Velocity): Non-lagging directional flow.
        3. History (Zone Memory): Detection of Role Reversals (Old R -> New S).
        4. Market Structure (Topic 2): HH/HL and LH/LL Trend Identification.
        5. Chart Patterns (Topic 3): Reversals (Double Top) and Continuations (Triangle).
        6. Candlestick Analysis (Topic 5): Hammer/Engulfing Trigger.
        7. Volume (Topic 6): Institutional Participation Spike.
        """
        direction = None
        reasons = []

        if now is None:
            now = datetime.now()

        # Update 3TF Trend Tracker
        self.tracker_3tf.update(now, spot_close)
        h4_trend, h1_trend, m15_trend = self.tracker_3tf.get_trends(spot_close)

        # --- TOPIC 6: Update Volume Engine ---
        current_volume = 0
        if spot_history:
            current_volume = spot_history[-1].get('volume', 0)
            self.volume.update(current_volume)
        vol_score = self.volume.get_participation_score(current_volume)

        # --- TOPIC 2: Update Market Structure (HH/HL) ---
        self.structure.update(now, spot_close)
        ms = self.structure.get_structure()
        ms_trend = ms['trend']

        # --- TOPIC 3: Pattern Recognition ---
        pattern_reversal = self.patterns.detect_reversal(self.structure.swing_highs, self.structure.swing_lows)
        pattern_cont = self.patterns.detect_continuation(self.structure.swing_highs, self.structure.swing_lows)

        # --- TOPIC 5: Candlestick Trigger ---
        candle_trigger = None
        if len(spot_history) >= 2:
            curr = spot_history[-1]
            prev = spot_history[-2]
            candle_trigger = self.candles.get_pattern(
                open_p=prev['spot'], high_p=max(prev['spot'], curr['spot']),
                low_p=min(prev['spot'], curr['spot']), close_p=curr['spot'],
                prev_candle={'open': prev['spot'], 'close': prev['spot'], 'high': prev['spot'], 'low': prev['spot']} # Stub
            )

        # --- PILLAR 1 & 2: Update Institutional Velocity ---
        total_ce = oi_pattern.get('total_ce_oi', 0) if isinstance(oi_pattern, dict) else 0
        total_pe = oi_pattern.get('total_pe_oi', 0) if isinstance(oi_pattern, dict) else 0
        self.vic.update(now, focus_pcr, total_pe, total_ce)
        vic_signal = self.vic.get_signal()
        conviction_score = self.vic.get_conviction_score()

        self._push_history(now, focus_pcr, oi_pattern)

        # Calculate distances to walls
        dist_to_sup = abs(spot_close - support) / spot_close if support > 0 else 999
        dist_to_res = abs(resistance - spot_close) / spot_close if resistance > 0 else 999

        # --- PILLAR 3: Update Zone Memory ---
        near_support = support > 0 and dist_to_sup <= PROXIMITY_PCT
        near_resistance = resistance > 0 and dist_to_res <= PROXIMITY_PCT

        if near_support: self.memory.record_touch(support, spot_close, now)
        if near_resistance: self.memory.record_touch(resistance, spot_close, now)

        # Detect Role Reversal (History Repeats)
        is_reclaim = False
        if near_resistance:
            mem_status = self.memory.check_role_reversal(resistance, spot_close, "RESISTANCE")
            if mem_status == "SUPPORT_RECLAIMED" and conviction_score > 3.0:
                is_reclaim = True
                reasons.append(f"🔄 PILLAR 3: Resistance {resistance} RECLAIMED as Support. Switching bias to CE.")

        # ==========================================
        # SIGNAL LOGIC (MASTER CONFLUENCE)
        # ==========================================
        
        # Priority 1: Support Reclaim (High Gamma Breakout)
        if is_reclaim:
            if ms_trend == "DOWNTREND":
                reasons.append(f"❌ TOPIC 2 FAIL: Role Reversal detected but blocked by Structural DOWNTREND.")
            else:
                direction = "CE"
                reasons.append(f"✅ SIGNAL: Role Reversal detected at {resistance}. Institutional Score: {conviction_score:.1f}")
        
        # Priority 2: Standard Support Bounce with Full Confluence
        elif near_support:
            required_ticks = 1 if scalp_mode else SUSTAIN_TICKS
            sustained = check_sustain(spot_history, support, required_ticks=required_ticks)
            
            if sustained:
                # Fyers Option Chain Confluence Gates
                if data_fetcher is not None:
                    # 1. S/R Migration Gate
                    sr_migration = data_fetcher.get_sr_migration()
                    if sr_migration == "BEARISH_SHIFT":
                        reasons.append(f"❌ FYERS PILLAR C FAIL: S/R walls are shifting BEARISH ({sr_migration}). Blocking CE entry.")
                        conviction_score = min(conviction_score, 0.0)
                    
                    # 2. Price-OI Quadrant Check on Put Options (Writers defending)
                    put_quad = self._classify_fyers_quadrant(support, "PE", data_fetcher)
                    if put_quad == "LONG_BUILDUP":
                        reasons.append(f"❌ FYERS PILLAR B FAIL: Support {support} is experiencing PUT LONG_BUILDUP (speculative put buying). Blocking CE entry.")
                        conviction_score = min(conviction_score, 0.0)
                    elif put_quad == "SHORT_BUILDUP":
                        reasons.append(f"📊 FYERS PILLAR B PASS: Support {support} confirmed PUT SHORT_BUILDUP (institutional put writing).")
                        
                    # 3. Call Option Volume-Premium Momentum Gate
                    ce_vol_ok = self._check_option_volume_momentum(support, "CE", data_fetcher)
                    if not ce_vol_ok:
                        reasons.append(f"❌ FYERS PILLAR A FAIL: CE premium at {support} has low volume participation. Blocking CE entry.")
                        conviction_score = min(conviction_score, 0.0)

                # FULL CONFLUENCE: OI + VIC + Pattern + Candle + VOLUME
                if conviction_score >= 3.0:
                    if ms_trend == "DOWNTREND":
                        reasons.append(f"❌ TOPIC 2 FAIL: Conviction high, but Structure is DOWNTREND. Block CE.")
                    else:
                        # NEW: Volume & Candle Trigger
                        if (candle_trigger in ["HAMMER", "BULLISH_ENGULFING"] and vol_score > 1.2) or scalp_mode:
                            direction = "CE"
                            conf_msg = f"Bullish Flow ({vic_signal})"
                            if vol_score > 1.5: conf_msg += f" + 📊 VOL SPIKE ({vol_score:.1f}x)"
                            if candle_trigger: conf_msg += f" + 🔥 {candle_trigger}"
                            reasons.append(f"✅ MASTER CONFLUENCE: {conf_msg} at Support {support}. Structure: {ms_trend}.")
                        else:
                            reasons.append(f"[WAIT] TOPIC 6: Near Support, but waiting for Volume Participation (Score: {vol_score:.1f}) and Candle Trigger.")
                else:
                    reasons.append(f"❌ PILLAR 1 FAIL: Low Institutional Conviction (Score: {conviction_score:.1f}). Block CE.")
            else:
                reasons.append(f"[WAIT] GATE 1: Price near Support {support} but sustain not confirmed.")

        # Priority 3: Resistance Rejection with Full Confluence
        elif near_resistance:
            required_ticks = 1 if scalp_mode else SUSTAIN_TICKS
            sustained = check_sustain(spot_history, resistance, required_ticks=required_ticks)
            
            if sustained:
                # Fyers Option Chain Confluence Gates
                if data_fetcher is not None:
                    # 1. S/R Migration Gate
                    sr_migration = data_fetcher.get_sr_migration()
                    if sr_migration == "BULLISH_SHIFT":
                        reasons.append(f"❌ FYERS PILLAR C FAIL: S/R walls are shifting BULLISH ({sr_migration}). Blocking PE entry.")
                        conviction_score = max(conviction_score, 0.0)
                    
                    # 2. Price-OI Quadrant Check on Call Options (Writers defending)
                    call_quad = self._classify_fyers_quadrant(resistance, "CE", data_fetcher)
                    if call_quad == "LONG_BUILDUP":
                        reasons.append(f"❌ FYERS PILLAR B FAIL: Resistance {resistance} is experiencing CALL LONG_BUILDUP (speculative call buying). Blocking PE entry.")
                        conviction_score = max(conviction_score, 0.0)
                    elif call_quad == "SHORT_BUILDUP":
                        reasons.append(f"📊 FYERS PILLAR B PASS: Resistance {resistance} confirmed CALL SHORT_BUILDUP (institutional call writing).")
                        
                    # 3. Put Option Volume-Premium Momentum Gate
                    pe_vol_ok = self._check_option_volume_momentum(resistance, "PE", data_fetcher)
                    if not pe_vol_ok:
                        reasons.append(f"❌ FYERS PILLAR A FAIL: PE premium at {resistance} has low volume participation. Blocking PE entry.")
                        conviction_score = max(conviction_score, 0.0)

                if conviction_score <= -3.0:
                    if ms_trend == "UPTREND":
                        reasons.append(f"❌ TOPIC 2 FAIL: Bearish conviction, but Structure is UPTREND. Block PE.")
                    else:
                        if (candle_trigger in ["SHOOTING_STAR", "BEARISH_ENGULFING"] and vol_score > 1.2) or scalp_mode:
                            direction = "PE"
                            conf_msg = f"Bearish Flow ({vic_signal})"
                            if vol_score > 1.5: conf_msg += f" + 📊 VOL SPIKE ({vol_score:.1f}x)"
                            if candle_trigger: conf_msg += f" + ❄️ {candle_trigger}"
                            reasons.append(f"✅ MASTER CONFLUENCE: {conf_msg} at Resistance {resistance}. Structure: {ms_trend}.")
                        else:
                            reasons.append(f"[WAIT] TOPIC 6: Near Resistance, but waiting for Volume Participation (Score: {vol_score:.1f}) and Candle Trigger.")
                else:
                    reasons.append(f"❌ PILLAR 1 FAIL: Low Institutional Conviction (Score: {conviction_score:.1f}). Block PE.")
            else:
                reasons.append(f"[WAIT] GATE 1: Price near Resistance {resistance} but sustain not confirmed.")

        else:
            reasons.append(f"No proximity: Spot={spot_close:.0f} | S={support} | R={resistance} | Structure={ms_trend} | Vol={vol_score:.1f}x")

        # Final Pattern-Only Signals (Breakouts)
        if direction is None and pattern_cont in ["ASCENDING_TRIANGLE", "DESCENDING_TRIANGLE"] and abs(conviction_score) >= 7.0 and vol_score > 1.5:
             if pattern_cont == "ASCENDING_TRIANGLE" and conviction_score >= 7.0:
                 direction = "CE"
                 reasons.append(f"✅ SIGNAL: High-Conviction TRIANGLE Breakout + VOLUME SPIKE (Score: {conviction_score:.1f}, Vol: {vol_score:.1f}x).")
             elif pattern_cont == "DESCENDING_TRIANGLE" and conviction_score <= -7.0:
                 direction = "PE"
                 reasons.append(f"✅ SIGNAL: High-Conviction TRIANGLE Breakout + VOLUME SPIKE (Score: {conviction_score:.1f}, Vol: {vol_score:.1f}x).")

        # --- 3TF CONFLUENCE CHECK & CRB BYPASS ---
        enable_3tf = OPTIONS_CONFIG.get("configurableParameters", {}).get("enable_3tf_filters", False)
        if enable_3tf and direction:
            is_bullish = (h4_trend == "UP" and h1_trend == "UP" and m15_trend == "UP")
            is_bearish = (h4_trend == "DOWN" and h1_trend == "DOWN" and m15_trend == "DOWN")
            
            # Reversal confirmations at S/R walls can bypass 3TF filters (retaining mean-reversion edge)
            has_candle_confirm = candle_trigger in ["HAMMER", "BULLISH_ENGULFING", "SHOOTING_STAR", "BEARISH_ENGULFING", "BULLISH_MARUBOZU", "BEARISH_MARUBOZU"]
            has_volume_spike = vol_score >= 1.30 or conviction_score >= 5.0
            is_high_conviction_reversal = (near_support or near_resistance) and (has_candle_confirm or has_volume_spike)
            
            if direction == "CE" and not is_bullish:
                if is_high_conviction_reversal:
                    reasons.append(f"⚡ 3TF BYPASS ACTIVE: CE generated with non-aligned trends (4H={h4_trend}, 1H={h1_trend}, 15M={m15_trend}), but bypassed due to High-Conviction Reversal at Support (Candle={candle_trigger}, Vol={vol_score:.1f}x).")
                else:
                    reasons.append(f"❌ 3TF FILTER FAIL: CE signal generated but 3TF not aligned (4H={h4_trend}, 1H={h1_trend}, 15M={m15_trend}). Sitting on hands.")
                    direction = None
            elif direction == "PE" and not is_bearish:
                if is_high_conviction_reversal:
                    reasons.append(f"⚡ 3TF BYPASS ACTIVE: PE generated with non-aligned trends (4H={h4_trend}, 1H={h1_trend}, 15M={m15_trend}), but bypassed due to High-Conviction Reversal at Resistance (Candle={candle_trigger}, Vol={vol_score:.1f}x).")
                else:
                    reasons.append(f"❌ 3TF FILTER FAIL: PE signal generated but 3TF not aligned (4H={h4_trend}, 1H={h1_trend}, 15M={m15_trend}). Sitting on hands.")
                    direction = None
            else:
                reasons.append(f"✅ 3TF CONFLUENCE PASS: 4H={h4_trend}, 1H={h1_trend}, 15M={m15_trend} aligned.")

        # DTE Risk Classification
        dte_risk = "MODERATE"
        dte_days = 99
        is_expiry_day = False

        if expiry_date:
            dte_risk, dte_days = classify_dte_risk(expiry_date, current_date)
            is_expiry_day = (dte_days <= 0)

        return {
            "direction": direction,
            "reasons": reasons,
            "dte_risk": dte_risk,
            "dte_days": dte_days,
            "is_expiry_day": is_expiry_day,
            "score": 5 if direction else 0
        }
