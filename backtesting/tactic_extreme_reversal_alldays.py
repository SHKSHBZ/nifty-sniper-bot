"""Tactic 1 backtest on ALL trading days, not just expiry days.

For each trading day in data/NIFTY50_INDEX_1minute.csv:
  - Find the nearest upcoming weekly expiry that has option data
  - Run Tactic 1 (Spot-Extreme Reversal) for that day using that
    expiry's ATM contract
  - Aggregate across hundreds of trading days

Strategy parameters identical to tactic_extreme_reversal_test.py.
Premium gate widened to Rs.10-200 (non-expiry days have richer
premiums since they have more time value).

Output: reports/tactic_extreme_reversal_alldays_trades.csv
        reports/tactic_extreme_reversal_alldays_summary.md
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

import backtesting.expiry_gamma_hero as base


# Strategy params (matching tactic_extreme_reversal_test, with widened premium)
ENTRY_WINDOW_START = dtime(9, 30)
ENTRY_WINDOW_END = dtime(14, 30)
ROLLING_WINDOW_MIN = 5
MIN_RANGE_PTS = 30.0
EDGE_PROXIMITY_PTS = 5.0
MIN_PREMIUM = 10.0
MAX_PREMIUM = 200.0   # widened from 100 to handle non-expiry premiums
TP_PCT = 30.0
SL_PCT = 25.0
TIME_STOP_MIN = 20
COOLDOWN_MIN = 5

CAPITAL = 20_000.0
LOT_SIZE = 65
STRIKE_STEP = 50
SLIPPAGE_PER_LEG = 0.05
BROKERAGE_PER_TRADE = 60.0


@dataclass
class ScalpTrade:
    trade_date: str
    expiry: str
    days_to_expiry: int
    entry_ts: str
    side: str
    strike: int
    entry_spot: float
    range_5m_at_entry: float
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


def discover_trading_days(spot_df: pd.DataFrame) -> list[datetime]:
    """Trading days are spot dates where there's data 09:15-15:30."""
    spot_df = spot_df.copy()
    spot_df["date"] = spot_df.index.date
    days = sorted(spot_df["date"].unique())
    return [pd.Timestamp(d).to_pydatetime() for d in days]


def build_expiry_lookup(expiries: list[tuple[str, datetime]]) -> dict[datetime, tuple[str, datetime]]:
    """For each expiry token + date, build a sorted list. Caller will
    pick nearest-upcoming."""
    return sorted(expiries, key=lambda e: e[1])


def find_active_expiry(trade_date: datetime,
                       expiries_sorted: list[tuple[str, datetime]]
                       ) -> Optional[tuple[str, datetime]]:
    """Return the next-upcoming expiry on or after trade_date."""
    for tok, dt in expiries_sorted:
        if dt.date() >= trade_date.date():
            return (tok, dt)
    return None


