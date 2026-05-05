"""Tactic 1 backtest: Spot-Extreme Reversal Scalper.

LOGIC (per minute, in entry window):
  1. Compute 5-min rolling spot range (max - min over last 5 bars).
  2. Skip if range < MIN_RANGE_PTS (not volatile enough for mean-revert).
  3. If spot within EDGE_PROXIMITY pts of 5-min low -> buy ATM CE.
     If spot within EDGE_PROXIMITY pts of 5-min high -> buy ATM PE.
  4. Premium gate Rs.20-100 (rejects illiquid OTM and expensive deep-ITM).
  5. Position sizing: lots = floor(20000 / (premium * 65)).
  6. Exits: +30% TP, -25% SL, or 20-min time-stop.
  7. Cooldown: no entry within COOLDOWN_MIN minutes of last exit on same side.
  8. Entry window: 09:30 to 14:30 IST (skip open noise + late-day weirdness).
  9. Only one position at a time (single-position bot model).

Tested on the 41 valid NIFTY expiries we already have data for.

Output: reports/tactic_extreme_reversal_trades.csv
        reports/tactic_extreme_reversal_summary.md
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

import backtesting.expiry_gamma_hero as base


# ---------- Tactic parameters ----------
ENTRY_WINDOW_START = dtime(9, 30)
ENTRY_WINDOW_END = dtime(14, 30)
ROLLING_WINDOW_MIN = 5
MIN_RANGE_PTS = 30.0
EDGE_PROXIMITY_PTS = 5.0
MIN_PREMIUM = 20.0
MAX_PREMIUM = 100.0
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
    expiry: str
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
    gross_pnl: float
    net_pnl: float
    return_pct: float


def run_for_expiry(expiry_token: str, expiry_date: datetime,
                   spot_df: pd.DataFrame) -> list[ScalpTrade]:
    """Walk minute-by-minute through this expiry day, run Tactic 1."""
    tz = spot_df.index.tz
    day_start = pd.Timestamp.combine(expiry_date.date(),
                                     ENTRY_WINDOW_START).tz_localize(tz)
    day_end = pd.Timestamp.combine(expiry_date.date(),
                                   dtime(15, 25)).tz_localize(tz)
    minutes = pd.date_range(day_start, day_end, freq="1min")

    trades: list[ScalpTrade] = []
    position: Optional[dict] = None
    last_exit_ts_by_side: dict[str, Optional[pd.Timestamp]] = {"CE": None, "PE": None}
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

        # ----- Manage open position -----
        if position is not None:
            opt_df = opt(position["strike"], position["side"])
            cur_p = base.get_value_at(opt_df, ts, "close")
            if cur_p is None:
                # No data - just continue with stale position state
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
                    expiry=expiry_token,
                    entry_ts=position["entry_ts"].isoformat(),
                    side=position["side"],
                    strike=position["strike"],
                    entry_spot=round(position["entry_spot"], 2),
                    range_5m_at_entry=round(position["range_5m"], 2),
                    entry_premium=round(position["entry_premium"], 2),
                    lots=position["lots"], qty=position["qty"],
                    exit_ts=ts.isoformat(),
                    exit_premium=round(cur_p, 2),
                    exit_spot=round(spot, 2),
                    exit_reason=reason,
                    minutes_held=int((ts - position["entry_ts"]).total_seconds() // 60),
                    gross_pnl=round(gross, 2),
                    net_pnl=round(net, 2),
                    return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
                ))
                last_exit_ts_by_side[position["side"]] = ts
                position = None
            else:
                continue  # holding - skip entry logic

        # ----- Entry window check -----
        if ts.time() < ENTRY_WINDOW_START or ts.time() > ENTRY_WINDOW_END:
            continue

        # ----- Compute 5-min range -----
        win_start = ts - pd.Timedelta(minutes=ROLLING_WINDOW_MIN)
        win = spot_df[(spot_df.index >= win_start) & (spot_df.index <= ts)]
        if len(win) < ROLLING_WINDOW_MIN:
            continue
        range_high = float(win["close"].max())
        range_low = float(win["close"].min())
        range_size = range_high - range_low
        if range_size < MIN_RANGE_PTS:
            continue

        # ----- Edge detection -----
        side: Optional[str] = None
        if (spot - range_low) <= EDGE_PROXIMITY_PTS:
            side = "CE"
        elif (range_high - spot) <= EDGE_PROXIMITY_PTS:
            side = "PE"
        if side is None:
            continue

        # Cooldown check
        last = last_exit_ts_by_side.get(side)
        if last is not None and (ts - last).total_seconds() < COOLDOWN_MIN * 60:
            continue

        # ----- Strike & premium check -----
        atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
        opt_df = opt(atm, side)
        if opt_df is None:
            continue
        prem = base.get_value_at(opt_df, ts, "close")
        if prem is None or not (MIN_PREMIUM <= prem <= MAX_PREMIUM):
            continue

        # ----- Sizing -----
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
            "lots": lots, "qty": qty,
            "range_5m": range_size,
        }

    # Force-close any open position at day end
    if position is not None:
        opt_df = opt(position["strike"], position["side"])
        last_ts = day_end
        cur_p = base.get_value_at(opt_df, last_ts, "close")
        if cur_p is None:
            sub = opt_df[opt_df.index <= last_ts] if opt_df is not None else None
            cur_p = float(sub["close"].iloc[-1]) if sub is not None and len(sub) else 0.0
        eff_in = position["entry_premium"] + SLIPPAGE_PER_LEG
        eff_out = max(0.0, cur_p - SLIPPAGE_PER_LEG)
        gross = (eff_out - eff_in) * position["qty"]
        net = gross - BROKERAGE_PER_TRADE
        trades.append(ScalpTrade(
            expiry=expiry_token,
            entry_ts=position["entry_ts"].isoformat(),
            side=position["side"], strike=position["strike"],
            entry_spot=round(position["entry_spot"], 2),
            range_5m_at_entry=round(position["range_5m"], 2),
            entry_premium=round(position["entry_premium"], 2),
            lots=position["lots"], qty=position["qty"],
            exit_ts=last_ts.isoformat(),
            exit_premium=round(cur_p, 2),
            exit_spot=round(spot or 0, 2),
            exit_reason="EOD_FORCE_CLOSE",
            minutes_held=int((last_ts - position["entry_ts"]).total_seconds() // 60),
            gross_pnl=round(gross, 2), net_pnl=round(net, 2),
            return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
        ))
    return trades


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = base.discover_expiries()
    print(f"Loaded {len(expiries)} expiries.\n")
    print(f"{'Expiry':<12} {'Trades':>7} {'Wins':>6} {'TP':>4} {'SL':>4} "
          f"{'TIME':>5} {'Net P&L':>11}")
    print("-" * 60)

    all_trades: list[ScalpTrade] = []
    by_expiry: list[dict] = []
    for tok, dt in expiries:
        try:
            ts = run_for_expiry(tok, dt, spot_df)
        except Exception as e:
            print(f"  {tok}: ERR {e}")
            continue
        all_trades.extend(ts)
        n = len(ts)
        if n == 0:
            print(f"{tok:<12} {n:>7}")
            by_expiry.append({"expiry": tok, "trades": 0, "wins": 0, "pnl": 0})
            continue
        df_e = pd.DataFrame([asdict(t) for t in ts])
        wins = (df_e["net_pnl"] > 0).sum()
        tp = (df_e["exit_reason"] == "TP").sum()
        sl = (df_e["exit_reason"] == "SL").sum()
        time_stop = (df_e["exit_reason"] == "TIME_STOP").sum()
        pnl = int(df_e["net_pnl"].sum())
        by_expiry.append({"expiry": tok, "trades": n, "wins": int(wins),
                          "pnl": pnl})
        print(f"{tok:<12} {n:>7} {wins:>6} {tp:>4} {sl:>4} {time_stop:>5} "
              f"Rs.{pnl:>8,}")

    if not all_trades:
        print("\nNo trades generated.")
        return

    df = pd.DataFrame([asdict(t) for t in all_trades])
    out_csv = base.REPORTS_DIR / "tactic_extreme_reversal_trades.csv"
    df.to_csv(out_csv, index=False)

    # Aggregate
    total = df["net_pnl"].sum()
    wins = (df["net_pnl"] > 0).sum()
    losses = (df["net_pnl"] < 0).sum()
    n = len(df)
    avg = df["net_pnl"].mean()
    cum = df.groupby("expiry")["net_pnl"].sum().cumsum()
    dd = (cum.cummax() - cum).max() if len(cum) else 0
    by_reason = df.groupby("exit_reason").agg(
        n=("net_pnl", "count"),
        total=("net_pnl", "sum"),
        avg=("net_pnl", "mean"),
    )

    print(f"\n{'='*70}")
    print(f"TOTAL TRADES:    {n}")
    print(f"WIN RATE:        {wins}/{n} = {wins/n*100:.1f}%  ({losses} losses)")
    print(f"TOTAL NET P&L:   Rs.{int(total):,}")
    print(f"AVG / TRADE:     Rs.{int(avg):,}")
    print(f"AVG / EXPIRY:    Rs.{int(total/len(by_expiry)):,}")
    print(f"MAX EXPIRY-LEVEL DRAWDOWN: Rs.{int(dd):,}")
    print(f"\nBy exit reason:\n{by_reason.to_string()}")
    print(f"{'='*70}")
    print(f"Wrote {out_csv}")

    # Markdown summary
    md = ["# Tactic 1: Spot-Extreme Reversal Scalper — Backtest\n",
          f"Capital Rs.{int(CAPITAL):,}/trade. Lot {LOT_SIZE}. ",
          f"Premium gate Rs.{int(MIN_PREMIUM)}-Rs.{int(MAX_PREMIUM)}. ",
          f"TP +{TP_PCT}% / SL -{SL_PCT}% / time-stop {TIME_STOP_MIN}m.",
          f"Range gate >{MIN_RANGE_PTS} pts in {ROLLING_WINDOW_MIN}-min window.",
          f"Slippage Rs.{SLIPPAGE_PER_LEG}/leg. Brokerage Rs.{int(BROKERAGE_PER_TRADE)}/trade.\n",
          "## Headline\n",
          f"- Expiries: **{len(by_expiry)}**",
          f"- Total trades: **{n}**",
          f"- Win rate: **{wins}/{n} = {wins/n*100:.1f}%**",
          f"- Total net P&L: **Rs.{int(total):,}**",
          f"- Avg / trade: Rs.{int(avg):,}",
          f"- Avg / expiry: Rs.{int(total/len(by_expiry)):,}",
          f"- Max drawdown: Rs.{int(dd):,}",
          "",
          "## Per-expiry breakdown\n```",
          pd.DataFrame(by_expiry).to_string(index=False),
          "```",
          "",
          "## By exit reason\n```",
          by_reason.to_string(),
          "```", ""]
    md_path = base.REPORTS_DIR / "tactic_extreme_reversal_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
