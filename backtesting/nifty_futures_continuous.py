"""Stitch monthly NIFTY futures into a continuous front-month series.

Two outputs:

  Approach A — VOLUME-ONLY merge (recommended for our backtests):
    data/NIFTY_FUT_volume_1minute.csv
    Columns: timestamp, futures_volume, futures_oi, contract_used
    Joins minute-by-minute to existing NIFTY50_INDEX_1minute.csv
    via timestamp. Use spot OHLC for price, futures volume for
    Volume Profile / HVN / LVN.

  Approach B — FULL CONTINUOUS series:
    data/NIFTY_FUT_continuous_1minute.csv
    Columns: timestamp, open, high, low, close, volume, open_interest, contract
    Front-month-rolled OHLCV. Use this if you want
    futures-based backtests directly.

Roll rule (volume-based):
  At each calendar day d, choose the contract C such that:
    - C is not yet expired on d
    - C had the highest daily volume on the preceding trading day
  This auto-handles the natural roll 3-5 days before expiry.

Date-based roll fallback:
  If volume-based logic fails (e.g., zero volume on prior day),
  use the calendar-nearest unexpired contract.
"""
from __future__ import annotations

import re
from pathlib import Path
from datetime import date, datetime

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SPOT_FILE = DATA_DIR / "NIFTY50_INDEX_1minute.csv"

OPT_NAME_RE = re.compile(
    r"^NIFTY_FUT_(\d{1,2}_[A-Z]{3}_\d{2})_1min\.csv$"
)
MONTHS_REV = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def parse_token(token: str) -> date:
    d, m, y = token.split("_")
    return date(2000 + int(y), MONTHS_REV[m], int(d))


def discover_contracts() -> list[tuple[date, Path]]:
    """Return [(expiry_date, csv_path)] sorted by expiry."""
    out = []
    for p in DATA_DIR.iterdir():
        m = OPT_NAME_RE.match(p.name)
        if m:
            out.append((parse_token(m.group(1)), p))
    return sorted(out)


def load_contract(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def build_daily_volume_table(contracts: list[tuple[date, Path]]) -> pd.DataFrame:
    """For each (expiry, day) compute total daily volume.
    Returns wide-format: index=date, columns=expiry_date, values=daily_volume."""
    rows = []
    for exp, p in contracts:
        df = load_contract(p)
        df["date"] = df["timestamp"].dt.date
        daily = df.groupby("date")["volume"].sum().reset_index()
        daily["expiry"] = exp
        rows.append(daily)
    if not rows:
        return pd.DataFrame()
    long = pd.concat(rows, ignore_index=True)
    return long.pivot(index="date", columns="expiry", values="volume").fillna(0)


def choose_front_month_per_day(daily_volume: pd.DataFrame,
                               contract_expiries: list[date]) -> dict[date, date]:
    """For each calendar day in the daily-volume index, pick which
    contract is 'active' = highest-volume contract that hasn't expired."""
    front_month = {}
    sorted_expiries = sorted(contract_expiries)
    prev_volumes = None
    for d in sorted(daily_volume.index):
        # Candidates: contracts whose expiry >= d (not yet expired)
        unexpired = [e for e in sorted_expiries if e >= d]
        if not unexpired:
            continue
        # Among unexpired, pick highest YESTERDAY volume; fallback to
        # nearest-expiry contract.
        if prev_volumes is not None:
            scores = {e: prev_volumes.get(e, 0) for e in unexpired}
            picked = max(scores, key=scores.get)
            if scores[picked] == 0:
                picked = min(unexpired)  # nearest expiry
        else:
            picked = min(unexpired)
        front_month[d] = picked
        prev_volumes = daily_volume.loc[d].to_dict()
    return front_month


def build_continuous(contracts: list[tuple[date, Path]],
                     front_month: dict[date, date]) -> pd.DataFrame:
    """For each minute, take that day's chosen contract's row."""
    contract_dfs = {exp: load_contract(p) for exp, p in contracts}
    pieces = []
    for d, picked_exp in front_month.items():
        df = contract_dfs[picked_exp]
        day_df = df[df["timestamp"].dt.date == d].copy()
        if day_df.empty:
            continue
        day_df["contract"] = picked_exp.isoformat()
        pieces.append(day_df)
    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces, ignore_index=True)
    return out.sort_values("timestamp").reset_index(drop=True)


def write_volume_only(continuous: pd.DataFrame, out_path: Path) -> None:
    """Volume + OI per minute, indexed by timestamp."""
    cols = ["timestamp", "volume", "open_interest", "contract"]
    df = continuous[cols].rename(columns={
        "volume": "futures_volume",
        "open_interest": "futures_oi",
        "contract": "contract_used",
    })
    df.to_csv(out_path, index=False)


def main():
    if not SPOT_FILE.exists():
        print(f"[error] {SPOT_FILE} missing")
        return

    contracts = discover_contracts()
    if not contracts:
        print("[error] no NIFTY_FUT_*.csv found in data/. "
              "Run nifty_futures_downloader first.")
        return
    print(f"Found {len(contracts)} monthly futures contracts: "
          f"{contracts[0][0]} → {contracts[-1][0]}\n")

    print("Building daily-volume table for roll selection...")
    daily_vol = build_daily_volume_table(contracts)
    print(f"  {len(daily_vol)} unique trading days across all contracts")

    contract_expiries = [exp for exp, _ in contracts]
    front_month = choose_front_month_per_day(daily_vol, contract_expiries)
    print(f"  picked front-month for {len(front_month)} days")
    # Roll points
    rolls = []
    prev_picked = None
    for d in sorted(front_month.keys()):
        if front_month[d] != prev_picked:
            rolls.append((d, front_month[d]))
            prev_picked = front_month[d]
    print(f"  {len(rolls)} roll-overs detected")
    for d, exp in rolls[:8]:
        print(f"    {d}: → {exp.isoformat()} contract")
    if len(rolls) > 8:
        print(f"    ... ({len(rolls) - 8} more)")

    print("\nStitching continuous series...")
    continuous = build_continuous(contracts, front_month)
    print(f"  {len(continuous)} bars total")

    out_b = DATA_DIR / "NIFTY_FUT_continuous_1minute.csv"
    continuous.to_csv(out_b, index=False)
    print(f"  wrote {out_b}")

    out_a = DATA_DIR / "NIFTY_FUT_volume_1minute.csv"
    write_volume_only(continuous, out_a)
    print(f"  wrote {out_a}")

    # Sanity check — average daily volume
    daily = continuous.groupby(continuous["timestamp"].dt.date)["volume"].sum()
    print(f"\nSanity:  median daily volume = {int(daily.median()):,}")
    print(f"         min / max = {int(daily.min()):,} / {int(daily.max()):,}")


if __name__ == "__main__":
    main()
