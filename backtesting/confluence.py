"""Confluence scorer — wires Volume Profile, S/R, Fibonacci, FVG into one
score per decision point, per nifty_trading_system_config.json.

Active pillars in BACKTEST (market_depth is live-only and disabled here):
    sr_alignment              weight 1
    volume_node_alignment     weight 1
    approach_volume_match     weight 1   (paired with VP)
    fib_alignment             weight 1
    fvg_overlap               weight 1
    multi_tf_confluence       weight 2

Max possible in backtest: 7 (8 with market_depth, which we can't replay).
Threshold to trade: 4 (per spec's `min_score_to_trade`).

Direction logic per pillar:
    - S/R: bounce off support -> long; rejection at resistance -> short
    - Volume Profile: at HVN -> bias toward mean reversion to POC;
                       at LVN -> bias toward continuation through gap
    - Fibonacci: at retracement zone in up-leg -> long bounce expected;
                 same in down-leg -> short bounce expected
    - FVG overlap: bullish FVG below price acting as support -> long;
                   bearish FVG above price acting as resistance -> short
    - Multi-TF: higher TF (4H/1H) trend direction; if 1H and 4H agree
                with the lower-TF pillar bias -> add 2 weight to that side

CLI demo:
    python -m backtesting.confluence --start 2025-08-25 --end 2025-08-28 --tf 5min
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv
from backtesting.volume_profile import (
    VolumeProfile, compute_profile, load_aligned as vp_load_aligned,
)
from backtesting.fibonacci import FibLevels, latest_fib_leg
from backtesting.fair_value_gaps import FVG, detect_gaps, active_gaps_at
from backtesting.candle_patterns import any_bullish_pattern, any_bearish_pattern


ROOT = Path(__file__).resolve().parent.parent
SR_FILE = ROOT / "reports" / "sr_levels_nifty.csv"

NEAR_LEVEL_PCT = 0.2     # within 0.2% of an S/R or fib level counts as "at"
FVG_NEAR_PCT = 0.3       # within 0.3% of an FVG zone counts as overlap
MIN_SCORE_TO_TRADE = 4
MAX_SCORE_BACKTEST = 7   # 8 minus market_depth


@dataclass
class PillarResult:
    pillar: str
    fired: bool
    direction: Optional[Literal["long", "short"]]
    weight: float
    detail: str = ""


@dataclass
class ConfluenceSignal:
    timestamp: pd.Timestamp
    spot_price: float
    score_long: float
    score_short: float
    pillars: list[PillarResult] = field(default_factory=list)

    @property
    def trade_direction(self) -> Optional[str]:
        return self.direction_at(MIN_SCORE_TO_TRADE)

    def direction_at(self, threshold: float) -> Optional[str]:
        if self.score_long >= threshold and self.score_long > self.score_short:
            return "long"
        if self.score_short >= threshold and self.score_short > self.score_long:
            return "short"
        return None


# -------------------------- SR Provider --------------------------

class SRProvider:
    """Loads daily S/R levels from reports/sr_levels_nifty.csv and
    provides a 'levels for date X' lookup. Picks the priority levels
    per spec: Camarilla S3/R3, Classic S1/R1, prev day H/L, OR-30 H/L.
    """

    SUPPORT_COLS = ["cam_S3", "pivot_S1", "prev_low", "or30_low"]
    RESISTANCE_COLS = ["cam_R3", "pivot_R1", "prev_high", "or30_high"]

    def __init__(self, sr_csv: Path = SR_FILE):
        if not sr_csv.exists():
            raise FileNotFoundError(
                f"S/R levels file not found: {sr_csv}. "
                f"Run: python -m backtesting.sr_levels_compute"
            )
        df = pd.read_csv(sr_csv)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        self.df = df.set_index("date")

    def levels_for(self, day) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Return (supports, resistances) for the given trading day.
        Each is a list of (label, price) tuples in priority order.
        """
        if day not in self.df.index:
            return [], []
        row = self.df.loc[day]
        supports = [(c, float(row[c])) for c in self.SUPPORT_COLS
                    if c in row.index and pd.notna(row[c])]
        resistances = [(c, float(row[c])) for c in self.RESISTANCE_COLS
                       if c in row.index and pd.notna(row[c])]
        return supports, resistances


# -------------------------- Pillar evaluators --------------------------

def _within_pct(price: float, level: float, pct: float) -> bool:
    return abs(price - level) / level * 100 <= pct


