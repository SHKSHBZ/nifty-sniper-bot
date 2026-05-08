"""Lightweight candlestick pattern detector.

Used by Pillar 1 of the strategy spec. Operates on a single bar (or
prev+current bar) of OHLC data — caller is responsible for resampling
1-min spot to 5-min or 15-min before passing in.

All functions return True/False. Tolerance is in absolute price units
(pts for index spot). For NIFTY use ~5pt min body; SENSEX ~15pt.

Bullish patterns (use at SUPPORT for CE entry):
  - is_hammer
  - is_bullish_engulfing(prev, cur)
  - is_piercing(prev, cur)
  - is_bullish_pin

Bearish patterns (use at RESISTANCE for PE entry):
  - is_shooting_star
  - is_bearish_engulfing(prev, cur)
  - is_dark_cloud(prev, cur)
  - is_bearish_pin
"""
from __future__ import annotations


def _body(o, c):
    return abs(c - o)


def _lower_shadow(o, h, l, c):
    return min(o, c) - l


def _upper_shadow(o, h, l, c):
    return h - max(o, c)


def is_hammer(o, h, l, c, min_body=2.0) -> bool:
    """Bullish reversal: long lower shadow, small body at top."""
    body = _body(o, c)
    if body < min_body:
        return False
    lower = _lower_shadow(o, h, l, c)
    upper = _upper_shadow(o, h, l, c)
    return lower >= 2 * body and upper <= body * 0.5


def is_shooting_star(o, h, l, c, min_body=2.0) -> bool:
    """Bearish reversal: long upper shadow, small body at bottom."""
    body = _body(o, c)
    if body < min_body:
        return False
    upper = _upper_shadow(o, h, l, c)
    lower = _lower_shadow(o, h, l, c)
    return upper >= 2 * body and lower <= body * 0.5


def is_bullish_engulfing(prev_o, prev_h, prev_l, prev_c,
                         o, h, l, c) -> bool:
    """Prev bar red and small; current bar green and engulfs prev body."""
    if prev_c >= prev_o or c <= o:
        return False
    return o <= prev_c and c >= prev_o


def is_bearish_engulfing(prev_o, prev_h, prev_l, prev_c,
                         o, h, l, c) -> bool:
    """Prev bar green; current bar red and engulfs prev body."""
    if prev_c <= prev_o or c >= o:
        return False
    return o >= prev_c and c <= prev_o


def is_piercing(prev_o, prev_h, prev_l, prev_c, o, h, l, c) -> bool:
    """Bullish: red bar, current opens below prev low, closes above prev midpoint."""
    if prev_c >= prev_o or c <= o:
        return False
    midpoint = (prev_o + prev_c) / 2
    return o < prev_l and c >= midpoint


def is_dark_cloud(prev_o, prev_h, prev_l, prev_c, o, h, l, c) -> bool:
    """Bearish: green bar, current opens above prev high, closes below prev midpoint."""
    if prev_c <= prev_o or c >= o:
        return False
    midpoint = (prev_o + prev_c) / 2
    return o > prev_h and c <= midpoint


def is_bullish_pin(o, h, l, c, min_body=2.0) -> bool:
    """Pin bar with long lower wick — same as hammer but allows bigger upper shadow."""
    body = _body(o, c)
    if body < min_body:
        return False
    lower = _lower_shadow(o, h, l, c)
    return lower >= 2.5 * body


def is_bearish_pin(o, h, l, c, min_body=2.0) -> bool:
    body = _body(o, c)
    if body < min_body:
        return False
    upper = _upper_shadow(o, h, l, c)
    return upper >= 2.5 * body


def any_bullish_pattern(prev, cur, min_body=2.0) -> tuple[bool, str]:
    """Check any bullish pattern. cur is the just-closed bar.
    `prev` may be None (single-bar patterns only).
    Returns (matched, name)."""
    o, h, l, c = cur
    if is_hammer(o, h, l, c, min_body):
        return True, "HAMMER"
    if is_bullish_pin(o, h, l, c, min_body):
        return True, "BULL_PIN"
    if prev is not None:
        po, ph, pl, pc = prev
        if is_bullish_engulfing(po, ph, pl, pc, o, h, l, c):
            return True, "BULL_ENGULF"
        if is_piercing(po, ph, pl, pc, o, h, l, c):
            return True, "PIERCING"
    return False, ""


def any_bearish_pattern(prev, cur, min_body=2.0) -> tuple[bool, str]:
    o, h, l, c = cur
    if is_shooting_star(o, h, l, c, min_body):
        return True, "SHOOTING_STAR"
    if is_bearish_pin(o, h, l, c, min_body):
        return True, "BEAR_PIN"
    if prev is not None:
        po, ph, pl, pc = prev
        if is_bearish_engulfing(po, ph, pl, pc, o, h, l, c):
            return True, "BEAR_ENGULF"
        if is_dark_cloud(po, ph, pl, pc, o, h, l, c):
            return True, "DARK_CLOUD"
    return False, ""
