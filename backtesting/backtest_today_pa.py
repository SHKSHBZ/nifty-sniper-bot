"""
Backtest PriceActionBot on June 4, 2026 using saved macro CSV + focus zone options.
Upstox historical API doesn't have today's candles yet, but our CSVs have everything.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

# --- Load spot data from macro CSV ---
print("Loading macro spot data...")
macro = pd.read_csv("logs/macro_nifty_2026-06-04.csv")
macro["timestamp"] = pd.to_datetime(macro["timestamp"])
print(f"Loaded {len(macro)} spot ticks: {macro['timestamp'].iloc[0]} to {macro['timestamp'].iloc[-1]}")

# Build 1-min candles
raw_1m = []
for _, row in macro.iterrows():
    raw_1m.append({"ts": row["timestamp"], "o": row["spot"], "h": row["spot"], "l": row["spot"], "c": row["spot"]})

# Resample to 5-min (using actual OHLC from ticks)
buckets = defaultdict(list)
for c in raw_1m:
    bk = c["ts"].replace(minute=(c["ts"].minute // 5) * 5, second=0, microsecond=0)
    buckets[bk].append(c)

candles_5m = []
for bk in sorted(buckets):
    g = buckets[bk]
    candles_5m.append({"ts": bk, "o": g[0]["o"], "h": max(x["h"] for x in g), "l": min(x["l"] for x in g), "c": g[-1]["c"]})
print(f"Built {len(candles_5m)} 5-min candles: {candles_5m[0]['ts'].time()} to {candles_5m[-1]['ts'].time()}")

# --- Load focus zone data for option premiums ---
print("Loading focus zone options data...")
fz = pd.read_csv("logs/focus_zone_nifty_2026-06-04.csv")
fz["timestamp"] = pd.to_datetime(fz["timestamp"])
print(f"Loaded {len(fz)} option snapshots, {fz['strike'].nunique()} strikes")

def get_option_premium(ts, strike, opt_type):
    """Find the closest option premium for a given timestamp, strike, and type."""
    # Find nearest timestamp
    fz_sorted = fz.iloc[(fz["timestamp"] - ts).abs().argsort()[:1]]
    row = fz_sorted[fz_sorted["strike"] == strike]
    if row.empty:
        return 0.0
    col = "ce_ltp" if opt_type == "CE" else "pe_ltp"
    return float(row[col].iloc[0]) if pd.notna(row[col].iloc[0]) else 0.0

# --- Bootstrap 3TF ---
from signal_engine import PriceActionBot
bot = PriceActionBot(fetcher=None)
print("Bootstrapping 3TF...")
for c in candles_5m[:50]:
    bot.tracker_3tf.update(c["ts"], c["c"])
print(f"3TF: {len(bot.tracker_3tf.m15_candles)} candles seeded")

# --- Run backtest ---
STRIKE_STEP = 50
LOT_SIZE = 65
BASE_LOTS = 5
MAX_LOSS = 8000

trades = []
in_position = False
position = {}
daily_pnl = 0.0
BLOCKED = False

for i, candle in enumerate(candles_5m):
    if i < 20:
        continue
    if BLOCKED:
        continue
    if candle["ts"].time() < datetime.strptime("09:20", "%H:%M").time():
        continue

    bot._candles_5m.append(candle)
    spot = candle["c"]
    signal = bot.evaluate(now=candle["ts"], spot=spot)

    if signal["direction"] and not in_position:
        direction = signal["direction"]
        htf_state = signal.get("htf_state", "neutral")

        if htf_state == "confirm":
            lots = BASE_LOTS
        elif htf_state == "neutral":
            lots = max(1, round(BASE_LOTS * 0.5))
        else:
            continue

        # Pick 1-strike ITM
        atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
        strike = atm - STRIKE_STEP if direction == "CE" else atm + STRIKE_STEP

        entry_premium = get_option_premium(candle["ts"], strike, direction)
        if entry_premium <= 0:
            continue

        qty = lots * LOT_SIZE

        if entry_premium >= 100:
            sl_pct = 0.12
        elif entry_premium >= 70:
            sl_pct = 0.15
        elif entry_premium >= 40:
            sl_pct = 0.20
        else:
            sl_pct = 0.25

        sl_price = entry_premium * (1 - sl_pct)

        position = {
            "direction": direction, "strike": strike, "entry": entry_premium,
            "qty": qty, "sl": sl_price, "lots": lots, "htf": htf_state,
            "entry_time": str(candle["ts"].time()), "entry_spot": spot,
            "event": signal["reasons"][0][:100] if signal["reasons"] else ""
        }
        in_position = True
        bot._candles_since_last_signal = 0
        print(f"  SIGNAL: {candle['ts'].time()} {direction} {strike} premium={entry_premium:.1f} lots={lots} htf={htf_state}")

    # Exit check
    if in_position:
        exit_price = None
        exit_reason = ""

        # Check SL (approximate: if spot dropped enough)
        exit_premium_now = get_option_premium(candle["ts"], position["strike"], position["direction"])
        if exit_premium_now > 0 and exit_premium_now <= position["sl"]:
            exit_price = position["sl"]
            exit_reason = "SL"
        elif i == len(candles_5m) - 1:
            exit_price = exit_premium_now if exit_premium_now > 0 else position["entry"] * 0.5
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
                print(f"  BLOCKED: daily loss Rs.{daily_pnl:,.0f}")
            in_position = False

# --- Results ---
total_pnl = sum(t["pnl"] for t in trades)
wins = sum(1 for t in trades if t["pnl"] > 0)
losses = sum(1 for t in trades if t["pnl"] < 0)

print(f"\n{'='*60}")
print(f"PriceActionBot Backtest: June 4, 2026 (SENSEX Expiry Day)")
print(f"{'='*60}")
print(f"Total trades: {len(trades)}")
if trades:
    print(f"Wins: {wins}, Losses: {losses}, Win rate: {wins/len(trades)*100:.1f}%")
print(f"Total P&L: Rs. {total_pnl:,.0f}")
print(f"Blocked by loss: {BLOCKED}")

ce_pnl = sum(t["pnl"] for t in trades if t["direction"] == "CE")
pe_pnl = sum(t["pnl"] for t in trades if t["direction"] == "PE")
print(f"CE: Rs.{ce_pnl:,.0f} | PE: Rs.{pe_pnl:,.0f}")

print(f"\n--- Trades ---")
for t in trades:
    print(f"  {t['entry_time']} {t['direction']} {t['strike']} entry={t['entry']:.1f} exit={t['exit']:.1f} pnl={t['pnl']:,.0f} [{t['exit_reason']}] htf={t['htf']} lots={t['lots']}")
