"""
Phase 11 — Aggregate near-miss analysis.

Loads phase11_near_misses.pkl (produced by run_all_tactics.py with the
new diagnostic gates), aggregates by (tactic, blocker), and computes
the would-have-been P&L impact per blocker.

Outputs reports/phase11_near_miss_summary.md, which answers:

  * Which gate blocked the most signals?
  * Of the trades it blocked, how many would have WON if entered?
  * What's the cumulative hypothetical P&L delta if we relaxed each gate?

This is the actionable artifact: blockers that frequently rejected
winners are candidates for relaxation; blockers that rejected losers
are doing their job.
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.run_all_tactics import NearMiss, TacticTrade  # noqa: F401
import __main__ as _main_mod
_main_mod.NearMiss = NearMiss
_main_mod.TacticTrade = TacticTrade

NM_PICKLE = ROOT / "reports" / "phase11_near_misses.pkl"
TRADE_PICKLE = ROOT / "reports" / "phase9_tactic_trades.pkl"
OUTPUT = ROOT / "reports" / "phase11_near_miss_summary.md"


def load_near_misses() -> dict:
    if not NM_PICKLE.exists():
        raise SystemExit(f"Cache not found: {NM_PICKLE}")
    with NM_PICKLE.open("rb") as fh:
        return pickle.load(fh)


def to_df(near_misses_by_tactic: dict) -> pd.DataFrame:
    rows = []
    for tactic, nms in near_misses_by_tactic.items():
        for nm in nms:
            rows.append({
                "tactic": tactic,
                "direction": nm.direction,
                "ts": nm.ts,
                "blocked_by": nm.blocked_by,
                "blocker_detail": nm.blocker_detail,
                "regime": nm.regime_at_ts,
                "hypothetical_strike": nm.hypothetical_strike,
                "hypothetical_entry_premium": nm.hypothetical_entry_premium,
                "hypothetical_exit_premium": nm.hypothetical_exit_premium,
                "hypothetical_exit_reason": nm.hypothetical_exit_reason,
                "hypothetical_pnl": nm.hypothetical_pnl,
                "hypothetical_outcome": nm.hypothetical_outcome,
            })
    df = pd.DataFrame(rows)
    return df


def main():
    nms = load_near_misses()
    df = to_df(nms)
    if df.empty:
        print("No near-misses captured. Bot may have fired everything or "
              "all tactics' gates were too far from passing.")
        OUTPUT.write_text("# Phase 11 — No near-misses captured.\n")
        return

    # ----- Top-line summary --------------------------------------------
    out: list[str] = []
    out.append("# Phase 11 — Near-Miss Aggregate Analysis\n")
    out.append(f"Total near-misses captured: **{len(df):,}**")
    out.append(f"Date range: {df['ts'].min()} -> {df['ts'].max()}")
    out.append(
        "A near-miss is a 5-min bar where exactly ONE gate blocked the "
        "tactic from firing. For each, we simulate the would-have-been "
        "trade with default exits (TP +50%, SL -30%, time stop 120m) "
        "and classify the outcome.\n")

    # ----- Per-tactic breakdown ----------------------------------------
    out.append("## Per-Tactic Summary\n")
    out.append("| Tactic | Near-misses | Hypothetical W | L | Breakeven | Unknown | Net hypothetical P&L |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for tactic, sub in df.groupby("tactic"):
        outcomes = sub["hypothetical_outcome"].value_counts()
        wins = outcomes.get("WIN", 0)
        losses = outcomes.get("LOSS", 0)
        be = outcomes.get("BREAKEVEN", 0)
        unk = outcomes.get("UNKNOWN", 0)
        net_pnl = sub.loc[sub["hypothetical_outcome"].isin(["WIN", "LOSS", "BREAKEVEN"]),
                          "hypothetical_pnl"].sum()
        out.append(f"| {tactic} | {len(sub)} | {wins} | {losses} | {be} | {unk} "
                   f"| Rs {net_pnl:,.0f} |")
    out.append("")

    # ----- Per-tactic, per-blocker — the actionable table --------------
    out.append("## Blocker Analysis (per tactic)\n")
    out.append(
        "For each (tactic, blocker), counts how often it fired AND the net "
        "hypothetical P&L from those rejected trades. **A blocker with high "
        "positive net P&L is a candidate for relaxation** — it's been "
        "rejecting profitable setups. Negative net P&L means the blocker "
        "is doing its job.\n")

    for tactic, sub in df.groupby("tactic"):
        out.append(f"### {tactic}\n")
        gb = sub.groupby("blocked_by").agg(
            n=("hypothetical_pnl", "count"),
            n_win=("hypothetical_outcome", lambda x: (x == "WIN").sum()),
            n_loss=("hypothetical_outcome", lambda x: (x == "LOSS").sum()),
            n_be=("hypothetical_outcome", lambda x: (x == "BREAKEVEN").sum()),
            n_unk=("hypothetical_outcome", lambda x: (x == "UNKNOWN").sum()),
            net_pnl=("hypothetical_pnl",
                     lambda x: x[df.loc[x.index, "hypothetical_outcome"]
                                 .isin(["WIN", "LOSS", "BREAKEVEN"])].sum()),
        ).reset_index().sort_values("net_pnl", ascending=False)
        out.append("| Blocker | Times fired | W | L | BE | UNK | Net hypothetical P&L | Verdict |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for _, r in gb.iterrows():
            actionable = (r["n_win"] + r["n_loss"] + r["n_be"])
            net = r["net_pnl"]
            if actionable < 5:
                verdict = "(too few samples)"
            elif net > 1000:
                verdict = "🔴 RELAX — rejecting winners"
            elif net < -1000:
                verdict = "✅ KEEP — rejecting losers"
            else:
                verdict = "⚪ neutral / no clear edge"
            out.append(
                f"| `{r['blocked_by']}` | {int(r['n'])} | {int(r['n_win'])} "
                f"| {int(r['n_loss'])} | {int(r['n_be'])} | {int(r['n_unk'])} "
                f"| Rs {net:,.0f} | {verdict} |"
            )
        out.append("")

    # ----- Direction breakdown -----------------------------------------
    out.append("## Direction Breakdown\n")
    dgb = df.groupby(["tactic", "direction"]).agg(
        n=("hypothetical_pnl", "count"),
        n_win=("hypothetical_outcome", lambda x: (x == "WIN").sum()),
        n_loss=("hypothetical_outcome", lambda x: (x == "LOSS").sum()),
        net_pnl=("hypothetical_pnl",
                 lambda x: x[df.loc[x.index, "hypothetical_outcome"]
                             .isin(["WIN", "LOSS", "BREAKEVEN"])].sum()),
    ).reset_index()
    out.append("| Tactic | Dir | Near-misses | W | L | Net hypothetical P&L |")
    out.append("|---|---|---:|---:|---:|---:|")
    for _, r in dgb.iterrows():
        out.append(f"| {r['tactic']} | {r['direction']} | {int(r['n'])} "
                   f"| {int(r['n_win'])} | {int(r['n_loss'])} "
                   f"| Rs {r['net_pnl']:,.0f} |")
    out.append("")

    # ----- Top-impact individual near-misses ---------------------------
    actionable = df[df["hypothetical_outcome"].isin(["WIN", "LOSS"])].copy()
    if len(actionable):
        out.append("## Top 20 Highest-Impact Near-Misses\n")
        actionable["abs_pnl"] = actionable["hypothetical_pnl"].abs()
        top = actionable.sort_values("abs_pnl", ascending=False).head(20)
        out.append("| Date | Time | Tactic | Dir | Blocker | Hypo P&L | Outcome |")
        out.append("|---|---|---|---|---|---:|---|")
        for _, r in top.iterrows():
            ts = r["ts"]
            out.append(
                f"| {ts.date()} | {ts.strftime('%H:%M')} | {r['tactic']} "
                f"| {r['direction']} | `{r['blocked_by']}` "
                f"| Rs {r['hypothetical_pnl']:+,.0f} | {r['hypothetical_outcome']} |"
            )
        out.append("")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(out))
    print(f"Wrote: {OUTPUT.relative_to(ROOT)}")
    print(f"Total near-misses: {len(df):,}")


if __name__ == "__main__":
    main()
