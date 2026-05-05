"""Expiry-day long straddle backtest.

Premise: at 14:50 IST on weekly expiry, buy 1×ATM CE + 1×ATM PE
(equal-lot straddle). Don't try to predict direction — bet that
*something* moves. Square off both legs at 15:25.

Run all 4 exit variants in one pass:
  S0 hold        — straddle to 15:25, no SL/TP
  S1 TP only     — close both legs at +50% combined return
  S2 SL only     — close both legs at -50% combined return
  S3 TP + SL     — both bumpers (+50% / -50%)

Inputs : data/NIFTY_*_{CE,PE}_*_1min.csv, data/NIFTY50_INDEX_1minute.csv
Outputs: reports/expiry_straddle_<variant>_trades.csv
         reports/expiry_straddle_summary.md
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

from backtesting import expiry_gamma_hero as base


CAPITAL_PER_TRADE = 20_000.0
LOT_SIZE = 65
STRIKE_STEP = 50
MIN_PREMIUM = 5.0
MAX_PREMIUM = 20.0
ENTRY_TIMES = [dtime(14, 50), dtime(15, 0)]
TIME_EXIT = dtime(15, 25)
SLIPPAGE_PER_LEG = 0.05
BROKERAGE_PER_TRADE = 120.0  # 2 legs round-trip = 4 orders, paper rate

VARIANTS = [
    {"name": "S0_hold",    "tp_pct": None, "sl_pct": None},
    {"name": "S1_tp50",    "tp_pct": 50.0, "sl_pct": None},
    {"name": "S2_sl50",    "tp_pct": None, "sl_pct": 50.0},
    {"name": "S3_tp_sl",   "tp_pct": 50.0, "sl_pct": 50.0},
]


@dataclass
class StraddleTrade:
    expiry: str
    entry_ts: str
    atm_strike: int
    entry_spot: float
    ce_entry: float
    pe_entry: float
    combined_entry: float
    lots: int
    qty: int
    exit_ts: str
    ce_exit: float
    pe_exit: float
    exit_spot: float
    exit_reason: str
    gross_pnl: float
    net_pnl: float
    return_pct: float
    minutes_held: int


def evaluate_straddle_entry(spot_df, expiry_token, entry_ts):
    spot = base.get_value_at(spot_df, entry_ts, "close")
    if spot is None or spot <= 0:
        return None
    atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)

    ce = base.load_option(atm, "CE", expiry_token)
    pe = base.load_option(atm, "PE", expiry_token)
    ce_prem = base.get_value_at(ce, entry_ts, "close")
    pe_prem = base.get_value_at(pe, entry_ts, "close")
    if ce_prem is None or pe_prem is None:
        return None
    if not (MIN_PREMIUM <= ce_prem <= MAX_PREMIUM):
        return None
    if not (MIN_PREMIUM <= pe_prem <= MAX_PREMIUM):
        return None

    combined = ce_prem + pe_prem
    lots = int(CAPITAL_PER_TRADE // (combined * LOT_SIZE))
    if lots < 1:
        return None
    return {
        "atm": atm, "spot": spot,
        "ce_entry": ce_prem, "pe_entry": pe_prem, "combined": combined,
        "lots": lots, "qty": lots * LOT_SIZE,
        "ce_df": ce, "pe_df": pe,
    }


def simulate_straddle_exit(spot_df, ce_df, pe_df, entry_ts,
                           combined_entry, tp_pct, sl_pct):
    expiry_date = entry_ts.date()
    deadline = pd.Timestamp.combine(expiry_date, TIME_EXIT).tz_localize(entry_ts.tz)

    cur = entry_ts + pd.Timedelta(minutes=1)
    while cur <= deadline:
        ce_p = base.get_value_at(ce_df, cur, "close")
        pe_p = base.get_value_at(pe_df, cur, "close")
        if ce_p is not None and pe_p is not None:
            combined_now = ce_p + pe_p
            ret = (combined_now - combined_entry) / combined_entry * 100.0
            if tp_pct is not None and ret >= tp_pct:
                return cur, ce_p, pe_p, "TP"
            if sl_pct is not None and ret <= -sl_pct:
                return cur, ce_p, pe_p, "SL"
        cur += pd.Timedelta(minutes=1)

    # Time exit
    ce_p = base.get_value_at(ce_df, deadline, "close")
    pe_p = base.get_value_at(pe_df, deadline, "close")
    if ce_p is None:
        sub = ce_df[ce_df.index <= deadline]
        ce_p = float(sub["close"].iloc[-1]) if len(sub) else 0.0
    if pe_p is None:
        sub = pe_df[pe_df.index <= deadline]
        pe_p = float(sub["close"].iloc[-1]) if len(sub) else 0.0
    return deadline, ce_p, pe_p, "TIME_EXIT"


def run_one_expiry(expiry_token, expiry_date, spot_df, tp_pct, sl_pct):
    tz = spot_df.index.tz
    for entry_t in ENTRY_TIMES:
        entry_ts = pd.Timestamp.combine(expiry_date.date(), entry_t).tz_localize(tz)
        sig = evaluate_straddle_entry(spot_df, expiry_token, entry_ts)
        if sig is None:
            continue

        exit_ts, ce_x, pe_x, reason = simulate_straddle_exit(
            spot_df, sig["ce_df"], sig["pe_df"], entry_ts,
            sig["combined"], tp_pct, sl_pct,
        )
        # Slippage: pay up on entry both legs, receive less on exit both legs
        eff_combined_entry = sig["combined"] + 2 * SLIPPAGE_PER_LEG
        eff_combined_exit  = max(0.0, ce_x + pe_x - 2 * SLIPPAGE_PER_LEG)
        gross = (eff_combined_exit - eff_combined_entry) * sig["qty"]
        net = gross - BROKERAGE_PER_TRADE
        ret_pct = (eff_combined_exit - eff_combined_entry) / eff_combined_entry * 100.0
        held = int((exit_ts - entry_ts).total_seconds() // 60)
        exit_spot = base.get_value_at(spot_df, exit_ts, "close") or 0.0

        return StraddleTrade(
            expiry=expiry_token, entry_ts=entry_ts.isoformat(),
            atm_strike=sig["atm"], entry_spot=round(sig["spot"], 2),
            ce_entry=round(sig["ce_entry"], 2),
            pe_entry=round(sig["pe_entry"], 2),
            combined_entry=round(sig["combined"], 2),
            lots=sig["lots"], qty=sig["qty"],
            exit_ts=exit_ts.isoformat(),
            ce_exit=round(ce_x, 2), pe_exit=round(pe_x, 2),
            exit_spot=round(exit_spot, 2),
            exit_reason=reason,
            gross_pnl=round(gross, 2), net_pnl=round(net, 2),
            return_pct=round(ret_pct, 2), minutes_held=held,
        )
    return None


def stats_for_trades(trades, label):
    n = len(trades)
    if n == 0:
        return {"name": label, "n_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    cum = df["net_pnl"].cumsum()
    dd = (cum.cummax() - cum).max()
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
        "exit_TIME": int((df["exit_reason"] == "TIME_EXIT").sum()),
        "exit_TP": int((df["exit_reason"] == "TP").sum()),
        "exit_SL": int((df["exit_reason"] == "SL").sum()),
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
        print(f"=== {v['name']}  (TP={v['tp_pct']}, SL={v['sl_pct']}) ===")
        trades = []
        for tok, dt in expiries:
            try:
                t = run_one_expiry(tok, dt, spot_df, v["tp_pct"], v["sl_pct"])
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
              f"TP/SL/TIME {s.get('exit_TP', 0)}/{s.get('exit_SL', 0)}/{s.get('exit_TIME', 0)}\n")

        if trades:
            csv = base.REPORTS_DIR / f"expiry_straddle_{v['name']}_trades.csv"
            pd.DataFrame([asdict(t) for t in trades]).to_csv(csv, index=False)

    out = base.REPORTS_DIR / "expiry_straddle_summary.md"
    cols = ["name", "n_trades", "wins", "win_rate_%", "total_pnl", "avg_pnl",
            "median_ret_%", "best", "worst", "max_dd",
            "exit_TP", "exit_SL", "exit_TIME", "avg_minutes"]
    df = pd.DataFrame(all_stats)[cols]
    lines = ["# Expiry Long Straddle — Variant Comparison\n",
             f"Capital ₹{int(CAPITAL_PER_TRADE):,}/trade split equally across CE+PE",
             f"(equal lots both legs). Lot {LOT_SIZE}. Premium gate ₹{int(MIN_PREMIUM)}–"
             f"₹{int(MAX_PREMIUM)} per leg. Entry windows 14:50 / 15:00. Time exit 15:25.",
             f"Slippage ₹{SLIPPAGE_PER_LEG}/leg. Brokerage ₹{int(BROKERAGE_PER_TRADE)} round-trip.\n",
             "## Variants\n",
             "- **S0 hold**: no SL/TP, square off at 15:25",
             "- **S1 TP+50%**: close both legs when combined premium up 50%",
             "- **S2 SL−50%**: close both legs when combined premium down 50%",
             "- **S3 TP+SL**: both bumpers active\n",
             "## Headline\n", "```", df.to_string(index=False), "```", ""]
    out.write_text("\n".join(lines))
    print(f"\nWrote {out}")
    print("\n", df.to_string(index=False))


if __name__ == "__main__":
    main()
