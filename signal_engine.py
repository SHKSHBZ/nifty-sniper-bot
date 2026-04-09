"""
signal_engine.py
================
The upgraded options buying brain: Pure Option Chain Intelligence.
Replaces lagging chart indicators with live OI Support/Resistance & PCR.
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime, date
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
DTE_EXTREME        = 3
DTE_HIGH           = 7

# Tolerance for price interacting with Support/Resistance walls
PROXIMITY_PCT = 0.0015  # 0.15% (~33 points on Nifty)

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
# Indicator Calcs (Kept strictly for trend confirmation)
# ---------------------------------------------------------------------------
def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def calc_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close  = np.abs(df['low']  - df['close'].shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()

# ---------------------------------------------------------------------------
# PCR Bias Interpretation
# ---------------------------------------------------------------------------
def interpret_pcr(pcr_value):
    if pcr_value > 1.5 or pcr_value < 0.6: return "contrarian"
    if pcr_value >= 1.05: return "bullish"
    if pcr_value <= 0.85: return "bearish"
    return "neutral"

# ---------------------------------------------------------------------------
# The Signal Scorer (Option Chain Pivot)
# ---------------------------------------------------------------------------
class SignalEngine:
    def evaluate(self, spot_5m_df, spot_15m_df=None, pcr=1.0, support=0, resistance=0, 
                 spot_close=None, expiry_date=None, current_date=None):
        if spot_close is None:
            spot_close = spot_5m_df['close'].iloc[-1]

        direction = None
        reasons = []

        # 1. Option Chain Dynamics
        pcr_bias = interpret_pcr(pcr)
        dist_to_sup = abs(spot_close - support) / spot_close if support > 0 else 999
        dist_to_res = abs(resistance - spot_close) / spot_close if resistance > 0 else 999

        # 2. Basic Underlying Momentum
        if spot_5m_df is not None:
            ema9  = spot_5m_df['ema_fast'].iloc[-1]
            ema21 = spot_5m_df['ema_slow'].iloc[-1]
            trend = "bullish" if ema9 > ema21 else "bearish"
        else:
            trend = "neutral"

        # Strategy 1: Support Bounce
        if support > 0 and dist_to_sup <= PROXIMITY_PCT:
            reasons.append(f"Price ({spot_close:.0f}) near Support OI Wall ({support}).")
            if pcr_bias != "bearish" and trend != "bearish":
                direction = "CE"
                reasons.append(f"-> Bounce Confirmed: PCR={pcr:.2f} & Trend aligns.")
            else:
                reasons.append(f"-> Pass: PCR is {pcr_bias} or Trend is {trend}.")

        # Strategy 2: Resistance Rejection
        elif resistance > 0 and dist_to_res <= PROXIMITY_PCT:
            reasons.append(f"Price ({spot_close:.0f}) near Resistance OI Wall ({resistance}).")
            if pcr_bias != "bullish" and trend != "bullish":
                direction = "PE"
                reasons.append(f"-> Rejection Confirmed: PCR={pcr:.2f} & Trend aligns.")
            else:
                reasons.append(f"-> Pass: PCR is {pcr_bias} or Trend is {trend}.")

        # 15-Minute Macro Filter
        if direction and spot_15m_df is not None:
            ema9_15m = spot_15m_df['ema_fast'].iloc[-1]
            if direction == "CE" and spot_close < ema9_15m:
                reasons.append(f"15m FILTER BLOCK: Spot {spot_close:.0f} below 15m VWAP/EMA {ema9_15m:.0f}")
                direction = None
            elif direction == "PE" and spot_close > ema9_15m:
                reasons.append(f"15m FILTER BLOCK: Spot {spot_close:.0f} above 15m VWAP/EMA {ema9_15m:.0f}")
                direction = None

        # DTE Risk Classification
        dte_risk = "MODERATE"
        dte_days = 99
        is_expiry_day = False
        
        if expiry_date:
            dte_risk, dte_days = classify_dte_risk(expiry_date, current_date)
            is_expiry_day = (dte_days <= 0)

        # Log PCR/OI state regardless of trade
        if direction is None:
            reasons.append(f"Chain State: S={support} R={resistance} PCR={pcr:.2f}")

        return {
            "direction": direction,
            "reasons": reasons,
            "dte_risk": dte_risk,
            "dte_days": dte_days,
            "is_expiry_day": is_expiry_day,
            "score": 5 if direction else 0
        }
