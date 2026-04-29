"""
Phase 5 — Parameter sweep over the production SignalEngine.

Goal:
    Find the SL / TP / time-stop combination that maximises net P&L on
    the historical 1-year sample, then propose tuned defaults for the
    live bot.

Strategy:
    1. Run the simulator ONCE (~10 min). At every signal, record the
       FULL option OHLC path from entry through end-of-day.
    2. Persist those `TradeRecord` objects.
    3. For each (sl_pct, tp_pct, time_stop_min) combo, replay every
       record's path applying that combo's exit rules — compute P&L.
       This is fast (microseconds per record × N records × M combos).
    4. Rank combos by net P&L, profit factor, return/drawdown, etc.

This isolates parameter-tuning runtime from simulation runtime — a
single expensive pass enables hundreds of cheap evaluations.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, time
from itertools import product
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.classifier import RegimeClassifier, ClassifierConfig, Regime  # noqa: E402
from signal_engine import SignalEngine  # noqa: E402
from backtesting.backtest_regime_phase1 import (  # noqa: E402
    load_spot, load_vix, resample, previous_day_close, build_feature_for_bar,
)
from backtesting.backtest_regime_phase3 import (  # noqa: E402
    discover_expiries, load_chain_for_expiry, map_day_to_expiry,
)
from backtesting.backtest_regime_phase4 import (  # noqa: E402
    reconstruct_chain_state, build_spot_history,
    SLIPPAGE, BROKERAGE_PER_LEG, LOT_SIZE, STRIKE_STEP, MIN_ENTRY_PREMIUM,
    ENTRY_AFTER, ENTRY_CUTOFF, FORCE_FLAT,
)


# ============================================================================
# Search grid — adjust these to widen / narrow the sweep
# ============================================================================
SL_GRID = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]      # 6 values
TP_GRID = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]   # 7 values
TIME_STOP_GRID = [30, 45, 60, 90, 120]              # 5 values
# 6 * 7 * 5 = 210 combinations evaluated against captured paths


# ============================================================================
# Trade record — captures full post-entry path for replay
# ============================================================================

@dataclass
class TradeRecord:
    day: str
    entry_ts: pd.Timestamp
    direction: str
    strike: int
    entry_premium: float
    regime_at_entry: str
    # 1-minute OHLC of the option from entry through 14:30 cutoff (inclusive)
    path_ts: list[pd.Timestamp] = field(default_factory=list)
    path_close: list[float] = field(default_factory=list)
    path_high: list[float] = field(default_factory=list)
    path_low: list[float] = field(default_factory=list)


def capture_option_path(
    chain: dict[tuple[int, str], pd.DataFrame],
    strike: int,
    side: str,
    entry_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> tuple[list, list, list, list]:
    """Return (ts_list, close_list, high_list, low_list) for the option from
    entry_ts inclusive to end_ts inclusive."""
    df = chain.get((strike, side))
    if df is None:
        return [], [], [], []
    sub = df.loc[entry_ts:end_ts]
    return (list(sub.index),
            sub["close"].tolist(),
            sub["high"].tolist(),
            sub["low"].tolist())


# ============================================================================
# Single-pass simulator that captures paths instead of executing exits
# ============================================================================

def simulate_and_capture(
    spot_1m: pd.DataFrame,
    vix_1m: pd.DataFrame,
    expiries_by_date: dict[date, list[Path]],
    *,
    regime_gated: bool,
) -> list[TradeRecord]:
    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
    engine = SignalEngine()
    records: list[TradeRecord] = []

    trading_days = sorted({d for d in spot_1m.index.date})
    expiries = sorted(expiries_by_date.keys())
    day_to_expiry = map_day_to_expiry(trading_days, expiries)
    chain_cache: dict[date, dict] = {}

    for day in trading_days:
        if day not in day_to_expiry:
            continue
        exp = day_to_expiry[day]
        if exp not in chain_cache:
            chain_cache[exp] = load_chain_for_expiry(expiries_by_date[exp])
        chain = chain_cache[exp]

        day_str = day.isoformat()
        day_1m = spot_1m[spot_1m.index.date == day]
        if day_1m.empty:
            continue
        day_5m = resample(day_1m, "5min")
        day_15m = resample(day_1m, "15min")
        prev_close = previous_day_close(spot_1m, day)

        classifier._current = None
        classifier._candidate = None

        # Track if a position is "open" for purposes of preventing overlap.
        # We use entry-cutoff-aware logic: once a TradeRecord is captured at ts,
        # we don't allow another entry until end_of_day to keep this simple.
        in_position_until: Optional[pd.Timestamp] = None

        vix_today = vix_1m[vix_1m.index.date == day] if vix_1m is not None else None

        for ts, row in day_5m.iterrows():
            spot_close = float(row["close"])

            feat = build_feature_for_bar(ts, day_5m, day_15m, prev_close, vix_1m)
            regime = classifier.classify(feat)

            if in_position_until is not None and ts < in_position_until:
                continue
            if ts.time() < ENTRY_AFTER or ts.time() >= ENTRY_CUTOFF:
                continue
            if regime_gated and regime != Regime.RANGE:
                continue
            if regime in (Regime.NO_TRADE, Regime.WAIT, Regime.EXPIRY):
                continue

            # Build chain state (cluster S/R + focus PCR + OI changes)
            atm = min({k[0] for k in chain.keys()},
                      key=lambda x: abs(x - spot_close))
            chain_state = reconstruct_chain_state(chain, chain, ts, spot_close)

            ts_prev = ts - pd.Timedelta(minutes=5)
            focus_strikes = [atm + (i * STRIKE_STEP) for i in range(-3, 4)]

            def focus_oi_total(side: str, when: pd.Timestamp) -> float:
                tot = 0.0
                for s in focus_strikes:
                    df = chain.get((s, side))
                    if df is None:
                        continue
                    try:
                        r = df.loc[when]
                    except KeyError:
                        w = df.loc[when - pd.Timedelta(minutes=2):when]
                        if w.empty:
                            continue
                        r = w.iloc[-1]
                    if isinstance(r, pd.DataFrame):
                        r = r.iloc[0]
                    tot += float(r.get("open_interest", 0))
                return tot

            ce_now = focus_oi_total("CE", ts)
            pe_now = focus_oi_total("PE", ts)
            ce_prev = focus_oi_total("CE", ts_prev)
            pe_prev = focus_oi_total("PE", ts_prev)
            chain_state["oi_pattern"] = {
                "ce_oi_change": int(ce_now - ce_prev),
                "pe_oi_change": int(pe_now - pe_prev),
            }

            spot_history = build_spot_history(spot_1m, ts)
            vix_level = 15.0
            if vix_today is not None and not vix_today.empty:
                vw = vix_today[vix_today.index <= ts]
                if not vw.empty:
                    vix_level = float(vw.iloc[-1]["close"])

            sig = engine.evaluate(
                spot_close=spot_close,
                support=chain_state["support"],
                resistance=chain_state["resistance"],
                focus_pcr=chain_state["focus_pcr"],
                oi_pattern=chain_state["oi_pattern"],
                spot_history=spot_history,
                india_vix=vix_level,
                expiry_date=exp.isoformat(),
                current_date=day_str,
            )
            direction = sig["direction"]
            if direction is None:
                continue

            atm_strike = int(round(spot_close / STRIKE_STEP) * STRIKE_STEP)
            entry_opt = chain.get((atm_strike, direction))
            if entry_opt is None:
                continue
            try:
                entry_premium = float(entry_opt.loc[ts, "close"])
            except KeyError:
                continue
            if entry_premium < MIN_ENTRY_PREMIUM:
                continue

            # End of day for capture window
            day_eod = pd.Timestamp.combine(day, time(14, 30)).tz_localize(ts.tz)
            ts_list, close_list, high_list, low_list = capture_option_path(
                chain, atm_strike, direction, ts, day_eod
            )
            if not ts_list:
                continue

            rec = TradeRecord(
                day=day_str,
                entry_ts=ts,
                direction=direction,
                strike=atm_strike,
                entry_premium=entry_premium,
                regime_at_entry=regime.value,
                path_ts=ts_list,
                path_close=close_list,
                path_high=high_list,
                path_low=low_list,
            )
            records.append(rec)
            in_position_until = day_eod  # one trade per day per pass

    return records


# ============================================================================
# Replay engine — walk recorded paths under a given (sl, tp, time_stop)
# ============================================================================

def replay_record(
    rec: TradeRecord,
    sl_pct: float,
    tp_pct: float,
    time_stop_min: int,
) -> dict:
    """Apply exit rules to a captured path and return PnL/exit details."""
    if not rec.path_ts:
        return {"net_pnl": 0.0, "exit_reason": "NO_PATH"}
    entry = rec.entry_premium
    tp = entry * (1 + tp_pct)
    sl = entry * (1 - sl_pct)
    eff_entry = entry * (1 + SLIPPAGE)

    exit_premium = float(rec.path_close[-1])
    exit_reason = "EOD"

    for i, ts in enumerate(rec.path_ts):
        if i == 0:
            continue  # entry bar
        elapsed = (ts - rec.entry_ts).total_seconds() / 60
        ohigh = rec.path_high[i]
        olow = rec.path_low[i]
        oclose = rec.path_close[i]
        if ohigh >= tp:
            exit_premium = tp
            exit_reason = "TP"
            break
        if olow <= sl:
            exit_premium = sl
            exit_reason = "SL"
            break
        if elapsed >= time_stop_min:
            exit_premium = oclose
            exit_reason = "TIME_STOP"
            break

    eff_exit = exit_premium * (1 - SLIPPAGE)
    gross_pnl = (eff_exit - eff_entry) * LOT_SIZE
    net_pnl = gross_pnl - (BROKERAGE_PER_LEG * 2)
    return {"net_pnl": net_pnl, "exit_reason": exit_reason,
            "exit_premium": exit_premium}


# ============================================================================
# Sweep
# ============================================================================

def sweep(records: list[TradeRecord]) -> pd.DataFrame:
    rows = []
    for sl, tp, ts_min in product(SL_GRID, TP_GRID, TIME_STOP_GRID):
        results = [replay_record(r, sl, tp, ts_min) for r in records]
        df = pd.DataFrame(results)
        winners = df[df["net_pnl"] > 0]
        losers = df[df["net_pnl"] <= 0]
        cum = df["net_pnl"].cumsum()
        max_dd = (cum.cummax() - cum).max() if len(df) else 0.0
        gross_profit = winners["net_pnl"].sum() if len(winners) else 0.0
        gross_loss = abs(losers["net_pnl"].sum()) if len(losers) else 0.0
        rows.append({
            "sl_pct": sl,
            "tp_pct": tp,
            "time_stop_min": ts_min,
            "trades": len(df),
            "win_rate": len(winners) / len(df) * 100 if len(df) else 0,
            "winners": len(winners),
            "losers": len(losers),
            "net_pnl": float(df["net_pnl"].sum()),
            "avg_win": float(winners["net_pnl"].mean()) if len(winners) else 0,
            "avg_loss": float(losers["net_pnl"].mean()) if len(losers) else 0,
            "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
            "max_dd": float(max_dd),
            "return_over_dd": float(df["net_pnl"].sum() / max_dd) if max_dd > 0 else float("inf"),
        })
    return pd.DataFrame(rows)


# ============================================================================
# Report
# ============================================================================

def write_report(
    records_baseline: list[TradeRecord],
    records_gated: list[TradeRecord],
    sweep_baseline: pd.DataFrame,
    sweep_gated: pd.DataFrame,
) -> Path:
    out = ROOT / "reports" / "phase5_param_sweep_report.md"
    lines: list[str] = []
    lines.append("# Phase 5 — Parameter Sweep Over Production SignalEngine\n")
    lines.append(f"Search grid: SL {SL_GRID}, TP {TP_GRID}, "
                 f"time_stop {TIME_STOP_GRID} mins.")
    lines.append(f"Total combinations evaluated: "
                 f"{len(SL_GRID) * len(TP_GRID) * len(TIME_STOP_GRID)}")
    lines.append(f"Captured records — baseline: {len(records_baseline)}, "
                 f"regime-gated: {len(records_gated)}\n")

    lines.append("## Production defaults (Phase 4 reference)")
    lines.append("- SL 30% / TP 50% / time_stop 120 min")
    lines.append("- Baseline result: -Rs 29,100 across 159 trades")
    lines.append("- Regime-gated: -Rs 23,174 across 125 trades\n")

    for label, df in [("BASELINE (always armed)", sweep_baseline),
                      ("REGIME-GATED (RANGE only)", sweep_gated)]:
        lines.append(f"## {label}\n")
        lines.append("### Top 10 by Net P&L\n")
        top = df.nlargest(10, "net_pnl")
        lines.append("| SL | TP | Time | Trades | Win% | Net P&L | Avg Win | Avg Loss | Profit Factor | Max DD | P&L/DD |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in top.iterrows():
            lines.append(
                f"| {r['sl_pct']:.0%} | {r['tp_pct']:.0%} | "
                f"{int(r['time_stop_min'])} | {int(r['trades'])} | "
                f"{r['win_rate']:.1f} | Rs {r['net_pnl']:,.0f} | "
                f"Rs {r['avg_win']:,.0f} | Rs {r['avg_loss']:,.0f} | "
                f"{r['profit_factor']:.2f} | Rs {r['max_dd']:,.0f} | "
                f"{r['return_over_dd']:.2f} |"
            )

        lines.append("\n### Top 10 by Profit Factor (>= 30 trades)\n")
        df_pf = df[df["trades"] >= 30]
        top_pf = df_pf.nlargest(10, "profit_factor")
        lines.append("| SL | TP | Time | Trades | Win% | Net P&L | Profit Factor |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in top_pf.iterrows():
            lines.append(
                f"| {r['sl_pct']:.0%} | {r['tp_pct']:.0%} | "
                f"{int(r['time_stop_min'])} | {int(r['trades'])} | "
                f"{r['win_rate']:.1f} | Rs {r['net_pnl']:,.0f} | "
                f"{r['profit_factor']:.2f} |"
            )

        lines.append("\n### Top 10 by Return / Max-DD (>= 30 trades)\n")
        top_rd = df_pf.nlargest(10, "return_over_dd")
        lines.append("| SL | TP | Time | Trades | Win% | Net P&L | Max DD | P&L/DD |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in top_rd.iterrows():
            lines.append(
                f"| {r['sl_pct']:.0%} | {r['tp_pct']:.0%} | "
                f"{int(r['time_stop_min'])} | {int(r['trades'])} | "
                f"{r['win_rate']:.1f} | Rs {r['net_pnl']:,.0f} | "
                f"Rs {r['max_dd']:,.0f} | {r['return_over_dd']:.2f} |"
            )
        lines.append("")

    # Recommendation
    best_baseline = sweep_baseline.loc[sweep_baseline["net_pnl"].idxmax()]
    best_gated = sweep_gated.loc[sweep_gated["net_pnl"].idxmax()]
    lines.append("## Recommendation\n")
    lines.append("**Best parameters by net P&L:**\n")
    lines.append(f"- Baseline:   SL {best_baseline['sl_pct']:.0%}, "
                 f"TP {best_baseline['tp_pct']:.0%}, "
                 f"time {int(best_baseline['time_stop_min'])} min "
                 f"-> Rs {best_baseline['net_pnl']:,.0f} across "
                 f"{int(best_baseline['trades'])} trades")
    lines.append(f"- Gated:      SL {best_gated['sl_pct']:.0%}, "
                 f"TP {best_gated['tp_pct']:.0%}, "
                 f"time {int(best_gated['time_stop_min'])} min "
                 f"-> Rs {best_gated['net_pnl']:,.0f} across "
                 f"{int(best_gated['trades'])} trades\n")
    lines.append("Caveats:")
    lines.append("- This sample is a +7% bullish year. Optimal params may "
                 "differ in sideways/bearish regimes.")
    lines.append("- The grid is coarse. After identifying promising regions, "
                 "narrow the grid and re-sweep.")
    lines.append("- Beware overfitting: any combo that wins on 1 year of data "
                 "must be validated walk-forward before live deployment.\n")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    return out


# ============================================================================
# Main
# ============================================================================

def _save_sweep_csv(df: pd.DataFrame, name: str) -> Path:
    p = ROOT / "reports" / f"phase5_sweep_{name}.csv"
    df.to_csv(p, index=False)
    return p


def main() -> None:
    spot_1m = load_spot()
    vix_1m = load_vix()
    expiries_by_date = discover_expiries(ROOT / "data")
    print(f"Loaded spot rows={len(spot_1m):,}  expiries={len(expiries_by_date)}")

    print("\nCapturing trade paths — BASELINE (always armed)...")
    rec_b = simulate_and_capture(spot_1m, vix_1m, expiries_by_date, regime_gated=False)
    print(f"Captured {len(rec_b)} baseline records")

    print("\nCapturing trade paths — REGIME-GATED (RANGE only)...")
    rec_g = simulate_and_capture(spot_1m, vix_1m, expiries_by_date, regime_gated=True)
    print(f"Captured {len(rec_g)} regime-gated records")

    print("\nSweeping baseline parameter grid...")
    sweep_b = sweep(rec_b)
    print(f"Evaluated {len(sweep_b)} combos")

    print("Sweeping regime-gated parameter grid...")
    sweep_g = sweep(rec_g)

    csv_b = _save_sweep_csv(sweep_b, "baseline")
    csv_g = _save_sweep_csv(sweep_g, "gated")
    print(f"\nSaved CSV: {csv_b.relative_to(ROOT)}, {csv_g.relative_to(ROOT)}")

    report = write_report(rec_b, rec_g, sweep_b, sweep_g)
    print(f"Report: {report.relative_to(ROOT)}")

    # Top-line summary on stdout
    best_b = sweep_b.loc[sweep_b["net_pnl"].idxmax()]
    best_g = sweep_g.loc[sweep_g["net_pnl"].idxmax()]
    print("\n=== BEST PARAMS ===")
    print(f"  baseline: SL {best_b['sl_pct']:.0%}  TP {best_b['tp_pct']:.0%}  "
          f"time {int(best_b['time_stop_min'])}m  ->  Rs {best_b['net_pnl']:,.0f}")
    print(f"  gated:    SL {best_g['sl_pct']:.0%}  TP {best_g['tp_pct']:.0%}  "
          f"time {int(best_g['time_stop_min'])}m  ->  Rs {best_g['net_pnl']:,.0f}")


if __name__ == "__main__":
    main()
