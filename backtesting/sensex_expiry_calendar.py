"""Generate SENSEX weekly-expiry calendar for the last 2 years.

SENSEX weekly-expiry-day history (per BSE notifications, corrected):

  * Up to 2024-12-31 : FRIDAY  (BSE original weekly expiry)
  * 2025-01-01 -> :   TUESDAY  (BSE realignment after SEBI's
                                one-weekly-per-exchange rule)
  * 2025-09-01 -> :   THURSDAY (BSE moved to Thu when NSE took Tue)

Legacy exception: Friday 2025-01-03 was a one-off Friday expiry
(carry-over of a contract created before the rule change).

Holidays where the regular expiry day is a market holiday: BSE
shifts the expiry to the previous trading day.

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
    (date(2025, 1, 1),  1),  # Tuesday
    (date(2025, 9, 1),  3),  # Thursday
]

# One-off legacy contracts that kept their original expiry day after
# a phase change.
LEGACY_EXCEPTIONS = {date(2025, 1, 3): "Friday-legacy-carry-over"}

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
    date(2026, 1, 15),  # Local festival (per BSE 2026 calendar)
    date(2026, 1, 26),  # Republic Day
    date(2026, 2, 17),  # Mahashivratri
    date(2026, 3, 4),   # Holi
    date(2026, 3, 26),  # Eid-ul-Fitr observed
    date(2026, 3, 31),  # Eid-ul-Fitr
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 14),  # Ambedkar Jayanti
    date(2026, 5, 1),   # May Day
    date(2026, 5, 28),  # Bakri Eid
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
    # Inject legacy exceptions first
    for legacy_date, note in LEGACY_EXCEPTIONS.items():
        if WINDOW_START <= legacy_date <= WINDOW_END:
            rows.append({
                "expiry_date": legacy_date.strftime("%Y-%m-%d"),
                "weekday": legacy_date.strftime("%A"),
                "phase_rule": note,
                "shifted_from_holiday": False,
                "regular_day_was": "",
            })
            seen.add(legacy_date)

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
    rows.sort(key=lambda r: r["expiry_date"])

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
