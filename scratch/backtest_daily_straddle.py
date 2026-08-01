import os
import glob
import pandas as pd
from datetime import datetime

def backtest_daily_straddle():
    log_files = glob.glob("logs/focus_zone_nifty*.csv") + glob.glob("logs/focus_zone_sensex*.csv")
    
    trades = []
    total_pnl_pts = 0
    wins = 0
    losses = 0
    
    for file in sorted(log_files):
        # Extract date from filename (e.g. focus_zone_nifty_2026-06-15.csv)
        basename = os.path.basename(file)
        parts = basename.replace(".csv", "").split("_")
        date_str = parts[-1]
        index_name = "NIFTY" if "nifty" in basename else "SENSEX"
        strike_step = 50 if index_name == "NIFTY" else 100
        
        try:
            df = pd.read_csv(file)
        except Exception as e:
            continue
            
        if "timestamp" not in df.columns or "spot" not in df.columns or "strike" not in df.columns:
            continue
            
        # Parse timestamp column
        df['TimeObj'] = pd.to_datetime(df['timestamp'], format='mixed').dt.time
        mask = (df['TimeObj'] >= pd.to_datetime("14:55:00").time()) & (df['TimeObj'] <= pd.to_datetime("15:06:00").time())
        subset = df[mask]
        
        if subset.empty:
            continue
            
        # Get unique timestamps
        timestamps = sorted(subset['timestamp'].unique())
        if not timestamps:
            continue
            
        entry_time = timestamps[0]
        entry_rows = subset[subset['timestamp'] == entry_time]
        if entry_rows.empty:
            continue
            
        spot = entry_rows.iloc[0]['spot']
        atm_strike = round(spot / strike_step) * strike_step
        
        atm_row = entry_rows[entry_rows['strike'] == atm_strike]
        if atm_row.empty:
            continue
            
        ce_entry = atm_row.iloc[0]['ce_ltp']
        pe_entry = atm_row.iloc[0]['pe_ltp']
        if ce_entry <= 0 or pe_entry <= 0:
            continue
            
        combined_entry = ce_entry + pe_entry
        target = combined_entry * 2.0  # +100%
        sl = combined_entry * 0.5      # -50%
        
        exit_time = None
        exit_price = None
        exit_reason = ""
        
        # Track minute by minute
        for t in timestamps[1:]:
            t_rows = subset[subset['timestamp'] == t]
            t_atm = t_rows[t_rows['strike'] == atm_strike]
            if t_atm.empty:
                continue
            curr_ce = t_atm.iloc[0]['ce_ltp']
            curr_pe = t_atm.iloc[0]['pe_ltp']
            curr_combined = curr_ce + curr_pe
            
            if curr_combined >= target:
                exit_price = target
                exit_reason = "TARGET HIT (+100%)"
                exit_time = t
                break
            elif curr_combined <= sl:
                exit_price = sl
                exit_reason = "STOP LOSS (-50%)"
                exit_time = t
                break
                
            if str(t)[11:] >= "15:05:00":
                exit_price = curr_combined
                exit_reason = "TIME STOP (15:05)"
                exit_time = t
                break
                
        if exit_price is None:
            # If 15:05 wasn't reached, take the last available tick
            last_t = timestamps[-1]
            t_atm = subset[(subset['timestamp'] == last_t) & (subset['strike'] == atm_strike)]
            if not t_atm.empty:
                exit_price = t_atm.iloc[0]['ce_ltp'] + t_atm.iloc[0]['pe_ltp']
                exit_reason = "DATA END"
                exit_time = last_t
            else:
                continue
                
        pnl_pts = exit_price - combined_entry
        total_pnl_pts += pnl_pts
        if pnl_pts > 0:
            wins += 1
        else:
            losses += 1
            
        trades.append({
            "Date": date_str,
            "Index": index_name,
            "Spot": spot,
            "ATM": atm_strike,
            "EntryTime": entry_time,
            "ExitTime": exit_time,
            "EntryPremium": combined_entry,
            "ExitPremium": exit_price,
            "Reason": exit_reason,
            "PnL": pnl_pts
        })
        
    print("="*60)
    print(" 14:55 PM DAILY STRADDLE BACKTEST (LAST 30 DAYS) ")
    print("="*60)
    print(f"{'Date':<12} | {'Index':<8} | {'Entry':<8} | {'Exit':<8} | {'PnL (Pts)':<10} | {'Reason'}")
    print("-" * 60)
    for t in trades:
        print(f"{t['Date']:<12} | {t['Index']:<8} | {t['EntryPremium']:<8.1f} | {t['ExitPremium']:<8.1f} | {t['PnL']:<10.1f} | {t['Reason']}")
        
    print("="*60)
    print(f"Total Trades : {len(trades)}")
    print(f"Wins         : {wins}")
    print(f"Losses       : {losses}")
    win_rate = (wins / len(trades) * 100) if trades else 0
    print(f"Win Rate     : {win_rate:.1f}%")
    print(f"Total Points : {total_pnl_pts:.1f} pts")
    print(f"Nifty 1 Lot  : Rs. {total_pnl_pts * 25:.0f}")
    print(f"Sensex 1 Lot : Rs. {total_pnl_pts * 10:.0f}")

if __name__ == "__main__":
    backtest_daily_straddle()
