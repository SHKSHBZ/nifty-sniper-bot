"""SENSEX straddle diagnostic: try multiple premium gates and dump
all 74 expiries' ATM premiums so we can see what range to use.

Step 1: dump per-expiry CE/PE entry premiums at 14:50.
Step 2: re-run S2 (SL-only, no TP — best NIFTY config) across several
        premium-gate ranges to see which works on SENSEX.
"""
from __future__ import annotations

from datetime import time as dtime
from pathlib import Path

import pandas as pd

import backtesting.expiry_straddle_sensex as base


GATES = [
    (5,   20),    # NIFTY-matching (baseline)
    (5,   50),    # widen
    (10,  50),    # widen + raise floor
    (20,  100),   # SENSEX-realistic (premiums scale ~3x)
    (50,  200),   # mid-IV days
    (5,   500),   # near no-gate
]


def dump_premiums(spot_df, expiries):
    """For each expiry, show CE/PE at 14:50 so user can see the range."""
    rows = []
    tz = spot_df.index.tz
    for tok, dt in expiries:
        entry_ts = pd.Timestamp.combine(dt.date(), dtime(14, 50)).tz_localize(tz)
        spot = base.get_value_at(spot_df, entry_ts, "close")
        if spot is None:
            rows.append({"expiry": tok, "spot_1450": "", "atm": "", "ce": "", "pe": "", "combined": ""})
            continue
        atm = int(round(spot / base.STRIKE_STEP) * base.STRIKE_STEP)
        ce = base.load_option(atm, "CE", tok)
        pe = base.load_option(atm, "PE", tok)
        ce_p = base.get_value_at(ce, entry_ts, "close")
        pe_p = base.get_value_at(pe, entry_ts, "close")
        rows.append({
            "expiry": tok,
            "spot_1450": round(spot, 2),
            "atm": atm,
            "ce": round(ce_p, 2) if ce_p is not None else "",
            "pe": round(pe_p, 2) if pe_p is not None else "",
            "combined": round(ce_p + pe_p, 2) if (ce_p is not None and pe_p is not None) else "",
        })
    return pd.DataFrame(rows)


def run_with_gate(spot_df, expiries, min_p, max_p, tp, sl):
    """Re-run straddle with overridden premium gate."""
    saved_min = base.MIN_PREMIUM
    saved_max = base.MAX_PREMIUM
    base.MIN_PREMIUM = float(min_p)
    base.MAX_PREMIUM = float(max_p)
    try:
        trades = []
        for tok, dt in expiries:
            try:
                t = base.run_one_expiry(tok, dt, spot_df, tp, sl)
                if t is not None:
                    trades.append(t)
            except Exception:
                pass
        return trades
    finally:
        base.MIN_PREMIUM = saved_min
        base.MAX_PREMIUM = saved_max


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = base.discover_expiries()
    print(f"Loaded {len(expiries)} SENSEX expiries.\n")

    # Step 1: dump per-expiry premiums
    print("=== ATM premiums at 14:50 across all expiries ===")
    df = dump_premiums(spot_df, expiries)
    df.to_csv(base.REPORTS_DIR / "sensex_premium_dump.csv", index=False)
    valid = df[df["combined"] != ""]
    if len(valid):
        print(f"\nValid rows: {len(valid)}/{len(df)}")
        c = pd.to_numeric(valid["combined"], errors="coerce").dropna()
        print(f"Combined ATM premium (CE+PE) at 14:50:")
        print(f"  min:    ₹{c.min():.2f}")
        print(f"  10pct:  ₹{c.quantile(0.10):.2f}")
        print(f"  25pct:  ₹{c.quantile(0.25):.2f}")
        print(f"  median: ₹{c.median():.2f}")
        print(f"  75pct:  ₹{c.quantile(0.75):.2f}")
        print(f"  90pct:  ₹{c.quantile(0.90):.2f}")
        print(f"  max:    ₹{c.max():.2f}")
    print(f"Wrote per-expiry dump to {base.REPORTS_DIR / 'sensex_premium_dump.csv'}")

    # Step 2: try multiple gates with S2 (SL only, no TP)
    print("\n=== S2 (SL -50% only) across premium gates ===")
    print(f"{'gate (per leg)':<20} {'trades':>7} {'win%':>6} "
          f"{'total':>10} {'avg':>8} {'best':>8} {'worst':>9} {'DD':>10}")
    print("-" * 90)
    for min_p, max_p in GATES:
        trades = run_with_gate(spot_df, expiries, min_p, max_p, tp=None, sl=50.0)
        n = len(trades)
        if n == 0:
            print(f"Rs.{min_p}-{max_p:<14} {n:>7}")
            continue
        df_t = pd.DataFrame([t.__dict__ for t in trades])
        win = (df_t["net_pnl"] > 0).mean() * 100
        total = df_t["net_pnl"].sum()
        avg = df_t["net_pnl"].mean()
        best = df_t["net_pnl"].max()
        worst = df_t["net_pnl"].min()
        cum = df_t["net_pnl"].cumsum()
        dd = (cum.cummax() - cum).max()
        gate_label = f"Rs.{min_p}-{max_p}"
        print(f"{gate_label:<20} {n:>7} {win:>5.1f}% "
              f"Rs.{int(total):>7,} Rs.{int(avg):>5,} "
              f"Rs.{int(best):>5,} Rs.{int(worst):>6,} Rs.{int(dd):>7,}")

    # Save full premium dump for manual inspection
    out_md = base.REPORTS_DIR / "sensex_diag_summary.md"
    out_md.write_text("See sensex_premium_dump.csv for per-expiry premiums.\n",
                      encoding="utf-8")


if __name__ == "__main__":
    main()
