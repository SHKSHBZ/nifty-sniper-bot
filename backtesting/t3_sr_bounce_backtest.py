"""T3: Support/Resistance Bounce Reversal backtest.

Replicates the manual trade pattern you used today:
  - Spot tests pre-computed S/R level
  - Bounce confirmed (next 5-min candle reverses)
  - Buy ITM CE if bouncing off support
  - Buy ITM PE if rejecting from resistance
  - Exit: +50% TP / break of level SL / EOD 15:25

Tests four S/R methods to find which works best:
  - Camarilla S3/R3
  - Camarilla S4/R4 (wider)
  - Classic Pivot S1/R1
  - Yesterday's High/Low

For each method, sweeps tolerance bands {0.15%, 0.25%, 0.4%}.

Data:
  - reports/sr_levels_nifty.csv (precomputed levels per day)
  - data/NIFTY50_INDEX_1minute.csv
  - data/NIFTY_<strike>_<CE|PE>_<exp>_1min.csv
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


ENTRY_WINDOW_START = dtime(9, 45)
ENTRY_WINDOW_END = dtime(14, 30)
EOD_TIME = dtime(15, 25)
COOLDOWN_MIN = 30
TP_PCT = 50.0
TRAIL_BE_AT_PCT = 20.0

# Strike selection: how many strikes ITM for the directional bet
ITM_OFFSET_STRIKES = 2     # CE at support: strike = ATM - 2*step
                           # PE at resistance: strike = ATM + 2*step
STRIKE_STEP = 50
LOT_SIZE = 65
CAPITAL = 20_000.0
SLIPPAGE_PER_LEG = 0.05
BROKERAGE_PER_TRADE = 60.0
MIN_PREMIUM = 30.0
MAX_PREMIUM = 400.0


@dataclass
class SRTrade:
    trade_date: str
    expiry: str
    dte: int
    entry_ts: str
    sr_method: str
    sr_level: float
    sr_kind: str   # "support" or "resistance"
    side: str
    strike: int
    spot_at_entry: float
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


def load_sr_levels() -> pd.DataFrame:
    sr = pd.read_csv(base.REPORTS_DIR / "sr_levels_nifty.csv")
    sr["date"] = pd.to_datetime(sr["date"]).dt.date
    return sr.set_index("date")


def get_sr_for_day(sr_df: pd.DataFrame, the_date,
                   method: str) -> tuple[Optional[float], Optional[float]]:
    """Return (support, resistance) for given method on this date."""
    if the_date not in sr_df.index:
        return None, None
    row = sr_df.loc[the_date]
    if method == "camarilla_3":
        return row.get("cam_S3"), row.get("cam_R3")
    if method == "camarilla_4":
        return row.get("cam_S4"), row.get("cam_R4")
    if method == "classic_1":
        return row.get("pivot_S1"), row.get("pivot_R1")
    if method == "prev_hl":
        return row.get("prev_low"), row.get("prev_high")
    if method == "or30":
        return row.get("or30_low"), row.get("or30_high")
    return None, None


def detect_bounce(spot_df: pd.DataFrame, ts, sr_level: float,
                  kind: str, tolerance_pts: float) -> bool:
    """Confirm that spot tested the level then reversed.
    For support: spot dipped to within tolerance, next bar closed higher.
    For resistance: spot rose to within tolerance, next bar closed lower."""
    if ts not in spot_df.index:
        return False
    row = spot_df.loc[ts]
    spot = float(row["close"])
    low = float(row["low"])
    high = float(row["high"])
    if kind == "support":
        if abs(low - sr_level) > tolerance_pts:
            return False
        # Need to see a bounce: prev bar's close should be close to low,
        # current close should be above prev close
        idx = spot_df.index.get_loc(ts)
        if idx == 0:
            return False
        prev = spot_df.iloc[idx - 1]
        return spot > float(prev["close"])
    if kind == "resistance":
        if abs(high - sr_level) > tolerance_pts:
            return False
        idx = spot_df.index.get_loc(ts)
        if idx == 0:
            return False
        prev = spot_df.iloc[idx - 1]
        return spot < float(prev["close"])
    return False


def run_for_day(trade_date: datetime, exp_token: str, exp_date: datetime,
                spot_df: pd.DataFrame, sr_df: pd.DataFrame,
                method: str, tolerance_pct: float) -> list[SRTrade]:
    tz = spot_df.index.tz
    day_start = pd.Timestamp.combine(trade_date.date(), ENTRY_WINDOW_START).tz_localize(tz)
    day_end = pd.Timestamp.combine(trade_date.date(), EOD_TIME).tz_localize(tz)
    entry_close = pd.Timestamp.combine(trade_date.date(), ENTRY_WINDOW_END).tz_localize(tz)
    minutes = pd.date_range(day_start, day_end, freq="1min")

    sup, res = get_sr_for_day(sr_df, trade_date.date(), method)
    if sup is None or res is None:
        return []

    days_to_exp = (exp_date.date() - trade_date.date()).days
    trades: list[SRTrade] = []
    position: Optional[dict] = None
    last_exit_ts: Optional[pd.Timestamp] = None
    cache: dict[tuple[int, str], Optional[pd.DataFrame]] = {}

    def opt(strike: int, side: str):
        key = (strike, side)
        if key not in cache:
            cache[key] = base.load_option(strike, side, exp_token)
        return cache[key]

    for ts in minutes:
        spot = base.get_value_at(spot_df, ts, "close")
        if spot is None:
            continue
        tolerance_pts = spot * tolerance_pct / 100.0

        # Manage open position
        if position is not None:
            opt_df = opt(position["strike"], position["side"])
            cur = base.get_value_at(opt_df, ts, "close")
            if cur is None:
                continue
            # Update peak
            if cur > position["peak"]:
                position["peak"] = cur
            # Trail to BE
            if (not position["trail_armed"]
                    and cur >= position["entry_premium"] * (1 + TRAIL_BE_AT_PCT / 100)):
                position["trail_armed"] = True
                position["sl_premium"] = position["entry_premium"]
            exit_now = False
            reason = ""
            # SL: break of S/R level
            if position["side"] == "CE" and spot < position["sr_level"] - tolerance_pts:
                exit_now, reason = True, "BREAK_SR"
            elif position["side"] == "PE" and spot > position["sr_level"] + tolerance_pts:
                exit_now, reason = True, "BREAK_SR"
            elif cur >= position["tp_premium"]:
                exit_now, reason = True, "TP"
            elif cur <= position["sl_premium"]:
                exit_now, reason = True, ("BE_STOP" if position["trail_armed"] else "SL")
            elif ts >= day_end:
                exit_now, reason = True, "EOD"
            if exit_now:
                eff_in = position["entry_premium"] + SLIPPAGE_PER_LEG
                eff_out = max(0.0, cur - SLIPPAGE_PER_LEG)
                net = (eff_out - eff_in) * position["qty"] - BROKERAGE_PER_TRADE
                trades.append(SRTrade(
                    trade_date=trade_date.date().isoformat(),
                    expiry=exp_token, dte=days_to_exp,
                    entry_ts=position["entry_ts"].isoformat(),
                    sr_method=method,
                    sr_level=round(position["sr_level"], 2),
                    sr_kind=position["sr_kind"],
                    side=position["side"], strike=position["strike"],
                    spot_at_entry=round(position["entry_spot"], 2),
                    entry_premium=round(position["entry_premium"], 2),
                    lots=position["lots"], qty=position["qty"],
                    exit_ts=ts.isoformat(),
                    exit_premium=round(cur, 2),
                    exit_spot=round(spot, 2),
                    exit_reason=reason,
                    minutes_held=int((ts - position["entry_ts"]).total_seconds() // 60),
                    net_pnl=round(net, 2),
                    return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
                ))
                last_exit_ts = ts
                position = None
            else:
                continue

        # Entry window
        if ts.time() < ENTRY_WINDOW_START or ts.time() > ENTRY_WINDOW_END:
            continue
        if last_exit_ts is not None and (ts - last_exit_ts).total_seconds() < COOLDOWN_MIN * 60:
            continue

        # Check for support bounce or resistance rejection
        kind = None
        sr_level = None
        if detect_bounce(spot_df, ts, sup, "support", tolerance_pts):
            kind, sr_level = "support", sup
            side = "CE"
            atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
            strike = atm - ITM_OFFSET_STRIKES * STRIKE_STEP   # ITM CE
        elif detect_bounce(spot_df, ts, res, "resistance", tolerance_pts):
            kind, sr_level = "resistance", res
            side = "PE"
            atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
            strike = atm + ITM_OFFSET_STRIKES * STRIKE_STEP   # ITM PE
        else:
            continue

        opt_df = opt(strike, side)
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
            "side": side, "strike": strike, "entry_ts": ts,
            "entry_spot": spot, "entry_premium": prem,
            "tp_premium": prem * (1 + TP_PCT / 100),
            "sl_premium": prem * (1 - 30 / 100),
            "sr_level": sr_level, "sr_kind": kind,
            "lots": lots, "qty": qty,
            "peak": prem, "trail_armed": False,
        }

    # Force close at EOD
    if position is not None:
        opt_df = opt(position["strike"], position["side"])
        cur = base.get_value_at(opt_df, day_end, "close")
        if cur is None:
            sub = opt_df[opt_df.index <= day_end] if opt_df is not None else None
            cur = float(sub["close"].iloc[-1]) if sub is not None and len(sub) else 0.0
        eff_in = position["entry_premium"] + SLIPPAGE_PER_LEG
        eff_out = max(0.0, cur - SLIPPAGE_PER_LEG)
        net = (eff_out - eff_in) * position["qty"] - BROKERAGE_PER_TRADE
        trades.append(SRTrade(
            trade_date=trade_date.date().isoformat(),
            expiry=exp_token, dte=days_to_exp,
            entry_ts=position["entry_ts"].isoformat(),
            sr_method=method, sr_level=round(position["sr_level"], 2),
            sr_kind=position["sr_kind"],
            side=position["side"], strike=position["strike"],
            spot_at_entry=round(position["entry_spot"], 2),
            entry_premium=round(position["entry_premium"], 2),
            lots=position["lots"], qty=position["qty"],
            exit_ts=day_end.isoformat(),
            exit_premium=round(cur, 2),
            exit_spot=round(spot or 0, 2),
            exit_reason="EOD",
            minutes_held=int((day_end - position["entry_ts"]).total_seconds() // 60),
            net_pnl=round(net, 2),
            return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
        ))
    return trades


def run_variant(method: str, tolerance_pct: float, spot_df, sr_df,
                expiries_sorted, trading_days) -> tuple[list, dict]:
    all_trades = []
    for td in trading_days:
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        try:
            ts = run_for_day(td, tok, exp_date, spot_df, sr_df,
                             method, tolerance_pct)
            all_trades.extend(ts)
        except Exception:
            continue
    n = len(all_trades)
    if n == 0:
        return all_trades, {"method": method, "tol": tolerance_pct, "n": 0,
                             "win_rate": 0, "total": 0, "avg": 0, "dd": 0}
    df = pd.DataFrame([asdict(t) for t in all_trades])
    cum = df["net_pnl"].cumsum()
    dd = (cum.cummax() - cum).max()
    return all_trades, {
        "method": method, "tol": tolerance_pct, "n": n,
        "win_rate": round((df["net_pnl"] > 0).mean() * 100, 1),
        "total": int(df["net_pnl"].sum()),
        "avg": int(df["net_pnl"].mean()),
        "dd": int(dd),
    }


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts").sort_index()
    sr_df = load_sr_levels()
    expiries = base.discover_expiries()
    expiries_sorted = build_expiry_lookup(expiries)
    trading_days = discover_trading_days(spot_df)
    latest = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest]
    print(f"Days: {len(trading_days)}\n")

    methods = ["camarilla_3", "camarilla_4", "classic_1", "prev_hl", "or30"]
    tols = [0.15, 0.25, 0.4]

    summary = []
    best_trades = []
    best_total = 0
    best_label = None
    for method in methods:
        for tol in tols:
            print(f"--- {method} tol={tol}% ---")
            trades, stats = run_variant(method, tol, spot_df, sr_df,
                                        expiries_sorted, trading_days)
            summary.append(stats)
            print(f"  n={stats['n']}, win={stats['win_rate']}%, "
                  f"total={stats['total']:,}, dd={stats['dd']:,}")
            if stats["total"] > best_total:
                best_total = stats["total"]
                best_label = f"{method}@{tol}"
                best_trades = trades

    sdf = pd.DataFrame(summary)
    out_csv = base.REPORTS_DIR / "t3_sr_bounce_sweep.csv"
    sdf.to_csv(out_csv, index=False)
    if best_trades:
        td = pd.DataFrame([asdict(t) for t in best_trades])
        td.to_csv(base.REPORTS_DIR / "t3_sr_bounce_best_trades.csv", index=False)

    print(f"\n{'='*70}")
    print(f"BEST CONFIG: {best_label}, total Rs.{best_total:,}")
    print(sdf.sort_values("total", ascending=False).to_string(index=False))

    md = ["# T3 — Support/Resistance Bounce Reversal\n",
          f"Tested {len(methods)*len(tols)} variants across "
          f"{len(trading_days)} trading days.",
          f"Strike: ATM±{ITM_OFFSET_STRIKES} ITM ({ITM_OFFSET_STRIKES*STRIKE_STEP}pts ITM).\n",
          "## Sweep results (sorted by total P&L)\n```",
          sdf.sort_values("total", ascending=False).to_string(index=False),
          "```", "",
          f"**Best**: {best_label} = Rs.{best_total:,}", ""]
    md_path = base.REPORTS_DIR / "t3_sr_bounce_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
