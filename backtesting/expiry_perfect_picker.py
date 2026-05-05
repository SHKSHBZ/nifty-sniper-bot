"""Perfect-leg upper bound vs straddle reality.

For every expiry where the straddle would have fired, compute three
parallel scenarios with ₹20k capital:

  CE_only      : put all ₹20k into ATM CE at 14:50, hold to 15:25
  PE_only      : put all ₹20k into ATM PE at 14:50, hold to 15:25
  perfect_pick : pick whichever of {CE_only, PE_only} was a winner
                 (this is the magic-crystal-ball upper bound)
  straddle     : split ₹20k equally across CE+PE (what we actually do)

The gap between `perfect_pick` and `straddle` shows how much edge
we leave on the table by not predicting direction.

Output: reports/expiry_perfect_picker.csv  (per-expiry)
        reports/expiry_perfect_picker_summary.md
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from backtesting import expiry_gamma_hero as base


CAPITAL = 20_000.0
LOT = 65
ENTRY_TIME = dtime(14, 50)
EXIT_TIME = dtime(15, 25)
SLIP = 0.05
BROK_LEG = 60.0  # round-trip for one leg
BROK_STRADDLE = 120.0


@dataclass
class Row:
    expiry: str
    spot_entry: float
    spot_exit: float
    spot_move: float
    atm: int
    ce_entry: float
    ce_exit: float
    pe_entry: float
    pe_exit: float
    ce_only_qty: int
    pe_only_qty: int
    straddle_qty: int
    ce_only_pnl: float
    pe_only_pnl: float
    perfect_pick: str   # "CE", "PE", or "TIE"
    perfect_pnl: float
    straddle_pnl: float
    capture_pct: float  # straddle / perfect


def evaluate_one(expiry_token: str, expiry_date, spot_df: pd.DataFrame):
    tz = spot_df.index.tz
    entry_ts = pd.Timestamp.combine(expiry_date.date(), ENTRY_TIME).tz_localize(tz)
    exit_ts = pd.Timestamp.combine(expiry_date.date(), EXIT_TIME).tz_localize(tz)

    spot_e = base.get_value_at(spot_df, entry_ts, "close")
    spot_x = base.get_value_at(spot_df, exit_ts, "close")
    if spot_e is None or spot_e <= 0:
        return None
    atm = int(round(spot_e / 50) * 50)

    ce = base.load_option(atm, "CE", expiry_token)
    pe = base.load_option(atm, "PE", expiry_token)
    ce_e = base.get_value_at(ce, entry_ts, "close")
    pe_e = base.get_value_at(pe, entry_ts, "close")
    ce_x = base.get_value_at(ce, exit_ts, "close")
    pe_x = base.get_value_at(pe, exit_ts, "close")

    if any(v is None for v in (ce_e, pe_e, ce_x, pe_x)):
        # Fallback: last available bar before exit_ts
        if ce_x is None and ce is not None:
            sub = ce[ce.index <= exit_ts]
            ce_x = float(sub["close"].iloc[-1]) if len(sub) else 0.0
        if pe_x is None and pe is not None:
            sub = pe[pe.index <= exit_ts]
            pe_x = float(sub["close"].iloc[-1]) if len(sub) else 0.0
    if ce_e is None or pe_e is None:
        return None

    # CE-only sizing
    ce_only_lots = int(CAPITAL // (ce_e * LOT)) if ce_e > 0 else 0
    ce_only_qty = ce_only_lots * LOT
    ce_only_gross = (max(0.0, ce_x - SLIP) - (ce_e + SLIP)) * ce_only_qty
    ce_only_pnl = ce_only_gross - BROK_LEG if ce_only_qty > 0 else 0.0

    # PE-only sizing
    pe_only_lots = int(CAPITAL // (pe_e * LOT)) if pe_e > 0 else 0
    pe_only_qty = pe_only_lots * LOT
    pe_only_gross = (max(0.0, pe_x - SLIP) - (pe_e + SLIP)) * pe_only_qty
    pe_only_pnl = pe_only_gross - BROK_LEG if pe_only_qty > 0 else 0.0

    # Straddle sizing (equal lots, total cost ~ ₹20k)
    combined = ce_e + pe_e
    s_lots = int(CAPITAL // (combined * LOT)) if combined > 0 else 0
    s_qty = s_lots * LOT
    s_gross = (max(0.0, ce_x + pe_x - 2 * SLIP) - (combined + 2 * SLIP)) * s_qty
    s_pnl = s_gross - BROK_STRADDLE if s_qty > 0 else 0.0

    if ce_only_pnl > pe_only_pnl:
        pick = "CE"
    elif pe_only_pnl > ce_only_pnl:
        pick = "PE"
    else:
        pick = "TIE"
    perfect = max(ce_only_pnl, pe_only_pnl)
    capture = (s_pnl / perfect * 100.0) if perfect > 0 else 0.0

    return Row(
        expiry=expiry_token,
        spot_entry=round(spot_e, 2),
        spot_exit=round(spot_x or 0, 2),
        spot_move=round((spot_x or 0) - spot_e, 2),
        atm=atm,
        ce_entry=round(ce_e, 2), ce_exit=round(ce_x, 2),
        pe_entry=round(pe_e, 2), pe_exit=round(pe_x, 2),
        ce_only_qty=ce_only_qty, pe_only_qty=pe_only_qty,
        straddle_qty=s_qty,
        ce_only_pnl=round(ce_only_pnl, 0),
        pe_only_pnl=round(pe_only_pnl, 0),
        perfect_pick=pick,
        perfect_pnl=round(perfect, 0),
        straddle_pnl=round(s_pnl, 0),
        capture_pct=round(capture, 1),
    )


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

    if not rows:
        print("No qualifying expiries.")
        return

    df = pd.DataFrame([asdict(r) for r in rows])
    csv_path = base.REPORTS_DIR / "expiry_perfect_picker.csv"
    df.to_csv(csv_path, index=False)

    # Aggregates
    n = len(df)
    perf_total = df["perfect_pnl"].sum()
    s_total = df["straddle_pnl"].sum()
    ce_total = df["ce_only_pnl"].sum()
    pe_total = df["pe_only_pnl"].sum()
    ce_wins = (df["perfect_pick"] == "CE").sum()
    pe_wins = (df["perfect_pick"] == "PE").sum()
    capture = s_total / perf_total * 100 if perf_total else 0
    s_wins = (df["straddle_pnl"] > 0).sum()

    md = [
        "# Expiry Perfect-Leg vs Straddle\n",
        f"Capital ₹{int(CAPITAL):,}/trade. Lot {LOT}. ATM. Entry 14:50, exit 15:25.",
        f"Slippage ₹{SLIP}/leg. Brokerage ₹{int(BROK_LEG)}/leg single, "
        f"₹{int(BROK_STRADDLE)} for straddle.\n",
        "## Headline\n",
        f"- Expiries traded:        **{n}**",
        f"- CE was right:           {ce_wins} ({ce_wins/n*100:.0f}%)",
        f"- PE was right:           {pe_wins} ({pe_wins/n*100:.0f}%)",
        "",
        "### P&L if you knew direction perfectly each day",
        f"- Perfect picker total:   **₹{perf_total:,.0f}**",
        f"- CE-only total:          ₹{ce_total:,.0f}",
        f"- PE-only total:          ₹{pe_total:,.0f}",
        "",
        "### Straddle reality (no prediction needed)",
        f"- Straddle total:         **₹{s_total:,.0f}**",
        f"- Straddle wins:          {s_wins}/{n} ({s_wins/n*100:.0f}%)",
        f"- Capture vs perfect:     **{capture:.1f}%**",
        "",
        "## Per-expiry breakdown",
        "```",
        df.to_string(index=False),
        "```",
        "",
    ]
    md_path = base.REPORTS_DIR / "expiry_perfect_picker_summary.md"
    md_path.write_text("\n".join(md))
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}\n")
    print(f"Perfect picker total: ₹{perf_total:,.0f}")
    print(f"Straddle total:       ₹{s_total:,.0f}")
    print(f"Capture:              {capture:.1f}%")
    print(f"CE was right: {ce_wins}/{n}, PE was right: {pe_wins}/{n}")


if __name__ == "__main__":
    main()
