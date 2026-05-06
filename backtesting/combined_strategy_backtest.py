"""Combined strategy backtest: VIX-Direction + Straddle S2.

Runs BOTH strategies on the same 489-day dataset with separate
capital pools (Rs.20k each = Rs.40k total working capital):

  Strategy A: VIX-Direction Tuned (decision 10:30 IST)
    - Only when 15 <= VIX < 18 AND |VIX intraday change| >= 1%
    - Buy ATM CE if VIX falling, ATM PE if VIX rising
    - TP +30%, SL -30%, trail to BE at +15%, EOD 15:25

  Strategy B: Long Straddle (decision 14:50 IST, expiry day only)
    - Buy ATM CE + ATM PE if both premiums in Rs.5-20
    - SL at -50% combined, no TP, EOD 15:25

The two strategies trade on different days and different times,
so they're complementary, not overlapping. Separate ledgers.

Output:
  reports/combined_strategy_trades.csv
  reports/combined_strategy_summary.md
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time as dtime
from pathlib import Path

import pandas as pd

import backtesting.expiry_gamma_hero as base
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)
from backtesting.vix_direction_signal_tuned import (
    run_for_day as run_vix_signal,
)


# Straddle strategy params
STRADDLE_ENTRY_TIME = dtime(14, 50)
STRADDLE_EXIT_TIME = dtime(15, 25)
STRADDLE_MIN_PREMIUM = 5.0
STRADDLE_MAX_PREMIUM = 20.0
STRADDLE_SL_PCT = 50.0
STRADDLE_LOT = 65
STRADDLE_CAPITAL = 20_000.0
STRADDLE_BROKERAGE = 120.0
SLIPPAGE_PER_LEG = 0.05


def run_straddle_for_expiry(expiry_token: str, expiry_date: datetime,
                             spot_df: pd.DataFrame):
    """Mirror of expiry_straddle.S2 (SL-only) — single trade per expiry."""
    tz = spot_df.index.tz
    entry_ts = pd.Timestamp.combine(expiry_date.date(),
                                    STRADDLE_ENTRY_TIME).tz_localize(tz)
    exit_ts = pd.Timestamp.combine(expiry_date.date(),
                                   STRADDLE_EXIT_TIME).tz_localize(tz)

    spot = base.get_value_at(spot_df, entry_ts, "close")
    if spot is None:
        return None
    atm = int(round(spot / 50) * 50)
    ce = base.load_option(atm, "CE", expiry_token)
    pe = base.load_option(atm, "PE", expiry_token)
    if ce is None or pe is None:
        return None
    ce_in = base.get_value_at(ce, entry_ts, "close")
    pe_in = base.get_value_at(pe, entry_ts, "close")
    if ce_in is None or pe_in is None:
        return None
    if not (STRADDLE_MIN_PREMIUM <= ce_in <= STRADDLE_MAX_PREMIUM):
        return None
    if not (STRADDLE_MIN_PREMIUM <= pe_in <= STRADDLE_MAX_PREMIUM):
        return None
    combined = ce_in + pe_in
    lots = int(STRADDLE_CAPITAL // (combined * STRADDLE_LOT))
    if lots < 1:
        return None
    qty = lots * STRADDLE_LOT
    sl_combined = combined * (1 - STRADDLE_SL_PCT / 100)

    # Walk to find SL or hit EOD
    minutes = pd.date_range(entry_ts + pd.Timedelta(minutes=1),
                            exit_ts, freq="1min")
    exit_reason = "TIME_EXIT"
    ce_x = ce_in
    pe_x = pe_in
    final_ts = exit_ts
    for ts in minutes:
        ce_p = base.get_value_at(ce, ts, "close")
        pe_p = base.get_value_at(pe, ts, "close")
        if ce_p is None or pe_p is None:
            continue
        ce_x, pe_x = ce_p, pe_p
        if (ce_p + pe_p) <= sl_combined:
            exit_reason = "SL"
            final_ts = ts
            break

    if exit_reason == "TIME_EXIT":
        ce_x = base.get_value_at(ce, exit_ts, "close")
        pe_x = base.get_value_at(pe, exit_ts, "close")
        if ce_x is None:
            sub = ce[ce.index <= exit_ts]
            ce_x = float(sub["close"].iloc[-1]) if len(sub) else 0.0
        if pe_x is None:
            sub = pe[pe.index <= exit_ts]
            pe_x = float(sub["close"].iloc[-1]) if len(sub) else 0.0
        final_ts = exit_ts

    eff_in = combined + 2 * SLIPPAGE_PER_LEG
    eff_out = max(0.0, ce_x + pe_x - 2 * SLIPPAGE_PER_LEG)
    net = (eff_out - eff_in) * qty - STRADDLE_BROKERAGE
    return {
        "trade_date": expiry_date.date().isoformat(),
        "strategy": "straddle_S2",
        "entry_ts": entry_ts.isoformat(),
        "side": "CE+PE",
        "strike": atm,
        "entry_premium": round(combined, 2),
        "lots": lots, "qty": qty,
        "exit_ts": final_ts.isoformat(),
        "exit_premium": round(ce_x + pe_x, 2),
        "exit_reason": exit_reason,
        "net_pnl": round(net, 2),
    }


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts").sort_index()

    vix_df = pd.read_csv(base.DATA_DIR / "INDIA_VIX_1minute.csv")
    vix_df["ts"] = pd.to_datetime(vix_df["timestamp"])
    vix_df = vix_df.set_index("ts").sort_index()

    expiries = base.discover_expiries()
    expiries_sorted = build_expiry_lookup(expiries)
    expiry_dates = {dt.date() for _, dt in expiries_sorted}
    expiry_lookup = {dt.date(): tok for tok, dt in expiries_sorted}
    print(f"Loaded {len(expiries)} expiries.")

    trading_days = discover_trading_days(spot_df)
    latest_expiry = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest_expiry]
    print(f"Trading days to scan: {len(trading_days)}\n")

    vix_trades = []
    straddle_trades = []
    for i, td in enumerate(trading_days):
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        # --- Strategy A: VIX-Direction at 10:30 ---
        try:
            t = run_vix_signal(td, tok, exp_date, spot_df, vix_df)
            if t is not None:
                d = asdict(t)
                d["strategy"] = "vix_direction"
                vix_trades.append(d)
        except Exception as e:
            print(f"  vix {td.date()}: {e}")
        # --- Strategy B: Straddle at 14:50 (expiry day only) ---
        if td.date() in expiry_dates:
            try:
                s = run_straddle_for_expiry(tok, td, spot_df)
                if s is not None:
                    straddle_trades.append(s)
            except Exception as e:
                print(f"  straddle {td.date()}: {e}")
        if (i + 1) % 100 == 0:
            print(f"  progress: {i+1}/{len(trading_days)}, "
                  f"vix={len(vix_trades)} straddle={len(straddle_trades)}")

    print(f"\n{'='*70}")
    print("STRATEGY A: VIX-Direction Tuned")
    if vix_trades:
        v = pd.DataFrame(vix_trades)
        n_v = len(v)
        wins_v = (v["net_pnl"] > 0).sum()
        total_v = v["net_pnl"].sum()
        cum_v = v["net_pnl"].cumsum()
        dd_v = (cum_v.cummax() - cum_v).max()
        print(f"  Trades:  {n_v}")
        print(f"  Wins:    {wins_v}/{n_v} = {wins_v/n_v*100:.1f}%")
        print(f"  Total:   Rs.{int(total_v):,}")
        print(f"  Avg:     Rs.{int(v['net_pnl'].mean()):,}")
        print(f"  Max DD:  Rs.{int(dd_v):,}")
    else:
        n_v = 0; total_v = 0; dd_v = 0

    print(f"\nSTRATEGY B: Long Straddle S2 (SL only, expiry-day)")
    if straddle_trades:
        s = pd.DataFrame(straddle_trades)
        n_s = len(s)
        wins_s = (s["net_pnl"] > 0).sum()
        total_s = s["net_pnl"].sum()
        cum_s = s["net_pnl"].cumsum()
        dd_s = (cum_s.cummax() - cum_s).max()
        print(f"  Trades:  {n_s}")
        print(f"  Wins:    {wins_s}/{n_s} = {wins_s/n_s*100:.1f}%")
        print(f"  Total:   Rs.{int(total_s):,}")
        print(f"  Avg:     Rs.{int(s['net_pnl'].mean()):,}")
        print(f"  Max DD:  Rs.{int(dd_s):,}")
    else:
        n_s = 0; total_s = 0; dd_s = 0

    # Combined
    combined = vix_trades + straddle_trades
    if combined:
        c = pd.DataFrame(combined)
        c["entry_dt"] = pd.to_datetime(c["entry_ts"])
        c = c.sort_values("entry_dt").reset_index(drop=True)
        n_c = len(c)
        wins_c = (c["net_pnl"] > 0).sum()
        total_c = c["net_pnl"].sum()
        cum_c = c["net_pnl"].cumsum()
        dd_c = (cum_c.cummax() - cum_c).max()
        avg_c = c["net_pnl"].mean()
        # Same-day overlaps (when both strategies fire same day)
        c["date"] = pd.to_datetime(c["entry_ts"]).dt.date
        same_day = c.groupby("date").size()
        n_overlap_days = (same_day > 1).sum()

        print(f"\n{'='*70}")
        print("COMBINED PORTFOLIO")
        print(f"  Total trades:    {n_c} (vix {n_v} + straddle {n_s})")
        print(f"  Same-day overlaps: {n_overlap_days} days "
              f"both strategies fired")
        print(f"  Win rate:        {wins_c}/{n_c} = {wins_c/n_c*100:.1f}%")
        print(f"  Total P&L:       Rs.{int(total_c):,}")
        print(f"  Avg / trade:     Rs.{int(avg_c):,}")
        print(f"  Combined DD:     Rs.{int(dd_c):,}")
        print(f"  Capital deployed: Rs.40,000 (20k per strategy)")
        print(f"  Annual estimate ({n_c}/489 days * 250 days/yr): "
              f"~Rs.{int(total_c/489*250):,}/yr")
        print(f"{'='*70}")

        out_csv = base.REPORTS_DIR / "combined_strategy_trades.csv"
        c.drop(columns=["entry_dt", "date"], errors="ignore").to_csv(
            out_csv, index=False
        )

        md = ["# Combined Strategy Backtest\n",
              "Two strategies, separate Rs.20k pools, run on same dataset:\n",
              "  - **A: VIX-Direction Tuned** (10:30 IST decision)",
              "  - **B: Long Straddle S2** (14:50 IST, expiry-day only)",
              "",
              "## Strategy A — VIX-Direction\n",
              f"- Trades: {n_v}",
              f"- Total: **Rs.{int(total_v):,}**",
              f"- Max DD: Rs.{int(dd_v):,}",
              "",
              "## Strategy B — Straddle S2\n",
              f"- Trades: {n_s}",
              f"- Total: **Rs.{int(total_s):,}**",
              f"- Max DD: Rs.{int(dd_s):,}",
              "",
              "## Combined Portfolio\n",
              f"- Total trades:    **{n_c}**",
              f"- Same-day overlap: {n_overlap_days} days",
              f"- Win rate:        **{wins_c}/{n_c} = {wins_c/n_c*100:.1f}%**",
              f"- Total P&L:       **Rs.{int(total_c):,}**",
              f"- Avg / trade:     Rs.{int(avg_c):,}",
              f"- Combined DD:     Rs.{int(dd_c):,}",
              f"- Capital:         Rs.40,000 (20k/strategy)",
              f"- Annual est:      ~Rs.{int(total_c/489*250):,}/yr",
              ""]
        md_path = base.REPORTS_DIR / "combined_strategy_summary.md"
        md_path.write_text("\n".join(md), encoding="utf-8")
        print(f"\nWrote {out_csv}")
        print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
