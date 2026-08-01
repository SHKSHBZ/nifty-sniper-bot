"""
Full Backtest: PriceActionBot with multi-position (up to 5 concurrent)
Period: May 22 - June 3, 2026
Uses real Upstox candles + saved option chain data.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests, pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

# --- Fetch candles from Upstox ---
from upstox_auth import UpstoxAuth
auth = UpstoxAuth()
if not auth.is_session_valid():
    print("AUTH FAILED")
    exit()

headers = {"Authorization": f"Bearer {auth.access_token}", "Accept": "application/json"}
url = "https://api.upstox.com/v2/historical-candle/NSE_INDEX%7CNifty%2050/1minute/2026-06-03/2026-05-19"
print("Fetching 1-min candles May 19 - Jun 3...")
resp = requests.get(url, headers=headers, timeout=30)
data = resp.json().get("data", {}).get("candles", [])
print(f"Got {len(data)} 1-min candles")

# Parse
raw = []
for c in data:
    ts = datetime.fromisoformat(c[0].replace("+05:30", ""))
    raw.append({"ts": ts, "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])})
raw.sort(key=lambda x: x["ts"])

# Resample to 5-min
buckets = defaultdict(list)
for c in raw:
    bk = c["ts"].replace(minute=(c["ts"].minute // 5) * 5, second=0, microsecond=0)
    buckets[bk].append(c)

all_candles = []
for bk in sorted(buckets):
    g = buckets[bk]
    all_candles.append({"ts": bk, "o": g[0]["o"], "h": max(x["h"] for x in g), "l": min(x["l"] for x in g), "c": g[-1]["c"]})

print(f"Resampled to {len(all_candles)} 5-min candles")

# --- Setup ---
STRIKE_STEP = 50
LOT_SIZE = 65
BASE_LOTS = 5
MAX_LOSS = 8000
MAX_CONCURRENT = 5

from signal_engine import SignalEngine, classify_dte_risk

def backtest_day(day_str, all_candles, prior_candles):
    """Run backtest for a single day with multi-position support."""
    day_candles = [c for c in all_candles if str(c["ts"].date()) == day_str]
    if len(day_candles) < 20:
        return [], 0, False
    
    # Load focus zone for option premiums
    fz_path = f"logs/focus_zone_nifty_{day_str}.csv"
    if not os.path.exists(fz_path):
        print(f"  SKIP: no focus zone data for {day_str}")
        return [], 0, False
    
    fz = pd.read_csv(fz_path)
    fz["timestamp"] = pd.to_datetime(fz["timestamp"])
    
    def get_premium(ts, strike, opt_type):
        idx = (fz["timestamp"] - ts).abs().argsort()[:1]
        row = fz.iloc[idx]
        row = row[row["strike"] == strike]
        if row.empty:
            return 0
        col = "ce_ltp" if opt_type == "CE" else "pe_ltp"
        val = row[col].iloc[0]
        return float(val) if pd.notna(val) else 0
    
    bot = PriceActionBot(fetcher=None)
    
    # Properly bootstrap 3TF: pre-load candle deques from prior days
    # This avoids the single-price on-the-fly bootstrap bug
    t3 = bot.tracker_3tf
    
    # Build 15-min candles from 5-min (take every 3rd close)
    all_prior = prior_candles + day_candles[:20]
    for c in all_prior:
        t3.m15_candles.append(c["c"])
    
    # Build 1H candles (every 12th 5-min close)
    for i in range(0, len(all_prior), 12):
        t3.h1_candles.append(all_prior[min(i, len(all_prior)-1)]["c"])
    
    # Build 4H candles (every 48th 5-min close)  
    for i in range(0, len(all_prior), 48):
        t3.h4_candles.append(all_prior[min(i, len(all_prior)-1)]["c"])
    
    t3._recalculate_all_emas()
    t3.is_bootstrapped = True
    print(f"  3TF bootstrapped: m15={len(t3.m15_candles)} h1={len(t3.h1_candles)} h4={len(t3.h4_candles)}")
    h4, h1, m15 = t3.get_trends(all_prior[-1]["c"])
    print(f"  Trend: 4H={h4} 1H={h1} 15M={m15}")
    
    trades = []
    open_positions = []  # list of position dicts
    blocked = False
    signals_seen = 0
    
    for i, c in enumerate(day_candles):
        if i < 10:
            bot._candles_5m.append(c)
            continue
        if blocked:
            continue
        
        bot._candles_5m.append(c)
        signal = bot.evaluate(now=c["ts"], spot=c["c"])
        
        if signal["direction"]:
            signals_seen += 1
        
        # --- Monitor all open positions ---
        for pos in list(open_positions):
            exit_p = get_premium(c["ts"], pos["strike"], pos["dir"])
            if exit_p <= 0:
                continue
            
            should_exit = False
            exit_reason = ""
            final_exit = exit_p
            
            if exit_p <= pos["sl"]:
                should_exit = True
                exit_reason = "SL"
                final_exit = pos["sl"]
            elif i == len(day_candles) - 1:
                should_exit = True
                exit_reason = "EOD"
            
            if should_exit:
                pnl = (final_exit - pos["entry"]) * pos["qty"]
                pos["exit"] = final_exit
                pos["pnl"] = round(pnl, 0)
                pos["reason"] = exit_reason
                pos["exit_time"] = str(c["ts"].time())
                trades.append(pos)
                open_positions.remove(pos)
                bot.on_trade_closed(pnl)
                
                total_day_pnl = sum(t["pnl"] for t in trades)
                if total_day_pnl <= -MAX_LOSS:
                    blocked = True
        
        # --- Enter new position if room ---
        if signal["direction"] and len(open_positions) < MAX_CONCURRENT and not blocked:
            d = signal["direction"]
            htf = signal.get("htf_state", "neutral")
            if htf == "oppose":
                continue
            
            lots = BASE_LOTS if htf == "confirm" else max(1, round(BASE_LOTS * 0.5))
            atm = int(round(c["c"] / STRIKE_STEP) * STRIKE_STEP)
            strike = atm - STRIKE_STEP if d == "CE" else atm + STRIKE_STEP
            entry = get_premium(c["ts"], strike, d)
            if entry <= 0:
                continue
            
            sl_pct = 0.12 if entry >= 100 else (0.15 if entry >= 70 else (0.20 if entry >= 40 else 0.25))
            
            pos = {
                "dir": d, "strike": strike, "entry": entry,
                "qty": lots * LOT_SIZE, "sl": entry * (1 - sl_pct),
                "lots": lots, "htf": htf,
                "time": str(c["ts"].time()), "spot": c["c"],
            }
            open_positions.append(pos)
            bot._candles_since_last_signal = 0
    
    # Close any remaining positions at EOD
    last_c = day_candles[-1]
    for pos in open_positions:
        exit_p = get_premium(last_c["ts"], pos["strike"], pos["dir"])
        if exit_p <= 0:
            exit_p = pos["sl"]
        pnl = (exit_p - pos["entry"]) * pos["qty"]
        pos["exit"] = exit_p
        pos["pnl"] = round(pnl, 0)
        pos["reason"] = "EOD"
        pos["exit_time"] = str(last_c["ts"].time())
        trades.append(pos)
    
    return trades, signals_seen, blocked


# --- Run all days ---
test_days = ["2026-05-22", "2026-05-25", "2026-05-26", "2026-05-27", "2026-06-01", "2026-06-02"]
prior_candles = [c for c in all_candles if str(c["ts"].date()) < "2026-05-22"]

all_results = []
grand_total = 0
grand_wins = 0
grand_losses = 0

for day in test_days:
    trades, signals, blocked = backtest_day(day, all_candles, prior_candles)
    day_pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] < 0)
    
    grand_total += day_pnl
    grand_wins += wins
    grand_losses += losses
    
    ce = sum(t["pnl"] for t in trades if t["dir"] == "CE")
    pe = sum(t["pnl"] for t in trades if t["dir"] == "PE")
    
    print(f"\n{day}: {signals} signals, {len(trades)} trades, P&L=Rs.{day_pnl:,.0f}, Win={wins}/{len(trades)} {'BLOCKED' if blocked else ''}")
    print(f"  CE=Rs.{ce:,.0f}, PE=Rs.{pe:,.0f}")
    for t in trades:
        print(f"  {t['time']} {t['dir']} {t['strike']} e={t['entry']:.1f} x={t['exit']:.1f} pnl={t['pnl']:,.0f} [{t['reason']}] htf={t['htf']} lots={t['lots']}")
    
    all_results.append({"day": day, "pnl": day_pnl, "trades": len(trades), "wins": wins, "losses": losses})
    prior_candles = [c for c in all_candles if str(c["ts"].date()) <= day]

total_trades = sum(r["trades"] for r in all_results)
print(f"\n{'='*60}")
print(f"GRAND TOTAL: {total_trades} trades, P&L=Rs.{grand_total:,.0f}")
print(f"Wins={grand_wins}, Losses={grand_losses}, Win rate={grand_wins/total_trades*100:.1f}%" if total_trades else "No trades")
print(f"CE=Rs.{sum(t['pnl'] for r in all_results for t in []):,.0f}")  # placeholder
