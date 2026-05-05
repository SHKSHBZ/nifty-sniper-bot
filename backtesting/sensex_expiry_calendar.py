"""Generate SENSEX weekly-expiry calendar for the last 2 years.

SENSEX weekly-expiry-day history (per SEBI / BSE notifications):

  * Up to 2024-11-19 : FRIDAY  (BSE original weekly expiry)
  * 2024-11-20 -> :   TUESDAY  (post SEBI "one weekly expiry per
                                exchange" rule)
  * 2025-09-01 -> :   THURSDAY (BSE moved when NSE took Tuesday)

If any of those cutovers don't match what you remember, edit the
PHASES dict below and re-run.

Holidays where the regular expiry day is a market holiday: BSE
shifts the expiry to the previous trading day. We list known
Indian-market holidays for 2024-2026 and shift accordingly.

Output: reports/sensex_expiry_calendar.csv
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from backtesting import expiry_gamma_hero as base


WINDOW_START = date(2024, 5, 5)
WINDOW_END = date(2026, 5, 5)

# (start_date_inclusive, weekday_index_0_mon_to_6_sun)
PHASES = [
    (date(2024, 1, 1),  4),  # Friday
    (date(2024, 11, 20), 1),  # Tuesday
    (date(2025, 9, 1),  3),  # Thursday
]

# Indian market holidays (NSE/BSE — same list). Source: NSE holiday
# list 2024 + 2025 + 2026.
HOLIDAYS = {
    # 2024
    date(2024, 1, 22),  # Ram temple
    date(2024, 1, 26),  # Republic Day
    date(2024, 3, 8),   # Mahashivratri
    date(2024, 3, 25),  # Holi
    date(2024, 3, 29),  # Good Friday
    date(2024, 4, 11),  # Eid-ul-Fitr
    date(2024, 4, 17),  # Ram Navami
    date(2024, 5, 1),   # May Day
    date(2024, 5, 20),  # Mumbai election
    date(2024, 6, 17),  # Bakri Eid
    date(2024, 7, 17),  # Muharram
    date(2024, 8, 15),  # Independence Day
    date(2024, 10, 2),  # Gandhi Jayanti
    date(2024, 11, 1),  # Diwali Laxmi Pujan (muhurat only)
    date(2024, 11, 15), # Guru Nanak
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 2, 26),  # Mahashivratri
    date(2025, 3, 14),  # Holi
    date(2025, 3, 31),  # Eid-ul-Fitr
    date(2025, 4, 10),  # Mahavir Jayanti
    date(2025, 4, 14),  # Ambedkar Jayanti
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 1),   # May Day
    date(2025, 8, 15),  # Independence Day
    date(2025, 8, 27),  # Ganesh Chaturthi
    date(2025, 10, 2),  # Gandhi Jayanti / Dussehra
    date(2025, 10, 21), # Diwali Laxmi (muhurat)
    date(2025, 10, 22), # Balipratipada
    date(2025, 11, 5),  # Guru Nanak
    date(2025, 12, 25), # Christmas
    # 2026
    date(2026, 1, 26),  # Republic Day
    date(2026, 2, 17),  # Mahashivratri
    date(2026, 3, 4),   # Holi
    date(2026, 3, 31),  # Eid-ul-Fitr
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 14),  # Ambedkar Jayanti
    date(2026, 5, 1),   # May Day
}


def expiry_weekday_for(d: date) -> int:
    """Return weekday index that's the regular expiry day on date d."""
    current = PHASES[0][1]
    for start, wd in PHASES:
        if d >= start:
            current = wd
        else:
            break
    return current


def previous_trading_day(d: date) -> date:
    """Walk back until we hit a non-holiday weekday."""
    cur = d - timedelta(days=1)
    while cur.weekday() >= 5 or cur in HOLIDAYS:
        cur -= timedelta(days=1)
    return cur


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    rows = []
    cur = WINDOW_START
    seen: set[date] = set()
    while cur <= WINDOW_END:
        wd_target = expiry_weekday_for(cur)
        if cur.weekday() == wd_target:
            shifted = False
            actual = cur
            if cur in HOLIDAYS:
                actual = previous_trading_day(cur)
                shifted = True
            if actual in seen:
                cur += timedelta(days=1)
                continue
            seen.add(actual)
            phase = "Friday" if wd_target == 4 else (
                "Tuesday" if wd_target == 1 else "Thursday")
            rows.append({
                "expiry_date": actual.strftime("%Y-%m-%d"),
                "weekday": actual.strftime("%A"),
                "phase_rule": f"{phase}-weekly",
                "shifted_from_holiday": shifted,
                "regular_day_was": cur.strftime("%Y-%m-%d") if shifted else "",
            })
        cur += timedelta(days=1)

    df = pd.DataFrame(rows)
    out = base.REPORTS_DIR / "sensex_expiry_calendar.csv"
    df.to_csv(out, index=False)

    print(f"Generated {len(df)} SENSEX expiries from "
          f"{WINDOW_START} to {WINDOW_END}\n")
    print(df.to_string(index=False))
    by_phase = df["phase_rule"].value_counts()
    print(f"\nPhases:\n{by_phase.to_string()}")
    n_shift = df["shifted_from_holiday"].sum()
    print(f"Holiday-shifted: {n_shift}")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
