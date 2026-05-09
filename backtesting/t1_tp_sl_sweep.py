"""T1 focused TP / SL sweep.

Holds best base config constant (decision 10:00 IST, VIX [13,18),
intraday change >=0.5%, trail-BE at +20%) and sweeps:
  TP: 30, 40, 50, 60, 75, 100, 125, 150
  SL: 10, 15, 20, 25, 30, 40

Tests user's hypothesis: bigger TP + tighter SL = better edge.
"""
from __future__ import annotations

from datetime import time as dtime
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import backtesting.expiry_gamma_hero as base
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)


# Fixed (best) config
DECISION_TIME = dtime(10, 0)
VIX_MIN = 13.0
VIX_MAX = 18.0
VIX_CHANGE = 0.5
TRAIL_BE_PCT = 20.0

DAY_OPEN_TIME = dtime(9, 15)
EOD_TIME = dtime(15, 25)
MIN_PREMIUM = 20.0
MAX_PREMIUM = 200.0
CAPITAL = 20_000.0
LOT_SIZE = 65
STRIKE_STEP = 50
SLIPPAGE = 0.05
BROKERAGE = 60.0


def run_variant(tp_pct, sl_pct, spot_df, vix_df, expiries_sorted, trading_days):
    tz = spot_df.index.tz
    pnl_list = []
    n_tp = n_sl = n_be = n_eod = 0
    for td in trading_days:
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        day_open_ts = pd.Timestamp.combine(td.date(), DAY_OPEN_TIME).tz_localize(tz)
        decision_ts = pd.Timestamp.combine(td.date(), DECISION_TIME).tz_localize(tz)
        eod_ts = pd.Timestamp.combine(td.date(), EOD_TIME).tz_localize(tz)
        vix_open = base.get_value_at(vix_df, day_open_ts, "close")
        vix_now = base.get_value_at(vix_df, decision_ts, "close")
        if vix_open is None or vix_now is None or vix_open <= 0:
            continue
        if not (VIX_MIN <= vix_now < VIX_MAX):
            continue
        vix_pct = (vix_now / vix_open - 1) * 100
        if abs(vix_pct) < VIX_CHANGE:
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
        trail_arm_p = prem * (1 + TRAIL_BE_PCT / 100)
        minutes = pd.date_range(decision_ts + pd.Timedelta(minutes=1),
                                eod_ts, freq="1min")
        exit_p = None
        exit_reason = "EOD"
        trail_armed = False
        for ts in minutes:
            cur = base.get_value_at(opt_df, ts, "close")
            if cur is None:
                continue
            if not trail_armed and cur >= trail_arm_p:
                trail_armed = True
                sl_p = prem
            if cur >= tp_p:
                exit_p = cur
                exit_reason = "TP"
                break
            if cur <= sl_p:
                exit_p = cur
                exit_reason = "BE_STOP" if trail_armed else "SL"
                break
        if exit_p is None:
            exit_p = base.get_value_at(opt_df, eod_ts, "close")
            if exit_p is None:
                sub = opt_df[opt_df.index <= eod_ts]
                exit_p = float(sub["close"].iloc[-1]) if len(sub) else 0.0
        eff_in = prem + SLIPPAGE
        eff_out = max(0.0, exit_p - SLIPPAGE)
        net = (eff_out - eff_in) * qty - BROKERAGE
        pnl_list.append(net)
        if exit_reason == "TP":
            n_tp += 1
        elif exit_reason == "SL":
            n_sl += 1
        elif exit_reason == "BE_STOP":
            n_be += 1
        else:
            n_eod += 1

    if not pnl_list:
        return None
    arr = pd.Series(pnl_list)
    cum = arr.cumsum()
    dd = (cum.cummax() - cum).max()
    return {
        "tp_pct": tp_pct, "sl_pct": sl_pct,
        "n": len(pnl_list),
        "wins": int((arr > 0).sum()),
        "win_rate": round((arr > 0).mean() * 100, 1),
        "total": int(arr.sum()),
        "avg": int(arr.mean()),
        "dd": int(dd),
        "best": int(arr.max()),
        "worst": int(arr.min()),
        "tp_hits": n_tp, "sl_hits": n_sl,
        "be_hits": n_be, "eod_hits": n_eod,
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

    tps = [30, 40, 50, 60, 75, 100, 125, 150]
    sls = [10, 15, 20, 25, 30, 40]
    print(f"Sweeping {len(tps) * len(sls)} TP/SL combos across {len(trading_days)} days\n")

    results = []
    for tp in tps:
        for sl in sls:
            r = run_variant(tp, sl, spot_df, vix_df, expiries_sorted, trading_days)
            if r is not None:
                results.append(r)
                print(f"  TP+{tp}/SL-{sl}: n={r['n']}, win={r['win_rate']}%, "
                      f"total={r['total']:,}, dd={r['dd']:,}, "
                      f"tp/sl/be/eod={r['tp_hits']}/{r['sl_hits']}/{r['be_hits']}/{r['eod_hits']}")

    df = pd.DataFrame(results)
    out_csv = base.REPORTS_DIR / "t1_tp_sl_sweep.csv"
    df.to_csv(out_csv, index=False)
    top = df.sort_values("total", ascending=False).head(15)

    print(f"\n{'='*100}")
    print(f"BEST 15 by total P&L:")
    print(top.to_string(index=False))

    md = ["# T1 TP/SL Focused Sweep\n",
          f"Base config: decision 10:00, VIX [13,18), |chg|>=0.5%, trail-BE +20%.",
          f"Tested {len(df)} TP/SL combinations.\n",
          "## Top 15 by P&L\n```",
          top.to_string(index=False), "```", "",
          "## Top 15 by win rate (n>=20)\n```",
          df[df["n"] >= 20].sort_values("win_rate", ascending=False).head(15).to_string(index=False),
          "```", ""]
    md_path = base.REPORTS_DIR / "t1_tp_sl_sweep_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
