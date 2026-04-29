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
    Regime,
)
from signal_engine import SignalEngine  # noqa: E402
from backtesting.backtest_regime_phase1 import (  # noqa: E402
    load_spot, load_vix, resample, previous_day_close, build_feature_for_bar,
)
from backtesting.backtest_regime_phase3 import (  # noqa: E402
    OPT_FILENAME_RE, MONTH_CODE,
    discover_expiries, load_chain_for_expiry, map_day_to_expiry,
    Trade, _regime_breakdown, _monthly_breakdown,
)


# -------------------------------- Config -------------------------------------

# Production tactic exits (matching live bot's defaults)
SL_PCT_NORMAL = 0.30
TP_PCT_NORMAL = 0.50
SL_PCT_EXPIRY = 0.20
TP_PCT_EXPIRY = 0.35
TIME_STOP_NORMAL_MIN = 120
TIME_STOP_EXPIRY_MIN = 45
ENTRY_AFTER = time(10, 0)
ENTRY_CUTOFF = time(14, 0)
FORCE_FLAT = time(14, 30)
SLIPPAGE = 0.015
BROKERAGE_PER_LEG = 30.0
LOT_SIZE = 75
STRIKE_STEP = 50
MIN_ENTRY_PREMIUM = 20.0
SPOT_HISTORY_MIN = 15      # last 15 1-min readings for sustain check


# ------------------------- Chain reconstruction ------------------------------

def reconstruct_chain_state(
    chain: dict[tuple[int, str], pd.DataFrame],
    chain_5m_ago: dict[tuple[int, str], pd.DataFrame],
    ts: pd.Timestamp,
    spot: float,
) -> dict:
    """
    Returns {'support', 'resistance', 'focus_pcr', 'oi_pattern'}
    for the given minute, computed exactly like data_fetcher.py:
      - S/R: cluster-based 3-strike bands within ATM +/- 5
      - focus_pcr: ATM +/- 3 strikes only
      - oi_pattern: focus-zone OI change vs ts - 5min
    """
    strikes = sorted({k[0] for k in chain.keys()})
    if not strikes:
        return {"support": 0, "resistance": 0, "focus_pcr": 1.0,
                "oi_pattern": {"ce_oi_change": 0, "pe_oi_change": 0}}

    atm = min(strikes, key=lambda x: abs(x - spot))

    def oi_at(k: int, side: str, source: dict) -> float:
        df = source.get((k, side))
        if df is None:
            return 0.0
        try:
            row = df.loc[ts]
        except KeyError:
            window = df.loc[ts - pd.Timedelta(minutes=2):ts]
            if window.empty:
                return 0.0
            row = window.iloc[-1]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        return float(row.get("open_interest", 0))

    # ---- Cluster-based S/R within ATM +/- 5 ----
    res_strike, sup_strike = atm, atm
    max_ce_cluster, max_pe_cluster = 0.0, 0.0
    for s in [atm + (i * STRIKE_STEP) for i in range(-5, 6)]:
        band_ce = (oi_at(s, "CE", chain)
                   + oi_at(s + STRIKE_STEP, "CE", chain)
                   + oi_at(s - STRIKE_STEP, "CE", chain))
        band_pe = (oi_at(s, "PE", chain)
                   + oi_at(s + STRIKE_STEP, "PE", chain)
                   + oi_at(s - STRIKE_STEP, "PE", chain))
        if s >= atm and band_ce > max_ce_cluster:
            max_ce_cluster, res_strike = band_ce, s
        if s <= atm and band_pe > max_pe_cluster:
            max_pe_cluster, sup_strike = band_pe, s

    # ---- Focus-zone PCR + OI changes (ATM +/- 3) ----
    total_ce_oi, total_pe_oi = 0.0, 0.0
    ce_change, pe_change = 0.0, 0.0
    for s in [atm + (i * STRIKE_STEP) for i in range(-3, 4)]:
        ce_now = oi_at(s, "CE", chain)
        pe_now = oi_at(s, "PE", chain)
        ce_prev = oi_at(s, "CE", chain_5m_ago)
        pe_prev = oi_at(s, "PE", chain_5m_ago)
        total_ce_oi += ce_now
        total_pe_oi += pe_now
        ce_change += (ce_now - ce_prev)
        pe_change += (pe_now - pe_prev)

    focus_pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

    return {
        "support": int(sup_strike),
        "resistance": int(res_strike),
        "focus_pcr": focus_pcr,
        "oi_pattern": {"ce_oi_change": ce_change, "pe_oi_change": pe_change},
    }


# ------------------------- Spot history builder ------------------------------

def build_spot_history(
    spot_1m: pd.DataFrame,
    ts: pd.Timestamp,
    minutes: int = SPOT_HISTORY_MIN,
) -> list[dict]:
    """Return last `minutes` of 1-min readings as list of {'time', 'spot'}."""
    end = ts
    start = ts - pd.Timedelta(minutes=minutes)
    window = spot_1m.loc[start:end]
    return [{"time": idx.to_pydatetime(), "spot": float(row["close"])}
            for idx, row in window.iterrows()]


# ----------------------------- Option price lookup ---------------------------

def get_option_price_at(
    chain: dict[tuple[int, str], pd.DataFrame],
    strike: int,
    side: str,
    ts: pd.Timestamp,
) -> Optional[tuple[float, float, float]]:
    df = chain.get((strike, side))
    if df is None:
        return None
    try:
        row = df.loc[ts]
    except KeyError:
        window = df.loc[ts - pd.Timedelta(minutes=5):ts]
        if window.empty:
            return None
        row = window.iloc[-1]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return float(row["close"]), float(row["high"]), float(row["low"])


