"""
Shared near-miss aggregation helpers — consumed by both
backtesting/phase11_near_miss_analysis.py (pickle source) and
backtesting/live_near_miss_aggregator.py (journal-JSON source).

Pure functions over a pandas DataFrame so the two callers can plug in
different loaders but reuse the rendering.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


REQUIRED_COLS = (
    "tactic", "direction", "ts", "blocked_by", "blocker_detail", "regime",
    "hypothetical_strike", "hypothetical_entry_premium",
    "hypothetical_exit_premium", "hypothetical_pnl", "hypothetical_outcome",
)


def render_summary(df: pd.DataFrame, *, header: str | None = None) -> list[str]:
    """Top-line summary lines for the markdown report."""
    out: list[str] = []
    out.append(header or "# Near-Miss Aggregate Analysis\n")
    if df.empty:
        out.append("(No near-misses captured.)")
        return out
    out.append(f"Total near-misses captured: **{len(df):,}**")
    out.append(f"Date range: {df['ts'].min()} -> {df['ts'].max()}")
    out.append(
        "A near-miss is a 5-min bar where exactly ONE gate blocked the "
        "tactic from firing. For each, we simulate the would-have-been "
        "trade with the tactic's prescribed exits and classify the "
        "outcome.\n"
    )
    return out


def render_per_tactic(df: pd.DataFrame) -> list[str]:
    out: list[str] = ["## Per-Tactic Summary\n"]
    out.append("| Tactic | Near-misses | Hypothetical W | L | Breakeven | "
               "Unknown | Net hypothetical P&L |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for tactic, sub in df.groupby("tactic"):
        outcomes = sub["hypothetical_outcome"].value_counts()
        wins = int(outcomes.get("WIN", 0))
        losses = int(outcomes.get("LOSS", 0))
        be = int(outcomes.get("BREAKEVEN", 0))
        unk = int(outcomes.get("UNKNOWN", 0))
        net_pnl = sub.loc[
            sub["hypothetical_outcome"].isin(["WIN", "LOSS", "BREAKEVEN"]),
            "hypothetical_pnl",
        ].sum()
        out.append(f"| {tactic} | {len(sub)} | {wins} | {losses} | {be} "
                   f"| {unk} | Rs {net_pnl:,.0f} |")
    out.append("")
    return out


def render_blocker_table(df: pd.DataFrame) -> list[str]:
    out: list[str] = ["## Blocker Analysis (per tactic)\n"]
    out.append(
        "For each (tactic, blocker), counts how often it fired AND the net "
        "hypothetical P&L from those rejected trades. **A blocker with high "
        "positive net P&L is a candidate for relaxation** — it's been "
        "rejecting profitable setups. Negative net P&L means the blocker "
        "is doing its job.\n"
    )
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
        out.append("| Blocker | Times fired | W | L | BE | UNK | "
                   "Net hypothetical P&L | Verdict |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for _, r in gb.iterrows():
            actionable = (r["n_win"] + r["n_loss"] + r["n_be"])
            net = r["net_pnl"]
            if actionable < 5:
                verdict = "(too few samples)"
            elif net > 1000:
                verdict = "RELAX -- rejecting winners"
            elif net < -1000:
                verdict = "KEEP -- rejecting losers"
            else:
                verdict = "neutral / no clear edge"
            out.append(
                f"| `{r['blocked_by']}` | {int(r['n'])} | {int(r['n_win'])} "
                f"| {int(r['n_loss'])} | {int(r['n_be'])} | {int(r['n_unk'])} "
                f"| Rs {net:,.0f} | {verdict} |"
            )
        out.append("")
    return out


def render_direction_breakdown(df: pd.DataFrame) -> list[str]:
    out: list[str] = ["## Direction Breakdown\n"]
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
    return out


def render_top_impact(df: pd.DataFrame, *, n: int = 20) -> list[str]:
    out: list[str] = []
    actionable = df[df["hypothetical_outcome"].isin(["WIN", "LOSS"])].copy()
    if len(actionable) == 0:
        return out
    out.append(f"## Top {n} Highest-Impact Near-Misses\n")
    actionable["abs_pnl"] = actionable["hypothetical_pnl"].abs()
    top = actionable.sort_values("abs_pnl", ascending=False).head(n)
    out.append("| Date | Time | Tactic | Dir | Blocker | Hypo P&L | Outcome |")
    out.append("|---|---|---|---|---|---:|---|")
    for _, r in top.iterrows():
        ts = r["ts"]
        date_str = ts.date() if hasattr(ts, "date") else str(ts)[:10]
        time_str = ts.strftime('%H:%M') if hasattr(ts, "strftime") else str(ts)[11:16]
        out.append(
            f"| {date_str} | {time_str} | {r['tactic']} "
            f"| {r['direction']} | `{r['blocked_by']}` "
            f"| Rs {r['hypothetical_pnl']:+,.0f} | {r['hypothetical_outcome']} |"
        )
    out.append("")
    return out


def build_full_report(
    df: pd.DataFrame, *, header: str | None = None,
) -> list[str]:
    """Stitch all sections together."""
    sections: list[Iterable[str]] = [
        render_summary(df, header=header),
    ]
    if not df.empty:
        sections += [
            render_per_tactic(df),
            render_blocker_table(df),
            render_direction_breakdown(df),
            render_top_impact(df),
        ]
    out: list[str] = []
    for s in sections:
        out.extend(s)
    return out
