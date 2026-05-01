"""
Phase 11 — Aggregate near-miss analysis.

Loads phase11_near_misses.pkl (produced by run_all_tactics.py with the
new diagnostic gates), aggregates by (tactic, blocker), and computes
the would-have-been P&L impact per blocker.

Outputs five CSVs into reports/:

    phase11_near_miss_raw.csv         — every near-miss row
    phase11_near_miss_per_tactic.csv  — totals per tactic
    phase11_near_miss_blockers.csv    — per (tactic, blocker) with verdict
    phase11_near_miss_direction.csv   — CE/PE breakdown per tactic
    phase11_near_miss_top_impact.csv  — top 20 highest-|P&L| near-misses

This is the actionable artifact: blockers that frequently rejected
winners are candidates for relaxation; blockers that rejected losers
are doing their job.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.run_all_tactics import NearMiss, TacticTrade  # noqa: F401
from journal.near_miss_aggregator_lib import write_all_csvs
import __main__ as _main_mod
_main_mod.NearMiss = NearMiss
_main_mod.TacticTrade = TacticTrade

NM_PICKLE = ROOT / "reports" / "phase11_near_misses.pkl"
TRADE_PICKLE = ROOT / "reports" / "phase9_tactic_trades.pkl"
OUT_DIR = ROOT / "reports"
PREFIX = "phase11_near_miss"


def load_near_misses() -> dict:
    if not NM_PICKLE.exists():
        raise SystemExit(f"Cache not found: {NM_PICKLE}")
    with NM_PICKLE.open("rb") as fh:
        return pickle.load(fh)


def to_df(near_misses_by_tactic: dict) -> pd.DataFrame:
    rows = []
    for tactic, nms in near_misses_by_tactic.items():
        for nm in nms:
            rows.append({
                "tactic": tactic,
                "direction": nm.direction,
                "ts": nm.ts,
                "blocked_by": nm.blocked_by,
                "blocker_detail": nm.blocker_detail,
                "regime": nm.regime_at_ts,
                "hypothetical_strike": nm.hypothetical_strike,
                "hypothetical_entry_premium": nm.hypothetical_entry_premium,
                "hypothetical_exit_premium": nm.hypothetical_exit_premium,
                "hypothetical_exit_reason": nm.hypothetical_exit_reason,
                "hypothetical_pnl": nm.hypothetical_pnl,
                "hypothetical_outcome": nm.hypothetical_outcome,
            })
    return pd.DataFrame(rows)


def main():
    nms = load_near_misses()
    df = to_df(nms)
    paths = write_all_csvs(df, OUT_DIR, prefix=PREFIX)
    print(f"Total near-misses: {len(df):,}")
    for section, path in paths.items():
        print(f"  {section}: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
