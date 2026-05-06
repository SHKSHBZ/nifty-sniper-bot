"""India VIX vs NIFTY spot — day-by-day and hour-by-hour analysis.

Builds:
  1. Daily summary: VIX open/close/range/%chg vs NIFTY same. Correlation
     between VIX move and NIFTY move per day.
  2. Hourly average pattern: at each hour-of-day (09, 10, 11... 15),
     compute the average VIX & NIFTY behaviour.
  3. VIX regime classification:
       Low      <  12
       Normal   12-15
       Elevated 15-18
       High     >  18
     Then per-regime: avg NIFTY range, % up days, biggest move.
  4. VIX leads NIFTY? Lag correlation: does today's VIX move predict
     tomorrow's NIFTY move? Does VIX in first hour predict NIFTY's
     full-day range?

Outputs:
  reports/vix_nifty_daily.csv
  reports/vix_nifty_hourly_pattern.csv
  reports/vix_nifty_relationship_summary.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"


def load_minute(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    df["hour"] = df["timestamp"].dt.hour
    return df


def daily_summary(spot: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    """One row per trading day with both indices' OHLC + range + change."""
    s_daily = spot.groupby("date").agg(
        nifty_open=("open", "first"), nifty_high=("high", "max"),
        nifty_low=("low", "min"), nifty_close=("close", "last"),
    ).reset_index()
    v_daily = vix.groupby("date").agg(
        vix_open=("open", "first"), vix_high=("high", "max"),
        vix_low=("low", "min"), vix_close=("close", "last"),
    ).reset_index()
    df = s_daily.merge(v_daily, on="date")
    df["nifty_range"] = df["nifty_high"] - df["nifty_low"]
    df["nifty_pct_chg"] = (df["nifty_close"] / df["nifty_open"] - 1) * 100
    df["vix_range"] = df["vix_high"] - df["vix_low"]
    df["vix_pct_chg"] = (df["vix_close"] / df["vix_open"] - 1) * 100
    df["nifty_direction"] = df["nifty_pct_chg"].apply(
        lambda x: "UP" if x > 0.1 else ("DOWN" if x < -0.1 else "FLAT")
    )
    # VIX regime by close
    def regime(v):
        if v < 12: return "Low"
        if v < 15: return "Normal"
        if v < 18: return "Elevated"
        return "High"
    df["vix_regime"] = df["vix_close"].apply(regime)
    return df


