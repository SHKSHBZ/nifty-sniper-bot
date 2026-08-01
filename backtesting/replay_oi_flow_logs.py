import os
import sys
import csv
import glob
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from oi_flow_engine import OIFlowEngine


class MockFetcher:
    def __init__(self):
        self.ltp_map = {}
        self.spot = 0.0
        self.strike_oi = {}

    def get_ltp_map(self):
        return self.ltp_map

    def get_spot(self):
        return self.spot
        
    def get_support(self):
        if not self.strike_oi:
            return int(self.spot / 50) * 50 - 50 if self.spot else 0
        return max(self.strike_oi.keys(), key=lambda s: self.strike_oi[s]['pe_oi'])
        
    def get_resistance(self):
        if not self.strike_oi:
            return int(self.spot / 50) * 50 + 50 if self.spot else 0
        return max(self.strike_oi.keys(), key=lambda s: self.strike_oi[s]['ce_oi'])

def backtest_day(csv_path, symbol, prem_ema_fast=5, prem_ema_slow=9, trailing_sl_pct=0.25, use_structural_sl=False):
    # Group data by timestamp
    timeline = defaultdict(list)
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = row['timestamp']
                timeline[ts].append(row)
    except Exception as e:
        print(f"Error reading {csv_path}: {e}")
        return None

    # Sort timestamps chronologically
    sorted_ts = sorted(timeline.keys())
    if not sorted_ts:
        return None

    date_str = sorted_ts[0][:10]

    config = {
        "strategy": {
            "nifty_lot_size": 65 if symbol == "NIFTY" else 20,
            "nifty_strike_step": 50 if symbol == "NIFTY" else 100
        },
        "signal_threshold": 500000,
        "entry_time": "10:00",
        "snap2_time": "09:45",
        "snap1_time": "09:30",
        "trailing_sl_pct": trailing_sl_pct,
        "use_structural_sl": use_structural_sl,
        "prem_ema_fast": prem_ema_fast,
        "prem_ema_slow": prem_ema_slow
    }

    # Initialize Engine
    engine = OIFlowEngine(config)
    
    # Silence logging in engine to avoid clutter
    import logging
    logging.getLogger().setLevel(logging.CRITICAL)

    fetcher = MockFetcher()
    
    trades = []
    daily_pnl = 0.0

    for ts_str in sorted_ts:
        rows = timeline[ts_str]
        
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except:
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
            except:
                ts = datetime.strptime(ts_str, "%d/%m/%Y %H:%M")

        spot = float(rows[0]['spot'])
        fetcher.spot = spot
        fetcher.ltp_map = {}
        fetcher.strike_oi = {}
        
        oi_snapshot = {}
        premiums = {}
        
        for row in rows:
            try:
                strike = float(row['strike'])
                ce_oi = float(row.get('ce_oi', 0))
                ce_prev_oi = float(row.get('ce_prev_oi', 0))
                pe_oi = float(row.get('pe_oi', 0))
                pe_prev_oi = float(row.get('pe_prev_oi', 0))
                
                ce_ltp = float(row.get('ce_ltp', 0))
                pe_ltp = float(row.get('pe_ltp', 0))
                
                fetcher.strike_oi[strike] = {
                    "ce_oi": ce_oi,
                    "pe_oi": pe_oi
                }
                
                oi_snapshot[strike] = {
                    "ce_oi": ce_oi - ce_prev_oi,
                    "pe_oi": pe_oi - pe_prev_oi
                }
                
                premiums[f"{int(strike)}_CE"] = ce_ltp
                premiums[f"{int(strike)}_PE"] = pe_ltp
                
                fetcher.ltp_map[f"{int(strike)}_CE"] = ce_ltp
                fetcher.ltp_map[f"{int(strike)}_PE"] = pe_ltp
            except ValueError:
                continue

        # Process Tick
        signals = engine.tick(spot, ts, oi_snapshot, premiums, fetcher=fetcher)
        if ts_str.endswith("10:30") or ts_str.endswith("10:30:00") or "10:30" in ts_str[-8:]:
            if engine.oi_at_snap2 and engine.oi_at_entry:
                pe_d = engine.oi_at_entry[0] - engine.oi_at_snap2[0]
                ce_d = engine.oi_at_entry[1] - engine.oi_at_snap2[1]
                net = pe_d - ce_d
                print(f"DEBUG 10:30 | Regime: {engine.regime} | Net: {net/1e3:.0f}K | Spot: {spot:.0f} | Open: {engine.open_spot:.0f}")

        for signal in signals:
            print(f"DEBUG {ts_str}: Signal generated -> {signal['action']}")
            action = signal["action"]
            
            if action == "exit" or action == "partial_exit":
                direction = engine.position["direction"]
                reason = signal["reason"]
                exit_premiums = {}
                for s in engine.position["strikes"]:
                    key = f"{int(s)}_{direction}"
                    exit_premiums[int(s)] = fetcher.ltp_map.get(key, 0)
                
                result = engine.close_position(reason, exit_premiums, ts)
                if result:
                    daily_pnl += result["total_pnl"]
                    for t in trades:
                        if t["exit_time"] is None and t["type"] in ["CE", "PE"]:
                            t["exit_time"] = ts_str
                            t["exit_reason"] = reason
                            t["pnl"] = result["total_pnl"]
                            t["hold_mins"] = result["hold_minutes"]
                            break

            elif action == "exit_ic":
                reason = signal["reason"]
                result = engine.close_ic_position(signal, premiums, ts)
                if result:
                    daily_pnl += result["total_pnl"]
                    for t in trades:
                        if t["exit_time"] is None and t["type"] == "IRON_CONDOR":
                            t["exit_time"] = ts_str
                            t["exit_reason"] = reason
                            t["pnl"] = result["total_pnl"]
                            t["hold_mins"] = result["hold_minutes"]
                            break

            elif action == "entry":
                direction = signal["direction"]
                strikes = signal["strikes"]
                real_premiums = {}
                valid_entry = True
                for s in strikes:
                    key = f"{int(s)}_{direction}"
                    ltp = fetcher.ltp_map.get(key, 0)
                    if ltp <= 0:
                        valid_entry = False
                        break
                    real_premiums[int(s)] = ltp
                    
                if valid_entry:
                    lots = engine.compute_lot_allocation(real_premiums, 1.0)
                    engine.open_position(signal, real_premiums, ts, lots)
                    trades.append({
                        "date": date_str,
                        "entry_time": ts_str,
                        "type": direction,
                        "reason": signal["reason"],
                        "strikes": strikes,
                        "lots": lots,
                        "spot_entry": spot,
                        "exit_time": None,
                        "exit_reason": None,
                        "pnl": 0.0,
                        "hold_mins": 0
                    })
                    
            elif action == "entry_ic":
                strikes = signal["strikes"]
                # 6 Lakhs capital -> ~12 lots for Iron Condor
                lots = 12
                engine.open_ic_position(signal, premiums, ts, lots)
                trades.append({
                    "date": date_str,
                    "entry_time": ts_str,
                    "type": "IRON_CONDOR",
                    "reason": signal["reason"],
                    "strikes": [strikes["ce_short"], strikes["ce_long"], strikes["pe_short"], strikes["pe_long"]],
                    "lots": lots,
                    "spot_entry": spot,
                    "exit_time": None,
                    "exit_reason": None,
                    "pnl": 0.0,
                    "hold_mins": 0
                })

    # Force close any open positions at end of day
    if engine.position is not None:
        direction = engine.position["direction"]
        exit_premiums = {}
        for s in engine.position["strikes"]:
            key = f"{int(s)}_{direction}"
            exit_premiums[int(s)] = fetcher.ltp_map.get(key, 0)
        
        result = engine.close_position("End of Day Force Exit", exit_premiums, ts)
        if result:
            daily_pnl += result["total_pnl"]
            for t in trades:
                if t["exit_time"] is None and t["type"] in ["CE", "PE"]:
                    t["exit_time"] = ts_str
                    t["exit_reason"] = "EOD Force Close"
                    t["pnl"] = result["total_pnl"]
                    t["hold_mins"] = result["hold_minutes"]

    if engine.ic_position is not None:
        strikes = engine.ic_position["strikes"]
        close_cost = (premiums.get(f"{strikes['ce_short']}_CE",0) - premiums.get(f"{strikes['ce_long']}_CE",0)) + (premiums.get(f"{strikes['pe_short']}_PE",0) - premiums.get(f"{strikes['pe_long']}_PE",0))
        signal = {"reason": "EOD Force Close", "close_cost": close_cost}
        result = engine.close_ic_position(signal, premiums, ts)
        if result:
            daily_pnl += result["total_pnl"]
            for t in trades:
                if t["exit_time"] is None and t["type"] == "IRON_CONDOR":
                    t["exit_time"] = ts_str
                    t["exit_reason"] = "EOD Force Close"
                    t["pnl"] = result["total_pnl"]
                    t["hold_mins"] = result["hold_minutes"]

    return {
        "date": date_str,
        "daily_pnl": daily_pnl,
        "trades": trades,
        "regime": engine.regime
    }

