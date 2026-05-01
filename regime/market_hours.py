"""
Pure market-hours helpers (Indian equities) — IST-aware.

Kept in its own module so unit tests can import it without pulling
production-only dependencies (Upstox client, DataFetcher, etc).
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import pytz

IST = pytz.timezone('Asia/Kolkata')

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
ENTRY_WINDOW_OPEN = dtime(10, 0)
FORCE_FLAT_TIME = dtime(14, 30)


def next_market_open(now_ist: datetime):
    """
    Return tz-aware IST datetime of the next 09:15. Returns None if
    the market is currently open (i.e. weekday and 09:15 <= time < 15:30).

    Handles weekends (Sat/Sun) by skipping forward to Monday 09:15.
    Caller is responsible for trading-holiday handling.
    """
    if now_ist.weekday() == 5:        # Saturday
        target_date = now_ist.date() + timedelta(days=2)
    elif now_ist.weekday() == 6:      # Sunday
        target_date = now_ist.date() + timedelta(days=1)
    else:
        t = now_ist.time()
        if MARKET_OPEN <= t < MARKET_CLOSE:
            return None
        if t < MARKET_OPEN:
            target_date = now_ist.date()
        else:
            tomorrow = now_ist + timedelta(days=1)
            while tomorrow.weekday() >= 5:
                tomorrow += timedelta(days=1)
            target_date = tomorrow.date()

    return IST.localize(datetime.combine(target_date, MARKET_OPEN))
