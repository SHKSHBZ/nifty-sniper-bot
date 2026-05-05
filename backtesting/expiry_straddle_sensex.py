"""SENSEX expiry-day long straddle backtest.

Mirrors backtesting/expiry_straddle.py but for SENSEX:
  - 100-pt strike step (vs 50 for Nifty)
  - 20 lot size (vs 65 for Nifty)
  - Reads SENSEX_<strike>_<CE|PE>_<token>_1min.csv files
  - Spot from data/SENSEX_INDEX_1minute.csv

Premise: at 14:50 IST on weekly expiry, buy 1×ATM CE + 1×ATM PE
(equal-lot straddle). Exit at 15:25 with optional SL/TP bumpers.

Run all 4 exit variants in one pass:
  S0 hold        — straddle to 15:25, no SL/TP
  S1 TP only     — close both legs at +50% combined return
  S2 SL only     — close both legs at -50% combined return
  S3 TP + SL     — both bumpers (+50% / -50%)

Output: reports/expiry_straddle_sensex_<variant>_trades.csv
        reports/expiry_straddle_sensex_summary.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------- SENSEX-specific config ----------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
SPOT_CSV = DATA_DIR / "SENSEX_INDEX_1minute.csv"

OPT_NAME_RE = re.compile(
    r"^SENSEX_(\d+)_(CE|PE)_(\d{1,2}_[A-Z]{3}_\d{2})_1min\.csv$"
)
MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# ---------- Strategy parameters ----------
CAPITAL_PER_TRADE = 20_000.0
LOT_SIZE = 20
STRIKE_STEP = 100
MIN_PREMIUM = 5.0
MAX_PREMIUM = 20.0
ENTRY_TIMES = [dtime(14, 50), dtime(15, 0)]
TIME_EXIT = dtime(15, 25)
SLIPPAGE_PER_LEG = 0.05
BROKERAGE_PER_TRADE = 120.0  # 2 legs round-trip, paper rate

VARIANTS = [
    {"name": "S0_hold",  "tp_pct": None, "sl_pct": None},
    {"name": "S1_tp50",  "tp_pct": 50.0, "sl_pct": None},
    {"name": "S2_sl50",  "tp_pct": None, "sl_pct": 50.0},
    {"name": "S3_tp_sl", "tp_pct": 50.0, "sl_pct": 50.0},
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


def parse_expiry_token(token: str) -> datetime:
    d, m, y = token.split("_")
    return datetime(2000 + int(y), MONTHS[m], int(d))


def discover_expiries() -> list[tuple[str, datetime]]:
    seen: set[str] = set()
    for p in DATA_DIR.iterdir():
        m = OPT_NAME_RE.match(p.name)
        if m:
            seen.add(m.group(3))
    return sorted(((tok, parse_expiry_token(tok)) for tok in seen),
                  key=lambda x: x[1])


def load_option(strike: int, opt_type: str, expiry_token: str) -> Optional[pd.DataFrame]:
    path = DATA_DIR / f"SENSEX_{strike}_{opt_type}_{expiry_token}_1min.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["timestamp"])
    return df.set_index("ts")


def get_value_at(df: pd.DataFrame, ts: pd.Timestamp, col: str) -> Optional[float]:
    if df is None:
        return None
    try:
        row = df.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        v = row[col]
        return None if pd.isna(v) else float(v)
    except KeyError:
        return None


def evaluate_entry(spot_df, expiry_token, entry_ts):
    spot = get_value_at(spot_df, entry_ts, "close")
    if spot is None or spot <= 0:
        return None
    atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
    ce = load_option(atm, "CE", expiry_token)
    pe = load_option(atm, "PE", expiry_token)
    ce_p = get_value_at(ce, entry_ts, "close")
    pe_p = get_value_at(pe, entry_ts, "close")
    if ce_p is None or pe_p is None:
        return None
    if not (MIN_PREMIUM <= ce_p <= MAX_PREMIUM):
        return None
    if not (MIN_PREMIUM <= pe_p <= MAX_PREMIUM):
        return None
    combined = ce_p + pe_p
    lots = int(CAPITAL_PER_TRADE // (combined * LOT_SIZE))
    if lots < 1:
        return None
    return {"atm": atm, "spot": spot, "ce_entry": ce_p, "pe_entry": pe_p,
            "combined": combined, "lots": lots, "qty": lots * LOT_SIZE,
            "ce_df": ce, "pe_df": pe}


def simulate_exit(spot_df, ce_df, pe_df, entry_ts, combined_entry, tp, sl):
    expiry_date = entry_ts.date()
    deadline = pd.Timestamp.combine(expiry_date, TIME_EXIT).tz_localize(entry_ts.tz)

    cur = entry_ts + pd.Timedelta(minutes=1)
    while cur <= deadline:
        ce_p = get_value_at(ce_df, cur, "close")
        pe_p = get_value_at(pe_df, cur, "close")
        if ce_p is not None and pe_p is not None:
            ret = (ce_p + pe_p - combined_entry) / combined_entry * 100.0
            if tp is not None and ret >= tp:
                return cur, ce_p, pe_p, "TP"
            if sl is not None and ret <= -sl:
                return cur, ce_p, pe_p, "SL"
        cur += pd.Timedelta(minutes=1)

    ce_p = get_value_at(ce_df, deadline, "close")
    pe_p = get_value_at(pe_df, deadline, "close")
    if ce_p is None:
        sub = ce_df[ce_df.index <= deadline]
        ce_p = float(sub["close"].iloc[-1]) if len(sub) else 0.0
    if pe_p is None:
        sub = pe_df[pe_df.index <= deadline]
        pe_p = float(sub["close"].iloc[-1]) if len(sub) else 0.0
    return deadline, ce_p, pe_p, "TIME_EXIT"


def run_one_expiry(expiry_token, expiry_date, spot_df, tp, sl):
    tz = spot_df.index.tz
    for entry_t in ENTRY_TIMES:
        entry_ts = pd.Timestamp.combine(expiry_date.date(), entry_t).tz_localize(tz)
        sig = evaluate_entry(spot_df, expiry_token, entry_ts)
        if sig is None:
            continue
        exit_ts, ce_x, pe_x, reason = simulate_exit(
            spot_df, sig["ce_df"], sig["pe_df"], entry_ts,
            sig["combined"], tp, sl,
        )
        eff_in = sig["combined"] + 2 * SLIPPAGE_PER_LEG
        eff_out = max(0.0, ce_x + pe_x - 2 * SLIPPAGE_PER_LEG)
        gross = (eff_out - eff_in) * sig["qty"]
        net = gross - BROKERAGE_PER_TRADE
        ret_pct = (eff_out - eff_in) / eff_in * 100.0
        held = int((exit_ts - entry_ts).total_seconds() // 60)
        exit_spot = get_value_at(spot_df, exit_ts, "close") or 0.0
        return StraddleTrade(
            expiry=expiry_token, entry_ts=entry_ts.isoformat(),
            atm_strike=sig["atm"], entry_spot=round(sig["spot"], 2),
            ce_entry=round(sig["ce_entry"], 2), pe_entry=round(sig["pe_entry"], 2),
            combined_entry=round(sig["combined"], 2),
            lots=sig["lots"], qty=sig["qty"],
            exit_ts=exit_ts.isoformat(),
            ce_exit=round(ce_x, 2), pe_exit=round(pe_x, 2),
            exit_spot=round(exit_spot, 2), exit_reason=reason,
            gross_pnl=round(gross, 2), net_pnl=round(net, 2),
            return_pct=round(ret_pct, 2), minutes_held=held,
        )
    return None


def stats(trades, label):
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
    REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = discover_expiries()
    print(f"Loaded {len(expiries)} SENSEX expiries with data.\n")

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
        s = stats(trades, v["name"])
        all_stats.append(s)
        print(f"  -> {s['n_trades']} trades, "
              f"win {s.get('win_rate_%', 0)}%, "
              f"net ₹{s.get('total_pnl', 0):,}, "
              f"DD ₹{s.get('max_dd', 0):,}, "
              f"TP/SL/TIME {s.get('exit_TP', 0)}/{s.get('exit_SL', 0)}/{s.get('exit_TIME', 0)}\n")
        if trades:
            csv = REPORTS_DIR / f"expiry_straddle_sensex_{v['name']}_trades.csv"
            pd.DataFrame([asdict(t) for t in trades]).to_csv(csv, index=False)

    cols = ["name", "n_trades", "wins", "win_rate_%", "total_pnl", "avg_pnl",
            "median_ret_%", "best", "worst", "max_dd",
            "exit_TP", "exit_SL", "exit_TIME", "avg_minutes"]
    df = pd.DataFrame(all_stats)[cols]
    out = REPORTS_DIR / "expiry_straddle_sensex_summary.md"
    lines = ["# SENSEX Long Straddle — Variant Comparison\n",
             f"Capital ₹{int(CAPITAL_PER_TRADE):,}/trade. Lot {LOT_SIZE}. Strike step {STRIKE_STEP}.",
             f"Premium gate ₹{int(MIN_PREMIUM)}–₹{int(MAX_PREMIUM)} per leg. "
             f"Entry 14:50/15:00. Time exit 15:25.",
             f"Slippage ₹{SLIPPAGE_PER_LEG}/leg. Brokerage ₹{int(BROKERAGE_PER_TRADE)} round-trip.\n",
             "## Variants\n",
             "- **S0 hold**: no SL/TP",
             "- **S1 TP+50%**: close both legs at +50% combined return",
             "- **S2 SL−50%**: close both legs at −50% combined return",
             "- **S3 TP+SL**: both bumpers\n",
             "## Headline\n", "```", df.to_string(index=False), "```", ""]
    out.write_text("\n".join(lines))
    print(f"\nWrote {out}")
    print("\n", df.to_string(index=False))


if __name__ == "__main__":
    main()
