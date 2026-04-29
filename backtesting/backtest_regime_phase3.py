"""
Phase 3 — Mean-reversion trade simulator, baseline vs regime-gated.

Question we're answering:
    Does gating the existing mean-reversion strategy to only fire during
    RANGE regime improve or hurt P&L?

Simulation scope:
    - All trading days where option chain coverage exists.
      Auto-discovers expiries from data/NIFTY_*_<DD_MMM_YY>_1min.csv files.
      Each trading day uses the NEXT upcoming weekly expiry's options.
    - Single simplified mean-reversion tactic (not the full 3-gate
      OI-wall logic — but same behavior class: fade extremes back to
      VWAP). Enough to answer the directional question.
    - Two parallel runs share ALL inputs. The only difference is the
      regime gate.

Tactic entry:
    - Price > VWAP by `extension_pct` AND candle reclaims back
      (close < midpoint of the bar) -> BUY ATM PE
    - Price < VWAP by `extension_pct` AND candle reclaims back
      (close > midpoint of the bar) -> BUY ATM CE

Tactic exit:
    - TP: +40% on entry premium
    - SL: -25% on entry premium
    - Time stop: 90 minutes
    - EOD: 14:30 force flat

Slippage & fees:
    - Entry fill at close * (1 + 0.015) slippage
    - Exit fill at exit_premium * (1 - 0.015) slippage
    - Rs 30 brokerage per leg (round trip = Rs 60)
    - Lot size 75 (Nifty current). Fixed 1 lot for simplicity.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
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
    compute_session_vwap,
)
from backtesting.backtest_regime_phase1 import (  # noqa: E402
    load_spot,
    load_vix,
    resample,
    previous_day_close,
    build_feature_for_bar,
)


# -------------------------------- Config -------------------------------------

EXTENSION_PCT = 0.0025      # 0.25% from VWAP to trigger
TP_PCT = 0.40
SL_PCT = 0.25
TIME_STOP_MIN = 90
ENTRY_CUTOFF = time(14, 0)
FORCE_FLAT = time(14, 30)
SLIPPAGE = 0.015
BROKERAGE_PER_LEG = 30.0
LOT_SIZE = 75
STRIKE_STEP = 50
MIN_ENTRY_PREMIUM = 20.0   # skip if ATM option too cheap (junk)


OPT_FILENAME_RE = re.compile(
    r"^NIFTY_(\d+)_(CE|PE)_(\d{2})_([A-Z]{3})_(\d{2})_1min\.csv$"
)
MONTH_CODE = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
              "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}


# ---------------------------- Option chain loader ----------------------------

def discover_expiries(data_dir: Path) -> dict[date, list[Path]]:
    """Group option files by expiry date."""
    by_expiry: dict[date, list[Path]] = defaultdict(list)
    for p in data_dir.glob("NIFTY_*_*_1min.csv"):
        m = OPT_FILENAME_RE.match(p.name)
        if not m:
            continue
        d = date(2000 + int(m.group(5)), MONTH_CODE[m.group(4)], int(m.group(3)))
        by_expiry[d].append(p)
    return dict(by_expiry)


def load_chain_for_expiry(
    files: list[Path],
) -> dict[tuple[int, str], pd.DataFrame]:
    """Load all (strike, side) DataFrames for one expiry."""
    out: dict[tuple[int, str], pd.DataFrame] = {}
    for p in files:
        m = OPT_FILENAME_RE.match(p.name)
        if not m:
            continue
        strike = int(m.group(1))
        side = m.group(2)
        df = pd.read_csv(p)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        out[(strike, side)] = df
    return out


def map_day_to_expiry(
    trading_days: list[date],
    expiries: list[date],
) -> dict[date, date]:
    """For each trading day, find the next expiry (>= day)."""
    expiries = sorted(expiries)
    out: dict[date, date] = {}
    for d in trading_days:
        for e in expiries:
            if e >= d:
                out[d] = e
                break
    return out


def get_option_price_at(
    chain: dict[tuple[int, str], pd.DataFrame],
    strike: int,
    side: str,
    ts: pd.Timestamp,
) -> Optional[tuple[float, float, float]]:
    """Return (close, high, low) for the option at timestamp ts (1-min bar)."""
    df = chain.get((strike, side))
    if df is None:
        return None
    # Align to the minute (entry at 5m close -> use the 1-min bar at that minute)
    try:
        row = df.loc[ts]
    except KeyError:
        # fall back to nearest earlier minute within 5m
        window = df.loc[ts - pd.Timedelta(minutes=5):ts]
        if window.empty:
            return None
        row = window.iloc[-1]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return float(row["close"]), float(row["high"]), float(row["low"])


def nearest_strike(spot_price: float, step: int = STRIKE_STEP) -> int:
    return int(round(spot_price / step) * step)


# --------------------------------- Sim core ----------------------------------

@dataclass
class Trade:
    day: str
    tactic: str
    direction: str           # "CE" or "PE"
    strike: int
    entry_ts: pd.Timestamp
    entry_premium: float
    qty_lots: int
    exit_ts: Optional[pd.Timestamp] = None
    exit_premium: float = 0.0
    exit_reason: str = ""
    regime_at_entry: Regime = Regime.RANGE
    gross_pnl: float = 0.0
    net_pnl: float = 0.0

    def close(self, ts, exit_premium, reason):
        self.exit_ts = ts
        self.exit_premium = exit_premium
        self.exit_reason = reason
        # Slippage on entry (paid more) and exit (got less)
        eff_entry = self.entry_premium * (1 + SLIPPAGE)
        eff_exit = exit_premium * (1 - SLIPPAGE)
        self.gross_pnl = (eff_exit - eff_entry) * self.qty_lots * LOT_SIZE
        self.net_pnl = self.gross_pnl - (BROKERAGE_PER_LEG * 2)


def check_entry_signal(
    day_5m_upto: pd.DataFrame,
    vwap_series: pd.Series,
) -> Optional[str]:
    """
    Return 'CE' / 'PE' / None.

    Long (buy CE) signal: price EXTENDED BELOW VWAP, current bar RECLAIMS (close > midpoint)
    Short (buy PE) signal: price EXTENDED ABOVE VWAP, current bar RECLAIMS (close < midpoint)
    """
    if len(day_5m_upto) < 2 or vwap_series.empty:
        return None
    last = day_5m_upto.iloc[-1]
    vwap_now = float(vwap_series.iloc[-1])
    price = float(last["close"])
    midpoint = (float(last["high"]) + float(last["low"])) / 2

    dist = price - vwap_now
    dist_pct = abs(dist) / vwap_now if vwap_now else 0.0

    if dist_pct < EXTENSION_PCT:
        return None

    if dist < 0 and price > midpoint:
        return "CE"
    if dist > 0 and price < midpoint:
        return "PE"
    return None


def simulate_one_pass(
    spot_1m: pd.DataFrame,
    vix_1m: pd.DataFrame,
    expiries_by_date: dict[date, list[Path]],
    *,
    regime_gated: bool,
) -> list[Trade]:
    """
    Run the simulator over every trading day that has matching option
    chain coverage. For each day, uses the next-upcoming weekly expiry's
    contracts. When `regime_gated` is True, entries require the classifier
    to currently be in RANGE. Otherwise entries are allowed in any regime
    (the baseline).
    """
    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
    trades: list[Trade] = []

    # Trading days available in spot data
    trading_days = sorted({d for d in spot_1m.index.date})
    expiries = sorted(expiries_by_date.keys())
    day_to_expiry = map_day_to_expiry(trading_days, expiries)

    # Cache loaded chains so we don't re-read for every day in the same expiry
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

        for ts, row in day_5m.iterrows():
            # 1) update regime
            feat = build_feature_for_bar(ts, day_5m, day_15m, prev_close, vix_1m)
            regime = classifier.classify(feat)

            # 2) monitor open trade — TP/SL/time/EOD
            if open_trade is not None:
                opt = get_option_price_at(chain, open_trade.strike,
                                          open_trade.direction, ts)
                if opt is not None:
                    oclose, ohigh, olow = opt
                    eff_entry = open_trade.entry_premium
                    tp = eff_entry * (1 + TP_PCT)
                    sl = eff_entry * (1 - SL_PCT)
                    mins_held = (ts - open_trade.entry_ts).total_seconds() / 60

                    if ohigh >= tp:
                        open_trade.close(ts, tp, "TP")
                        trades.append(open_trade)
                        open_trade = None
                    elif olow <= sl:
                        open_trade.close(ts, sl, "SL")
                        trades.append(open_trade)
                        open_trade = None
                    elif mins_held >= TIME_STOP_MIN:
                        open_trade.close(ts, oclose, "TIME_STOP")
                        trades.append(open_trade)
                        open_trade = None
                    elif ts.time() >= FORCE_FLAT:
                        open_trade.close(ts, oclose, "EOD")
                        trades.append(open_trade)
                        open_trade = None

            # 3) attempt new entry
            if open_trade is not None:
                continue
            if ts.time() < time(10, 0) or ts.time() >= ENTRY_CUTOFF:
                continue
            if regime_gated and regime != Regime.RANGE:
                continue
            if regime in (Regime.NO_TRADE, Regime.WAIT, Regime.EXPIRY):
                # even the baseline respects the hard risk-off regimes
                continue

            day_5m_upto = day_5m[day_5m.index <= ts]
            vwap_series = compute_session_vwap(day_5m_upto)
            direction = check_entry_signal(day_5m_upto, vwap_series)
            if direction is None:
                continue

            spot = float(row["close"])
            strike = nearest_strike(spot)
            opt = get_option_price_at(chain, strike, direction, ts)
            if opt is None:
                continue
            oclose, _, _ = opt
            if oclose < MIN_ENTRY_PREMIUM:
                continue

            open_trade = Trade(
                day=day_str,
                tactic="mean_reversion",
                direction=direction,
                strike=strike,
                entry_ts=ts,
                entry_premium=oclose,
                qty_lots=1,
                regime_at_entry=regime,
            )

        # End of day — force flat anything still open
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
                "avg_win": 0, "avg_loss": 0}
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
        "max_dd_estimate": df["net_pnl"].cumsum().cummax().sub(df["net_pnl"].cumsum()).max(),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }


def _regime_breakdown(trades: list[Trade]) -> dict[str, dict]:
    """Net P&L bucketed by regime-at-entry."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        buckets[t.regime_at_entry.value].append(t.net_pnl)
    return {
        r: {"trades": len(v),
            "net_pnl": sum(v),
            "wins": len([x for x in v if x > 0])}
        for r, v in buckets.items()
    }


