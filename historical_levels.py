"""
Historical Levels Scanner
─────────────────────────
Identifies Swing Highs / Swing Lows from daily OHLC data and classifies
them as Buy-Side Liquidity (BSL) or Sell-Side Liquidity (SSL) zones.

Data is cached in a local CSV so we only download 60 days on the first
run; every subsequent day we just fetch the 1 missing candle.
"""

import csv
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("HistLevels")

CACHE_DIR = Path("data")
CACHE_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# LOCAL CSV CACHE
# ═══════════════════════════════════════════════════════════════

def _cache_path(index: str) -> Path:
    """Return the path to the local OHLC cache file for a given index."""
    return CACHE_DIR / f"historical_ohlc_{index.lower()}.csv"


def load_cache(index: str) -> list[dict]:
    """Load cached daily OHLC rows from disk."""
    path = _cache_path(index)
    if not path.exists():
        return []
    rows = []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "date": r["date"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
            })
    return rows


def save_cache(index: str, rows: list[dict]):
    """Write (or overwrite) the local OHLC cache."""
    path = _cache_path(index)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close"])
        writer.writeheader()
        writer.writerows(rows)


def get_missing_dates(cached: list[dict], lookback_days: int = 60) -> tuple[Optional[str], Optional[str]]:
    """
    Return (from_date, to_date) for the range we still need to fetch.
    If cache is empty or stale, returns the full 60-day range.
    If cache is fresh, returns only the gap (1-2 days typically).
    Returns (None, None) if no fetch is needed.
    """
    today = date.today()
    earliest_needed = today - timedelta(days=lookback_days)

    if not cached:
        return earliest_needed.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    last_cached_date = max(r["date"] for r in cached)
    last_dt = datetime.strptime(last_cached_date, "%Y-%m-%d").date()

    # If the cache already has today (or yesterday if today is a holiday),
    # we don't need to fetch anything.
    if last_dt >= today - timedelta(days=1):
        return None, None

    # Fetch from the day after the last cached date
    fetch_from = last_dt + timedelta(days=1)
    return fetch_from.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def merge_and_trim(cached: list[dict], new_rows: list[dict], lookback_days: int = 60) -> list[dict]:
    """Merge new rows into cache, deduplicate, sort, and trim to lookback window."""
    by_date = {r["date"]: r for r in cached}
    for r in new_rows:
        by_date[r["date"]] = r  # overwrite if duplicate

    cutoff = (date.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    merged = [r for r in by_date.values() if r["date"] >= cutoff]
    merged.sort(key=lambda r: r["date"])
    return merged


# ═══════════════════════════════════════════════════════════════
# SWING HIGH / LOW DETECTION
# ═══════════════════════════════════════════════════════════════

def find_swing_highs(candles: list[dict], lookback: int = 2) -> list[float]:
    """
    A Swing High is a candle whose High is greater than the Highs
    of the `lookback` candles on each side.
    """
    swings = []
    for i in range(lookback, len(candles) - lookback):
        h = candles[i]["high"]
        is_swing = True
        for j in range(1, lookback + 1):
            if candles[i - j]["high"] >= h or candles[i + j]["high"] >= h:
                is_swing = False
                break
        if is_swing:
            swings.append(h)
    return swings


def find_swing_lows(candles: list[dict], lookback: int = 2) -> list[float]:
    """
    A Swing Low is a candle whose Low is lower than the Lows
    of the `lookback` candles on each side.
    """
    swings = []
    for i in range(lookback, len(candles) - lookback):
        lo = candles[i]["low"]
        is_swing = True
        for j in range(1, lookback + 1):
            if candles[i - j]["low"] <= lo or candles[i + j]["low"] <= lo:
                is_swing = False
                break
        if is_swing:
            swings.append(lo)
    return swings


def cluster_levels(levels: list[float], tolerance: float = 30.0) -> list[float]:
    """
    Cluster nearby levels (within `tolerance` points) and return the
    average of each cluster. Stronger levels (more touches) naturally
    appear because multiple swing points cluster together.
    """
    if not levels:
        return []

    levels_sorted = sorted(levels)
    clusters: list[list[float]] = [[levels_sorted[0]]]

    for lv in levels_sorted[1:]:
        if abs(lv - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])

    # Return the average of each cluster, sorted by strength (cluster size) descending
    result = [(sum(c) / len(c), len(c)) for c in clusters]
    result.sort(key=lambda x: x[1], reverse=True)
    return [round(r[0], 2) for r in result[:5]]  # Top 5 strongest


# ═══════════════════════════════════════════════════════════════
# MAIN SCANNER
# ═══════════════════════════════════════════════════════════════

def scan(candles: list[dict], current_spot: float) -> dict:
    """
    Main entry point. Given a list of daily OHLC candles (sorted by date),
    returns a dict of historical levels with liquidity classification.
    """
    if len(candles) < 5:
        log.warning("[HIST] Not enough candle data for swing detection")
        return {
            "pdh": 0.0, "pdl": 0.0,
            "swing_highs": [], "swing_lows": [],
            "nearest_resistance": 0.0, "nearest_support": 0.0,
        }

    # Previous Day High / Low (last completed candle)
    prev = candles[-1]
    pdh = prev["high"]
    pdl = prev["low"]

    # Detect swings
    raw_highs = find_swing_highs(candles)
    raw_lows = find_swing_lows(candles)

    # Also add PDH/PDL and the overall 60-day high/low as key levels
    all_highs = raw_highs + [pdh]
    all_lows = raw_lows + [pdl]

    # Cluster and rank
    swing_highs = cluster_levels(all_highs, tolerance=30.0)
    swing_lows = cluster_levels(all_lows, tolerance=30.0)

    # Nearest resistance above current spot
    resistances_above = [h for h in swing_highs if h > current_spot]
    nearest_resistance = min(resistances_above) if resistances_above else 0.0

    # Nearest support below current spot
    supports_below = [s for s in swing_lows if s < current_spot]
    nearest_support = max(supports_below) if supports_below else 0.0

    result = {
        "pdh": pdh,
        "pdl": pdl,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
    }

    log.info(f"[HIST] PDH={pdh:.0f} PDL={pdl:.0f} | "
             f"Nearest Resistance={nearest_resistance:.0f} | "
             f"Nearest Support={nearest_support:.0f}")
    log.info(f"[HIST] Swing Highs={swing_highs}")
    log.info(f"[HIST] Swing Lows={swing_lows}")

    return result
