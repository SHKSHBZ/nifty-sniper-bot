import pandas as pd
import numpy as np
import logging
import requests
from datetime import datetime
from pathlib import Path

from upstox_auth import UpstoxAuth
from strike_selector import StrikeSelector

logging.basicConfig(level=logging.INFO, format='%(message)s')

def get_spot_data():
    auth = UpstoxAuth()
    access_token = auth.get_access_token()
    if not access_token:
        print("Auth failed.")
        return None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    url = f"https://api.upstox.com/v2/historical-candle/NSE_INDEX|Nifty 50/1minute/2026-03-31/2026-03-24"
    resp = requests.get(url, headers=headers)
    data = resp.json().get("data", {}).get("candles", [])
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "open_interest"])
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("Asia/Kolkata")
    df = df.sort_values(by="timestamp").reset_index(drop=True)
    df['time_str'] = df['timestamp'].dt.strftime('%H:%M:%00') # Ensure string matching
    df['date_str'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    return df

def load_options_data():
    options_dict = {}
    files_loaded = 0
    for p in Path("data").glob("NIFTY_*_1min.csv"):
        parts = p.name.split('_')
        if len(parts) >= 4:
            strike = parts[1]
            opt_type = parts[2]
            key = f"{strike}_{opt_type}"
            df = pd.read_csv(p)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            # Convert Upstox option timestamps to Kolkata
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
            elif str(df['timestamp'].dt.tz) != 'Asia/Kolkata':
                 df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Kolkata')
            
            df['time_str'] = df['timestamp'].dt.strftime('%H:%M:%00')
            # Index by time for lightning fast lookup
            df.set_index('time_str', inplace=True)
            options_dict[key] = df
            files_loaded += 1
    
    print(f"Loaded {files_loaded} real Options CSV files into memory.")
    return options_dict

def calculate_atr_series(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

def run_real_backtest():
    print("Fetching historical Market Spot Data...")
    spot_df = get_spot_data()
    if spot_df is None: return
    
    options_dict = load_options_data()
    
    trades = []
    capital = 100000.0
    qty = 65 
    
    in_position = False
    trade_type = ""
    strike_key = ""
    entry_premium = 0.0
    entry_time = None
    target_price = 0.0
    sl_price = 0.0

    target_date = "2026-03-30"
    print(f"Starting true 5-MINUTE algorithmic backtest on {target_date}...")

    # --- THE 5-MINUTE QUANT UPGRADE ---
    print("Resampling underlying Spot Index to 5-Minute timeframe to eliminate scalping noise...")
    spot_df.set_index('timestamp', inplace=True)
    spot_5m = spot_df.resample('5min').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last'})
    spot_5m.dropna(inplace=True)
    
    # Calculate Indicators precisely on the 5-Minute chart
    spot_5m['ema_fast'] = spot_5m['close'].ewm(span=9, adjust=False).mean()
    spot_5m['ema_slow'] = spot_5m['close'].ewm(span=21, adjust=False).mean()
    
    # RSI (14)
    delta = spot_5m['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    spot_5m['rsi'] = 100 - (100 / (1 + rs))
    spot_5m['atr'] = calculate_atr_series(spot_5m, 14)
    
    # SHIFT BY 1 TO PREVENT TIME-MACHINE LOOKAHEAD BIAS!
    # The 13:00 candle ends at 13:05. So at 13:05 we use the 13:00 indicators.
    spot_5m[['ema_fast', 'ema_slow', 'rsi', 'atr']] = spot_5m[['ema_fast', 'ema_slow', 'rsi', 'atr']].shift(1)
    
    # Merge the 5-Minute Brain back onto our 1-Minute Execution Tracker
    spot_df = spot_df.join(spot_5m[['ema_fast', 'ema_slow', 'rsi', 'atr']], how='left')
    spot_df[['ema_fast', 'ema_slow', 'rsi', 'atr']] = spot_df[['ema_fast', 'ema_slow', 'rsi', 'atr']].ffill()
    spot_df.reset_index(inplace=True)

    for i in range(len(spot_df)):
        current_candle = spot_df.iloc[i]
        date_str = current_candle['date_str']
        time_str = current_candle['time_str']
        
        # We need enough data ramp-up, so avoid nulls
        if pd.isna(current_candle['rsi']):
            continue
            
        if date_str != target_date:
            continue
            
        spot_close = current_candle['close']
        ema_fast = current_candle['ema_fast']
        ema_slow = current_candle['ema_slow']
        rsi = current_candle['rsi']
        atr = current_candle['atr']
        
        if in_position:
            # Check the EXACT REAL PREMUIM of the Option right now
            opt_df = options_dict.get(strike_key)
            if opt_df is None or time_str not in opt_df.index:
                # Missing minute data, hold position
                continue
                
            opt_candle = opt_df.loc[time_str]
            # Use the physical High/Low of the option to determine if target hit
            # Ensure we only take the first row if there are duplicate timestamps
            if isinstance(opt_candle, pd.DataFrame):
                opt_candle = opt_candle.iloc[0]
                
            opt_high = opt_candle['high']
            opt_low = opt_candle['low']
            opt_close = opt_candle['close']
            
            trade_closed = False
            exit_reason = ""
            exit_premium = 0.0
            
            # Since an Option is an Option, the High hits Target regardless of CE/PE
            if opt_high >= target_price:
                trade_closed = True
                exit_reason = "TARGET HIT"
                exit_premium = target_price
            elif opt_low <= sl_price:
                trade_closed = True
                exit_reason = "STOP LOSS HIT"
                exit_premium = sl_price
                
            # Hard EOD close at 15:20
            if "15:20" in time_str:
                trade_closed = True
                exit_reason = "EOD SQUARE OFF"
                exit_premium = opt_close # Take exactly whatever it is marked at
                
            if trade_closed:
                # APPLY SLIPPAGE (1.5%) AND FEES (Rs. 60 Round Trip [30 Entry / 30 Exit])
                net_exit_premium = exit_premium * 0.985 # 1.5% Exit Slippage
                gross_pnl = (net_exit_premium - entry_premium) * qty
                net_pnl = gross_pnl - 60.0 # Brokerage + Taxes approx
                
                capital += net_pnl
                trades.append({
                    "Reason": exit_reason,
                    "In Time": entry_time,
                    "Out Time": time_str,
                    "Type": trade_type,
                    "Strike": strike_key,
                    "In_Prem": round(entry_premium, 2),
                    "Out_Prem": round(exit_premium, 2),
                    "Qty": qty,
                    "P&L": round(net_pnl, 2),
                    "Bal": round(capital, 2)
                })
                in_position = False
                
        else:
            # Filters
            if "09:00" <= time_str < "10:00:00": continue # Time Gate
            if atr < 6.0: continue # Volatility Gate
            
            signal = None
            if ema_fast > ema_slow and rsi > 52:
                signal = "CE"
            elif ema_fast < ema_slow and rsi < 48:
                signal = "PE"
                
            if signal:
                atm = int(round(spot_close / 50) * 50)
                eval_key = f"{atm}_{signal}"
                
                # Verify we actually downloaded this strike
                if eval_key in options_dict:
                    opt_df = options_dict[eval_key]
                    
                    if time_str in opt_df.index:
                        opt_candle = opt_df.loc[time_str]
                        if isinstance(opt_candle, pd.DataFrame):
                            opt_candle = opt_candle.iloc[0]
                            
                        raw_entry_premium = opt_candle['close'] 
                        
                        # 0-DTE PREMIUM SAFETY FILTER!
                        # Do not trade if the option has decayed to penny stock levels (< Rs 30)
                        if float(raw_entry_premium) < 30.0:
                            continue
                            
                        in_position = True
                        entry_time = time_str
                        strike_key = eval_key
                        trade_type = f"BUY {signal}"
                        
                        # APPLY ENTRY SLIPPAGE (1.5%)
                        entry_premium = raw_entry_premium * 1.015 
                        
                        # Set Hard Technical Targets
                        target_price = entry_premium * 1.30
                        sl_price = entry_premium * 0.85

    if len(trades) > 0:
        trades_df = pd.DataFrame(trades)
        report_path = "TrueData_Backtest_Report.xlsx"
        trades_df.to_excel(report_path, index=False)
        print(f"\n[SUCCESS] REAL DATA BACKTEST COMPLETED!")
        print(f"Report saved to: {report_path}")
        print("\n--- INSTITUTIONAL REAL-NUMBER EXECUTION LOG ---")
        
        # Generate Markdown for the User dynamically
        md_table = "| In Time | Out Time | Type | Strike | In Prem (₹) | Out Prem (₹) | Reason | P&L (₹) | Balance (₹) |\n"
        md_table += "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        for _, row in trades_df.iterrows():
            md_table += f"| {row['In Time']} | {row['Out Time']} | **{row['Type']}** | {row['Strike']} | ₹{row['In_Prem']:.2f} | ₹{row['Out_Prem']:.2f} | {row['Reason']} | **{row['P&L']:.2f}** | {row['Bal']:.2f} |\n"
        
        md_path = "data/full_report.md"
        with open(md_path, "w", encoding="utf-8") as f:
             f.write(md_table)
             
        print(f"MARKDOWN TABLE READY AT: {md_path}")
        print(f"\nNet End of Day Profit/Loss: Rs. {capital - 100000.0:.2f}")
        print(f"Total True Trades: {len(trades)}")
    else:
        print("\nNo volatile trade criteria met inside downloaded data range.")

if __name__ == "__main__":
    run_real_backtest()
