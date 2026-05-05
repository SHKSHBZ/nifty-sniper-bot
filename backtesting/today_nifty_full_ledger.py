"""Comprehensive NIFTY trade ledger for 2026-05-05.

For each profitable opportunity identified, lock the strike at entry
time and trace THAT specific contract forward (vs the floating-ATM
approach in today_nifty_opportunities.py). Add context features at
entry so we can see WHAT was happening in the chain when each move
started:

  - max_pain_strike  = strike with highest combined CE+PE OI value
  - support_strike   = strike below spot with highest PE OI
  - resistance_strike= strike above spot with highest CE OI
  - pe_oi_delta_5m   = sum across all visible strikes
  - ce_oi_delta_5m   = sum across all visible strikes
  - oi_pcr_now       = total PE OI / total CE OI across visible strikes
  - prem_pcr_atm     = PE LTP / CE LTP at ATM
  - atm_ce_delta     = ATM CE delta (IV gauge)

Capital Rs.20,000/trade, lot=65, brokerage Rs.60 r/t.

Outputs:
  - reports/today_nifty_full_ledger.csv  (every trade detail)
  - reports/today_nifty_full_ledger.md   (human-readable summary)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "focus_zone_nifty_2026-05-05.csv"
LOT = 65
CAPITAL = 20_000.0
BROKERAGE = 60.0
MIN_PROFIT_PCT = 30.0
MIN_PREMIUM = 5.0
LOOKAHEAD_MIN = 120
DEDUP_GAP_MIN = 30
MARKET_OPEN_IST = "09:15"  # filter pre-market noise (CSV time + 1:30)


def to_ist(ts):
    return (ts + pd.Timedelta(hours=1, minutes=30)).strftime("%H:%M")


def is_market_hours(ts) -> bool:
    ist = ts + pd.Timedelta(hours=1, minutes=30)
    return ist.time() >= pd.Timestamp("09:15").time() \
        and ist.time() <= pd.Timestamp("15:30").time()


def load_chain(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values(["timestamp", "strike"])


def compute_chain_features(snap: pd.DataFrame, spot: float) -> dict:
    """For one timestamp's chain snapshot (multiple rows, one per strike),
    return summary features."""
    if snap.empty:
        return {}
    snap = snap.copy()
    snap["combined_oi_value"] = snap["ce_oi"] + snap["pe_oi"]
    max_pain = int(snap.loc[snap["combined_oi_value"].idxmax(), "strike"])
    above = snap[snap["strike"] > spot]
    below = snap[snap["strike"] < spot]
    resistance = int(above.loc[above["ce_oi"].idxmax(), "strike"]) if not above.empty else 0
    support = int(below.loc[below["pe_oi"].idxmax(), "strike"]) if not below.empty else 0
    total_ce_oi = float(snap["ce_oi"].sum())
    total_pe_oi = float(snap["pe_oi"].sum())
    oi_pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 0
    atm_row = snap[snap["pos"].astype(str).str.upper() == "ATM"]
    atm_ce_delta = float(atm_row["ce_delta"].iloc[0]) if not atm_row.empty else 0
    atm_ce_ltp = float(atm_row["ce_ltp"].iloc[0]) if not atm_row.empty else 0
    atm_pe_ltp = float(atm_row["pe_ltp"].iloc[0]) if not atm_row.empty else 0
    prem_pcr = atm_pe_ltp / atm_ce_ltp if atm_ce_ltp > 0 else 0
    return {
        "max_pain": max_pain, "support": support, "resistance": resistance,
        "total_ce_oi": int(total_ce_oi), "total_pe_oi": int(total_pe_oi),
        "oi_pcr": round(oi_pcr, 2),
        "atm_ce_delta": round(atm_ce_delta, 3),
        "prem_pcr_atm": round(prem_pcr, 2),
    }


def find_trade_opportunities(df: pd.DataFrame) -> list[dict]:
    """For each minute and each strike, see if buying CE or PE there
    would have been profitable within LOOKAHEAD_MIN minutes."""
    timestamps = sorted(df["timestamp"].unique())
    # Pre-index for fast lookups
    by_ts = {ts: df[df["timestamp"] == ts] for ts in timestamps}

    candidates: list[dict] = []
    for entry_ts in timestamps:
        if not is_market_hours(entry_ts):
            continue
        snap = by_ts[entry_ts]
        atm_rows = snap[snap["pos"].astype(str).str.upper() == "ATM"]
        if atm_rows.empty:
            continue
        spot = float(atm_rows["spot"].iloc[0])
        atm_strike = int(atm_rows["strike"].iloc[0])

        # Entry is always the ATM strike at this minute
        ce_entry = float(atm_rows["ce_ltp"].iloc[0])
        pe_entry = float(atm_rows["pe_ltp"].iloc[0])

        cutoff = entry_ts + pd.Timedelta(minutes=LOOKAHEAD_MIN)

        # Trace SAME strike forward
        future = df[
            (df["timestamp"] > entry_ts)
            & (df["timestamp"] <= cutoff)
            & (df["strike"] == atm_strike)
        ]
        if future.empty:
            continue
        # Best exit for CE
        if ce_entry >= MIN_PREMIUM:
            best_ce_idx = future["ce_ltp"].idxmax()
            best_ce_p = float(future.loc[best_ce_idx, "ce_ltp"])
            best_ce_ts = future.loc[best_ce_idx, "timestamp"]
            ce_roi = (best_ce_p / ce_entry - 1) * 100
            if ce_roi >= MIN_PROFIT_PCT:
                lots = int(CAPITAL // (ce_entry * LOT))
                if lots >= 1:
                    qty = lots * LOT
                    pnl = (best_ce_p - ce_entry) * qty - BROKERAGE
                    candidates.append({
                        "entry_ts": entry_ts, "side": "CE",
                        "strike": atm_strike, "spot_at_entry": spot,
                        "entry_premium": round(ce_entry, 2),
                        "exit_ts": best_ce_ts,
                        "exit_premium": round(best_ce_p, 2),
                        "spot_at_exit": float(future.loc[best_ce_idx, "spot"]),
                        "lots": lots, "qty": qty,
                        "hold_minutes": int(
                            (best_ce_ts - entry_ts).total_seconds() // 60),
                        "roi_pct": round(ce_roi, 1),
                        "pnl": int(pnl),
                    })
        # Best exit for PE
        if pe_entry >= MIN_PREMIUM:
            best_pe_idx = future["pe_ltp"].idxmax()
            best_pe_p = float(future.loc[best_pe_idx, "pe_ltp"])
            best_pe_ts = future.loc[best_pe_idx, "timestamp"]
            pe_roi = (best_pe_p / pe_entry - 1) * 100
            if pe_roi >= MIN_PROFIT_PCT:
                lots = int(CAPITAL // (pe_entry * LOT))
                if lots >= 1:
                    qty = lots * LOT
                    pnl = (best_pe_p - pe_entry) * qty - BROKERAGE
                    candidates.append({
                        "entry_ts": entry_ts, "side": "PE",
                        "strike": atm_strike, "spot_at_entry": spot,
                        "entry_premium": round(pe_entry, 2),
                        "exit_ts": best_pe_ts,
                        "exit_premium": round(best_pe_p, 2),
                        "spot_at_exit": float(future.loc[best_pe_idx, "spot"]),
                        "lots": lots, "qty": qty,
                        "hold_minutes": int(
                            (best_pe_ts - entry_ts).total_seconds() // 60),
                        "roi_pct": round(pe_roi, 1),
                        "pnl": int(pnl),
                    })
    return candidates


def dedupe(rows: list[dict], gap_min: int) -> list[dict]:
    rows = sorted(rows, key=lambda r: r["pnl"], reverse=True)
    kept: list[dict] = []
    for r in rows:
        ok = True
        for k in kept:
            if r["side"] == k["side"] and abs(
                (r["entry_ts"] - k["entry_ts"]).total_seconds() / 60
            ) < gap_min:
                ok = False
                break
        if ok:
            kept.append(r)
    return sorted(kept, key=lambda r: r["entry_ts"])


def add_context_features(rows: list[dict], df: pd.DataFrame) -> list[dict]:
    """Annotate each trade with chain-features at entry minute."""
    timestamps = sorted(df["timestamp"].unique())
    ts_set = set(timestamps)

    for r in rows:
        ts = r["entry_ts"]
        snap = df[df["timestamp"] == ts]
        feats = compute_chain_features(snap, r["spot_at_entry"])
        r.update(feats)

        # 5-min OI deltas (rolling sum across all strikes)
        prior_ts = ts - pd.Timedelta(minutes=5)
        prior_snaps = df[df["timestamp"].between(prior_ts, ts)]
        if not prior_snaps.empty:
            grp = prior_snaps.groupby("timestamp")[["ce_oi", "pe_oi"]].sum()
            if len(grp) >= 2:
                r["ce_oi_delta_5m"] = int(grp["ce_oi"].iloc[-1] - grp["ce_oi"].iloc[0])
                r["pe_oi_delta_5m"] = int(grp["pe_oi"].iloc[-1] - grp["pe_oi"].iloc[0])
            else:
                r["ce_oi_delta_5m"] = r["pe_oi_delta_5m"] = 0
        else:
            r["ce_oi_delta_5m"] = r["pe_oi_delta_5m"] = 0
    return rows


def main():
    df = load_chain(CSV)
    print(f"Loaded {len(df)} chain rows for NIFTY today.\n")

    raw = find_trade_opportunities(df)
    pruned = dedupe(raw, DEDUP_GAP_MIN)
    pruned = add_context_features(pruned, df)

    # Console table
    print(f"Found {len(raw)} raw opportunities, "
          f"{len(pruned)} non-overlapping (>={DEDUP_GAP_MIN} min apart).\n")
    print(f"{'Entry IST':<10} {'Side':<5} {'Strike':>7} {'Spot':>7} "
          f"{'Entry Rs':>9} {'Lots':>5} {'Qty':>5} "
          f"{'Exit IST':<10} {'Exit Rs':>9} {'Hold':>5} "
          f"{'ROI%':>7} {'P&L Rs':>10}")
    print("-" * 130)
    for r in pruned:
        print(f"{to_ist(r['entry_ts']):<10} {r['side']:<5} "
              f"{r['strike']:>7} {r['spot_at_entry']:>7.0f} "
              f"{r['entry_premium']:>9.2f} {r['lots']:>5} {r['qty']:>5} "
              f"{to_ist(r['exit_ts']):<10} {r['exit_premium']:>9.2f} "
              f"{r['hold_minutes']:>5} {r['roi_pct']:>7.1f} "
              f"{r['pnl']:>10,}")

    df_out = pd.DataFrame(pruned)
    if not df_out.empty:
        df_out["entry_IST"] = df_out["entry_ts"].apply(to_ist)
        df_out["exit_IST"] = df_out["exit_ts"].apply(to_ist)
        # Reorder columns for readability
        cols = ["entry_IST", "side", "strike", "spot_at_entry",
                "entry_premium", "lots", "qty",
                "exit_IST", "exit_premium", "spot_at_exit",
                "hold_minutes", "roi_pct", "pnl",
                "max_pain", "support", "resistance",
                "oi_pcr", "prem_pcr_atm", "atm_ce_delta",
                "ce_oi_delta_5m", "pe_oi_delta_5m",
                "total_ce_oi", "total_pe_oi"]
        df_out = df_out[[c for c in cols if c in df_out.columns]]
        out_csv = ROOT / "reports" / "today_nifty_full_ledger.csv"
        df_out.to_csv(out_csv, index=False)
        print(f"\nWrote {out_csv}")

        total_pnl = df_out["pnl"].sum()
        total_capital = len(df_out) * CAPITAL
        print(f"\n{'='*70}")
        print(f"TOTAL P&L (taking all {len(df_out)} non-overlapping trades): "
              f"Rs.{int(total_pnl):,}")
        print(f"Capital required (Rs.20k per trade): Rs.{int(total_capital):,}")
        print(f"Single-day return on capital deployed: "
              f"{total_pnl/total_capital*100:.1f}%")
        print(f"Average P&L per trade: Rs.{int(total_pnl/len(df_out)):,}")
        best_idx = df_out["pnl"].idxmax()
        worst_idx = df_out["pnl"].idxmin()
        print(f"Best trade:  Rs.{int(df_out.loc[best_idx, 'pnl']):,} "
              f"({df_out.loc[best_idx, 'side']} "
              f"strike {df_out.loc[best_idx, 'strike']} @ "
              f"{df_out.loc[best_idx, 'entry_IST']})")
        print(f"Worst trade: Rs.{int(df_out.loc[worst_idx, 'pnl']):,} "
              f"({df_out.loc[worst_idx, 'side']} "
              f"strike {df_out.loc[worst_idx, 'strike']} @ "
              f"{df_out.loc[worst_idx, 'entry_IST']})")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
