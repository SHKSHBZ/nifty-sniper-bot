"""Quick analysis: What went wrong on March 24?"""
import pandas as pd
import requests
from upstox_auth import UpstoxAuth

auth = UpstoxAuth()
headers = {"Authorization": f"Bearer {auth.get_access_token()}", "Accept": "application/json"}

# 1. Read Trade 1 option premium data
ce_df = pd.read_csv("data/NIFTY_22800_CE_24_MAR_26_1min.csv")
ce_df["timestamp"] = pd.to_datetime(ce_df["timestamp"])

print("=== 22800 CE Premium around Trade 1 (12:00) ===")
for _, r in ce_df.iterrows():
    t = str(r["timestamp"])[11:19]
    if "11:55" <= t <= "12:10":
        print(f"  {t} | Open:{r['open']:.2f}  High:{r['high']:.2f}  Low:{r['low']:.2f}  Close:{r['close']:.2f}")

# 2. Get Nifty Spot 1-min data for the same window
url = "https://api.upstox.com/v2/historical-candle/NSE_INDEX%7CNifty+50/1minute/2026-03-25/2026-03-23"
resp = requests.get(url, headers=headers)
data = resp.json().get("data", {}).get("candles", [])
sdf = pd.DataFrame(data, columns=["ts","o","h","l","c","v","oi"])
sdf["ts"] = pd.to_datetime(sdf["ts"])
sdf = sdf.sort_values("ts")

day = sdf[sdf["ts"].dt.strftime("%Y-%m-%d") == "2026-03-24"]

print("\n=== Nifty Spot around Trade 1 (12:00) ===")
for _, r in day.iterrows():
    t = str(r["ts"])[11:19]
    if "11:55" <= t <= "12:10":
        print(f"  {t} | Nifty: {r['c']:.1f}")

print(f"\n=== March 24 Day Summary ===")
print(f"  Open:  {day.iloc[0]['o']:.1f}")
print(f"  High:  {day['h'].max():.1f}")
print(f"  Low:   {day['l'].min():.1f}")
print(f"  Close: {day.iloc[-1]['c']:.1f}")
print(f"  Range: {day['h'].max() - day['l'].min():.1f} points")

# 3. Check: Did Nifty actually go UP after 12:00?
print("\n=== Nifty Direction after Trade 1 entry (12:00 onwards) ===")
at_noon = None
for _, r in day.iterrows():
    t = str(r["ts"])[11:19]
    if t == "12:00:00":
        at_noon = r["c"]
    if at_noon and "12:00" <= t <= "12:10":
        change = r["c"] - at_noon
        print(f"  {t} | Nifty: {r['c']:.1f} | Change from 12:00: {change:+.1f} pts")
