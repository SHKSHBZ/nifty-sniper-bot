"""Build SENSEX per-expiry option-chain views.

Each CSV under reports/sensex_expiry_detail/<token>.csv has one row
per minute from 14:30 to 15:30 IST and columns showing the spot,
ATM strike, plus CE/PE close + OI for ATM-200, ATM-100, ATM,
ATM+100, ATM+200 (5 strikes per expiry, 100-pt step).

Use this to manually inspect what was happening on each expiry day:
spot drift, premium decay, OI shifts -- all in one chain-shaped view.
"""
from __future__ import annotations

import sys
from datetime import time as dtime
from pathlib import Path

import pandas as pd

# Allow running from repo root OR from inside backtesting/
_HERE = Path(__file__).resolve().parent
if _HERE.name == "backtesting":
    sys.path.insert(0, str(_HERE.parent))

import backtesting.expiry_straddle_sensex as base


WINDOW_START = dtime(14, 30)
WINDOW_END = dtime(15, 30)
ATM_FREEZE_TIME = dtime(14, 50)
STRIKE_STEP = base.STRIKE_STEP  # 100 for SENSEX
STRIKES_EITHER_SIDE = 2  # ATM-200, ATM-100, ATM, ATM+100, ATM+200


def dump_one(expiry_token: str, expiry_date, spot_df: pd.DataFrame, out_dir: Path) -> dict:
    tz = spot_df.index.tz
    freeze_ts = pd.Timestamp.combine(expiry_date.date(), ATM_FREEZE_TIME).tz_localize(tz)
    spot_at_freeze = base.get_value_at(spot_df, freeze_ts, "close")
    if spot_at_freeze is None:
        return {"expiry": expiry_token, "status": "no_spot_at_freeze"}

    atm = int(round(spot_at_freeze / STRIKE_STEP) * STRIKE_STEP)
    strikes = [atm + i * STRIKE_STEP
               for i in range(-STRIKES_EITHER_SIDE, STRIKES_EITHER_SIDE + 1)]

    legs: dict[tuple[int, str], pd.DataFrame] = {}
    for s in strikes:
        for opt in ("CE", "PE"):
            df = base.load_option(s, opt, expiry_token)
            if df is not None:
                legs[(s, opt)] = df

    start_ts = pd.Timestamp.combine(expiry_date.date(), WINDOW_START).tz_localize(tz)
    end_ts = pd.Timestamp.combine(expiry_date.date(), WINDOW_END).tz_localize(tz)
    minutes = pd.date_range(start_ts, end_ts, freq="1min")

    baselines = {}
    for (s, opt), df in legs.items():
        baselines[(s, opt)] = base.get_value_at(df, start_ts, "open_interest") or 0

    rows = []
    for ts in minutes:
        spot = base.get_value_at(spot_df, ts, "close")
        row = {"ts": ts.strftime("%H:%M"), "spot": spot, "atm_frozen_at_1450": atm}
        for s in strikes:
            offset = (s - atm) // STRIKE_STEP
            label = f"ATM" if offset == 0 else (
                f"ATM{offset:+d}"  # e.g. ATM-1, ATM+2
            )
            for opt in ("CE", "PE"):
                df = legs.get((s, opt))
                close = base.get_value_at(df, ts, "close") if df is not None else None
                oi = base.get_value_at(df, ts, "open_interest") if df is not None else None
                base_oi = baselines.get((s, opt), 0) or 0
                oi_chg = (oi - base_oi) if oi is not None else None
                row[f"{label}_K{s}_{opt}_close"] = round(close, 2) if close is not None else ""
                row[f"{label}_K{s}_{opt}_OI"] = int(oi) if oi is not None else ""
                row[f"{label}_K{s}_{opt}_OI_chg_vs_1430"] = int(oi_chg) if oi_chg is not None else ""
        rows.append(row)

    out_path = out_dir / f"{expiry_token}.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)

    end_spot = base.get_value_at(spot_df, end_ts, "close") or 0
    spot_move = (end_spot - spot_at_freeze) if (end_spot and spot_at_freeze) else 0
    atm_ce_open = base.get_value_at(legs.get((atm, "CE")), freeze_ts, "close")
    atm_pe_open = base.get_value_at(legs.get((atm, "PE")), freeze_ts, "close")
    atm_ce_end = base.get_value_at(legs.get((atm, "CE")), end_ts, "close")
    atm_pe_end = base.get_value_at(legs.get((atm, "PE")), end_ts, "close")

    return {
        "expiry": expiry_token,
        "status": "ok",
        "atm": atm,
        "spot_at_1450": round(spot_at_freeze, 2) if spot_at_freeze else "",
        "spot_at_1530": round(end_spot, 2) if end_spot else "",
        "spot_move": round(spot_move, 2),
        "ce_atm_at_1450": round(atm_ce_open, 2) if atm_ce_open else "",
        "ce_atm_at_1530": round(atm_ce_end, 2) if atm_ce_end else "",
        "pe_atm_at_1450": round(atm_pe_open, 2) if atm_pe_open else "",
        "pe_atm_at_1530": round(atm_pe_end, 2) if atm_pe_end else "",
        "ce_change_pct": round((atm_ce_end / atm_ce_open - 1) * 100, 1)
            if (atm_ce_open and atm_ce_end and atm_ce_open > 0) else "",
        "pe_change_pct": round((atm_pe_end / atm_pe_open - 1) * 100, 1)
            if (atm_pe_open and atm_pe_end and atm_pe_open > 0) else "",
    }


def main():
    out_dir = base.REPORTS_DIR / "sensex_expiry_detail"
    out_dir.mkdir(parents=True, exist_ok=True)

    spot_df = pd.read_csv(base.SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")
    expiries = base.discover_expiries()
    print(f"Building chain views for {len(expiries)} SENSEX expiries -> {out_dir}/\n")

    summaries = []
    for tok, dt in expiries:
        try:
            s = dump_one(tok, dt, spot_df, out_dir)
            summaries.append(s)
            print(f"  {tok}: {s.get('status')} "
                  f"(spot {s.get('spot_at_1450','')} -> {s.get('spot_at_1530','')}, "
                  f"CE {s.get('ce_change_pct','')}%, PE {s.get('pe_change_pct','')}%)")
        except Exception as e:
            print(f"  {tok}: ERR {e}")
            summaries.append({"expiry": tok, "status": f"err: {e}"})

    idx = out_dir / "_index.csv"
    pd.DataFrame(summaries).to_csv(idx, index=False)
    print(f"\nIndex (one row per expiry): {idx}")


if __name__ == "__main__":
    main()
