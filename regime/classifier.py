"""
RegimeClassifier — labels each 5-minute bar with the prevailing market regime.

Regimes:
    NO_TRADE        event window, extreme VIX, or admin halt
    WAIT            before 10:00 on gap days — reclassify later
    EXPIRY          0-DTE — route to spreads only
    TREND_UP_GAP    gap up + breakout above opening range + positive VWAP slope
    TREND_DOWN_GAP  gap down + breakdown below opening range + negative VWAP slope
    TREND_UP        ADX >= 25, range expansion, price above VWAP, VWAP sloping up
    TREND_DOWN      mirror of TREND_UP
    RANGE           fallback for balanced sessions
    CHOP            ADX < 18, range contraction, price pinned to VWAP

Design notes:
  - Trigger math (ADX / VWAP / ATR / OR levels) runs on Nifty futures, not spot.
    Spot Nifty has no real volume so VWAP is meaningless there.
  - Strike / PCR / VIX features run on spot — that's what option chains reference.
  - Hysteresis: a new label must sustain for `sustain_min` minutes before the
    classifier switches. NO_TRADE and EXPIRY override immediately.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional, TYPE_CHECKING

# pandas/numpy only needed for the DataFrame-based feature builders below,
# not for the state machine itself. Import lazily so the classifier can be
# unit-tested in environments without pandas installed.
if TYPE_CHECKING:
    import pandas as pd  # pragma: no cover

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    NO_TRADE = "NO_TRADE"
    WAIT = "WAIT"
    EXPIRY = "EXPIRY"
    TREND_UP_GAP = "TREND_UP_GAP"
    TREND_DOWN_GAP = "TREND_DOWN_GAP"
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    CHOP = "CHOP"


@dataclass
class ClassifierFeatures:
    """Snapshot of all features the classifier evaluates at a given timestamp."""
    ts: datetime
    gap_pct: float = 0.0
    or_range_pct: float = 0.0
    avg_or_range_pct: float = 0.0
    adx_15m: float = 0.0
    range_ratio: float = 1.0
    vwap_slope_30m: float = 0.0
    dist_from_vwap_pct: float = 0.0
    price: float = 0.0
    vwap: float = 0.0
    or_high: float = 0.0
    or_low: float = 0.0
    vix_level: float = 0.0
    vix_chg_15m: float = 0.0
    dte: int = 99
    event_flag: bool = False
    prev_day_close: float = 0.0

    def to_dict(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in self.__dict__.items()}


@dataclass
class ClassifierConfig:
    """Tunables for the classifier. Keep in lockstep with strategy_regime_master.json."""
    sustain_min: int = 15
    morning_lock_time: time = time(10, 15)
    no_entry_before: time = time(10, 0)
    gap_threshold: float = 0.005          # 0.5%
    vix_hard_halt: float = 28.0
    vix_chg_hard_halt: float = 0.20       # 20% jump in 15 min
    trend_adx_min: float = 25.0
    trend_range_ratio_min: float = 1.2
    chop_adx_max: float = 18.0
    chop_range_ratio_max: float = 0.7
    chop_vwap_dist_max: float = 0.0015    # 0.15%
    or_expansion_min: float = 0.25        # OR range must be >= 0.25 * avg OR


@dataclass
class _RegimeCandidate:
    regime: Regime
    first_seen: datetime

    def sustained(self, now: datetime, sustain_min: int) -> bool:
        return now - self.first_seen >= timedelta(minutes=sustain_min)


class RegimeClassifier:
    """
    Stateful classifier. Call `classify(features)` with a ClassifierFeatures
    for every 5m bar in chronological order; it returns the current regime.

    Hysteresis: state only flips to a new regime once the candidate has
    persisted for `config.sustain_min` minutes. EXPIRY and NO_TRADE override
    immediately — those are risk-off conditions we don't want to delay.
    """

    IMMEDIATE_REGIMES = {Regime.NO_TRADE, Regime.EXPIRY, Regime.WAIT}

    def __init__(self, config: Optional[ClassifierConfig] = None):
        self.config = config or ClassifierConfig()
        self._current: Optional[Regime] = None
        self._candidate: Optional[_RegimeCandidate] = None
        self._locked_morning: bool = False

    @property
    def current(self) -> Optional[Regime]:
        return self._current

    def classify(self, f: ClassifierFeatures) -> Regime:
        raw = self._raw_classify(f)

        # Immediate-override regimes skip the sustain check on ENTRY
        if raw in self.IMMEDIATE_REGIMES:
            self._commit(raw, f.ts)
            return raw

        # Exit from an immediate-override regime is also immediate —
        # once the halting condition clears, we don't force a 15m wait.
        if self._current in self.IMMEDIATE_REGIMES:
            self._commit(raw, f.ts)
            return raw

        # First observation
        if self._current is None:
            if f.ts.time() >= self.config.morning_lock_time:
                self._commit(raw, f.ts)
                self._locked_morning = True
                return raw
            # Before morning lock we still expose `raw` but don't fix state
            return raw

        # No change from current
        if raw == self._current:
            self._candidate = None
            return self._current

        # New candidate — accumulate or promote
        if self._candidate is None or self._candidate.regime != raw:
            self._candidate = _RegimeCandidate(regime=raw, first_seen=f.ts)
            return self._current

        if self._candidate.sustained(f.ts, self.config.sustain_min):
            logger.info(
                "regime: %s -> %s (sustained %dm at %s)",
                self._current, raw, self.config.sustain_min, f.ts.isoformat()
            )
            self._commit(raw, f.ts)

        return self._current

    def _commit(self, regime: Regime, ts: datetime) -> None:
        self._current = regime
        self._candidate = None

    def _raw_classify(self, f: ClassifierFeatures) -> Regime:
        c = self.config

        if f.event_flag:
            return Regime.NO_TRADE
        if f.vix_level > c.vix_hard_halt or f.vix_chg_15m > c.vix_chg_hard_halt:
            return Regime.NO_TRADE
        if f.ts.time() < c.no_entry_before and abs(f.gap_pct) > c.gap_threshold:
            return Regime.WAIT
        if f.dte == 0:
            return Regime.EXPIRY

        or_expanded = (
            f.avg_or_range_pct > 0
            and f.or_range_pct >= c.or_expansion_min * f.avg_or_range_pct
        )

        if (
            f.gap_pct >= c.gap_threshold
            and or_expanded
            and f.price > f.or_high
            and f.vwap_slope_30m > 0
        ):
            return Regime.TREND_UP_GAP
        if (
            f.gap_pct <= -c.gap_threshold
            and or_expanded
            and f.price < f.or_low
            and f.vwap_slope_30m < 0
        ):
            return Regime.TREND_DOWN_GAP

        if (
            f.adx_15m >= c.trend_adx_min
            and f.range_ratio >= c.trend_range_ratio_min
            and f.vwap_slope_30m > 0
            and f.price > f.vwap
        ):
            return Regime.TREND_UP
        if (
            f.adx_15m >= c.trend_adx_min
            and f.range_ratio >= c.trend_range_ratio_min
            and f.vwap_slope_30m < 0
            and f.price < f.vwap
        ):
            return Regime.TREND_DOWN

        if (
            f.adx_15m < c.chop_adx_max
            and f.range_ratio < c.chop_range_ratio_max
            and f.dist_from_vwap_pct < c.chop_vwap_dist_max
        ):
            return Regime.CHOP

        return Regime.RANGE


# -- Feature builders ---------------------------------------------------------
#
# These helpers compute the ClassifierFeatures dataclass from raw DataFrames.
# Kept separate from the classifier itself so they can be unit-tested and
# swapped out (e.g. for a different ATR/ADX implementation) without touching
# the state machine.


def compute_adx(df: "pd.DataFrame", period: int = 14) -> "pd.Series":
    """Wilder's ADX on OHLC dataframe indexed by timestamp."""
    import numpy as np
    import pandas as pd
    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)


