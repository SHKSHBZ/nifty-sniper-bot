"""T1 VIX-Direction parameter sweep.

Sweeps key parameters of the tuned VIX-direction signal to find a
better config than baseline (+25k, 11 trades, 54.5% win rate).

Grid:
  vix_min:     [12, 13, 14, 15]
  vix_max:     [17, 18, 19, 20]
  vix_change:  [0.5, 1.0, 1.5, 2.0]    %
  decision:    ["10:00", "10:30", "11:00"]
  tp_pct:      [25, 30, 40]
  sl_pct:      [25, 30, 40]
  trail_be:    [10, 15, 20]            % uplift trigger

Full grid is large; we sweep coarsely then refine.
Output: reports/t1_vix_param_sweep.csv (all results)
        reports/t1_vix_param_sweep_summary.md (top 10 by P&L)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

import backtesting.expiry_gamma_hero as base
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)


# Fixed
DAY_OPEN_TIME = dtime(9, 15)
EOD_TIME = dtime(15, 25)
MIN_PREMIUM = 20.0
MAX_PREMIUM = 200.0
CAPITAL = 20_000.0
LOT_SIZE = 65
STRIKE_STEP = 50
SLIPPAGE_PER_LEG = 0.05
BROKERAGE = 60.0


def run_variant(decision_time, vix_min, vix_max, vix_change, tp_pct, sl_pct,
                trail_be_pct, spot_df, vix_df, expiries_sorted, trading_days):
    tz = spot_df.index.tz
    trades_pnl = []
    n_signals = 0
    n_wins = 0

    for td in trading_days:
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        day_open_ts = pd.Timestamp.combine(td.date(), DAY_OPEN_TIME).tz_localize(tz)
        decision_ts = pd.Timestamp.combine(td.date(), decision_time).tz_localize(tz)
        eod_ts = pd.Timestamp.combine(td.date(), EOD_TIME).tz_localize(tz)

        vix_open = base.get_value_at(vix_df, day_open_ts, "close")
        vix_now = base.get_value_at(vix_df, decision_ts, "close")
        if vix_open is None or vix_now is None or vix_open <= 0:
            continue
        if not (vix_min <= vix_now < vix_max):
            continue
        vix_pct = (vix_now / vix_open - 1) * 100
        if abs(vix_pct) < vix_change:
            continue
        side = "PE" if vix_pct >= 0 else "CE"
        spot = base.get_value_at(spot_df, decision_ts, "close")
        if spot is None:
            continue
        atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
        opt_df = base.load_option(atm, side, tok)
        if opt_df is None:
            continue
        prem = base.get_value_at(opt_df, decision_ts, "close")
        if prem is None or not (MIN_PREMIUM <= prem <= MAX_PREMIUM):
            continue
        lots = int(CAPITAL // (prem * LOT_SIZE))
        if lots < 1:
            continue
        qty = lots * LOT_SIZE

        tp_p = prem * (1 + tp_pct / 100)
        sl_p = prem * (1 - sl_pct / 100)
        trail_arm_p = prem * (1 + trail_be_pct / 100)

        minutes = pd.date_range(decision_ts + pd.Timedelta(minutes=1),
                                eod_ts, freq="1min")
        exit_p = None
        trail_armed = False
        for ts in minutes:
            cur = base.get_value_at(opt_df, ts, "close")
            if cur is None:
                continue
            if not trail_armed and cur >= trail_arm_p:
                trail_armed = True
                sl_p = prem  # BE
            if cur >= tp_p:
                exit_p = cur
                break
            if cur <= sl_p:
                exit_p = cur
                break
        if exit_p is None:
            exit_p = base.get_value_at(opt_df, eod_ts, "close")
            if exit_p is None:
                sub = opt_df[opt_df.index <= eod_ts]
                exit_p = float(sub["close"].iloc[-1]) if len(sub) else 0.0

        eff_in = prem + SLIPPAGE_PER_LEG
        eff_out = max(0.0, exit_p - SLIPPAGE_PER_LEG)
        net = (eff_out - eff_in) * qty - BROKERAGE
        trades_pnl.append(net)
        n_signals += 1
        if net > 0:
            n_wins += 1

    if not trades_pnl:
        return None
    arr = pd.Series(trades_pnl)
    cum = arr.cumsum()
    dd = (cum.cummax() - cum).max()
    return {
        "decision": decision_time.strftime("%H:%M"),
        "vix_min": vix_min, "vix_max": vix_max, "vix_chg": vix_change,
        "tp": tp_pct, "sl": sl_pct, "trail_be": trail_be_pct,
        "n": n_signals, "wins": n_wins,
        "win_rate": round(n_wins / n_signals * 100, 1) if n_signals else 0,
        "total": int(arr.sum()),
        "avg": int(arr.mean()),
        "dd": int(dd),
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
    trading_days = discover_trading_days(spot_df)
    latest = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest]

    # COARSE GRID — keep it manageable
    decisions = [dtime(10, 0), dtime(10, 30), dtime(11, 0)]
    bands = [(12, 18), (13, 18), (15, 18), (12, 20), (15, 20)]
    changes = [0.5, 1.0, 1.5]
    tp_sl_pairs = [(30, 30), (40, 25), (25, 30), (50, 30)]
    trail_bes = [15, 20]

    print(f"Sweeping ~{len(decisions)*len(bands)*len(changes)*len(tp_sl_pairs)*len(trail_bes)} combos.\n")
    results = []
    n_done = 0
    for dec in decisions:
        for vmin, vmax in bands:
            for chg in changes:
                for tp, sl in tp_sl_pairs:
                    for tbe in trail_bes:
                        r = run_variant(dec, vmin, vmax, chg, tp, sl, tbe,
                                        spot_df, vix_df, expiries_sorted,
                                        trading_days)
                        if r is not None:
                            results.append(r)
                        n_done += 1
                        if n_done % 50 == 0:
                            print(f"  {n_done} combos tested, "
                                  f"{len(results)} produced trades")

    if not results:
        print("No combos produced trades.")
        return

    df = pd.DataFrame(results)
    out_csv = base.REPORTS_DIR / "t1_vix_param_sweep.csv"
    df.to_csv(out_csv, index=False)
    top = df.sort_values("total", ascending=False).head(15)

    print(f"\n{'='*100}")
    print(f"Total combos with trades: {len(df)}")
    print(f"Best total P&L: Rs.{int(df['total'].max()):,}")
    print(f"\nTop 15 by total P&L:\n")
    print(top.to_string(index=False))

    md = ["# T1 VIX-Direction Parameter Sweep\n",
          f"Total combos tested: {len(df)}\n",
          "## Top 15 by total P&L\n```",
          top.to_string(index=False), "```", "",
          "## Best by win-rate (n>=10)\n```",
          df[df["n"] >= 10].sort_values("win_rate", ascending=False)
              .head(10).to_string(index=False),
          "```", ""]
    md_path = base.REPORTS_DIR / "t1_vix_param_sweep_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