def _monthly_breakdown(trades: list[Trade]) -> dict[str, dict]:
    """Net P&L bucketed by year-month of entry."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        ym = t.entry_ts.strftime("%Y-%m")
        buckets[ym].append(t.net_pnl)
    return {
        m: {"trades": len(v), "net_pnl": sum(v),
            "wins": len([x for x in v if x > 0])}
        for m, v in sorted(buckets.items())
    }


def write_report(baseline: dict, gated: dict,
                 baseline_trades: list[Trade],
                 gated_trades: list[Trade]) -> Path:
    out = ROOT / "reports" / "phase3_backtest_report.md"
    lines: list[str] = []

    days = sorted({t.day for t in baseline_trades})
    lines.append("# Phase 3 — Mean-Reversion Backtest: Baseline vs Regime-Gated\n")
    if days:
        lines.append(f"Period: {days[0]} to {days[-1]} "
                     f"({len(days)} trading days with at least one entry)\n")
    lines.append("Tactic: simplified VWAP-extension mean reversion on ATM options\n")
    lines.append("")
    lines.append(
        "**Important caveat** — this simplified tactic is NOT the full 3-gate "
        "OI-wall mean-reversion logic of the live bot. It captures the same "
        "behavior class (fade extremes back to VWAP) using fewer inputs so "
        "that it's cleanly regime-gatable. Use this as a directional signal, "
        "not a production-accuracy forecast.\n"
    )

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
    reasons = set(baseline.get("exit_reasons", {}).keys()) | set(gated.get("exit_reasons", {}).keys())
    for r in sorted(reasons):
        lines.append(f"| {r} | {baseline.get('exit_reasons', {}).get(r, 0)} "
                     f"| {gated.get('exit_reasons', {}).get(r, 0)} |")

    # Per-regime breakdown (baseline only — the gated run by definition only fires in RANGE)
    lines.append("\n## Baseline P&L Bucketed By Regime At Entry\n")
    lines.append("Tells us where the baseline bleeds vs where it wins. "
                 "If TREND_* regimes show heavy losses, that's why gating helps.\n")
    lines.append("| Regime | Trades | Wins | Net P&L |")
    lines.append("|---|---:|---:|---:|")
    rb = _regime_breakdown(baseline_trades)
    for r in sorted(rb.keys(), key=lambda x: -rb[x]["trades"]):
        v = rb[r]
        lines.append(f"| {r} | {v['trades']} | {v['wins']} | "
                     f"Rs {v['net_pnl']:,.0f} |")

    # Monthly breakdown
    lines.append("\n## Monthly P&L\n")
    lines.append("| Month | Baseline trades | Baseline P&L | Gated trades | Gated P&L |")
    lines.append("|---|---:|---:|---:|---:|")
    mb = _monthly_breakdown(baseline_trades)
    mg = _monthly_breakdown(gated_trades)
    months = sorted(set(mb.keys()) | set(mg.keys()))
    for m in months:
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
    if baseline.get("trades", 0) and gated.get("trades", 0):
        lines.append(
            f"- Regime-gated took **{gated['trades']}** trades vs baseline's "
            f"**{baseline['trades']}** — that's a "
            f"{100*(1-gated['trades']/baseline['trades']):.0f}% reduction.\n"
        )
    lines.append(f"- P&L delta (gated - baseline): **Rs {diff:,.0f}**\n")
    n_baseline = baseline.get("trades", 0)
    if n_baseline >= 30:
        lines.append("- Sample is **statistically usable** "
                     f"(n={n_baseline} trades baseline).\n")
    else:
        lines.append(f"- Sample is **small** (n={n_baseline} baseline trades) — "
                     "directional signal only.\n")

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
