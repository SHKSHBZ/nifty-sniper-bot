"""
Daily journal report — CSV outputs (one per section).

For each trading day, writes the following files into the output dir:

    journal_YYYY-MM-DD_summary.csv  — one row of P&L / win-loss totals
    journal_YYYY-MM-DD_trades.csv   — one row per executed trade
    journal_YYYY-MM-DD_missed.csv   — one row per near-miss
    journal_YYYY-MM-DD_events.csv   — one row per JournalEntry event

CSV is chosen over Markdown so the operator can pivot / filter in Excel.
Multi-line narrative fields (entry_reason, win_lose_explanation,
counterfactual_notes, suggestions) are still included; Excel handles
quoted multi-line cells natively.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from journal.models import JournalDay, ExecutedTrade, MissedEntry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_daily_report(day: JournalDay, output_dir: Path) -> Path:
    """Write the day's journal as a set of CSV files. Returns the path of
    the trades CSV (the primary artifact). Empty sections still get a
    header-only CSV so downstream tooling has a stable file layout."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = f"journal_{day.day.isoformat()}"

    summary_path = output_dir / f"{base}_summary.csv"
    trades_path = output_dir / f"{base}_trades.csv"
    missed_path = output_dir / f"{base}_missed.csv"
    events_path = output_dir / f"{base}_events.csv"

    _write_csv(summary_path, _SUMMARY_COLS, [_summary_row(day)])
    _write_csv(trades_path, _TRADE_COLS,
               [_trade_row(day, t) for t in day.trades])
    _write_csv(missed_path, _MISSED_COLS,
               [_missed_row(day, m) for m in day.missed])
    _write_csv(events_path, _EVENT_COLS,
               [_event_row(day, e) for e in day.events])
    return trades_path


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

_SUMMARY_COLS = (
    "date", "weekday",
    "n_trades", "win_count", "loss_count",
    "realized_pnl", "cumulative_pnl_after_day",
    "n_missed", "n_events",
)

_TRADE_COLS = (
    "date", "tactic", "direction", "strike", "qty_lots",
    "entry_ts", "entry_premium",
    "exit_ts", "exit_premium",
    "sl_pct", "tp_pct", "time_stop_min",
    "exit_reason", "regime_at_entry",
    "net_pnl", "outcome",
    "optimal_exit_premium", "optimal_exit_ts", "captured_pct_of_optimum",
    "entry_reason", "exit_reason_text", "win_lose_explanation",
    "counterfactual_notes", "suggestions",
    "entry_state_json",
)

_MISSED_COLS = (
    "date", "ts", "tactic", "direction",
    "blocked_by", "blocker_detail",
    "hypothetical_strike",
    "hypothetical_entry_premium", "hypothetical_exit_premium",
    "hypothetical_pnl", "hypothetical_outcome", "hypothetical_explanation",
    "sl_pct", "tp_pct", "time_stop_min", "poll_count",
    "regime", "spot", "vix_level", "focus_pcr",
    "state_snapshot_json",
)

_EVENT_COLS = (
    "date", "ts", "kind", "message", "context_json",
)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _summary_row(day: JournalDay) -> dict:
    return {
        "date": day.day.isoformat(),
        "weekday": day.day.strftime("%a"),
        "n_trades": len(day.trades),
        "win_count": day.win_count,
        "loss_count": day.loss_count,
        "realized_pnl": _money(day.realized_pnl),
        "cumulative_pnl_after_day": _money(day.cumulative_pnl_after_day),
        "n_missed": len(day.missed),
        "n_events": len(day.events),
    }


def _trade_row(day: JournalDay, t: ExecutedTrade) -> dict:
    outcome = "WIN" if t.net_pnl > 0 else ("LOSS" if t.net_pnl < 0 else "FLAT")
    return {
        "date": day.day.isoformat(),
        "tactic": t.tactic,
        "direction": t.direction,
        "strike": t.strike,
        "qty_lots": t.qty_lots,
        "entry_ts": _iso(t.entry_ts),
        "entry_premium": _money(t.entry_premium),
        "exit_ts": _iso(t.exit_ts),
        "exit_premium": _money(t.exit_premium),
        "sl_pct": t.sl_pct,
        "tp_pct": t.tp_pct,
        "time_stop_min": t.time_stop_min,
        "exit_reason": t.exit_reason,
        "regime_at_entry": t.regime_at_entry,
        "net_pnl": _money(t.net_pnl),
        "outcome": outcome,
        "optimal_exit_premium": _money(t.optimal_exit_premium),
        "optimal_exit_ts": _iso(t.optimal_exit_ts) if t.optimal_exit_ts else "",
        "captured_pct_of_optimum": round(t.captured_pct_of_optimum, 2),
        "entry_reason": t.entry_reason,
        "exit_reason_text": t.exit_reason_text,
        "win_lose_explanation": t.win_lose_explanation,
        "counterfactual_notes": " | ".join(
            n.strip().lstrip("- ") for n in t.counterfactual_notes
        ),
        "suggestions": " | ".join(s.strip() for s in t.suggestions),
        "entry_state_json": json.dumps(t.entry_state, default=str),
    }


def _missed_row(day: JournalDay, m: MissedEntry) -> dict:
    state = m.state_snapshot or {}
    return {
        "date": day.day.isoformat(),
        "ts": _iso(m.ts),
        "tactic": m.tactic,
        "direction": m.direction,
        "blocked_by": m.blocked_by,
        "blocker_detail": m.blocker_detail,
        "hypothetical_strike": m.hypothetical_strike,
        "hypothetical_entry_premium": _money(m.hypothetical_entry_premium),
        "hypothetical_exit_premium": _money(m.hypothetical_exit_premium),
        "hypothetical_pnl": _money(m.hypothetical_pnl),
        "hypothetical_outcome": m.hypothetical_outcome,
        "hypothetical_explanation": m.hypothetical_explanation,
        "sl_pct": m.sl_pct,
        "tp_pct": m.tp_pct,
        "time_stop_min": m.time_stop_min,
        "poll_count": m.poll_count,
        "regime": state.get("regime", ""),
        "spot": state.get("spot", ""),
        "vix_level": state.get("vix_level", ""),
        "focus_pcr": state.get("focus_pcr", ""),
        "state_snapshot_json": json.dumps(state, default=str),
    }


def _event_row(day: JournalDay, e) -> dict:
    return {
        "date": day.day.isoformat(),
        "ts": _iso(e.ts),
        "kind": e.kind,
        "message": e.message,
        "context_json": json.dumps(e.context or {}, default=str),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, columns: Iterable[str], rows: list[dict]) -> None:
    cols = list(columns)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _iso(ts) -> str:
    return ts.isoformat() if ts else ""


def _money(x: float) -> float:
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return 0.0
