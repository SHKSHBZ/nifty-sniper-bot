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
PROXIMITY_PCT = PARAMS.get("wallProximityTolerancePercent", 0.15) / 100.0

# Sustain parameters
SUSTAIN_TICKS = 3       # Number of consecutive 5m candle closes required in zone
SUSTAIN_INTERVAL = 5    # Minutes between each candle close check

# Focus Zone PCR thresholds
FOCUS_PCR_BULLISH_THRESHOLD = 1.1    # PCR above this = bullish (heavy Put writing)
FOCUS_PCR_BEARISH_THRESHOLD = 0.85   # PCR below this = bearish (heavy Call writing)

# Gate 2: PCR must lie inside an *entry band* (Goldilocks zone).
# Below the lower bound  = flow not strong enough → block.
# Above the upper bound  = move is over-extended; entering now = chasing
#                          → block (most reversals happen at PCR extremes).
# Inside the band        = early-enough confirmation → fire.
FOCUS_PCR_CE_ENTRY_LOW  = 1.00   # need at least this much bullish lean for CE
FOCUS_PCR_CE_ENTRY_HIGH = 1.30   # above this = too extended; fade-risk

FOCUS_PCR_PE_ENTRY_LOW  = 0.50   # below this = extreme; fade-risk
FOCUS_PCR_PE_ENTRY_HIGH = 0.95   # need at least this much bearish lean for PE

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


