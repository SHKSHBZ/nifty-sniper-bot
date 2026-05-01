"""
LiveMissedTracker — owns the lifecycle of in-flight near-miss follow-ups
during a live (or replayed) trading session.

When the dispatcher reports a near-miss (a tactic that was one gate away
from firing), the live bot calls `register_near_miss(...)`. The tracker:

  1. Records the would-be entry premium via DataFetcher.
  2. Stores a `PendingFollowUp` keyed off the blocked direction + strike.
  3. On every subsequent main-loop tick, polls the option's premium once
     per minute and appends to the path.
  4. When the path triggers TP / SL / time-stop, finalises the
     `MissedEntry` via `journal.analyzer.analyze_missed`.
  5. At end of day, `flush_all` finalises any still-open follow-ups
     using whatever path was collected.

Design constraints:
  - Single-threaded, no asyncio. Just a list + a tick() called from main.
  - Wrapped in try/except at every public boundary; never raises.
  - Bounded queue (max_pending) — drops new registrations if overloaded.
  - Dedup: same (tactic, direction, blocker, strike) within a sliding
    window is silently ignored to avoid flooding from repeated bars.
  - Zero side-effects on portfolio / orders / Telegram.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from journal.models import MissedEntry
from journal.analyzer import analyze_missed

log = logging.getLogger("missed_tracker")


@dataclass
class PendingFollowUp:
    entry: MissedEntry
    tactic_name: str
    strike: int
    direction: str                  # "CE" or "PE"
    sl_pct: float
    tp_pct: float
    time_stop_min: int
    registered_at: datetime
    deadline: datetime
    last_poll_ts: Optional[datetime] = None
    path: list[tuple[datetime, float]] = field(default_factory=list)
    settled: bool = False


class LiveMissedTracker:
    """
    Args:
      recorder: a JournalRecorder instance — the tracker uses
        recorder.on_near_miss to attach the entry into today's journal.
      fetcher: any object exposing get_option_ltp(strike, opt_type) -> float.
      max_pending: cap on simultaneously-tracked follow-ups (default 10).
      dedup_window_min: dedup horizon for (tactic, direction, blocker, strike).
      poll_interval_sec: minimum gap between premium polls (default 60s).
      lot_size: lot size used for hypothetical P&L (default 75).
      brokerage: per-trade brokerage to subtract (default Rs 60).
    """

    def __init__(
        self,
        recorder,
        fetcher,
        *,
        max_pending: int = 10,
        dedup_window_min: int = 5,
        poll_interval_sec: int = 60,
        lot_size: int = 75,
        brokerage: float = 60.0,
    ):
        self.recorder = recorder
        self.fetcher = fetcher
        self.max_pending = max_pending
        self.dedup_window = timedelta(minutes=dedup_window_min)
        self.poll_interval = timedelta(seconds=poll_interval_sec)
        self.lot_size = lot_size
        self.brokerage = brokerage

        self._pending: list[PendingFollowUp] = []
        # keyed by (tactic, direction, blocker, strike) -> last register ts
        self._recent_registers: dict[tuple, datetime] = {}

    # ----- public API ---------------------------------------------------

    def register_near_miss(
        self,
        *,
        tactic_name: str,
        direction: str,
        ts: datetime,
        blocked_by: str,
        blocker_detail: str,
        state_snapshot: dict,
        hypothetical_strike: int,
        sl_pct: float,
        tp_pct: float,
        time_stop_min: int,
    ) -> Optional[PendingFollowUp]:
        """Register a new near-miss for live follow-up. Returns None when
        deduped, queue full, or premium lookup failed."""
        try:
            return self._register_inner(
                tactic_name=tactic_name, direction=direction, ts=ts,
                blocked_by=blocked_by, blocker_detail=blocker_detail,
                state_snapshot=state_snapshot,
                hypothetical_strike=hypothetical_strike,
                sl_pct=sl_pct, tp_pct=tp_pct, time_stop_min=time_stop_min,
            )
        except Exception as e:
            log.warning("register_near_miss failed: %s", e)
            return None

    def tick(self, now: datetime) -> None:
        """Poll all pending follow-ups; finalise any that hit TP/SL/time."""
        try:
            self._tick_inner(now)
        except Exception as e:
            log.warning("missed-tracker tick failed: %s", e)

    def flush_all(self, now: datetime) -> int:
        """Finalise every still-pending follow-up. Returns count finalised."""
        try:
            return self._flush_inner(now)
        except Exception as e:
            log.warning("missed-tracker flush failed: %s", e)
            return 0

    def pending_count(self) -> int:
        return sum(1 for p in self._pending if not p.settled)

    # ----- internals ----------------------------------------------------

    def _register_inner(
        self, *, tactic_name, direction, ts, blocked_by, blocker_detail,
        state_snapshot, hypothetical_strike, sl_pct, tp_pct, time_stop_min,
    ) -> Optional[PendingFollowUp]:
        # 0) dedup
        key = (tactic_name, direction, blocked_by, int(hypothetical_strike))
        last = self._recent_registers.get(key)
        if last is not None and (ts - last) < self.dedup_window:
            return None
        # Prune stale dedup keys to keep the dict bounded
        if len(self._recent_registers) > 256:
            cutoff = ts - self.dedup_window
            self._recent_registers = {
                k: v for k, v in self._recent_registers.items() if v >= cutoff
            }

        # 1) capacity check (only count *unsettled* follow-ups)
        if self.pending_count() >= self.max_pending:
            log.warning(
                "missed-tracker queue full (%d pending) — dropping %s %s blocked by %s",
                self.max_pending, tactic_name, direction, blocked_by,
            )
            return None

        # 2) seed entry premium (cached LTP — no extra API call)
        entry_prem = 0.0
        try:
            entry_prem = float(self.fetcher.get_option_ltp(
                hypothetical_strike, direction,
            ) or 0.0)
        except Exception as e:
            log.debug("get_option_ltp failed at register: %s", e)
            entry_prem = 0.0
        if entry_prem <= 0:
            log.info(
                "missed-tracker: skipping register — no LTP for %s %s %s",
                tactic_name, hypothetical_strike, direction,
            )
            return None

        # 3) attach to journal
        entry = self.recorder.on_near_miss(
            tactic=tactic_name,
            direction=direction,
            ts=ts,
            blocked_by=blocked_by,
            blocker_detail=blocker_detail,
            state_snapshot=state_snapshot,
            hypothetical_strike=int(hypothetical_strike),
            hypothetical_entry_premium=entry_prem,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            time_stop_min=time_stop_min,
        )

        # 4) queue follow-up. Seed last_poll_ts = ts so the poll-interval
        # clock starts from registration; register itself counts as the
        # first poll (we already have an LTP).
        deadline = ts + timedelta(minutes=time_stop_min)
        fu = PendingFollowUp(
            entry=entry,
            tactic_name=tactic_name,
            strike=int(hypothetical_strike),
            direction=direction,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            time_stop_min=time_stop_min,
            registered_at=ts,
            deadline=deadline,
            last_poll_ts=ts,
            path=[(ts, entry_prem)],
        )
        self._pending.append(fu)
        self._recent_registers[key] = ts
        log.info(
            "missed-tracker: registered %s %s blocked by %s (strike=%d, entry=Rs.%.1f, deadline=%s)",
            tactic_name, direction, blocked_by, fu.strike, entry_prem,
            deadline.strftime("%H:%M"),
        )
        return fu

    def _tick_inner(self, now: datetime) -> None:
        for fu in self._pending:
            if fu.settled:
                continue

            # Time-stop reached?
            if now >= fu.deadline:
                self._finalise(fu, reason="time_stop")
                continue

            # Poll due?
            if fu.last_poll_ts is not None and \
               (now - fu.last_poll_ts) < self.poll_interval:
                continue

            ltp = 0.0
            try:
                ltp = float(self.fetcher.get_option_ltp(
                    fu.strike, fu.direction,
                ) or 0.0)
            except Exception as e:
                log.debug("get_option_ltp failed during tick: %s", e)
                ltp = 0.0

            if ltp <= 0:
                # Skip this tick but advance last_poll_ts so we don't
                # hammer in a tight loop.
                fu.last_poll_ts = now
                continue

            fu.path.append((now, ltp))
            fu.last_poll_ts = now

            # TP / SL trigger check (premium-relative)
            entry = fu.path[0][1]
            tp = entry * (1 + fu.tp_pct)
            sl = entry * (1 - fu.sl_pct)
            if ltp >= tp:
                self._finalise(fu, reason="tp")
            elif ltp <= sl:
                self._finalise(fu, reason="sl")

    def _flush_inner(self, now: datetime) -> int:
        n = 0
        for fu in self._pending:
            if fu.settled:
                continue
            self._finalise(fu, reason="eod")
            n += 1
        return n

    def _finalise(self, fu: PendingFollowUp, *, reason: str) -> None:
        try:
            analyze_missed(
                fu.entry,
                list(fu.path),
                sl_pct=fu.sl_pct,
                tp_pct=fu.tp_pct,
                time_stop_min=fu.time_stop_min,
                lot_size=self.lot_size,
                brokerage=self.brokerage,
            )
        except Exception as e:
            log.warning("missed-tracker finalise analyze_missed failed: %s", e)
        fu.settled = True
        outcome = fu.entry.hypothetical_outcome or "UNKNOWN"
        pnl = fu.entry.hypothetical_pnl
        log.info(
            "missed-tracker: finalised %s %s [%s] -> %s Rs.%+,.0f (polls=%d)",
            fu.tactic_name, fu.direction, reason.upper(), outcome, pnl,
            len(fu.path),
        )
