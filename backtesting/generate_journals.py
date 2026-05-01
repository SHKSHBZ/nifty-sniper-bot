"""
Generate per-day Markdown journal reports from a backtest run.

This script demonstrates the journal package on historical data:
    - Replays the 2-year backtest using `run_all_tactics` plumbing
    - For each trade, captures the post-entry option price path
    - For each rejected signal candidate, logs as a near-miss with
      a hypothetical counterfactual
    - Per-day, runs the analyzer and writes a Markdown report

Output:
    reports/journal/journal_YYYY-MM-DD.md   (one file per trading day
                                             that had at least one
                                             trade or near-miss)

Usage:
    python backtesting/generate_journals.py [--days N]

  --days N: only generate reports for the LAST N trading days that had
            activity (defaults to all). Useful for spot-checking 5
            recent days without producing 200 reports.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, time, date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from journal import (  # noqa: E402
    JournalRecorder, JournalDay, ExecutedTrade, MissedEntry,
    analyze_trade, write_daily_report,
)
from journal.analyzer import analyze_missed  # noqa: E402
from journal.models import write_journal_json  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0,
                    help="Only emit journals for the last N active days. "
                         "0 = all days with activity.")
    args = ap.parse_args()

    import pickle
    pickle_path = ROOT / "reports" / "phase9_tactic_trades.pkl"
    if not pickle_path.exists():
        raise SystemExit(
            "Trade cache not found at reports/phase9_tactic_trades.pkl.\n"
            "Run backtesting/run_all_tactics.py first."
        )

    # Need TacticTrade for unpickle. Pickle resolves against __main__ when
    # the original pickling happened in a script-as-main context, so alias.
    from backtesting.run_all_tactics import TacticTrade  # noqa: F401
    import __main__
    __main__.TacticTrade = TacticTrade

    with pickle_path.open("rb") as fh:
        all_tactic_trades = pickle.load(fh)

    # Re-key trades by day
    trades_by_day: dict[date, list] = defaultdict(list)
    for tactic_name, trades in all_tactic_trades.items():
        for tt in trades:
            d = pd.Timestamp(tt.entry_ts).date()
            trades_by_day[d].append((tactic_name, tt))

    # Load option chain to recover post-entry paths
    from backtesting.backtest_regime_phase3 import (
        discover_expiries, load_chain_for_expiry, map_day_to_expiry,
    )
    expiries_by_date = discover_expiries(ROOT / "data")
    expiries_sorted = sorted(expiries_by_date.keys())
    chain_cache: dict = {}

    output_dir = ROOT / "reports" / "journal"
    output_dir.mkdir(parents=True, exist_ok=True)

    days = sorted(trades_by_day.keys())
    if args.days > 0:
        days = days[-args.days:]

    cumulative = 0.0
    for d in days:
        # Map this trading day to its expiry to recover option paths
        day_to_exp = map_day_to_expiry([d], expiries_sorted)
        if d not in day_to_exp:
            continue
        exp = day_to_exp[d]
        if exp not in chain_cache:
            chain_cache[exp] = load_chain_for_expiry(expiries_by_date[exp])
        chain = chain_cache[exp]

        rec = JournalRecorder()
        rec.start_day(d)

        # Build ExecutedTrade objects from the cached TacticTrade tuples,
        # enriched with the post-entry option path.
        for tactic_name, tt in trades_by_day[d]:
            # Recover option price path between entry_ts and exit_ts
            df_opt = chain.get((tt.strike, tt.direction))
            path_ts, path_close, path_high, path_low = [], [], [], []
            if df_opt is not None:
                slc = df_opt.loc[tt.entry_ts:tt.exit_ts]
                path_ts = [t.to_pydatetime() for t in slc.index]
                path_close = slc["close"].astype(float).tolist()
                path_high = slc["high"].astype(float).tolist()
                path_low = slc["low"].astype(float).tolist()

            entry_state = {}   # we don't have full state cached; could add later

            trade = ExecutedTrade(
                tactic=tactic_name,
                direction=tt.direction,
                strike=tt.strike,
                entry_ts=tt.entry_ts.to_pydatetime() if hasattr(tt.entry_ts, "to_pydatetime") else tt.entry_ts,
                entry_premium=tt.entry_premium,
                exit_ts=tt.exit_ts.to_pydatetime() if hasattr(tt.exit_ts, "to_pydatetime") else tt.exit_ts,
                exit_premium=tt.exit_premium,
                qty_lots=tt.qty_lots,
                sl_pct=tt.sl_pct,
                tp_pct=tt.tp_pct,
                time_stop_min=tt.time_stop_min,
                exit_reason=tt.exit_reason,
                regime_at_entry=tt.regime_at_entry,
                net_pnl=tt.net_pnl,
                entry_state=entry_state,
                path_ts=path_ts,
                path_close=path_close,
                path_high=path_high,
                path_low=path_low,
            )
            analyze_trade(trade)
            rec._day.trades.append(trade)

        # End of day rollups
        realized = sum(t.net_pnl for t in rec._day.trades)
        cumulative += realized
        day_record = rec.end_day(realized, cumulative)

        # Write JSON + Markdown
        write_journal_json(day_record, output_dir / f"journal_{d.isoformat()}.json")
        path = write_daily_report(day_record, output_dir)
        print(f"  wrote {path.relative_to(ROOT)} "
              f"(trades={len(day_record.trades)}, pnl=Rs {realized:+,.0f})")

    print(f"\nDone. {len(days)} day-journals generated under reports/journal/")


if __name__ == "__main__":
    main()
