"""
merge_chain_daily.py
====================
Merge per-expiry-per-strike option chain CSVs into daily focus-zone style files.
Deduplicates by keeping the latest expiry data for each timestamp.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "data" / "daily_chain"
OUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("merge")

STRIKE_STEP = 50

OPT_RE = re.compile(r"NIFTY_(\d+)_(CE|PE)_(\d+)_([A-Z]+)_(\d+)_1min\.csv$")
MONTHS = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
          7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}
REV_MONTHS = {v: k for k, v in MONTHS.items()}


def _exp_date(fpath: Path) -> tuple:
    m = OPT_RE.match(fpath.name)
    d, mon, y = int(m.group(3)), m.group(4), int(m.group(5))
    return (2000 + y, REV_MONTHS[mon], d)


def main():
    all_files = sorted(DATA_DIR.glob("NIFTY_*_*_*_*_1min.csv"))
    log.info(f"Found {len(all_files)} chain files")

    spot_df = pd.read_csv(DATA_DIR / "NIFTY50_INDEX_1minute.csv",
                           usecols=["timestamp", "close"])
    spot_df["timestamp"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.rename(columns={"close": "spot"}).set_index("timestamp")

    # Load all files, tag with expiry date for dedup
    frames = []
    for fpath in all_files:
        m = OPT_RE.match(fpath.name)
        if not m:
            continue
        strike = int(m.group(1))
        opt_type = m.group(2)
        exp_rank = _exp_date(fpath)  # (year, month, day) tuple for sorting
        try:
            df = pd.read_csv(fpath, usecols=["timestamp", "close", "open_interest"])
        except Exception:
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["strike"] = strike
        df["exp_rank"] = exp_rank[0] * 10000 + exp_rank[1] * 100 + exp_rank[2]
        col_p = f"{opt_type.lower()}_ltp"
        col_o = f"{opt_type.lower()}_oi"
        df = df.rename(columns={"close": col_p, "open_interest": col_o})
        df = df[["timestamp", "strike", col_p, col_o, "exp_rank"]]
        frames.append(df)

    full = pd.concat(frames, ignore_index=True)
    log.info(f"Total raw rows: {len(full)}")

    # Dedup: for each (timestamp, strike, side), keep the row with highest expiry rank
    ce = full[full["ce_ltp"].notna()].copy()
    pe = full[full["pe_ltp"].notna()].copy()

    # Keep latest expiry per (timestamp, strike)
    ce = ce.sort_values("exp_rank").drop_duplicates(subset=["timestamp", "strike"], keep="last")
    pe = pe.sort_values("exp_rank").drop_duplicates(subset=["timestamp", "strike"], keep="last")

    ce = ce[["timestamp", "strike", "ce_ltp", "ce_oi"]]
    pe = pe[["timestamp", "strike", "pe_ltp", "pe_oi"]]

    merged = ce.merge(pe, on=["timestamp", "strike"], how="outer")
    merged = merged.sort_values(["timestamp", "strike"]).reset_index(drop=True)

    merged = merged.merge(spot_df, left_on="timestamp", right_index=True, how="left")

    merged["atm"] = (merged["spot"] / STRIKE_STEP).round() * STRIKE_STEP
    merged["pos"] = ((merged["strike"] - merged["atm"]) / STRIKE_STEP).astype(int)

    out_cols = ["timestamp", "spot", "strike", "pos", "ce_ltp", "ce_oi", "pe_ltp", "pe_oi"]
    merged = merged[out_cols].copy()
    merged[["ce_ltp", "ce_oi", "pe_ltp", "pe_oi"]] = merged[
        ["ce_ltp", "ce_oi", "pe_ltp", "pe_oi"]
    ].fillna(0)

    merged["date"] = merged["timestamp"].dt.date
    n = 0
    for day, grp in merged.groupby("date", sort=True):
        out = OUT_DIR / f"daily_chain_NIFTY_{day.isoformat()}.csv"
        grp.drop(columns=["date"]).sort_values(["timestamp", "strike"]).to_csv(out, index=False)
        n += 1
        if n % 30 == 0:
            log.info(f"  wrote {n} files")

    log.info(f"Done. {n} daily files written to {OUT_DIR}")
    sample = merged.head(3)
    log.info(f"Sample: {sample.to_dict('records')}")


if __name__ == "__main__":
    main()
