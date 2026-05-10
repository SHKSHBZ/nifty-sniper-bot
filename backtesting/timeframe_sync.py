"""Resample 1-min OHLCV bars to higher timeframes (5/15/30/60/240 min).

Per nifty_trading_system_config.json the active timeframes are
5M, 15M, 30M, 1H, 4H. Lower TF = entry timing; higher TF = trend bias.

Critical detail: NSE/BSE sessions run 09:15 -> 15:30 IST. Naive pandas
resample('5min') anchors bins on midnight, which produces partial bars
at session open (09:15-09:20) that mix with the previous session's
overnight gap. We use `origin='start_day'` + `offset='9h15min'` so bins
align cleanly to the session.

Also: the 4H bin lands oddly because the session is 6h15m long. We
treat 4H as "first 4h of session (09:15-13:15)" + "last 2h15m
(13:15-15:30, marked as a partial last bin)". The caller can drop
partial bars by setting drop_partial=True.

Usage:
    from backtesting.timeframe_sync import resample_ohlcv

    df_15m = resample_ohlcv(df_1m, "15min")
    bars = resample_all(df_1m, ["5min", "15min", "30min", "1h", "4h"])

CLI demo:
    python -m backtesting.timeframe_sync --tf 15min --start 2025-08-25 --end 2025-08-25
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SPOT_FILE = DATA / "NIFTY50_INDEX_1minute.csv"
VOL_FILE = DATA / "NIFTY_FUT_volume_1minute.csv"

# Map user-friendly names to pandas offset aliases.
TF_ALIASES = {
    "5m": "5min", "5min": "5min",
    "15m": "15min", "15min": "15min",
    "30m": "30min", "30min": "30min",
    "1h": "1h", "60m": "1h", "60min": "1h",
    "4h": "4h", "240m": "4h", "240min": "4h",
}

SESSION_START = "9h15min"
SESSION_END_HHMM = (15, 30)


def _normalize_tf(tf: str) -> str:
    key = tf.lower()
    if key not in TF_ALIASES:
        raise ValueError(
            f"Unknown timeframe {tf!r}. Supported: {sorted(set(TF_ALIASES))}"
        )
    return TF_ALIASES[key]


def resample_ohlcv(
    df: pd.DataFrame,
    tf: str,
    *,
    drop_partial: bool = True,
) -> pd.DataFrame:
    """Resample 1-min OHLCV (and futures_volume if present) to `tf` bars.

    Expects df indexed by tz-aware timestamp with columns:
      open, high, low, close [, volume, futures_volume, ...]

    Bins are session-aligned (anchored to 09:15 IST). Optional non-OHLCV
    numeric columns are summed (good default for volume); pass them
    pre-aggregated if you need a different rule.
    """
    if df.empty:
        return df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("df must have a DatetimeIndex")

    tf = _normalize_tf(tf)

    agg: dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    # Sum any extra numeric columns (volume, futures_volume, open_interest, ...)
    for col in df.columns:
        if col in agg:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            agg[col] = "sum"
        else:
            agg[col] = "last"  # e.g. contract_used label

    out = (df.resample(tf, origin="start_day", offset=SESSION_START, label="left")
             .agg(agg)
             .dropna(subset=["open"]))

    if drop_partial:
        # A bin is "complete" if it contains a full TF window of trading
        # minutes inside one session. Quick proxy: keep bins whose START
        # time is within the session AND whose end fits before 15:30 IST.
        tf_minutes = pd.Timedelta(tf).total_seconds() / 60
        out = out[out.index.map(lambda t: _bin_fits_in_session(t, tf_minutes))]

    return out


def _bin_fits_in_session(start: pd.Timestamp, tf_minutes: float) -> bool:
    """Return True if the bin starting at `start` ends at or before 15:30 IST."""
    end_h, end_m = SESSION_END_HHMM
    session_close = start.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    bin_end = start + pd.Timedelta(minutes=tf_minutes)
    return bin_end <= session_close + pd.Timedelta(minutes=1)


def resample_all(
    df: pd.DataFrame,
    timeframes: list[str],
    **kwargs,
) -> dict[str, pd.DataFrame]:
    """Convenience: resample once into a {tf: df} dict."""
    return {tf: resample_ohlcv(df, tf, **kwargs) for tf in timeframes}


def load_aligned_1min() -> pd.DataFrame:
    """Same loader as volume_profile.load_aligned() — kept here so this
    module is independently usable. Inner-joins spot OHLC + futures volume.
    """
    spot = pd.read_csv(SPOT_FILE)
    spot["timestamp"] = pd.to_datetime(spot["timestamp"])
    spot = spot.set_index("timestamp")[["open", "high", "low", "close"]]

    if VOL_FILE.exists():
        vol = pd.read_csv(VOL_FILE)
        vol["timestamp"] = pd.to_datetime(vol["timestamp"])
        vol = vol.set_index("timestamp")[["futures_volume"]]
        return spot.join(vol, how="left").sort_index()
    return spot.sort_index()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tf", default="15min",
                   help="Target timeframe: 5min, 15min, 30min, 1h, 4h")
    p.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--keep-partial", action="store_true",
                   help="Keep partial bars (default drops them)")
    args = p.parse_args()

    df = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df.loc[start:end]
    print(f"Source: {len(sub):,} 1-min bars from {sub.index.min()} -> {sub.index.max()}")

    out = resample_ohlcv(sub, args.tf, drop_partial=not args.keep_partial)
    print(f"Resampled to {args.tf}: {len(out):,} bars\n")

    print(out.head(10).to_string())
    print(...)
    print(out.tail(5).to_string())


if __name__ == "__main__":
    main()
