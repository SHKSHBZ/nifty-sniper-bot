"""Sanity check on the expiry-day data we used."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtesting import expiry_gamma_hero as base


def check_one(expiry_token: str, expiry_date, spot_df: pd.DataFrame) -> dict:
    tz = spot_df.index.tz
    expiry_ts_open = pd.Timestamp.combine(expiry_date.date(),
                                          pd.Timestamp("09:15").time()).tz_localize(tz)
    expiry_ts_close = pd.Timestamp.combine(expiry_date.date(),
                                           pd.Timestamp("15:30").time()).tz_localize(tz)

    spot_e = base.get_value_at(spot_df, expiry_ts_open, "close")
    if spot_e is None:
        # Try later in the day
        for h, m in [(14, 50), (10, 0), (15, 0)]:
            ts = pd.Timestamp.combine(expiry_date.date(),
                                      pd.Timestamp(f"{h:02d}:{m:02d}").time()).tz_localize(tz)
            spot_e = base.get_value_at(spot_df, ts, "close")
            if spot_e is not None:
                break
    atm = int(round(spot_e / 50) * 50) if spot_e else None

    # Look for the ATM CE file
    ce_path = base.DATA_DIR / f"NIFTY_{atm}_CE_{expiry_token}_1min.csv"
    pe_path = base.DATA_DIR / f"NIFTY_{atm}_PE_{expiry_token}_1min.csv"

    info = {
        "expiry": expiry_token,
        "date": expiry_date.strftime("%Y-%m-%d"),
        "weekday": expiry_date.strftime("%A"),
        "spot_open": round(spot_e, 2) if spot_e else "",
        "atm": atm or "",
        "ce_file_exists": ce_path.exists(),
        "pe_file_exists": pe_path.exists(),
    }

    if ce_path.exists():
        df = pd.read_csv(ce_path)
        df["ts"] = pd.to_datetime(df["timestamp"])
        on_day = df[df["ts"].dt.date == expiry_date.date()]
        info["ce_rows_on_expiry_day"] = len(on_day)
        if len(on_day):
            info["ce_first_ts"] = on_day["ts"].iloc[0].strftime("%H:%M")
            info["ce_last_ts"] = on_day["ts"].iloc[-1].strftime("%H:%M")
            info["ce_close_min"] = float(on_day["close"].min())
            info["ce_close_max"] = float(on_day["close"].max())
            info["ce_oi_max"] = int(on_day["open_interest"].max())
            # Check for stale rows (consecutive identical prices)
            stale = (on_day["close"].diff() == 0).sum()
            info["ce_stale_minutes"] = int(stale)

    if pe_path.exists():
        df = pd.read_csv(pe_path)
        df["ts"] = pd.to_datetime(df["timestamp"])
        on_day = df[df["ts"].dt.date == expiry_date.date()]
        info["pe_rows_on_expiry_day"] = len(on_day)
        if len(on_day):
            info["pe_close_min"] = float(on_day["close"].min())
            info["pe_close_max"] = float(on_day["close"].max())
            info["pe_oi_max"] = int(on_day["open_interest"].max())

    return info


def main():
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = base.discover_expiries()

    rows = [check_one(t, d, spot_df) for t, d in expiries]
    df = pd.DataFrame(rows)
    out = base.REPORTS_DIR / "expiry_data_check.csv"
    df.to_csv(out, index=False)

    # Print a clean readout
    print(f"\n{'date':<12} {'day':<10} {'atm':>6} {'CE rows':>8} {'PE rows':>8} "
          f"{'CE prem range':<16} {'PE prem range':<16} {'CE OI':>10}")
    print("-" * 100)
    for r in rows:
        ce_range = f"{r.get('ce_close_min', '?')}-{r.get('ce_close_max', '?')}"
        pe_range = f"{r.get('pe_close_min', '?')}-{r.get('pe_close_max', '?')}"
        print(f"{r['date']:<12} {r['weekday']:<10} {str(r['atm']):>6} "
              f"{str(r.get('ce_rows_on_expiry_day','?')):>8} "
              f"{str(r.get('pe_rows_on_expiry_day','?')):>8} "
              f"{ce_range:<16} {pe_range:<16} "
              f"{r.get('ce_oi_max', '?'):>10}")

    weekdays = pd.Series([r["weekday"] for r in rows]).value_counts()
    print(f"\nWeekday distribution:\n{weekdays.to_string()}")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
