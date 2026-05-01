"""
Aggregate live (paper-bot) near-miss data from reports/journal/journal_*.json.

Usage:
    python -m backtesting.live_near_miss_aggregator [--days N]
                                                    [--from YYYY-MM-DD]
                                                    [--to YYYY-MM-DD]

Output: reports/live_near_miss_summary_<from>_to_<to>.md

The shape mirrors backtesting/phase11_near_miss_summary.md but is fed
from the journal JSON the live bot writes at end-of-day. Run after a
few days/weeks of live data has accumulated to get an actionable view
of which gates are costing money.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from journal.near_miss_aggregator_lib import build_full_report  # noqa: E402

JOURNAL_DIR = ROOT / "reports" / "journal"
OUT_DIR = ROOT / "reports"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Aggregate live near-miss data from journal JSONs",
    )
    p.add_argument("--days", type=int, default=None,
                   help="Use the last N days of journal files.")
    p.add_argument("--from", dest="from_date", type=str, default=None,
                   help="Start date YYYY-MM-DD (inclusive).")
    p.add_argument("--to", dest="to_date", type=str, default=None,
                   help="End date YYYY-MM-DD (inclusive).")
    p.add_argument("--journal-dir", type=str, default=str(JOURNAL_DIR),
                   help="Override journal directory (for tests).")
    p.add_argument("--out", type=str, default=None,
                   help="Override output report path.")
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
    rows: list[dict] = []
    if not journal_dir.exists():
        return pd.DataFrame(columns=[
            "tactic", "direction", "ts", "blocked_by", "blocker_detail",
            "regime", "hypothetical_strike", "hypothetical_entry_premium",
            "hypothetical_exit_premium", "hypothetical_pnl",
            "hypothetical_outcome",
        ])
    for path in sorted(journal_dir.glob("journal_*.json")):
        try:
            day_str = path.stem.replace("journal_", "")
            d = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < start or d > end:
            continue
        try:
            with path.open("r") as fh:
                payload = json.load(fh)
        except Exception as e:
            print(f"warn: could not load {path}: {e}")
            continue
        for m in payload.get("missed", []):
            ts = m.get("ts")
            try:
                ts_parsed = datetime.fromisoformat(ts) if ts else None
            except (TypeError, ValueError):
                ts_parsed = None
            state = m.get("state_snapshot") or {}
            rows.append({
                "tactic": m.get("tactic", ""),
                "direction": m.get("direction", ""),
                "ts": ts_parsed,
                "blocked_by": m.get("blocked_by", ""),
                "blocker_detail": m.get("blocker_detail", ""),
                "regime": state.get("regime", ""),
                "hypothetical_strike": m.get("hypothetical_strike", 0),
                "hypothetical_entry_premium": m.get("hypothetical_entry_premium", 0.0),
                "hypothetical_exit_premium": m.get("hypothetical_exit_premium", 0.0),
                "hypothetical_pnl": m.get("hypothetical_pnl", 0.0),
                "hypothetical_outcome": m.get("hypothetical_outcome", "UNKNOWN"),
            })
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    start, end = resolve_window(args)
    journal_dir = Path(args.journal_dir)
    df = load_missed(journal_dir, start, end)

    header = (
        f"# Live Near-Miss Aggregate Analysis\n"
        f"\nWindow: **{start.isoformat()}** to **{end.isoformat()}** "
        f"({(end - start).days + 1} days)\n"
        f"Source: `{journal_dir}`\n"
    )
    lines = build_full_report(df, header=header)

    if args.out:
        out_path = Path(args.out)
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / (
            f"live_near_miss_summary_{start.isoformat()}_to_{end.isoformat()}.md"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    print(f"Wrote: {out_path}")
    print(f"Total near-misses aggregated: {len(df):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
