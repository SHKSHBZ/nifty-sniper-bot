"""Backtest the VIX-direction signal on real CE/PE premium data.

Strategy (decided at 10:30 IST each trading day):
  vix_pct_change = vix_at_10:30 / vix_at_09:15 - 1
  vix_level      = vix_at_10:30

  if vix_level >= 15 and vix_pct_change >= +1.0%:
      buy ATM PE  (bearish setup)
  elif vix_level >= 15 and vix_pct_change <= -1.0%:
      buy ATM CE  (bullish setup)
  else:
      no trade

Exits: +50% TP, -30% SL, or 15:25 EOD force-close.
Capital Rs.20k, lot 65, premium gate Rs.20-200.

Output:
  reports/vix_direction_signal_trades.csv
  reports/vix_direction_signal_summary.md
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

import backtesting.expiry_gamma_hero as base
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)


# Decision parameters
DECISION_TIME = dtime(10, 30)
DAY_OPEN_TIME = dtime(9, 15)
EOD_TIME = dtime(15, 25)

VIX_REGIME_MIN = 15.0       # only trade Elevated/High
VIX_CHANGE_THRESHOLD = 1.0  # %

# Trade parameters
TP_PCT = 50.0
SL_PCT = 30.0
MIN_PREMIUM = 20.0
MAX_PREMIUM = 200.0
CAPITAL = 20_000.0
LOT_SIZE = 65
STRIKE_STEP = 50
SLIPPAGE_PER_LEG = 0.05
BROKERAGE_PER_TRADE = 60.0


@dataclass
class VixTrade:
    trade_date: str
    expiry: str
    dte: int
    vix_open: float
    vix_at_decision: float
    vix_pct_change: float
    vix_regime: str
    signal: str  # "BUY_PE" or "BUY_CE"
    spot_at_decision: float
    strike: int
    entry_premium: float
    lots: int
    qty: int
    exit_ts: str
    exit_premium: float
    exit_spot: float
    exit_reason: str
    minutes_held: int
    net_pnl: float
    return_pct: float


def vix_regime_label(v: float) -> str:
    if v < 12: return "Low"
    if v < 15: return "Normal"
    if v < 18: return "Elevated"
    return "High"


def run_for_day(trade_date: datetime, exp_token: str, exp_date: datetime,
                spot_df: pd.DataFrame, vix_df: pd.DataFrame) -> Optional[VixTrade]:
    tz = spot_df.index.tz
    day_open_ts = pd.Timestamp.combine(trade_date.date(),
                                       DAY_OPEN_TIME).tz_localize(tz)
    decision_ts = pd.Timestamp.combine(trade_date.date(),
                                       DECISION_TIME).tz_localize(tz)
    eod_ts = pd.Timestamp.combine(trade_date.date(), EOD_TIME).tz_localize(tz)

    vix_open = base.get_value_at(vix_df, day_open_ts, "close")
    vix_now = base.get_value_at(vix_df, decision_ts, "close")
    if vix_open is None or vix_now is None or vix_open <= 0:
        return None

    vix_pct = (vix_now / vix_open - 1) * 100
    regime = vix_regime_label(vix_now)

    # Apply signal logic
    if vix_now < VIX_REGIME_MIN:
        return None
    if vix_pct >= VIX_CHANGE_THRESHOLD:
        signal = "BUY_PE"
        side = "PE"
    elif vix_pct <= -VIX_CHANGE_THRESHOLD:
        signal = "BUY_CE"
        side = "CE"
    else:
        return None

    spot_now = base.get_value_at(spot_df, decision_ts, "close")
    if spot_now is None:
        return None
    atm = int(round(spot_now / STRIKE_STEP) * STRIKE_STEP)
    opt_df = base.load_option(atm, side, exp_token)
    if opt_df is None:
        return None
    entry_p = base.get_value_at(opt_df, decision_ts, "close")
    if entry_p is None or not (MIN_PREMIUM <= entry_p <= MAX_PREMIUM):
        return None

    lots = int(CAPITAL // (entry_p * LOT_SIZE))
    if lots < 1:
        return None
    qty = lots * LOT_SIZE
    tp_premium = entry_p * (1 + TP_PCT / 100)
    sl_premium = entry_p * (1 - SL_PCT / 100)

    # Walk minute-by-minute to find exit
    minutes = pd.date_range(decision_ts + pd.Timedelta(minutes=1),
                            eod_ts, freq="1min")
    exit_ts = None
    exit_p = None
    exit_reason = None
    for ts in minutes:
        cur = base.get_value_at(opt_df, ts, "close")
        if cur is None:
            continue
        if cur >= tp_premium:
            exit_ts, exit_p, exit_reason = ts, cur, "TP"
            break
        if cur <= sl_premium:
            exit_ts, exit_p, exit_reason = ts, cur, "SL"
            break

    if exit_ts is None:
        # EOD force close
        exit_ts = eod_ts
        exit_p = base.get_value_at(opt_df, eod_ts, "close")
        if exit_p is None:
            sub = opt_df[opt_df.index <= eod_ts]
            exit_p = float(sub["close"].iloc[-1]) if len(sub) else 0.0
        exit_reason = "EOD"

    eff_in = entry_p + SLIPPAGE_PER_LEG
    eff_out = max(0.0, exit_p - SLIPPAGE_PER_LEG)
    net = (eff_out - eff_in) * qty - BROKERAGE_PER_TRADE
    spot_exit = base.get_value_at(spot_df, exit_ts, "close") or 0.0

    return VixTrade(
        trade_date=trade_date.date().isoformat(),
        expiry=exp_token,
        dte=(exp_date.date() - trade_date.date()).days,
        vix_open=round(vix_open, 2),
        vix_at_decision=round(vix_now, 2),
        vix_pct_change=round(vix_pct, 2),
        vix_regime=regime,
        signal=signal,
        spot_at_decision=round(spot_now, 2),
        strike=atm,
        entry_premium=round(entry_p, 2),
        lots=lots, qty=qty,
        exit_ts=exit_ts.isoformat(),
        exit_premium=round(exit_p, 2),
        exit_spot=round(spot_exit, 2),
        exit_reason=exit_reason,
        minutes_held=int((exit_ts - decision_ts).total_seconds() // 60),
        net_pnl=round(net, 2),
        return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
    )


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts").sort_index()

    vix_df = pd.read_csv(base.DATA_DIR / "INDIA_VIX_1minute.csv")
    vix_df["ts"] = pd.to_datetime(vix_df["timestamp"])
    vix_df = vix_df.set_index("ts").sort_index()

    expiries = base.discover_expiries()
    expiries_sorted = build_expiry_lookup(expiries)
    print(f"Loaded {len(expiries)} expiries.")

    trading_days = discover_trading_days(spot_df)
    latest_expiry = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest_expiry]
    print(f"Trading days to scan: {len(trading_days)}\n")

    trades: list[VixTrade] = []
    for i, td in enumerate(trading_days):
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        try:
            t = run_for_day(td, tok, exp_date, spot_df, vix_df)
            if t is not None:
                trades.append(t)
        except Exception as e:
            print(f"  {td.date()} ({tok}): ERR {e}")
        if (i + 1) % 100 == 0:
            print(f"  progress: {i+1}/{len(trading_days)}, signals fired: {len(trades)}")

    if not trades:
        print("\nNo signals fired across the dataset.")
        return

    df = pd.DataFrame([asdict(t) for t in trades])
    out_csv = base.REPORTS_DIR / "vix_direction_signal_trades.csv"
    df.to_csv(out_csv, index=False)

    n = len(df)
    wins = (df["net_pnl"] > 0).sum()
    losses = (df["net_pnl"] < 0).sum()
    total = df["net_pnl"].sum()
    avg = df["net_pnl"].mean()
    cum = df["net_pnl"].cumsum()
    dd = (cum.cummax() - cum).max() if len(cum) else 0

    by_signal = df.groupby("signal").agg(
        n=("net_pnl", "count"),
        wins=("net_pnl", lambda x: (x > 0).sum()),
        win_rate_pct=("net_pnl", lambda x: round((x > 0).mean() * 100, 1)),
        total_pnl=("net_pnl", "sum"),
        avg_pnl=("net_pnl", "mean"),
    ).round(0)
    by_regime = df.groupby("vix_regime").agg(
        n=("net_pnl", "count"),
        win_rate_pct=("net_pnl", lambda x: round((x > 0).mean() * 100, 1)),
        total_pnl=("net_pnl", "sum"),
        avg_pnl=("net_pnl", "mean"),
    ).round(0)
    by_reason = df.groupby("exit_reason")["net_pnl"].agg(["count", "sum", "mean"]).round(0)
    by_dte = df.groupby("dte")["net_pnl"].agg(["count", "sum", "mean"]).round(0)

    print(f"\n{'='*70}")
    print(f"SIGNALS FIRED:    {n}")
    print(f"WIN RATE:         {wins}/{n} = {wins/n*100:.1f}%  ({losses} losses)")
    print(f"TOTAL NET P&L:    Rs.{int(total):,}")
    print(f"AVG / TRADE:      Rs.{int(avg):,}")
    print(f"MAX DRAWDOWN:     Rs.{int(dd):,}")
    print(f"\nBy signal:\n{by_signal.to_string()}")
    print(f"\nBy VIX regime:\n{by_regime.to_string()}")
    print(f"\nBy exit reason:\n{by_reason.to_string()}")
    print(f"\nBy DTE:\n{by_dte.to_string()}")
    print(f"{'='*70}")

    md = ["# VIX-Direction Signal Backtest\n",
          "Decision at 10:30 IST. Entry: ATM CE (if VIX falling) or "
          "ATM PE (if VIX rising), only when VIX >= 15.",
          f"Capital Rs.{int(CAPITAL):,}. Lot {LOT_SIZE}.",
          f"TP +{TP_PCT}% / SL -{SL_PCT}% / EOD 15:25.\n",
          "## Headline\n",
          f"- Signals fired:    **{n}**",
          f"- Win rate:         **{wins}/{n} = {wins/n*100:.1f}%**",
          f"- Total net P&L:    **Rs.{int(total):,}**",
          f"- Avg / trade:      Rs.{int(avg):,}",
          f"- Max drawdown:     Rs.{int(dd):,}",
          "",
          "## By signal\n```", by_signal.to_string(), "```", "",
          "## By VIX regime\n```", by_regime.to_string(), "```", "",
          "## By exit reason\n```", by_reason.to_string(), "```", "",
          "## By DTE\n```", by_dte.to_string(), "```", ""]
    md_path = base.REPORTS_DIR / "vix_direction_signal_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
