"""SENSEX no-SL straddle: sweep entry times to find where (if anywhere)
buying ATM CE+PE and just holding pays off.

No SL, no TP. Just enter at the time, hold to 15:25, square off.
Try entries at 11:00, 12:00, 13:00, 13:30, 14:00, 14:30, 14:50, 15:00.

If ANY entry time gives positive total P&L, we know SENSEX has a
gamma window — just not at 14:50. If all negative, the strategy
genuinely doesn't work on SENSEX.

Output: console table + reports/sensex_nosl_entry_sweep.csv
"""
from __future__ import annotations

import sys
from datetime import time as dtime
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
if _HERE.name == "backtesting":
    sys.path.insert(0, str(_HERE.parent))

import backtesting.expiry_straddle_sensex as base


ENTRY_TIMES_TO_TEST = [
    dtime(11, 0),
    dtime(12, 0),
    dtime(13, 0),
    dtime(13, 30),
    dtime(14, 0),
    dtime(14, 30),
    dtime(14, 50),
    dtime(15, 0),
]


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = base.discover_expiries()
    print(f"Loaded {len(expiries)} SENSEX expiries.\n")
    print(f"Premium gate: Rs.{base.MIN_PREMIUM}-Rs.{base.MAX_PREMIUM} per leg "
          f"(override via env or by editing expiry_straddle_sensex.py)\n")

    # Save the original ENTRY_TIMES so we can restore after each run.
    original_entry_times = base.ENTRY_TIMES

    results = []
    for entry_t in ENTRY_TIMES_TO_TEST:
        # Force a single entry time for this run
        base.ENTRY_TIMES = [entry_t]
        trades = []
        for tok, dt in expiries:
            try:
                t = base.run_one_expiry(tok, dt, spot_df, tp=None, sl=None)
                if t is not None:
                    trades.append(t)
            except Exception:
                pass
        n = len(trades)
        if n == 0:
            results.append({"entry_time": entry_t.strftime("%H:%M"),
                            "n_trades": 0})
            print(f"  {entry_t.strftime('%H:%M')}  {n:>3} trades")
            continue
        df = pd.DataFrame([t.__dict__ for t in trades])
        cum = df["net_pnl"].cumsum()
        dd = (cum.cummax() - cum).max()
        win = (df["net_pnl"] > 0).mean() * 100
        results.append({
            "entry_time": entry_t.strftime("%H:%M"),
            "n_trades": n,
            "win_rate_%": round(win, 1),
            "total_pnl": int(df["net_pnl"].sum()),
            "avg_pnl": int(df["net_pnl"].mean()),
            "median_ret_%": round(df["return_pct"].median(), 1),
            "best": int(df["net_pnl"].max()),
            "worst": int(df["net_pnl"].min()),
            "max_dd": int(dd),
            "avg_minutes": int(df["minutes_held"].mean()),
        })
        print(f"  {entry_t.strftime('%H:%M')}  {n:>3} trades, "
              f"win {win:>4.1f}%, "
              f"total Rs.{int(df['net_pnl'].sum()):>9,}, "
              f"best Rs.{int(df['net_pnl'].max()):>7,}, "
              f"worst Rs.{int(df['net_pnl'].min()):>7,}, "
              f"DD Rs.{int(dd):>7,}")

        # Save per-entry-time ledger
        csv = base.REPORTS_DIR / f"sensex_nosl_entry_{entry_t.strftime('%H%M')}_trades.csv"
        df.to_csv(csv, index=False)

    base.ENTRY_TIMES = original_entry_times
    summary = pd.DataFrame(results)
    out = base.REPORTS_DIR / "sensex_nosl_entry_sweep.csv"
    summary.to_csv(out, index=False)
    print(f"\nWrote {out}")
    print("\n", summary.to_string(index=False))


if __name__ == "__main__":
    main()
