"""
JournalRecorder — captures executed trades, missed entries and events
during a paper-trading or backtest run.

Use:
    rec = JournalRecorder()
    rec.start_day(date(2026, 4, 21))

    # When a tactic enters:
    rec.on_entry(tactic, direction, strike, entry_ts, entry_premium,
                 sl_pct, tp_pct, time_stop_min, regime, entry_state)

    # On every subsequent bar while in position, append the option's path:
    rec.on_path_tick(option_ts, option_close, option_high, option_low)

    # When the trade exits:
    rec.on_exit(exit_ts, exit_premium, exit_reason)

    # When a tactic is BLOCKED by a single specific gate:
    rec.on_near_miss(tactic, direction, blocked_by, blocker_detail,
                     state_snapshot, hypothetical_strike,
                     hypothetical_entry_premium, hypothetical_path)

    # End of day:
    day_record = rec.end_day(realized_pnl, cumulative_pnl)

The recorder owns no business logic — it's a passive sink. The runner
or live bot decides WHEN to call it. Everything is in-memory until
`end_day()`, which returns a JournalDay you can persist or pass to
the report generator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from journal.models import JournalDay, ExecutedTrade, MissedEntry, JournalEntry


@dataclass
class _OpenTrade:
    tactic: str
    direction: str
    strike: int
    entry_ts: datetime
    entry_premium: float
    qty_lots: int
    sl_pct: float
    tp_pct: float
    time_stop_min: int
    regime_at_entry: str
    entry_state: dict
    path_ts: list[datetime] = field(default_factory=list)
    path_close: list[float] = field(default_factory=list)
    path_high: list[float] = field(default_factory=list)
    path_low: list[float] = field(default_factory=list)


class JournalRecorder:
    def __init__(self):
        self._day: Optional[JournalDay] = None
        self._open: dict[str, _OpenTrade] = {}   # tactic_name -> open trade

    # ----- lifecycle ----------------------------------------------------

    def start_day(self, d: date) -> None:
        self._day = JournalDay(day=d)
        self._open.clear()

    def end_day(self, realized_pnl: float, cumulative_pnl: float) -> JournalDay:
        if self._day is None:
            raise RuntimeError("start_day() must be called before end_day()")
        self._day.realized_pnl = realized_pnl
        self._day.cumulative_pnl_after_day = cumulative_pnl
        self._day.win_count = sum(1 for t in self._day.trades if t.net_pnl > 0)
        self._day.loss_count = sum(1 for t in self._day.trades if t.net_pnl <= 0)
        d = self._day
        self._day = None
        self._open.clear()
        return d

    # ----- trade lifecycle ----------------------------------------------

    def on_entry(
        self,
        tactic: str,
        direction: str,
        strike: int,
        entry_ts: datetime,
        entry_premium: float,
        qty_lots: int,
        sl_pct: float,
        tp_pct: float,
        time_stop_min: int,
        regime_at_entry: str,
        entry_state: dict,
    ) -> None:
        self._require_day()
        self._open[tactic] = _OpenTrade(
            tactic=tactic,
            direction=direction,
            strike=strike,
            entry_ts=entry_ts,
            entry_premium=entry_premium,
            qty_lots=qty_lots,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            time_stop_min=time_stop_min,
            regime_at_entry=regime_at_entry,
            entry_state=dict(entry_state),
        )

    def on_path_tick(
        self,
        tactic: str,
        ts: datetime,
        close: float,
        high: float,
        low: float,
    ) -> None:
        if tactic not in self._open:
            return
        ot = self._open[tactic]
        ot.path_ts.append(ts)
        ot.path_close.append(close)
        ot.path_high.append(high)
        ot.path_low.append(low)

    def on_exit(
        self,
        tactic: str,
        exit_ts: datetime,
        exit_premium: float,
        exit_reason: str,
        net_pnl: float,
    ) -> None:
        self._require_day()
        if tactic not in self._open:
            return
        ot = self._open.pop(tactic)
        trade = ExecutedTrade(
            tactic=ot.tactic,
            direction=ot.direction,
            strike=ot.strike,
            entry_ts=ot.entry_ts,
            entry_premium=ot.entry_premium,
            exit_ts=exit_ts,
            exit_premium=exit_premium,
            qty_lots=ot.qty_lots,
            sl_pct=ot.sl_pct,
            tp_pct=ot.tp_pct,
            time_stop_min=ot.time_stop_min,
            exit_reason=exit_reason,
            regime_at_entry=ot.regime_at_entry,
            net_pnl=net_pnl,
            entry_state=ot.entry_state,
            path_ts=ot.path_ts,
            path_close=ot.path_close,
            path_high=ot.path_high,
            path_low=ot.path_low,
        )
        self._day.trades.append(trade)

    # ----- near-miss ----------------------------------------------------

    def on_near_miss(
        self,
        tactic: str,
        direction: str,
        ts: datetime,
        blocked_by: str,
        blocker_detail: str,
        state_snapshot: dict,
        hypothetical_strike: int = 0,
        hypothetical_entry_premium: float = 0.0,
    ) -> MissedEntry:
        self._require_day()
        m = MissedEntry(
            ts=ts,
            tactic=tactic,
            direction=direction,
            blocked_by=blocked_by,
            blocker_detail=blocker_detail,
            state_snapshot=dict(state_snapshot),
            hypothetical_strike=hypothetical_strike,
            hypothetical_entry_premium=hypothetical_entry_premium,
        )
        self._day.missed.append(m)
        return m

    # ----- generic events ----------------------------------------------

    def on_event(self, ts: datetime, kind: str, message: str, **context) -> None:
        self._require_day()
        self._day.events.append(JournalEntry(
            ts=ts, kind=kind, message=message, context=context,
        ))

    # ----- helpers -----------------------------------------------------

    def is_in_position(self, tactic: str) -> bool:
        return tactic in self._open

    def _require_day(self) -> None:
        if self._day is None:
            raise RuntimeError("start_day() must be called first")
