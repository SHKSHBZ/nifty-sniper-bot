"""
Unit tests for the market-time gating logic in main.py.

We test only the pure helper `_next_market_open` (a static method) so
the tests don't need to instantiate LiveOrchestrator or hit Upstox.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.market_hours import next_market_open  # noqa: E402

IST = pytz.timezone('Asia/Kolkata')


def _ist(year, month, day, hour, minute=0, second=0) -> datetime:
    return IST.localize(datetime(year, month, day, hour, minute, second))


# --- weekday cases ---------------------------------------------------

def test_pre_market_returns_today_915():
    # Monday 6 AM IST -> waits until Monday 9:15 IST
    now = _ist(2026, 5, 4, 6, 0)        # Mon
    target = next_market_open(now)
    assert target.weekday() == 0
    assert target.hour == 9 and target.minute == 15
    assert target.date() == now.date()


def test_inside_session_returns_none():
    # Tuesday 11:30 IST is mid-session
    now = _ist(2026, 5, 5, 11, 30)
    assert next_market_open(now) is None


def test_at_open_boundary_returns_none():
    # 09:15:00 IST exactly is open
    now = _ist(2026, 5, 5, 9, 15)
    assert next_market_open(now) is None


def test_at_close_boundary_returns_next_open():
    # 15:30:00 is closed; should target tomorrow 9:15
    now = _ist(2026, 5, 5, 15, 30)        # Tue close
    target = next_market_open(now)
    assert target is not None
    assert target.weekday() == 2          # Wed
    assert target.hour == 9 and target.minute == 15


def test_post_market_returns_next_day():
    # Wed 4 PM -> Thu 9:15
    now = _ist(2026, 5, 6, 16, 0)
    target = next_market_open(now)
    assert target.weekday() == 3
    assert target.hour == 9 and target.minute == 15


# --- weekend cases ---------------------------------------------------

def test_friday_post_market_skips_weekend():
    # Fri 4 PM -> Mon 9:15
    now = _ist(2026, 5, 8, 16, 0)         # Fri
    target = next_market_open(now)
    assert target.weekday() == 0           # Monday
    assert target.hour == 9 and target.minute == 15


def test_saturday_morning_waits_until_monday():
    now = _ist(2026, 5, 9, 8, 0)          # Sat 8 AM
    target = next_market_open(now)
    assert target.weekday() == 0           # Monday
    assert target.hour == 9 and target.minute == 15


def test_sunday_evening_waits_until_monday():
    now = _ist(2026, 5, 10, 22, 0)        # Sun 10 PM
    target = next_market_open(now)
    assert target.weekday() == 0           # Monday
    assert target.hour == 9 and target.minute == 15


def test_thursday_evening_jumps_to_friday():
    # Thursday after close should NOT skip weekend (Friday is a trading day)
    now = _ist(2026, 5, 7, 16, 0)         # Thu 4 PM
    target = next_market_open(now)
    assert target.weekday() == 4           # Friday


# --- timezone independence ------------------------------------------

def test_returns_ist_aware_datetime():
    now = _ist(2026, 5, 4, 6, 0)
    target = next_market_open(now)
    assert target.tzinfo is not None
    # Compare offsets — IST is +05:30
    assert target.utcoffset().total_seconds() == 19800
