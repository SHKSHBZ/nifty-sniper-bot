"""Phase 2: real options execution simulator on top of confluence signals.

Takes the same signals from Phase 1 (confluence_backtest.py) but instead
of computing P&L from spot points, it:

  1. Picks the nearest weekly expiry where 2 <= DTE <= 6 (per spec).
  2. Rounds spot to nearest 50 to get ATM strike.
  3. Buys ATM CE for long signals, ATM PE for short signals.
  4. Reads actual premium from the option's 1-min CSV at entry.
  5. Walks forward in the option file (NOT spot), applying premium-based
     TP / SL / EOD-force-close.
  6. Computes Rs P&L = (exit_premium - entry_premium) x LOT_SIZE x lots.

This catches the things the spot-based Phase 1 misses:
    - Theta decay (premium erodes hour-by-hour, especially DTE <= 2)
    - Premium delta (option moves ~0.5x spot move at ATM, less ITM/OTM)
    - Bid/ask spread at execution (we use close-to-close which is generous)
    - Real lot economics (Rs 20k buys ~3 lots at avg premium Rs 100)

CLI demo:
    python -m backtesting.confluence_options_backtest --start 2025-06-01 --end 2025-08-28
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv
from backtesting.confluence import SRProvider, score_signals, ConfluenceSignal


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"

LOT_SIZE = 65
STRIKE_STEP = 50
CAPITAL_PER_TRADE = 20_000     # Rs per spec
COST_PER_TRADE = 200           # Rs round-trip (slippage + brokerage + STT + GST)
MIN_DTE = 2                    # Per spec dte_preferred = [2,3,4,5,6]
MAX_DTE = 6
TP_PREMIUM_PCT = 30
SL_PREMIUM_PCT = 30
FORCE_CLOSE_AT = dtime(15, 25)
NO_ENTRIES_BEFORE = dtime(9, 30)
NO_ENTRIES_AFTER = dtime(14, 30)
COOLDOWN_MIN = 30

MONTH_CODE = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


@dataclass
class OptionTrade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str               # 'long' or 'short' (signal direction)
    leg: str                     # 'CE' or 'PE'
    strike: int
    expiry: date
    dte: int
    entry_premium: float
    exit_premium: float
    exit_reason: str             # 'tp', 'sl', 'eod', 'no_data'
    lots: int
    pnl_rs: float                # gross, before per-trade cost
    score: float


# -------------------------- File index --------------------------

_FILE_RE = re.compile(r"NIFTY_(\d+)_(CE|PE)_(\d{2})_([A-Z]{3})_(\d{2})_1min\.csv")


def build_option_index() -> dict[tuple[date, int, str], Path]:
    """Map (expiry_date, strike, leg) -> CSV path."""
    out: dict = {}
    for f in DATA.glob("NIFTY_*_*_1min.csv"):
        m = _FILE_RE.match(f.name)
        if not m:
            continue
        strike = int(m.group(1))
        leg = m.group(2)
        d, mon, y = int(m.group(3)), MONTH_CODE[m.group(4)], 2000 + int(m.group(5))
        out[(date(y, mon, d), strike, leg)] = f
    return out


def expiries_index(idx: dict) -> dict[date, list[int]]:
    """Map expiry_date -> sorted list of strikes available for that expiry."""
    out = defaultdict(set)
    for (exp, strike, _), _ in idx.items():
        out[exp].add(strike)
    return {k: sorted(v) for k, v in out.items()}


# -------------------------- Lookups --------------------------

class OptionDataCache:
    """Lazy-loads option CSVs, caches DataFrames keyed by (expiry, strike, leg)."""

    def __init__(self, idx: dict[tuple[date, int, str], Path]):
        self.idx = idx
        self._cache: dict = {}

    def get(self, expiry: date, strike: int, leg: str) -> Optional[pd.DataFrame]:
        key = (expiry, strike, leg)
        if key in self._cache:
            return self._cache[key]
        path = self.idx.get(key)
        if path is None:
            self._cache[key] = None
            return None
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        self._cache[key] = df
        return df


def find_expiry_for(signal_ts: pd.Timestamp,
                    available_expiries: list[date]) -> Optional[date]:
    """Pick the nearest weekly expiry where MIN_DTE <= DTE <= MAX_DTE."""
    sig_d = signal_ts.date()
    candidates = [
        e for e in available_expiries
        if MIN_DTE <= (e - sig_d).days <= MAX_DTE
    ]
    return min(candidates) if candidates else None


def find_atm_strike(spot: float, available_strikes: list[int]) -> Optional[int]:
    """Round spot to nearest STRIKE_STEP, then snap to nearest available."""
    target = round(spot / STRIKE_STEP) * STRIKE_STEP
    return min(available_strikes, key=lambda s: abs(s - target)) if available_strikes else None


def get_premium_at(df: pd.DataFrame, ts: pd.Timestamp) -> Optional[float]:
    """Last known close price at-or-before ts. Returns None if no data."""
    sub = df.loc[:ts]
    return float(sub.iloc[-1]["close"]) if len(sub) else None


def walk_forward_exit(
    df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_premium: float,
    *,
    tp_pct: float,
    sl_pct: float,
) -> tuple[pd.Timestamp, float, str]:
    """In the option premium series, walk forward until TP / SL / 15:25
    force close. Returns (exit_ts, exit_premium, reason).
    """
    tp_price = entry_premium * (1 + tp_pct / 100)
    sl_price = entry_premium * (1 - sl_pct / 100)
    forward = df.loc[entry_ts:].iloc[1:]
    if forward.empty:
        return entry_ts, entry_premium, "no_data"

    for ts, row in forward.iterrows():
        if ts.time() >= FORCE_CLOSE_AT:
            return ts, float(row["close"]), "eod"
        if row["low"] <= sl_price:
            return ts, sl_price, "sl"
        if row["high"] >= tp_price:
            return ts, tp_price, "tp"
    last = forward.iloc[-1]
    return forward.index[-1], float(last["close"]), "eod"


# -------------------------- Backtest loop --------------------------

def run_options_backtest(
    signals: list[ConfluenceSignal],
    *,
    score_threshold: float = 4.0,
    tp_pct: float = TP_PREMIUM_PCT,
    sl_pct: float = SL_PREMIUM_PCT,
    capital_per_trade: float = CAPITAL_PER_TRADE,
    cooldown_min: int = COOLDOWN_MIN,
) -> list[OptionTrade]:
    idx = build_option_index()
    expiries_strikes = expiries_index(idx)
    available_expiries = sorted(expiries_strikes.keys())
    cache = OptionDataCache(idx)

    trades: list[OptionTrade] = []
    last_entry_ts: Optional[pd.Timestamp] = None
    n_skipped_no_expiry = n_skipped_no_strike = n_skipped_no_premium = 0

    for sig in signals:
        ts = sig.timestamp
        t = ts.time()
        if not (NO_ENTRIES_BEFORE <= t < NO_ENTRIES_AFTER):
            continue
        direction = sig.direction_at(score_threshold)
        if direction is None:
            continue
        if last_entry_ts is not None:
            if (ts - last_entry_ts).total_seconds() / 60 < cooldown_min:
                continue

        expiry = find_expiry_for(ts, available_expiries)
        if expiry is None:
            n_skipped_no_expiry += 1
            continue

        strikes = expiries_strikes[expiry]
        atm = find_atm_strike(sig.spot_price, strikes)
        if atm is None:
            n_skipped_no_strike += 1
            continue

        leg = "CE" if direction == "long" else "PE"
        df = cache.get(expiry, atm, leg)
        if df is None or df.empty:
            n_skipped_no_premium += 1
            continue

        entry_premium = get_premium_at(df, ts)
        if entry_premium is None or entry_premium <= 0:
            n_skipped_no_premium += 1
            continue

        # Sizing: floor(capital / (premium * lot_size))
        cost_per_lot = entry_premium * LOT_SIZE
        if cost_per_lot <= 0:
            n_skipped_no_premium += 1
            continue
        lots = max(1, int(capital_per_trade // cost_per_lot))

        exit_ts, exit_premium, reason = walk_forward_exit(
            df, ts, entry_premium, tp_pct=tp_pct, sl_pct=sl_pct,
        )
        pnl_rs = (exit_premium - entry_premium) * LOT_SIZE * lots

        trades.append(OptionTrade(
            entry_time=ts, exit_time=exit_ts,
            direction=direction, leg=leg, strike=atm,
            expiry=expiry, dte=(expiry - ts.date()).days,
            entry_premium=entry_premium, exit_premium=exit_premium,
            exit_reason=reason, lots=lots,
            pnl_rs=pnl_rs, score=max(sig.score_long, sig.score_short),
        ))
        last_entry_ts = ts

    return trades, dict(
        skipped_no_expiry=n_skipped_no_expiry,
        skipped_no_strike=n_skipped_no_strike,
        skipped_no_premium=n_skipped_no_premium,
    )


# -------------------------- Reporting --------------------------

def summarize(trades: list[OptionTrade], n_months: float, capital: float) -> dict:
    if not trades:
        return {"n_trades": 0}
    df = pd.DataFrame([t.__dict__ for t in trades])
    n = len(df)
    wins = df[df["pnl_rs"] > 0]
    gross_rs = df["pnl_rs"].sum()
    cost_total = n * COST_PER_TRADE
    net_rs = gross_rs - cost_total
    annual_rs = net_rs * 12 / n_months if n_months > 0 else 0
    cumulative = df["pnl_rs"].cumsum()
    dd_rs = (cumulative - cumulative.cummax()).min()

    return {
        "n_trades": n,
        "n_long_CE": int((df["leg"] == "CE").sum()),
        "n_short_PE": int((df["leg"] == "PE").sum()),
        "win_rate_pct": len(wins) / n * 100,
        "avg_win_rs": float(wins["pnl_rs"].mean()) if len(wins) else 0,
        "avg_loss_rs": float(df[df["pnl_rs"] <= 0]["pnl_rs"].mean()) if (n - len(wins)) else 0,
        "avg_dte": float(df["dte"].mean()),
        "avg_premium_entry": float(df["entry_premium"].mean()),
        "avg_lots": float(df["lots"].mean()),
        "gross_rs": float(gross_rs),
        "costs_rs": -cost_total,
        "net_rs": float(net_rs),
        "annual_rs": float(annual_rs),
        "return_pct_on_capital": annual_rs / capital * 100,
        "max_dd_rs": float(dd_rs),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--threshold", type=float, default=4.0)
    p.add_argument("--tp", type=float, default=TP_PREMIUM_PCT)
    p.add_argument("--sl", type=float, default=SL_PREMIUM_PCT)
    p.add_argument("--capital-per-trade", type=float, default=CAPITAL_PER_TRADE)
    p.add_argument("--total-capital", type=float, default=100_000)
    p.add_argument("--out-csv", default=None)
    args = p.parse_args()

    print("Loading + computing confluence signals...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]
    df_5m = resample_ohlcv(sub, "5min")
    df_1h = resample_ohlcv(sub, "1h")
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)
    sr = SRProvider()
    signals = score_signals(df_5m, df_1h, df_4h, sr)
    print(f"  {len(signals):,} decision points\n")

    print(f"Running OPTIONS backtest: TP={args.tp}% SL={args.sl}% "
          f"thr={args.threshold} sizing=Rs.{args.capital_per_trade:,.0f}/trade\n")
    trades, skipped = run_options_backtest(
        signals, score_threshold=args.threshold,
        tp_pct=args.tp, sl_pct=args.sl,
        capital_per_trade=args.capital_per_trade,
    )
    n_months = (df_5m.index.max() - df_5m.index.min()).days / 30.4

    s = summarize(trades, n_months, args.total_capital)
    print("=" * 70)
    print(f"PHASE-2 RESULTS  ({args.start} -> {args.end}, {n_months:.1f} months)")
    print("=" * 70)
    if s["n_trades"] == 0:
        print("No trades generated.")
        return

    print(f"  Trades:            {s['n_trades']}  "
          f"(CE={s['n_long_CE']}, PE={s['n_short_PE']})")
    print(f"  Win rate:          {s['win_rate_pct']:.1f}%")
    print(f"  Avg win:           Rs.{s['avg_win_rs']:>+10,.0f}")
    print(f"  Avg loss:          Rs.{s['avg_loss_rs']:>+10,.0f}")
    print(f"  Avg DTE:           {s['avg_dte']:.1f} days")
    print(f"  Avg entry premium: Rs.{s['avg_premium_entry']:>10,.1f}")
    print(f"  Avg lots:          {s['avg_lots']:.1f}")
    print(f"  Exit reasons:      {s['exit_reasons']}")
    print()
    print(f"  Gross P&L:         Rs.{s['gross_rs']:>+12,.0f}")
    print(f"  Costs (Rs.{COST_PER_TRADE}/tr):    Rs.{s['costs_rs']:>+12,.0f}")
    print(f"  Net P&L:           Rs.{s['net_rs']:>+12,.0f}  ({n_months:.1f} months)")
    print(f"  Annualised:        Rs.{s['annual_rs']:>+12,.0f}")
    print(f"  Return on Rs.{args.total_capital:,.0f}: {s['return_pct_on_capital']:+.1f}% / year")
    print(f"  Max DD:            Rs.{s['max_dd_rs']:>+12,.0f}")
    if any(skipped.values()):
        print(f"\n  Signals skipped: {skipped}")

    out = Path(args.out_csv) if args.out_csv else REPORTS / "confluence_options_trades.csv"
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(out, index=False)
    try:
        rel = out.relative_to(ROOT)
    except ValueError:
        rel = out
    print(f"\nLedger: {rel}")


if __name__ == "__main__":
    main()
