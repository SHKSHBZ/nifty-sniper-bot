import os
import glob
import sys

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from replay_oi_flow_logs import backtest_day

def main():
    print("=" * 60)
    print("PREMIUM EMA PARAMETER OPTIMIZATION")
    print("=" * 60)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
    files = glob.glob(os.path.join(base_dir, "focus_zone_*.csv"))
    
    if not files:
        print(f"No focus_zone_*.csv files found in {base_dir}")
        return

    files.sort()
    
    # Define combinations of Fast/Slow EMAs to test
    ema_combinations = [
        (3, 9),
        (5, 9),
        (5, 15),
        (9, 21),
        (12, 26)
    ]
    
    best_pnl = -float('inf')
    best_combo = None
    
    results = []

    # Disable prints during optimization runs
    original_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    
    for fast, slow in ema_combinations:
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
                
            res = backtest_day(file, symbol, fast, slow)
            if res:
                total_pnl += res["daily_pnl"]
                for t in res["trades"]:
                    total_trades += 1
                    if t["pnl"] > 0: wins += 1
                    elif t["pnl"] < 0: losses += 1
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        results.append({
            "combo": (fast, slow),
            "pnl": total_pnl,
            "win_rate": win_rate,
            "trades": total_trades
        })
        
        if total_pnl > best_pnl:
            best_pnl = total_pnl
            best_combo = (fast, slow)

    # Restore prints
    sys.stdout.close()
    sys.stdout = original_stdout
    
    print("\nOPTIMIZATION RESULTS:\n")
    print(f"{'Fast/Slow':<15} | {'Win Rate':<10} | {'Trades':<8} | {'Total P&L'}")
    print("-" * 55)
    
    for r in sorted(results, key=lambda x: x['pnl'], reverse=True):
        combo_str = f"{r['combo'][0]}/{r['combo'][1]}"
        print(f"{combo_str:<15} | {r['win_rate']:>8.1f}% | {r['trades']:>6}   | Rs. {r['pnl']:,.0f}")
        
    print("\n" + "=" * 60)
    print(f"BEST COMBINATION: {best_combo[0]}/{best_combo[1]} with Rs. {best_pnl:,.0f} P&L")
    print("=" * 60)

if __name__ == "__main__":
    main()