def compute_session_vwap(df: "pd.DataFrame", session_start: time = time(9, 15)) -> "pd.Series":
    """Session-anchored VWAP. Requires a 'volume' column; zero volume yields NaN."""
    import numpy as np
    pv = (df["close"] * df["volume"]).groupby(df.index.date).cumsum()
    vv = df["volume"].groupby(df.index.date).cumsum()
    vwap = pv / vv.replace(0, np.nan)
    return vwap


def build_features(
    *,
    ts: datetime,
    fut_5m: "pd.DataFrame",
    fut_15m: "pd.DataFrame",
    spot_vix: "pd.DataFrame",
    prev_day_close: float,
    dte: int,
    event_flag: bool = False,
    avg_or_range_pct: float = 0.0025,
) -> ClassifierFeatures:
    """
    Build a ClassifierFeatures snapshot at `ts`.

    `fut_5m` and `fut_15m` must be timezone-aware OHLCV dataframes indexed by
    timestamp, covering at least the current session up to `ts`.
    `spot_vix` is the India VIX series (OHLC is fine, close is used).

    `avg_or_range_pct` is the 20-day average opening-range width as a fraction
    of mid-price. For Nifty it hovers around 0.2–0.3%. Caller supplies it.
    """
    today = ts.date()
    today_bars_5m = fut_5m[fut_5m.index.date == today]
    today_bars_15m = fut_15m[fut_15m.index.date == today]

    if today_bars_5m.empty:
        return ClassifierFeatures(ts=ts)

    or_slice = today_bars_5m.between_time("09:15", "09:29:59")
    or_high = float(or_slice["high"].max()) if not or_slice.empty else 0.0
    or_low = float(or_slice["low"].min()) if not or_slice.empty else 0.0
    or_mid = (or_high + or_low) / 2 if or_high and or_low else 0.0
    or_range_pct = (or_high - or_low) / or_mid if or_mid else 0.0

    open_0915 = float(or_slice.iloc[0]["open"]) if not or_slice.empty else 0.0
    gap_pct = (open_0915 - prev_day_close) / prev_day_close if prev_day_close else 0.0

    price = float(today_bars_5m.iloc[-1]["close"])

    vwap_series = compute_session_vwap(today_bars_5m)
    vwap_now = float(vwap_series.iloc[-1]) if not vwap_series.empty else price
    vwap_30m_ago = float(vwap_series.iloc[-7]) if len(vwap_series) >= 7 else vwap_now
    vwap_slope_30m = (vwap_now - vwap_30m_ago) / price if price else 0.0
    dist_from_vwap_pct = abs(price - vwap_now) / vwap_now if vwap_now else 0.0

    if len(today_bars_15m) >= 14:
        adx_series = compute_adx(today_bars_15m, period=14)
        adx_15m = float(adx_series.iloc[-1]) if not adx_series.empty else 0.0
    else:
        adx_15m = 0.0

    today_range = today_bars_5m["high"].max() - today_bars_5m["low"].min()
    # naive range ratio: today's range so far vs the OR expansion we'd expect
    range_ratio = (today_range / (or_high - or_low)) if (or_high - or_low) else 0.0

    vix_today = spot_vix[spot_vix.index.date == today]
    vix_level = float(vix_today["close"].iloc[-1]) if not vix_today.empty else 0.0
    if len(vix_today) >= 4:
        vix_15m_ago = float(vix_today["close"].iloc[-4])
        vix_chg_15m = (vix_level - vix_15m_ago) / vix_15m_ago if vix_15m_ago else 0.0
    else:
        vix_chg_15m = 0.0

    return ClassifierFeatures(
        ts=ts,
        gap_pct=gap_pct,
        or_range_pct=or_range_pct,
        avg_or_range_pct=avg_or_range_pct,
        adx_15m=adx_15m,
        range_ratio=range_ratio,
        vwap_slope_30m=vwap_slope_30m,
        dist_from_vwap_pct=dist_from_vwap_pct,
        price=price,
        vwap=vwap_now,
        or_high=or_high,
        or_low=or_low,
        vix_level=vix_level,
        vix_chg_15m=vix_chg_15m,
        dte=dte,
        event_flag=event_flag,
        prev_day_close=prev_day_close,
    )
