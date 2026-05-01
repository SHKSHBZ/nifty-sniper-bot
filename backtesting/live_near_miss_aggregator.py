"""
Aggregate live (paper-bot) near-miss data from
reports/journal/journal_*_missed.csv.

Usage:
    python -m backtesting.live_near_miss_aggregator [--days N]
                                                    [--from YYYY-MM-DD]
                                                    [--to YYYY-MM-DD]

Output (5 CSVs into reports/):
    live_near_miss_<from>_to_<to>_raw.csv
    live_near_miss_<from>_to_<to>_per_tactic.csv
    live_near_miss_<from>_to_<to>_blockers.csv
    live_near_miss_<from>_to_<to>_direction.csv
    live_near_miss_<from>_to_<to>_top_impact.csv

Run after a few days/weeks of live data has accumulated to get an
actionable view of which gates are costing money. Open in Excel and
pivot from the raw CSV, or use the pre-aggregated summary CSVs directly.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from journal.near_miss_aggregator_lib import write_all_csvs  # noqa: E402

JOURNAL_DIR = ROOT / "reports" / "journal"
OUT_DIR = ROOT / "reports"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Aggregate live near-miss data from journal CSVs",
    )
    p.add_argument("--days", type=int, default=None,
                   help="Use the last N days of journal files.")
    p.add_argument("--from", dest="from_date", type=str, default=None,
                   help="Start date YYYY-MM-DD (inclusive).")
    p.add_argument("--to", dest="to_date", type=str, default=None,
                   help="End date YYYY-MM-DD (inclusive).")
    p.add_argument("--journal-dir", type=str, default=str(JOURNAL_DIR),
                   help="Override journal directory (for tests).")
    p.add_argument("--out-dir", type=str, default=None,
                   help="Override output directory (default reports/).")
    return p.parse_args(argv)


def resolve_window(args: argparse.Namespace) -> tuple[date, date]:
    today = date.today()
    if args.days is not None:
        return today - timedelta(days=args.days - 1), today
    if args.from_date and args.to_date:
        return (datetime.strptime(args.from_date, "%Y-%m-%d").date(),
                datetime.strptime(args.to_date, "%Y-%m-%d").date())
    if args.from_date and not args.to_date:
        return (datetime.strptime(args.from_date, "%Y-%m-%d").date(), today)
    if args.to_date and not args.from_date:
        end = datetime.strptime(args.to_date, "%Y-%m-%d").date()
        return (end - timedelta(days=29), end)
    return today - timedelta(days=29), today


def load_missed(journal_dir: Path, start: date, end: date) -> pd.DataFrame:
    """Load every journal_<date>_missed.csv in [start, end] and concat."""
    if not journal_dir.exists():
        return pd.DataFrame(columns=[
            "tactic", "direction", "ts", "blocked_by", "blocker_detail",
            "regime", "hypothetical_strike", "hypothetical_entry_premium",
            "hypothetical_exit_premium", "hypothetical_pnl",
            "hypothetical_outcome",
        ])
    frames = []
    for path in sorted(journal_dir.glob("journal_*_missed.csv")):
        try:
            day_str = path.stem.replace("journal_", "").replace("_missed", "")
            d = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < start or d > end:
            continue
        try:
            sub = pd.read_csv(path)
        except Exception as e:
            print(f"warn: could not read {path}: {e}")
            continue
        if not sub.empty:
            frames.append(sub)
    if not frames:
        return pd.DataFrame(columns=[
            "tactic", "direction", "ts", "blocked_by", "blocker_detail",
            "regime", "hypothetical_strike", "hypothetical_entry_premium",
            "hypothetical_exit_premium", "hypothetical_pnl",
            "hypothetical_outcome",
        ])
    df = pd.concat(frames, ignore_index=True)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    return df


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    start, end = resolve_window(args)
    journal_dir = Path(args.journal_dir)
    df = load_missed(journal_dir, start, end)

    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    prefix = f"live_near_miss_{start.isoformat()}_to_{end.isoformat()}"
    paths = write_all_csvs(df, out_dir, prefix=prefix)

    print(f"Window: {start} to {end} ({(end - start).days + 1} days)")
    print(f"Source: {journal_dir}")
    print(f"Total near-misses aggregated: {len(df):,}")
    for section, path in paths.items():
        print(f"  {section}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
