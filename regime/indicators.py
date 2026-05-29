"""
IndicatorTracker — maintains per-day OHLC bars + indicators from streaming
spot ticks for use in the live dispatcher.

The live bot only has 1-minute spot pricing (and option chain). To run
the new tactics (TrendPullback, ORB) we need EMA9, EMA21, ATR, OR levels
and recent-bar history. This tracker maintains them incrementally from
periodic spot updates, with no dependency on a heavy DataFrame at run time.

Usage in the live loop (main.py):

    tracker = IndicatorTracker()
    tracker.start_day(today, prev_day_close=24580.0)

    # On every periodic spot poll (every ~60s in the live bot):
    tracker.on_spot_tick(now, spot_price)

    # Whenever the dispatcher needs context for a tactic decision:
    snap = tracker.snapshot()  # dict with day_open / day_high / day_low /
                               # or_high / or_low / ema9 / ema21 / atr_5m /
                               # bar / prev_bar / recent_lows / recent_highs

The tracker buckets ticks into 5-minute bars by floor-dividing the
timestamp's minute. The current (in-progress) 5m bar is exposed live;
finalized bars feed the indicator series.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional


@dataclass
class _Bar:
    ts: datetime          # bar OPEN time (multiple of 5 min)
    open: float
    high: float
    low: float
    close: float


def _floor_5m(dt: datetime) -> datetime:
    minute = (dt.minute // 5) * 5
    return dt.replace(minute=minute, second=0, microsecond=0)


class IndicatorTracker:
    """Stateful tracker — feed it spot ticks, query it for indicator values."""

    EMA9_LEN = 9
    EMA21_LEN = 21
    ATR_LEN = 14

    def __init__(self):
        self.day: Optional[date] = None
        self.prev_day_close: float = 0.0
        self.day_open: float = 0.0
        self.day_high: float = 0.0
        self.day_low: float = 0.0
        self.or_high: float = 0.0
        self.or_low: float = 0.0
        self.or_volume_sum: float = 0.0   # always 0 on spot — kept for API parity
        self.or_bar_count: int = 0

        self.bars_5m: list[_Bar] = []
        self.current_bar: Optional[_Bar] = None
        self.ema9: float = 0.0
        self.ema21: float = 0.0
        self.atr_5m: float = 0.0

        # Lightweight ATR via Wilder smoothing on True Range
        self._tr_smoothed: float = 0.0

        # Raw spot tick history for trend-confidence and rejection signals.
        # Deque caps at ~75 entries (75 min) since on_spot_tick fires roughly
        # once per minute in the live loop. We only need 60 min lookback.
        self.tick_history: deque = deque(maxlen=75)

    # --- lifecycle -----------------------------------------------------

    def start_day(self, d: date, prev_day_close: float = 0.0) -> None:
        self.day = d
        self.prev_day_close = prev_day_close
        self.day_open = 0.0
        self.day_high = 0.0
        self.day_low = 0.0
        self.or_high = 0.0
        self.or_low = 0.0
        self.or_bar_count = 0
        self.bars_5m.clear()
        self.current_bar = None
        self.ema9 = 0.0
        self.ema21 = 0.0
        self.atr_5m = 0.0
        self._tr_smoothed = 0.0
        self.tick_history.clear()

    # --- ingest --------------------------------------------------------

    def on_spot_tick(self, ts: datetime, price: float) -> None:
        if price <= 0:
            return
        if self.day is None:
            self.start_day(ts.date())
        # Raw tick history (used by trend_confidence_score + rejection_at_level)
        self.tick_history.append((ts, price))
        # Day OHLC
        if self.day_open == 0.0:
            self.day_open = price
            self.day_high = price
            self.day_low = price
        else:
            if price > self.day_high:
                self.day_high = price
            if price < self.day_low:
                self.day_low = price

        # 5-min bar accumulation
        bar_ts = _floor_5m(ts)
        if self.current_bar is None or self.current_bar.ts != bar_ts:
            # Finalize previous bar if any
            if self.current_bar is not None:
                self._finalize_bar(self.current_bar)
            self.current_bar = _Bar(
                ts=bar_ts, open=price, high=price, low=price, close=price,
            )
        else:
            if price > self.current_bar.high:
                self.current_bar.high = price
            if price < self.current_bar.low:
                self.current_bar.low = price
            self.current_bar.close = price

        # Opening Range tracking — bars whose open-time is in [09:15, 09:30)
        if time(9, 15) <= bar_ts.time() < time(9, 30):
            if self.or_high == 0.0 or price > self.or_high:
                self.or_high = price
            if self.or_low == 0.0 or price < self.or_low:
                self.or_low = price
            self.or_bar_count = max(self.or_bar_count, 1)

    # --- internals -----------------------------------------------------

    def _finalize_bar(self, bar: _Bar) -> None:
        self.bars_5m.append(bar)

        # EMA update
        if self.ema9 == 0.0:
            self.ema9 = bar.close
        else:
            k = 2 / (self.EMA9_LEN + 1)
            self.ema9 = bar.close * k + self.ema9 * (1 - k)
        if self.ema21 == 0.0:
            self.ema21 = bar.close
        else:
            k = 2 / (self.EMA21_LEN + 1)
            self.ema21 = bar.close * k + self.ema21 * (1 - k)

        # ATR via Wilder smoothing
        prev_close = self.bars_5m[-2].close if len(self.bars_5m) >= 2 else bar.close
        tr = max(
            bar.high - bar.low,
            abs(bar.high - prev_close),
            abs(bar.low - prev_close),
        )
        if self._tr_smoothed == 0.0:
            self._tr_smoothed = tr
        else:
            alpha = 1 / self.ATR_LEN
            self._tr_smoothed = tr * alpha + self._tr_smoothed * (1 - alpha)
        self.atr_5m = self._tr_smoothed

    # --- new signals (added 2026-05-17 — validated cross-day) -----------

    def trend_confidence_score(self, window_min: int = 30) -> tuple[float, str]:
        """Signal-to-noise ratio for the recent directional move.

        score = |spot_move_over_window| / (rolling_stdev_log_returns * sqrt(window))

        - score < 1.0 : noise / chop — do not trade
        - 1.0-2.0    : weak signal — small size only
        - 2.0-3.0    : clean trend — full size
        - > 3.0      : high conviction

        Returns (score, direction) where direction is "UP" / "DOWN" / "FLAT".
        Returns (0, "FLAT") if there is not enough history yet.
        """
        if len(self.tick_history) < window_min + 1:
            return 0.0, "FLAT"
        # Take last (window_min + 1) ticks so we have window_min log-returns
        recent = list(self.tick_history)[-(window_min + 1):]
        prices = [p for _, p in recent]
        log_rets = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0 and prices[i] > 0:
                log_rets.append(math.log(prices[i] / prices[i - 1]))
        if len(log_rets) < 3:
            return 0.0, "FLAT"
        mean_r = sum(log_rets) / len(log_rets)
        variance = sum((r - mean_r) ** 2 for r in log_rets) / (len(log_rets) - 1)
        stdev = math.sqrt(variance)
        noise_floor = stdev * math.sqrt(window_min) * prices[-1]
        if noise_floor <= 0:
            return 0.0, "FLAT"
        move = prices[-1] - prices[0]
        score = abs(move) / noise_floor
        direction = "UP" if move > 0 else "DOWN" if move < 0 else "FLAT"
        return score, direction

    def rejection_at_level(self, level: float,
                           tolerance_pct: float = 0.001,
                           lookback_min: int = 60) -> int:
        """Count distinct 5-min buckets in the last `lookback_min` minutes
        where spot came within `tolerance_pct` of `level`.

        2 or more touches = the market has tested this level multiple times.
        On reversal days that pattern fires before ~56% of the day's
        eventual high/low (validated across 9 days on 2026-05-16).
        """
        if not self.tick_history or level <= 0:
            return 0
        threshold = level * tolerance_pct
        cutoff = self.tick_history[-1][0] - timedelta(minutes=lookback_min)
        buckets = set()
        for ts, price in self.tick_history:
            if ts < cutoff:
                continue
            if abs(price - level) <= threshold:
                buckets.add(_floor_5m(ts))
        return len(buckets)

    # --- query ---------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a snapshot suitable for building TacticState."""
        bar = self.current_bar
        prev_bar = self.bars_5m[-1] if self.bars_5m else None
        recent_3 = self.bars_5m[-3:]
        # IEF needs deeper OHLC history (last 50 finalized bars)
        recent_50 = self.bars_5m[-50:]
        # 2026-05-17: trend-confidence score + rejection counts at today's
        # extremes — surfaced in the snapshot so the dashboard and main.py
        # can read them without holding a tracker handle.
        trend_score, trend_dir = self.trend_confidence_score()
        rej_at_high = self.rejection_at_level(self.day_high) if self.day_high else 0
        rej_at_low = self.rejection_at_level(self.day_low) if self.day_low else 0
        return {
            "day": self.day,
            "prev_day_close": self.prev_day_close,
            "day_open": self.day_open,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "or_high": self.or_high,
            "or_low": self.or_low,
            "ema9_5m": self.ema9,
            "ema21_5m": self.ema21,
            "atr_5m": self.atr_5m,
            "bar_open": bar.open if bar else 0.0,
            "bar_high": bar.high if bar else 0.0,
            "bar_low": bar.low if bar else 0.0,
            "bar_close": bar.close if bar else 0.0,
            "bar_volume": 0.0,    # spot has no volume
            "prev_bar_open": prev_bar.open if prev_bar else 0.0,
            "prev_bar_high": prev_bar.high if prev_bar else 0.0,
            "prev_bar_low": prev_bar.low if prev_bar else 0.0,
            "prev_bar_close": prev_bar.close if prev_bar else 0.0,
            "recent_5m_lows": tuple(b.low for b in recent_3),
            "recent_5m_highs": tuple(b.high for b in recent_3),
            "recent_5m_bars": tuple(
                (b.ts, b.open, b.high, b.low, b.close) for b in recent_50
            ),
            "trend_confidence": trend_score,
            "trend_direction": trend_dir,
            "rejection_at_high": rej_at_high,
            "rejection_at_low": rej_at_low,
        }
