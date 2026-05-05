"""Identify all profitable entry windows from today's NIFTY data.

For 2026-05-05 focus_zone_nifty CSV:
  1. Walk through every minute, track ATM CE and ATM PE LTPs.
  2. Find ALL local-minimum entries where buying that leg and exiting
     at the next local maximum within Z minutes would have been profitable.
  3. Report each opportunity: entry IST, side, entry premium, best exit
     within 30/60/120 min, P&L on Rs.20k capital, hold time.
  4. Rank by P&L. Show top 15.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
NIFTY_CSV = ROOT / "focus_zone_nifty_2026-05-05.csv"
LOT_SIZE = 65
CAPITAL = 20_000.0
MIN_PROFIT_PCT = 30.0  # only show entries with at least 30% upside
MIN_PREMIUM = 5.0      # filter out illiquid near-zero entries


def load_atm(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    # pos column is mixed str/numeric; ATM rows are tagged "ATM" or 0.0
    atm = df[df["pos"].astype(str).str.upper() == "ATM"].copy()
    if atm.empty:
        atm = df[df["pos"].astype(str) == "0.0"].copy()
    atm = atm.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return atm.set_index("timestamp")


def find_opportunities(atm: pd.DataFrame, side: str, hold_minutes_options: list[int]):
    """For every minute, look forward `max(hold_minutes_options)` minutes
    and record the best exit price. Yield rows where ROI >= MIN_PROFIT_PCT."""
    col = f"{side.lower()}_ltp"
    series = atm[col].copy()
    rows = []
    max_lookahead = max(hold_minutes_options)

    for i, (entry_ts, entry_p) in enumerate(series.items()):
        if entry_p < MIN_PREMIUM:
            continue
        # Within the next max_lookahead minutes, find best exit
        future = series.iloc[i + 1:]
        # Cap by time delta, not just count, since data may be sparse
        cutoff = entry_ts + pd.Timedelta(minutes=max_lookahead)
        future = future[future.index <= cutoff]
        if future.empty:
            continue
        best_exit_p = future.max()
        if best_exit_p <= entry_p:
            continue
        roi = (best_exit_p / entry_p - 1) * 100
        if roi < MIN_PROFIT_PCT:
            continue
        best_exit_ts = future.idxmax()
        hold_min = int((best_exit_ts - entry_ts).total_seconds() // 60)
        lots = int(CAPITAL // (entry_p * LOT_SIZE))
        if lots < 1:
            continue
        qty = lots * LOT_SIZE
        pnl = (best_exit_p - entry_p) * qty - 60.0  # brokerage
        rows.append({
            "entry_ts": entry_ts,
            "side": side,
            "entry_premium": round(entry_p, 2),
            "best_exit_ts": best_exit_ts,
            "best_exit_premium": round(best_exit_p, 2),
            "hold_minutes": hold_min,
            "lots": lots, "qty": qty,
            "pnl": int(pnl),
            "roi_pct": round(roi, 1),
            "spot_at_entry": round(float(atm.loc[entry_ts, "spot"]), 2),
            "spot_at_exit": round(float(atm.loc[best_exit_ts, "spot"]), 2),
        })
    return rows


def filter_non_overlapping(rows: list[dict], min_gap_min: int = 30) -> list[dict]:
    """Keep only the highest-PnL row from any overlapping cluster."""
    rows = sorted(rows, key=lambda r: r["pnl"], reverse=True)
    kept = []
    for r in rows:
        ok = True
        for k in kept:
            # Same side, entry windows within min_gap_min
            if r["side"] == k["side"] and abs(
                (r["entry_ts"] - k["entry_ts"]).total_seconds() / 60
            ) < min_gap_min:
                ok = False
                break
        if ok:
            kept.append(r)
    return sorted(kept, key=lambda r: r["entry_ts"])


def main():
    atm = load_atm(NIFTY_CSV)
    print(f"Loaded {len(atm)} ATM rows for NIFTY today.\n")

    spot_open = float(atm["spot"].iloc[0])
    spot_close = float(atm["spot"].iloc[-1])
    spot_low = float(atm["spot"].min())
    spot_high = float(atm["spot"].max())
    print(f"Spot today: open={spot_open:.0f}  high={spot_high:.0f}  "
          f"low={spot_low:.0f}  close={spot_close:.0f}  "
          f"range={spot_high-spot_low:.0f} pts\n")

    ce_ops = find_opportunities(atm, "CE", [15, 30, 60, 120])
    pe_ops = find_opportunities(atm, "PE", [15, 30, 60, 120])
    all_ops = ce_ops + pe_ops
    pruned = filter_non_overlapping(all_ops, min_gap_min=30)

    # Convert timestamps to IST (CSV looks like GST/UAE = GMT+4; IST = GMT+5:30)
    def to_ist(ts):
        return (ts + pd.Timedelta(hours=1, minutes=30)).strftime("%H:%M")

    print(f"Found {len(all_ops)} raw opportunities, "
          f"{len(pruned)} non-overlapping (>=30-min apart).\n")
    print(f"{'Entry IST':<10} {'Side':<5} {'Entry Rs':>9} {'Exit IST':<10} "
          f"{'Exit Rs':>9} {'Hold min':>9} {'ROI%':>7} {'P&L Rs':>10} "
          f"{'Spot in':>9} {'Spot out':>9}")
    print("-" * 110)

    pruned_sorted = sorted(pruned, key=lambda r: r["entry_ts"])
    for r in pruned_sorted:
        print(f"{to_ist(r['entry_ts']):<10} {r['side']:<5} "
              f"{r['entry_premium']:>9.2f} {to_ist(r['best_exit_ts']):<10} "
              f"{r['best_exit_premium']:>9.2f} {r['hold_minutes']:>9} "
              f"{r['roi_pct']:>7.1f} {r['pnl']:>10,} "
              f"{r['spot_at_entry']:>9.0f} {r['spot_at_exit']:>9.0f}")

    df = pd.DataFrame(pruned_sorted)
    if not df.empty:
        # Add IST column for the CSV
        df["entry_IST"] = df["entry_ts"].apply(to_ist)
        df["exit_IST"] = df["best_exit_ts"].apply(to_ist)
        out = ROOT / "reports" / "today_nifty_opportunities.csv"
        df.to_csv(out, index=False)
        print(f"\nWrote {out}")
        print(f"\nTotal P&L if you took all {len(df)} non-overlapping trades: "
              f"Rs.{df['pnl'].sum():,}")
        print(f"Best single trade: Rs.{df['pnl'].max():,} "
              f"({df.loc[df['pnl'].idxmax(), 'side']} @ "
              f"{to_ist(df.loc[df['pnl'].idxmax(), 'entry_ts'])})")


if __name__ == "__main__":
    main()
