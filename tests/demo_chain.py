import pandas as pd
from datetime import datetime
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

# Load only March 27 CSVs for Nifty
options_dict = {}
for p in Path("data").glob("NIFTY_*_1min.csv"):
    parts = p.name.split('_')
    key = f"{parts[1]}_{parts[2]}"
    df = pd.read_csv(p)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Filter for just March 27 to keep it fast
    df = df[df['timestamp'].dt.strftime('%Y-%m-%d') == "2026-03-27"]
    if df.empty: continue
        
    df['time_str'] = df['timestamp'].dt.strftime('%H:%M:%S')
    df.set_index('time_str', inplace=True)
    options_dict[key] = df

# Let's perfectly simulate exactly what the bot sees at a specific minute (e.g., 12:35 PM on Mar 27 where it took a trade)
check_time = "12:35:00"
spot_close = 22941.0  # Approx Nifty Spot at this time

print("="*60)
print(f" BOT VISION AT EXACTLY {check_time} (NIFTY SPOT: {spot_close})")
print("="*60)

ce_oi = 0
pe_oi = 0
max_ce_oi = 0
max_pe_oi = 0
res_strike = 0
sup_strike = 0

print(f"{'Strike':<10} | {'Call OI (Resistance)':<20} | {'Put OI (Support)':<20}")
print("-" * 60)

# We loop through all available strikes
strikes = sorted(list(set([int(k.split('_')[0]) for k in options_dict.keys()])))

for strike in strikes:
    # Get CE Open Interest
    c_oi = 0
    if f"{strike}_CE" in options_dict and check_time in options_dict[f"{strike}_CE"].index:
        c_row = options_dict[f"{strike}_CE"].loc[check_time]
        if isinstance(c_row, pd.DataFrame): c_row = c_row.iloc[0]
        c_oi = float(c_row.get('open_interest', 0))
    
    # Get PE Open Interest
    p_oi = 0
    if f"{strike}_PE" in options_dict and check_time in options_dict[f"{strike}_PE"].index:
        p_row = options_dict[f"{strike}_PE"].loc[check_time]
        if isinstance(p_row, pd.DataFrame): p_row = p_row.iloc[0]
        p_oi = float(p_row.get('open_interest', 0))

    # Log to screen
    print(f"{strike:<10} | {int(c_oi):<20,} | {int(p_oi):<20,}")

    ce_oi += c_oi
    pe_oi += p_oi
    
    # Find active resistance (Call writers defend Above spot)
    if c_oi > max_ce_oi and strike >= spot_close - 50:
        max_ce_oi = c_oi
        res_strike = strike
        
    # Find active support (Put writers defend Below spot)
    if p_oi > max_pe_oi and strike <= spot_close + 50:
        max_pe_oi = p_oi
        sup_strike = strike

pcr = (pe_oi / ce_oi) if ce_oi > 0 else 1.0

print("-" * 60)
print(f"\n[BOT CALCULATION]")
print(f"Total Call OI (Bears): {int(ce_oi):,}")
print(f"Total Put OI  (Bulls): {int(pe_oi):,}")
print(f"PCR (Put/Call Ratio):  {pcr:.2f}")

print("\n[BOT DECISION LOGIC]")
print(f"1. Scanning for Highest Call OI (Resistance Wall) -> Found at {res_strike} CE")
print(f"2. Scanning for Highest Put OI (Support Wall)     -> Found at {sup_strike} PE")

if spot_close >= res_strike * 0.9985: # Within 0.15% proximity
    print(f"\n[TRIGGER EXECUTED]")
    print(f"-> Nifty Spot ({spot_close}) has hit the Resistance Wall ({res_strike}).")
    print(f"-> PCR is {pcr:.2f} (Not Bullish). Call writers are actively defending this level.")
    print(f"-> ACTION: BUY PUT OPTIONS (Expecting Rejection Downwards).")