def hourly_pattern(spot: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    """Average move per hour-of-day across all trading days."""
    spot = spot.copy()
    vix = vix.copy()
    spot_h = spot.groupby(["date", "hour"]).agg(
        nifty_open=("open", "first"), nifty_close=("close", "last"),
        nifty_high=("high", "max"), nifty_low=("low", "min"),
    ).reset_index()
    vix_h = vix.groupby(["date", "hour"]).agg(
        vix_open=("open", "first"), vix_close=("close", "last"),
        vix_high=("high", "max"), vix_low=("low", "min"),
    ).reset_index()
    h = spot_h.merge(vix_h, on=["date", "hour"])
    h["nifty_pct_chg"] = (h["nifty_close"] / h["nifty_open"] - 1) * 100
    h["vix_pct_chg"] = (h["vix_close"] / h["vix_open"] - 1) * 100
    h["nifty_range_pts"] = h["nifty_high"] - h["nifty_low"]
    # Filter to market hours 09-15
    h = h[(h["hour"] >= 9) & (h["hour"] <= 15)]
    pattern = h.groupby("hour").agg(
        n_days=("date", "nunique"),
        avg_nifty_pct_chg=("nifty_pct_chg", "mean"),
        avg_nifty_range=("nifty_range_pts", "mean"),
        avg_vix_pct_chg=("vix_pct_chg", "mean"),
    )
    return pattern


def regime_analysis(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.groupby("vix_regime").agg(
        n_days=("date", "count"),
        avg_nifty_range=("nifty_range", "mean"),
        median_nifty_range=("nifty_range", "median"),
        avg_nifty_pct_chg=("nifty_pct_chg", "mean"),
        pct_up_days=("nifty_pct_chg", lambda x: (x > 0.1).mean() * 100),
        pct_down_days=("nifty_pct_chg", lambda x: (x < -0.1).mean() * 100),
        biggest_up_move=("nifty_pct_chg", "max"),
        biggest_down_move=("nifty_pct_chg", "min"),
    ).round(2)


def lag_correlation(daily: pd.DataFrame) -> dict:
    """Does today's VIX move predict tomorrow's NIFTY?"""
    d = daily.copy().reset_index(drop=True)
    d["nifty_next"] = d["nifty_pct_chg"].shift(-1)
    d["vix_today"] = d["vix_pct_chg"]
    same_day_corr = d[["nifty_pct_chg", "vix_pct_chg"]].corr().iloc[0, 1]
    next_day_corr = d[["nifty_next", "vix_today"]].corr().iloc[0, 1]
    # First-hour VIX vs full-day NIFTY range — needs hourly recompute, simplified:
    return {
        "same_day_vix_vs_nifty_corr": round(same_day_corr, 3),
        "vix_today_vs_nifty_next_corr": round(next_day_corr, 3),
    }


def first_hour_predictor(spot: pd.DataFrame, vix: pd.DataFrame) -> dict:
    """Does VIX 9:15-10:00 movement predict NIFTY's full-day range?"""
    s_first = spot[(spot["hour"] == 9)].groupby("date").agg(
        nifty_open=("open", "first"), nifty_close_10=("close", "last"),
    ).reset_index()
    v_first = vix[(vix["hour"] == 9)].groupby("date").agg(
        vix_open=("open", "first"), vix_close_10=("close", "last"),
    ).reset_index()
    d = s_first.merge(v_first, on="date")
    d["vix_first_hr_pct"] = (d["vix_close_10"] / d["vix_open"] - 1) * 100

    # Day's full range
    full = spot.groupby("date").agg(
        nifty_high=("high", "max"), nifty_low=("low", "min"),
    ).reset_index()
    full["nifty_full_range_pts"] = full["nifty_high"] - full["nifty_low"]
    d = d.merge(full, on="date")
    corr = d[["vix_first_hr_pct", "nifty_full_range_pts"]].corr().iloc[0, 1]

    # Group: when VIX rises >2% in first hour, what's the avg full-day range?
    high_vix_jump = d[d["vix_first_hr_pct"] > 2.0]
    low_vix_jump = d[d["vix_first_hr_pct"] < -2.0]
    flat = d[d["vix_first_hr_pct"].abs() <= 0.5]
    return {
        "first_hour_vix_vs_full_range_corr": round(corr, 3),
        "n_days_vix_up_2pct_first_hr": len(high_vix_jump),
        "avg_range_when_vix_up_2pct": round(high_vix_jump["nifty_full_range_pts"].mean(), 0)
            if len(high_vix_jump) else None,
        "n_days_vix_down_2pct_first_hr": len(low_vix_jump),
        "avg_range_when_vix_down_2pct": round(low_vix_jump["nifty_full_range_pts"].mean(), 0)
            if len(low_vix_jump) else None,
        "n_days_vix_flat_first_hr": len(flat),
        "avg_range_when_vix_flat": round(flat["nifty_full_range_pts"].mean(), 0)
            if len(flat) else None,
    }


def main():
    REPORTS.mkdir(exist_ok=True)
    print("Loading minute data...")
    spot = load_minute(DATA / "NIFTY50_INDEX_1minute.csv")
    vix = load_minute(DATA / "INDIA_VIX_1minute.csv")
    print(f"  NIFTY: {len(spot):,} rows | VIX: {len(vix):,} rows\n")

    daily = daily_summary(spot, vix)
    daily.to_csv(REPORTS / "vix_nifty_daily.csv", index=False)
    hourly = hourly_pattern(spot, vix)
    hourly.to_csv(REPORTS / "vix_nifty_hourly_pattern.csv")
    regime = regime_analysis(daily)
    lag = lag_correlation(daily)
    first_hr = first_hour_predictor(spot, vix)

    print("=" * 70)
    print(f"Trading days: {len(daily)}")
    print(f"VIX overall:   min {daily['vix_close'].min():.2f}  "
          f"median {daily['vix_close'].median():.2f}  "
          f"max {daily['vix_close'].max():.2f}")
    print(f"NIFTY overall: min {daily['nifty_close'].min():.0f}  "
          f"median {daily['nifty_close'].median():.0f}  "
          f"max {daily['nifty_close'].max():.0f}")

    print(f"\n--- VIX Regime ---")
    print(regime.to_string())

    print(f"\n--- Hourly average pattern ---")
    print(hourly.round(2).to_string())

    print(f"\n--- Same-day correlation ---")
    print(f"VIX %chg vs NIFTY %chg same day: "
          f"{lag['same_day_vix_vs_nifty_corr']}")
    print(f"  (Strong negative = textbook fear-gauge behaviour)")
    print(f"VIX today vs NIFTY tomorrow: {lag['vix_today_vs_nifty_next_corr']}")

    print(f"\n--- First-hour VIX as predictor of full-day NIFTY range ---")
    print(f"Correlation: {first_hr['first_hour_vix_vs_full_range_corr']}")
    print(f"VIX UP >2% in first hour ({first_hr['n_days_vix_up_2pct_first_hr']} days): "
          f"avg NIFTY range = {first_hr['avg_range_when_vix_up_2pct']} pts")
    print(f"VIX DOWN >2% in first hour ({first_hr['n_days_vix_down_2pct_first_hr']} days): "
          f"avg NIFTY range = {first_hr['avg_range_when_vix_down_2pct']} pts")
    print(f"VIX FLAT first hour ({first_hr['n_days_vix_flat_first_hr']} days): "
          f"avg NIFTY range = {first_hr['avg_range_when_vix_flat']} pts")

    md = ["# India VIX vs NIFTY — Relationship Analysis\n",
          f"Trading days: **{len(daily)}**  "
          f"(spot {daily['date'].min()} -> {daily['date'].max()})",
          f"VIX range: **{daily['vix_close'].min():.2f} - "
          f"{daily['vix_close'].max():.2f}** (median {daily['vix_close'].median():.2f})",
          f"NIFTY range: **{int(daily['nifty_close'].min()):,} - "
          f"{int(daily['nifty_close'].max()):,}**",
          "",
          "## Same-day correlation\n",
          f"- VIX % change vs NIFTY % change (same day): **"
          f"{lag['same_day_vix_vs_nifty_corr']}**",
          f"  - Strong negative (-0.6 to -0.8 expected): textbook fear-gauge",
          f"  - Near zero: weakly related, day-to-day noise",
          f"- VIX today vs NIFTY tomorrow: {lag['vix_today_vs_nifty_next_corr']}",
          f"  - If significantly negative -> VIX leads NIFTY by 1 day",
          "",
          "## First-hour VIX as predictor of full-day range\n",
          f"- Correlation: **{first_hr['first_hour_vix_vs_full_range_corr']}**",
          f"- VIX UP >2% in first hour (n={first_hr['n_days_vix_up_2pct_first_hr']}): "
          f"avg full-day range = **{first_hr['avg_range_when_vix_up_2pct']} pts**",
          f"- VIX DOWN >2% in first hour (n={first_hr['n_days_vix_down_2pct_first_hr']}): "
          f"avg = **{first_hr['avg_range_when_vix_down_2pct']} pts**",
          f"- VIX FLAT first hour (n={first_hr['n_days_vix_flat_first_hr']}): "
          f"avg = **{first_hr['avg_range_when_vix_flat']} pts**",
          "",
          "## VIX regime breakdown\n```",
          regime.to_string(),
          "```",
          "",
          "## Hourly average pattern (across all days)\n```",
          hourly.round(2).to_string(),
          "```", ""]
    md_path = REPORTS / "vix_nifty_relationship_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {REPORTS}/vix_nifty_daily.csv")
    print(f"Wrote {REPORTS}/vix_nifty_hourly_pattern.csv")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
