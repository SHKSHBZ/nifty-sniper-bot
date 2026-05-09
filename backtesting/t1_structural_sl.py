"""T1 with STRUCTURAL stop-loss (break of S/R level) instead of % of premium.

Logic:
  - CE entry (bullish bias from VIX falling):
      SL = spot drops below SUPPORT level
  - PE entry (bearish bias from VIX rising):
      SL = spot rises above RESISTANCE level
  - TP and trail-BE remain premium-based

This way the SL only fires when the directional thesis is genuinely
broken, not on noise/IV crush. Hold winners until either TP hits or
direction inverts.

Tests:
  S/R method:  cam_S3_R3, cam_S4_R4, classic_S1_R1, prev_low_high
  TP %:        30, 50, 75, 100, 150
  Buffer:      0 (exact level), 10pts (allow noise tolerance)

24 combinations.
"""
from __future__ import annotations

from datetime import time as dtime
from pathlib import Path

import pandas as pd

import backtesting.expiry_gamma_hero as base
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)


# Fixed (best) base config
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


SR_METHODS = {
    "cam_3":     ("cam_S3", "cam_R3"),
    "cam_4":     ("cam_S4", "cam_R4"),
    "classic_1": ("pivot_S1", "pivot_R1"),
    "prev_hl":   ("prev_low", "prev_high"),
}


def load_sr() -> pd.DataFrame:
    sr = pd.read_csv(base.REPORTS_DIR / "sr_levels_nifty.csv")
    sr["date"] = pd.to_datetime(sr["date"]).dt.date
    return sr.set_index("date")


def run_variant(method, tp_pct, buffer_pts, spot_df, vix_df,
                sr_df, expiries_sorted, trading_days):
    sup_col, res_col = SR_METHODS[method]
    tz = spot_df.index.tz
    pnl_list = []
    n_tp = n_break = n_be = n_eod = 0
    for td in trading_days:
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        d = td.date()
        if d not in sr_df.index:
            continue
        sr_row = sr_df.loc[d]
        sup = sr_row.get(sup_col)
        res = sr_row.get(res_col)
        if sup is None or res is None or pd.isna(sup) or pd.isna(res):
            continue

        day_open_ts = pd.Timestamp.combine(d, DAY_OPEN_TIME).tz_localize(tz)
        decision_ts = pd.Timestamp.combine(d, DECISION_TIME).tz_localize(tz)
        eod_ts = pd.Timestamp.combine(d, EOD_TIME).tz_localize(tz)
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
        trail_arm_p = prem * (1 + TRAIL_BE_PCT / 100)
        sl_premium_be = None  # set when trail-BE arms

        # Define structural break level
        # CE (bullish): break below support
        # PE (bearish): break above resistance
        if side == "CE":
            sl_spot_threshold = sup - buffer_pts
        else:
            sl_spot_threshold = res + buffer_pts

        minutes = pd.date_range(decision_ts + pd.Timedelta(minutes=1),
                                eod_ts, freq="1min")
        exit_p = None
        exit_reason = "EOD"
        trail_armed = False
        for ts in minutes:
            cur = base.get_value_at(opt_df, ts, "close")
            cur_spot = base.get_value_at(spot_df, ts, "close")
            if cur is None or cur_spot is None:
                continue
            # Trail to BE
            if not trail_armed and cur >= trail_arm_p:
                trail_armed = True
                sl_premium_be = prem
            # TP check
            if cur >= tp_p:
                exit_p = cur
                exit_reason = "TP"
                break
            # BE stop check (only if armed)
            if trail_armed and cur <= sl_premium_be:
                exit_p = cur
                exit_reason = "BE_STOP"
                break
            # Structural SL check
            if side == "CE" and cur_spot <= sl_spot_threshold:
                exit_p = cur
                exit_reason = "BREAK_SR"
                break
            if side == "PE" and cur_spot >= sl_spot_threshold:
                exit_p = cur
                exit_reason = "BREAK_SR"
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
        elif exit_reason == "BREAK_SR":
            n_break += 1
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
        "sr_method": method, "tp_pct": tp_pct, "buffer": buffer_pts,
        "n": len(pnl_list),
        "win_rate": round((arr > 0).mean() * 100, 1),
        "total": int(arr.sum()),
        "avg": int(arr.mean()),
        "dd": int(dd),
        "best": int(arr.max()),
        "worst": int(arr.min()),
        "tp": n_tp, "break_sr": n_break, "be": n_be, "eod": n_eod,
    }


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts").sort_index()
    vix_df = pd.read_csv(base.DATA_DIR / "INDIA_VIX_1minute.csv")
    vix_df["ts"] = pd.to_datetime(vix_df["timestamp"])
    vix_df = vix_df.set_index("ts").sort_index()
    sr_df = load_sr()
    expiries = base.discover_expiries()
    expiries_sorted = build_expiry_lookup(expiries)
    trading_days = discover_trading_days(spot_df)
    latest = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest]
    print(f"Days: {len(trading_days)}\n")

    methods = list(SR_METHODS.keys())
    tps = [30, 50, 75, 100, 150]
    buffers = [0, 10]

    results = []
    for m in methods:
        for tp in tps:
            for buf in buffers:
                r = run_variant(m, tp, buf, spot_df, vix_df, sr_df,
                                expiries_sorted, trading_days)
                if r is not None:
                    results.append(r)
                    print(f"  {m}/TP{tp}/buf{buf}: n={r['n']}, "
                          f"win={r['win_rate']}%, total={r['total']:,}, "
                          f"dd={r['dd']:,} | tp/break/be/eod="
                          f"{r['tp']}/{r['break_sr']}/{r['be']}/{r['eod']}")

    df = pd.DataFrame(results)
    out_csv = base.REPORTS_DIR / "t1_structural_sl_sweep.csv"
    df.to_csv(out_csv, index=False)
    top = df.sort_values("total", ascending=False).head(15)

    print(f"\n{'='*100}")
    print(f"BEST 15 by total P&L:")
    print(top.to_string(index=False))

    md = ["# T1 with Structural Stop-Loss\n",
          "Same T1 entry signal (best base config: 10:00, VIX 13-18, "
          "intraday |chg|>=0.5%, trail-BE +20%).",
          "STOP-LOSS REDESIGNED: instead of -30% premium, uses spot "
          "break-of-S/R-level.\n",
          "## Top 15 by P&L\n```", top.to_string(index=False), "```", "",
          "## All variants by win rate\n```",
          df.sort_values("win_rate", ascending=False).head(20).to_string(index=False),
          "```", ""]
    md_path = base.REPORTS_DIR / "t1_structural_sl_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
