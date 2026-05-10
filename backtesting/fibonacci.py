"""Swing-pivot detection + Fibonacci retracement / extension levels.

Pillar 4 of nifty_trading_system_config.json:
    retracement_levels: [0.382, 0.500, 0.618]
    extension_levels:   [1.272, 1.618]
    secondary_levels:   [0.236, 0.786]
    min_swing_size_pct: 0.5%
    trend_lookback_candles: 50

Swing detection uses the standard fractal rule: a bar at index i is a
pivot HIGH if its high is strictly greater than the highs of the `window`
bars on each side (default 2 -> total 5-bar fractal). Pivot LOW is the
mirror.

Given the most recent swing leg (low -> high or high -> low), we project
the Fibonacci retracement levels backward (where price might pull back
to) and extension levels forward (where the move might extend to).

CLI demo:
    python -m backtesting.fibonacci --start 2025-08-25 --end 2025-08-28 --tf 15min
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv


# Defaults from the strategy spec
RETRACEMENT_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786]
EXTENSION_LEVELS = [1.272, 1.618]
MIN_SWING_SIZE_PCT = 0.5
SWING_WINDOW = 2  # bars on each side


@dataclass
class Swing:
    timestamp: pd.Timestamp
    price: float
    kind: Literal["high", "low"]


@dataclass
class FibLevels:
    leg_start: Swing      # older pivot
    leg_end: Swing        # newer pivot
    direction: Literal["up", "down"]
    leg_size_pct: float
    is_significant: bool  # passes min_swing_size_pct
    retracements: dict[float, float] = field(default_factory=dict)
    extensions: dict[float, float] = field(default_factory=dict)


def find_swings(
    df: pd.DataFrame,
    *,
    window: int = SWING_WINDOW,
) -> list[Swing]:
    """Return all swing pivots in chronological order.

    A bar i is a pivot HIGH if its `high` is strictly greater than the
    highs of bars [i-window..i-1] and [i+1..i+window]. Pivot LOW is the
    mirror on `low`. Bars in the first/last `window` rows can never be
    pivots (insufficient context).
    """
    if len(df) < 2 * window + 1:
        return []

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    swings: list[Swing] = []

    for i in range(window, len(df) - window):
        left_h = highs[i - window:i]
        right_h = highs[i + 1:i + window + 1]
        left_l = lows[i - window:i]
        right_l = lows[i + 1:i + window + 1]

        if highs[i] > left_h.max() and highs[i] > right_h.max():
            swings.append(Swing(df.index[i], float(highs[i]), "high"))
        elif lows[i] < left_l.min() and lows[i] < right_l.min():
            swings.append(Swing(df.index[i], float(lows[i]), "low"))

    return swings


def _fib_from_leg(
    leg_start: Swing,
    leg_end: Swing,
    *,
    retracement_levels: list[float],
    extension_levels: list[float],
    min_swing_pct: float,
) -> FibLevels:
    """Compute retracement + extension prices given start/end swings."""
    direction: Literal["up", "down"] = (
        "up" if leg_end.price > leg_start.price else "down"
    )
    leg_size = leg_end.price - leg_start.price
    leg_size_pct = abs(leg_size) / leg_start.price * 100

    # Retracement: from leg_end back toward leg_start
    # For an UP leg (low->high): 38.2% retrace lands at high - 0.382*(high-low)
    retracements = {
        lv: float(leg_end.price - lv * leg_size)
        for lv in retracement_levels
    }
    # Extension: beyond leg_end in the leg's direction
    extensions = {
        lv: float(leg_start.price + lv * leg_size)
        for lv in extension_levels
    }

    return FibLevels(
        leg_start=leg_start,
        leg_end=leg_end,
        direction=direction,
        leg_size_pct=leg_size_pct,
        is_significant=leg_size_pct >= min_swing_pct,
        retracements=retracements,
        extensions=extensions,
    )


def latest_fib_leg(
    df: pd.DataFrame,
    *,
    window: int = SWING_WINDOW,
    min_swing_pct: float = MIN_SWING_SIZE_PCT,
    retracement_levels: list[float] | None = None,
    extension_levels: list[float] | None = None,
) -> FibLevels | None:
    """Find the most recent significant swing leg in df and return its fib
    levels. A "leg" is the segment between the two most recent opposite-
    type pivots. Returns None if no valid leg found.

    Walks back from the end of df to find the last pivot. Then walks
    further back to find the most recent pivot of the OPPOSITE kind.
    Together they form the leg.
    """
    rl = retracement_levels or RETRACEMENT_LEVELS
    el = extension_levels or EXTENSION_LEVELS

    swings = find_swings(df, window=window)
    if len(swings) < 2:
        return None

    # Walk from the end: the last swing is the leg END. Find the most
    # recent swing of the opposite kind to be the leg START.
    leg_end = swings[-1]
    leg_start = None
    for s in reversed(swings[:-1]):
        if s.kind != leg_end.kind:
            leg_start = s
            break

    if leg_start is None:
        return None

    return _fib_from_leg(
        leg_start, leg_end,
        retracement_levels=rl,
        extension_levels=el,
        min_swing_pct=min_swing_pct,
    )


def fib_levels_at(
    df: pd.DataFrame,
    asof: pd.Timestamp,
    *,
    lookback_bars: int = 50,
    **kwargs,
) -> FibLevels | None:
    """Same as latest_fib_leg but evaluated as of `asof`, considering only
    the previous `lookback_bars` rows. Useful for walking forward in a
    backtest to know what fib levels were "live" at each decision point.
    """
    sub = df.loc[:asof].iloc[-lookback_bars:]
    return latest_fib_leg(sub, **kwargs)


def _fmt_levels(d: dict[float, float]) -> str:
    return "  ".join(f"{int(k*1000)/1000:>5}={v:>9.2f}" for k, v in sorted(d.items()))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--tf", default="15min",
                   help="Timeframe to detect swings on. Default 15min.")
    p.add_argument("--window", type=int, default=SWING_WINDOW)
    p.add_argument("--min-swing", type=float, default=MIN_SWING_SIZE_PCT,
                   help="Minimum swing size as %% of price (default 0.5)")
    args = p.parse_args()

    df1 = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]
    if sub.empty:
        raise SystemExit("No data in window")

    df = resample_ohlcv(sub, args.tf)
    print(f"Window: {args.start} -> {args.end}  TF={args.tf}  bars={len(df)}\n")

    swings = find_swings(df, window=args.window)
    print(f"Detected {len(swings)} swing pivots:")
    for s in swings:
        marker = "H" if s.kind == "high" else "L"
        print(f"  {s.timestamp.strftime('%Y-%m-%d %H:%M')}  {marker}  {s.price:.2f}")

    fib = latest_fib_leg(df, window=args.window, min_swing_pct=args.min_swing)
    if fib is None:
        print("\nNo valid leg found.")
        return

    print(f"\n=== Latest leg ({fib.direction}) ===")
    print(f"  Start: {fib.leg_start.timestamp.strftime('%Y-%m-%d %H:%M')} "
          f"@ {fib.leg_start.price:.2f}  ({fib.leg_start.kind})")
    print(f"  End:   {fib.leg_end.timestamp.strftime('%Y-%m-%d %H:%M')} "
          f"@ {fib.leg_end.price:.2f}  ({fib.leg_end.kind})")
    print(f"  Size:  {fib.leg_size_pct:.2f}%  "
          f"({'SIGNIFICANT' if fib.is_significant else 'too small'})")
    print(f"\n  Retracements: {_fmt_levels(fib.retracements)}")
    print(f"  Extensions:   {_fmt_levels(fib.extensions)}")


if __name__ == "__main__":
    main()