def run_for_day(trade_date: datetime, expiry_token: str, expiry_date: datetime,
                spot_df: pd.DataFrame) -> list[ScalpTrade]:
    """Run Tactic 1 intraday for one trading day."""
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

    for ts in minutes:
        spot = base.get_value_at(spot_df, ts, "close")
        if spot is None:
            continue

        # Manage open position
        if position is not None:
            opt_df = opt(position["strike"], position["side"])
            cur_p = base.get_value_at(opt_df, ts, "close")
            if cur_p is None:
                continue
            exit_now = False
            reason = ""
            if cur_p >= position["tp_premium"]:
                exit_now, reason = True, "TP"
            elif cur_p <= position["sl_premium"]:
                exit_now, reason = True, "SL"
            elif ts >= position["deadline"]:
                exit_now, reason = True, "TIME_STOP"
            if exit_now:
                eff_in = position["entry_premium"] + SLIPPAGE_PER_LEG
                eff_out = max(0.0, cur_p - SLIPPAGE_PER_LEG)
                gross = (eff_out - eff_in) * position["qty"]
                net = gross - BROKERAGE_PER_TRADE
                trades.append(ScalpTrade(
                    trade_date=trade_date.date().isoformat(),
                    expiry=expiry_token, days_to_expiry=days_to_exp,
                    entry_ts=position["entry_ts"].isoformat(),
                    side=position["side"], strike=position["strike"],
                    entry_spot=round(position["entry_spot"], 2),
                    range_5m_at_entry=round(position["range_5m"], 2),
                    entry_premium=round(position["entry_premium"], 2),
                    lots=position["lots"], qty=position["qty"],
                    exit_ts=ts.isoformat(), exit_premium=round(cur_p, 2),
                    exit_spot=round(spot, 2), exit_reason=reason,
                    minutes_held=int((ts - position["entry_ts"]).total_seconds() // 60),
                    net_pnl=round(net, 2),
                    return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
                ))
                last_exit_by_side[position["side"]] = ts
                position = None
            else:
                continue

        # Entry window
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
            "deadline": ts + pd.Timedelta(minutes=TIME_STOP_MIN),
            "lots": lots, "qty": qty, "range_5m": range_size,
        }

    # Force-close at day end
    if position is not None:
        opt_df = opt(position["strike"], position["side"])
        cur_p = base.get_value_at(opt_df, day_end, "close")
        if cur_p is None:
            sub = opt_df[opt_df.index <= day_end] if opt_df is not None else None
            cur_p = float(sub["close"].iloc[-1]) if sub is not None and len(sub) else 0.0
        eff_in = position["entry_premium"] + SLIPPAGE_PER_LEG
        eff_out = max(0.0, cur_p - SLIPPAGE_PER_LEG)
        net = (eff_out - eff_in) * position["qty"] - BROKERAGE_PER_TRADE
        trades.append(ScalpTrade(
            trade_date=trade_date.date().isoformat(),
            expiry=expiry_token, days_to_expiry=days_to_exp,
            entry_ts=position["entry_ts"].isoformat(),
            side=position["side"], strike=position["strike"],
            entry_spot=round(position["entry_spot"], 2),
            range_5m_at_entry=round(position["range_5m"], 2),
            entry_premium=round(position["entry_premium"], 2),
            lots=position["lots"], qty=position["qty"],
            exit_ts=day_end.isoformat(), exit_premium=round(cur_p, 2),
            exit_spot=round(spot or 0, 2), exit_reason="EOD",
            minutes_held=int((day_end - position["entry_ts"]).total_seconds() // 60),
            net_pnl=round(net, 2),
            return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
        ))
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
    # Filter to only days where we have an expiry on/after them
    earliest_expiry = expiries_sorted[0][1].date()
    latest_expiry = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days
                    if d.date() <= latest_expiry]
    print(f"Trading days to test: {len(trading_days)}\n")

    all_trades: list[ScalpTrade] = []
    by_day: list[dict] = []
    for i, td in enumerate(trading_days):
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        try:
            ts = run_for_day(td, tok, exp_date, spot_df)
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
        if (i + 1) % 50 == 0 or i == len(trading_days) - 1:
            print(f"  progress: {i+1}/{len(trading_days)} days | "
                  f"trades so far: {len(all_trades)}")

    if not all_trades:
        print("\nNo trades generated.")
        return

    df = pd.DataFrame([asdict(t) for t in all_trades])
    out_csv = base.REPORTS_DIR / "tactic_extreme_reversal_alldays_trades.csv"
    df.to_csv(out_csv, index=False)

    # Aggregates
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
    print(f"TRADING DAYS:   {n_days} (with at least 1 trade) of "
          f"{len(by_day)} candidate days")
    print(f"WIN RATE:       {wins}/{n} = {wins/n*100:.1f}%  ({losses} losses)")
    print(f"TOTAL NET P&L:  Rs.{int(total):,}")
    print(f"AVG / TRADE:    Rs.{int(avg):,}")
    print(f"AVG / DAY:      Rs.{int(total / max(n_days, 1)):,}")
    print(f"MAX DAILY DD:   Rs.{int(dd):,}")
    print(f"\nBy exit reason:\n{by_reason.to_string()}")
    print(f"\nBy days-to-expiry:\n{by_dte.to_string()}")
    print(f"{'='*70}")

    md = ["# Tactic 1 - All Trading Days Backtest\n",
          f"Spot range: {spot_df.index.min().date()} -> "
          f"{spot_df.index.max().date()}",
          f"Capital Rs.{int(CAPITAL):,}/trade. Lot {LOT_SIZE}. "
          f"Premium gate Rs.{int(MIN_PREMIUM)}-Rs.{int(MAX_PREMIUM)}.",
          f"TP +{TP_PCT}% / SL -{SL_PCT}% / time-stop {TIME_STOP_MIN}m.",
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
          "## By days-to-expiry (DTE 0 = expiry day)\n```",
          by_dte.to_string(), "```", ""]
    md_path = base.REPORTS_DIR / "tactic_extreme_reversal_alldays_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
