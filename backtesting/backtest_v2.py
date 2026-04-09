"""
backtest_v2.py
==============
Definitive backtesting engine upgraded for PURE OPTIONS BUYING.
Features: 
- Historical Option Chain reconstruction (OI + PCR live calculation)
- Target adjustments on Expiry Days
- Theta Shield (Max holding time logic)
- Trailing Stop-Loss (Breakeven guarantee after +15%)
"""

import pandas as pd
import numpy as np
import logging
import requests
import json
import os
from datetime import datetime
from pathlib import Path

from upstox_auth import UpstoxAuth
from signal_engine import (
    SignalEngine, calc_ema, calc_atr,
    classify_dte_risk, calculate_position_size, 
    SL_PCT, TARGET_PCT
)

logging.basicConfig(level=logging.INFO, format='%(message)s')

def send_telegram(message):
    pass # Disabled for simple local testing output

def get_spot_data(from_date, to_date):
    auth = UpstoxAuth()
    access_token = auth.get_access_token()
    if not access_token: return None
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    url = f"https://api.upstox.com/v2/historical-candle/NSE_INDEX|Nifty 50/1minute/{to_date}/{from_date}"
    resp = requests.get(url, headers=headers)
    data = resp.json().get("data", {}).get("candles", [])
    if not data: return None
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "open_interest"])
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("Asia/Kolkata")
    df = df.sort_values(by="timestamp").reset_index(drop=True)
    df['time_str'] = df['timestamp'].dt.strftime('%H:%M:%S')
    df['date_str'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    return df

def load_options_data():
    options_dict = {}
    files_loaded = 0
    for p in Path("data").glob("NIFTY_*_1min.csv"):
        parts = p.name.split('_')
        if len(parts) >= 4:
            key = f"{parts[1]}_{parts[2]}"
            df = pd.read_csv(p)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
            df['time_str'] = df['timestamp'].dt.strftime('%H:%M:%S')
            df.set_index('time_str', inplace=True)
            options_dict[key] = df
            files_loaded += 1
    print(f"Loaded {files_loaded} Option Contracts.")
    return options_dict

def build_indicators(spot_df):
    spot_indexed = spot_df.set_index('timestamp')
    spot_5m = spot_indexed.resample('5min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    spot_5m['ema_fast'] = calc_ema(spot_5m['close'], 9)
    spot_5m['ema_slow'] = calc_ema(spot_5m['close'], 21)
    spot_5m['atr']      = calc_atr(spot_5m, 14)
    for col in ['ema_fast', 'ema_slow', 'atr']: spot_5m[col] = spot_5m[col].shift(1)

    spot_15m = spot_indexed.resample('15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    spot_15m['ema_fast'] = calc_ema(spot_15m['close'], 9)
    spot_15m['ema_fast'] = spot_15m['ema_fast'].shift(1)

    merged = spot_indexed.join(spot_5m[['ema_fast', 'ema_slow', 'atr']], how='left')
    for col in ['ema_fast', 'ema_slow', 'atr']: merged[col] = merged[col].ffill()
    merged.reset_index(inplace=True)
    return merged, spot_5m, spot_15m

def get_historical_chain_snapshot(options_dict, time_str, spot_close):
    """Reconstructs the PCR and Max Support/Resistance OI walls at this exact minute."""
    ce_oi, pe_oi, max_ce_oi, max_pe_oi, res_strike, sup_strike = 0, 0, 0, 0, 0, 0
    for key, df in options_dict.items():
        if time_str not in df.index: continue
        try:
            row = df.loc[time_str]
            if isinstance(row, pd.DataFrame): row = row.iloc[0]
            oi = float(row.get('open_interest', 0))
        except: continue
        
        strike, opt_type = int(key.split('_')[0]), key.split('_')[1]
        if opt_type == 'CE':
            ce_oi += oi
            if oi > max_ce_oi and strike >= spot_close - 50: # Allow slight ITM resistance
                max_ce_oi, res_strike = oi, strike
        elif opt_type == 'PE':
            pe_oi += oi
            if oi > max_pe_oi and strike <= spot_close + 50: # Allow slight ITM support
                max_pe_oi, sup_strike = oi, strike
                
    pcr = (pe_oi / ce_oi) if ce_oi > 0 else 1.0
    return pcr, sup_strike, res_strike


def run_backtest():
    TARGET_DATES    = ["2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27", "2026-03-30"]
    EXPIRY_DATE     = "2026-03-30"
    SPOT_FROM       = "2026-03-17"
    SPOT_TO         = "2026-03-31"
    INITIAL_CAPITAL = 100000.0
    LOT_SIZE        = 65
    BROKERAGE       = 30.0
    SLIPPAGE_PCT    = 0.015
    ATR_THRESHOLD   = 5.0
    TIME_GATE       = "10:00:00"

    print("=" * 70)
    print("  PURE OPTIONS BUYER BACKTEST - Option Chain AI")
    print("=" * 70)
    print(f"  Target Dates:     {TARGET_DATES[0]} to {TARGET_DATES[-1]}\n")

    spot_df = get_spot_data(SPOT_FROM, SPOT_TO)
    options_dict = load_options_data()
    merged_df, spot_5m, spot_15m = build_indicators(spot_df)

    engine = SignalEngine()
    trades = []
    capital = INITIAL_CAPITAL
    in_position = False
    pos = {}

    for i in range(len(merged_df)):
        row = merged_df.iloc[i]
        date_str, time_str, spot_close = row['date_str'], row['time_str'], row['close']
        if date_str not in TARGET_DATES: continue
        if pd.isna(row['atr']): continue

        # ===================================================================
        # IN POSITION: Monitor Exits (Trailing SL + Theta Shield)
        # ===================================================================
        if in_position:
            opt_df = options_dict.get(pos['strike_key'])
            if opt_df is None or time_str not in opt_df.index: continue
            opt_candle = opt_df.loc[time_str]
            if isinstance(opt_candle, pd.DataFrame): opt_candle = opt_candle.iloc[0]

            opt_high, opt_low, opt_close = float(opt_candle['high']), float(opt_candle['low']), float(opt_candle['close'])
            
            # Trailing SL Trigger (15% in profit -> move SL to breakeven + buffer)
            if opt_high >= pos['entry_premium'] * 1.15 and not pos.get('tsl_active'):
                pos['tsl_active'] = True
                pos['dynamic_sl'] = pos['entry_premium'] * 1.02 # Trail to 2% profit
            
            sl_prem = pos.get('dynamic_sl', pos['sl_prem'])
            target_prem = pos['target_prem']

            # Theta Shield: Max holding time based on DTE
            time_held_mins = (pd.to_datetime(f"{date_str} {time_str}") - pd.to_datetime(f"{date_str} {pos['entry_time']}")).total_seconds() / 60.0
            max_hold = 45 if pos['is_expiry_day'] else 120

            exit_reason, exit_premium = None, 0.0

            if opt_high >= target_prem:
                exit_reason, exit_premium = "TARGET HIT", target_prem
            elif opt_low <= sl_prem:
                exit_reason = "TRAILING STOP" if pos.get('tsl_active') else "STOP LOSS"
                exit_premium = sl_prem
            elif time_held_mins >= max_hold:
                exit_reason, exit_premium = "THETA SHIELD (Time Stop)", opt_close
            elif time_str >= "14:30:00":
                exit_reason, exit_premium = "14:30 EOD EXIT", opt_close

            if exit_reason:
                net_exit = exit_premium * (1 - SLIPPAGE_PCT)
                gross_pnl = (net_exit - pos['entry_premium']) * pos['qty']
                net_pnl = gross_pnl - (BROKERAGE * 2)
                capital += net_pnl

                trades.append({
                    "Date": date_str, "In": pos['entry_time'], "Out": time_str,
                    "Type": pos['trade_type'], "Strike": pos['strike_key'],
                    "In_Prem": round(pos['entry_premium'], 2), "Out_Prem": round(exit_premium, 2),
                    "Qty": pos['qty'], "Net_PnL": round(net_pnl, 2), "Balance": round(capital, 2),
                    "Exit_Reason": exit_reason, "DTE_Risk": pos['dte_risk'],
                    "Reasons": " | ".join(pos['reasons'])
                })
                in_position, pos = False, {}

        # ===================================================================
        # NOT IN POSITION: Look for entries using Option Chain
        # ===================================================================
        else:
            if time_str < TIME_GATE or time_str >= "14:00:00" or row['atr'] < ATR_THRESHOLD:
                continue

            ts = row['timestamp']
            ctx_5m = spot_5m.loc[spot_5m.index <= ts].tail(30).copy()
            ctx_15m = spot_15m.loc[spot_15m.index <= ts].tail(20).copy()
            if len(ctx_5m) < 15: continue

            # Reconstruct Live Option Chain snapshot
            pcr, sup_strike, res_strike = get_historical_chain_snapshot(options_dict, time_str, spot_close)

            # Evaluate through new Signal Engine
            signal = engine.evaluate(
                spot_5m_df=ctx_5m, spot_15m_df=ctx_15m, pcr=pcr,
                support=sup_strike, resistance=res_strike, spot_close=spot_close,
                expiry_date=EXPIRY_DATE, current_date=date_str
            )

            if signal['direction'] is None: continue

            direction = signal['direction']
            atm = int(round(spot_close / 50) * 50)
            eval_key = f"{atm}_{direction}"

            opt_df = options_dict.get(eval_key)
            if opt_df is None or time_str not in opt_df.index: continue
            
            raw_premium = float(opt_df.loc[time_str]['close']) if isinstance(opt_df.loc[time_str], pd.Series) else float(opt_df.loc[time_str].iloc[0]['close'])
            if raw_premium < 20: continue

            entry_premium = raw_premium * (1 + SLIPPAGE_PCT)
            
            # Tighter SL on 0-DTE (20% instead of 30%)
            sl_pct = 0.20 if signal['is_expiry_day'] else SL_PCT
            # Easier Target on 0-DTE (35% instead of 50%)
            tgt_pct = 0.35 if signal['is_expiry_day'] else TARGET_PCT

            sl_prem = entry_premium * (1 - sl_pct)
            tgt_prem = entry_premium * (1 + tgt_pct)

            qty = calculate_position_size(capital, entry_premium, sl_prem, LOT_SIZE, signal['is_expiry_day'])

            in_position = True
            pos = {
                'entry_time': time_str, 'strike_key': eval_key, 'trade_type': f"BUY {direction}",
                'entry_premium': entry_premium, 'qty': qty, 'sl_prem': sl_prem, 'target_prem': tgt_prem,
                'reasons': signal['reasons'], 'dte_risk': signal['dte_risk'], 'is_expiry_day': signal['is_expiry_day']
            }

    # -----------------------------------------------------------------------
    # REPORTING
    # -----------------------------------------------------------------------
    if not trades:
        print("No trades triggered under new strict logic.")
        return

    df_t = pd.DataFrame(trades)
    winners, losers = len(df_t[df_t['Net_PnL'] > 0]), len(df_t[df_t['Net_PnL'] <= 0])
    win_rate = (winners / len(df_t)) * 100
    pnl = df_t['Net_PnL'].sum()

    print("\n" + "=" * 70)
    print("  PURE OPTIONS BUYER STRATEGY RESULTS")
    print("=" * 70)
    print(f"  Total Trades:  {len(df_t)}")
    print(f"  Win Rate:      {win_rate:.1f}% ({winners}W / {losers}L)")
    print(f"  Net P&L:       Rs. {pnl:,.2f}")
    print(f"  Capital:       Rs. {capital:,.2f}")
    print("=" * 70)

    for idx, r in df_t.iterrows():
        print(f"  {r['Date']} | {r['In'][:5]} -> {r['Out'][:5]} | {r['Type']} {r['Strike']} | "
              f"P&L: {r['Net_PnL']:>8.0f} | {r['Exit_Reason']}")

if __name__ == "__main__":
    run_backtest()
