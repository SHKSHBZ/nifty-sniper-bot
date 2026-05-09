"""T3 S/R-Bounce with optional Pillar 1 (candle) and Pillar 5 (VIX) filters.

Tests four variants on the same data:
  V0  baseline                — no extra filter (was -₹91k DD)
  V1  + candle filter         — require Bullish/Bearish pattern
                                on 5-min bar at entry minute (Pillar 1)
  V2  + VIX direction filter  — only fire if VIX move agrees with
                                bounce direction (Pillar 5)
  V3  + BOTH filters          — must pass candle AND VIX

Uses the best baseline config: Camarilla S3/R3 with 0.25% tolerance.

Outputs:
  reports/t3_with_filters_summary.md
  reports/t3_with_filters_<variant>_trades.csv
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

import backtesting.expiry_gamma_hero as base
from backtesting.candle_patterns import (
    any_bullish_pattern, any_bearish_pattern,
)
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)


# Strategy params (best baseline from earlier sweep)
ENTRY_WINDOW_START = dtime(9, 45)
ENTRY_WINDOW_END = dtime(14, 30)
EOD_TIME = dtime(15, 25)
COOLDOWN_MIN = 30
TP_PCT = 50.0
TRAIL_BE_AT_PCT = 20.0
SR_METHOD = "camarilla_3"
TOLERANCE_PCT = 0.25
ITM_OFFSET_STRIKES = 2

# VIX filter
DAY_OPEN_TIME = dtime(9, 15)

# Sizing / costs
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
    sr_kind: str
    side: str
    strike: int
    spot_at_entry: float
    entry_premium: float
    lots: int
    qty: int
    candle_pattern: str
    vix_pct_change: float
    exit_ts: str
    exit_premium: float
    exit_reason: str
    minutes_held: int
    net_pnl: float
    return_pct: float


def load_sr() -> pd.DataFrame:
    sr = pd.read_csv(base.REPORTS_DIR / "sr_levels_nifty.csv")
    sr["date"] = pd.to_datetime(sr["date"]).dt.date
    return sr.set_index("date")


def get_5m_bar_at(spot_df: pd.DataFrame, ts: pd.Timestamp,
                  bars_back: int = 0):
    """Return (o,h,l,c) for the 5-min bar ending `bars_back*5` minutes before ts."""
    end = ts - pd.Timedelta(minutes=bars_back * 5)
    start = end - pd.Timedelta(minutes=5)
    win = spot_df[(spot_df.index > start) & (spot_df.index <= end)]
    if len(win) < 3:
        return None
    return (float(win["open"].iloc[0]), float(win["high"].max()),
            float(win["low"].min()), float(win["close"].iloc[-1]))


def check_candle_filter(spot_df: pd.DataFrame, ts: pd.Timestamp,
                        kind: str) -> tuple[bool, str]:
    """Pillar 1 — does last 5-min bar form a bullish (for support) or
    bearish (for resistance) pattern?"""
    cur = get_5m_bar_at(spot_df, ts, bars_back=0)
    prev = get_5m_bar_at(spot_df, ts, bars_back=1)
    if cur is None:
        return False, ""
    if kind == "support":
        return any_bullish_pattern(prev, cur, min_body=2.0)
    else:
        return any_bearish_pattern(prev, cur, min_body=2.0)


def check_vix_filter(vix_df: pd.DataFrame, ts: pd.Timestamp,
                     kind: str) -> tuple[bool, float]:
    """Pillar 5 — VIX direction agrees with bounce.
    Bullish bounce (CE): VIX should be falling (negative pct from open).
    Bearish bounce (PE): VIX should be rising."""
    tz = ts.tz
    day_open_ts = pd.Timestamp.combine(ts.date(), DAY_OPEN_TIME).tz_localize(tz)
    vix_open = base.get_value_at(vix_df, day_open_ts, "close")
    vix_now = base.get_value_at(vix_df, ts, "close")
    if vix_open is None or vix_now is None or vix_open <= 0:
        return False, 0.0
    pct = (vix_now / vix_open - 1) * 100
    if kind == "support":
        # CE entry — VIX should be flat/falling (bullish)
        return pct <= 0.5, round(pct, 2)
    else:
        # PE entry — VIX should be flat/rising (bearish)
        return pct >= -0.5, round(pct, 2)


def detect_bounce(spot_df, ts, sr_level, kind, tolerance_pts):
    if ts not in spot_df.index:
        return False
    row = spot_df.loc[ts]
    spot = float(row["close"])
    low = float(row["low"])
    high = float(row["high"])
    if kind == "support":
        if abs(low - sr_level) > tolerance_pts:
            return False
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


def run_for_day(trade_date, exp_token, exp_date, spot_df, sr_df, vix_df,
                use_candle_filter, use_vix_filter):
    tz = spot_df.index.tz
    day_start = pd.Timestamp.combine(trade_date.date(), ENTRY_WINDOW_START).tz_localize(tz)
    day_end = pd.Timestamp.combine(trade_date.date(), EOD_TIME).tz_localize(tz)
    minutes = pd.date_range(day_start, day_end, freq="1min")

    if trade_date.date() not in sr_df.index:
        return []
    row = sr_df.loc[trade_date.date()]
    sup = row.get("cam_S3")
    res = row.get("cam_R3")
    if sup is None or res is None:
        return []

    days_to_exp = (exp_date.date() - trade_date.date()).days
    trades = []
    position = None
    last_exit_ts = None
    cache = {}

    def opt(strike, side):
        key = (strike, side)
        if key not in cache:
            cache[key] = base.load_option(strike, side, exp_token)
        return cache[key]

    for ts in minutes:
        spot = base.get_value_at(spot_df, ts, "close")
        if spot is None:
            continue
        tolerance_pts = spot * TOLERANCE_PCT / 100.0

        if position is not None:
            opt_df = opt(position["strike"], position["side"])
            cur = base.get_value_at(opt_df, ts, "close")
            if cur is None:
                continue
            if cur > position["peak"]:
                position["peak"] = cur
            if (not position["trail_armed"]
                    and cur >= position["entry_premium"] * (1 + TRAIL_BE_AT_PCT / 100)):
                position["trail_armed"] = True
                position["sl_premium"] = position["entry_premium"]
            exit_now = False
            reason = ""
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
                    sr_kind=position["sr_kind"], side=position["side"],
                    strike=position["strike"],
                    spot_at_entry=round(position["entry_spot"], 2),
                    entry_premium=round(position["entry_premium"], 2),
                    lots=position["lots"], qty=position["qty"],
                    candle_pattern=position["candle_pattern"],
                    vix_pct_change=position["vix_pct_change"],
                    exit_ts=ts.isoformat(),
                    exit_premium=round(cur, 2),
                    exit_reason=reason,
                    minutes_held=int((ts - position["entry_ts"]).total_seconds() // 60),
                    net_pnl=round(net, 2),
                    return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
                ))
                last_exit_ts = ts
                position = None
            else:
                continue

        if ts.time() < ENTRY_WINDOW_START or ts.time() > ENTRY_WINDOW_END:
            continue
        if last_exit_ts is not None and (ts - last_exit_ts).total_seconds() < COOLDOWN_MIN * 60:
            continue

        kind = None
        sr_level = None
        if detect_bounce(spot_df, ts, sup, "support", tolerance_pts):
            kind, sr_level = "support", sup
            side = "CE"
            atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
            strike = atm - ITM_OFFSET_STRIKES * STRIKE_STEP
        elif detect_bounce(spot_df, ts, res, "resistance", tolerance_pts):
            kind, sr_level = "resistance", res
            side = "PE"
            atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
            strike = atm + ITM_OFFSET_STRIKES * STRIKE_STEP
        else:
            continue

        candle_name = ""
        if use_candle_filter:
            ok, candle_name = check_candle_filter(spot_df, ts, kind)
            if not ok:
                continue

        vix_pct = 0.0
        if use_vix_filter:
            ok, vix_pct = check_vix_filter(vix_df, ts, kind)
            if not ok:
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
            "candle_pattern": candle_name,
            "vix_pct_change": vix_pct,
        }

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
            sr_kind=position["sr_kind"], side=position["side"],
            strike=position["strike"],
            spot_at_entry=round(position["entry_spot"], 2),
            entry_premium=round(position["entry_premium"], 2),
            lots=position["lots"], qty=position["qty"],
            candle_pattern=position["candle_pattern"],
            vix_pct_change=position["vix_pct_change"],
            exit_ts=day_end.isoformat(),
            exit_premium=round(cur, 2),
            exit_reason="EOD",
            minutes_held=int((day_end - position["entry_ts"]).total_seconds() // 60),
            net_pnl=round(net, 2),
            return_pct=round((eff_out - eff_in) / eff_in * 100, 2),
        ))
    return trades


def run_variant(name, use_candle, use_vix, spot_df, sr_df, vix_df,
                expiries_sorted, trading_days):
    all_trades = []
    for td in trading_days:
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        try:
            ts = run_for_day(td, tok, exp_date, spot_df, sr_df, vix_df,
                             use_candle, use_vix)
            all_trades.extend(ts)
        except Exception:
            continue
    n = len(all_trades)
    if n == 0:
        return all_trades, {"variant": name, "n": 0, "win_rate": 0,
                             "total": 0, "avg": 0, "dd": 0}
    df = pd.DataFrame([asdict(t) for t in all_trades])
    cum = df["net_pnl"].cumsum()
    dd = (cum.cummax() - cum).max()
    return all_trades, {
        "variant": name, "n": n,
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
    sr_df = load_sr()
    vix_df = pd.read_csv(base.DATA_DIR / "INDIA_VIX_1minute.csv")
    vix_df["ts"] = pd.to_datetime(vix_df["timestamp"])
    vix_df = vix_df.set_index("ts").sort_index()
    expiries = base.discover_expiries()
    expiries_sorted = build_expiry_lookup(expiries)
    trading_days = discover_trading_days(spot_df)
    latest = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest]
    print(f"Days: {len(trading_days)}\n")

    variants = [
        ("V0_baseline", False, False),
        ("V1_candles_only", True, False),
        ("V2_vix_only", False, True),
        ("V3_both", True, True),
    ]
    summary = []
    for name, candle, vix in variants:
        print(f"--- {name} ---")
        trades, stats = run_variant(name, candle, vix, spot_df, sr_df, vix_df,
                                    expiries_sorted, trading_days)
        summary.append(stats)
        print(f"  n={stats['n']}, win={stats['win_rate']}%, "
              f"total={stats['total']:,}, dd={stats['dd']:,}")
        if trades:
            df = pd.DataFrame([asdict(t) for t in trades])
            df.to_csv(base.REPORTS_DIR / f"t3_with_filters_{name}_trades.csv",
                      index=False)

    sdf = pd.DataFrame(summary)
    print(f"\n{'='*70}")
    print(sdf.to_string(index=False))

    md = ["# T3 with Pillar Filters — Comparison\n",
          f"Base config: {SR_METHOD}, tolerance {TOLERANCE_PCT}%, "
          f"ATM±{ITM_OFFSET_STRIKES} ITM strike.\n",
          "## Results\n```", sdf.to_string(index=False), "```", "",
          "Variants:",
          "- V0: no filter (baseline)",
          "- V1: + Pillar 1 (candle pattern at 5-min bar)",
          "- V2: + Pillar 5 (VIX direction agrees)",
          "- V3: both filters", ""]
    out = base.REPORTS_DIR / "t3_with_filters_summary.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
