"""
Journal data model.

Three kinds of records per trading day:

  - ExecutedTrade   a position that was opened and closed
  - MissedEntry     a near-miss: most gates passed but one blocked it
  - JournalEntry    a generic event with timestamp + context

A JournalDay aggregates all of these for one calendar day.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any, Optional


@dataclass
class ExecutedTrade:
    """A trade that was opened and closed during paper/live operation."""
    tactic: str
    direction: str                  # "CE" or "PE"
    strike: int
    entry_ts: datetime
    entry_premium: float
    exit_ts: datetime
    exit_premium: float
    qty_lots: int
    sl_pct: float
    tp_pct: float
    time_stop_min: int
    exit_reason: str               # "TP" / "SL" / "TIME_STOP" / "EOD"
    regime_at_entry: str
    net_pnl: float

    # Snapshot of the market state at entry — used by the analyzer to
    # explain WHY the tactic fired.
    entry_state: dict = field(default_factory=dict)

    # Captured price path post-entry (option close prices) — used to
    # compute counterfactual / optimal exits.
    path_ts: list[datetime] = field(default_factory=list)
    path_close: list[float] = field(default_factory=list)
    path_high: list[float] = field(default_factory=list)
    path_low: list[float] = field(default_factory=list)

    # Filled in by analyzer
    entry_reason: str = ""
    exit_reason_text: str = ""
    win_lose_explanation: str = ""
    optimal_exit_premium: float = 0.0
    optimal_exit_ts: Optional[datetime] = None
    captured_pct_of_optimum: float = 0.0
    counterfactual_notes: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class MissedEntry:
    """
    A near-miss: the tactic was 'almost going to fire' but one specific
    gate blocked it. Used to identify whether a slightly different
    threshold would have unlocked profitable trades.
    """
    ts: datetime
    tactic: str
    direction: str                 # would-be CE / PE
    blocked_by: str                # the gate that failed
    blocker_detail: str            # human-readable: "VIX 18.4 > ceiling 18.0"
    state_snapshot: dict = field(default_factory=dict)

    # What would have happened if we had taken the trade?
    hypothetical_strike: int = 0
    hypothetical_entry_premium: float = 0.0
    hypothetical_exit_premium: float = 0.0
    hypothetical_pnl: float = 0.0
    hypothetical_outcome: str = ""    # "WIN" / "LOSS" / "BREAKEVEN" / "UNKNOWN"
    hypothetical_explanation: str = ""


@dataclass
class JournalEntry:
    """Generic timestamped event (info, warning, halt, etc.)."""
    ts: datetime
    kind: str                      # "info" / "halt" / "regime_change" / etc.
    message: str
    context: dict = field(default_factory=dict)


@dataclass
class JournalDay:
    """All journal data for one trading day."""
    day: date
    trades: list[ExecutedTrade] = field(default_factory=list)
    missed: list[MissedEntry] = field(default_factory=list)
    events: list[JournalEntry] = field(default_factory=list)

    # End-of-day summary
    realized_pnl: float = 0.0
    cumulative_pnl_after_day: float = 0.0
    win_count: int = 0
    loss_count: int = 0


# ---------------------------------------------------------------------------
# JSON helpers — for persisting the journal to disk
# ---------------------------------------------------------------------------

def _default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {type(obj)}")


def trade_to_jsonable(t: ExecutedTrade) -> dict:
    d = asdict(t)
    d["entry_ts"] = t.entry_ts.isoformat()
    d["exit_ts"] = t.exit_ts.isoformat()
    d["path_ts"] = [ts.isoformat() if isinstance(ts, datetime) else str(ts)
                    for ts in t.path_ts]
    if t.optimal_exit_ts:
        d["optimal_exit_ts"] = t.optimal_exit_ts.isoformat()
    return d


def missed_to_jsonable(m: MissedEntry) -> dict:
    d = asdict(m)
    d["ts"] = m.ts.isoformat()
    return d


def write_journal_json(day: JournalDay, path) -> None:
    payload = {
        "day": day.day.isoformat(),
        "realized_pnl": day.realized_pnl,
        "cumulative_pnl_after_day": day.cumulative_pnl_after_day,
        "win_count": day.win_count,
        "loss_count": day.loss_count,
        "trades": [trade_to_jsonable(t) for t in day.trades],
        "missed": [missed_to_jsonable(m) for m in day.missed],
        "events": [
            {"ts": e.ts.isoformat(), "kind": e.kind,
             "message": e.message, "context": e.context}
            for e in day.events
        ],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, default=_default, indent=2)
