"""Fair Value Gap (FVG) detector — 3-candle pattern.

Per nifty_trading_system_config.json:
    candle_pattern:    3_candle
    min_gap_size_pct:  0.05% (relative to mid-price of the gap)
    expiry_candles:    200  (after which an unfilled gap is considered stale)

Detection (acts on consecutive c1, c2, c3):
    bullish_fvg: c1.high < c3.low  -> gap zone = (c1.high, c3.low)
                                       acts as SUPPORT below price
    bearish_fvg: c1.low  > c3.high -> gap zone = (c3.high, c1.low)
                                       acts as RESISTANCE above price

Fill rule: a gap is "filled" the first time any later candle's range
[low, high] touches the gap zone. Once filled, it's removed from the
active set. After `expiry_candles` it expires unfilled.

CLI demo:
    python -m backtesting.fair_value_gaps --start 2025-08-25 --end 2025-08-28 --tf 5min
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv


MIN_GAP_PCT = 0.05
EXPIRY_BARS = 200


@dataclass
class FVG:
    formed_at: pd.Timestamp     # timestamp of c3 (when the gap is confirmed)
    direction: Literal["bullish", "bearish"]
    low: float                  # bottom of the gap zone
    high: float                 # top of the gap zone
    size_pct: float             # (high-low) / mid * 100
    filled_at: pd.Timestamp | None = None
    expired_at: pd.Timestamp | None = None

    @property
    def is_active(self) -> bool:
        return self.filled_at is None and self.expired_at is None

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2


def detect_gaps(
    df: pd.DataFrame,
    *,
    min_gap_pct: float = MIN_GAP_PCT,
    expiry_bars: int = EXPIRY_BARS,
) -> list[FVG]:
    """Scan df for 3-candle FVGs, then walk forward to mark each as
    filled / expired / active. Returns all gaps detected (including
    filled ones — caller filters).
    """
    if len(df) < 3:
        return []

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df.index

    gaps: list[FVG] = []

    # First pass: detect formation
    for i in range(2, len(df)):
        c1_high, c1_low = highs[i - 2], lows[i - 2]
        c3_high, c3_low = highs[i], lows[i]

        if c1_high < c3_low:
            lo, hi = c1_high, c3_low
            mid = (lo + hi) / 2
            if mid > 0 and (hi - lo) / mid * 100 >= min_gap_pct:
                gaps.append(FVG(times[i], "bullish", float(lo), float(hi),
                                (hi - lo) / mid * 100))
        elif c1_low > c3_high:
            lo, hi = c3_high, c1_low
            mid = (lo + hi) / 2
            if mid > 0 and (hi - lo) / mid * 100 >= min_gap_pct:
                gaps.append(FVG(times[i], "bearish", float(lo), float(hi),
                                (hi - lo) / mid * 100))

    # Second pass: walk forward to mark fill/expiry per gap
    # Build a quick index from timestamp -> row position for O(1) lookups
    idx_pos = {ts: pos for pos, ts in enumerate(times)}
    n = len(df)

    for g in gaps:
        start_pos = idx_pos[g.formed_at] + 1  # the candle AFTER c3
        bars_available = n - start_pos
        scan_end = start_pos + min(expiry_bars, bars_available)
        for j in range(start_pos, scan_end):
            if lows[j] <= g.high and highs[j] >= g.low:
                g.filled_at = times[j]
                break
        else:
            # Only mark expired if we genuinely scanned the full expiry window.
            # If we ran out of data first, leave as active (status is unknown).
            if bars_available >= expiry_bars:
                g.expired_at = times[start_pos + expiry_bars - 1]

    return gaps


def active_gaps_at(
    gaps: list[FVG],
    asof: pd.Timestamp,
) -> list[FVG]:
    """Return gaps that were formed before `asof` and are still
    unfilled / unexpired as of `asof`. Use this when walking forward
    in a backtest.
    """
    out = []
    for g in gaps:
        if g.formed_at >= asof:
            continue
        if g.filled_at is not None and g.filled_at < asof:
            continue
        if g.expired_at is not None and g.expired_at < asof:
            continue
        out.append(g)
    return out


def nearest_gap(
    gaps: list[FVG],
    price: float,
    *,
    direction: Literal["above", "below", "any"] = "any",
) -> FVG | None:
    """From a list of (presumed active) gaps, return the closest one to
    `price`. Filter by side: 'above' = gaps whose mid > price (acts as
    overhead resistance), 'below' = gaps whose mid < price (support).
    """
    candidates = []
    for g in gaps:
        if direction == "above" and g.mid <= price: continue
        if direction == "below" and g.mid >= price: continue
        candidates.append(g)
    if not candidates:
        return None
    return min(candidates, key=lambda g: abs(g.mid - price))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--tf", default="5min",
                   help="Timeframe to detect FVGs on. Default 5min.")
    p.add_argument("--min-gap", type=float, default=MIN_GAP_PCT,
                   help="Minimum gap size as %% of mid (default 0.05)")
    p.add_argument("--expiry", type=int, default=EXPIRY_BARS,
                   help="Bars before unfilled gap expires (default 200)")
    p.add_argument("--show-filled", action="store_true",
                   help="Include filled gaps in output (default: only active+expired)")
    args = p.parse_args()

    df1 = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]
    if sub.empty:
        raise SystemExit("No data in window")

    df = resample_ohlcv(sub, args.tf)
    print(f"Window: {args.start} -> {args.end}  TF={args.tf}  bars={len(df)}\n")

    gaps = detect_gaps(df, min_gap_pct=args.min_gap, expiry_bars=args.expiry)

    n_total = len(gaps)
    n_filled = sum(1 for g in gaps if g.filled_at is not None)
    n_expired = sum(1 for g in gaps if g.expired_at is not None)
    n_active = sum(1 for g in gaps if g.is_active)
    n_bull = sum(1 for g in gaps if g.direction == "bullish")
    n_bear = sum(1 for g in gaps if g.direction == "bearish")

    print(f"Detected {n_total} FVGs ({n_bull} bullish + {n_bear} bearish)")
    print(f"  filled:  {n_filled}  ({n_filled/max(n_total,1)*100:.0f}%)")
    print(f"  expired: {n_expired}")
    print(f"  active:  {n_active}\n")

    print(f"{'Formed':<22} {'Dir':<8} {'Low':>10} {'High':>10} {'Size%':>7} {'Status':<25}")
    print("-" * 90)
    for g in gaps:
        if not args.show_filled and g.filled_at is not None:
            continue
        status = "ACTIVE"
        if g.filled_at is not None:
            status = f"filled {g.filled_at.strftime('%m-%d %H:%M')}"
        elif g.expired_at is not None:
            status = f"expired {g.expired_at.strftime('%m-%d %H:%M')}"
        print(f"{g.formed_at.strftime('%Y-%m-%d %H:%M'):<22} "
              f"{g.direction:<8} {g.low:>10.2f} {g.high:>10.2f} "
              f"{g.size_pct:>6.2f}% {status:<25}")


if __name__ == "__main__":
    main()
