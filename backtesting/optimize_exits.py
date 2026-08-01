import os
import glob
import sys

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from replay_oi_flow_logs import backtest_day

def main():
    print("=" * 70)
    print("EXIT STRATEGY OPTIMIZATION (5/9 EMA)")
    print("=" * 70)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
    files = glob.glob(os.path.join(base_dir, "focus_zone_*.csv"))
    
    if not files:
        print(f"No focus_zone_*.csv files found in {base_dir}")
        return

    files.sort()
    
    # Define combinations to test: (Strategy_Name, trailing_sl_pct, use_structural_sl)
    strategies = [
        ("Trailing SL 25%", 0.25, False),
        ("Trailing SL 40%", 0.40, False),
        ("Trailing SL 50%", 0.50, False),
        ("Structural SL (20pt)", 0.25, True),
    ]
    
    results = []

    # Disable prints during optimization runs
    original_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    
    for name, tsl_pct, use_str_sl in strategies:
        total_pnl = 0.0
        wins = 0
        losses = 0
        total_trades = 0
        
        for file in files:
            filename = os.path.basename(file)
            parts = filename.replace(".csv", "").split("_")
            if len(parts) >= 3:
                symbol = parts[2].upper()
                if symbol == "NIFTY":
                    if "expiry" in filename: continue
            else:
                continue
                
            res = backtest_day(file, symbol, 5, 9, tsl_pct, use_str_sl)
            if res:
                total_pnl += res["daily_pnl"]
                for t in res["trades"]:
                    total_trades += 1
                    if t["pnl"] > 0: wins += 1
                    elif t["pnl"] < 0: losses += 1
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        results.append({
            "name": name,
            "pnl": total_pnl,
            "win_rate": win_rate,
            "trades": total_trades
        })

    # Restore prints
    sys.stdout.close()
    sys.stdout = original_stdout
    
    print("\nOPTIMIZATION RESULTS:\n")
    print(f"{'Exit Strategy':<25} | {'Win Rate':<10} | {'Trades':<8} | {'Total P&L'}")
    print("-" * 65)
    
    for r in sorted(results, key=lambda x: x['pnl'], reverse=True):
        print(f"{r['name']:<25} | {r['win_rate']:>8.1f}% | {r['trades']:>6}   | Rs. {r['pnl']:,.0f}")
        
    print("\n" + "=" * 70)

if __name__ == "__main__":
    main()
