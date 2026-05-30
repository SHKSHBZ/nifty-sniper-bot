"""Phase 3 MTF: Horizontal S/R Walls + Vertical 3-Timeframe Momentum Alignment.

Implements the user's ultimate combined option buying strategy:
  1. Horizontal Wall: Spot price must be near Support (for CE) or Resistance (for PE).
  2. Vertical Momentum: 4H trend, 1H trend, and 15M trend must all align in the entry direction.
     - Buy CE Setup: Price near Support AND 4H is UP AND 1H is UP AND 15M is UP.
     - Buy PE Setup: Price near Resistance AND 4H is DOWN AND 1H is DOWN AND 15M is DOWN.

Usage:
    python backtesting/confluence_options_backtest_v3_mtf.py --start 2025-06-01 --end 2026-05-21
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
from backtesting.confluence import SRProvider

REPORTS = ROOT / "reports"

LOT_SIZE = 65
CAPITAL_PER_TRADE = 20_000
COST_PER_TRADE = 200
COOLDOWN_MIN = 30
TP_PREMIUM_PCT = 30
SL_PREMIUM_PCT = 30
NEAR_LEVEL_PCT = 0.002  # 0.2% proximity to support/resistance levels

@dataclass
class TradeRecord:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str               # 'long' (CE) or 'short' (PE)
    level_type: str              # 'SUPPORT' or 'RESISTANCE'
    level_val: float
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

def df_to_md(df: pd.DataFrame, index: bool = True) -> str:
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

def run_v3_mtf_backtest(start_date: str, end_date: str, ema_window: int = 20):
    print("Loading aligned Spot index data...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(start_date, tz="Asia/Kolkata")
    end = pd.Timestamp(end_date, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]

    print("Resampling timeframes...")
    df_15m = resample_ohlcv(sub, "15min", drop_partial=True)
    df_1h = resample_ohlcv(sub, "1h", drop_partial=True)
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)

    print("Computing EMAs...")
    df_4h["ema"] = calculate_ema(df_4h["close"], ema_window)
    df_1h["ema"] = calculate_ema(df_1h["close"], ema_window)
    df_15m["ema"] = calculate_ema(df_15m["close"], ema_window)

    # Initialize S/R Levels provider
    sr = SRProvider()

    # Pre-build options lookup index
    idx = build_option_index()
    expiries_strikes = expiries_index(idx)
    available_expiries = sorted(expiries_strikes.keys())
    cache = OptionDataCache(idx)

    trades: list[TradeRecord] = []
    last_entry_ts: Optional[pd.Timestamp] = None
    vigilance_skipped_count = 0

    print("Walking through 15-minute execution chart...")
    for i in range(ema_window, len(df_15m)):
        row_15m = df_15m.iloc[i]
        ts = df_15m.index[i]
        spot = float(row_15m["close"])
        t = ts.time()
        
        # Avoid early morning or late entries
        if not (dtime(9, 30) <= t < dtime(14, 30)):
            continue

        # Cooldown guard
        if last_entry_ts is not None:
            if (ts - last_entry_ts).total_seconds() / 60 < COOLDOWN_MIN:
                continue

        # Fetch S/R Levels for this date
        levels_res = sr.levels_for(ts.date())
        if not levels_res:
            continue
        supports, resistances = levels_res

        # Check proximity to S/R levels
        near_sup = False
        sup_val = 0.0
        sup_name = ""
        for name, val in supports:
            if abs(spot - val) / spot <= NEAR_LEVEL_PCT:
                near_sup = True
                sup_val = val
                sup_name = name
                break

        near_res = False
        res_val = 0.0
        res_name = ""
        for name, val in resistances:
            if abs(spot - val) / spot <= NEAR_LEVEL_PCT:
                near_res = True
                res_val = val
                res_name = name
                break

        if not (near_sup or near_res):
            continue  # Price is not near any S/R horizontal walls

        # 1. Macro Trend: Completed 4H bar
        h4_completed_ts = ts - pd.Timedelta(hours=4)
        h4_idx = df_4h.index.searchsorted(h4_completed_ts, side="right") - 1
        if h4_idx < 0:
            continue
        h4_bar = df_4h.iloc[h4_idx]
        h4_trend = "UP" if h4_bar["close"] > h4_bar["ema"] else "DOWN"

        # 2. Medium Trend: Completed 1H bar
        h1_completed_ts = ts - pd.Timedelta(hours=1)
        h1_idx = df_1h.index.searchsorted(h1_completed_ts, side="right") - 1
        if h1_idx < 0:
            continue
        h1_bar = df_1h.iloc[h1_idx]
        h1_trend = "UP" if h1_bar["close"] > h1_bar["ema"] else "DOWN"

        # 3. Trigger Trend: Current 15M bar close vs EMA
        m15_trend = "UP" if row_15m["close"] > row_15m["ema"] else "DOWN"

        # --- Combined Strategy Logic ---
        # CE Setup: Price near Support AND all 3 timeframes are UP
        is_ce_entry = (near_sup and h4_trend == "UP" and h1_trend == "UP" and m15_trend == "UP")
        # PE Setup: Price near Resistance AND all 3 timeframes are DOWN
        is_pe_entry = (near_res and h4_trend == "DOWN" and h1_trend == "DOWN" and m15_trend == "DOWN")

        if not (is_ce_entry or is_pe_entry):
            vigilance_skipped_count += 1
            continue  # Momentum trend filter blocked this horizontal level touch

        # Option Setup
        direction = "long" if is_ce_entry else "short"
        level_type = "SUPPORT" if is_ce_entry else "RESISTANCE"
        level_val = sup_val if is_ce_entry else res_val
        
        expiry = find_expiry_for(ts, available_expiries)
        if expiry is None:
            continue

        strikes = expiries_strikes[expiry]
        atm = find_atm_strike(spot, strikes)
        if atm is None:
            continue

        leg = "CE" if is_ce_entry else "PE"
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
            direction=direction, level_type=level_type, level_val=level_val,
            strike=atm, expiry=expiry,
            entry_premium=entry_premium, exit_premium=exit_premium,
            exit_reason=reason, lots=lots, pnl_rs=pnl_rs,
            h4_trend=h4_trend, h1_trend=h1_trend, m15_trend=m15_trend
        ))
        last_entry_ts = ts

    # Results tabulation
    n_months = (df_15m.index.max() - df_15m.index.min()).days / 30.4
    
    print("\n" + "="*80)
    print("V3 COMBINED MULTI-TIMEFRAME S/R BACKTEST COMPLETED")
    print("="*80)
    
    if not trades:
        print("No trades triggered. All horizontal wall touches were blocked by trend momentum.")
        return

    df_tr = pd.DataFrame([t.__dict__ for t in trades])
    n = len(df_tr)
    wins = df_tr[df_tr["pnl_rs"] > 0]
    gross_rs = df_tr["pnl_rs"].sum()
    costs = n * COST_PER_TRADE
    net_rs = gross_rs - costs

    print(f"  Total Trades Fired:       {n}")
    print(f"  Trend Blocked Touches:    {vigilance_skipped_count} entries")
    print(f"  Win Rate:                 {len(wins)/n*100:.1f}%")
    print(f"  Gross P&L:                Rs. {gross_rs:+,.2f}")
    print(f"  Transaction Costs:        Rs. {costs:,.2f}")
    print(f"  Net P&L (12 Months):      Rs. {net_rs:+,.2f}")
    print(f"  Max Drawdown Estimate:    Rs. {(df_tr['pnl_rs'].cumsum() - df_tr['pnl_rs'].cumsum().cummax()).min():,.2f}")
    print(f"  Exit Reasons:             {df_tr['exit_reason'].value_counts().to_dict()}")

    # Write report
    report_lines = [
        "# V3 Combined S/R & Multi-Timeframe Confluence Backtest Report\n",
        f"Period: {start_date} to {end_date} ({n_months:.1f} months)\n",
        f"Strategy: Nifty Options Buy at Camarilla/Classic S/R with EMA-20 Multi-Timeframe Alignment\n",
        "## Performance Metrics\n",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **Total Trades Fired** | {n} |",
        f"| **Blocked by Trend Filter** | {vigilance_skipped_count} entries |",
        f"| **Win Rate** | {len(wins)/n*100:.1f}% |",
        f"| **Gross P&L** | Rs. {gross_rs:+,.2f} |",
        f"| **Costs (Rs.200/tr)** | Rs. {costs:,.2f} |",
        f"| **Net P&L** | **Rs. {net_rs:+,.2f}** |",
        f"| **Max Drawdown** | Rs. {(df_tr['pnl_rs'].cumsum() - df_tr['pnl_rs'].cumsum().cummax()).min():,.2f} |",
        "\n## Exit Reasons\n",
        f"{df_to_md(df_tr['exit_reason'].value_counts().to_frame())}\n",
        "\n## Trade Log\n",
        df_to_md(df_tr[["entry_time", "exit_time", "direction", "level_type", "level_val", "strike", "entry_premium", "exit_premium", "exit_reason", "pnl_rs"]], index=False)
    ]
    
    report_file = REPORTS / "confluence_options_trades_v3_mtf_report.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(report_lines))
    print(f"\nSaved report to: reports/confluence_options_trades_v3_mtf_report.md")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--ema", type=int, default=20)
    args = p.parse_args()
    
    run_v3_mtf_backtest(args.start, args.end, args.ema)