def main():
    print("=" * 60)
    print("OI-FLOW LOG BACKTESTER")
    print("=" * 60)

    # Find all CSV files
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
    files = glob.glob(os.path.join(base_dir, "focus_zone_*.csv"))
    
    if not files:
        print(f"No focus_zone_*.csv files found in {base_dir}")
        return

    files.sort()
    
    total_pnl = 0.0
    wins = 0
    losses = 0
    total_trades = 0
    processed_days = 0

    for file in files:
        filename = os.path.basename(file)
        # Parse symbol from filename (e.g. focus_zone_nifty_2026-06-04.csv)
        parts = filename.replace(".csv", "").split("_")
        if len(parts) >= 3:
            symbol = parts[2].upper()
            if symbol == "NIFTY":
                if "expiry" in filename: continue
        else:
            continue
            
        processed_days += 1

        res = backtest_day(file, symbol, 5, 9)
        if res:
            pnl = res["daily_pnl"]
            total_pnl += pnl
            print(f"\n[{res['date']}] | {symbol} | Regime: {res['regime'].upper()} | Daily P&L: Rs.{pnl:,.0f}")
            for t in res["trades"]:
                total_trades += 1
                if t["pnl"] > 0: wins += 1
                elif t["pnl"] < 0: losses += 1
                
                print(f"  -> {t['entry_time'][11:16]} BUY {t['type']} ({t['reason']}) @ Spot {t['spot_entry']:.0f}")
                print(f"     Exit: {str(t['exit_time'])[11:16]} | {t['exit_reason']} | P&L: Rs.{t['pnl']:,.0f} | Hold: {t['hold_mins']:.0f}m")

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"Total Days Processed: {processed_days}")
    print(f"Total Trades Taken  : {total_trades}")
    print(f"Wins / Losses       : {wins} / {losses}")
    if total_trades > 0:
        print(f"Win Rate            : {(wins/total_trades)*100:.1f}%")
    print(f"Total Net P&L       : Rs.{total_pnl:,.0f}")
    print("=" * 60)

if __name__ == "__main__":
    main()
