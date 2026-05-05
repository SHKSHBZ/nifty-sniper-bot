"""Run Expiry Gamma Hero across 4 variants and produce a comparison.

Variants (held constant: capital ₹20k, lot 65, premium ₹5–20, time-exit 15:25):

  V0  baseline       — OI ratio at ATM+50, tight stop (spot crosses ATM)
  V1  wider OI       — OI ratio at ATM strike itself, tight stop
  V2  wider stop     — OI at ATM+50, stop only after spot moves 30 pts past ATM
  V3  both           — OI at ATM, stop after spot moves 30 pts past ATM

Output: reports/expiry_gamma_hero_variants.md
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from backtesting import expiry_gamma_hero as base


VARIANTS = [
    {"name": "V0_baseline",   "oi_offset": 50, "stop_buffer": 0},
    {"name": "V1_wider_OI",   "oi_offset": 0,  "stop_buffer": 0},
    {"name": "V2_wider_stop", "oi_offset": 50, "stop_buffer": 30},
    {"name": "V3_both",       "oi_offset": 0,  "stop_buffer": 30},
]


def evaluate_entry_v(spot_df, expiry_token, entry_ts, oi_offset):
    """Same as base.evaluate_entry but the OI-ratio strike is parametric."""
    spot = base.get_value_at(spot_df, entry_ts, "close")
    if spot is None or spot <= 0:
        return None

    atm = int(round(spot / base.STRIKE_STEP) * base.STRIKE_STEP)
    oi_strike = atm + oi_offset

    ce = base.load_option(oi_strike, "CE", expiry_token)
    pe = base.load_option(oi_strike, "PE", expiry_token)
    ce_oi = base.get_value_at(ce, entry_ts, "open_interest")
    pe_oi = base.get_value_at(pe, entry_ts, "open_interest")
    if ce_oi is None or pe_oi is None or ce_oi <= 0 or pe_oi <= 0:
        return None

    if pe_oi >= base.OI_RATIO_THRESHOLD * ce_oi:
        direction, ratio = "CE", pe_oi / ce_oi
    elif ce_oi >= base.OI_RATIO_THRESHOLD * pe_oi:
        direction, ratio = "PE", ce_oi / pe_oi
    else:
        return None

    atm_opt = base.load_option(atm, direction, expiry_token)
    entry_premium = base.get_value_at(atm_opt, entry_ts, "close")
    if entry_premium is None or not (base.MIN_PREMIUM <= entry_premium <= base.MAX_PREMIUM):
        return None
    lots = int(base.CAPITAL_PER_TRADE // (entry_premium * base.LOT_SIZE))
    if lots < 1:
        return None

    return {
        "atm": atm, "spot": spot, "direction": direction,
        "entry_premium": entry_premium, "lots": lots, "qty": lots * base.LOT_SIZE,
        "ce_oi_above": int(ce_oi), "pe_oi_above": int(pe_oi),
        "oi_ratio": round(ratio, 2), "atm_opt_df": atm_opt,
    }


def simulate_exit_v(spot_df, atm_opt_df, entry_ts, atm, direction, stop_buffer):
    """Adverse-spot stop fires only when spot has moved `stop_buffer`
    points past the ATM strike (CE: spot <= atm - buffer, PE: spot >=
    atm + buffer)."""
    expiry_date = entry_ts.date()
    deadline = pd.Timestamp.combine(expiry_date, base.TIME_EXIT).tz_localize(entry_ts.tz)

    cur = entry_ts + pd.Timedelta(minutes=1)
    while cur <= deadline:
        spot = base.get_value_at(spot_df, cur, "close")
        prem = base.get_value_at(atm_opt_df, cur, "close")
        if spot is not None:
            if direction == "CE" and spot <= (atm - stop_buffer):
                return cur, (prem if prem is not None else 0.0), spot, "ADVERSE_SPOT"
            if direction == "PE" and spot >= (atm + stop_buffer):
                return cur, (prem if prem is not None else 0.0), spot, "ADVERSE_SPOT"
        cur += pd.Timedelta(minutes=1)

    spot = base.get_value_at(spot_df, deadline, "close") or 0.0
    prem = base.get_value_at(atm_opt_df, deadline, "close")
    if prem is None:
        sub = atm_opt_df[atm_opt_df.index <= deadline]
        prem = float(sub["close"].iloc[-1]) if len(sub) else 0.0
    return deadline, prem, spot, "TIME_EXIT"


def run_one_expiry_v(expiry_token, expiry_date, spot_df, oi_offset, stop_buffer):
    tz = spot_df.index.tz
    for entry_t in base.ENTRY_TIMES:
        entry_ts = pd.Timestamp.combine(expiry_date.date(), entry_t).tz_localize(tz)
        sig = evaluate_entry_v(spot_df, expiry_token, entry_ts, oi_offset)
        if sig is None:
            continue

        exit_ts, exit_prem, exit_spot, reason = simulate_exit_v(
            spot_df, sig["atm_opt_df"], entry_ts,
            sig["atm"], sig["direction"], stop_buffer,
        )
        eff_entry = sig["entry_premium"] + base.SLIPPAGE_PER_LEG
        eff_exit = max(0.0, exit_prem - base.SLIPPAGE_PER_LEG)
        gross = (eff_exit - eff_entry) * sig["qty"]
        net = gross - base.BROKERAGE_PER_TRADE
        ret_pct = (eff_exit - eff_entry) / eff_entry * 100.0
        held = int((exit_ts - entry_ts).total_seconds() // 60)

        return base.Trade(
            expiry=expiry_token, entry_ts=entry_ts.isoformat(),
            direction=sig["direction"], atm_strike=sig["atm"],
            entry_spot=round(sig["spot"], 2),
            entry_premium=round(sig["entry_premium"], 2),
            qty=sig["qty"], lots=sig["lots"],
            exit_ts=exit_ts.isoformat(), exit_premium=round(exit_prem, 2),
            exit_spot=round(exit_spot, 2), exit_reason=reason,
            gross_pnl=round(gross, 2), net_pnl=round(net, 2),
            return_pct=round(ret_pct, 2), minutes_held=held,
            ce_oi_above=sig["ce_oi_above"], pe_oi_above=sig["pe_oi_above"],
            oi_ratio=sig["oi_ratio"],
        )
    return None


def stats_for_trades(trades, label):
    n = len(trades)
    if n == 0:
        return {"name": label, "n_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    cum = df["net_pnl"].cumsum()
    dd = (cum.cummax() - cum).max()
    n_ce = (df["direction"] == "CE").sum()
    n_pe = (df["direction"] == "PE").sum()
    n_time = (df["exit_reason"] == "TIME_EXIT").sum()
    return {
        "name": label, "n_trades": n,
        "wins": int((df["net_pnl"] > 0).sum()),
        "win_rate_%": round((df["net_pnl"] > 0).mean() * 100, 1),
        "total_pnl": int(df["net_pnl"].sum()),
        "avg_pnl": int(df["net_pnl"].mean()),
        "median_ret_%": round(df["return_pct"].median(), 1),
        "best": int(df["net_pnl"].max()),
        "worst": int(df["net_pnl"].min()),
        "max_dd": int(dd),
        "ce_pe": f"{n_ce}/{n_pe}",
        "time_exits": int(n_time),
        "avg_minutes": int(df["minutes_held"].mean()),
    }


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = base.discover_expiries()
    print(f"Loaded {len(expiries)} expiries.\n")

    all_stats = []
    for v in VARIANTS:
        print(f"=== {v['name']}  (oi_offset={v['oi_offset']}, stop_buffer={v['stop_buffer']}) ===")
        trades = []
        for tok, dt in expiries:
            try:
                t = run_one_expiry_v(tok, dt, spot_df, v["oi_offset"], v["stop_buffer"])
                if t is not None:
                    trades.append(t)
            except Exception as e:
                print(f"  {tok}: ERR {e}")
        s = stats_for_trades(trades, v["name"])
        all_stats.append(s)
        print(f"  -> {s['n_trades']} trades, "
              f"win {s.get('win_rate_%', 0)}%, "
              f"net ₹{s.get('total_pnl', 0):,}, "
              f"DD ₹{s.get('max_dd', 0):,}, "
              f"CE/PE {s.get('ce_pe', '0/0')}\n")

        if trades:
            csv = base.REPORTS_DIR / f"expiry_gamma_hero_{v['name']}_trades.csv"
            pd.DataFrame([asdict(t) for t in trades]).to_csv(csv, index=False)

    # Comparison report
    out = base.REPORTS_DIR / "expiry_gamma_hero_variants.md"
    cols = ["name", "n_trades", "wins", "win_rate_%", "total_pnl", "avg_pnl",
            "median_ret_%", "best", "worst", "max_dd", "ce_pe",
            "time_exits", "avg_minutes"]
    df = pd.DataFrame(all_stats)[cols]
    lines = ["# Expiry Gamma Hero — Variant Comparison\n",
             "Held constant: capital ₹20k, lot 65, premium ₹5–20, time-exit 15:25,",
             "₹0.05/leg slippage, ₹60 round-trip brokerage.\n",
             "## Variants\n",
             "- **V0 baseline**: OI ratio at ATM+50, exit when spot crosses ATM",
             "- **V1 wider OI**: OI ratio at the ATM strike itself, exit when spot crosses ATM",
             "- **V2 wider stop**: OI at ATM+50, exit only after spot moves 30 pts past ATM",
             "- **V3 both**: OI at ATM, exit only after spot moves 30 pts past ATM\n",
             "## Headline\n", "```", df.to_string(index=False), "```", ""]
    out.write_text("\n".join(lines))
    print(f"\nWrote {out}")
    print("\n", df.to_string(index=False))


if __name__ == "__main__":
    main()