def eval_candle(prev_bar, cur_bar, min_body: float = 5.0) -> PillarResult:
    """Pillar 1: bullish reversal pattern -> long bias; bearish -> short.
    prev_bar / cur_bar are tuples (open, high, low, close) or None.
    """
    if cur_bar is None:
        return PillarResult("candle_pattern", False, None, 1.0, "no current bar")
    bull_match, bull_name = any_bullish_pattern(prev_bar, cur_bar, min_body=min_body)
    if bull_match:
        return PillarResult("candle_pattern", True, "long", 1.0, f"bullish {bull_name}")
    bear_match, bear_name = any_bearish_pattern(prev_bar, cur_bar, min_body=min_body)
    if bear_match:
        return PillarResult("candle_pattern", True, "short", 1.0, f"bearish {bear_name}")
    return PillarResult("candle_pattern", False, None, 1.0, "no pattern")


def eval_sr(spot: float, supports: list, resistances: list) -> PillarResult:
    """Pillar 2: at a support level -> long bias; at resistance -> short."""
    for label, lvl in supports:
        if _within_pct(spot, lvl, NEAR_LEVEL_PCT):
            return PillarResult("sr_alignment", True, "long", 1.0,
                                f"at {label}={lvl:.2f}")
    for label, lvl in resistances:
        if _within_pct(spot, lvl, NEAR_LEVEL_PCT):
            return PillarResult("sr_alignment", True, "short", 1.0,
                                f"at {label}={lvl:.2f}")
    return PillarResult("sr_alignment", False, None, 1.0, "no S/R within 0.2%")


def eval_volume_profile(spot: float, vp: VolumeProfile) -> PillarResult:
    """volume_node_alignment: at HVN -> revert toward POC.
    Direction = whichever side of POC we are NOT on.
    """
    for lo, hi, _ in vp.hvn:
        if lo <= spot <= hi:
            direction = "short" if spot > vp.poc else "long"
            return PillarResult("volume_node_alignment", True, direction, 1.0,
                                f"at HVN [{lo:.0f}, {hi:.0f}], POC={vp.poc:.2f}")
    return PillarResult("volume_node_alignment", False, None, 1.0, "not at HVN")


def eval_approach_volume(close_now: float, close_prev: float,
                         vol_now: float, vol_prev_avg: float,
                         vp: VolumeProfile) -> PillarResult:
    """approach_volume_match: per spec rules.
        - high vol close above resistance -> bullish breakout -> long
        - high vol close below support -> bearish breakdown -> short
    Treat VAH as resistance, VAL as support.
    'High volume' = current bar's vol >= 1.5x recent avg.
    """
    if vol_prev_avg <= 0:
        return PillarResult("approach_volume_match", False, None, 1.0, "no vol baseline")
    high_vol = vol_now >= 1.5 * vol_prev_avg
    if not high_vol:
        return PillarResult("approach_volume_match", False, None, 1.0,
                            f"vol {vol_now:.0f} < 1.5x avg {vol_prev_avg:.0f}")

    if close_prev <= vp.vah and close_now > vp.vah:
        return PillarResult("approach_volume_match", True, "long", 1.0,
                            f"high-vol breakout above VAH={vp.vah:.2f}")
    if close_prev >= vp.val and close_now < vp.val:
        return PillarResult("approach_volume_match", True, "short", 1.0,
                            f"high-vol breakdown below VAL={vp.val:.2f}")
    return PillarResult("approach_volume_match", False, None, 1.0,
                        "high vol but no VAH/VAL break")


def eval_fib(spot: float, fib: Optional[FibLevels]) -> PillarResult:
    """fib_alignment: spot near a retracement level of a significant leg.
    Up-leg + at retrace -> long bounce expected.
    Down-leg + at retrace -> short bounce expected.
    """
    if fib is None or not fib.is_significant:
        return PillarResult("fib_alignment", False, None, 1.0,
                            "no significant leg" if fib is None else
                            f"leg too small ({fib.leg_size_pct:.2f}%)")
    for level_pct, level_price in fib.retracements.items():
        if _within_pct(spot, level_price, NEAR_LEVEL_PCT):
            direction = "long" if fib.direction == "up" else "short"
            return PillarResult("fib_alignment", True, direction, 1.0,
                                f"at {level_pct*100:.1f}% retrace={level_price:.2f} "
                                f"of {fib.direction}-leg")
    return PillarResult("fib_alignment", False, None, 1.0, "not at any fib retrace")


