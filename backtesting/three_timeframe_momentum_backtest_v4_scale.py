"""Three-Timeframe Momentum Confluence Option Backtester with 5-Lot Partial Exit (Scale-Out).

Implements the user's custom day-trading momentum confluence framework with the 5-Lot scale-out:
  1. Macro Trend (4H "Anchor"): Dictates overall direction for the day/week.
  2. Medium Trend (1H "Bridge"): Shows if intraday momentum favors the macro trend.
  3. Entry Trigger (15M "Trigger"): Precision execution chart.

Execution Rules:
  - BUY CE Setup: 4H Trend is UP, 1H Trend is UP, 15M Trend is UP.
  - BUY PE Setup: 4H Trend is DOWN, 1H Trend is DOWN, 15M Trend is DOWN.
  - "Sitting on hands": If trends do not align, do not trade.

5-Lot Scale-Out Specifics:
  - Buy exactly 5 lots of the ATM option at entry.
  - Scale-Out: Sell 3 lots when premium gains +15%. Stop loss on remaining 2 lots is immediately moved to breakeven (entry premium).
  - Runner: Sell remaining 2 lots when premium gains +30%.
  - Hard Stop-Loss: If no scale-out occurs, exit all 5 lots at a -20% Stop Loss.

Usage:
    python backtesting/three_timeframe_momentum_backtest_v4_scale.py --start 2025-06-01 --end 2026-05-21
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv
from backtesting.confluence_options_backtest_v2 import (
    build_option_index, expiries_index, OptionDataCache,
    find_expiry_for, find_atm_strike, get_premium_at
)

REPORTS = ROOT / "reports"

LOT_SIZE = 65
COST_PER_TRADE = 200
COOLDOWN_MIN = 60

# Scale-Out Specifics
INIT_LOTS = 5
SCALE_LOTS = 3
RUNNER_LOTS = 2

SCALE_TP_PCT = 0.15   # +15% to scale out 3 lots
RUNNER_TP_PCT = 0.30  # +30% to exit remaining 2 lots
HARD_SL_PCT = 0.20    # -20% stop loss initially
FORCE_CLOSE_AT = dtime(15, 25)

@dataclass
class Scaled3TFTrade:
    entry_time: pd.Timestamp
    exit_time_scale: Optional[pd.Timestamp]
    exit_time_runner: pd.Timestamp
    direction: str
    strike: int
    expiry: date
    dte: int
    entry_premium: float
    exit_premium_scale: Optional[float]
    exit_premium_runner: float
    exit_reason_scale: Optional[str]
    exit_reason_runner: str
    pnl_rs: float

def walk_forward_scale_out(
    df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_premium: float,
) -> tuple[Optional[pd.Timestamp], Optional[float], Optional[str], pd.Timestamp, float, str, float]:
    """
    Walks forward minute-by-minute simulating the 5-lot partial scale-out strategy:
      - Scales out 3 lots at +15% profit, then moves SL to breakeven for the remaining 2.
      - If no scale-out occurs, exits all 5 lots at -20% initial SL.
      - Runners exit at +30% TP, breakeven SL (if scaled out), or hard SL/EOD.
    """
    scale_tp_price = entry_premium * (1 + SCALE_TP_PCT)
    runner_tp_price = entry_premium * (1 + RUNNER_TP_PCT)
    initial_sl_price = entry_premium * (1 - HARD_SL_PCT)
    
    forward = df.loc[entry_ts:].iloc[1:]
    if forward.empty:
        # Fallback EOD
        pnl = 0.0
        return None, None, "no_data", entry_ts, entry_premium, "no_data", pnl

    scaled_out = False
    scale_ts = None
    scale_prem = None
    scale_reason = None
    
    runner_ts = None
    runner_prem = None
    runner_reason = None

    for ts, row in forward.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        # EOD check takes priority
        if ts.time() >= FORCE_CLOSE_AT:
            if not scaled_out:
                pnl = (close - entry_premium) * LOT_SIZE * INIT_LOTS
                return None, None, "eod", ts, close, "eod", pnl
            else:
                pnl_scale = (scale_prem - entry_premium) * LOT_SIZE * SCALE_LOTS
                pnl_runner = (close - entry_premium) * LOT_SIZE * RUNNER_LOTS
                return scale_ts, scale_prem, scale_reason, ts, close, "eod", pnl_scale + pnl_runner

        # --- Phase 1: Scale-Out Check ---
        if not scaled_out:
            # Did it hit initial Hard Stop Loss first?
            if low <= initial_sl_price:
                # Total loss exit on all 5 lots
                pnl = (initial_sl_price - entry_premium) * LOT_SIZE * INIT_LOTS
                return None, None, "sl_full", ts, initial_sl_price, "sl_full", pnl
            
            # Did it hit the Scale-Out TP?
            if high >= scale_tp_price:
                scaled_out = True
                scale_ts = ts
                scale_prem = scale_tp_price
                scale_reason = "scale_tp"
                # Move Stop Loss to Breakeven for the remaining 2 lots!
                sl_price_runner = entry_premium
                continue

        # --- Phase 2: Runner Check (Only executed after scale-out) ---
        if scaled_out:
            # Did runner hit its +30% TP?
            if high >= runner_tp_price:
                runner_ts = ts
                runner_prem = runner_tp_price
                runner_reason = "runner_tp"
                break
            
            # Did runner hit the Breakeven Stop Loss?
            if low <= sl_price_runner:
                runner_ts = ts
                runner_prem = sl_price_runner
                runner_reason = "runner_sl_breakeven"
                break

    # If loop ends without breaking (meaning EOD close)
    if runner_ts is None:
        last = forward.iloc[-1]
        runner_ts = forward.index[-1]
        runner_prem = float(last["close"])
        runner_reason = "eod"

    # Compute Net PnL
    if not scaled_out:
        pnl = (runner_prem - entry_premium) * LOT_SIZE * INIT_LOTS
    else:
        pnl_scale = (scale_prem - entry_premium) * LOT_SIZE * SCALE_LOTS
        pnl_runner = (runner_prem - entry_premium) * LOT_SIZE * RUNNER_LOTS
        pnl = pnl_scale + pnl_runner

    return scale_ts, scale_prem, scale_reason, runner_ts, runner_prem, runner_reason, pnl

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

def run_3tf_scale_backtest(start_date: str, end_date: str, ema_window: int = 20):
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

    trades: list[Scaled3TFTrade] = []
    last_entry_ts: Optional[pd.Timestamp] = None
    sitting_on_hands_count = 0

    print("Walking through 15-minute trigger chart with 5-lot scale-out risk model...")
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
            continue

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

        # Scale out walk forward simulation
        scale_ts, scale_prem, scale_reason, runner_ts, runner_prem, runner_reason, pnl = walk_forward_scale_out(
            df_opt, ts, entry_premium
        )

        pnl_net = pnl - COST_PER_TRADE

        trades.append(Scaled3TFTrade(
            entry_time=ts, exit_time_scale=scale_ts, exit_time_runner=runner_ts,
            direction=direction, strike=atm, expiry=expiry,
            dte=(expiry - ts.date()).days, entry_premium=entry_premium,
            exit_premium_scale=scale_prem, exit_premium_runner=runner_prem,
            exit_reason_scale=scale_reason, exit_reason_runner=runner_reason,
            pnl_rs=pnl_net
        ))
        last_entry_ts = ts

    # Print results
    n_months = (df_15m.index.max() - df_15m.index.min()).days / 30.4
    
    print("\n" + "="*80)
    print("3TF MOMENTUM 5-LOT PARTIAL SCALE-OUT BACKTEST RESULTS")
    print("="*80)

    if not trades:
        print("No trades generated.")
        return

    df_tr = pd.DataFrame([t.__dict__ for t in trades])
    n = len(df_tr)
    wins = df_tr[df_tr["pnl_rs"] > 0]
    net_rs = df_tr["pnl_rs"].sum()
    annual_rs = net_rs * 12 / n_months if n_months > 0 else 0
    cumulative = df_tr["pnl_rs"].cumsum()
    dd_rs = (cumulative - cumulative.cummax()).min()

    print(f"  Total Trades Fired:       {n}")
    print(f"  Hands-Sat Occasions:     {sitting_on_hands_count} times")
    print(f"  Win Rate:                 {len(wins)/n*100:.1f}%")
    print(f"  Net P&L (11.6 Months):    Rs. {net_rs:>+12,.2f}")
    print(f"  Return on Rs. 100,000:    {net_rs / 100_000 * 100:+.1f}%")
    print(f"  Max Drawdown Estimate:    Rs. {dd_rs:>+12,.2f}")
    print(f"  Scale-out triggers:       {df_tr['exit_reason_scale'].value_counts().to_dict()}")
    print(f"  Runner exit triggers:     {df_tr['exit_reason_runner'].value_counts().to_dict()}")

    # Write report
    report_lines = [
        "# 3TF Momentum 5-Lot Partial Scale-Out Backtest Report\n",
        f"Period: {start_date} to {end_date} ({n_months:.1f} months)\n",
        "Strategy: 5-Lot Initial Entry, Scale out 3 lots @ +15% (SL to Breakeven), Runner 2 lots @ +30%, Initial SL @ -20%\n",
        "## Performance Metrics\n",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **Total Trades Fired** | {n} |",
        f"| **Patiently Skipped (Sat on Hands)** | {sitting_on_hands_count} times |",
        f"| **Win Rate** | {len(wins)/n*100:.1f}% |",
        f"| **Net P&L** | **Rs. {net_rs:+,.2f}** |",
        f"| **Return on Rs. 100,000 Capital** | **{net_rs/100_000*100:+.1f}%** |",
        f"| **Max Drawdown** | Rs. {dd_rs:,.2f} |",
        "\n## Scale-Out Reason Breakdown\n",
        f"{df_to_md(df_tr['exit_reason_scale'].value_counts().to_frame())}\n",
        "\n## Runner Reason Breakdown\n",
        f"{df_to_md(df_tr['exit_reason_runner'].value_counts().to_frame())}\n",
        "\n## Trade Log\n",
        df_to_md(df_tr[["entry_time", "exit_time_runner", "direction", "strike", "entry_premium", "exit_premium_scale", "exit_premium_runner", "exit_reason_scale", "exit_reason_runner", "pnl_rs"]], index=False)
    ]
    
    report_file = REPORTS / "three_timeframe_momentum_scale_report.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(report_lines))
    print(f"\nSaved report to: reports/three_timeframe_momentum_scale_report.md")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--ema", type=int, default=20)
    args = p.parse_args()

    run_3tf_scale_backtest(args.start, args.end, args.ema)
