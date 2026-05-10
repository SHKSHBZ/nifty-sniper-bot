"""Volume Profile (POC / VAH / VAL / HVN / LVN) for the NIFTY index.

Per nifty_trading_system_config.json: spot has no real volume, so we use
NIFTY monthly futures volume (joined minute-by-minute on timestamp) as
the volume input, while bucketing on spot OHLC price ranges. This is the
standard index-VP construction.

Inputs:
  - data/NIFTY50_INDEX_1minute.csv      (spot OHLC)
  - data/NIFTY_FUT_volume_1minute.csv   (futures volume from continuous-stitcher)

Outputs (VolumeProfile dataclass):
  - poc:   price level with peak volume (Point of Control)
  - vah:   Value Area High (upper edge of the band that holds 70% of vol)
  - val:   Value Area Low
  - hvn:   list of (price_low, price_high, vol) bins above hvn percentile
  - lvn:   list of (price_low, price_high, vol) bins below lvn percentile
  - bin_edges, bin_volumes: raw arrays for plotting / further analysis

Volume distribution is uniform across each bar's [low, high] span — the
classic TPO approximation. A bar that straddles 5 bins contributes
volume/5 to each. Bars where high == low collapse to a single bin.

CLI demo:
    python -m backtesting.volume_profile --start 2025-08-01 --end 2025-08-28
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SPOT_FILE = DATA / "NIFTY50_INDEX_1minute.csv"
VOL_FILE = DATA / "NIFTY_FUT_volume_1minute.csv"


@dataclass
class VolumeProfile:
    poc: float
    vah: float
    val: float
    hvn: list[tuple[float, float, float]] = field(default_factory=list)
    lvn: list[tuple[float, float, float]] = field(default_factory=list)
    bin_edges: np.ndarray = field(default_factory=lambda: np.array([]))
    bin_volumes: np.ndarray = field(default_factory=lambda: np.array([]))
    total_volume: float = 0.0
    n_bars: int = 0
    price_range: tuple[float, float] = (0.0, 0.0)


def load_aligned() -> pd.DataFrame:
    """Load spot OHLC + futures volume, inner-joined on timestamp.

    Returns a DataFrame indexed by timestamp with columns:
      open, high, low, close, futures_volume, contract_used
    """
    if not SPOT_FILE.exists():
        raise FileNotFoundError(f"Spot file missing: {SPOT_FILE}")
    if not VOL_FILE.exists():
        raise FileNotFoundError(f"Futures volume file missing: {VOL_FILE}")

    spot = pd.read_csv(SPOT_FILE)
    spot["timestamp"] = pd.to_datetime(spot["timestamp"])
    spot = spot.set_index("timestamp")[["open", "high", "low", "close"]]

    vol = pd.read_csv(VOL_FILE)
    vol["timestamp"] = pd.to_datetime(vol["timestamp"])
    vol = vol.set_index("timestamp")[["futures_volume", "contract_used"]]

    df = spot.join(vol, how="inner").sort_index()
    return df


def _distribute_volume(
    lows: np.ndarray,
    highs: np.ndarray,
    vols: np.ndarray,
    bin_edges: np.ndarray,
) -> np.ndarray:
    """For each bar i, spread vols[i] uniformly across the bins overlapping
    [lows[i], highs[i]]. Returns total volume per bin.

    Vectorized only across bins (per-bar loop). Fast enough for ~100k bars.
    """
    n_bins = len(bin_edges) - 1
    bin_vol = np.zeros(n_bins, dtype=np.float64)

    for low, high, vol in zip(lows, highs, vols):
        if vol <= 0:
            continue
        if high <= low:
            # Degenerate bar — all volume to the bin containing low
            idx = np.searchsorted(bin_edges, low, side="right") - 1
            idx = max(0, min(n_bins - 1, idx))
            bin_vol[idx] += vol
            continue

        # Find first/last bin that the [low, high] range touches
        first = np.searchsorted(bin_edges, low, side="right") - 1
        last = np.searchsorted(bin_edges, high, side="left") - 1
        first = max(0, first)
        last = min(n_bins - 1, last)

        if first == last:
            bin_vol[first] += vol
            continue

        n_touched = last - first + 1
        bin_vol[first:last + 1] += vol / n_touched

    return bin_vol


def _value_area(
    bin_volumes: np.ndarray,
    poc_idx: int,
    target_frac: float = 0.70,
) -> tuple[int, int]:
    """Expand outward from POC, taking the higher-volume neighbor each step,
    until cumulative volume >= target_frac * total. Returns (val_idx, vah_idx).
    """
    total = bin_volumes.sum()
    if total <= 0:
        return poc_idx, poc_idx
    target = total * target_frac

    n = len(bin_volumes)
    low_i = high_i = poc_idx
    cum = bin_volumes[poc_idx]

    while cum < target and (low_i > 0 or high_i < n - 1):
        # Sum the next two bars on each side — Steidlmayer's classical
        # rule pairs them together (avoids zigzag artifacts on noisy bins)
        below = bin_volumes[max(0, low_i - 2):low_i].sum() if low_i > 0 else 0
        above = bin_volumes[high_i + 1:min(n, high_i + 3)].sum() if high_i < n - 1 else 0

        if below >= above and low_i > 0:
            step = min(2, low_i)
            cum += bin_volumes[low_i - step:low_i].sum()
            low_i -= step
        elif high_i < n - 1:
            step = min(2, n - 1 - high_i)
            cum += bin_volumes[high_i + 1:high_i + 1 + step].sum()
            high_i += step
        else:
            break

    return low_i, high_i


def compute_profile(
    df: pd.DataFrame,
    *,
    bins: int = 50,
    value_area_pct: float = 0.70,
    hvn_percentile: float = 75.0,
    lvn_percentile: float = 25.0,
) -> VolumeProfile:
    """Compute Volume Profile over the full DataFrame slice.

    Caller is responsible for slicing to the desired window (single
    session, multi-day, lookback N candles, etc.) before calling.
    """
    if df.empty:
        raise ValueError("Empty DataFrame — slice first")

    lows = df["low"].to_numpy(dtype=np.float64)
    highs = df["high"].to_numpy(dtype=np.float64)
    vols = df["futures_volume"].to_numpy(dtype=np.float64)

    p_min = float(lows.min())
    p_max = float(highs.max())
    if p_max <= p_min:
        # All bars at one price — degenerate but valid
        return VolumeProfile(
            poc=p_min, vah=p_min, val=p_min,
            bin_edges=np.array([p_min, p_min + 0.01]),
            bin_volumes=np.array([vols.sum()]),
            total_volume=float(vols.sum()),
            n_bars=len(df),
            price_range=(p_min, p_max),
        )

    bin_edges = np.linspace(p_min, p_max, bins + 1)
    bin_volumes = _distribute_volume(lows, highs, vols, bin_edges)

    poc_idx = int(np.argmax(bin_volumes))
    poc = (bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2

    val_idx, vah_idx = _value_area(bin_volumes, poc_idx, value_area_pct)
    val = float(bin_edges[val_idx])
    vah = float(bin_edges[vah_idx + 1])

    # HVN / LVN classification by percentile of NON-ZERO bin volumes
    nonzero = bin_volumes[bin_volumes > 0]
    if len(nonzero) == 0:
        hvn_thr = lvn_thr = 0.0
    else:
        hvn_thr = float(np.percentile(nonzero, hvn_percentile))
        lvn_thr = float(np.percentile(nonzero, lvn_percentile))

    hvn = [
        (float(bin_edges[i]), float(bin_edges[i + 1]), float(v))
        for i, v in enumerate(bin_volumes) if v >= hvn_thr and v > 0
    ]
    lvn = [
        (float(bin_edges[i]), float(bin_edges[i + 1]), float(v))
        for i, v in enumerate(bin_volumes) if 0 < v <= lvn_thr
    ]

    return VolumeProfile(
        poc=float(poc),
        vah=vah,
        val=val,
        hvn=hvn,
        lvn=lvn,
        bin_edges=bin_edges,
        bin_volumes=bin_volumes,
        total_volume=float(bin_volumes.sum()),
        n_bars=len(df),
        price_range=(p_min, p_max),
    )


def session_profiles(
    df: pd.DataFrame,
    **kwargs,
) -> dict[pd.Timestamp, VolumeProfile]:
    """Group bars by trading-day date and compute one profile per session.

    Returns {session_date_midnight_ts: VolumeProfile}.
    """
    out: dict[pd.Timestamp, VolumeProfile] = {}
    for day, group in df.groupby(df.index.date):
        if len(group) < 30:
            continue  # too few bars for a meaningful profile
        out[pd.Timestamp(day)] = compute_profile(group, **kwargs)
    return out


def _summarize(vp: VolumeProfile) -> str:
    return (
        f"  bars={vp.n_bars}  range=[{vp.price_range[0]:.2f}, {vp.price_range[1]:.2f}]\n"
        f"  POC = {vp.poc:.2f}\n"
        f"  Value Area = [{vp.val:.2f}, {vp.vah:.2f}]  (width={vp.vah - vp.val:.2f})\n"
        f"  HVN bins: {len(vp.hvn)}   LVN bins: {len(vp.lvn)}\n"
        f"  total_volume = {vp.total_volume:,.0f}"
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--bins", type=int, default=50)
    p.add_argument("--va-pct", type=float, default=0.70)
    p.add_argument("--per-session", action="store_true",
                   help="Print one profile per trading day instead of one combined")
    args = p.parse_args()

    print(f"Loading spot + futures volume from {DATA}...")
    df = load_aligned()
    print(f"  joined rows: {len(df):,}  range: {df.index.min()} -> {df.index.max()}")

    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df.loc[start:end]
    if sub.empty:
        raise SystemExit(f"No data in window {args.start} -> {args.end}")
    print(f"  window: {args.start} -> {args.end}  ({len(sub):,} bars)\n")

    if args.per_session:
        profiles = session_profiles(sub, bins=args.bins, value_area_pct=args.va_pct)
        for day, vp in sorted(profiles.items()):
            print(f"=== {day.date()} ===")
            print(_summarize(vp))
            print()
        print(f"Total sessions: {len(profiles)}")
    else:
        vp = compute_profile(sub, bins=args.bins, value_area_pct=args.va_pct)
        print("=== Combined profile ===")
        print(_summarize(vp))
        print()
        print("Top 5 HVN bins (price_low - price_high : volume):")
        for lo, hi, v in sorted(vp.hvn, key=lambda x: -x[2])[:5]:
            print(f"  {lo:8.2f} - {hi:8.2f}  vol = {v:>14,.0f}")
        print("Top 5 LVN bins:")
        for lo, hi, v in sorted(vp.lvn, key=lambda x: x[2])[:5]:
            print(f"  {lo:8.2f} - {hi:8.2f}  vol = {v:>14,.0f}")


if __name__ == "__main__":
    main()
