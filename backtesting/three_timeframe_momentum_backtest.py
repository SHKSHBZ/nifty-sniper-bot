"""Three-Timeframe Momentum Confluence Option Backtester.

Implements the user's custom day-trading momentum confluence framework:
  1. Macro Trend (4H "Anchor"): Dictates overall direction for the day/week.
  2. Medium Trend (1H "Bridge"): Shows if intraday momentum favors the macro trend.
  3. Entry Trigger (15M "Trigger"): Precision execution chart.

Execution Rules:
  - BUY CE Setup: 4H Trend is UP, 1H Trend is UP, 15M Trend is UP.
  - BUY PE Setup: 4H Trend is DOWN, 1H Trend is DOWN, 15M Trend is DOWN.
  - "Sitting on hands": If trends do not align, do not trade.

We use the 20-period Exponential Moving Average (EMA) to identify trend on each TF.

Usage:
    python backtesting/three_timeframe_momentum_backtest.py --start 2025-06-01 --end 2026-05-21
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv
from backtesting.confluence_options_backtest_v2 import (
    build_option_index, expiries_index, OptionDataCache,
    find_expiry_for, find_atm_strike, get_premium_at, walk_forward_exit
)

REPORTS = ROOT / "reports"

def df_to_md(df: pd.DataFrame, index: bool = True) -> str:
    # Zero-dependency pandas to markdown generator
    cols = list(df.columns)
    if index:
        cols = [df.index.name or ""] + cols
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    divider = "| " + " | ".join("---" for _ in cols) + " |"
    rows = []
    for idx, r in df.iterrows():
        vals = [str(v) for v in r]
        if index:
            vals = [str(idx)] + vals
        rows.append("| " + " | ".join(vals) + " |")
    return "\n".join([header, divider] + rows)

LOT_SIZE = 65
CAPITAL_PER_TRADE = 20_000
COST_PER_TRADE = 200
COOLDOWN_MIN = 60
TP_PREMIUM_PCT = 30
SL_PREMIUM_PCT = 30

@dataclass
class TradeRecord:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str               # 'long' (CE) or 'short' (PE)
    strike: int
    expiry: date
    entry_premium: float
    exit_premium: float
    exit_reason: str
    lots: int
    pnl_rs: float
    h4_trend: str
    h1_trend: str
    m15_trend: str

def calculate_ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()

def run_3tf_backtest(start_date: str, end_date: str, ema_window: int = 20):
    print("Loading 1-minute aligned spot data...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(start_date, tz="Asia/Kolkata")
    end = pd.Timestamp(end_date, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]

    print("Resampling timeframes session-aligned...")
    df_15m = resample_ohlcv(sub, "15min", drop_partial=True)
    df_1h = resample_ohlcv(sub, "1h", drop_partial=True)
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)

    print("Computing EMAs...")
    df_4h["ema"] = calculate_ema(df_4h["close"], ema_window)
    df_1h["ema"] = calculate_ema(df_1h["close"], ema_window)
    df_15m["ema"] = calculate_ema(df_15m["close"], ema_window)

    # Pre-build options lookup index
    idx = build_option_index()
    expiries_strikes = expiries_index(idx)
    available_expiries = sorted(expiries_strikes.keys())
    cache = OptionDataCache(idx)

    trades: list[TradeRecord] = []
    last_entry_ts: Optional[pd.Timestamp] = None
    sitting_on_hands_count = 0

    print("Walking through 15-minute trigger chart...")
    # Skip first window to let EMA build up
    for i in range(ema_window, len(df_15m)):
        row_15m = df_15m.iloc[i]
        ts = df_15m.index[i]
        
        # Avoid early morning or late entries
        if not (dtime(9, 30) <= ts.time() < dtime(14, 30)):
            continue

        # Cooldown guard
        if last_entry_ts is not None:
            if (ts - last_entry_ts).total_seconds() / 60 < COOLDOWN_MIN:
                continue

        # 1. Macro Trend: Most recent COMPLETED 4H bar
        h4_completed_ts = ts - pd.Timedelta(hours=4)
        h4_idx = df_4h.index.searchsorted(h4_completed_ts, side="right") - 1
        if h4_idx < 0:
            continue
        h4_bar = df_4h.iloc[h4_idx]
        h4_trend = "UP" if h4_bar["close"] > h4_bar["ema"] else "DOWN"

        # 2. Medium Trend: Most recent COMPLETED 1H bar
        h1_completed_ts = ts - pd.Timedelta(hours=1)
        h1_idx = df_1h.index.searchsorted(h1_completed_ts, side="right") - 1
        if h1_idx < 0:
            continue
        h1_bar = df_1h.iloc[h1_idx]
        h1_trend = "UP" if h1_bar["close"] > h1_bar["ema"] else "DOWN"

        # 3. Execution Trend: Current 15M bar close vs EMA
        m15_trend = "UP" if row_15m["close"] > row_15m["ema"] else "DOWN"

        # --- Confluence logic ---
        is_long = (h4_trend == "UP" and h1_trend == "UP" and m15_trend == "UP")
        is_short = (h4_trend == "DOWN" and h1_trend == "DOWN" and m15_trend == "DOWN")

        if not (is_long or is_short):
            sitting_on_hands_count += 1
            continue  # Patiently sitting on hands

        # Strike, Expiry, Leg Selection
        direction = "long" if is_long else "short"
        expiry = find_expiry_for(ts, available_expiries)
        if expiry is None:
            continue

        strikes = expiries_strikes[expiry]
        atm = find_atm_strike(row_15m["close"], strikes)
        if atm is None:
            continue

        leg = "CE" if is_long else "PE"
        df_opt = cache.get(expiry, atm, leg)
        if df_opt is None or df_opt.empty:
            continue

        entry_premium = get_premium_at(df_opt, ts)
        if entry_premium is None or entry_premium <= 0:
            continue

        cost_per_lot = entry_premium * LOT_SIZE
        lots = max(1, int(CAPITAL_PER_TRADE // cost_per_lot))

        exit_ts, exit_premium, reason = walk_forward_exit(
            df_opt, ts, entry_premium, tp_pct=TP_PREMIUM_PCT, sl_pct=SL_PREMIUM_PCT
        )
        pnl_rs = (exit_premium - entry_premium) * LOT_SIZE * lots

        trades.append(TradeRecord(
            entry_time=ts, exit_time=exit_ts,
            direction=direction, strike=atm, expiry=expiry,
            entry_premium=entry_premium, exit_premium=exit_premium,
            exit_reason=reason, lots=lots, pnl_rs=pnl_rs,
            h4_trend=h4_trend, h1_trend=h1_trend, m15_trend=m15_trend
        ))
        last_entry_ts = ts

    # Print results
    n_months = (df_15m.index.max() - df_15m.index.min()).days / 30.4
    
    print("\n" + "="*80)
    print("THREE-TIMEFRAME CONFLUENCE BACKTEST COMPLETED")
    print("="*80)
    
    if not trades:
        print("No trades triggered. You sat on your hands perfectly!")
        return

    df_tr = pd.DataFrame([t.__dict__ for t in trades])
    n = len(df_tr)
    wins = df_tr[df_tr["pnl_rs"] > 0]
    gross_rs = df_tr["pnl_rs"].sum()
    costs = n * COST_PER_TRADE
    net_rs = gross_rs - costs

    print(f"  Total Trades Fired:       {n}")
    print(f"  Hands-Sat Occasions:     {sitting_on_hands_count} times")
    print(f"  Win Rate:                 {len(wins)/n*100:.1f}%")
    print(f"  Gross P&L:                Rs. {gross_rs:+,.2f}")
    print(f"  Transaction Costs:        Rs. {costs:,.2f}")
    print(f"  Net P&L (12 Months):      Rs. {net_rs:+,.2f}")
    print(f"  Max Drawdown Estimate:    Rs. {(df_tr['pnl_rs'].cumsum() - df_tr['pnl_rs'].cumsum().cummax()).min():,.2f}")
    print(f"  Exit Reasons:             {df_tr['exit_reason'].value_counts().to_dict()}")

    # Write report
    report_lines = [
        "# 3-Timeframe Momentum Confluence Backtest Report\n",
        f"Period: {start_date} to {end_date} ({n_months:.1f} months)\n",
        f"Strategy: EMA-{ema_window} Multi-Timeframe Alignment (4H Anchor + 1H Bridge + 15M Trigger)\n",
        "## Performance Metrics\n",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **Total Trades Fired** | {n} |",
        f"| **Patiently Skipped (Sat on Hands)** | {sitting_on_hands_count} times |",
        f"| **Win Rate** | {len(wins)/n*100:.1f}% |",
        f"| **Gross P&L** | Rs. {gross_rs:+,.2f} |",
        f"| **Costs (Rs.200/tr)** | Rs. {costs:,.2f} |",
        f"| **Net P&L** | **Rs. {net_rs:+,.2f}** |",
        f"| **Max Drawdown** | Rs. {(df_tr['pnl_rs'].cumsum() - df_tr['pnl_rs'].cumsum().cummax()).min():,.2f} |",
        "\n## Exit Reasons\n",
        f"{df_to_md(df_tr['exit_reason'].value_counts().to_frame())}\n",
        "\n## Trade Log\n",
        df_to_md(df_tr[["entry_time", "exit_time", "direction", "strike", "entry_premium", "exit_premium", "exit_reason", "pnl_rs"]], index=False)
    ]
    
    report_file = REPORTS / "three_timeframe_momentum_report.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(report_lines))
    print(f"\nSaved report to: reports/three_timeframe_momentum_report.md")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--ema", type=int, default=20)
    args = p.parse_args()
    
    run_3tf_backtest(args.start, args.end, args.ema)
