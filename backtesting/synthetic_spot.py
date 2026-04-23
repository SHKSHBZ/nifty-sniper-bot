"""
Derive a synthetic Nifty spot 1-min series from the option chain via
put-call parity:

    C - P = S - K * exp(-r*T)   ->   S ≈ K + C - P   (when r*T is tiny)

For each 1-min timestamp, we:
  1. Find the strike K where |C_close - P_close| is smallest (the ATM).
  2. Compute S_est = K + C_close - P_close.
  3. Optionally average over the 3 strikes closest to ATM for stability.

This is only an approximation — real spot has ±5–15 pt noise vs this estimate
for weekly expiries, driven by interest and early-exercise premium. For
regime classification (which uses broad thresholds like ADX > 25 or gap >
0.5%), the approximation is adequate.

Produces an OHLCV-shaped DataFrame that the classifier can ingest.
"""
from __future__ import annotations

from pathlib import Path
import re

import numpy as np
import pandas as pd


FILENAME_RE = re.compile(
    r"^NIFTY_(\d+)_(CE|PE)_(\d+_[A-Z]+_\d+)_1min\.csv$"
)


def _load_chain(data_dir: Path) -> tuple[dict[int, pd.DataFrame], dict[int, pd.DataFrame]]:
    """Return (ce_by_strike, pe_by_strike) dicts of DataFrames indexed by timestamp."""
    ce: dict[int, pd.DataFrame] = {}
    pe: dict[int, pd.DataFrame] = {}
    for p in sorted(data_dir.glob("NIFTY_*_1min.csv")):
        m = FILENAME_RE.match(p.name)
        if not m:
            continue
        strike = int(m.group(1))
        side = m.group(2)
        df = pd.read_csv(p)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        (ce if side == "CE" else pe)[strike] = df
    return ce, pe


def derive_synthetic_spot(
    data_dir: Path,
    *,
    atm_neighbors: int = 3,
) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by timestamp with columns
    [open, high, low, close, volume].

    Volume is set to 1 as a stub so downstream VWAP math doesn't collapse —
    this synthetic series has no actual volume.
    """
    ce_map, pe_map = _load_chain(data_dir)
    if not ce_map or not pe_map:
        raise RuntimeError(f"No option CSVs matching NIFTY_*_1min.csv in {data_dir}")

    common_strikes = sorted(set(ce_map) & set(pe_map))
    if not common_strikes:
        raise RuntimeError("No strikes have both CE and PE files")

    # Align all chains on a common minute-grid
    all_timestamps = sorted({ts for df in ce_map.values() for ts in df.index}
                            & {ts for df in pe_map.values() for ts in df.index})
    ts_index = pd.DatetimeIndex(all_timestamps)

    # Build close matrices: rows=timestamps, cols=strikes
    ce_close = pd.DataFrame(index=ts_index, columns=common_strikes, dtype=float)
    pe_close = pd.DataFrame(index=ts_index, columns=common_strikes, dtype=float)
    for k in common_strikes:
        ce_close[k] = ce_map[k]["close"].reindex(ts_index)
        pe_close[k] = pe_map[k]["close"].reindex(ts_index)

    # Per-timestamp ATM = strike with smallest |C - P|
    diff = (ce_close - pe_close).abs()
    atm_idx = diff.idxmin(axis=1)          # Series: ts -> strike

    # For each row, average (K + C - P) across atm_neighbors nearest strikes
    estimates = []
    for ts in ts_index:
        k_atm = atm_idx.loc[ts]
        if pd.isna(k_atm):
            estimates.append(np.nan)
            continue
        idx = common_strikes.index(int(k_atm))
        lo = max(0, idx - atm_neighbors // 2)
        hi = min(len(common_strikes), lo + atm_neighbors)
        ks = common_strikes[lo:hi]
        vals = [k + ce_close.loc[ts, k] - pe_close.loc[ts, k] for k in ks
                if not pd.isna(ce_close.loc[ts, k]) and not pd.isna(pe_close.loc[ts, k])]
        estimates.append(np.mean(vals) if vals else np.nan)

    s_close = pd.Series(estimates, index=ts_index, name="close").dropna()

    # Build an OHLCV frame at 1-min resolution by aggregating nearby estimates.
    # Since we have a "close" estimate per minute, build a naive OHLC where
    # open=close and high/low use a small rolling band to approximate intrabar range.
    out = pd.DataFrame(index=s_close.index)
    out["close"] = s_close
    out["open"] = s_close.shift(1).fillna(s_close)
    rolling = s_close.rolling(3, min_periods=1)
    out["high"] = rolling.max()
    out["low"] = rolling.min()
    out["volume"] = 1   # synthetic; keeps VWAP math from dividing by zero
    return out


if __name__ == "__main__":
    from pathlib import Path
    import sys
    ROOT = Path(__file__).resolve().parent.parent
    data_dir = ROOT / "data"
    df = derive_synthetic_spot(data_dir)
    print(f"rows: {len(df):,}")
    print(f"range: {df.index.min()} -> {df.index.max()}")
    days = df.groupby(df.index.date).size()
    print(f"days: {len(days)}")
    print(days)
    print("\nhead:")
    print(df.head(3))
    print("\ntail:")
    print(df.tail(3))
    out_path = ROOT / "data" / "NIFTY_SPOT_SYNTHETIC_1min.csv"
    df.to_csv(out_path, index_label="timestamp")
    print(f"\nwrote {out_path.relative_to(ROOT)}")
