"""Direction-signal screener for expiry day at 14:50.

For each of the 38 expiries we have data for, compute several
candidate "is the winning leg CE or PE?" signals at 14:50, then
compare to the actual winning leg (from perfect-picker analysis).

The winning leg is defined as the one with the larger 14:50→15:25
P&L (i.e. the leg whose premium expanded more).

Signals tested (each maps to a CE/PE prediction):
  S1  spot_momentum_30m : spot[14:50] - spot[14:20]    >0 → CE
  S2  spot_momentum_60m : spot[14:50] - spot[13:50]    >0 → CE
  S3  spot_vs_dayopen   : spot[14:50] - spot[09:15]    >0 → CE
  S4  spot_vs_vwap      : spot[14:50] - vwap[14:50]    >0 → CE
  S5  oi_pcr_change_60m : (ΔPE_OI − ΔCE_OI at ATM)     >0 → CE
       PE writers piling in late → bullish (writers are sellers, sell PE = bullish bet)
  S6  premium_pcr       : pe_close / ce_close at 14:50 >1 → CE
       PE costlier than CE = market pricing in downside, fade it → CE

Output: reports/direction_signal_screen.md
        reports/direction_signal_screen.csv
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from backtesting import expiry_gamma_hero as base


ENTRY_TIME = dtime(14, 50)
EXIT_TIME = dtime(15, 25)
STRIKE_STEP = 50


@dataclass
class Row:
    expiry: str
    actual_winner: str
    spot_1450: float
    spot_1525: float
    s1_mom30:    str
    s2_mom60:    str
    s3_dayopen:  str
    s4_vwap:     str
    s5_oi_pcr:   str
    s6_prem_pcr: str


def predict(positive_means_ce: bool, value):
    """value > 0 → CE if positive_means_ce else PE; reverse otherwise."""
    if value is None:
        return ""
    if value > 0:
        return "CE" if positive_means_ce else "PE"
    elif value < 0:
        return "PE" if positive_means_ce else "CE"
    return ""


def compute_vwap(spot_df: pd.DataFrame, day_start: pd.Timestamp,
                 up_to: pd.Timestamp) -> float:
    """Cumulative VWAP from day_start to up_to using close × volume."""
    sub = spot_df.loc[day_start:up_to]
    if sub.empty:
        return 0.0
    pv = (sub["close"] * sub["volume"]).sum()
    v = sub["volume"].sum()
    if v <= 0:
        # Spot index has no volume on Nifty — use simple TWAP fallback
        return float(sub["close"].mean())
    return float(pv / v)


def evaluate_one(expiry_token: str, expiry_date, spot_df: pd.DataFrame):
    tz = spot_df.index.tz
    entry_ts = pd.Timestamp.combine(expiry_date.date(), ENTRY_TIME).tz_localize(tz)
    exit_ts = pd.Timestamp.combine(expiry_date.date(), EXIT_TIME).tz_localize(tz)
    open_ts = pd.Timestamp.combine(expiry_date.date(), dtime(9, 15)).tz_localize(tz)
    ts_30m_ago = entry_ts - pd.Timedelta(minutes=30)
    ts_60m_ago = entry_ts - pd.Timedelta(minutes=60)

    spot_e = base.get_value_at(spot_df, entry_ts, "close")
    spot_x = base.get_value_at(spot_df, exit_ts, "close")
    if spot_e is None:
        return None
    atm = int(round(spot_e / STRIKE_STEP) * STRIKE_STEP)

    ce = base.load_option(atm, "CE", expiry_token)
    pe = base.load_option(atm, "PE", expiry_token)
    ce_e = base.get_value_at(ce, entry_ts, "close")
    pe_e = base.get_value_at(pe, entry_ts, "close")
    ce_x = base.get_value_at(ce, exit_ts, "close")
    pe_x = base.get_value_at(pe, exit_ts, "close")
    if any(v is None for v in (ce_e, pe_e, ce_x, pe_x)):
        return None

    ce_ret = ce_x - ce_e
    pe_ret = pe_x - pe_e
    if ce_ret > pe_ret:
        actual = "CE"
    elif pe_ret > ce_ret:
        actual = "PE"
    else:
        actual = "TIE"

    # Signals
    spot_30m = base.get_value_at(spot_df, ts_30m_ago, "close")
    spot_60m = base.get_value_at(spot_df, ts_60m_ago, "close")
    spot_open = base.get_value_at(spot_df, open_ts, "close")
    s1 = predict(True, (spot_e - spot_30m)) if spot_30m else ""
    s2 = predict(True, (spot_e - spot_60m)) if spot_60m else ""
    s3 = predict(True, (spot_e - spot_open)) if spot_open else ""

    vwap = compute_vwap(spot_df, open_ts, entry_ts)
    s4 = predict(True, (spot_e - vwap)) if vwap else ""

    ce_oi_e = base.get_value_at(ce, entry_ts, "open_interest")
    pe_oi_e = base.get_value_at(pe, entry_ts, "open_interest")
    ce_oi_60 = base.get_value_at(ce, ts_60m_ago, "open_interest")
    pe_oi_60 = base.get_value_at(pe, ts_60m_ago, "open_interest")
    if all(v is not None for v in (ce_oi_e, pe_oi_e, ce_oi_60, pe_oi_60)):
        delta_pe = pe_oi_e - pe_oi_60
        delta_ce = ce_oi_e - ce_oi_60
        s5 = predict(True, (delta_pe - delta_ce))
    else:
        s5 = ""

    # Premium PCR: pe_close / ce_close. If > 1, PE is dearer; market
    # implying more downside risk. Fading that says: buy CE.
    s6 = predict(True, (pe_e - ce_e)) if (pe_e and ce_e) else ""

    return Row(
        expiry=expiry_token,
        actual_winner=actual,
        spot_1450=round(spot_e, 2),
        spot_1525=round(spot_x or 0, 2),
        s1_mom30=s1, s2_mom60=s2, s3_dayopen=s3,
        s4_vwap=s4, s5_oi_pcr=s5, s6_prem_pcr=s6,
    )


def accuracy(df: pd.DataFrame, col: str) -> dict:
    """% correct excluding rows where signal is empty or actual is TIE."""
    sub = df[(df[col] != "") & (df["actual_winner"] != "TIE")]
    if sub.empty:
        return {"n": 0, "correct": 0, "accuracy_pct": 0.0,
                "ce_calls": 0, "ce_correct": 0,
                "pe_calls": 0, "pe_correct": 0}
    correct = (sub[col] == sub["actual_winner"]).sum()
    ce_calls = (sub[col] == "CE").sum()
    pe_calls = (sub[col] == "PE").sum()
    ce_correct = ((sub[col] == "CE") & (sub["actual_winner"] == "CE")).sum()
    pe_correct = ((sub[col] == "PE") & (sub["actual_winner"] == "PE")).sum()
    return {
        "n": len(sub),
        "correct": int(correct),
        "accuracy_pct": round(correct / len(sub) * 100, 1),
        "ce_calls": int(ce_calls),
        "ce_correct": int(ce_correct),
        "pe_calls": int(pe_calls),
        "pe_correct": int(pe_correct),
    }


def main():
    base.REPORTS_DIR.mkdir(exist_ok=True)
    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = base.discover_expiries()

    rows: list[Row] = []
    for tok, dt in expiries:
        try:
            r = evaluate_one(tok, dt, spot_df)
            if r is not None:
                rows.append(r)
        except Exception as e:
            print(f"  {tok}: ERR {e}")

    df = pd.DataFrame([asdict(r) for r in rows])
    df.to_csv(base.REPORTS_DIR / "direction_signal_screen.csv", index=False)

    cols = ["s1_mom30", "s2_mom60", "s3_dayopen", "s4_vwap",
            "s5_oi_pcr", "s6_prem_pcr"]
    labels = {
        "s1_mom30":   "Spot momentum last 30 min",
        "s2_mom60":   "Spot momentum last 60 min",
        "s3_dayopen": "Spot vs day-open (intraday trend)",
        "s4_vwap":    "Spot vs VWAP",
        "s5_oi_pcr":  "ΔPE OI − ΔCE OI at ATM (last 60 min)",
        "s6_prem_pcr":"PE − CE premium at 14:50 (fade)",
    }
    md = ["# Direction Signal Screen — Expiry 14:50",
          "",
          f"Sample: {len(df)} expiries with complete data.",
          f"Actual winner distribution: "
          f"{(df['actual_winner']=='CE').sum()} CE / "
          f"{(df['actual_winner']=='PE').sum()} PE / "
          f"{(df['actual_winner']=='TIE').sum()} ties",
          "",
          "## Signal accuracy",
          "",
          "| Signal | n | accuracy | CE calls (right) | PE calls (right) |",
          "|---|---|---|---|---|"]
    for c in cols:
        a = accuracy(df, c)
        md.append(f"| {labels[c]} | {a['n']} | **{a['accuracy_pct']}%** "
                  f"({a['correct']}/{a['n']}) | {a['ce_calls']} ({a['ce_correct']}) "
                  f"| {a['pe_calls']} ({a['pe_correct']}) |")
        print(f"  {labels[c]:<45s} {a['accuracy_pct']:>5.1f}%  "
              f"({a['correct']}/{a['n']}) "
              f"CE={a['ce_correct']}/{a['ce_calls']} "
              f"PE={a['pe_correct']}/{a['pe_calls']}")

    md += ["",
           "## Read",
           "",
           "- A signal is *useful* if accuracy > 55%.",
           "- A signal is *strong* if accuracy > 60%.",
           "- 50% = coin flip — useless.",
           "",
           "## Combined-signal idea",
           "",
           "If two independent signals each have ~58% accuracy, taking only the "
           "trades where both *agree* on direction roughly compounds to ~67% "
           "accuracy on a smaller sample. Identify the top two non-overlapping "
           "signals from the table above and try the AND-rule.",
           ""]
    out = base.REPORTS_DIR / "direction_signal_screen.md"
    out.write_text("\n".join(md))
    print(f"\nWrote {out}")
    print(f"      {base.REPORTS_DIR / 'direction_signal_screen.csv'}")


if __name__ == "__main__":
    main()
