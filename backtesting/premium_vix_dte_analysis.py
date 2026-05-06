"""How did option premiums behave across VIX regimes + days-to-expiry?

For every trading day with option data:
  1. Determine the active expiry and days-to-expiry (DTE).
  2. At 09:30 IST, find ATM strike and capture CE+PE LTP (entry premium).
  3. Track ATM CE+PE through the day - record peak combined premium.
  4. At 15:25 IST, capture exit premium.
  5. Cross-reference with that day's VIX close (regime: Low/Normal/Elevated/High).

Builds a master table linking VIX regime, DTE, and straddle behaviour.
Shows where buying ATM straddle at 09:30 actually paid off.

Output:
  reports/premium_vix_dte_daily.csv  (one row per day)
  reports/premium_vix_dte_summary.md (regime + DTE pivot tables)
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from pathlib import Path

import pandas as pd

import backtesting.expiry_gamma_hero as base
from backtesting.tactic_extreme_reversal_alldays import (
    discover_trading_days, build_expiry_lookup, find_active_expiry,
)

ENTRY_TIME = dtime(9, 30)
EXIT_TIME = dtime(15, 25)
STRIKE_STEP = 50
LOT_SIZE = 65
CAPITAL = 20_000.0


def vix_regime(v):
    if v < 12: return "Low"
    if v < 15: return "Normal"
    if v < 18: return "Elevated"
    return "High"


def per_day_premium(trade_date: datetime, exp_token: str, exp_date: datetime,
                    spot_df, vix_df) -> dict:
    """Return one row of premium stats for this trading day."""
    tz = spot_df.index.tz
    entry_ts = pd.Timestamp.combine(trade_date.date(), ENTRY_TIME).tz_localize(tz)
    exit_ts = pd.Timestamp.combine(trade_date.date(), EXIT_TIME).tz_localize(tz)

    spot_entry = base.get_value_at(spot_df, entry_ts, "close")
    spot_exit = base.get_value_at(spot_df, exit_ts, "close")
    if spot_entry is None:
        return None
    atm = int(round(spot_entry / STRIKE_STEP) * STRIKE_STEP)

    ce_df = base.load_option(atm, "CE", exp_token)
    pe_df = base.load_option(atm, "PE", exp_token)
    if ce_df is None or pe_df is None:
        return None

    ce_in = base.get_value_at(ce_df, entry_ts, "close")
    pe_in = base.get_value_at(pe_df, entry_ts, "close")
    if ce_in is None or pe_in is None:
        return None
    combined_in = ce_in + pe_in

    # Peak straddle within the day (after entry)
    intra_ce = ce_df[(ce_df.index >= entry_ts) & (ce_df.index <= exit_ts)]
    intra_pe = pe_df[(pe_df.index >= entry_ts) & (pe_df.index <= exit_ts)]
    if intra_ce.empty or intra_pe.empty:
        return None
    # Align indexes (they should mostly coincide minute-by-minute)
    common = intra_ce.index.intersection(intra_pe.index)
    if len(common) == 0:
        return None
    combined_path = (intra_ce.loc[common, "close"]
                     + intra_pe.loc[common, "close"])
    peak = float(combined_path.max())
    peak_ts = combined_path.idxmax()
    trough = float(combined_path.min())

    ce_out = base.get_value_at(ce_df, exit_ts, "close")
    pe_out = base.get_value_at(pe_df, exit_ts, "close")
    if ce_out is None or pe_out is None:
        ce_out = float(intra_ce["close"].iloc[-1])
        pe_out = float(intra_pe["close"].iloc[-1])
    combined_out = ce_out + pe_out

    # Hold-to-EOD P&L
    lots = int(CAPITAL // (combined_in * LOT_SIZE)) if combined_in > 0 else 0
    qty = lots * LOT_SIZE
    pnl_hold = (combined_out - combined_in - 0.10) * qty - 120 if qty > 0 else 0
    pnl_peak = (peak - combined_in - 0.10) * qty - 120 if qty > 0 else 0

    # VIX for that day
    day_vix = vix_df[vix_df.index.date == trade_date.date()]
    vix_close = float(day_vix["close"].iloc[-1]) if len(day_vix) else None
    if vix_close is None:
        return None

    return {
        "trade_date": trade_date.date().isoformat(),
        "expiry": exp_token,
        "dte": (exp_date.date() - trade_date.date()).days,
        "vix_close": round(vix_close, 2),
        "vix_regime": vix_regime(vix_close),
        "spot_at_0930": round(spot_entry, 2),
        "spot_at_1525": round(spot_exit or 0, 2),
        "spot_move": round((spot_exit or 0) - spot_entry, 2),
        "atm": atm,
        "ce_at_0930": round(ce_in, 2),
        "pe_at_0930": round(pe_in, 2),
        "straddle_in": round(combined_in, 2),
        "straddle_peak": round(peak, 2),
        "straddle_peak_ts": peak_ts.strftime("%H:%M") if isinstance(peak_ts, pd.Timestamp) else "",
        "straddle_trough": round(trough, 2),
        "straddle_out": round(combined_out, 2),
        "lots": lots, "qty": qty,
        "pnl_hold_to_1525": int(pnl_hold),
        "pnl_peak_exit": int(pnl_peak),
        "peak_uplift_pct": round((peak - combined_in) / combined_in * 100, 1)
            if combined_in > 0 else 0,
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
    print(f"Loaded {len(expiries)} expiries.")

    trading_days = discover_trading_days(spot_df)
    latest_expiry = expiries_sorted[-1][1].date()
    trading_days = [d for d in trading_days if d.date() <= latest_expiry]
    print(f"Trading days to scan: {len(trading_days)}\n")

    rows = []
    for i, td in enumerate(trading_days):
        active = find_active_expiry(td, expiries_sorted)
        if active is None:
            continue
        tok, exp_date = active
        try:
            r = per_day_premium(td, tok, exp_date, spot_df, vix_df)
            if r is not None:
                rows.append(r)
        except Exception as e:
            print(f"  {td.date()} ({tok}): ERR {e}")
        if (i + 1) % 100 == 0:
            print(f"  progress: {i+1}/{len(trading_days)}, "
                  f"valid days: {len(rows)}")

    if not rows:
        print("\nNo data extracted.")
        return

    df = pd.DataFrame(rows)
    out_csv = base.REPORTS_DIR / "premium_vix_dte_daily.csv"
    df.to_csv(out_csv, index=False)
    print(f"\n{'='*70}")
    print(f"Days analysed: {len(df)}")

    # Aggregate: by VIX regime
    by_vix = df.groupby("vix_regime").agg(
        n_days=("trade_date", "count"),
        avg_vix=("vix_close", "mean"),
        avg_straddle_in=("straddle_in", "mean"),
        avg_straddle_peak=("straddle_peak", "mean"),
        avg_straddle_out=("straddle_out", "mean"),
        avg_peak_uplift=("peak_uplift_pct", "mean"),
        avg_pnl_hold=("pnl_hold_to_1525", "mean"),
        avg_pnl_peak=("pnl_peak_exit", "mean"),
        pct_winners_hold=("pnl_hold_to_1525", lambda x: (x > 0).mean() * 100),
    ).round(1)

    by_dte = df.groupby("dte").agg(
        n_days=("trade_date", "count"),
        avg_vix=("vix_close", "mean"),
        avg_straddle_in=("straddle_in", "mean"),
        avg_straddle_out=("straddle_out", "mean"),
        avg_peak_uplift=("peak_uplift_pct", "mean"),
        avg_pnl_hold=("pnl_hold_to_1525", "mean"),
        pct_winners_hold=("pnl_hold_to_1525", lambda x: (x > 0).mean() * 100),
    ).round(1)

    # Pivot: regime x dte for avg P&L
    pivot = df.pivot_table(
        index="vix_regime", columns="dte",
        values="pnl_hold_to_1525", aggfunc="mean"
    ).round(0)

    print("\n--- By VIX regime ---")
    print(by_vix.to_string())
    print("\n--- By Days-to-Expiry ---")
    print(by_dte.to_string())
    print("\n--- Pivot: VIX regime x DTE (avg straddle P&L hold-to-15:25) ---")
    print(pivot.to_string())

    md = ["# Premium behaviour: VIX regime x DTE x straddle\n",
          f"Days analysed: **{len(df)}**\n",
          "ATM straddle entered at 09:30 IST, exited at 15:25 IST.",
          f"Capital Rs.{int(CAPITAL):,}/trade. Lot {LOT_SIZE}.\n",
          "## By VIX regime\n```",
          by_vix.to_string(), "```", "",
          "## By Days-to-Expiry\n```",
          by_dte.to_string(), "```", "",
          "## VIX regime x DTE pivot (avg straddle P&L)\n```",
          pivot.to_string(), "```", ""]
    md_path = base.REPORTS_DIR / "premium_vix_dte_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