def eval_fvg(spot: float, active: list[FVG]) -> PillarResult:
    """fvg_overlap: spot inside or within 0.3% of an active FVG.
    Bullish FVG below current price -> support -> long.
    Bearish FVG above current price -> resistance -> short.
    """
    for g in active:
        zone_lo, zone_hi = g.low, g.high
        # Direct overlap
        in_zone = zone_lo <= spot <= zone_hi
        # Near (within 0.3%)
        near = (
            (spot < zone_lo and (zone_lo - spot) / spot * 100 <= FVG_NEAR_PCT) or
            (spot > zone_hi and (spot - zone_hi) / spot * 100 <= FVG_NEAR_PCT)
        )
        if in_zone or near:
            direction = "long" if g.direction == "bullish" else "short"
            return PillarResult("fvg_overlap", True, direction, 1.0,
                                f"{g.direction} FVG [{g.low:.0f}, {g.high:.0f}]")
    return PillarResult("fvg_overlap", False, None, 1.0, "no FVG overlap")


def eval_multi_tf(htf_1h_close: float, htf_1h_open: float,
                  htf_4h_close: float, htf_4h_open: float,
                  bias_direction: str) -> PillarResult:
    """multi_tf_confluence (weight 2): both 1H and 4H closes agree with
    the lower-TF bias direction. Trend = simple sign of close - open of
    the most recent COMPLETED higher-TF bar.
    """
    h1_dir = "long" if htf_1h_close > htf_1h_open else "short"
    h4_dir = "long" if htf_4h_close > htf_4h_open else "short"

    if h1_dir == bias_direction and h4_dir == bias_direction:
        return PillarResult("multi_tf_confluence", True, bias_direction, 2.0,
                            f"1H and 4H both {bias_direction}")
    return PillarResult("multi_tf_confluence", False, None, 2.0,
                        f"1H={h1_dir}, 4H={h4_dir}, bias={bias_direction}")


# -------------------------- Top-level orchestrator --------------------------

