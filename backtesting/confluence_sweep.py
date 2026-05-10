"""Parameter sweep over the confluence backtest.

Goal: find a configuration that produces 36-50% annual return on
Rs.1,00,000 capital after realistic transaction costs.

Sweeps:
  score_threshold: 4, 5, 6
  TP%:             0.30, 0.45, 0.60, 0.90
  SL%:             0.30 (fixed)  +  structural-with-buffer modes

Output: reports/confluence_sweep.csv (full grid)
        printed table sorted by net annual return on Rs.1L
"""
from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path

import pandas as pd

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv
from backtesting.confluence import SRProvider, score_signals
from backtesting.confluence_backtest import run_backtest, summarize


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

LOT = 65
COST_PER_TRADE = 200          # Rs realistic round-trip on options
CAPITAL = 100_000


def annualize_pnl_rupees(total_pts: float, n_trades: int, n_months: float) -> float:
    """Convert points to rupees (1 lot futures), deduct costs, annualise."""
    gross = total_pts * LOT
    net = gross - n_trades * COST_PER_TRADE
    return net * 12 / n_months if n_months > 0 else 0.0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2025-06-01")
    p.add_argument("--end",   default="2025-08-28")
    args = p.parse_args()

    print(f"Loading data + signals for {args.start} -> {args.end}...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]
    df_5m = resample_ohlcv(sub, "5min")
    df_1h = resample_ohlcv(sub, "1h")
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)
    sr = SRProvider()
    signals = score_signals(df_5m, df_1h, df_4h, sr)
    print(f"  {len(signals):,} decision points generated\n")

    n_months = (df_5m.index.max() - df_5m.index.min()).days / 30.4

    # --- the grid ---
    score_thresholds = [4.0, 5.0, 6.0]
    tp_pcts = [0.30, 0.45, 0.60, 0.90]
    sl_pcts = [0.30]
    sl_modes = [
        ("fixed",                   {"sr_provider": None}),
        ("struct+0.10buf",          {"sr_provider": sr, "structural_sl_buffer_pct": 0.10}),
        ("struct+0.20buf",          {"sr_provider": sr, "structural_sl_buffer_pct": 0.20}),
    ]

    rows = []
    for thr, tp, sl, (mode_name, mode_kwargs) in product(
        score_thresholds, tp_pcts, sl_pcts, sl_modes
    ):
        trades = run_backtest(
            signals, df_5m,
            tp_pct=tp, sl_pct=sl, score_threshold=thr,
            **mode_kwargs,
        )
        s = summarize(trades)
        if s["n_trades"] == 0:
            rows.append({
                "thr": thr, "tp%": tp, "sl%": sl, "sl_mode": mode_name,
                "n_trades": 0, "win_pct": 0, "pf": 0, "total_pts": 0,
                "max_dd_pts": 0, "net_annual_inr": 0, "return_pct": 0,
            })
            continue
        annual_inr = annualize_pnl_rupees(s["total_points"], s["n_trades"], n_months)
        rows.append({
            "thr": thr, "tp%": tp, "sl%": sl, "sl_mode": mode_name,
            "n_trades": s["n_trades"],
            "win_pct": round(s["win_rate_pct"], 1),
            "pf": round(s["profit_factor"], 2),
            "total_pts": round(s["total_points"], 1),
            "max_dd_pts": round(s["max_drawdown_pts"], 1),
            "net_annual_inr": round(annual_inr, 0),
            "return_pct": round(annual_inr / CAPITAL * 100, 1),
        })

    df = pd.DataFrame(rows).sort_values("net_annual_inr", ascending=False)

    out = REPORTS / "confluence_sweep.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print("=" * 100)
    print(f"PARAMETER SWEEP — {len(df)} configs ({args.start} to {args.end}, "
          f"{n_months:.1f} months)")
    print("=" * 100)
    print(f"Costs assumed: Rs.{COST_PER_TRADE}/trade (round-trip)")
    print(f"Capital:       Rs.{CAPITAL:,}\n")

    print(df.to_string(index=False))
    print(f"\nCSV: {out.relative_to(ROOT)}")

    profitable = df[df["return_pct"] > 0]
    target = df[df["return_pct"] >= 36]
    print(f"\nConfigs profitable after costs: {len(profitable)} / {len(df)}")
    print(f"Configs hitting >=36% annual:    {len(target)} / {len(df)}")
    if len(target) > 0:
        print(f"\nBest hit: {target.iloc[0]['return_pct']:+.1f}% annual on Rs.{CAPITAL:,}")


if __name__ == "__main__":
    main()
