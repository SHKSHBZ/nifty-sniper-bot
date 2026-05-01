"""
Unit tests for journal.missed_tracker.LiveMissedTracker.

The tracker is the live counterpart of the backtest near-miss harness:
when the dispatcher reports a near-miss, the tracker records the would-be
entry premium, polls the option's price for the tactic-prescribed window,
and finalises the MissedEntry with TP/SL/time-stop classification.

These tests use a programmable FakeFetcher so the tracker can be driven
deterministically without any live API calls.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from journal.recorder import JournalRecorder  # noqa: E402
from journal.missed_tracker import LiveMissedTracker  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class FakeFetcher:
    """LTP returns whatever the script tells it to.

    Configure:
        f.set_ltp(strike, opt_type, value)
        f.set_path(strike, opt_type, [(idx, value), ...]) — optional time series

    Each get_option_ltp call advances the index.
    """

    def __init__(self, default: float = 100.0):
        self._values: dict[tuple[int, str], float] = {}
        self._paths: dict[tuple[int, str], list[float]] = {}
        self._idx: dict[tuple[int, str], int] = {}
        self.default = default

    def set_ltp(self, strike: int, opt_type: str, value: float) -> None:
        self._values[(int(strike), opt_type)] = float(value)

    def set_path(self, strike: int, opt_type: str, path: list[float]) -> None:
        self._paths[(int(strike), opt_type)] = list(path)
        self._idx[(int(strike), opt_type)] = 0

    def get_option_ltp(self, strike, opt_type):
        key = (int(strike), opt_type)
        if key in self._paths:
            i = self._idx[key]
            path = self._paths[key]
            v = path[min(i, len(path) - 1)]
            self._idx[key] = i + 1
            return float(v)
        return float(self._values.get(key, self.default))


def _make_tracker(**kwargs):
    rec = JournalRecorder()
    rec.start_day(date(2026, 4, 21))
    fetcher = kwargs.pop("fetcher", FakeFetcher())
    tracker = LiveMissedTracker(rec, fetcher, **kwargs)
    return tracker, rec, fetcher


def _register(tracker, ts: datetime, *,
              tactic="trend_pullback", direction="CE", strike=24800,
              blocker="oi_bias_ratio", sl=0.30, tp=0.50, t=90):
    return tracker.register_near_miss(
        tactic_name=tactic, direction=direction, ts=ts,
        blocked_by=blocker,
        blocker_detail=f"{blocker} value=0.83 threshold=0.85",
        state_snapshot={"spot": 24820},
        hypothetical_strike=strike,
        sl_pct=sl, tp_pct=tp, time_stop_min=t,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_basic_register_creates_pending_and_journal_entry(self):
        tracker, rec, fetcher = _make_tracker()
        fetcher.set_ltp(24800, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        fu = _register(tracker, ts)
        assert fu is not None
        assert tracker.pending_count() == 1
        assert len(rec._day.missed) == 1
        m = rec._day.missed[0]
        assert m.tactic == "trend_pullback"
        assert m.hypothetical_strike == 24800
        assert m.hypothetical_entry_premium == 100.0
        assert m.sl_pct == 0.30
        assert m.tp_pct == 0.50
        assert m.time_stop_min == 90

    def test_register_skipped_when_ltp_zero(self):
        tracker, rec, fetcher = _make_tracker()
        # Default + no override = 0
        fetcher.default = 0.0
        ts = datetime(2026, 4, 21, 11, 0)
        fu = _register(tracker, ts)
        assert fu is None
        assert tracker.pending_count() == 0
        assert len(rec._day.missed) == 0

    def test_dedup_within_window_drops_second_register(self):
        tracker, rec, fetcher = _make_tracker(dedup_window_min=5)
        fetcher.set_ltp(24800, "CE", 100.0)
        ts1 = datetime(2026, 4, 21, 11, 0)
        ts2 = ts1 + timedelta(minutes=4)
        ts3 = ts1 + timedelta(minutes=6)
        assert _register(tracker, ts1) is not None
        assert _register(tracker, ts2) is None      # within dedup window
        assert _register(tracker, ts3) is not None  # after window

    def test_dedup_keys_independent_per_blocker_or_strike(self):
        tracker, rec, fetcher = _make_tracker()
        fetcher.set_ltp(24800, "CE", 100.0)
        fetcher.set_ltp(24850, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        # Same tactic+direction+strike+blocker -> dedup
        assert _register(tracker, ts, blocker="g1") is not None
        assert _register(tracker, ts, blocker="g1") is None
        # Different blocker -> allowed
        assert _register(tracker, ts, blocker="g2") is not None
        # Different strike -> allowed
        assert _register(tracker, ts, blocker="g1", strike=24850) is not None

    def test_max_pending_drops_overflow(self):
        tracker, rec, fetcher = _make_tracker(max_pending=2)
        for s in (24800, 24850, 24900):
            fetcher.set_ltp(s, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        assert _register(tracker, ts, strike=24800) is not None
        assert _register(tracker, ts, strike=24850) is not None
        assert _register(tracker, ts, strike=24900) is None  # full
        assert tracker.pending_count() == 2


class TestTickFinalisation:
    def test_premium_through_tp_finalises_win(self):
        tracker, rec, fetcher = _make_tracker(poll_interval_sec=60)
        # Entry @ 100. TP at +50% = 150. Path drives to 200.
        fetcher.set_ltp(24800, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        _register(tracker, ts)

        # Switch fetcher to a path: 110, 130, 200
        fetcher.set_path(24800, "CE", [110.0, 130.0, 200.0])
        for i, t in enumerate([
            ts + timedelta(minutes=1), ts + timedelta(minutes=2),
            ts + timedelta(minutes=3),
        ]):
            tracker.tick(t)
            if rec._day.missed[0].hypothetical_outcome:
                break

        m = rec._day.missed[0]
        assert m.hypothetical_outcome == "WIN"
        assert m.hypothetical_pnl > 0
        # Used tactic-prescribed sl/tp
        assert "TP" in m.hypothetical_explanation

    def test_premium_through_sl_finalises_loss(self):
        tracker, rec, fetcher = _make_tracker(poll_interval_sec=60)
        fetcher.set_ltp(24800, "PE", 200.0)
        ts = datetime(2026, 4, 21, 11, 0)
        _register(tracker, ts, direction="PE")

        # SL = 200 * (1-0.30) = 140. Drive premium below.
        fetcher.set_path(24800, "PE", [180.0, 150.0, 130.0])
        for i in range(3):
            tracker.tick(ts + timedelta(minutes=i + 1))
        m = rec._day.missed[0]
        assert m.hypothetical_outcome == "LOSS"
        assert m.hypothetical_pnl < 0
        assert "SL" in m.hypothetical_explanation

    def test_time_stop_finalises_at_deadline(self):
        tracker, rec, fetcher = _make_tracker(poll_interval_sec=60)
        fetcher.set_ltp(24800, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        _register(tracker, ts, t=10)  # 10-min time stop

        # Hover near 110 — never hits TP=150 / SL=70
        fetcher.set_path(24800, "CE", [105.0] * 20)
        # Tick at minutes 1..9 then deadline at 10
        for i in range(1, 11):
            tracker.tick(ts + timedelta(minutes=i))
        m = rec._day.missed[0]
        assert m.hypothetical_outcome in ("WIN", "LOSS", "BREAKEVEN")
        # Path size includes seed entry + intermediate polls
        assert len(rec._day.missed) == 1
        assert tracker.pending_count() == 0

    def test_zero_ltp_during_tick_skipped_no_append(self):
        tracker, rec, fetcher = _make_tracker(poll_interval_sec=60)
        fetcher.set_ltp(24800, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        fu = _register(tracker, ts)
        # Now path returns 0 — should skip
        fetcher.set_path(24800, "CE", [0.0, 0.0])
        tracker.tick(ts + timedelta(minutes=1))
        # No new path tick should be appended
        assert len(fu.path) == 1   # only the seed entry

    def test_poll_interval_throttles(self):
        tracker, rec, fetcher = _make_tracker(poll_interval_sec=60)
        fetcher.set_ltp(24800, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        fu = _register(tracker, ts)

        fetcher.set_path(24800, "CE", [110.0, 120.0, 130.0])
        # First tick at +30s should NOT poll (interval is 60s)
        tracker.tick(ts + timedelta(seconds=30))
        assert len(fu.path) == 1
        tracker.tick(ts + timedelta(seconds=70))
        assert len(fu.path) == 2


class TestFlush:
    def test_flush_finalises_pending(self):
        tracker, rec, fetcher = _make_tracker()
        fetcher.set_ltp(24800, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        _register(tracker, ts)
        # No ticks ran — entry still pending
        n = tracker.flush_all(ts + timedelta(minutes=2))
        assert n == 1
        m = rec._day.missed[0]
        assert m.hypothetical_outcome   # something assigned

    def test_flush_is_idempotent(self):
        tracker, rec, fetcher = _make_tracker()
        fetcher.set_ltp(24800, "CE", 100.0)
        ts = datetime(2026, 4, 21, 11, 0)
        _register(tracker, ts)
        tracker.flush_all(ts + timedelta(minutes=5))
        # Second flush is a no-op
        n = tracker.flush_all(ts + timedelta(minutes=10))
        assert n == 0


class TestSafety:
    def test_register_swallows_internal_exceptions(self):
        class ExplodingFetcher:
            def get_option_ltp(self, strike, opt_type):
                raise RuntimeError("API down")
        rec = JournalRecorder(); rec.start_day(date(2026, 4, 21))
        tracker = LiveMissedTracker(rec, ExplodingFetcher())
        # Should not raise — returns None
        out = _register(tracker, datetime(2026, 4, 21, 11, 0))
        assert out is None
        assert tracker.pending_count() == 0

    def test_tick_swallows_fetcher_errors(self):
        class FlakyFetcher:
            def __init__(self):
                self.calls = 0
            def get_option_ltp(self, strike, opt_type):
                self.calls += 1
                if self.calls == 1:
                    return 100.0   # let register succeed
                raise RuntimeError("flaky")
        rec = JournalRecorder(); rec.start_day(date(2026, 4, 21))
        tracker = LiveMissedTracker(rec, FlakyFetcher(), poll_interval_sec=60)
        ts = datetime(2026, 4, 21, 11, 0)
        _register(tracker, ts)
        # Tick should NOT propagate the exception
        tracker.tick(ts + timedelta(minutes=2))
        # Still pending (no usable LTP), no crash
        assert tracker.pending_count() == 1