def score_signals(
    df_5m: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    sr_provider: SRProvider,
    *,
    vp_lookback_sessions: int = 5,
    vol_avg_window: int = 20,
    fib_lookback_bars: int = 50,
    fvg_min_gap_pct: float = 0.05,
    fvg_expiry_bars: int = 200,
) -> list[ConfluenceSignal]:
    """For each 5-min bar, compute confluence signal. Heavy operation —
    pre-computes per-session VP and per-bar fib/FVG lookups.
    """
    # Pre-compute FVGs once (uses 5-min bars per spec)
    all_fvgs = detect_gaps(df_5m, min_gap_pct=fvg_min_gap_pct,
                           expiry_bars=fvg_expiry_bars)

    # Group bars by session for per-session VP
    df_5m = df_5m.sort_index()
    sessions = df_5m.groupby(df_5m.index.date)
    session_dates = sorted(sessions.groups.keys())

    # Pre-compute volume profiles using ROLLING N-session lookback
    # (so today's profile is built from the last N COMPLETED sessions)
    profiles_by_date: dict = {}
    full_vol = vp_load_aligned()
    for d in session_dates:
        idx = session_dates.index(d)
        if idx < vp_lookback_sessions:
            continue  # not enough history yet
        lookback_days = session_dates[idx - vp_lookback_sessions:idx]
        mask = pd.Series(full_vol.index.date).isin(lookback_days).to_numpy()
        sub_vp = full_vol.iloc[mask]
        if len(sub_vp) < 30:
            continue
        profiles_by_date[d] = compute_profile(sub_vp)

    # Volume average from the joined 5m bars. SHIFT(1) so the average
    # at bar i excludes bar i itself (avoids self-comparison look-ahead).
    vol_5m = df_5m.get("futures_volume", pd.Series(0, index=df_5m.index))
    vol_avg = vol_5m.shift(1).rolling(vol_avg_window, min_periods=5).mean()

    signals: list[ConfluenceSignal] = []

    for i, ts in enumerate(df_5m.index):
        row = df_5m.iloc[i]
        spot = float(row["close"])
        day = ts.date()

        vp = profiles_by_date.get(day)
        if vp is None:
            continue

        supports, resistances = sr_provider.levels_for(day)

        # Fib leg from the 15min/1h-equivalent context — use 5m here for
        # speed (caller can rerun on higher TF if needed)
        fib = latest_fib_leg(df_5m.iloc[max(0, i - fib_lookback_bars):i + 1])

        active_fvgs = active_gaps_at(all_fvgs, ts)

        # Higher-TF context: most recent COMPLETED bar at/before ts.
        # A bar labeled with start time T covers [T, T+tf), so it has
        # CLOSED only when ts >= T + tf. We shift the lookup back by tf
        # to enforce this — otherwise we'd be reading the close of a bar
        # that's still forming (look-ahead bias).
        h1_idx = df_1h.index.searchsorted(ts - pd.Timedelta(hours=1), side="right") - 1
        h4_idx = df_4h.index.searchsorted(ts - pd.Timedelta(hours=4), side="right") - 1
        if h1_idx < 0 or h4_idx < 0:
            continue
        h1 = df_1h.iloc[h1_idx]
        h4 = df_4h.iloc[h4_idx]

        # Evaluate pillars
        prev_close = float(df_5m.iloc[i - 1]["close"]) if i > 0 else spot
        vnow = float(vol_5m.iloc[i])
        vavg = float(vol_avg.iloc[i]) if not np.isnan(vol_avg.iloc[i]) else 0

        # Bars for candle pattern
        cur_bar = (float(row["open"]), float(row["high"]),
                   float(row["low"]),  float(row["close"]))
        prev_bar = None
        if i > 0:
            prev = df_5m.iloc[i - 1]
            prev_bar = (float(prev["open"]), float(prev["high"]),
                        float(prev["low"]),  float(prev["close"]))

        pillars = [
            eval_candle(prev_bar, cur_bar),
            eval_sr(spot, supports, resistances),
            eval_volume_profile(spot, vp),
            eval_approach_volume(spot, prev_close, vnow, vavg, vp),
            eval_fib(spot, fib),
            eval_fvg(spot, active_fvgs),
        ]

        # Determine bias for multi-TF check: vote across lower-TF pillars
        long_w = sum(p.weight for p in pillars if p.fired and p.direction == "long")
        short_w = sum(p.weight for p in pillars if p.fired and p.direction == "short")
        bias = "long" if long_w > short_w else "short"
        pillars.append(eval_multi_tf(
            float(h1["close"]), float(h1["open"]),
            float(h4["close"]), float(h4["open"]),
            bias,
        ))

        score_long = sum(p.weight for p in pillars
                         if p.fired and p.direction == "long")
        score_short = sum(p.weight for p in pillars
                          if p.fired and p.direction == "short")

        signals.append(ConfluenceSignal(
            timestamp=ts, spot_price=spot,
            score_long=score_long, score_short=score_short,
            pillars=pillars,
        ))

    return signals


# -------------------------- CLI demo --------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--show-all", action="store_true",
                   help="Print all bars, not just trade-worthy ones")
    args = p.parse_args()

    print("Loading data...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]
    if sub.empty:
        raise SystemExit("No data")

    print("Resampling to 5m / 1h / 4h...")
    df_5m = resample_ohlcv(sub, "5min")
    df_1h = resample_ohlcv(sub, "1h")
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)

    sr = SRProvider()
    print(f"Computing confluence over {len(df_5m):,} 5-min bars...\n")
    signals = score_signals(df_5m, df_1h, df_4h, sr)

    n_total = len(signals)
    n_trade = sum(1 for s in signals if s.trade_direction is not None)
    n_long = sum(1 for s in signals if s.trade_direction == "long")
    n_short = sum(1 for s in signals if s.trade_direction == "short")

    print(f"Total decision points: {n_total}")
    print(f"Trade-worthy signals (score >= {MIN_SCORE_TO_TRADE}): {n_trade}")
    print(f"  long: {n_long}   short: {n_short}\n")

    print(f"{'Timestamp':<22} {'Spot':>9} {'L':>4} {'S':>4} {'Dir':<6}  Pillars fired")
    print("-" * 100)
    for s in signals:
        if not args.show_all and s.trade_direction is None:
            continue
        fired = ", ".join(p.pillar for p in s.pillars if p.fired)
        print(f"{s.timestamp.strftime('%Y-%m-%d %H:%M'):<22} "
              f"{s.spot_price:>9.2f} {s.score_long:>4.1f} {s.score_short:>4.1f} "
              f"{(s.trade_direction or '-'):<6}  {fired}")


if __name__ == "__main__":
    main()
