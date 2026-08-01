"""Replay a focus_zone CSV through the new PCR slope + OI delta gates.

Usage:
    python backtesting/replay_pcr_oi_gates.py logs/focus_zone_nifty_2026-05-08.csv

Reports per checkpoint whether a hypothetical CE entry (and PE) would have
been allowed by Gate 2a (PCR slope) and Gate 2b (OI delta ratio). Run this
after editing thresholds in Options.json to see the impact on prior days.
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout on Windows so the arrow / rupee glyphs in gate messages
# don't crash on cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Ensure repo root on path so signal_engine imports cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signal_engine import PriceActionBot

CHECKPOINTS = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
               "13:00", "13:30", "14:00", "14:30", "15:00"]


def parse_ts(s: str) -> datetime:
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp format: {s!r}")


def replay(csv_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    by_ts = defaultdict(list)
    for r in rows:
        by_ts[r["timestamp"]].append(r)
    timestamps = sorted(by_ts.keys(), key=parse_ts)

    engine = PriceActionBot()
    seen = set()

    print(f"Replay file: {csv_path}")
    print(f"{'time':<19}  {'spot':>9}  {'PCR':>5}  CE  PE  notes")

    for ts in timestamps:
        snap = by_ts[ts]
        spot = float(snap[0]["spot"])
        ce_oi = sum(float(r["ce_oi"] or 0) for r in snap)
        pe_oi = sum(float(r["pe_oi"] or 0) for r in snap)
        pcr = pe_oi / ce_oi if ce_oi > 0 else 0.0
        dt = parse_ts(ts)
        engine._push_history(dt, pcr, {"total_ce_oi": ce_oi, "total_pe_oi": pe_oi})

        bucket = dt.strftime("%H:%M")
        if bucket in CHECKPOINTS and bucket not in seen:
            seen.add(bucket)
            ce_slope_ok, ce_slope_msg = engine._check_pcr_slope(dt, pcr, "CE")
            ce_oi_ok, ce_oi_msg = engine._check_oi_delta_ratio(
                dt, {"total_ce_oi": ce_oi, "total_pe_oi": pe_oi}, "CE")
            pe_slope_ok, pe_slope_msg = engine._check_pcr_slope(dt, pcr, "PE")
            pe_oi_ok, pe_oi_msg = engine._check_oi_delta_ratio(
                dt, {"total_ce_oi": ce_oi, "total_pe_oi": pe_oi}, "PE")

            ce = "PASS" if (ce_slope_ok and ce_oi_ok) else "BLOCK"
            pe = "PASS" if (pe_slope_ok and pe_oi_ok) else "BLOCK"
            note = ""
            if ce == "BLOCK":
                note += f" [CE: {(ce_slope_msg if not ce_slope_ok else ce_oi_msg)[:55]}]"
            if pe == "BLOCK":
                note += f" [PE: {(pe_slope_msg if not pe_slope_ok else pe_oi_msg)[:55]}]"
            print(f"{ts:<19}  {spot:>9,.0f}  {pcr:>5.2f}  {ce}  {pe} {note}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    replay(Path(sys.argv[1]))
