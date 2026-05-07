"""Tactic 1 backtest, no time stop. Pure price action exits.

Same as tactic_extreme_reversal_alldays.py but with TIME_STOP removed.
Position holds until TP, SL, or end-of-day forced close.

The user's point: a 20-min time-stop was forcing 661 exits at avg
-Rs.193 each. Many of those trades might have eventually hit TP if
allowed to run. Pure price-action decides.

Output: reports/tactic_extreme_reversal_no_time_stop_trades.csv
        reports/tactic_extreme_reversal_no_time_stop_summary.md
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
    ENTRY_WINDOW_START, ENTRY_WINDOW_END, ROLLING_WINDOW_MIN,
    MIN_RANGE_PTS, EDGE_PROXIMITY_PTS, MIN_PREMIUM, MAX_PREMIUM,
    TP_PCT, SL_PCT, COOLDOWN_MIN, CAPITAL, LOT_SIZE, STRIKE_STEP,
    SLIPPAGE_PER_LEG, BROKERAGE_PER_TRADE, ScalpTrade,
)


def run_for_day_no_time_stop(trade_date: datetime, expiry_token: str,
                             expiry_date: datetime,
                             spot_df: pd.DataFrame) -> list[ScalpTrade]:
    tz = spot_df.index.tz
    day_start = pd.Timestamp.combine(trade_date.date(),
                                     ENTRY_WINDOW_START).tz_localize(tz)
    day_end = pd.Timestamp.combine(trade_date.date(),
                                   dtime(15, 25)).tz_localize(tz)
    minutes = pd.date_range(day_start, day_end, freq="1min")
    days_to_exp = (expiry_date.date() - trade_date.date()).days

    trades: list[ScalpTrade] = []
    position: Optional[dict] = None
    last_exit_by_side: dict[str, Optional[pd.Timestamp]] = {"CE": None, "PE": None}
    option_cache: dict[tuple[int, str], Optional[pd.DataFrame]] = {}

    def opt(strike: int, side: str) -> Optional[pd.DataFrame]:
        key = (strike, side)
        if key not in option_cache:
            option_cache[key] = base.load_option(strike, side, expiry_token)
        return option_cache[key]

    def close_position(pos, ts, cur_p, spot, reason):
        eff_in = pos["entry_premium"] + SLIPPAGE_PER_LEG
        eff_out = max(0.0, cur_p - SLIPPAGE_PER_LEG)
        gross = (eff_out - eff_in) * pos["qty"]
        net = gross - BROKERAGE_PER_TRADE
        return ScalpTrade(
            trade_date=trade_date.date().isoformat(),
            expiry=expiry_token, days_to_expiry=days_to_exp,
            entry_ts=pos["entry_ts"].isoformat(),
            side=pos["side"], strike=pos["strike"],
            entry_spot=round(pos["entry_spot"], 2),
            range_5m_at_entry=round(pos["range_5m"], 2),
            entry_premium=round(pos["entry_premium"], 2),
            lots=pos["lots"], qty=pos["qty"],
            exit_ts=ts.isoformat(), exit_premium=round(cur_p, 2),
            exit_spot=round(spot or 0, 2), exit_reason=reason,
            minutes_held=int((ts - pos["entry_ts"]).total_seconds() // 60),
            net_pnl=round(net, 2),
            return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
        )

    for ts in minutes:
        spot = base.get_value_at(spot_df, ts, "close")
        if spot is None:
            continue

        if position is not None:
            opt_df = opt(position["strike"], position["side"])
            cur_p = base.get_value_at(opt_df, ts, "close")
            if cur_p is None:
                continue
            if cur_p >= position["tp_premium"]:
                trades.append(close_position(position, ts, cur_p, spot, "TP"))
                last_exit_by_side[position["side"]] = ts
                position = None
            elif cur_p <= position["sl_premium"]:
                trades.append(close_position(position, ts, cur_p, spot, "SL"))
                last_exit_by_side[position["side"]] = ts
                position = None
            else:
                continue  # holding, no time-stop

        if ts.time() < ENTRY_WINDOW_START or ts.time() > ENTRY_WINDOW_END:
            continue

        win_start = ts - pd.Timedelta(minutes=ROLLING_WINDOW_MIN)
        win = spot_df[(spot_df.index >= win_start) & (spot_df.index <= ts)]
        if len(win) < ROLLING_WINDOW_MIN:
            continue
        range_high = float(win["close"].max())
        range_low = float(win["close"].min())
        range_size = range_high - range_low
        if range_size < MIN_RANGE_PTS:
            continue

        side: Optional[str] = None
        if (spot - range_low) <= EDGE_PROXIMITY_PTS:
            side = "CE"
        elif (range_high - spot) <= EDGE_PROXIMITY_PTS:
            side = "PE"
        if side is None:
            continue

        last = last_exit_by_side.get(side)
        if last is not None and (ts - last).total_seconds() < COOLDOWN_MIN * 60:
            continue

        atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
        opt_df = opt(atm, side)
        if opt_df is None:
            continue
        prem = base.get_value_at(opt_df, ts, "close")
        if prem is None or not (MIN_PREMIUM <= prem <= MAX_PREMIUM):
            continue

        lots = int(CAPITAL // (prem * LOT_SIZE))
        if lots < 1:
            continue
        qty = lots * LOT_SIZE
        position = {
            "side": side, "strike": atm, "entry_ts": ts,
            "entry_spot": spot, "entry_premium": prem,
            "tp_premium": prem * (1 + TP_PCT / 100),
            "sl_premium": prem * (1 - SL_PCT / 100),
            "lots": lots, "qty": qty, "range_5m": range_size,
        }

    # End-of-day forced close
    if position is not None:
        opt_df = opt(position["strike"], position["side"])
        cur_p = base.get_value_at(opt_df, day_end, "close")
        if cur_p is None:
            sub = opt_df[opt_df.index <= day_end] if opt_df is not None else None
            cur_p = float(sub["close"].iloc[-1]) if sub is not None and len(sub) else 0.0
        trades.append(close_position(position, day_end, cur_p, spot, "EOD"))

    return trades


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts").sort_index()

    expiries = base.discover_expiries()
    expiries_sorted = build_expiry_lookup(expiries)
    print(f"Loaded {len(expiries)} expiries.")
    print(f"Spot data spans {spot_df.index.min().date()} -> "
          f"{spot_df.index.max().date()}")

    trading_days = discover_trading_days(spot_df)
    latest_expiry = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest_expiry]
    print(f"Trading days to test: {len(trading_days)}\n")

    all_trades: list[ScalpTrade] = []
    by_day: list[dict] = []
    for i, td in enumerate(trading_days):
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        try:
            ts = run_for_day_no_time_stop(td, tok, exp_date, spot_df)
        except Exception as e:
            print(f"  {td.date()} ({tok}): ERR {e}")
            continue
        all_trades.extend(ts)
        if ts:
            df_d = pd.DataFrame([asdict(t) for t in ts])
            day_pnl = int(df_d["net_pnl"].sum())
        else:
            day_pnl = 0
        by_day.append({"date": td.date().isoformat(), "expiry": tok,
                       "trades": len(ts), "pnl": day_pnl})
        if (i + 1) % 100 == 0 or i == len(trading_days) - 1:
            print(f"  progress: {i+1}/{len(trading_days)} | "
                  f"trades: {len(all_trades)}")

    if not all_trades:
        print("No trades.")
        return

    df = pd.DataFrame([asdict(t) for t in all_trades])
    out_csv = base.REPORTS_DIR / "tactic_extreme_reversal_no_time_stop_trades.csv"
    df.to_csv(out_csv, index=False)

    total = df["net_pnl"].sum()
    n = len(df)
    wins = (df["net_pnl"] > 0).sum()
    losses = (df["net_pnl"] < 0).sum()
    avg = df["net_pnl"].mean()
    by_day_df = pd.DataFrame(by_day)
    n_days = len(by_day_df[by_day_df["trades"] > 0])
    cum = by_day_df["pnl"].cumsum()
    dd = (cum.cummax() - cum).max() if len(cum) else 0
    by_reason = df.groupby("exit_reason")["net_pnl"].agg(
        ["count", "sum", "mean"]
    )
    by_dte = df.groupby("days_to_expiry")["net_pnl"].agg(
        ["count", "sum", "mean"]
    )

    print(f"\n{'='*70}")
    print(f"TOTAL TRADES:   {n}")
    print(f"DAYS WITH TRADES: {n_days}")
    print(f"WIN RATE:       {wins}/{n} = {wins/n*100:.1f}%  ({losses} losses)")
    print(f"TOTAL NET P&L:  Rs.{int(total):,}")
    print(f"AVG / TRADE:    Rs.{int(avg):,}")
    print(f"AVG / DAY:      Rs.{int(total / max(n_days, 1)):,}")
    print(f"MAX DAILY DD:   Rs.{int(dd):,}")
    print(f"\nBy exit reason:\n{by_reason.to_string()}")
    print(f"\nBy days-to-expiry:\n{by_dte.to_string()}")
    print(f"{'='*70}")

    md = ["# Tactic 1 - All Trading Days, NO TIME STOP\n",
          f"Capital Rs.{int(CAPITAL):,}/trade. Lot {LOT_SIZE}.",
          f"Premium gate Rs.{int(MIN_PREMIUM)}-Rs.{int(MAX_PREMIUM)}.",
          f"TP +{TP_PCT}% / SL -{SL_PCT}% / EOD force-close at 15:25.",
          f"Range gate >{MIN_RANGE_PTS} pts in {ROLLING_WINDOW_MIN}-min window.",
          "",
          "## Headline\n",
          f"- Trading days tested:   **{len(by_day)}**",
          f"- Days with trades:      {n_days}",
          f"- Total trades:          **{n}**",
          f"- Win rate:              **{wins}/{n} = {wins/n*100:.1f}%**",
          f"- Total net P&L:         **Rs.{int(total):,}**",
          f"- Avg / trade:           Rs.{int(avg):,}",
          f"- Avg / day-with-trades: Rs.{int(total/max(n_days,1)):,}",
          f"- Max daily drawdown:    Rs.{int(dd):,}",
          "",
          "## By exit reason\n```",
          by_reason.to_string(), "```", "",
          "## By days-to-expiry\n```",
          by_dte.to_string(), "```", ""]
    md_path = base.REPORTS_DIR / "tactic_extreme_reversal_no_time_stop_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