# ---------------------------------------------------------------------------
# The Sniper Signal Engine v3.0
# ---------------------------------------------------------------------------
class SignalEngine:
    def evaluate(self, spot_close, support, resistance, focus_pcr, oi_pattern,
                 spot_history, india_vix=15.0, expiry_date=None, current_date=None, scalp_mode=False):
        """
        Three-Gate entry evaluation:

        Gate 0: India VIX Macro Trend - Restricts contrarian entries based on volatility.
        Gate 1: Proximity + Sustain — Price must be near a wall AND sustained 3x 5m candles.
        Gate 2: Focus Zone PCR — Localized PCR must confirm directional bias.
        Gate 3: OI Build-Up — Option writers must be actively defending the wall.

        All gates must PASS for a signal to fire.
        """
        direction = None
        reasons = []

        # Calculate distances to walls
        dist_to_sup = abs(spot_close - support) / spot_close if support > 0 else 999
        dist_to_res = abs(resistance - spot_close) / spot_close if resistance > 0 else 999

        # Interpret Focus Zone PCR
        pcr_bias = interpret_focus_pcr(focus_pcr)
        
        # Interpret VIX Trend (>= 18 is fear/downtrend -> Bearish, < 18 is growth -> Bullish)
        vix_trend = "bearish" if india_vix >= 18.0 else "bullish"

        # ==========================================
        # Check proximity to Support or Resistance
        # ==========================================
        near_support = support > 0 and dist_to_sup <= PROXIMITY_PCT
        near_resistance = resistance > 0 and dist_to_res <= PROXIMITY_PCT

        if near_support:
            # --- GATE 1: Sustain Check at Support ---
            required_ticks = 1 if scalp_mode else SUSTAIN_TICKS
            sustained = check_sustain(spot_history, support, required_ticks=required_ticks)
            
            if sustained:
                time_str = "INSTANT TOUCH (Scalp)" if scalp_mode else f"{SUSTAIN_TICKS}x 5m candles"
                reasons.append(
                    f"✅ GATE 1 PASS: Price ({spot_close:.0f}) sustained near "
                    f"Support ({support}) for {time_str}."
                )

                # --- GATE 0: VIX Macro Trend Check ---
                if vix_trend == "bearish":
                    reasons.append(
                        f"❌ GATE 0 FAIL: India VIX is {india_vix:.2f} (Fear/Downtrend). "
                        f"Taking CE (Call) entries against the macro trend is blocked."
                    )
                else:
                    reasons.append(f"✅ GATE 0 PASS: VIX={india_vix:.2f} supports CE entries.")

                    # --- GATE 2: PCR must lie inside the CE entry band ---
                    if FOCUS_PCR_CE_ENTRY_LOW <= focus_pcr <= FOCUS_PCR_CE_ENTRY_HIGH:
                        reasons.append(
                            f"✅ GATE 2 PASS: Focus PCR={focus_pcr:.2f} inside CE band "
                            f"[{FOCUS_PCR_CE_ENTRY_LOW:.2f}-{FOCUS_PCR_CE_ENTRY_HIGH:.2f}]."
                        )

                        # --- GATE 3: OI Build-Up Confirmation ---
                        oi_confirmed, oi_reason = check_oi_confirmation(oi_pattern, "CE")
                        if oi_confirmed:
                            direction = "CE"
                            reasons.append(f"✅ GATE 3 PASS: {oi_reason}")
                        else:
                            reasons.append(f"❌ GATE 3 FAIL: {oi_reason}")
                    elif focus_pcr < FOCUS_PCR_CE_ENTRY_LOW:
                        reasons.append(
                            f"❌ GATE 2 FAIL: Focus PCR={focus_pcr:.2f} below "
                            f"{FOCUS_PCR_CE_ENTRY_LOW:.2f}. Bullish flow not yet confirmed."
                        )
                    else:
                        reasons.append(
                            f"❌ GATE 2 FAIL: Focus PCR={focus_pcr:.2f} above "
                            f"{FOCUS_PCR_CE_ENTRY_HIGH:.2f}. Move over-extended; chasing CE risky."
                        )
            else:
                time_req = "0m" if scalp_mode else f"{SUSTAIN_TICKS}x 5m candles"
                reasons.append(
                    f"[WAIT] GATE 1 PENDING: Price near Support ({support}) but sustain "
                    f"not confirmed ({time_req} needed)."
                )

        elif near_resistance:
            # --- GATE 1: Sustain Check at Resistance ---
            required_ticks = 1 if scalp_mode else SUSTAIN_TICKS
            sustained = check_sustain(spot_history, resistance, required_ticks=required_ticks)
            
            if sustained:
                time_str = "INSTANT TOUCH (Scalp)" if scalp_mode else f"{SUSTAIN_TICKS}x 5m candles"
                reasons.append(
                    f"✅ GATE 1 PASS: Price ({spot_close:.0f}) sustained near "
                    f"Resistance ({resistance}) for {time_str}."
                )

                # --- GATE 0: VIX Macro Trend Check ---
                if vix_trend == "bullish":
                    reasons.append(
                        f"❌ GATE 0 FAIL: India VIX is {india_vix:.2f} (Growth/Uptrend). "
                        f"Taking PE (Put) entries against the macro trend is blocked."
                    )
                else:
                    reasons.append(f"✅ GATE 0 PASS: VIX={india_vix:.2f} supports PE entries.")

                    # --- GATE 2: PCR must lie inside the PE entry band ---
                    if FOCUS_PCR_PE_ENTRY_LOW <= focus_pcr <= FOCUS_PCR_PE_ENTRY_HIGH:
                        reasons.append(
                            f"✅ GATE 2 PASS: Focus PCR={focus_pcr:.2f} inside PE band "
                            f"[{FOCUS_PCR_PE_ENTRY_LOW:.2f}-{FOCUS_PCR_PE_ENTRY_HIGH:.2f}]."
                        )

                        # --- GATE 3: OI Build-Up Confirmation ---
                        oi_confirmed, oi_reason = check_oi_confirmation(oi_pattern, "PE")
                        if oi_confirmed:
                            direction = "PE"
                            reasons.append(f"✅ GATE 3 PASS: {oi_reason}")
                        else:
                            reasons.append(f"❌ GATE 3 FAIL: {oi_reason}")
                    elif focus_pcr > FOCUS_PCR_PE_ENTRY_HIGH:
                        reasons.append(
                            f"❌ GATE 2 FAIL: Focus PCR={focus_pcr:.2f} above "
                            f"{FOCUS_PCR_PE_ENTRY_HIGH:.2f}. Bearish flow not yet confirmed."
                        )
                    else:
                        reasons.append(
                            f"❌ GATE 2 FAIL: Focus PCR={focus_pcr:.2f} below "
                            f"{FOCUS_PCR_PE_ENTRY_LOW:.2f}. Move over-extended; chasing PE risky."
                        )
            else:
                time_req = "0m" if scalp_mode else f"{SUSTAIN_TICKS}x 5m candles"
                reasons.append(
                    f"[WAIT] GATE 1 PENDING: Price near Resistance ({resistance}) but "
                    f"sustain not confirmed ({time_req} needed)."
                )

        else:
            reasons.append(
                f"No proximity: Spot={spot_close:.0f} | "
                f"S={support} (dist={dist_to_sup:.4f}) | "
                f"R={resistance} (dist={dist_to_res:.4f})"
            )

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
