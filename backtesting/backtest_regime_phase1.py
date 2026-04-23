"""
Phase 1 — Regime classifier smoke test on local spot data.

What this DOES:
  - Loads NIFTY50_INDEX_1minute.csv
  - Resamples to 5m and 15m
  - Walks through each trading day from 09:30 to 14:30
  - Builds ClassifierFeatures and feeds them to RegimeClassifier
  - Prints a per-day regime timeline and aggregate counts

What this DOES NOT DO (requires more data):
  - Does NOT use futures bars — classifier uses spot as a proxy for trigger math.
    Impact: VWAP is imperfect because spot Nifty has volume=0. Reported regimes
    that depend on vwap_slope_30m / dist_from_vwap_pct are best-effort only.
  - Does NOT use VIX data — vix_level set to 15 (neutral), vix_chg_15m to 0.
    Impact: NO_TRADE-on-VIX-spike gate is disabled.
  - Does NOT simulate any trades — no P&L computation. That's Phase 2.

Usage:
    python backtesting/backtest_regime_phase1.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.classifier import (  # noqa: E402
    RegimeClassifier,
    ClassifierFeatures,
    ClassifierConfig,
    Regime,
    compute_adx,
    compute_session_vwap,
)

SPOT_CSV = ROOT / "data" / "NIFTY50_INDEX_1minute.csv"
OUTPUT_LOG = ROOT / "reports" / "regime_phase1_log.jsonl"
OUTPUT_SUMMARY = ROOT / "reports" / "regime_phase1_summary.md"


def load_spot() -> pd.DataFrame:
    df = pd.read_csv(SPOT_CSV)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    # Spot Nifty has no real volume — give it a synthetic constant so
    # VWAP math doesn't collapse. Result is a time-weighted average, which
    # is a known degraded substitute (real VWAP needs futures volume).
    if (df["volume"] == 0).all():
        df = df.copy()
        df["volume"] = 1
    return df


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    return df.resample(tf).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()


def previous_day_close(spot_1m: pd.DataFrame, day) -> float:
    prior = spot_1m[spot_1m.index.date < day]
    if prior.empty:
        # fallback: use first open of the current day
        today = spot_1m[spot_1m.index.date == day]
        return float(today.iloc[0]["open"])
    return float(prior.iloc[-1]["close"])


def build_feature_for_bar(
    ts: pd.Timestamp,
    day_5m: pd.DataFrame,
    day_15m: pd.DataFrame,
    prev_close: float,
) -> ClassifierFeatures:
    bars_5m_upto_now = day_5m[day_5m.index <= ts]
    bars_15m_upto_now = day_15m[day_15m.index <= ts]

    or_slice = bars_5m_upto_now.between_time("09:15", "09:29:59")
    or_high = float(or_slice["high"].max()) if not or_slice.empty else 0.0
    or_low = float(or_slice["low"].min()) if not or_slice.empty else 0.0
    or_mid = (or_high + or_low) / 2 if or_high and or_low else 0.0
    or_range_pct = (or_high - or_low) / or_mid if or_mid else 0.0

    open_0915 = float(or_slice.iloc[0]["open"]) if not or_slice.empty else 0.0
    gap_pct = (open_0915 - prev_close) / prev_close if prev_close else 0.0

    price = float(bars_5m_upto_now.iloc[-1]["close"])

    vwap_series = compute_session_vwap(bars_5m_upto_now)
    vwap_now = float(vwap_series.iloc[-1]) if not vwap_series.empty else price
    if len(vwap_series) >= 7:
        vwap_30m_ago = float(vwap_series.iloc[-7])
    else:
        vwap_30m_ago = vwap_now
    vwap_slope_30m = (vwap_now - vwap_30m_ago) / price if price else 0.0
    dist_from_vwap_pct = abs(price - vwap_now) / vwap_now if vwap_now else 0.0

    if len(bars_15m_upto_now) >= 14:
        adx_series = compute_adx(bars_15m_upto_now, period=14)
        adx_15m = float(adx_series.iloc[-1]) if not adx_series.empty else 0.0
    else:
        adx_15m = 0.0

    today_range = bars_5m_upto_now["high"].max() - bars_5m_upto_now["low"].min()
    range_ratio = today_range / (or_high - or_low) if (or_high - or_low) else 0.0

    return ClassifierFeatures(
        ts=ts.to_pydatetime(),
        gap_pct=gap_pct,
        or_range_pct=or_range_pct,
        avg_or_range_pct=0.0025,
        adx_15m=adx_15m,
        range_ratio=range_ratio,
        vwap_slope_30m=vwap_slope_30m,
        dist_from_vwap_pct=dist_from_vwap_pct,
        price=price,
        vwap=vwap_now,
        or_high=or_high,
        or_low=or_low,
        # STUBBED — no VIX data yet
        vix_level=15.0,
        vix_chg_15m=0.0,
        dte=3,
        event_flag=False,
        prev_day_close=prev_close,
    )


def main() -> None:
    OUTPUT_LOG.parent.mkdir(parents=True, exist_ok=True)

    spot_1m = load_spot()
    print(f"Loaded {len(spot_1m):,} 1-min spot bars from "
          f"{spot_1m.index.min()} → {spot_1m.index.max()}")

    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))

    all_labels: list[Regime] = []
    per_day: dict[str, list[tuple[str, Regime]]] = {}
    log_rows: list[dict] = []

    for day in sorted({d for d in spot_1m.index.date}):
        day_1m = spot_1m[spot_1m.index.date == day]
        day_5m = resample(day_1m, "5min")
        day_15m = resample(day_1m, "15min")

        prev_close = previous_day_close(spot_1m, day)
        day_labels: list[tuple[str, Regime]] = []

        # New day -> reset classifier state (intraday hysteresis only)
        classifier._current = None  # type: ignore[attr-defined]
        classifier._candidate = None  # type: ignore[attr-defined]

        for ts, _row in day_5m.iterrows():
            if ts.time() < time(9, 30):
                continue
            if ts.time() > time(14, 30):
                continue

            feat = build_feature_for_bar(ts, day_5m, day_15m, prev_close)
            regime = classifier.classify(feat)

            day_labels.append((ts.strftime("%H:%M"), regime))
            all_labels.append(regime)
            log_rows.append({
                "ts": ts.isoformat(),
                "regime": regime.value,
                **feat.to_dict(),
            })

        per_day[day.isoformat()] = day_labels

    # -- Console summary -----------------------------------------------------
    print("\n" + "=" * 70)
    print("REGIME TIMELINE (per trading day)")
    print("=" * 70)
    for day, labels in per_day.items():
        print(f"\n{day}:")
        # compress consecutive identical labels
        compressed = []
        prev = None
        for t, r in labels:
            if r != prev:
                compressed.append((t, r.value))
                prev = r
        for t, r in compressed:
            print(f"  {t}  →  {r}")

    print("\n" + "=" * 70)
    print("AGGREGATE REGIME DISTRIBUTION (5m bars classified)")
    print("=" * 70)
    counts = Counter(l.value for l in all_labels)
    total = sum(counts.values())
    for r, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {r:<16}  {c:4d}  ({100*c/total:5.1f}%)")
    print(f"  {'TOTAL':<16}  {total:4d}")

    # -- Artifacts -----------------------------------------------------------
    with OUTPUT_LOG.open("w") as f:
        for row in log_rows:
            f.write(json.dumps(row, default=str) + "\n")

    with OUTPUT_SUMMARY.open("w") as f:
        f.write("# Phase 1 Regime Classifier Smoke Test\n\n")
        f.write(f"- Source: `{SPOT_CSV.name}`\n")
        f.write(f"- Rows: {len(spot_1m):,}\n")
        f.write(f"- Range: {spot_1m.index.min()} to {spot_1m.index.max()}\n\n")
        f.write("## Known Limitations\n\n")
        f.write("- **No futures data**: spot used as proxy. VWAP degraded.\n")
        f.write("- **No VIX data**: VIX gate disabled (stubbed at 15.0).\n")
        f.write("- **No trade simulation**: classifier output only.\n\n")
        f.write("## Aggregate Regime Distribution\n\n")
        f.write("| Regime | Bars | Share |\n|---|---:|---:|\n")
        for r, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            f.write(f"| {r} | {c} | {100*c/total:.1f}% |\n")
        f.write("\n## Per-Day Timeline (regime changes only)\n\n")
        for day, labels in per_day.items():
            f.write(f"\n### {day}\n\n")
            prev = None
            for t, r in labels:
                if r != prev:
                    f.write(f"- `{t}` → **{r.value}**\n")
                    prev = r

    print(f"\nLog   : {OUTPUT_LOG.relative_to(ROOT)}")
    print(f"Report: {OUTPUT_SUMMARY.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
