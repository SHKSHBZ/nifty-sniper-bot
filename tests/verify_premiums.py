import pandas as pd
from pathlib import Path

def print_real_option_premiums():
    # Load the real Nifty option data we just downloaded from Expiry Day (Mar 30)!
    target_file = Path("data/NIFTY_22350_CE_30_MAR_26_1min.csv")
    
    if not target_file.exists():
        print("Data file not found!")
        return
        
    df = pd.read_csv(target_file)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Filter to show specifically what the premium was doing at crucial morning times
    print("\n--- EXACT REAL PREMIUMS ON MARCH 30 EXPIRY DAY (NIFTY 22350 CE) ---")
    
    # Grab an open, mid-morning, noon, and close snapshot
    times_to_check = [
        "09:15:00", "09:30:00", "10:00:00", 
        "12:00:00", "14:00:00", "15:20:00"
    ]
    
    for tstr in times_to_check:
        snapshot = df[df['timestamp'].dt.strftime("%H:%M:%S") == tstr]
        if not snapshot.empty:
            row = snapshot.iloc[0]
            print(f"Time: {tstr}  |  Open Price (Premium): {row['open']:<6} | Vol: {row['volume']:<8} | High: {row['high']}")

if __name__ == "__main__":
    print_real_option_premiums()
