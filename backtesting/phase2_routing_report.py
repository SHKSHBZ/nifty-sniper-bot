"""
Phase 2 — routing report.

For each trading day in the synthetic spot series, walk the regime
classifier forward bar-by-bar and ask the StrategyRouter which tactic
would fire. Then show, per day:

  - opening price / closing price / high / low (so user can eyeball)
  - regime transitions during the day
  - the tactic that would be armed in each regime window
  - whether the existing live bot (OI_WALL_MEAN_REVERSION) would have
    been armed, as a baseline comparison

This does NOT simulate P&L. It answers:
  "If we flip the regime-switching system on, which days / which windows
   does our existing bot (RANGE-tactic) get armed, and what other
   tactics get proposed?"

That is the first level of comparison a trader cares about before
investing in a full trade-simulation run.
"""
from __future__ import annotations

import sys
from datetime import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.classifier import (
    RegimeClassifier,
    ClassifierConfig,
    Regime,
)
from regime.router import StrategyRouter, Tactic
from backtesting.backtest_regime_phase1 import (
    resample,
    previous_day_close,
    build_feature_for_bar,
)

SPOT_CSV = ROOT / "data" / "NIFTY_SPOT_SYNTHETIC_1min.csv"
OUTPUT_MD = ROOT / "reports" / "phase2_routing_report.md"


def main() -> None:
    df = pd.read_csv(SPOT_CSV)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    # volume is already 1 for synthetic spot

    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))
    router = StrategyRouter()

    lines: list[str] = []
    lines.append("# Phase 2 — Regime Routing Report\n")
    lines.append(f"Source: `{SPOT_CSV.name}` (synthetic spot via put-call parity on option chain)\n")
    lines.append(f"Range: {df.index.min()} to {df.index.max()}\n")
    lines.append("")
    lines.append("## Per-Day Breakdown\n")

    totals: dict[Tactic, int] = {}

    for day in sorted({d for d in df.index.date}):
        day_1m = df[df.index.date == day]
        day_5m = resample(day_1m, "5min")
        day_15m = resample(day_1m, "15min")
        prev_close = previous_day_close(df, day)

        # Reset intraday classifier state per trading day
        classifier._current = None
        classifier._candidate = None

        regime_timeline: list[tuple[pd.Timestamp, Regime]] = []

        for ts, _ in day_5m.iterrows():
            if ts.time() < time(9, 30) or ts.time() > time(14, 30):
                continue
            feat = build_feature_for_bar(ts, day_5m, day_15m, prev_close)
            regime = classifier.classify(feat)
            regime_timeline.append((ts, regime))

        # Collapse consecutive identical regimes
        segments: list[tuple[pd.Timestamp, pd.Timestamp, Regime]] = []
        if regime_timeline:
            start_ts, current = regime_timeline[0]
            for ts, r in regime_timeline[1:]:
                if r != current:
                    segments.append((start_ts, ts, current))
                    start_ts, current = ts, r
            segments.append((start_ts, regime_timeline[-1][0], current))

        day_open = float(day_1m.iloc[0]["open"])
        day_close = float(day_1m.iloc[-1]["close"])
        day_high = float(day_1m["high"].max())
        day_low = float(day_1m["low"].min())
        day_pct = (day_close - day_open) / day_open * 100

        lines.append(f"\n### {day}")
        lines.append("")
        lines.append(f"Open: {day_open:,.1f} | Close: {day_close:,.1f} | "
                     f"Move: {day_pct:+.2f}% | Range: [{day_low:,.1f}, {day_high:,.1f}]\n")
        lines.append("")
        lines.append("| Window | Regime | Tactic Armed | Direction |")
        lines.append("|---|---|---|---|")

        for start, end, regime in segments:
            decision = router.route(regime)
            tactic = decision.tactic
            totals[tactic] = totals.get(tactic, 0) + 1
            dirn = decision.direction or "-"
            lines.append(
                f"| {start.strftime('%H:%M')} – {end.strftime('%H:%M')} "
                f"| {regime.value} | {tactic.value} | {dirn} |"
            )

    # --- Aggregate tactic dispatch -----------------------------------------
    lines.append("\n## Aggregate Tactic Dispatch (regime-segment count)\n")
    lines.append("| Tactic | Segments Armed |")
    lines.append("|---|---:|")
    for t, c in sorted(totals.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {t.value} | {c} |")

    # --- Baseline comparison -----------------------------------------------
    lines.append("\n## Baseline vs Regime-Switched Arming\n")
    lines.append("_Baseline = existing bot always armed during the session._")
    lines.append("_Regime-switched = existing bot (`OI_WALL_MEAN_REVERSION`) armed only during `RANGE` segments._\n")

    baseline_armed_segments = sum(totals.values())   # every segment in-session
    range_armed_segments = totals.get(Tactic.OI_WALL_MEAN_REVERSION, 0)
    pct_reduction = 0.0
    if baseline_armed_segments:
        pct_reduction = 100 * (1 - range_armed_segments / baseline_armed_segments)

    lines.append(f"- Total in-session regime segments: **{baseline_armed_segments}**")
    lines.append(f"- Segments where existing bot would fire under regime switching: **{range_armed_segments}**")
    lines.append(f"- Reduction in armed-time: **{pct_reduction:.0f}%**")
    lines.append("")
    lines.append("### Implication")
    lines.append("")
    lines.append(
        "Under the regime-switched system, the existing OI-wall mean-reversion bot "
        "fires only during RANGE segments. On this 4-day sample, that reduces its "
        "trading surface by the percentage above. **The hypothesis being tested is that "
        "the trades skipped were the losing ones — i.e. your existing bot bleeds P&L "
        "during trend segments where mean reversion fails.**"
    )
    lines.append("")
    lines.append("A full trade-simulation P&L comparison requires:")
    lines.append("- real futures data (for proper ADX / VWAP / volume-aware trigger math)")
    lines.append("- real VIX (for Gate 0 VIX filter)")
    lines.append("- OR continued use of synthetic spot (this run) as a directional probe")
    lines.append("")
    lines.append("### Sample Size Caveat")
    lines.append("")
    lines.append(
        "**n = 4 trading days**. This cannot prove or disprove the regime hypothesis "
        "statistically. It only validates that the machinery (classifier + router) "
        "produces sensible outputs. Conclusions require ≥ 3 months of data."
    )

    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nWrote: {OUTPUT_MD}")


if __name__ == "__main__":
    main()
