import pandas as pd
from datetime import datetime, time as dtime
from oi_flow_engine import OIFlowEngine
from gann_engine import GannSquareOf9

# 1. Load focus zone data for today
df = pd.read_csv("logs/focus_zone_nifty_expiry_2026-07-14.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
df = df.sort_values("timestamp")

# Filter for today (July 8th)
df_today = df[df["timestamp"].dt.date == pd.to_datetime("2026-07-08").date()]

if df_today.empty:
    print("Error: No data found for July 8, 2026 in focus zone file!")
    exit(1)

# Sort and group by timestamp to simulate tick data
ticks = df_today.drop_duplicates(subset=["timestamp"])

# 2. Initialize OIFlowEngine with Nifty options configuration
config = {
    "trading_index": "NIFTY",
    "strike_step": 50,
    "entry_time": "10:30",
    "snap2_time": "10:00",
    "snap1_time": "09:30",
    "max_daily_loss": 100000.0 # Bypassed drawdown for simulation
}

engine = OIFlowEngine(config)
engine.gann = GannSquareOf9(24281.72) # Today's prev close/base price

print(f"Starting simulation for NIFTY on July 8, 2026")
print(f"Total Ticks to process: {len(ticks)}")
print("=" * 60)

trades = []

# Mock fetcher to simulate LTP map updates
class MockFetcher:
    def __init__(self, current_row, all_data_at_time):
        self.row = current_row
        self.all_data = all_data_at_time
        
    def get_spot(self):
        return float(self.row["spot"])
        
    def get_ltp_map(self):
        ltp_map = {}
        for _, r in self.all_data.iterrows():
            strike = int(r["strike"])
            ltp_map[f"{strike}_CE"] = float(r["ce_ltp"])
            ltp_map[f"{strike}_PE"] = float(r["pe_ltp"])
        return ltp_map

    def get_india_vix(self):
        return 15.0 # assume constant VIX > 12.5

# Iterate over each timestamp
for idx, tick_time in enumerate(ticks["timestamp"]):
    tick_data = df_today[df_today["timestamp"] == tick_time]
    row = tick_data.iloc[0]
    spot = float(row["spot"])
    
    # Check if we should process this tick (after 09:20 IST)
    t_str = tick_time.strftime("%H:%M")
    if t_str < "09:20":
        continue
        
    fetcher = MockFetcher(row, tick_data)
    
    # 1. Build premiums and snapshots
    oi_snapshot = {}
    premiums = {}
    all_strikes = engine.ce_fixed_strikes + engine.pe_fixed_strikes if engine._strikes_locked else []
    
    for _, r in tick_data.iterrows():
        s_int = int(r["strike"])
        oi_snapshot[s_int] = {"ce_oi": int(r["ce_oi"]), "pe_oi": int(r["pe_oi"])}
        if s_int in all_strikes:
            premiums[f"{s_int}_CE"] = float(r["ce_ltp"])
            premiums[f"{s_int}_PE"] = float(r["pe_ltp"])
            
    # 2. Tick the engine
    actions = engine.tick(spot, tick_time, oi_snapshot, premiums, fetcher)
    
    if actions:
        for act in actions:
            if act["action"] == "entry":
                # Get real entry premiums
                ltp_map = fetcher.get_ltp_map()
                entry_premiums = {int(s): ltp_map.get(f"{int(s)}_{act['direction']}", 0.0) for s in act["strikes"]}
                lots = engine.compute_lot_allocation(entry_premiums, act.get("size_multiplier", 1.0))
                
                engine.open_position(act, entry_premiums, tick_time, lots)
                print(f"[{tick_time.strftime('%H:%M:%S')}] ENTRY BUY {act['direction']} | Spot={spot:.2f} | Strikes={act['strikes']} | Reason={act['reason']}")
                
            elif act["action"] == "exit":
                # Get real exit premiums
                ltp_map = fetcher.get_ltp_map()
                direction = engine.position["direction"]
                exit_premiums = {int(s): ltp_map.get(f"{int(s)}_{direction}", 0.0) for s in engine.position["strikes"]}
                exit_premiums["spot"] = spot
                
                result = engine.close_position(act["reason"], exit_premiums, tick_time)
                if result:
                    print(f"[{tick_time.strftime('%H:%M:%S')}] EXIT {direction} | Spot={spot:.2f} | P&L=Rs. {result['total_pnl']:+,.2f} | Reason={act['reason']} | Hold={result['hold_minutes']:.1f}m")
                    trades.append(result)

print("=" * 60)
print(f"Simulation completed. Total trades taken: {len(trades)}")
net_pnl = sum(t["total_pnl"] for t in trades)
print(f"Net P&L: Rs. {net_pnl:+,.2f}")
