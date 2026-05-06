"""Compute daily Support & Resistance levels for NIFTY and SENSEX.

For each trading day, builds two snapshots:

  PRE-MARKET (computed from yesterday's data only — known before open):
    - Classic Pivot Points (PP, R1/S1, R2/S2, R3/S3)
    - Camarilla Pivots (R1-R4 / S1-S4)
    - Yesterday's High, Low, Close

  POST-OPEN at 9:45 IST (after first 30 min of today's session):
    - Opening Range 15-min high/low
    - Opening Range 30-min high/low
    - Today's open price

The "S/R level" you saw on screen today (~23900 on NIFTY) likely
matches one of these — most commonly:
  - Classic S1 or Camarilla S3 for first support
  - Yesterday's low for major support
  - OR-15 low for intraday support

Outputs (one CSV per index, one row per trading day):
  reports/sr_levels_nifty.csv
  reports/sr_levels_sensex.csv

These can then feed into a Support-Bounce-Reversal backtest:
trade reversal at the moment spot touches a known S/R level.
"""
from __future__ import annotations

from datetime import time as dtime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"

INDEXES = {
    "nifty":  DATA / "NIFTY50_INDEX_1minute.csv",
    "sensex": DATA / "SENSEX_INDEX_1minute.csv",
}

OR_15_END = dtime(9, 30)   # 9:15 + 15 = 9:30
OR_30_END = dtime(9, 45)   # 9:15 + 30 = 9:45
MARKET_OPEN = dtime(9, 15)


def daily_ohlc(spot_df: pd.DataFrame) -> pd.DataFrame:
    """One row per trading day: O/H/L/C from minute data."""
    spot_df = spot_df.copy()
    spot_df["date"] = spot_df.index.date
    g = spot_df.groupby("date")
    daily = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
    }).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    return daily.sort_values("date").reset_index(drop=True)


def classic_pivots(prev_high, prev_low, prev_close) -> dict:
    """Classic floor pivots — most common pre-market levels."""
    pp = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pp - prev_low
    s1 = 2 * pp - prev_high
    r2 = pp + (prev_high - prev_low)
    s2 = pp - (prev_high - prev_low)
    r3 = prev_high + 2 * (pp - prev_low)
    s3 = prev_low - 2 * (prev_high - pp)
    return {
        "pivot_PP": round(pp, 2),
        "pivot_R1": round(r1, 2), "pivot_S1": round(s1, 2),
        "pivot_R2": round(r2, 2), "pivot_S2": round(s2, 2),
        "pivot_R3": round(r3, 2), "pivot_S3": round(s3, 2),
    }


def camarilla_pivots(prev_high, prev_low, prev_close) -> dict:
    """Camarilla — intraday traders' favourite. R3/S3 typically the
    'first resistance/support' that intraday spot tests."""
    rng = prev_high - prev_low
    return {
        "cam_R1": round(prev_close + rng * 1.1 / 12, 2),
        "cam_S1": round(prev_close - rng * 1.1 / 12, 2),
        "cam_R2": round(prev_close + rng * 1.1 / 6, 2),
        "cam_S2": round(prev_close - rng * 1.1 / 6, 2),
        "cam_R3": round(prev_close + rng * 1.1 / 4, 2),
        "cam_S3": round(prev_close - rng * 1.1 / 4, 2),
        "cam_R4": round(prev_close + rng * 1.1 / 2, 2),
        "cam_S4": round(prev_close - rng * 1.1 / 2, 2),
    }


def opening_range(spot_df: pd.DataFrame, the_date,
                  start_time, end_time) -> tuple:
    """High/Low between start_time and end_time on `the_date`."""
    day = spot_df[spot_df.index.date == the_date.date()]
    in_window = day[
        (day.index.time >= start_time) & (day.index.time <= end_time)
    ]
    if in_window.empty:
        return (None, None)
    return (round(in_window["high"].max(), 2),
            round(in_window["low"].min(), 2))


def build_sr_table(spot_df: pd.DataFrame) -> pd.DataFrame:
    daily = daily_ohlc(spot_df)
    daily["prev_close"] = daily["close"].shift(1)
    daily["prev_high"] = daily["high"].shift(1)
    daily["prev_low"] = daily["low"].shift(1)
    daily = daily.dropna(subset=["prev_close"]).reset_index(drop=True)

    rows = []
    for _, r in daily.iterrows():
        d = r["date"]
        # Pre-market levels (use yesterday only)
        cp = classic_pivots(r["prev_high"], r["prev_low"], r["prev_close"])
        cm = camarilla_pivots(r["prev_high"], r["prev_low"], r["prev_close"])
        # Post-open levels (use today's first 15/30 min)
        or15_h, or15_l = opening_range(spot_df, d, MARKET_OPEN, OR_15_END)
        or30_h, or30_l = opening_range(spot_df, d, MARKET_OPEN, OR_30_END)

        rows.append({
            "date": d.date().isoformat(),
            "weekday": d.strftime("%A"),
            "day_open": round(r["open"], 2),
            "day_high": round(r["high"], 2),
            "day_low": round(r["low"], 2),
            "day_close": round(r["close"], 2),
            "prev_close": round(r["prev_close"], 2),
            "prev_high": round(r["prev_high"], 2),
            "prev_low": round(r["prev_low"], 2),
            **cp, **cm,
            "or15_high": or15_h, "or15_low": or15_l,
            "or30_high": or30_h, "or30_low": or30_l,
        })
    return pd.DataFrame(rows)


def main():
    REPORTS.mkdir(exist_ok=True)
    for name, path in INDEXES.items():
        if not path.exists():
            print(f"[skip] {name} - {path} missing")
            continue
        print(f"[{name}] loading {path.name}...")
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        sr = build_sr_table(df)
        out = REPORTS / f"sr_levels_{name}.csv"
        sr.to_csv(out, index=False)
        print(f"  wrote {len(sr)} days -> {out}")
        # Quick sanity-check on the most recent row
        last = sr.iloc[-1]
        print(f"  most recent ({last['date']}, "
              f"close {last['day_close']}):")
        print(f"    Classic S1={last['pivot_S1']}  "
              f"R1={last['pivot_R1']}  PP={last['pivot_PP']}")
        print(f"    Camarilla S3={last['cam_S3']}  "
              f"R3={last['cam_R3']}  S4={last['cam_S4']}  R4={last['cam_R4']}")
        print(f"    Yesterday H/L: {last['prev_high']} / {last['prev_low']}")
        print(f"    OR-15 H/L: {last['or15_high']} / {last['or15_low']}")
        print(f"    OR-30 H/L: {last['or30_high']} / {last['or30_low']}\n")


if __name__ == "__main__":
    main()
