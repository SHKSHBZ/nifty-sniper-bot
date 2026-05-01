"""
Trade Journal — daily detailed reporting + post-trade reinforcement-learning hooks.

Modules:
    models    Data classes (JournalEntry, TradeRecord)
    recorder  Captures entries/exits/near-misses during a run
    analyzer  Post-trade analysis: optimal exits, counterfactuals,
              improvement suggestions
    reporter  Generates daily Markdown narrative reports

The goal: every trade and every "almost-trade" becomes a documented
data point with a why-it-won/lost analysis, so the operator can
decide which rules to keep and which to revise.
"""

from journal.models import (
    JournalEntry, MissedEntry, ExecutedTrade, JournalDay,
)
from journal.recorder import JournalRecorder
from journal.analyzer import analyze_trade, suggest_improvements
from journal.reporter import write_daily_report

__all__ = [
    "JournalEntry",
    "MissedEntry",
    "ExecutedTrade",
    "JournalDay",
    "JournalRecorder",
    "analyze_trade",
    "suggest_improvements",
    "write_daily_report",
]
