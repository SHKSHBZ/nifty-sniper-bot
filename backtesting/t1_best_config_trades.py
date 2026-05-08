"""Re-run T1 VIX-direction with the BEST config from the param sweep,
saving the full trade ledger.

Best config (from t1_vix_param_sweep):
  decision  10:00 IST
  vix band  [13, 18)
  vix chg   >= 0.5%
  tp/sl     30%/30%
  trail_be  +20%
  -> 54 trades, 55.6% win rate, +71,827 net
"""
from __future__ import annotations

import backtesting.expiry_gamma_hero as base
import backtesting.vix_direction_signal_tuned as base_t1
from datetime import time as dtime
import pandas as pd
from dataclasses import asdict
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)

# Override with best params
base_t1.DECISION_TIME = dtime(10, 0)
base_t1.VIX_REGIME_MIN = 13.0
base_t1.VIX_REGIME_MAX = 18.0
base_t1.VIX_CHANGE_THRESHOLD = 0.5
base_t1.TP_PCT = 30.0
base_t1.SL_PCT = 30.0
base_t1.TRAIL_BE_TRIGGER_PCT = 20.0


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
    print(f"Days: {len(trading_days)}\n")

    trades = []
    for td in trading_days:
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        try:
            t = base_t1.run_for_day(td, tok, exp_date, spot_df, vix_df)
            if t is not None:
                trades.append(t)
        except Exception:
            continue

    print(f"Trades fired: {len(trades)}")
    if not trades:
        return
    df = pd.DataFrame([asdict(t) for t in trades])
    out = base.REPORTS_DIR / "t1_best_config_trades.csv"
    df.to_csv(out, index=False)
    total = df["net_pnl"].sum()
    wins = (df["net_pnl"] > 0).sum()
    print(f"Total: Rs.{int(total):,}  Win rate: {wins}/{len(df)}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
