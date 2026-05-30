"""Phase 2 v4: 5-Lot Partial Exit (Scale-Out) Strategy Backtester.

Implements the user's custom scaled-exit risk management strategy:
  1. Size: Always buy exactly 5 lots of the ATM option at entry.
  2. Scale-Out (Target 1): Sell 3 lots when premium gains +15%.
     - Once scaled out, immediately move the Stop Loss on the remaining 2 lots
       to Breakeven (entry premium) to guarantee a risk-free trade!
  3. Runner (Target 2): Sell the remaining 2 lots when premium gains +30%.
  4. Hard Stop Loss: If no scale-out occurs, exit all 5 lots at -20% Stop Loss.

Wires the high-performance V2 Vigilance Filters (Trend Guard + Volume Spike).

Usage:
    python backtesting/confluence_options_backtest_v4_scale.py --start 2025-06-01 --end 2026-05-21
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
from backtesting.confluence import SRProvider, score_signals, ConfluenceSignal
from backtesting.confluence_options_backtest_v2 import (
    build_option_index, expiries_index, OptionDataCache,
    find_expiry_for, find_atm_strike, get_premium_at
)
from vigilance.market_structure import MarketStructureEngine
from vigilance.candle_engine import CandleEngine
from vigilance.volume_engine import VolumeEngine

REPORTS = ROOT / "reports"

LOT_SIZE = 65
COST_PER_TRADE = 200
COOLDOWN_MIN = 30
MAX_POSITIONS_PER_DAY = 2

# Scale-Out Specifics
INIT_LOTS = 5
SCALE_LOTS = 3
RUNNER_LOTS = 2

SCALE_TP_PCT = 0.15   # +15% to scale out 3 lots
RUNNER_TP_PCT = 0.30  # +30% to exit remaining 2 lots
HARD_SL_PCT = 0.20    # -20% stop loss initially
FORCE_CLOSE_AT = dtime(15, 25)

@dataclass
class ScaledTrade:
    entry_time: pd.Timestamp
    exit_time_scale: Optional[pd.Timestamp]
    exit_time_runner: pd.Timestamp
    direction: str
    leg: str
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

def run_scale_out_backtest(
    signals: list[ConfluenceSignal],
    df1_1min: pd.DataFrame,
) -> list[ScaledTrade]:
    idx = build_option_index()
    expiries_strikes = expiries_index(idx)
    available_expiries = sorted(expiries_strikes.keys())
    cache = OptionDataCache(idx)

    trades: list[ScaledTrade] = []
    last_entry_ts: Optional[pd.Timestamp] = None
    daily_positions = defaultdict(int)

    for sig in signals:
        ts = sig.timestamp
        t = ts.time()
        sig_date = ts.date()

        if not (dtime(9, 30) <= t < dtime(14, 30)):
            continue
        direction = sig.direction_at(4.0)
        if direction is None:
            continue

        if daily_positions[sig_date] >= MAX_POSITIONS_PER_DAY:
            continue
        if last_entry_ts is not None:
            if (ts - last_entry_ts).total_seconds() / 60 < COOLDOWN_MIN:
                continue

        # ------------------- Vigilance Filters -------------------
        sub_1m = df1_1min.loc[:ts].tail(120)
        if len(sub_1m) < 40:
            continue

        structure = MarketStructureEngine(window=3)
        candles = CandleEngine()
        volume = VolumeEngine(window=20)

        for idx_ts, row in sub_1m.iterrows():
            spot = float(row["close"])
            vol = float(row.get("futures_volume", 0) or 0)
            structure.update(idx_ts, spot)
            if vol > 0:
                volume.update(vol)

        ms = structure.get_structure()
        trend = ms["trend"]
        curr_vol = float(sub_1m.iloc[-1].get("futures_volume", 0) or 0)
        vol_score = volume.get_participation_score(curr_vol)

        curr_bar = sub_1m.iloc[-1]
        prev_bar = sub_1m.iloc[-2]
        candle_trigger = candles.get_pattern(
            open_p=float(prev_bar["open"]), high_p=max(float(prev_bar["high"]), float(curr_bar["high"])),
            low_p=min(float(prev_bar["low"]), float(curr_bar["low"])), close_p=float(curr_bar["close"]),
            prev_candle={"open": float(prev_bar["open"]), "close": float(prev_bar["close"]),
                         "high": float(prev_bar["high"]), "low": float(prev_bar["low"])}
        )

        if direction == "long" and trend == "DOWNTREND":
            continue
        if direction == "short" and trend == "UPTREND":
            continue

        has_candle = candle_trigger in ["HAMMER", "BULLISH_ENGULFING", "SHOOTING_STAR", "BEARISH_ENGULFING", "BULLISH_MARUBOZU", "BEARISH_MARUBOZU"]
        if vol_score < 1.05 and not has_candle:
            continue
        # ----------------- End of Vigilance Filters -----------------

        expiry = find_expiry_for(ts, available_expiries)
        if expiry is None:
            continue

        strikes = expiries_strikes[expiry]
        atm = find_atm_strike(sig.spot_price, strikes)
        if atm is None:
            continue

        leg = "CE" if direction == "long" else "PE"
        df = cache.get(expiry, atm, leg)
        if df is None or df.empty:
            continue

        entry_premium = get_premium_at(df, ts)
        if entry_premium is None or entry_premium <= 0:
            continue

        # Scale out walk forward simulation
        scale_ts, scale_prem, scale_reason, runner_ts, runner_prem, runner_reason, pnl = walk_forward_scale_out(
            df, ts, entry_premium
        )

        pnl_net = pnl - COST_PER_TRADE

        trades.append(ScaledTrade(
            entry_time=ts, exit_time_scale=scale_ts, exit_time_runner=runner_ts,
            direction=direction, leg=leg, strike=atm, expiry=expiry,
            dte=(expiry - ts.date()).days, entry_premium=entry_premium,
            exit_premium_scale=scale_prem, exit_premium_runner=runner_prem,
            exit_reason_scale=scale_reason, exit_reason_runner=runner_reason,
            pnl_rs=pnl_net
        ))

        last_entry_ts = ts
        daily_positions[sig_date] += 1

    return trades

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

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()

    print("Loading index Spot and computing confluence signals...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]

    df_5m = resample_ohlcv(sub, "5min")
    df_1h = resample_ohlcv(sub, "1h")
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)
    
    sr = SRProvider()
    signals = score_signals(df_5m, df_1h, df_4h, sr)
    print(f"  Loaded {len(signals):,} decision points.\n")

    print("Running 5-LOT PARTIAL SCALE-OUT Backtest (TP1=+15% on 3 lots, TP2=+30% on 2 lots, SL=-20%)...")
    trades = run_scale_out_backtest(signals, df1)
    
    n_months = (df_5m.index.max() - df_5m.index.min()).days / 30.4
    
    print("\n" + "="*80)
    print("5-LOT PARTIAL SCALE-OUT BACKTEST RESULTS")
    print("="*80)

    if not trades:
        print("No trades generated after Vigilance filtering.")
        return

    df_tr = pd.DataFrame([t.__dict__ for t in trades])
    n = len(df_tr)
    wins = df_tr[df_tr["pnl_rs"] > 0]
    net_rs = df_tr["pnl_rs"].sum()
    annual_rs = net_rs * 12 / n_months if n_months > 0 else 0
    cumulative = df_tr["pnl_rs"].cumsum()
    dd_rs = (cumulative - cumulative.cummax()).min()

    print(f"  Total Trades Fired:       {n}")
    print(f"  Win Rate:                 {len(wins)/n*100:.1f}%")
    print(f"  Avg Win/Loss per trade:   Rs. {net_rs/n:>+10,.0f}")
    print(f"  Net P&L (11.6 Months):    Rs. {net_rs:>+12,.0f}")
    print(f"  Annualised Return:        Rs. {annual_rs:>+12,.0f}")
    print(f"  Return on Rs. 100,000:    {annual_rs / 100_000 * 100:+.1f}% / year")
    print(f"  Max Drawdown Estimate:    Rs. {dd_rs:>+12,.0f}")
    print(f"  Scale-out triggers:       {df_tr['exit_reason_scale'].value_counts().to_dict()}")
    print(f"  Runner exit triggers:     {df_tr['exit_reason_runner'].value_counts().to_dict()}")

    # Write report
    report_lines = [
        "# 5-Lot Partial Scale-Out Backtest Report\n",
        f"Period: {args.start} to {args.end} ({n_months:.1f} months)\n",
        "Strategy: 5-Lot Initial Entry, Scale out 3 lots @ +15% (SL to Breakeven), Runner 2 lots @ +30%, Initial SL @ -20%\n",
        "## Performance Metrics\n",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **Total Trades Fired** | {n} |",
        f"| **Win Rate** | {len(wins)/n*100:.1f}% |",
        f"| **Net P&L** | **Rs. {net_rs:+,.2f}** |",
        f"| **Annualised P&L** | Rs. {annual_rs:+,.2f} |",
        f"| **Return on Rs. 100,000 Capital** | **{annual_rs/100_000*100:+.1f}% / year** |",
        f"| **Max Drawdown** | Rs. {dd_rs:,.2f} |",
        "\n## Scale-Out Reason Breakdown\n",
        f"{df_to_md(df_tr['exit_reason_scale'].value_counts().to_frame())}\n",
        "\n## Runner Reason Breakdown\n",
        f"{df_to_md(df_tr['exit_reason_runner'].value_counts().to_frame())}\n",
        "\n## Trade Log\n",
        df_to_md(df_tr[["entry_time", "exit_time_runner", "direction", "strike", "entry_premium", "exit_premium_scale", "exit_premium_runner", "exit_reason_scale", "exit_reason_runner", "pnl_rs"]], index=False)
    ]
    
    report_file = REPORTS / "confluence_options_trades_v4_scale_report.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(report_lines))
    print(f"\nSaved report to: reports/confluence_options_trades_v4_scale_report.md")

if __name__ == "__main__":
    main()