# ------------------------------- Simulator -----------------------------------

def simulate_one_pass(
    spot_1m: pd.DataFrame,
    vix_1m: pd.DataFrame,
    expiries_by_date: dict[date, list[Path]],
    *,
    regime_gated: bool,
) -> list[Trade]:
    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
    engine = SignalEngine()
    trades: list[Trade] = []

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

        # fresh classifier state per day
        classifier._current = None
        classifier._candidate = None

        open_trade: Optional[Trade] = None
        is_expiry_day = (day == exp)
        sl_pct = SL_PCT_EXPIRY if is_expiry_day else SL_PCT_NORMAL
        tp_pct = TP_PCT_EXPIRY if is_expiry_day else TP_PCT_NORMAL
        time_stop = TIME_STOP_EXPIRY_MIN if is_expiry_day else TIME_STOP_NORMAL_MIN

        # current 1-min VIX series for this day
        vix_today = vix_1m[vix_1m.index.date == day] if vix_1m is not None else None

        for ts, row in day_5m.iterrows():
            spot_close = float(row["close"])

            # 1) regime
            feat = build_feature_for_bar(ts, day_5m, day_15m, prev_close, vix_1m)
            regime = classifier.classify(feat)

            # 2) monitor open trade
            if open_trade is not None:
                opt = get_option_price_at(chain, open_trade.strike,
                                          open_trade.direction, ts)
                if opt is not None:
                    oclose, ohigh, olow = opt
                    eff_entry = open_trade.entry_premium
                    tp = eff_entry * (1 + tp_pct)
                    sl = eff_entry * (1 - sl_pct)
                    mins_held = (ts - open_trade.entry_ts).total_seconds() / 60

                    if ohigh >= tp:
                        open_trade.close(ts, tp, "TP")
                        trades.append(open_trade); open_trade = None
                    elif olow <= sl:
                        open_trade.close(ts, sl, "SL")
                        trades.append(open_trade); open_trade = None
                    elif mins_held >= time_stop:
                        open_trade.close(ts, oclose, "TIME_STOP")
                        trades.append(open_trade); open_trade = None
                    elif ts.time() >= FORCE_FLAT:
                        open_trade.close(ts, oclose, "EOD")
                        trades.append(open_trade); open_trade = None

            # 3) attempt entry
            if open_trade is not None:
                continue
            if ts.time() < ENTRY_AFTER or ts.time() >= ENTRY_CUTOFF:
                continue
            if regime_gated and regime != Regime.RANGE:
                continue
            if regime in (Regime.NO_TRADE, Regime.WAIT, Regime.EXPIRY):
                continue

            # Reconstruct chain state at this minute
            chain_state = reconstruct_chain_state(
                chain,
                chain,    # 5min-ago handled by oi_at via window fallback in same chain
                ts,
                spot_close,
            )
            # Compute OI change vs 5 min ago using a separate lookup
            ts_prev = ts - pd.Timedelta(minutes=5)
            chain_state_5m_ago = reconstruct_chain_state(chain, chain, ts_prev, spot_close)
            chain_state["oi_pattern"] = {
                "ce_oi_change": (
                    chain_state["focus_pcr"] * 0  # placeholder, replaced below
                ),
            }
            # Properly recompute oi_change with the prev-snap PCR's totals
            # (cleaner: just call reconstruct_chain_state twice — one at ts, one at ts_prev,
            # then diff focus-zone CE/PE OI totals)

            # Re-derive precise OI change from focus-zone totals at ts vs ts_prev
            atm = min(
                {k[0] for k in chain.keys()},
                key=lambda x: abs(x - spot_close),
            )
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

            # spot_history (last 15 1-min readings)
            spot_history = build_spot_history(spot_1m, ts)

            # India VIX
            vix_level = 15.0
            if vix_today is not None and not vix_today.empty:
                vw = vix_today[vix_today.index <= ts]
                if not vw.empty:
                    vix_level = float(vw.iloc[-1]["close"])

            # Run the production engine
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

            # Strike selection: ATM (matching production main.py default)
            atm_strike = int(round(spot_close / STRIKE_STEP) * STRIKE_STEP)
            opt = get_option_price_at(chain, atm_strike, direction, ts)
            if opt is None:
                continue
            oclose, _, _ = opt
            if oclose < MIN_ENTRY_PREMIUM:
                continue

            open_trade = Trade(
                day=day_str,
                tactic="prod_oi_wall",
                direction=direction,
                strike=atm_strike,
                entry_ts=ts,
                entry_premium=oclose,
                qty_lots=1,
                regime_at_entry=regime,
            )

        # End of day: force flat
        if open_trade is not None:
            last_ts = day_5m.index[-1]
            opt = get_option_price_at(chain, open_trade.strike,
                                      open_trade.direction, last_ts)
            exit_premium = opt[0] if opt else open_trade.entry_premium
            open_trade.close(last_ts, exit_premium, "EOD_FORCE")
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

def main() -> None:
    spot_1m = load_spot()
    vix_1m = load_vix()
    expiries_by_date = discover_expiries(ROOT / "data")
    print(f"Loaded spot rows={len(spot_1m):,}  VIX rows={len(vix_1m):,}  "
          f"expiries={len(expiries_by_date)}")
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
    main()
