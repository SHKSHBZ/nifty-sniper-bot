import requests
import pandas as pd
import numpy as np
import logging
import time
from datetime import datetime
from upstox_auth import UpstoxAuth
from strike_selector import StrikeSelector

logging.basicConfig(level=logging.INFO, format='%(message)s')

def get_today_nifty_candles():
    auth = UpstoxAuth()
    access_token = auth.get_access_token()
    if not access_token:
        print("Auth failed.")
        return None
        
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    # Fetch last 5 days to prime the RSI and EMA calculations
    to_date = "2026-04-08"
    from_date = "2026-04-01"
    url = f"https://api.upstox.com/v2/historical-candle/NSE_INDEX|Nifty 50/1minute/{to_date}/{from_date}"
    resp = requests.get(url, headers=headers)
    
    if resp.status_code != 200:
        print(f"Error fetching data: {resp.text}")
        return None
        
    data = resp.json().get("data", {}).get("candles", [])
    if not data:
        print("No historical data found.")
        return None

    # columns: timestamp, open, high, low, close, vol, oi
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "open_interest"])
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("Asia/Kolkata")
    df = df.sort_values(by="timestamp").reset_index(drop=True)
    return df

def run_backtest():
    print("Fetching today's market data...")
    df = get_today_nifty_candles()
    if df is None:
        return
        
    print(f"Loaded {len(df)} candles for technical analysis.")
    selector = StrikeSelector(use_ollama=False)
    
    trades = []
    capital = 100000.0  # Rs 1 Lakh starting capital
    lot_size = 50
    
    # State tracking
    in_position = False
    entry_price = 0
    entry_time = None
    target_price = 0
    sl_price = 0
    trade_type = ""
    strike_price = 0
    option_type = ""
    qty = 50
    buy_premium = 150 # Baseline approximate ATM premium

    # Indicators calculation
    closes = df['close'].tolist()
    
    # Upstox doesn't store intraday data for the current active day until midnight.
    # We will grab the most recent full finished day: April 6, 2026
    today_str = "2026-04-06"
    
    for i in range(21, len(df)):
        current_candle = df.iloc[i]
        timestamp = current_candle['timestamp']
        spot_close = current_candle['close']
        spot_high = current_candle['high']
        spot_low = current_candle['low']
        
        # Calculate lagging indicators
        window_closes = closes[:i+1]
        rsi = selector.calculate_rsi(window_closes, 14)
        ema_fast = selector.calculate_ema(window_closes, 9)
        ema_slow = selector.calculate_ema(window_closes, 21)
        
        if in_position:
            # Check for Exit (Target or Stop Loss)
            # Since we trade Option, we approximate Option Delta = 0.5
            # Spot Move = (Option Target / 0.5)
            # So if target is +30% on Rs. 150 option -> 45 Rs profit.
            # Spot needs to move 45 / 0.5 = 90 Points in our favor.
            spot_points_needed = (target_price - buy_premium) / 0.5
            spot_target = entry_price + spot_points_needed if trade_type == "BUY CE" else entry_price - spot_points_needed
            
            spot_sl_needed = (buy_premium - sl_price) / 0.5
            spot_sl = entry_price - spot_sl_needed if trade_type == "BUY CE" else entry_price + spot_sl_needed

            trade_closed = False
            exit_reason = ""
            exit_spot = 0
            exit_premium = 0
            
            if trade_type == "BUY CE":
                if spot_high >= spot_target:
                    trade_closed = True
                    exit_reason = "TARGET HIT"
                    exit_spot = spot_target
                    exit_premium = target_price
                elif spot_low <= spot_sl:
                    trade_closed = True
                    exit_reason = "STOP LOSS HIT"
                    exit_spot = spot_sl
                    exit_premium = sl_price
            else: # BUY PE
                if spot_low <= spot_target:
                    trade_closed = True
                    exit_reason = "TARGET HIT"
                    exit_spot = spot_target
                    exit_premium = target_price
                elif spot_high >= spot_sl:
                    trade_closed = True
                    exit_reason = "STOP LOSS HIT"
                    exit_spot = spot_sl
                    exit_premium = sl_price
                    
            if trade_closed:
                pnl = (exit_premium - buy_premium) * qty
                capital += pnl
                if str(timestamp.date()) == today_str:
                    trades.append({
                        "Entry Time": entry_time.strftime("%H:%M:%S"),
                        "Exit Time": timestamp.strftime("%H:%M:%S"),
                        "Type": trade_type,
                        "Strike": strike_price,
                        "Entry Premium": round(buy_premium, 2),
                        "Exit Premium": round(exit_premium, 2),
                        "Reason": exit_reason,
                        "P&L (Rs)": round(pnl, 2),
                        "Balance (Rs)": round(capital, 2)
                    })
                in_position = False
                
        else:
            # Check for Entry Signals (Only taking trades on today's date for this test)
            if str(timestamp.date()) != today_str:
                continue
                
            # Golden Cross (Fast > Slow) + Momentum
            if ema_fast > ema_slow and rsi > 52:
                atm = int(round(spot_close / 50) * 50)
                in_position = True
                entry_time = timestamp
                entry_price = spot_close
                trade_type = "BUY CE"
                strike_price = atm
                buy_premium = 150 # Simulated starting premium for ATM
                target_price = buy_premium * 1.30 # +30% Target
                sl_price = buy_premium * 0.85 # -15% SL
                
            # Death Cross (Fast < Slow) + Downward Momentum
            elif ema_fast < ema_slow and rsi < 48:
                atm = int(round(spot_close / 50) * 50)
                in_position = True
                entry_time = timestamp
                entry_price = spot_close
                trade_type = "BUY PE"
                strike_price = atm
                buy_premium = 150 
                target_price = buy_premium * 1.30
                sl_price = buy_premium * 0.85

    # Force close any open position at end of day 3:25 PM
    if in_position and trades and str(timestamp.date()) == today_str:
        # Approximate close
        pnl = (sl_price - buy_premium) * qty # Worst case assumption for end of day close out
        capital += pnl
        trades.append({
            "Entry Time": entry_time.strftime("%H:%M:%S"),
            "Exit Time": "15:25:00",
            "Type": trade_type,
            "Strike": strike_price,
            "Entry Premium": round(buy_premium, 2),
            "Exit Premium": round(buy_premium*0.9, 2),
            "Reason": "EOD SQUARE OFF",
            "P&L (Rs)": round(pnl, 2),
            "Balance (Rs)": round(capital, 2)
        })

    # Wrap up report
    if len(trades) > 0:
        trades_df = pd.DataFrame(trades)
        # Export to Excel format exactly as requested by user
        excel_path = "Intraday_Backtest_Report.xlsx"
        trades_df.to_excel(excel_path, index=False)
        print(f"\n[SUCCESS] EXCEL REPORT SAVED: {excel_path}")
        
        # Now print markdown version
        print("\n--- TEXT REPORT FOR WORD ---")
        print(trades_df.to_string(index=False))
        print(f"\nNet End of Day Profit/Loss: Rs. {capital - 100000.0:.2f}")
        print(f"Total Trades Taken: {len(trades)}")
    else:
        print("\nNo trade criteria met today. Market might have been entirely sideways without meeting RSI/EMA volatility requirements.")


if __name__ == "__main__":
    run_backtest()
