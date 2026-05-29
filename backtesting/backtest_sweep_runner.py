"""
backtest_sweep_runner.py
========================
Fast parameter sweep runner using Phase 4's daily chain data.

Pre-loads all daily chains into optimized in-memory lookups,
then runs the SignalEngine simulation for each parameter combo.

Usage:
    python backtesting/backtest_sweep_runner.py
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from dataclasses import dataclass
import datetime
from datetime import date, time as dt_time, timedelta
from itertools import product
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.classifier import RegimeClassifier, ClassifierConfig, Regime
from regime.classifier import ClassifierFeatures
from signal_engine import SignalEngine
from backtesting.backtest_regime_phase1 import load_spot, load_vix, resample
from backtesting.backtest_regime_phase4 import (
    load_daily_chain,
    SLIPPAGE, BROKERAGE_PER_LEG, LOT_SIZE, STRIKE_STEP, MIN_ENTRY_PREMIUM,
    ENTRY_AFTER, ENTRY_CUTOFF, FORCE_FLAT,
)
from backtesting.backtest_regime_phase3 import Trade

# ============================================================================
# Fixed params
# ============================================================================
SL_PCT = 0.20
TP_PCT = 0.50

LOG_FILE = ROOT / "reports" / "sweep_progress.log"


def log(msg: str):
    print(msg, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


# ============================================================================
# Optimized chain data: pre-pivot to strike-indexed columns
# ============================================================================

def build_chain_lookup(daily: pd.DataFrame) -> dict:
    """Convert daily chain to nested dict: {strike: {timestamp: {ce_oi, pe_oi, ce_ltp, pe_ltp}}}"""
    lookup: dict[int, dict] = {}
    for _, row in daily.iterrows():
        strike = int(row["strike"])
        ts = row["timestamp"]
        if strike not in lookup:
            lookup[strike] = {}
        lookup[strike][ts] = {
            "ce_oi": float(row["ce_oi"]),
            "pe_oi": float(row["pe_oi"]),
            "ce_ltp": float(row["ce_ltp"]),
            "pe_ltp": float(row["pe_ltp"]),
        }
    return lookup


def preload_all() -> tuple[pd.DataFrame, pd.DataFrame | None, dict]:
    """Load spot, VIX, and all daily chains into dict lookup."""
    log("Loading spot data...")
    spot_1m = load_spot()
    log(f"  Spot: {len(spot_1m):,} rows, {spot_1m.index[0].date()} -> {spot_1m.index[-1].date()}")

    log("Loading VIX data...")
    vix_1m = load_vix()
    if vix_1m is not None:
        log(f"  VIX: {len(vix_1m):,} rows")

    trading_days = sorted({d for d in spot_1m.index.date})
    log(f"Loading {len(trading_days)} daily chain files...")
    chains: dict[date, dict] = {}
    for day in trading_days:
        daily = load_daily_chain(day)
        if daily is not None and len(daily):
            chains[day] = build_chain_lookup(daily)
    log(f"  Pre-loaded {len(chains)} days as fast lookups")
    return spot_1m, vix_1m, chains


def fast_oi(lookup: dict, strike: int, side: str, ts: pd.Timestamp) -> float:
    """O(1) OI lookup with 2-min fallback."""
    strike_data = lookup.get(strike)
    if strike_data is None:
        return 0.0
    row = strike_data.get(ts)
    if row is not None:
        return row[f"{side.lower()}_oi"]
    # 2-min fallback
    for delta in range(1, 3):
        row = strike_data.get(ts - timedelta(minutes=delta))
        if row is not None:
            return row[f"{side.lower()}_oi"]
    return 0.0


def fast_ltp(lookup: dict, strike: int, side: str, ts: pd.Timestamp) -> float | None:
    """O(1) LTP lookup with 2-min fallback."""
    strike_data = lookup.get(strike)
    if strike_data is None:
        return None
    row = strike_data.get(ts)
    if row is not None:
        return row[f"{side.lower()}_ltp"]
    for delta in range(1, 3):
        row = strike_data.get(ts - timedelta(minutes=delta))
        if row is not None:
            return row[f"{side.lower()}_ltp"]
    return None


def get_chain_state(lookup: dict, ts: pd.Timestamp, spot: float,
                    half_range: int) -> dict:
    """Compute S/R, focus PCR, OI changes with configurable focus zone."""
    strikes = sorted(lookup.keys())
    atm = min(strikes, key=lambda x: abs(x - spot))

    # S/R clusters within ATM ±5
    res_strike = sup_strike = atm
    max_ce_cluster = max_pe_cluster = 0.0
    for s in [atm + (i * STRIKE_STEP) for i in range(-5, 6)]:
        ce_s = fast_oi(lookup, s, "CE", ts)
        band_ce = (ce_s + fast_oi(lookup, s + STRIKE_STEP, "CE", ts)
                   + fast_oi(lookup, s - STRIKE_STEP, "CE", ts))
        pe_s = fast_oi(lookup, s, "PE", ts)
        band_pe = (pe_s + fast_oi(lookup, s + STRIKE_STEP, "PE", ts)
                   + fast_oi(lookup, s - STRIKE_STEP, "PE", ts))
        if s >= atm and band_ce > max_ce_cluster:
            max_ce_cluster = band_ce; res_strike = s
        if s <= atm and band_pe > max_pe_cluster:
            max_pe_cluster = band_pe; sup_strike = s

    # Focus PCR
    total_ce_oi = total_pe_oi = 0.0
    ce_change = pe_change = 0.0
    ts_prev = ts - timedelta(minutes=5)
    for s in [atm + (i * STRIKE_STEP) for i in range(-half_range, half_range + 1)]:
        ce_now = fast_oi(lookup, s, "CE", ts)
        pe_now = fast_oi(lookup, s, "PE", ts)
        ce_prev = fast_oi(lookup, s, "CE", ts_prev)
        pe_prev = fast_oi(lookup, s, "PE", ts_prev)
        total_ce_oi += ce_now; total_pe_oi += pe_now
        ce_change += (ce_now - ce_prev); pe_change += (pe_now - pe_prev)

    focus_pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0
    return {
        "support": int(sup_strike), "resistance": int(res_strike),
        "focus_pcr": focus_pcr,
        "oi_pattern": {"ce_oi_change": int(ce_change), "pe_oi_change": int(pe_change)},
    }


def run_one(spot_1m: pd.DataFrame, vix_1m: pd.DataFrame | None,
            chains: dict, time_stop: int, half_range: int) -> list[Trade]:
    """Run one simulation pass."""
    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
    engine = SignalEngine()
    trades: list[Trade] = []

    for day, lookup in chains.items():
        day_str = day.isoformat()
        day_5m = resample(spot_1m[spot_1m.index.date == day], "5min")
        if day_5m.empty:
            continue
        classifier._current = None
        classifier._candidate = None
        open_trade: Optional[Trade] = None

        for ts, row in day_5m.iterrows():
            spot_close = float(row["close"])
            feat = ClassifierFeatures(
                ts=ts.to_pydatetime(), gap_pct=0.0, or_range_pct=0.0,
                avg_or_range_pct=0.0025, adx_15m=0.0, range_ratio=1.0,
                vwap_slope_30m=0.0, dist_from_vwap_pct=0.0,
                price=spot_close, vwap=spot_close, or_high=0.0, or_low=0.0,
                vix_level=15.0, vix_chg_15m=0.0, dte=5, event_flag=False,
                prev_day_close=spot_close,
            )
            regime = classifier.classify(feat)

            # Monitor open trade
            if open_trade is not None:
                opt_prem = fast_ltp(lookup, open_trade.strike, open_trade.direction, ts)
                if opt_prem is not None:
                    eff_entry = open_trade.entry_premium
                    tp = eff_entry * (1 + TP_PCT)
                    sl = eff_entry * (1 - SL_PCT)
                    mins_held = (ts - open_trade.entry_ts).total_seconds() / 60
                    if opt_prem >= tp:
                        open_trade.close(ts, tp, "TP")
                        trades.append(open_trade); open_trade = None
                    elif opt_prem <= sl:
                        open_trade.close(ts, sl, "SL")
                        trades.append(open_trade); open_trade = None
                    elif mins_held >= time_stop:
                        open_trade.close(ts, opt_prem, "TIME_STOP")
                        trades.append(open_trade); open_trade = None
                    elif ts.time() >= FORCE_FLAT:
                        open_trade.close(ts, opt_prem, "EOD")
                        trades.append(open_trade); open_trade = None

            if open_trade is not None:
                continue
            if ts.time() < ENTRY_AFTER or ts.time() >= ENTRY_CUTOFF:
                continue
            if regime in (Regime.NO_TRADE, Regime.WAIT, Regime.EXPIRY):
                continue

            # Get chain state
            chain_state = get_chain_state(lookup, ts, spot_close, half_range)

            # Spot history
            spot_history = [
                {"time": idx.to_pydatetime(), "spot": float(r["close"])}
                for idx, r in spot_1m.loc[ts - timedelta(minutes=15):ts].iterrows()
            ]

            # VIX
            vix_level = 15.0
            if vix_1m is not None:
                vw = vix_1m[vix_1m.index <= ts]
                if not vw.empty:
                    vix_level = float(vw.iloc[-1]["close"])

            sig = engine.evaluate(
                spot_close=spot_close, support=chain_state["support"],
                resistance=chain_state["resistance"],
                focus_pcr=chain_state["focus_pcr"],
                oi_pattern=chain_state["oi_pattern"],
                spot_history=spot_history, india_vix=vix_level,
                expiry_date=day_str, current_date=day_str,
            )
            direction = sig["direction"]
            if direction is None:
                continue

            atm_strike = int(round(spot_close / STRIKE_STEP) * STRIKE_STEP)
            opt_prem = fast_ltp(lookup, atm_strike, direction, ts)
            if opt_prem is None or opt_prem < MIN_ENTRY_PREMIUM:
                continue

            open_trade = Trade(
                day=day_str, tactic="oi_wall", direction=direction,
                strike=atm_strike, entry_ts=ts, entry_premium=opt_prem,
                qty_lots=1, regime_at_entry=regime,
            )

    return trades


def summarize(trades: list[Trade]) -> dict:
    closed = [t for t in trades if t.exit_reason]
    if not closed:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "net_pnl": 0.0, "profit_factor": 0.0, "max_dd": 0.0}

    winners = [t for t in closed if t.net_pnl > 0]
    losers = [t for t in closed if t.net_pnl <= 0]
    pnls = [t.net_pnl for t in closed]
    cum = np.cumsum(pnls)
    max_dd = float(max(0, (pd.Series(cum).cummax() - cum).max()))
    gp = sum(t.net_pnl for t in winners) or 0.0
    gl = abs(sum(t.net_pnl for t in losers)) or 0.0
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    reasons = defaultdict(int)
    for t in closed:
        reasons[t.exit_reason] += 1

    return {
        "trades": len(closed), "wins": len(winners), "losses": len(losers),
        "win_rate": len(winners) / len(closed) * 100,
        "net_pnl": sum(pnls), "profit_factor": pf, "max_dd": max_dd,
        "exit_reasons": dict(reasons),
    }


def run():
    global LOG_FILE
    LOG_FILE.write_text("")

    log("=" * 60)
    log("Parameter Sweep: Time Stop × Focus Zone")
    log("=" * 60)

    t0 = datetime.datetime.now().timestamp()
    spot_1m, vix_1m, chains = preload_all()
    t1 = datetime.datetime.now().timestamp()
    log(f"Data loaded in {t1-t0:.1f}s")

    time_stops = [60, 90, 120, 150]
    half_ranges = [1, 2, 3]  # ±1=3 strikes, ±2=5 strikes, ±3=7 strikes

    results = []
    total = len(time_stops) * len(half_ranges)
    count = 0

    for ts, hr in product(time_stops, half_ranges):
        count += 1
        fz_total = hr * 2 + 1
        label = f"TS={ts}min FZ=±{hr} ({fz_total} strikes)"
        log(f"\n[{count}/{total}] {label} ...")
        t_start = datetime.datetime.now().timestamp()
        trades = run_one(spot_1m, vix_1m, chains, time_stop=ts, half_range=hr)
        elapsed = datetime.datetime.now().timestamp() - t_start
        summary = summarize(trades)
        summary["label"] = label
        summary["time_s"] = elapsed
        results.append(summary)
        log(f"  -> {summary['trades']} trades, {summary['win_rate']:.1f}%, "
            f"Rs {summary['net_pnl']:+,.0f}, PF {summary['profit_factor']:.2f} "
            f"({elapsed:.0f}s)")

    total_time = datetime.datetime.now().timestamp() - t0
    log(f"\n{'=' * 60}")
    log(f"Total time: {total_time:.0f}s")

    # Report
    out = [f"# Parameter Sweep: Time Stop × Focus Zone\n"]
    out.append(f"SL={SL_PCT*100:.0f}% TP={TP_PCT*100:.0f}% | Ran {total} combos in {total_time:.0f}s\n")
    out.append("## Summary\n")
    out.append("| Config | Trades | Win% | Net P&L | PF | Max DD | Time(s) |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")

    results.sort(key=lambda r: r["net_pnl"], reverse=True)
    for r in results:
        pf = r["profit_factor"]
        pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
        out.append(f"| {r['label']} | {r['trades']} | {r['win_rate']:.1f} "
                   f"| Rs {r['net_pnl']:+,.0f} | {pf_s} | Rs {r['max_dd']:,.0f} "
                   f"| {r['time_s']:.0f} |")

    out.append("\n## Top 5\n")
    for i, r in enumerate(results[:5], 1):
        out.append(f"{i}. **{r['label']}**: {r['trades']}t {r['win_rate']:.1f}% "
                   f"Rs {r['net_pnl']:+,.0f} PF {r['profit_factor']:.2f}")

    report_path = ROOT / "reports" / "sweep_results.md"
    report_path.write_text("\n".join(out))
    log(f"\nReport: {report_path.relative_to(ROOT)}")

    log("\n--- TOP 5 ---")
    for i, r in enumerate(results[:5], 1):
        log(f"  {i}. {r['label']}: {r['trades']}t {r['win_rate']:.1f}% "
            f"Rs {r['net_pnl']:+,.0f} PF {r['profit_factor']:.2f}")


if __name__ == "__main__":
    run()
