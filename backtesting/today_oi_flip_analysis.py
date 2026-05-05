"""Detect intraday OI-flip moments on today's NIFTY + SENSEX data.

For each focus_zone_*_2026-05-05.csv:

  1. Track ATM CE OI and ATM PE OI minute-by-minute.
  2. Compute 15-min rolling delta for each side.
  3. Identify "bullish flip": PE OI surging while CE OI flat/falling
     (put writers piling in -> spot has support).
  4. Identify "bearish flip": CE OI surging while PE OI flat/falling
     (call writers piling in -> spot has resistance).
  5. Simulate buying the corresponding ATM option at the flip moment
     with Rs.20k capital and holding to end of day.
  6. Compare to "do nothing" (sit out) and "14:50 straddle" baselines.

Output: console table + reports/today_oi_flip_analysis.md
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

CONFIGS = {
    "NIFTY":  {"file": ROOT / "focus_zone_nifty_2026-05-05.csv",
               "lot": 65,  "step": 50,  "capital": 20_000},
    "SENSEX": {"file": ROOT / "focus_zone_sensex_2026-05-05.csv",
               "lot": 20,  "step": 100, "capital": 20_000},
}

LOOKBACK_MIN = 15        # rolling window
SURGE_RATIO  = 2.0       # PE delta must be >= 2x CE delta (or vice versa)
MIN_ABS_OI_DELTA = 5_000_000  # filter noise (5M contracts)


def load_atm_series(csv_path: Path) -> pd.DataFrame:
    """Return one row per minute with ATM CE/PE LTP and OI."""
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    # ATM rows are where pos == 0 (the strike whose pos column is exactly 0)
    atm = df[df["pos"] == 0.0].copy()
    if atm.empty:
        # Some files may use string "ATM" instead of numeric 0
        atm = df[df["pos"].astype(str).str.upper() == "ATM"].copy()
    atm = atm.sort_values("timestamp").drop_duplicates(
        subset="timestamp", keep="last"
    )
    atm = atm.set_index("timestamp")
    return atm[["spot", "strike", "ce_ltp", "ce_oi", "pe_ltp", "pe_oi"]]


def compute_rolling_oi_deltas(atm: pd.DataFrame, window_min: int) -> pd.DataFrame:
    """Add ce_oi_delta_<n>m and pe_oi_delta_<n>m columns via window-shift."""
    # Re-index to 1-min freq so a fixed shift maps to N minutes
    full_range = pd.date_range(atm.index.min(), atm.index.max(), freq="1min")
    atm_1min = atm.reindex(full_range, method="ffill")

    atm_1min[f"ce_oi_delta_{window_min}m"] = (
        atm_1min["ce_oi"] - atm_1min["ce_oi"].shift(window_min)
    )
    atm_1min[f"pe_oi_delta_{window_min}m"] = (
        atm_1min["pe_oi"] - atm_1min["pe_oi"].shift(window_min)
    )
    atm_1min["pe_minus_ce_oi_delta"] = (
        atm_1min[f"pe_oi_delta_{window_min}m"]
        - atm_1min[f"ce_oi_delta_{window_min}m"]
    )
    return atm_1min


def detect_first_flip(atm: pd.DataFrame, direction: str) -> Optional[dict]:
    """First moment where the OI delta divergence exceeds the surge threshold.
    direction='bullish' -> PE side surging; 'bearish' -> CE side surging."""
    pe_col = f"pe_oi_delta_{LOOKBACK_MIN}m"
    ce_col = f"ce_oi_delta_{LOOKBACK_MIN}m"
    for ts, row in atm.iterrows():
        pe_delta = row.get(pe_col)
        ce_delta = row.get(ce_col)
        if pd.isna(pe_delta) or pd.isna(ce_delta):
            continue
        if direction == "bullish":
            if (pe_delta >= MIN_ABS_OI_DELTA
                    and (ce_delta <= 0 or pe_delta >= SURGE_RATIO * abs(ce_delta))):
                return {"ts": ts, "pe_delta": int(pe_delta),
                        "ce_delta": int(ce_delta), "row": row}
        else:
            if (ce_delta >= MIN_ABS_OI_DELTA
                    and (pe_delta <= 0 or ce_delta >= SURGE_RATIO * abs(pe_delta))):
                return {"ts": ts, "pe_delta": int(pe_delta),
                        "ce_delta": int(ce_delta), "row": row}
    return None


def simulate_buy(atm: pd.DataFrame, side: str, entry_ts, capital, lot, exit_ts=None):
    """Buy ATM CE or PE at entry_ts, hold to exit_ts (or last bar)."""
    # Find nearest available timestamp if exact match missing
    if entry_ts not in atm.index:
        diffs = atm.index.to_series().sub(entry_ts).abs()
        if diffs.empty:
            return None
        entry_ts = diffs.idxmin()
    entry_row = atm.loc[entry_ts]
    entry_premium = float(entry_row[f"{side.lower()}_ltp"])
    if entry_premium <= 0:
        return None
    lots = int(capital // (entry_premium * lot))
    if lots < 1:
        return None
    qty = lots * lot

    if exit_ts is None:
        exit_ts = atm.index[-1]
    elif exit_ts not in atm.index:
        diffs = atm.index.to_series().sub(exit_ts).abs()
        exit_ts = diffs.idxmin()
    exit_row = atm.loc[exit_ts]
    exit_premium = float(exit_row[f"{side.lower()}_ltp"])

    pnl = (exit_premium - entry_premium) * qty - 60.0  # ~Rs.60 brokerage
    return {
        "side": side, "entry_ts": entry_ts, "exit_ts": exit_ts,
        "entry_premium": round(entry_premium, 2),
        "exit_premium": round(exit_premium, 2),
        "lots": lots, "qty": qty,
        "pnl": int(pnl), "ret_pct": round((exit_premium / entry_premium - 1) * 100, 1),
    }


def simulate_straddle(atm: pd.DataFrame, entry_ts, capital, lot,
                      exit_ts=None) -> Optional[dict]:
    if entry_ts not in atm.index:
        return None
    entry_row = atm.loc[entry_ts]
    ce = float(entry_row["ce_ltp"])
    pe = float(entry_row["pe_ltp"])
    combined = ce + pe
    if combined <= 0:
        return None
    lots = int(capital // (combined * lot))
    if lots < 1:
        return None
    qty = lots * lot
    if exit_ts is None:
        exit_ts = atm.index[-1]
    exit_row = atm.loc[exit_ts]
    ce_x = float(exit_row["ce_ltp"])
    pe_x = float(exit_row["pe_ltp"])
    pnl = (ce_x + pe_x - ce - pe) * qty - 120.0
    return {
        "ce_in": round(ce, 2), "pe_in": round(pe, 2),
        "ce_out": round(ce_x, 2), "pe_out": round(pe_x, 2),
        "lots": lots, "qty": qty, "pnl": int(pnl),
    }


def find_nearest_ts(atm, target_hh, target_mm) -> Optional[pd.Timestamp]:
    """Find the index timestamp whose time-of-day is closest to target."""
    target = pd.Timedelta(hours=target_hh, minutes=target_mm)
    diffs = (atm.index - atm.index.normalize() - target).map(
        lambda d: abs(d.total_seconds())
    )
    if len(diffs) == 0:
        return None
    return atm.index[diffs.argmin()]


def best_single_leg(atm, side, capital, lot, min_entry_premium=2.0) -> dict:
    """Best entry timestamp for buying CE or PE (perfect-foresight upper bound).
    Filters out absurdly-cheap entry premiums where no real liquidity exists,
    and excludes the last 5 minutes (no time to win)."""
    col = f"{side.lower()}_ltp"
    eod_ts = atm.index[-1]
    cutoff = eod_ts - pd.Timedelta(minutes=5)
    valid = atm[(atm[col] >= min_entry_premium) & (atm.index <= cutoff)].copy()
    if valid.empty:
        return {"side": side, "pnl": 0}
    eod = float(atm[col].iloc[-1])
    valid["potential_pnl"] = valid[col].apply(
        lambda p: (eod - p) * (capital // (p * lot)) * lot - 60.0
    )
    best = valid.sort_values("potential_pnl", ascending=False).iloc[0]
    entry_p = float(best[col])
    lots = int(capital // (entry_p * lot))
    qty = lots * lot
    return {"side": side, "entry_ts": best.name, "entry_premium": round(entry_p, 2),
            "exit_premium": round(eod, 2), "lots": lots, "qty": qty,
            "pnl": int((eod - entry_p) * qty - 60.0),
            "ret_pct": round((eod / entry_p - 1) * 100, 1)}


def analyse(name: str, cfg: dict, lines: list[str]) -> None:
    print(f"\n{'='*70}\n{name}  (lot={cfg['lot']}, step={cfg['step']}, "
          f"capital=Rs.{cfg['capital']:,})\n{'='*70}")
    atm = load_atm_series(cfg["file"])
    if atm.empty:
        print("  no ATM rows")
        return
    atm_1m = compute_rolling_oi_deltas(atm, LOOKBACK_MIN)

    spot_open = float(atm["spot"].iloc[0])
    spot_close = float(atm["spot"].iloc[-1])
    spot_low = float(atm["spot"].min())
    spot_high = float(atm["spot"].max())
    print(f"\nDay's spot: open={spot_open:.0f}  high={spot_high:.0f}  "
          f"low={spot_low:.0f}  close={spot_close:.0f}  "
          f"net_move={spot_close - spot_open:+.0f}")
    lines.append(f"## {name}\n")
    lines.append(f"- Spot: open {spot_open:.0f} | high {spot_high:.0f} | "
                 f"low {spot_low:.0f} | close {spot_close:.0f} | "
                 f"net {spot_close - spot_open:+.0f}\n")

    # 1. Detect first bullish flip
    bull = detect_first_flip(atm_1m, "bullish")
    bear = detect_first_flip(atm_1m, "bearish")
    print("\n--- Signals ---")
    if bull:
        ts = bull["ts"]
        print(f"BULLISH flip first triggered at {ts.strftime('%H:%M')} "
              f"(PE OI delta_15m={bull['pe_delta']:>+,}, "
              f"CE OI delta_15m={bull['ce_delta']:>+,}) "
              f"-> would buy ATM CE")
    else:
        print("BULLISH flip: no signal today")
    if bear:
        ts = bear["ts"]
        print(f"BEARISH flip first triggered at {ts.strftime('%H:%M')} "
              f"(CE OI delta_15m={bear['ce_delta']:>+,}, "
              f"PE OI delta_15m={bear['pe_delta']:>+,}) "
              f"-> would buy ATM PE")
    else:
        print("BEARISH flip: no signal today")

    # 2. Simulate trades
    rows = []

    if bull:
        t = simulate_buy(atm, "CE", bull["ts"], cfg["capital"], cfg["lot"])
        if t:
            rows.append(("OI-flip BULL (buy ATM CE)", t["entry_ts"],
                         f"CE Rs.{t['entry_premium']} -> Rs.{t['exit_premium']}",
                         t["pnl"], t["ret_pct"]))
    if bear:
        t = simulate_buy(atm, "PE", bear["ts"], cfg["capital"], cfg["lot"])
        if t:
            rows.append(("OI-flip BEAR (buy ATM PE)", t["entry_ts"],
                         f"PE Rs.{t['entry_premium']} -> Rs.{t['exit_premium']}",
                         t["pnl"], t["ret_pct"]))

    # 3. 14:50 straddle baseline (entry = 13:20 in CSV time = 14:50 IST)
    ts_1450 = find_nearest_ts(atm, 13, 20)  # CSV-time 13:20 ~= 14:50 IST
    ts_1525 = find_nearest_ts(atm, 13, 55)  # CSV-time 13:55 ~= 15:25 IST
    if ts_1450 and ts_1525:
        s = simulate_straddle(atm, ts_1450, cfg["capital"], cfg["lot"], ts_1525)
        if s:
            rows.append(("14:50 straddle (baseline)", ts_1450,
                         f"CE+PE Rs.{s['ce_in']+s['pe_in']:.2f} -> "
                         f"Rs.{s['ce_out']+s['pe_out']:.2f}",
                         s["pnl"], "-"))

    # 4. Perfect-foresight single-leg upper bound
    best_ce = best_single_leg(atm, "CE", cfg["capital"], cfg["lot"])
    best_pe = best_single_leg(atm, "PE", cfg["capital"], cfg["lot"])
    rows.append((f"Perfect CE entry @ {best_ce.get('entry_ts').strftime('%H:%M') if best_ce.get('entry_ts') is not None else '?'}",
                 best_ce.get("entry_ts"),
                 f"CE Rs.{best_ce.get('entry_premium','?')} -> Rs.{best_ce.get('exit_premium','?')}",
                 best_ce["pnl"], best_ce.get("ret_pct", "-")))
    rows.append((f"Perfect PE entry @ {best_pe.get('entry_ts').strftime('%H:%M') if best_pe.get('entry_ts') is not None else '?'}",
                 best_pe.get("entry_ts"),
                 f"PE Rs.{best_pe.get('entry_premium','?')} -> Rs.{best_pe.get('exit_premium','?')}",
                 best_pe["pnl"], best_pe.get("ret_pct", "-")))

    # Print and save
    print(f"\n--- Outcomes ---")
    print(f"{'Strategy':<35} {'Entry':<10} {'Trade':<35} {'P&L':>10} {'Ret%':>8}")
    print("-" * 105)
    for label, ets, trade, pnl, ret in rows:
        ets_s = ets.strftime("%H:%M") if isinstance(ets, pd.Timestamp) else str(ets)
        print(f"{label:<35} {ets_s:<10} {trade:<35} Rs.{pnl:>7,} {str(ret):>8}")

    lines.append(f"\n| Strategy | Entry | Trade | P&L | Ret% |")
    lines.append("|---|---|---|---:|---:|")
    for label, ets, trade, pnl, ret in rows:
        ets_s = ets.strftime("%H:%M") if isinstance(ets, pd.Timestamp) else str(ets)
        lines.append(f"| {label} | {ets_s} | {trade} | Rs.{pnl:,} | {ret} |")
    lines.append("")


def main():
    REPORTS.mkdir(exist_ok=True)
    lines = ["# OI-Flip Directional Strategy — Today (2026-05-05)\n",
             f"Lookback: {LOOKBACK_MIN} min | Surge ratio: {SURGE_RATIO}x | "
             f"Min OI delta: {MIN_ABS_OI_DELTA:,} contracts\n"]
    for name, cfg in CONFIGS.items():
        analyse(name, cfg, lines)
    out = REPORTS / "today_oi_flip_analysis.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
