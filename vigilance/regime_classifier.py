"""Lightweight regime self-diagnostic.

Runs after 2 consecutive losses to answer: is the market trending or
chopping right now? Uses only data already in process memory — spot
history (1-min ticks) and the SignalEngine's PCR/OI deque from PR 1 —
so it can be called cheaply from the trade exit path.

This is intentionally NOT regime/classifier.py. That one is heavyweight
(pandas DataFrames, ADX on futures bars) and runs at dispatcher level.
This one is dirt-cheap and fits the "I just got punched twice, what is
this market actually doing?" use case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence


@dataclass
class RegimeReading:
    verdict: str          # 'TRENDING_UP' | 'TRENDING_DOWN' | 'CHOP' | 'UNCLEAR'
    direction: Optional[str]  # 'CE' | 'PE' | None
    reasons: List[str] = field(default_factory=list)

    @property
    def is_chop(self) -> bool:
        return self.verdict == "CHOP"

    @property
    def is_trending(self) -> bool:
        return self.verdict in ("TRENDING_UP", "TRENDING_DOWN")


# Decision thresholds. Tuned conservatively — this classifier only fires
# after 2 losses, so we'd rather call CHOP and stop than mis-call TREND
# and bleed more.
RANGE_TREND_MIN_PCT      = 0.005   # >= 0.5% range over 60m to even consider trending
RANGE_CHOP_MAX_PCT       = 0.004   # <= 0.4% range over 60m strongly suggests chop
DRIFT_TREND_MIN_PCT      = 0.002   # >= 0.2% drift in last 30m for trend conviction
PCR_STD_TREND_MAX        = 0.10    # PCR stddev tight → directional regime
PCR_STD_CHOP_MIN         = 0.15    # PCR stddev wide → oscillating chop


def _spots_window(spot_history: Sequence, minutes: int) -> List[float]:
    """Return last `minutes` spot values from spot_history.
    spot_history items are dicts with 'spot' key (per data_fetcher contract)."""
    if not spot_history:
        return []
    n = min(minutes, len(spot_history))
    return [float(item["spot"]) for item in list(spot_history)[-n:]
            if isinstance(item, dict) and "spot" in item]


def _stddev(values: Iterable[float]) -> float:
    vals = list(values)
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return var ** 0.5


def classify_regime(
    spot_history: Sequence,
    pcr_oi_history: Sequence,
) -> RegimeReading:
    """Classify current market regime from in-memory data.

    Args:
        spot_history: list of dicts with 'spot' key (data_fetcher format)
        pcr_oi_history: iterable of (timestamp, pcr, total_ce_oi, total_pe_oi)
                        — typically SignalEngine._history from PR 1.

    Returns:
        RegimeReading. Verdict 'UNCLEAR' is the safe default when there
        isn't enough data — caller should treat that as "wait, don't act."
    """
    reasons: List[str] = []

    # --- Spot range over last 60 min ---
    spots_60 = _spots_window(spot_history, 60)
    if len(spots_60) < 30:
        return RegimeReading("UNCLEAR", None,
                             [f"only {len(spots_60)} spot samples (<30) — warmup"])

    spot_now = spots_60[-1]
    spot_high = max(spots_60)
    spot_low = min(spots_60)
    range_pct = (spot_high - spot_low) / spot_now if spot_now > 0 else 0.0
    reasons.append(f"60m range {range_pct*100:.2f}% (high {spot_high:.0f}, low {spot_low:.0f})")

    # --- Drift: avg(last 5) vs avg(30..25 min ago) ---
    if len(spots_60) >= 30:
        recent_avg = sum(spots_60[-5:]) / 5
        earlier_avg = sum(spots_60[-30:-25]) / 5
        drift_pct = (recent_avg - earlier_avg) / spot_now if spot_now > 0 else 0.0
    else:
        drift_pct = 0.0
    reasons.append(f"30m drift {drift_pct*100:+.2f}% ({earlier_avg:.0f} -> {recent_avg:.0f})"
                   if len(spots_60) >= 30 else "drift: insufficient history")

    # --- PCR variance over last 6 samples (~30 min at 5-min cadence,
    # or ~6 min at 1-min cadence — either way it captures recent oscillation) ---
    pcr_samples = [p for (_ts, p, _ce, _pe) in list(pcr_oi_history)[-6:]]
    pcr_std = _stddev(pcr_samples) if len(pcr_samples) >= 6 else 0.0
    reasons.append(f"PCR stddev (last 6) {pcr_std:.3f}")

    # --- Decision matrix ---
    chop_signals = 0
    trend_signals = 0
    if range_pct < RANGE_CHOP_MAX_PCT:
        chop_signals += 1
    if pcr_std > PCR_STD_CHOP_MIN:
        chop_signals += 1
    if range_pct >= RANGE_TREND_MIN_PCT:
        trend_signals += 1
    if abs(drift_pct) >= DRIFT_TREND_MIN_PCT:
        trend_signals += 1
    if pcr_std < PCR_STD_TREND_MAX:
        trend_signals += 1

    # Two trend signals + clear directional drift → TRENDING
    if trend_signals >= 2 and abs(drift_pct) >= DRIFT_TREND_MIN_PCT:
        if drift_pct > 0:
            return RegimeReading("TRENDING_UP", "CE", reasons + ["verdict: TRENDING_UP — only CE allowed"])
        return RegimeReading("TRENDING_DOWN", "PE", reasons + ["verdict: TRENDING_DOWN — only PE allowed"])

    # Two chop signals → CHOP
    if chop_signals >= 2:
        return RegimeReading("CHOP", None, reasons + ["verdict: CHOP — stand aside"])

    # One signal of each / mixed → UNCLEAR
    return RegimeReading("UNCLEAR", None, reasons + ["verdict: UNCLEAR — re-check after cooldown"])
