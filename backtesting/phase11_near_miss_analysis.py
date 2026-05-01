"""
Phase 11 — Aggregate near-miss analysis.

Loads phase11_near_misses.pkl (produced by run_all_tactics.py with the
new diagnostic gates), aggregates by (tactic, blocker), and computes
the would-have-been P&L impact per blocker.

Outputs reports/phase11_near_miss_summary.md, which answers:

  * Which gate blocked the most signals?
  * Of the trades it blocked, how many would have WON if entered?
  * What's the cumulative hypothetical P&L delta if we relaxed each gate?

This is the actionable artifact: blockers that frequently rejected
winners are candidates for relaxation; blockers that rejected losers
are doing their job.
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.run_all_tactics import NearMiss, TacticTrade  # noqa: F401
from journal.near_miss_aggregator_lib import build_full_report
import __main__ as _main_mod
_main_mod.NearMiss = NearMiss
_main_mod.TacticTrade = TacticTrade

NM_PICKLE = ROOT / "reports" / "phase11_near_misses.pkl"
TRADE_PICKLE = ROOT / "reports" / "phase9_tactic_trades.pkl"
OUTPUT = ROOT / "reports" / "phase11_near_miss_summary.md"


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
    df = pd.DataFrame(rows)
    return df


def main():
    nms = load_near_misses()
    df = to_df(nms)
    if df.empty:
        print("No near-misses captured. Bot may have fired everything or "
              "all tactics' gates were too far from passing.")
        OUTPUT.write_text("# Phase 11 — No near-misses captured.\n")
        return

    lines = build_full_report(
        df, header="# Phase 11 -- Near-Miss Aggregate Analysis\n",
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines))
    print(f"Wrote: {OUTPUT.relative_to(ROOT)}")
    print(f"Total near-misses: {len(df):,}")


if __name__ == "__main__":
    main()
