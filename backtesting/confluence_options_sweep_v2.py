"""Phase 2 v2: Parametric Exit & Cooldown Sweep Runner.

Replays confluence signals over combinations of Profit Targets, Stop Losses,
and Cooldown limits to find the optimal mathematical peak for option buying.

Usage:
    python -m backtesting.confluence_options_sweep_v2 --start 2025-06-01 --end 2026-05-21
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.confluence_options_backtest_v2 import (
    run_options_backtest_v2,
    summarize,
    load_aligned_1min,
    resample_ohlcv,
    SRProvider,
    score_signals
)

REPORTS = ROOT / "reports"

def run_parametric_sweep(start_date: str, end_date: str):
    print("Loading index Spot and computing confluence signals...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(start_date, tz="Asia/Kolkata")
    end = pd.Timestamp(end_date, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]
    
    df_5m = resample_ohlcv(sub, "5min")
    df_1h = resample_ohlcv(sub, "1h")
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)
    
    sr = SRProvider()
    signals = score_signals(df_5m, df_1h, df_4h, sr)
    print(f"  Loaded {len(signals):,} decision points over {start_date} to {end_date}.\n")

    # Define sweep ranges
    tp_ranges = [20.0, 30.0, 40.0, 50.0]
    sl_ranges = [15.0, 20.0, 25.0, 30.0]
    cooldown_ranges = [15, 30, 45, 60]

    total_configs = len(tp_ranges) * len(sl_ranges) * len(cooldown_ranges)
    print(f"Starting parametric sweep: {total_configs} total configurations to test...")
    print("-" * 80)
    print(f"{'TP%':>6} | {'SL%':>6} | {'Cooldown':>8} | {'Trades':>6} | {'Win%':>6} | {'Net P&L (Rs)':>14} | {'Max DD':>12}")
    print("-" * 80)

    sweep_results = []
    n_months = (df_5m.index.max() - df_5m.index.min()).days / 30.4

    for tp in tp_ranges:
        for sl in sl_ranges:
            for cd in cooldown_ranges:
                trades, _ = run_options_backtest_v2(
                    signals, df1,
                    score_threshold=4.0,
                    tp_pct=tp, sl_pct=sl,
                    cooldown_min=cd
                )
                
                s = summarize(trades, n_months, 100_000)
                n_trades = s.get("n_trades", 0)
                win_rate = s.get("win_rate_pct", 0.0)
                net_pnl = s.get("net_rs", 0.0)
                max_dd = s.get("max_dd_rs", 0.0)

                print(f"{tp:>5.1f}% | {sl:>5.1f}% | {cd:>6} min | {n_trades:>6} | {win_rate:>5.1f}% | {net_pnl:>+14,.0f} | {max_dd:>12,.0f}")
                
                sweep_results.append({
                    "TP_Pct": tp,
                    "SL_Pct": sl,
                    "Cooldown_Mins": cd,
                    "Trades": n_trades,
                    "Win_Rate": win_rate,
                    "Net_PnL": net_pnl,
                    "Max_DD": max_dd,
                    "Gross_PnL": s.get("gross_rs", 0.0),
                    "Costs": s.get("costs_rs", 0.0),
                    "Avg_Win": s.get("avg_win_rs", 0.0),
                    "Avg_Loss": s.get("avg_loss_rs", 0.0),
                    "TP_Hits": s.get("exit_reasons", {}).get("tp", 0),
                    "SL_Hits": s.get("exit_reasons", {}).get("sl", 0),
                    "EOD_Hits": s.get("exit_reasons", {}).get("eod", 0),
                })

    # Sort results by Net P&L descending
    df_results = pd.DataFrame(sweep_results).sort_values(by="Net_PnL", ascending=False)
    
    # Save ledger
    csv_path = REPORTS / "confluence_options_sweep_v2.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df_results.to_csv(csv_path, index=False)
    print("\n" + "=" * 80)
    print(f"SWEEP COMPLETED. Top 5 Configurations saved to reports/confluence_options_sweep_v2.csv")
    print("=" * 80)
    
    # Print Top 5
    top_5 = df_results.head(5)
    print(top_5[["TP_Pct", "SL_Pct", "Cooldown_Mins", "Trades", "Win_Rate", "Net_PnL", "Max_DD"]].to_string(index=False))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()
    
    run_parametric_sweep(args.start, args.end)
