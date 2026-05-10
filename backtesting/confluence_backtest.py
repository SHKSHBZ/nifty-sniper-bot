"""Confluence backtest runner — Phase 1 (spot-only, hypothetical P&L).

Walks through ConfluenceSignals, applies the spec's time gates, cooldown,
sizing, and exit rules. Produces a trade ledger + summary metrics.

This is Phase 1: P&L is computed from SPOT price movement, not actual
options. Use to validate signal quality before layering options
execution simulation in Phase 2.

Per nifty_trading_system_config.json:
    time_gates:
        no_entries_before:           09:30
        no_entries_after:            14:30
        force_close_all_at:          15:25
        skip_first_n_min_after_open: 15
        min_minutes_between_entries: 30
    decision_logic.actions:
        score >= 3.5 (STRONG): 100% capital, ATM+ITM combo
        score >= 2.5 (NORMAL): 50% capital, ATM only
        score <  2.5         : SKIP
    risk_management:
        per_trade_max_loss_inr:        6000
        daily_max_loss_inr:            25000
        circuit_breaker:
            consecutive_losses_to_pause: 3
            pause_duration_minutes:      60

Phase-1 P&L proxy: each trade is sized in NIFTY POINTS (not options).
TP = +0.30% spot move, SL = -0.30% spot move (mirrors the T1 tactic
exits from the merged spec — it's a defensible default for spot-only).
Caller can override.

CLI demo:
    python -m backtesting.confluence_backtest --start 2025-06-01 --end 2025-08-28
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

from backtesting.timeframe_sync import load_aligned_1min, resample_ohlcv
from backtesting.confluence import (
    ConfluenceSignal, SRProvider, score_signals, MIN_SCORE_TO_TRADE,
)


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Spec-derived defaults
NO_ENTRIES_BEFORE = dtime(9, 30)
NO_ENTRIES_AFTER = dtime(14, 30)
FORCE_CLOSE_AT = dtime(15, 25)
COOLDOWN_MIN = 30
TP_PCT = 0.30   # spot move %
SL_PCT = 0.30   # spot move %
DAILY_MAX_LOSS_PCT = 1.0   # of capital
CONSEC_LOSS_PAUSE = 3
PAUSE_MIN = 60


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    entry_price: float
    exit_price: float
    exit_reason: str  # 'tp', 'sl', 'eod', 'circuit_breaker'
    points: float     # signed: +ve = profit, -ve = loss
    pnl_pct: float    # spot % move in signal direction
    score: float
    pillars_fired: list[str] = field(default_factory=list)


def _within_trading_window(ts: pd.Timestamp) -> bool:
    t = ts.time()
    return NO_ENTRIES_BEFORE <= t < NO_ENTRIES_AFTER


def _structural_sl(
    direction: str,
    entry_price: float,
    supports: list[tuple[str, float]],
    resistances: list[tuple[str, float]],
    *,
    max_sl_pct: float,
    fallback_sl_pct: float,
) -> tuple[float, str]:
    """Place SL at nearest opposite S/R level. LONG -> nearest support
    below entry. SHORT -> nearest resistance above entry.

    If the chosen level is more than max_sl_pct away (would risk too
    much), fall back to fixed fallback_sl_pct. Returns (sl_price, label).
    """
    if direction == "long":
        # Supports BELOW entry, sorted descending (nearest first)
        candidates = sorted(
            [(lbl, p) for lbl, p in supports if p < entry_price],
            key=lambda x: -x[1],
        )
    else:
        # Resistances ABOVE entry, sorted ascending (nearest first)
        candidates = sorted(
            [(lbl, p) for lbl, p in resistances if p > entry_price],
            key=lambda x: x[1],
        )

    fallback = (
        entry_price * (1 - fallback_sl_pct / 100) if direction == "long"
        else entry_price * (1 + fallback_sl_pct / 100)
    )
    if not candidates:
        return fallback, f"fallback_{fallback_sl_pct}%"

    label, price = candidates[0]
    distance_pct = abs(price - entry_price) / entry_price * 100
    if distance_pct > max_sl_pct:
        return fallback, f"fallback_{fallback_sl_pct}% (nearest {label} was {distance_pct:.2f}% > cap)"
    return price, f"{label}@{price:.2f} ({distance_pct:.2f}%)"


def _simulate_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    direction: str,
    entry_price: float,
    tp_pct: float,
    sl_price: float,
) -> tuple[int, float, str]:
    """Walk forward from entry_idx until TP, SL, or EOD. Returns
    (exit_idx, exit_price, exit_reason). SL is now a PRICE (computed by
    caller — could be % or structural).
    """
    if direction == "long":
        tp_price = entry_price * (1 + tp_pct / 100)
    else:
        tp_price = entry_price * (1 - tp_pct / 100)

    n = len(bars)
    for j in range(entry_idx + 1, n):
        bar = bars.iloc[j]
        ts = bars.index[j]

        # EOD force close
        if ts.time() >= FORCE_CLOSE_AT:
            return j, float(bar["close"]), "eod"

        if direction == "long":
            # Check SL first (conservative — assume SL hits before TP within same bar)
            if bar["low"] <= sl_price:
                return j, sl_price, "sl"
            if bar["high"] >= tp_price:
                return j, tp_price, "tp"
        else:
            if bar["high"] >= sl_price:
                return j, sl_price, "sl"
            if bar["low"] <= tp_price:
                return j, tp_price, "tp"

    # Ran out of bars without exit (last bar in window)
    return n - 1, float(bars.iloc[-1]["close"]), "eod"


def run_backtest(
    signals: list[ConfluenceSignal],
    bars_5m: pd.DataFrame,
    *,
    tp_pct: float = TP_PCT,
    sl_pct: float = SL_PCT,
    sr_provider: Optional['SRProvider'] = None,  # if set, use structural SL
    structural_sl_max_pct: float = 1.0,
    structural_sl_buffer_pct: float = 0.0,
    score_threshold: float = 4.0,
    cooldown_min: int = COOLDOWN_MIN,
    consec_loss_pause: int = CONSEC_LOSS_PAUSE,
    pause_min: int = PAUSE_MIN,
) -> list[Trade]:
    trades: list[Trade] = []
    last_entry_ts: Optional[pd.Timestamp] = None
    pause_until: Optional[pd.Timestamp] = None
    consec_losses = 0

    # Index bars by timestamp for fast lookup
    bar_idx = {ts: i for i, ts in enumerate(bars_5m.index)}

    for sig in signals:
        ts = sig.timestamp
        if not _within_trading_window(ts):
            continue
        direction = sig.direction_at(score_threshold)
        if direction is None:
            continue

        # Cooldown
        if last_entry_ts is not None:
            elapsed_min = (ts - last_entry_ts).total_seconds() / 60
            if elapsed_min < cooldown_min:
                continue
        # Circuit breaker pause
        if pause_until is not None and ts < pause_until:
            continue

        if ts not in bar_idx:
            continue
        i = bar_idx[ts]
        entry_price = sig.spot_price

        # Determine SL price: structural (S/R based) or fixed %
        if sr_provider is not None:
            supports, resistances = sr_provider.levels_for(ts.date())
            sl_price, sl_label = _structural_sl(
                direction, entry_price, supports, resistances,
                max_sl_pct=structural_sl_max_pct, fallback_sl_pct=sl_pct,
            )
            # Apply buffer: push SL further past the level by buffer_pct
            if structural_sl_buffer_pct > 0:
                buf = entry_price * structural_sl_buffer_pct / 100
                sl_price = sl_price - buf if direction == "long" else sl_price + buf
        else:
            sl_price = (entry_price * (1 - sl_pct / 100) if direction == "long"
                        else entry_price * (1 + sl_pct / 100))
            sl_label = f"fixed_{sl_pct}%"

        exit_i, exit_price, reason = _simulate_exit(
            bars_5m, i, direction, entry_price, tp_pct, sl_price,
        )
        exit_ts = bars_5m.index[exit_i]

        if direction == "long":
            points = exit_price - entry_price
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            points = entry_price - exit_price
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        trades.append(Trade(
            entry_time=ts, exit_time=exit_ts,
            direction=direction,
            entry_price=entry_price, exit_price=exit_price,
            exit_reason=reason,
            points=points, pnl_pct=pnl_pct,
            score=max(sig.score_long, sig.score_short),
            pillars_fired=[p.pillar for p in sig.pillars if p.fired],
        ))

        last_entry_ts = ts

        # Loss streak handling
        if points < 0:
            consec_losses += 1
            if consec_losses >= consec_loss_pause:
                pause_until = ts + pd.Timedelta(minutes=pause_min)
                consec_losses = 0
        else:
            consec_losses = 0

    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"n_trades": 0}
    df = pd.DataFrame([t.__dict__ for t in trades])
    n = len(df)
    wins = df[df["points"] > 0]
    losses = df[df["points"] <= 0]
    gross_profit = wins["points"].sum()
    gross_loss = abs(losses["points"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    cumulative = df["points"].cumsum()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max).min()

    return {
        "n_trades":          n,
        "n_long":            int((df["direction"] == "long").sum()),
        "n_short":           int((df["direction"] == "short").sum()),
        "win_rate_pct":      len(wins) / n * 100,
        "avg_win_points":    float(wins["points"].mean()) if len(wins) else 0,
        "avg_loss_points":   float(losses["points"].mean()) if len(losses) else 0,
        "total_points":      float(df["points"].sum()),
        "profit_factor":     float(pf),
        "max_drawdown_pts":  float(drawdown),
        "exit_reasons":      df["exit_reason"].value_counts().to_dict(),
        "avg_score":         float(df["score"].mean()),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--tp", type=float, default=TP_PCT)
    p.add_argument("--sl", type=float, default=SL_PCT)
    p.add_argument("--cooldown", type=int, default=COOLDOWN_MIN)
    p.add_argument("--structural-sl", action="store_true",
                   help="Use S/R levels as SL (LONG=nearest support below, "
                        "SHORT=nearest resistance above) instead of fixed %%.")
    p.add_argument("--max-structural-sl", type=float, default=1.0,
                   help="Cap structural SL distance to this %% of price; "
                        "fall back to --sl beyond that. Default 1.0%%.")
    p.add_argument("--out-csv", default=None,
                   help="Path to write trade ledger CSV (default reports/confluence_trades.csv)")
    args = p.parse_args()

    print("Loading data + computing confluence signals...")
    df1 = load_aligned_1min()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    sub = df1.loc[start:end]
    if sub.empty:
        raise SystemExit("No data in window")

    df_5m = resample_ohlcv(sub, "5min")
    df_1h = resample_ohlcv(sub, "1h")
    df_4h = resample_ohlcv(sub, "4h", drop_partial=False)

    sr = SRProvider()
    signals = score_signals(df_5m, df_1h, df_4h, sr)
    print(f"  {len(signals):,} decision points  -> "
          f"{sum(1 for s in signals if s.trade_direction):,} trade-worthy raw signals\n")

    sl_desc = (f"structural (S/R-based, capped at {args.max_structural_sl}%)"
               if args.structural_sl else f"{args.sl}% fixed")
    print(f"Running backtest: TP={args.tp}%  SL={sl_desc}  cooldown={args.cooldown}min\n")
    trades = run_backtest(
        signals, df_5m,
        tp_pct=args.tp, sl_pct=args.sl, cooldown_min=args.cooldown,
        sr_provider=sr if args.structural_sl else None,
        structural_sl_max_pct=args.max_structural_sl,
    )

    s = summarize(trades)
    print("=" * 60)
    print(f"PHASE-1 BACKTEST RESULTS  ({args.start} to {args.end})")
    print("=" * 60)
    if s["n_trades"] == 0:
        print("No trades.")
        return
    print(f"  Trades:           {s['n_trades']}  "
          f"(long={s['n_long']}, short={s['n_short']})")
    print(f"  Win rate:         {s['win_rate_pct']:.1f}%")
    print(f"  Avg win:          {s['avg_win_points']:>+8.2f} points")
    print(f"  Avg loss:         {s['avg_loss_points']:>+8.2f} points")
    print(f"  Profit factor:    {s['profit_factor']:.2f}")
    print(f"  Total P&L:        {s['total_points']:>+8.2f} points")
    print(f"  Max DD:           {s['max_drawdown_pts']:>+8.2f} points")
    print(f"  Avg score:        {s['avg_score']:.1f}")
    print(f"  Exit reasons:     {s['exit_reasons']}")

    out = Path(args.out_csv) if args.out_csv else REPORTS / "confluence_trades.csv"
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(out, index=False)
    try:
        rel = out.relative_to(ROOT)
    except ValueError:
        rel = out
    print(f"\nTrade ledger written: {rel}")


if __name__ == "__main__":
    main()
