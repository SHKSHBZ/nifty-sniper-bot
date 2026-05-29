"""
Phase 4 — Production SignalEngine backtest, baseline vs regime-gated.

Same architecture as Phase 3, but instead of the simplified VWAP-extension
mean-reversion stand-in, this harness wires the actual production logic
from signal_engine.py:

    Gate 0: India VIX macro trend (CE entries blocked when VIX>=18, etc.)
    Gate 1: Spot sustain near OI wall for 3 consecutive 5m candles
    Gate 2: Focus-zone PCR confirmation (ATM +/- 3 strikes)
    Gate 3: OI build-up confirmation (writers defending the wall)

Per-minute option-chain reconstruction matches data_fetcher.py:
    - Cluster-based support/resistance: 3-strike bands within ATM +/- 5
    - Focus PCR: ATM +/- 3 strikes only
    - OI changes: current minute vs same strike 5 minutes ago

The SAME simulator runs twice (baseline = always armed; regime-gated =
RANGE-only) with everything else identical, so we can attribute the P&L
delta entirely to the regime gate.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.classifier import (  # noqa: E402
    RegimeClassifier,
    ClassifierConfig,
    ClassifierFeatures,
    Regime,
)
from signal_engine import SignalEngine  # noqa: E402
from backtesting.backtest_regime_phase1 import (  # noqa: E402
    load_spot, load_vix, resample,
)
from backtesting.backtest_regime_phase3 import (  # noqa: E402
    Trade, _regime_breakdown, _monthly_breakdown,
)


# -------------------------------- Config -------------------------------------

# Read tunable parameters from Options.json so backtest reflects live config
import json as _json
from pathlib import Path as _Path
try:
    _opts = _json.loads((_Path(__file__).resolve().parent.parent / "Options.json").read_text())
    _cp = _opts.get("configurableParameters", {})
except Exception:
    _cp = {}

# Production tactic exits (matching live bot's defaults)
SL_PCT_NORMAL = float(_cp.get("normalDayStopLossPercent", 30)) / 100.0
TP_PCT_NORMAL = float(_cp.get("normalDayTargetPercent", 50)) / 100.0
SL_PCT_EXPIRY = float(_cp.get("expiryDayStopLossPercent", 20)) / 100.0
TP_PCT_EXPIRY = float(_cp.get("expiryDayTargetPercent", 35)) / 100.0
TIME_STOP_NORMAL_MIN = int(_cp.get("thetaShieldNormalMins", 120))
TIME_STOP_EXPIRY_MIN = int(_cp.get("thetaShieldExpiryMins", 45))
ENTRY_AFTER = time(10, 5)       # aligned with market_hours.py ENTRY_WINDOW_OPEN change
ENTRY_CUTOFF = time(14, 0)
FORCE_FLAT = time(14, 30)
SLIPPAGE = 0.015
FOCUS_ZONE_HALF = 3             # ±3 strikes for focus PCR (can be overridden)
BROKERAGE_PER_LEG = 30.0
LOT_SIZE = 75
STRIKE_STEP = 50
MIN_ENTRY_PREMIUM = 20.0
SPOT_HISTORY_MIN = 15      # last 15 1-min readings for sustain check


# ------------------------- Daily chain state builder -------------------------

_DAILY_CHAIN_CACHE: dict[date, Optional[pd.DataFrame]] = {}

def load_daily_chain(day: date) -> Optional[pd.DataFrame]:
    """Load the pre-merged daily option chain file for a trading day (cached)."""
    if day in _DAILY_CHAIN_CACHE:
        return _DAILY_CHAIN_CACHE[day]
    path = ROOT / "data" / "daily_chain" / f"daily_chain_NIFTY_{day.isoformat()}.csv"
    if not path.exists():
        _DAILY_CHAIN_CACHE[day] = None
        return None
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    _DAILY_CHAIN_CACHE[day] = df
    return df


def get_chain_state_at(
    daily: pd.DataFrame,
    ts: pd.Timestamp,
    spot: float,
) -> dict:
    """
    Reconstruct support/resistance/focus_pcr/oi_pattern from daily chain,
    matching the EXACT logic of the original reconstruct_chain_state().

    Key difference from previous version: uses a 2-minute fallback window
    for OI lookups (matching old per-expiry behavior), so missing timestamps
    don't collapse signals.
    """
    def _oi(strike: int, side: str, df: pd.DataFrame, t: pd.Timestamp) -> float:
        """Lookup OI at a strike/side/timestamp with 2-min fallback."""
        col = f"{side.lower()}_oi"
        row = df[(df["strike"] == strike) & (df["timestamp"] == t)]
        if not row.empty:
            return float(row.iloc[0][col])
        # 2-min fallback window (matching old reconstruct_chain_state)
        window = df[(df["strike"] == strike) &
                    (df["timestamp"] >= t - pd.Timedelta(minutes=2)) &
                    (df["timestamp"] <= t)]
        if window.empty:
            return 0.0
        return float(window.iloc[-1][col])

    def _ltp(strike: int, side: str, df: pd.DataFrame, t: pd.Timestamp) -> Optional[float]:
        """Lookup LTP at a strike/side/timestamp with 2-min fallback."""
        col = f"{side.lower()}_ltp"
        row = df[(df["strike"] == strike) & (df["timestamp"] == t)]
        if not row.empty:
            return float(row.iloc[0][col])
        window = df[(df["strike"] == strike) &
                    (df["timestamp"] >= t - pd.Timedelta(minutes=2)) &
                    (df["timestamp"] <= t)]
        if window.empty:
            return None
        return float(window.iloc[-1][col])

    strikes = sorted(daily["strike"].unique())
    if not len(strikes):
        return {"support": 0, "resistance": 0, "focus_pcr": 1.0,
                "oi_pattern": {"ce_oi_change": 0, "pe_oi_change": 0}}

    atm = min(strikes, key=lambda x: abs(x - spot))

    # ---- Cluster-based S/R within ATM +/- 5 (3-strike bands) ----
    res_strike = sup_strike = atm
    max_ce_cluster = max_pe_cluster = 0.0
    for s in [atm + (i * STRIKE_STEP) for i in range(-5, 6)]:
        band_ce = (_oi(s, "CE", daily, ts)
                   + _oi(s + STRIKE_STEP, "CE", daily, ts)
                   + _oi(s - STRIKE_STEP, "CE", daily, ts))
        band_pe = (_oi(s, "PE", daily, ts)
                   + _oi(s + STRIKE_STEP, "PE", daily, ts)
                   + _oi(s - STRIKE_STEP, "PE", daily, ts))
        if s >= atm and band_ce > max_ce_cluster:
            max_ce_cluster = band_ce
            res_strike = s
        if s <= atm and band_pe > max_pe_cluster:
            max_pe_cluster = band_pe
            sup_strike = s

    # ---- Focus-zone PCR + OI changes (ATM +/- FOCUS_ZONE_HALF) ----
    total_ce_oi = total_pe_oi = 0.0
    ce_change = pe_change = 0.0
    ts_prev = ts - pd.Timedelta(minutes=5)
    for s in [atm + (i * STRIKE_STEP) for i in range(-FOCUS_ZONE_HALF, FOCUS_ZONE_HALF + 1)]:
        ce_now = _oi(s, "CE", daily, ts)
        pe_now = _oi(s, "PE", daily, ts)
        ce_prev = _oi(s, "CE", daily, ts_prev)
        pe_prev = _oi(s, "PE", daily, ts_prev)
        total_ce_oi += ce_now
        total_pe_oi += pe_now
        ce_change += (ce_now - ce_prev)
        pe_change += (pe_now - pe_prev)

    focus_pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

    return {
        "support": int(sup_strike),
        "resistance": int(res_strike),
        "focus_pcr": focus_pcr,
        "oi_pattern": {
            "ce_oi_change": int(ce_change),
            "pe_oi_change": int(pe_change),
        },
    }


# Backward-compat aliases for other scripts that import from phase4
def reconstruct_chain_state(chain, chain_5m_ago, ts, spot):
    """Legacy signature — delegates to get_chain_state_at using chain as daily df."""
    return get_chain_state_at(chain, ts, spot)


def build_spot_history(spot_1m, ts, minutes=15):
    """Replicates old helper — builds list of {time, spot} dicts."""
    window = spot_1m.loc[ts - pd.Timedelta(minutes=minutes):ts]
    return [
        {"time": idx.to_pydatetime(), "spot": float(r["close"])}
        for idx, r in window.iterrows()
    ]


def get_option_premium_at(
    daily: pd.DataFrame,
    strike: int,
    side: str,
    ts: pd.Timestamp,
) -> Optional[float]:
    """Get option premium (close price) for a strike at or before ts.
    
    Uses 2-minute fallback window to match old per-expiry behavior.
    """
    col = f"{side.lower()}_ltp"
    row = daily[(daily["strike"] == strike) & (daily["timestamp"] == ts)]
    if not row.empty:
        return float(row.iloc[0][col])
    # 2-min fallback
    window = daily[(daily["strike"] == strike) &
                   (daily["timestamp"] >= ts - pd.Timedelta(minutes=2)) &
                   (daily["timestamp"] <= ts)]
    if window.empty:
        return None
    return float(window.iloc[-1][col])


# ------------------------------- Simulator -----------------------------------

def simulate_one_pass(
    spot_1m: pd.DataFrame,
    vix_1m: pd.DataFrame,
    expiries_by_date: dict[date, list[Path]],   # kept for signature compat; not used
    *,
    regime_gated: bool,
) -> list[Trade]:
    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
    engine = SignalEngine()
    trades: list[Trade] = []

    trading_days = sorted({d for d in spot_1m.index.date})

    for day in trading_days:
        daily = load_daily_chain(day)
        if daily is None:
            continue

        day_str = day.isoformat()
        day_5m = resample(spot_1m[spot_1m.index.date == day], "5min")
        if day_5m.empty:
            continue

        # fresh classifier state per day
        classifier._current = None  # type: ignore
        classifier._candidate = None  # type: ignore

        open_trade: Optional[Trade] = None
        sl_pct = SL_PCT_NORMAL
        tp_pct = TP_PCT_NORMAL
        time_stop = TIME_STOP_NORMAL_MIN
        is_expiry_day = False

        for ts, row in day_5m.iterrows():
            spot_close = float(row["close"])

            # Build regime classifier features
            feat = ClassifierFeatures(
                ts=ts.to_pydatetime(),
                gap_pct=0.0, or_range_pct=0.0, avg_or_range_pct=0.0025,
                adx_15m=0.0, range_ratio=1.0, vwap_slope_30m=0.0,
                dist_from_vwap_pct=0.0, price=spot_close, vwap=spot_close,
                or_high=0.0, or_low=0.0, vix_level=15.0, vix_chg_15m=0.0,
                dte=5, event_flag=False, prev_day_close=spot_close,
            )
            regime = classifier.classify(feat)

            # Monitor open trade
            if open_trade is not None:
                opt_prem = get_option_premium_at(daily, open_trade.strike,
                                                  open_trade.direction, ts)
                if opt_prem is not None:
                    eff_entry = open_trade.entry_premium
                    tp = eff_entry * (1 + tp_pct)
                    sl = eff_entry * (1 - sl_pct)
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
            if regime_gated and regime != Regime.RANGE:
                continue
            if regime in (Regime.NO_TRADE, Regime.WAIT, Regime.EXPIRY):
                continue

            # Build chain state from daily data
            chain_state = get_chain_state_at(daily, ts, spot_close)

            # Spot history
            spot_history = [
                {"time": idx.to_pydatetime(), "spot": float(r["close"])}
                for idx, r in spot_1m.loc[ts - pd.Timedelta(minutes=15):ts].iterrows()
            ]

            # VIX
            vix_level = 15.0
            vw = vix_1m[vix_1m.index <= ts]
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
                expiry_date=day_str,
                current_date=day_str,
            )

            direction = sig["direction"]
            if direction is None:
                continue

            atm_strike = int(round(spot_close / STRIKE_STEP) * STRIKE_STEP)
            opt_prem = get_option_premium_at(daily, atm_strike, direction, ts)
            if opt_prem is None or opt_prem < MIN_ENTRY_PREMIUM:
                continue

            open_trade = Trade(
                day=day_str,
                tactic="prod_oi_wall",
                direction=direction,
                strike=atm_strike,
                entry_ts=ts,
                entry_premium=opt_prem,
                qty_lots=1,
                regime_at_entry=regime,
            )

        # EOD force flat
        if open_trade is not None:
            last_ts = day_5m.index[-1]
            opt_prem = get_option_premium_at(daily, open_trade.strike,
                                              open_trade.direction, last_ts)
            exit_prem = opt_prem if opt_prem else open_trade.entry_premium
            open_trade.close(last_ts, exit_prem, "EOD_FORCE")
            trades.append(open_trade)

    return trades


# --------------------------------- Report ------------------------------------

def summarize(trades: list[Trade], label: str) -> dict:
    if not trades:
        return {"label": label, "trades": 0, "net_pnl": 0, "win_rate": 0,
                "avg_win": 0, "avg_loss": 0, "exit_reasons": {}}
    df = pd.DataFrame([t.__dict__ for t in trades])
    winners = df[df["net_pnl"] > 0]
    losers = df[df["net_pnl"] <= 0]
    return {
        "label": label,
        "trades": len(df),
        "net_pnl": df["net_pnl"].sum(),
        "gross_pnl": df["gross_pnl"].sum(),
        "win_rate": len(winners) / len(df) * 100,
        "winners": len(winners),
        "losers": len(losers),
        "avg_win": winners["net_pnl"].mean() if len(winners) else 0.0,
        "avg_loss": losers["net_pnl"].mean() if len(losers) else 0.0,
        "max_dd_estimate": df["net_pnl"].cumsum().cummax().sub(
            df["net_pnl"].cumsum()).max(),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }


def write_report(baseline: dict, gated: dict,
                 baseline_trades: list[Trade], gated_trades: list[Trade]) -> Path:
    out = ROOT / "reports" / "phase4_production_backtest_report.md"
    lines: list[str] = []
    days = sorted({t.day for t in baseline_trades})
    lines.append("# Phase 4 — Production SignalEngine Backtest: Baseline vs Regime-Gated\n")
    if days:
        lines.append(f"Period: {days[0]} to {days[-1]} "
                     f"({len(days)} trading days with at least one entry)\n")
    lines.append(
        "Tactic: production 3-gate OI-wall mean-reversion logic from "
        "`signal_engine.py`. Gate 0 (VIX), Gate 1 (sustain), Gate 2 (focus PCR), "
        "Gate 3 (OI build-up). Per-minute chain reconstruction matches "
        "`data_fetcher.py`.\n"
    )
    lines.append("")

    lines.append("## Side-by-Side Results\n")
    lines.append("| Metric | Baseline (always armed) | Regime-gated (RANGE only) |")
    lines.append("|---|---:|---:|")
    for k in ["trades", "winners", "losers", "win_rate", "net_pnl", "gross_pnl",
              "avg_win", "avg_loss", "max_dd_estimate"]:
        b = baseline.get(k, 0)
        g = gated.get(k, 0)
        fmt_b = f"{b:,.2f}" if isinstance(b, float) else str(b)
        fmt_g = f"{g:,.2f}" if isinstance(g, float) else str(g)
        lines.append(f"| {k} | {fmt_b} | {fmt_g} |")

    lines.append("\n## Exit Reason Breakdown\n")
    lines.append("| Reason | Baseline | Regime-gated |")
    lines.append("|---|---:|---:|")
    reasons = set(baseline.get("exit_reasons", {})) | set(gated.get("exit_reasons", {}))
    for r in sorted(reasons):
        lines.append(f"| {r} | {baseline.get('exit_reasons', {}).get(r, 0)} "
                     f"| {gated.get('exit_reasons', {}).get(r, 0)} |")

    lines.append("\n## Baseline P&L By Regime At Entry\n")
    lines.append("| Regime | Trades | Wins | Net P&L |")
    lines.append("|---|---:|---:|---:|")
    rb = _regime_breakdown(baseline_trades)
    for r in sorted(rb.keys(), key=lambda x: -rb[x]["trades"]):
        v = rb[r]
        lines.append(f"| {r} | {v['trades']} | {v['wins']} | Rs {v['net_pnl']:,.0f} |")

    lines.append("\n## Monthly P&L\n")
    lines.append("| Month | Baseline trades | Baseline P&L | Gated trades | Gated P&L |")
    lines.append("|---|---:|---:|---:|---:|")
    mb = _monthly_breakdown(baseline_trades)
    mg = _monthly_breakdown(gated_trades)
    for m in sorted(set(mb) | set(mg)):
        b = mb.get(m, {"trades": 0, "net_pnl": 0})
        g = mg.get(m, {"trades": 0, "net_pnl": 0})
        lines.append(f"| {m} | {b['trades']} | Rs {b['net_pnl']:,.0f} "
                     f"| {g['trades']} | Rs {g['net_pnl']:,.0f} |")

    lines.append("\n## Per-Trade Log — Baseline\n")
    lines.append("| Day | Enter | Exit | Reg@Entry | Dir | Strike | "
                 "EntryPrem | ExitPrem | Reason | Net PnL |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---|---:|")
    for t in baseline_trades:
        lines.append(f"| {t.day} | {t.entry_ts.strftime('%H:%M')} "
                     f"| {t.exit_ts.strftime('%H:%M') if t.exit_ts else '-'} "
                     f"| {t.regime_at_entry.value} | {t.direction} | {t.strike} "
                     f"| {t.entry_premium:.2f} | {t.exit_premium:.2f} "
                     f"| {t.exit_reason} | {t.net_pnl:,.0f} |")

    lines.append("\n## Per-Trade Log — Regime-gated\n")
    lines.append("| Day | Enter | Exit | Reg@Entry | Dir | Strike | "
                 "EntryPrem | ExitPrem | Reason | Net PnL |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---|---:|")
    for t in gated_trades:
        lines.append(f"| {t.day} | {t.entry_ts.strftime('%H:%M')} "
                     f"| {t.exit_ts.strftime('%H:%M') if t.exit_ts else '-'} "
                     f"| {t.regime_at_entry.value} | {t.direction} | {t.strike} "
                     f"| {t.entry_premium:.2f} | {t.exit_premium:.2f} "
                     f"| {t.exit_reason} | {t.net_pnl:,.0f} |")

    lines.append("\n## Interpretation\n")
    diff = gated.get("net_pnl", 0) - baseline.get("net_pnl", 0)
    lines.append(f"- **Net P&L delta (gated minus baseline): Rs {diff:,.0f}**\n")
    if baseline.get("trades", 0) >= 30:
        lines.append("- Sample is statistically usable "
                     f"(n={baseline.get('trades', 0)} baseline trades).\n")
    else:
        lines.append("- Sample is small "
                     f"(n={baseline.get('trades', 0)} baseline trades) — "
                     "directional signal only.\n")
    lines.append("- This is the PRODUCTION 3-gate logic; the only difference between "
                 "the two columns is whether the regime classifier had to be in "
                 "RANGE to allow entry.\n")

    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines))
    return out


# ---------------------------------- Main -------------------------------------

def main(time_stop_override: Optional[int] = None,
         focus_zone_half: Optional[int] = None) -> None:
    """Run Phase 4 backtest with optional parameter overrides.
    
    Args:
        time_stop_override: Override TIME_STOP_NORMAL_MIN (default None = use Options.json)
        focus_zone_half: Override focus zone half-range (e.g. 3 = ±3 strikes, default None = keep hardcoded)
    """
    global TIME_STOP_NORMAL_MIN, FOCUS_ZONE_HALF
    if time_stop_override is not None:
        TIME_STOP_NORMAL_MIN = time_stop_override
        print(f"[OVERRIDE] TIME_STOP_NORMAL_MIN = {TIME_STOP_NORMAL_MIN} min")
    if focus_zone_half is not None:
        FOCUS_ZONE_HALF = focus_zone_half
        print(f"[OVERRIDE] FOCUS_ZONE_HALF = {FOCUS_ZONE_HALF} (PCR uses ±{FOCUS_ZONE_HALF} strikes)")

    spot_1m = load_spot()
    vix_1m = load_vix()
    expiries_by_date: dict[date, list[Path]] = {}
    print(f"Loaded spot rows={len(spot_1m):,}  VIX rows={len(vix_1m):,}")
    print(f"Spot range: {spot_1m.index.min()} -> {spot_1m.index.max()}")

    print("\n=== Baseline (always armed) ===")
    baseline_trades = simulate_one_pass(
        spot_1m, vix_1m, expiries_by_date, regime_gated=False)
    baseline = summarize(baseline_trades, "baseline")
    for k, v in baseline.items():
        if k != "exit_reasons":
            print(f"  {k:<14}  {v}")

    print("\n=== Regime-gated (RANGE only) ===")
    gated_trades = simulate_one_pass(
        spot_1m, vix_1m, expiries_by_date, regime_gated=True)
    gated = summarize(gated_trades, "regime_gated")
    for k, v in gated.items():
        if k != "exit_reasons":
            print(f"  {k:<14}  {v}")

    report = write_report(baseline, gated, baseline_trades, gated_trades)
    print(f"\nReport: {report.relative_to(ROOT)}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # CLI usage: python backtest_regime_phase4.py <time_stop> [focus_zone_half]
        ts = int(sys.argv[1]) if len(sys.argv) > 1 else None
        fz = int(sys.argv[2]) if len(sys.argv) > 2 else None
        print(f"CLI args: time_stop={ts}, focus_zone_half={fz}")
        main(time_stop_override=ts, focus_zone_half=fz)
    else:
        main()
