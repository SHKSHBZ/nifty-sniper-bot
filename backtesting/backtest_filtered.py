import requests
import pandas as pd
import numpy as np
import logging
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

    # Fetch last 5 days to prime calculations
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

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "open_interest"])
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("Asia/Kolkata")
    df = df.sort_values(by="timestamp").reset_index(drop=True)
    return df

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0
    tr_list = []
    for j in range(1, len(closes)):
        tr1 = highs[j] - lows[j]
        tr2 = abs(highs[j] - closes[j-1])
        tr3 = abs(lows[j] - closes[j-1])
        tr_list.append(max(tr1, tr2, tr3))
    return sum(tr_list[-period:]) / period

def run_backtest():
    print("Fetching historical market data...")
    df = get_today_nifty_candles()
    if df is None:
        return
        
    print(f"Loaded {len(df)} candles for technical analysis.")
    selector = StrikeSelector(use_ollama=False)
    
    trades = []
    capital = 100000.0  # Rs 1 Lakh starting capital
    qty = 65 # Updated Nifty Lot Size as per user spec
    
    in_position = False
    entry_price = 0
    entry_time = None
    target_price = 0
    sl_price = 0
    trade_type = ""
    strike_price = 0
    buy_premium = 150 

    closes = df['close'].tolist()
    highs = df['high'].tolist()
    lows = df['low'].tolist()
    
    today_str = "2026-04-06" # Targeting the full previous day
    
    for i in range(21, len(df)):
        current_candle = df.iloc[i]
        timestamp = current_candle['timestamp']
        spot_close = current_candle['close']
        spot_high = current_candle['high']
        spot_low = current_candle['low']
        
        window_closes = closes[:i+1]
        window_highs = highs[:i+1]
        window_lows = lows[:i+1]
        
        rsi = selector.calculate_rsi(window_closes, 14)
        ema_fast = selector.calculate_ema(window_closes, 9)
        ema_slow = selector.calculate_ema(window_closes, 21)
        atr = calculate_atr(window_highs, window_lows, window_closes, 14)
        
        if in_position:
            # Approximate Option Premium Deltas with Spot
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
                    exit_premium = target_price
                elif spot_low <= spot_sl:
                    trade_closed = True
                    exit_reason = "STOP LOSS HIT"
                    exit_premium = sl_price
            else: # BUY PE
                if spot_low <= spot_target:
                    trade_closed = True
                    exit_reason = "TARGET HIT"
                    exit_premium = target_price
                elif spot_high >= spot_sl:
                    trade_closed = True
                    exit_reason = "STOP LOSS HIT"
                    exit_premium = sl_price
                    
            if trade_closed:
                pnl = (exit_premium - buy_premium) * qty
                capital += pnl
                if str(timestamp.date()) == today_str:
                    trades.append({
                        "Ext Reason": exit_reason,
                        "Entry Time": entry_time.strftime("%H:%M:%S"),
                        "Exit Time": timestamp.strftime("%H:%M:%S"),
                        "Type": trade_type,
                        "Strike": strike_price,
                        "Entry Prem": round(buy_premium, 2),
                        "Exit Prem": round(exit_premium, 2),
                        "P&L (Rs)": round(pnl, 2),
                        "Bal (Rs)": round(capital, 2)
                    })
                in_position = False
                
        else:
            if str(timestamp.date()) != today_str:
                continue
                
            # ==================================================
            # ALGORITHMIC MARKET FILTERS (SIDEWAYS AVOIDANCE)
            # ==================================================
            
            # FILER 1: No Trades Before 10:00 AM (Skip morning whipsaws)
            if timestamp.hour < 10:
                continue
                
            # FILTER 2: ATR Threshold (Skip low-volatility flattened markets)
            if atr < 6.0:
                continue
                
            # Normal Entry Logic
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
                qty = 65
                
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
                qty = 65

    if in_position and trades and str(timestamp.date()) == today_str:
        pnl = (sl_price - buy_premium) * qty 
        capital += pnl
        trades.append({
            "Ext Reason": "EOD CLOSE",
            "Entry Time": entry_time.strftime("%H:%M:%S"),
            "Exit Time": "15:25:00",
            "Type": trade_type,
            "Strike": strike_price,
            "Entry Prem": round(buy_premium, 2),
            "Exit Prem": round(buy_premium*0.9, 2),
            "P&L (Rs)": round(pnl, 2),
            "Bal (Rs)": round(capital, 2)
        })

    if len(trades) > 0:
        trades_df = pd.DataFrame(trades)
        excel_path = "Intraday_Backtest_Filtered_Report.xlsx"
        trades_df.to_excel(excel_path, index=False)
        print(f"\n[SUCCESS] FILTERED EXCEL REPORT SAVED: {excel_path}")
        print("\n--- TEXT REPORT FOR WORD ---")
        print(trades_df.to_string(index=False))
        print(f"\nNet End of Day Profit/Loss: Rs. {capital - 100000.0:.2f}")
        print(f"Total Trades Taken (After Filtering): {len(trades)}")
    else:
        print("\nNo volatile trade criteria met. Filters successfully kept bot out of sideways market today!")

if __name__ == "__main__":
    run_backtest()
