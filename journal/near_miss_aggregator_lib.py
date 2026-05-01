"""
Shared near-miss aggregation helpers — consumed by both
backtesting/phase11_near_miss_analysis.py (pickle source) and
backtesting/live_near_miss_aggregator.py (journal-CSV source).

Each public function returns a pandas DataFrame so both callers can
serialise to CSV without further reshaping.
"""
from __future__ import annotations

import pandas as pd


REQUIRED_COLS = (
    "tactic", "direction", "ts", "blocked_by", "blocker_detail", "regime",
    "hypothetical_strike", "hypothetical_entry_premium",
    "hypothetical_exit_premium", "hypothetical_pnl", "hypothetical_outcome",
)


def per_tactic_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per tactic: count, W/L/BE/UNK, net hypothetical P&L."""
    if df.empty:
        return pd.DataFrame(columns=[
            "tactic", "near_misses", "wins", "losses", "breakeven",
            "unknown", "net_hypothetical_pnl",
        ])
    rows = []
    for tactic, sub in df.groupby("tactic"):
        outcomes = sub["hypothetical_outcome"].value_counts()
        net_pnl = sub.loc[
            sub["hypothetical_outcome"].isin(["WIN", "LOSS", "BREAKEVEN"]),
            "hypothetical_pnl",
        ].sum()
        rows.append({
            "tactic": tactic,
            "near_misses": len(sub),
            "wins": int(outcomes.get("WIN", 0)),
            "losses": int(outcomes.get("LOSS", 0)),
            "breakeven": int(outcomes.get("BREAKEVEN", 0)),
            "unknown": int(outcomes.get("UNKNOWN", 0)),
            "net_hypothetical_pnl": round(float(net_pnl), 2),
        })
    return pd.DataFrame(rows).sort_values(
        "net_hypothetical_pnl", ascending=False,
    ).reset_index(drop=True)


def blocker_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (tactic, blocker) with counts, net P&L, and verdict."""
    if df.empty:
        return pd.DataFrame(columns=[
            "tactic", "blocked_by", "times_fired",
            "wins", "losses", "breakeven", "unknown",
            "net_hypothetical_pnl", "verdict",
        ])
    rows = []
    for tactic, sub in df.groupby("tactic"):
        gb = sub.groupby("blocked_by")
        for blocker, grp in gb:
            outcomes = grp["hypothetical_outcome"].value_counts()
            n_win = int(outcomes.get("WIN", 0))
            n_loss = int(outcomes.get("LOSS", 0))
            n_be = int(outcomes.get("BREAKEVEN", 0))
            n_unk = int(outcomes.get("UNKNOWN", 0))
            net = grp.loc[
                grp["hypothetical_outcome"].isin(["WIN", "LOSS", "BREAKEVEN"]),
                "hypothetical_pnl",
            ].sum()
            actionable = n_win + n_loss + n_be
            if actionable < 5:
                verdict = "TOO_FEW_SAMPLES"
            elif net > 1000:
                verdict = "RELAX_REJECTING_WINNERS"
            elif net < -1000:
                verdict = "KEEP_REJECTING_LOSERS"
            else:
                verdict = "NEUTRAL"
            rows.append({
                "tactic": tactic,
                "blocked_by": blocker,
                "times_fired": len(grp),
                "wins": n_win,
                "losses": n_loss,
                "breakeven": n_be,
                "unknown": n_unk,
                "net_hypothetical_pnl": round(float(net), 2),
                "verdict": verdict,
            })
    return pd.DataFrame(rows).sort_values(
        ["tactic", "net_hypothetical_pnl"], ascending=[True, False],
    ).reset_index(drop=True)


def direction_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "tactic", "direction", "near_misses",
            "wins", "losses", "net_hypothetical_pnl",
        ])
    rows = []
    for (tactic, direction), grp in df.groupby(["tactic", "direction"]):
        outcomes = grp["hypothetical_outcome"].value_counts()
        net = grp.loc[
            grp["hypothetical_outcome"].isin(["WIN", "LOSS", "BREAKEVEN"]),
            "hypothetical_pnl",
        ].sum()
        rows.append({
            "tactic": tactic,
            "direction": direction,
            "near_misses": len(grp),
            "wins": int(outcomes.get("WIN", 0)),
            "losses": int(outcomes.get("LOSS", 0)),
            "net_hypothetical_pnl": round(float(net), 2),
        })
    return pd.DataFrame(rows).sort_values(
        ["tactic", "direction"],
    ).reset_index(drop=True)


def top_impact(df: pd.DataFrame, *, n: int = 20) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "ts", "tactic", "direction", "blocked_by",
            "hypothetical_pnl", "hypothetical_outcome",
        ])
    actionable = df[df["hypothetical_outcome"].isin(["WIN", "LOSS"])].copy()
    if actionable.empty:
        return actionable[[
            "ts", "tactic", "direction", "blocked_by",
            "hypothetical_pnl", "hypothetical_outcome",
        ]]
    actionable["abs_pnl"] = actionable["hypothetical_pnl"].abs()
    return actionable.sort_values("abs_pnl", ascending=False).head(n)[[
        "ts", "tactic", "direction", "blocked_by",
        "hypothetical_pnl", "hypothetical_outcome",
    ]].reset_index(drop=True)


def write_all_csvs(df: pd.DataFrame, out_dir, *, prefix: str) -> dict:
    """Write the four summary CSVs + the raw rows. Returns a dict of
    section -> path written."""
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "raw": out_dir / f"{prefix}_raw.csv",
        "per_tactic": out_dir / f"{prefix}_per_tactic.csv",
        "blockers": out_dir / f"{prefix}_blockers.csv",
        "direction": out_dir / f"{prefix}_direction.csv",
        "top_impact": out_dir / f"{prefix}_top_impact.csv",
    }
    df.to_csv(paths["raw"], index=False)
    per_tactic_summary(df).to_csv(paths["per_tactic"], index=False)
    blocker_summary(df).to_csv(paths["blockers"], index=False)
    direction_summary(df).to_csv(paths["direction"], index=False)
    top_impact(df).to_csv(paths["top_impact"], index=False)
    return paths
