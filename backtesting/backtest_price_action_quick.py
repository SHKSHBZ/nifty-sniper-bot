"""
Quick Backtest: PriceActionBot on May 22 - June 2, 2026
Fetches 5-min candles from Upstox, runs engine candle-by-candle, simulates trades.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import requests
from datetime import datetime, timedelta

# --- Auth ---
from upstox_auth import UpstoxAuth
auth = UpstoxAuth()
if not auth.is_session_valid():
    print("AUTH FAILED")
    exit()

token = auth.access_token
headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
base_url = "https://api.upstox.com/v2/historical-candle/NSE_INDEX%7CNifty%2050/1minute"

# --- Fetch 1-min candles ---
print("Fetching 1-min candles from Upstox (May 19 - Jun 2, 2026)...")
url = f"{base_url}/2026-06-02/2026-05-19"
resp = requests.get(url, headers=headers, timeout=30)
if resp.status_code != 200:
    print(f"API Error: {resp.status_code} {resp.text[:200]}")
    exit()

data = resp.json().get("data", {}).get("candles", [])
if not data:
    print("No candle data returned")
    exit()

# Parse 1-min candles
raw_1m = []
for c in data:
    ts_str = c[0].replace("+05:30", "")
    ts = datetime.fromisoformat(ts_str)
    raw_1m.append({
        "ts": ts, "o": float(c[1]), "h": float(c[2]),
        "l": float(c[3]), "c": float(c[4])
    })
raw_1m.sort(key=lambda x: x["ts"])
print(f"Fetched {len(raw_1m)} 1-min candles from {raw_1m[0]['ts']} to {raw_1m[-1]['ts']}")

# Resample 1-min -> 5-min candles
from collections import defaultdict
buckets = defaultdict(list)
for c in raw_1m:
    bucket_key = c["ts"].replace(minute=(c["ts"].minute // 5) * 5, second=0, microsecond=0)
    buckets[bucket_key].append(c)

candles_raw = []
for bk in sorted(buckets):
    group = buckets[bk]
    candles_raw.append({
        "ts": bk,
        "o": group[0]["o"],
        "h": max(g["h"] for g in group),
        "l": min(g["l"] for g in group),
        "c": group[-1]["c"],
    })
print(f"Resampled to {len(candles_raw)} 5-min candles from {candles_raw[0]['ts']} to {candles_raw[-1]['ts']}")

# --- Run PriceActionBot ---
from signal_engine import PriceActionBot

bot = PriceActionBot(fetcher=None)

trades = []
in_position = False
position = {}
daily_pnl = 0.0
current_date = None
MAX_LOSS = 8000
BLOCKED = False
results_by_day = []

for i, candle in enumerate(candles_raw):
    day = candle["ts"].date()

    # New day
    if day != current_date:
        if current_date is not None:
            results_by_day.append({
                "date": str(current_date), "pnl": round(daily_pnl, 0),
                "trades": len([t for t in trades if t["date"] == str(current_date)])
            })
        current_date = day
        daily_pnl = 0.0
        BLOCKED = False
        bot.reset_daily_state()
        if in_position:
            exit_price = candle["c"]
            pnl = (exit_price - position["entry"]) * position["qty"]
            position["pnl"] = round(pnl, 0)
            position["exit_reason"] = "EOD_OVERLAP"
            trades.append(position)
            in_position = False

    if i < 20:
        continue

    if BLOCKED:
        continue

    # Feed candle
    bot._candles_5m.append(candle)

    # Evaluate signal
    spot = candle["c"]
    signal = bot.evaluate(now=candle["ts"], spot=spot)

    if signal["direction"] and not in_position:
        direction = signal["direction"]
        htf_state = signal.get("htf_state", "neutral")

        base_lots = 5
        if htf_state == "confirm":
            lots = base_lots
        elif htf_state == "neutral":
            lots = max(1, round(base_lots * 0.5))
        else:
            continue

        entry_price = candle["c"]
        qty = lots * 65  # NIFTY lot size

        if entry_price >= 100:
            sl_pct = 0.12
        elif entry_price >= 70:
            sl_pct = 0.15
        elif entry_price >= 40:
            sl_pct = 0.20
        else:
            sl_pct = 0.25

        sl_price = entry_price * (1 - sl_pct)

        position = {
            "direction": direction, "entry": entry_price, "qty": qty,
            "sl": sl_price, "lots": lots, "htf": htf_state,
            "entry_time": str(candle["ts"].time()), "date": str(day),
            "event": signal["reasons"][0][:100] if signal["reasons"] else "signal"
        }
        in_position = True
        bot._candles_since_last_signal = 0

    # Exit check
    if in_position:
        exit_price = None
        exit_reason = ""

        if candle["l"] <= position["sl"]:
            exit_price = position["sl"]
            exit_reason = "SL"
        elif i == len(candles_raw) - 1 or candles_raw[i + 1]["ts"].date() != day:
            exit_price = candle["c"]
            exit_reason = "EOD"

        if exit_price:
            pnl = (exit_price - position["entry"]) * position["qty"]
            position["exit"] = exit_price
            position["pnl"] = round(pnl, 0)
            position["exit_reason"] = exit_reason
            position["exit_time"] = str(candle["ts"].time())
            trades.append(position)
            daily_pnl += pnl
            bot.on_trade_closed(pnl)
            if daily_pnl <= -MAX_LOSS:
                BLOCKED = True
            in_position = False

# Final day
if current_date:
    results_by_day.append({
        "date": str(current_date), "pnl": round(daily_pnl, 0),
        "trades": len([t for t in trades if t["date"] == str(current_date)])
    })

# --- Print results ---
total_pnl = sum(t["pnl"] for t in trades)
wins = sum(1 for t in trades if t["pnl"] > 0)
losses = sum(1 for t in trades if t["pnl"] < 0)
blocked_days = sum(1 for d in results_by_day if d["pnl"] <= -MAX_LOSS)

print(f"\n{'='*60}")
print(f"PriceActionBot Backtest: 2026-05-22 to 2026-06-02")
print(f"{'='*60}")
print(f"Total trades: {len(trades)}")
if trades:
    print(f"Wins: {wins}, Losses: {losses}, Win rate: {wins/len(trades)*100:.1f}%")
print(f"Total P&L: Rs. {total_pnl:,.0f}")
print(f"Days blocked by loss: {blocked_days}")

ce_pnl = sum(t["pnl"] for t in trades if t["direction"] == "CE")
pe_pnl = sum(t["pnl"] for t in trades if t["direction"] == "PE")
print(f"CE P&L: Rs. {ce_pnl:,.0f}, PE P&L: Rs. {pe_pnl:,.0f}")
print()

for d in results_by_day:
    day_trades = [t for t in trades if t["date"] == d["date"]]
    blocked = "BLOCKED" if d["pnl"] <= -MAX_LOSS else ""
    print(f"  {d['date']}: P&L={d['pnl']:>8,.0f} | Trades={len(day_trades)} {blocked}")

print(f"\n--- All Trades ---")
for t in trades:
    print(f"  {t['date']} {t['entry_time']} {t['direction']:<3} entry={t['entry']:>7.1f} exit={t['exit']:>7.1f} pnl={t['pnl']:>8,.0f} [{t['exit_reason']:<4}] htf={t['htf']:<8} lots={t['lots']}")

# Compare
try:
    with open("backtest_final.json") as f:
        ref = json.load(f)
    ref_pnl = ref["summary"]["total_pnl"]
    ref_trades = ref["summary"]["total_trades"]
    print(f"\n--- Comparison vs backtest_final.json ---")
    print(f"Reference: {ref_trades} trades, P&L=Rs.{ref_pnl:,}")
    print(f"This run:   {len(trades)} trades, P&L=Rs.{total_pnl:,.0f}")
except Exception as e:
    print(f"\n[Note] Could not load backtest_final.json: {e}")
