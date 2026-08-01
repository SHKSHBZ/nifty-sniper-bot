import sys
sys.path.append(".")
import pandas as pd
import numpy as np
import glob
import math
from datetime import datetime
from gann_engine import GannSquareOf9

def run_simulation():
    # Load all Nifty focus zone files
    files = sorted(glob.glob("logs/focus_zone_nifty_*.csv"))
    all_days_summary = []
    total_net_pnl = 0.0
    wins = 0
    losses = 0

    print("=============================================================")
    print("REACTION & REVERSAL TRADER SIMULATOR (NIFTY)")
    print("=============================================================")

    for f_path in files:
        df = pd.read_csv(f_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
        df = df.sort_values("timestamp")
        
        # Group by date
        for date, day_df in df.groupby(df["timestamp"].dt.date):
            date_str = str(date)
            # Filter standard hours
            day_df = day_df[(day_df["timestamp"].dt.time >= datetime.strptime("09:15:00", "%H:%M:%S").time()) &
                            (day_df["timestamp"].dt.time <= datetime.strptime("15:30:00", "%H:%M:%S").time())]
            if len(day_df) < 30:
                continue

            ticks = []
            for ts, group in day_df.groupby("timestamp"):
                spot = group["spot"].iloc[0]
                # Premiums dict
                premiums = {}
                for idx, row in group.iterrows():
                    strike = int(row["strike"])
                    if not pd.isna(row["ce_ltp"]):
                        premiums[f"{strike}_CE"] = float(row["ce_ltp"])
                    if not pd.isna(row["pe_ltp"]):
                        premiums[f"{strike}_PE"] = float(row["pe_ltp"])
                ticks.append({"timestamp": ts, "spot": spot, "premiums": premiums})

            if not ticks:
                continue

            open_spot = ticks[0]["spot"]
            
            # Setup Gann Levels based on opening price
            gann = GannSquareOf9(open_spot)
            ce_trigger = gann.levels["buy"][45]   # Upside resistance
            ce_target = gann.levels["buy"][90]    # Upside target
            pe_trigger = gann.levels["sell"][45]  # Downside support
            pe_target = gann.levels["sell"][90]   # Downside target

            # Locate spot at 09:30 AM IST
            spot_0930 = None
            idx_0930 = None
            high_0915_0930 = open_spot
            low_0915_0930 = open_spot

            for i, tick in enumerate(ticks):
                t_str = tick["timestamp"].strftime("%H:%M")
                if t_str <= "09:30":
                    high_0915_0930 = max(high_0915_0930, tick["spot"])
                    low_0915_0930 = min(low_0915_0930, tick["spot"])
                if t_str == "09:30" or (t_str > "09:30" and spot_0930 is None):
                    spot_0930 = tick["spot"]
                    idx_0930 = i
                    break

            if spot_0930 is None or idx_0930 is None:
                continue

            # Determine Zone Track at 09:30
            zone = "sideways"
            if spot_0930 >= ce_trigger:
                zone = "upside"  # Market ran up to +45° resistance
            elif spot_0930 <= pe_trigger:
                zone = "downside" # Market ran down to -45° support

            if zone == "sideways":
                # Filter out flat sideways days completely
                continue

            # Scan from 09:30 onwards for reaction or reversal
            position = None
            pnl = 0.0

            for i in range(idx_0930, len(ticks)):
                tick = ticks[i]
                ts = tick["timestamp"]
                spot = tick["spot"]
                prems = tick["premiums"]

                if position is None:
                    # Look for Entry
                    if zone == "upside":
                        # 1. CE Continuation (Spot breaks above 09:15-09:30 high)
                        if spot >= high_0915_0930 + 5.0:
                            strike = round(spot / 50) * 50
                            key = f"{strike}_CE"
                            if key in prems:
                                # Dynamic Gann levels at entry spot
                                levels = gann.get_active_levels(spot)
                                position = {
                                    "type": "CE_Continuation",
                                    "strike": strike,
                                    "entry_ts": ts,
                                    "entry_spot": spot,
                                    "entry_premium": prems[key],
                                    "target": levels["ce_target"],
                                    "sl": open_spot, # Open spot gives breathing room
                                    "lots": 5,
                                    "max_premium": prems[key]
                                }
                        # 2. PE Reversal (Spot falls below 09:30 spot by 15 pts)
                        elif spot <= spot_0930 - 15.0:
                            strike = round(spot / 50) * 50
                            key = f"{strike}_PE"
                            if key in prems:
                                levels = gann.get_active_levels(spot)
                                position = {
                                    "type": "PE_Reversal",
                                    "strike": strike,
                                    "entry_ts": ts,
                                    "entry_spot": spot,
                                    "entry_premium": prems[key],
                                    "target": open_spot, # Target is the baseline open
                                    "sl": levels["pe_sl"] + 15.0, # Give some buffer
                                    "lots": 5,
                                    "max_premium": prems[key]
                                }

                    elif zone == "downside":
                        # 1. PE Continuation (Spot breaks below 09:15-09:30 low)
                        if spot <= low_0915_0930 - 5.0:
                            strike = round(spot / 50) * 50
                            key = f"{strike}_PE"
                            if key in prems:
                                levels = gann.get_active_levels(spot)
                                position = {
                                    "type": "PE_Continuation",
                                    "strike": strike,
                                    "entry_ts": ts,
                                    "entry_spot": spot,
                                    "entry_premium": prems[key],
                                    "target": levels["pe_target"],
                                    "sl": open_spot, # Open spot gives breathing room
                                    "lots": 5,
                                    "max_premium": prems[key]
                                }
                        # 2. CE Reversal (Spot rises above 09:30 spot by 15 pts)
                        elif spot >= spot_0930 + 15.0:
                            strike = round(spot / 50) * 50
                            key = f"{strike}_CE"
                            if key in prems:
                                levels = gann.get_active_levels(spot)
                                position = {
                                    "type": "CE_Reversal",
                                    "strike": strike,
                                    "entry_ts": ts,
                                    "entry_spot": spot,
                                    "entry_premium": prems[key],
                                    "target": open_spot, # Target is the baseline open
                                    "sl": levels["ce_sl"] - 15.0, # Give some buffer
                                    "lots": 5,
                                    "max_premium": prems[key]
                                }
                else:
                    # Position is active, check exit
                    strike = position["strike"]
                    p_type = position["type"]
                    opt_type = "CE" if "CE" in p_type or "Continuation" in p_type and "PE" not in p_type else "PE"
                    if p_type == "CE_Continuation" or p_type == "CE_Reversal":
                        opt_type = "CE"
                    else:
                        opt_type = "PE"

                    key = f"{strike}_{opt_type}"
                    if key not in prems:
                        # Missing option price, force close
                        pnl = (prems.get(key, position["entry_premium"]) - position["entry_premium"]) * position["lots"] * 65
                        exit_reason = "FORCE_CLOSE_MISSING_DATA"
                        break

                    curr_prem = prems[key]
                    position["max_premium"] = max(position["max_premium"], curr_prem)
                    
                    hold_mins = (ts - position["entry_ts"]).total_seconds() / 60
                    
                    # Exits
                    exit_triggered = False
                    reason = ""
                    
                    # 1. Target Hit
                    if opt_type == "CE" and spot >= position["target"]:
                        exit_triggered = True
                        reason = f"TARGET HIT ({position['target']:.1f})"
                    elif opt_type == "PE" and spot <= position["target"]:
                        exit_triggered = True
                        reason = f"TARGET HIT ({position['target']:.1f})"
                    
                    # 2. Stop Loss (Gann spot boundary)
                    elif opt_type == "CE" and spot <= position["sl"]:
                        exit_triggered = True
                        reason = f"STOP LOSS Spot broke {position['sl']:.1f}"
                    elif opt_type == "PE" and spot >= position["sl"]:
                        exit_triggered = True
                        reason = f"STOP LOSS Spot broke {position['sl']:.1f}"
                        
                    # 3. Trailing SL (-25% premium drop)
                    elif (curr_prem - position["max_premium"]) / position["max_premium"] <= -0.25:
                        exit_triggered = True
                        reason = "TSL: -25% from peak"
                        
                    # 4. Max Hold (30 mins)
                    elif hold_mins >= 30:
                        exit_triggered = True
                        reason = "MAX HOLD 30m"

                    if exit_triggered:
                        pnl = (curr_prem - position["entry_premium"]) * position["lots"] * 65
                        print(f"[{date_str}] {p_type} Entry {position['entry_ts'].strftime('%H:%M')} @ Spot {position['entry_spot']:.1f} (Prem: {position['entry_premium']:.1f}) | Exit {ts.strftime('%H:%M')} Reason: {reason} | P&L: Rs.{pnl:+,.0f}")
                        if pnl > 0:
                            wins += 1
                        else:
                            losses += 1
                        total_net_pnl += pnl
                        break

    print("=============================================================")
    print("FINAL REACTION & REVERSAL SUMMARY (NIFTY)")
    print("=============================================================")
    print(f"Total Net P&L : Rs. {total_net_pnl:+,.0f}")
    print(f"Wins / Losses : {wins} / {losses}")
    print(f"Win Rate      : {wins / max(1, wins+losses) * 100:.1f}%")
    print("=============================================================")

if __name__ == "__main__":
    run_simulation()
