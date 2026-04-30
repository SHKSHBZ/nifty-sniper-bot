"""
Run all 4 newly-coded tactics on the 2-year sample, in a single pass.

Architecture:
    Walk each 5-min bar across 2 years exactly once.  At every bar:
      1. Reconstruct chain state (PCR, S/R, OI changes) — same as Phase 4.
      2. Compute indicators (EMA9, EMA21, ATR, ADX, OR levels, etc.)
      3. Build a TacticState dict.
      4. For each tactic runner, ask it whether to act on this state.
      5. Each runner manages its own open positions and exits.

This avoids 4 sequential ~30-min runs (~2 hours) by piggybacking on a
single chain-reconstruction pass.

Output: reports/phase9_tactics_comparison.md plus one detailed
per-trade log per tactic.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.classifier import (  # noqa: E402
    RegimeClassifier, ClassifierConfig, Regime, compute_adx,
)
from tactics import (  # noqa: E402
    Tactic, TacticState, TacticSignal,
    VWAPHybridTactic, TrendPullbackTactic, BullishORBTactic, BearishORBTactic,
)
from backtesting.backtest_regime_phase1 import (  # noqa: E402
    load_spot, load_vix, resample, previous_day_close, build_feature_for_bar,
)
from backtesting.backtest_regime_phase3 import (  # noqa: E402
    discover_expiries, load_chain_for_expiry, map_day_to_expiry,
)
from backtesting.backtest_regime_phase4 import (  # noqa: E402
    reconstruct_chain_state, get_option_price_at,
    SLIPPAGE, BROKERAGE_PER_LEG, LOT_SIZE, STRIKE_STEP, MIN_ENTRY_PREMIUM,
)


# ---------------------------------------------------------------------------
# Trade tracker
# ---------------------------------------------------------------------------

@dataclass
class TacticTrade:
    tactic: str
    day: str
    entry_ts: pd.Timestamp
    direction: str
    strike: int
    entry_premium: float
    qty_lots: int
    sl_pct: float
    tp_pct: float
    time_stop_min: int
    exit_ts: Optional[pd.Timestamp] = None
    exit_premium: float = 0.0
    exit_reason: str = ""
    regime_at_entry: str = ""
    pyramid_lots: int = 1     # how many lots ended up after pyramiding
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    reason: str = ""

    def close(self, ts, exit_premium, reason):
        self.exit_ts = ts
        self.exit_premium = exit_premium
        self.exit_reason = reason
        eff_entry = self.entry_premium * (1 + SLIPPAGE)
        eff_exit = exit_premium * (1 - SLIPPAGE)
        units = self.qty_lots * LOT_SIZE * self.pyramid_lots
        self.gross_pnl = (eff_exit - eff_entry) * units
        self.net_pnl = self.gross_pnl - (BROKERAGE_PER_LEG * 2)


class TacticRunner:
    """Per-tactic position manager and trade log."""

    def __init__(self, name: str, tactic: Tactic):
        self.name = name
        self.tactic = tactic
        self.trades: list[TacticTrade] = []
        self.open_trade: Optional[TacticTrade] = None

    # ----- per-bar step ----------------------------------------------------

    def step(
        self,
        ts: pd.Timestamp,
        state: TacticState,
        chain: dict,
        spot_close: float,
    ) -> None:
        # 1) Refresh state with our position info
        if self.open_trade is not None:
            state.is_in_position = True
            state.open_position_direction = self.open_trade.direction
            state.open_position_entry_premium = self.open_trade.entry_premium
            state.open_position_entry_ts = self.open_trade.entry_ts
            state.open_position_lots_added = max(0, self.open_trade.pyramid_lots - 1)
        else:
            state.is_in_position = False
            state.open_position_direction = None
            state.open_position_lots_added = 0

        # 2) Manage open trade exits
        if self.open_trade is not None:
            self._check_exits(ts, chain)

        # 3) Evaluate tactic
        sig = self.tactic.evaluate(state)
        if sig is None:
            return
        if sig.action == "enter" and self.open_trade is None:
            self._open_trade(ts, sig, chain, spot_close, state.regime)
        elif sig.action == "add_lot" and self.open_trade is not None:
            # Pyramiding: simply increment lot count (simple model)
            self.open_trade.pyramid_lots += 1

    # ----- exits ---------------------------------------------------------

    def _check_exits(self, ts: pd.Timestamp, chain: dict) -> None:
        opt = get_option_price_at(chain, self.open_trade.strike,
                                  self.open_trade.direction, ts)
        if opt is None:
            return
        oclose, ohigh, olow = opt
        entry = self.open_trade.entry_premium
        tp = entry * (1 + self.open_trade.tp_pct)
        sl = entry * (1 - self.open_trade.sl_pct)
        mins_held = (ts - self.open_trade.entry_ts).total_seconds() / 60

        if ohigh >= tp:
            self.open_trade.close(ts, tp, "TP")
            self.trades.append(self.open_trade)
            self.open_trade = None
        elif olow <= sl:
            self.open_trade.close(ts, sl, "SL")
            self.trades.append(self.open_trade)
            self.open_trade = None
        elif mins_held >= self.open_trade.time_stop_min:
            self.open_trade.close(ts, oclose, "TIME_STOP")
            self.trades.append(self.open_trade)
            self.open_trade = None
        elif ts.time() >= time(14, 30):
            self.open_trade.close(ts, oclose, "EOD")
            self.trades.append(self.open_trade)
            self.open_trade = None

    # ----- entry --------------------------------------------------------

    def _open_trade(
        self,
        ts: pd.Timestamp,
        sig: TacticSignal,
        chain: dict,
        spot_close: float,
        regime: str,
    ) -> None:
        atm = int(round(spot_close / STRIKE_STEP) * STRIKE_STEP)
        # Strike offset semantics: for CE, ITM = strike < spot, so use -offset
        # For PE, ITM = strike > spot, so use +offset
        if sig.direction == "CE":
            strike = atm - sig.strike_offset * STRIKE_STEP
        else:
            strike = atm + sig.strike_offset * STRIKE_STEP

        opt = get_option_price_at(chain, strike, sig.direction, ts)
        if opt is None:
            return
        entry_premium = opt[0]
        if entry_premium < MIN_ENTRY_PREMIUM:
            return

        self.open_trade = TacticTrade(
            tactic=self.name,
            day=ts.date().isoformat(),
            entry_ts=ts,
            direction=sig.direction,
            strike=strike,
            entry_premium=entry_premium,
            qty_lots=1,
            sl_pct=sig.sl_pct,
            tp_pct=sig.tp_pct,
            time_stop_min=sig.time_stop_min,
            regime_at_entry=regime,
            pyramid_lots=1,
            reason=sig.reason,
        )

    # ----- end-of-day flat ---------------------------------------------

    def force_flat(self, last_ts: pd.Timestamp, chain: dict) -> None:
        if self.open_trade is None:
            return
        opt = get_option_price_at(chain, self.open_trade.strike,
                                  self.open_trade.direction, last_ts)
        exit_premium = opt[0] if opt else self.open_trade.entry_premium
        self.open_trade.close(last_ts, exit_premium, "EOD_FORCE")
        self.trades.append(self.open_trade)
        self.open_trade = None


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_session_vwap(df: pd.DataFrame) -> pd.Series:
    pv = (df["close"] * df["volume"]).groupby(df.index.date).cumsum()
    vv = df["volume"].groupby(df.index.date).cumsum()
    return pv / vv.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    spot_1m = load_spot()
    vix_1m = load_vix()
    expiries_by_date = discover_expiries(ROOT / "data")

    print(f"Loaded spot rows={len(spot_1m):,}  expiries={len(expiries_by_date)}")
    print(f"Spot range: {spot_1m.index.min()} -> {spot_1m.index.max()}")

    classifier = RegimeClassifier(ClassifierConfig(sustain_min=15))

    tactics_map = {
        "vwap_hybrid":   VWAPHybridTactic(),
        "trend_pullback": TrendPullbackTactic(),
        "bullish_orb":    BullishORBTactic(),
        "bearish_orb":    BearishORBTactic(),
    }
    runners = {name: TacticRunner(name, t) for name, t in tactics_map.items()}

    trading_days = sorted({d for d in spot_1m.index.date})
    expiries_sorted = sorted(expiries_by_date.keys())
    day_to_expiry = map_day_to_expiry(trading_days, expiries_sorted)

    chain_cache: dict[date, dict] = {}

    for day in trading_days:
        if day not in day_to_expiry:
            continue
        exp = day_to_expiry[day]
        if exp not in chain_cache:
            chain_cache[exp] = load_chain_for_expiry(expiries_by_date[exp])
        chain = chain_cache[exp]

        day_1m = spot_1m[spot_1m.index.date == day]
        if day_1m.empty:
            continue
        day_5m = resample(day_1m, "5min")
        day_15m = resample(day_1m, "15min")
        prev_close = previous_day_close(spot_1m, day)
        vix_today = vix_1m[vix_1m.index.date == day] if vix_1m is not None else None

        # Pre-compute indicators on day_5m
        day_5m["ema9"] = compute_ema(day_5m["close"], 9)
        day_5m["ema21"] = compute_ema(day_5m["close"], 21)
        day_5m["atr"] = compute_atr(day_5m, 14)
        day_5m["vwap"] = compute_session_vwap(day_5m)

        # OR window stats
        or_slice = day_5m.between_time("09:15", "09:29:59")
        or_high = float(or_slice["high"].max()) if not or_slice.empty else 0.0
        or_low = float(or_slice["low"].min()) if not or_slice.empty else 0.0
        or_volume_avg = float(or_slice["volume"].mean()) if not or_slice.empty else 0.0
        day_open = float(day_5m.iloc[0]["open"])

        # ADX (15m, daily reset)
        adx15 = compute_adx(day_15m, period=14) if len(day_15m) >= 14 else pd.Series(dtype=float)

        # Reset classifier per day
        classifier._current = None
        classifier._candidate = None

        prev_5m: Optional[pd.Series] = None

        for ts, row in day_5m.iterrows():
            # 1) regime
            feat = build_feature_for_bar(ts, day_5m, day_15m, prev_close, vix_1m)
            regime = classifier.classify(feat)

            spot_close = float(row["close"])

            # 2) chain state
            chain_state = reconstruct_chain_state(chain, chain, ts, spot_close)
            ts_prev = ts - pd.Timedelta(minutes=5)
            atm = int(round(spot_close / STRIKE_STEP) * STRIKE_STEP)
            focus_strikes = [atm + (i * STRIKE_STEP) for i in range(-3, 4)]

            def focus_oi_total(side: str, when: pd.Timestamp) -> float:
                tot = 0.0
                for s in focus_strikes:
                    df = chain.get((s, side))
                    if df is None:
                        continue
                    try:
                        r = df.loc[when]
                    except KeyError:
                        w = df.loc[when - pd.Timedelta(minutes=2):when]
                        if w.empty:
                            continue
                        r = w.iloc[-1]
                    if isinstance(r, pd.DataFrame):
                        r = r.iloc[0]
                    tot += float(r.get("open_interest", 0))
                return tot

            ce_now = focus_oi_total("CE", ts)
            pe_now = focus_oi_total("PE", ts)
            ce_prev = focus_oi_total("CE", ts_prev)
            pe_prev = focus_oi_total("PE", ts_prev)

            # 3) VIX
            vix_level = 15.0
            vix_chg_15m = 0.0
            if vix_today is not None and not vix_today.empty:
                vw = vix_today[vix_today.index <= ts]
                if not vw.empty:
                    vix_level = float(vw.iloc[-1]["close"])
                    if len(vw) >= 16:
                        prev = float(vw.iloc[-16]["close"])
                        vix_chg_15m = (vix_level - prev) / prev if prev else 0.0

            # 4) DTE
            dte = (exp - day).days

            # 5) Recent lows/highs (last 3 5m bars including current)
            day_5m_upto_now = day_5m[day_5m.index <= ts]
            recent = day_5m_upto_now.tail(3)
            recent_lows = tuple(float(x) for x in recent["low"].tolist())
            recent_highs = tuple(float(x) for x in recent["high"].tolist())

            # 6) ADX at this bar
            adx_now = 0.0
            if len(adx15):
                adx_upto = adx15[adx15.index <= ts]
                if len(adx_upto):
                    adx_now = float(adx_upto.iloc[-1])

            # 7) Build state
            state = TacticState(
                ts=ts.to_pydatetime(),
                spot=spot_close,
                futures=spot_close,    # we don't have futures separately
                dte=dte,
                expiry_date=exp.isoformat(),
                current_date=day.isoformat(),
                day_open=day_open,
                day_high=float(day_5m_upto_now["high"].max()),
                day_low=float(day_5m_upto_now["low"].min()),
                or_high=or_high,
                or_low=or_low,
                or_volume_avg=or_volume_avg,
                prev_day_close=prev_close,
                vwap=float(row["vwap"]) if pd.notna(row["vwap"]) else spot_close,
                ema9_5m=float(row["ema9"]) if pd.notna(row["ema9"]) else spot_close,
                ema21_5m=float(row["ema21"]) if pd.notna(row["ema21"]) else spot_close,
                atr_5m=float(row["atr"]) if pd.notna(row["atr"]) else 0.0,
                adx_15m=adx_now,
                bar_open=float(row["open"]),
                bar_high=float(row["high"]),
                bar_low=float(row["low"]),
                bar_close=float(row["close"]),
                bar_volume=float(row["volume"]),
                prev_bar_open=float(prev_5m["open"]) if prev_5m is not None else 0.0,
                prev_bar_high=float(prev_5m["high"]) if prev_5m is not None else 0.0,
                prev_bar_low=float(prev_5m["low"]) if prev_5m is not None else 0.0,
                prev_bar_close=float(prev_5m["close"]) if prev_5m is not None else 0.0,
                recent_5m_lows=recent_lows,
                recent_5m_highs=recent_highs,
                support_strike=chain_state["support"],
                resistance_strike=chain_state["resistance"],
                focus_pcr=chain_state["focus_pcr"],
                ce_oi_change=int(ce_now - ce_prev),
                pe_oi_change=int(pe_now - pe_prev),
                vix_level=vix_level,
                vix_chg_15m=vix_chg_15m,
                regime=regime.value,
            )

            # 8) Step every runner
            for runner in runners.values():
                runner.step(ts, state, chain, spot_close)

            prev_5m = row

        # End of day — force-flat any open positions for each runner
        last_ts = day_5m.index[-1]
        for runner in runners.values():
            runner.force_flat(last_ts, chain)

    # ---- Reports ----
    write_reports(runners)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _summarize(trades: list[TacticTrade]) -> dict:
    if not trades:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "winners": 0,
                "losers": 0, "avg_win": 0.0, "avg_loss": 0.0,
                "profit_factor": float("inf"), "max_dd": 0.0,
                "exit_reasons": {}}
    df = pd.DataFrame([t.__dict__ for t in trades])
    winners = df[df["net_pnl"] > 0]
    losers = df[df["net_pnl"] <= 0]
    cum = df["net_pnl"].cumsum()
    max_dd = (cum.cummax() - cum).max() if len(df) else 0.0
    gp = winners["net_pnl"].sum() if len(winners) else 0.0
    gl = abs(losers["net_pnl"].sum()) if len(losers) else 0.0
    return {
        "trades": len(df),
        "winners": int(len(winners)),
        "losers": int(len(losers)),
        "win_rate": len(winners) / len(df) * 100,
        "net_pnl": float(df["net_pnl"].sum()),
        "avg_win": float(winners["net_pnl"].mean()) if len(winners) else 0.0,
        "avg_loss": float(losers["net_pnl"].mean()) if len(losers) else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else float("inf"),
        "max_dd": float(max_dd),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }


def write_reports(runners: dict[str, TacticRunner]) -> None:
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Comparison report
    lines = ["# Phase 9 — All Tactics Backtest Comparison\n"]
    lines.append("Tactics evaluated on the same 2-year sample, single pass.")
    lines.append("Reference: production OI-Wall MR Phase 4 result was -Rs 53,393 over 159 trades.\n")
    lines.append("## Headline Numbers\n")
    lines.append("| Tactic | Trades | Wins | Win% | Net P&L | Avg Win | Avg Loss | PF | Max DD |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, runner in runners.items():
        s = _summarize(runner.trades)
        pf = s["profit_factor"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        lines.append(
            f"| {name} | {s['trades']} | {s['winners']} | {s['win_rate']:.1f} "
            f"| Rs {s['net_pnl']:,.0f} | Rs {s['avg_win']:,.0f} "
            f"| Rs {s['avg_loss']:,.0f} | {pf_str} | Rs {s['max_dd']:,.0f} |"
        )

    lines.append("\n## Per-Tactic Detail\n")
    for name, runner in runners.items():
        s = _summarize(runner.trades)
        lines.append(f"### {name}\n")
        if not runner.trades:
            lines.append("  (no trades)\n")
            continue
        lines.append(f"- Trades: {s['trades']}  Wins: {s['winners']}  "
                     f"Losers: {s['losers']}")
        lines.append(f"- Net P&L: Rs {s['net_pnl']:,.0f}    PF: {s['profit_factor']:.2f}")
        lines.append(f"- Exit reasons: {s['exit_reasons']}")
        # Per-month
        df = pd.DataFrame([t.__dict__ for t in runner.trades])
        df["month"] = df["entry_ts"].dt.strftime("%Y-%m")
        monthly = df.groupby("month")["net_pnl"].agg(["sum", "count"]).reset_index()
        lines.append("\n  Monthly P&L:")
        lines.append("  | Month | Trades | Net P&L |")
        lines.append("  |---|---:|---:|")
        for _, r in monthly.iterrows():
            lines.append(f"  | {r['month']} | {int(r['count'])} | Rs {r['sum']:,.0f} |")
        lines.append("")

    # Per-trade logs (limit to 200 rows each to keep files manageable)
    lines.append("\n## Per-Trade Sample (first 100 of each)\n")
    for name, runner in runners.items():
        if not runner.trades:
            continue
        lines.append(f"### {name} — first 100 trades\n")
        lines.append("| Day | Entry | Exit | Reg | Dir | Strike | Entry₹ | Exit₹ | Reason | Net P&L |")
        lines.append("|---|---|---|---|---|---:|---:|---:|---|---:|")
        for t in runner.trades[:100]:
            lines.append(
                f"| {t.day} | {t.entry_ts.strftime('%H:%M')} "
                f"| {t.exit_ts.strftime('%H:%M') if t.exit_ts else '-'} "
                f"| {t.regime_at_entry} | {t.direction} | {t.strike} "
                f"| {t.entry_premium:.0f} | {t.exit_premium:.0f} "
                f"| {t.exit_reason} | {t.net_pnl:,.0f} |"
            )
        lines.append("")

    (out_dir / "phase9_tactics_comparison.md").write_text("\n".join(lines))
    print(f"\nReport: reports/phase9_tactics_comparison.md")

    # Console summary
    print("\n=== TACTIC RESULTS ===")
    for name, runner in runners.items():
        s = _summarize(runner.trades)
        pf = s["profit_factor"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"  {name:18} trades={s['trades']:3}  win%={s['win_rate']:5.1f}  "
              f"net=Rs {s['net_pnl']:+10,.0f}  PF={pf_str}")


if __name__ == "__main__":
    run()
