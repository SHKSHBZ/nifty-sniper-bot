"""
Phase 7 — Loser Analysis.

Cheap, high-value analysis: take the captured trade records, replay them
under production params, slice the resulting trades by every available
dimension (time-of-day, day-of-week, month, direction, regime, premium
size, exit reason), and report which buckets are systematically losing.

Then propose simple "skip rules" — filters that would skip the trades in
the worst-performing buckets — and run a what-if simulation with each
rule applied, isolated and combined.

If a skip rule meaningfully improves out-of-sample P&L without nuking the
trade count, that's a real edge. If they all do nothing, the strategy
needs structural change rather than parameter / filter tuning.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.backtest_regime_phase5 import replay_record, TradeRecord  # noqa: E402
from backtesting.backtest_regime_phase6 import get_records  # noqa: E402


# Production defaults — what the live bot would do today
SL = 0.30
TP = 0.50
TIME_STOP = 120


def trades_dataframe(records: list[TradeRecord]) -> pd.DataFrame:
    """Replay each record at production params, return one row per trade."""
    rows = []
    for rec in records:
        result = replay_record(rec, SL, TP, TIME_STOP)
        rows.append({
            "day": rec.day,
            "entry_ts": rec.entry_ts,
            "hour": rec.entry_ts.hour + rec.entry_ts.minute / 60,
            "hour_bucket": f"{rec.entry_ts.hour:02d}:" +
                           ("00" if rec.entry_ts.minute < 30 else "30"),
            "dow": rec.entry_ts.day_name(),
            "month": rec.entry_ts.strftime("%Y-%m"),
            "direction": rec.direction,
            "strike": rec.strike,
            "entry_premium": rec.entry_premium,
            "premium_bucket": _premium_bucket(rec.entry_premium),
            "regime": rec.regime_at_entry,
            "net_pnl": result["net_pnl"],
            "exit_reason": result["exit_reason"],
            "is_winner": result["net_pnl"] > 0,
        })
    return pd.DataFrame(rows)


def _premium_bucket(p: float) -> str:
    if p < 50:
        return "1: <50"
    if p < 100:
        return "2: 50-100"
    if p < 200:
        return "3: 100-200"
    if p < 400:
        return "4: 200-400"
    return "5: 400+"


def slice_summary(df: pd.DataFrame, by: str) -> pd.DataFrame:
    g = df.groupby(by, dropna=False)
    out = g.agg(
        trades=("net_pnl", "count"),
        wins=("is_winner", "sum"),
        net_pnl=("net_pnl", "sum"),
        avg_pnl=("net_pnl", "mean"),
    ).reset_index()
    out["win_rate_pct"] = (out["wins"] / out["trades"] * 100).round(1)
    return out.sort_values("net_pnl")


def emit_table(out: list[str], title: str, df: pd.DataFrame, by: str) -> None:
    out.append(f"### Sliced by {title}\n")
    out.append(f"| {by} | Trades | Wins | Win% | Net P&L | Avg P&L |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for _, r in df.iterrows():
        out.append(
            f"| {r[by]} | {int(r['trades'])} | {int(r['wins'])} "
            f"| {r['win_rate_pct']:.1f} | Rs {r['net_pnl']:,.0f} "
            f"| Rs {r['avg_pnl']:,.0f} |"
        )
    out.append("")


def find_skip_rules(df: pd.DataFrame) -> list[dict]:
    """
    Identify single-rule skip candidates: dimension/value pairs where
    skipping all trades in that bucket would improve the cumulative P&L.

    NOTE: only entry-time-knowable dimensions are valid filters. The
    `exit_reason` slice is informational only (data leakage if used
    as a skip filter), so it's excluded here.

    Returns list of {dimension, value, trades_skipped, pnl_skipped, after_pnl}.
    """
    total_pnl = df["net_pnl"].sum()
    candidates = []
    for dim in ["hour_bucket", "dow", "regime", "direction", "premium_bucket"]:
        for val, sub in df.groupby(dim, dropna=False):
            skipped_pnl = sub["net_pnl"].sum()
            if skipped_pnl >= 0:
                continue   # not a loser bucket — no benefit to skipping it
            after = total_pnl - skipped_pnl
            improvement = after - total_pnl
            candidates.append({
                "dimension": dim,
                "value": val,
                "trades_skipped": len(sub),
                "wins_skipped": int(sub["is_winner"].sum()),
                "win_rate_skipped": (sub["is_winner"].sum() / len(sub) * 100),
                "pnl_skipped": float(skipped_pnl),
                "pnl_after_skip": float(after),
                "improvement": float(improvement),
            })
    candidates.sort(key=lambda x: -x["improvement"])
    return candidates


def whatif_combined(df: pd.DataFrame, rules: list[tuple[str, str]]) -> dict:
    """Apply a list of (dimension, value) skip rules and return result."""
    mask = pd.Series(True, index=df.index)
    for dim, val in rules:
        mask &= df[dim] != val
    kept = df[mask]
    return {
        "rules_applied": rules,
        "trades_kept": len(kept),
        "trades_dropped": len(df) - len(kept),
        "net_pnl_after": float(kept["net_pnl"].sum()),
        "win_rate_after": float(kept["is_winner"].sum() / len(kept) * 100)
                          if len(kept) else 0.0,
    }


def run() -> None:
    records = get_records()
    df = trades_dataframe(records)
    print(f"Loaded {len(df)} trade records")

    base_pnl = df["net_pnl"].sum()
    base_wins = int(df["is_winner"].sum())
    print(f"\nBaseline (production params SL{int(SL*100)}/TP{int(TP*100)}/{TIME_STOP}m):")
    print(f"  trades={len(df)}  wins={base_wins}  "
          f"win_rate={base_wins/len(df)*100:.1f}%  "
          f"net_pnl=Rs {base_pnl:,.0f}")

    out = []
    out.append("# Phase 7 — Loser Analysis & Skip-Rule Discovery\n")
    out.append(f"Records: {len(df)}, "
               f"params SL {int(SL*100)}% / TP {int(TP*100)}% / {TIME_STOP}m\n")
    out.append(f"Baseline net P&L: **Rs {base_pnl:,.0f}**, "
               f"win rate {base_wins/len(df)*100:.1f}% "
               f"({base_wins}/{len(df)})\n")
    out.append("")
    out.append("## Slice Tables\n")

    for dim, label in [
        ("hour_bucket", "Entry time-of-day (30-min buckets)"),
        ("dow", "Day of week"),
        ("month", "Month"),
        ("direction", "Direction (CE vs PE)"),
        ("regime", "Regime at entry"),
        ("premium_bucket", "Entry premium size"),
        ("exit_reason", "Exit reason"),
    ]:
        emit_table(out, label, slice_summary(df, dim), dim)

    out.append("## Skip-Rule Candidates\n")
    out.append("Buckets where skipping all trades would improve cumulative P&L. "
               "Sorted by improvement.\n")
    out.append("| Dim | Value | Trades skipped | Wins | Win% skipped | "
               "P&L skipped | New P&L |")
    out.append("|---|---|---:|---:|---:|---:|---:|")
    candidates = find_skip_rules(df)
    for c in candidates[:20]:
        out.append(
            f"| {c['dimension']} | {c['value']} | {c['trades_skipped']} | "
            f"{c['wins_skipped']} | {c['win_rate_skipped']:.0f}% | "
            f"Rs {c['pnl_skipped']:,.0f} | Rs {c['pnl_after_skip']:,.0f} |"
        )
    out.append("")

    # ---- What-if combined: stack the top non-overlapping rules ----
    chosen: list[tuple[str, str]] = []
    used_dims: set[str] = set()
    for c in candidates:
        if c["dimension"] in used_dims:
            continue
        chosen.append((c["dimension"], c["value"]))
        used_dims.add(c["dimension"])
        if len(chosen) >= 4:
            break

    out.append("## What-If: Top 4 Non-Overlapping Skip Rules Combined\n")
    if not chosen:
        out.append("(No loser buckets found.)")
    else:
        whatif = whatif_combined(df, chosen)
        for dim, val in chosen:
            out.append(f"- Skip if `{dim} == {val}`")
        out.append("")
        out.append(f"- Trades kept: **{whatif['trades_kept']}** "
                   f"(dropped {whatif['trades_dropped']})")
        out.append(f"- Net P&L after rules: **Rs {whatif['net_pnl_after']:,.0f}** "
                   f"(was Rs {base_pnl:,.0f})")
        out.append(f"- Win rate after: {whatif['win_rate_after']:.1f}%")
        delta = whatif['net_pnl_after'] - base_pnl
        out.append(f"- Improvement: **Rs {delta:,.0f}**\n")

    # ---- Walk-forward sanity check on the combined rules ----
    # Sort by entry_ts; apply rules to first 50% (train) and second 50% (test)
    df_sorted = df.sort_values("entry_ts").reset_index(drop=True)
    cut = len(df_sorted) // 2
    train, test = df_sorted.iloc[:cut], df_sorted.iloc[cut:]

    out.append("## Out-Of-Sample Sanity (50/50 chronological split)\n")
    if chosen:
        # Re-derive rules on train only — would the same buckets pop out?
        train_candidates = find_skip_rules(train)
        train_chosen: list[tuple[str, str]] = []
        used_dims = set()
        for c in train_candidates:
            if c["dimension"] in used_dims:
                continue
            train_chosen.append((c["dimension"], c["value"]))
            used_dims.add(c["dimension"])
            if len(train_chosen) >= 4:
                break
        out.append("**Rules derived from TRAIN half:**")
        for dim, val in train_chosen:
            out.append(f"- `{dim} == {val}` -> skip")
        out.append("")

        train_with_rules = whatif_combined(train, train_chosen)
        test_with_rules = whatif_combined(test, train_chosen)
        out.append(f"- TRAIN  with these rules: Rs {train_with_rules['net_pnl_after']:,.0f} "
                   f"({train_with_rules['trades_kept']} trades, was Rs {train['net_pnl'].sum():,.0f})")
        out.append(f"- TEST   with these rules: Rs {test_with_rules['net_pnl_after']:,.0f} "
                   f"({test_with_rules['trades_kept']} trades, was Rs {test['net_pnl'].sum():,.0f})")
        train_delta = train_with_rules["net_pnl_after"] - train["net_pnl"].sum()
        test_delta = test_with_rules["net_pnl_after"] - test["net_pnl"].sum()
        out.append(f"- TRAIN improvement: Rs {train_delta:,.0f}")
        out.append(f"- TEST improvement:  Rs {test_delta:,.0f}\n")
        if test_delta > 0 and train_delta > 0:
            out.append("**Verdict:** rules improved P&L on both train AND test — "
                       "candidate edge worth pursuing.\n")
        elif test_delta > 0:
            out.append("**Verdict:** rules helped on test but the train picks weren't "
                       "needed there. Marginal — verify with more data.\n")
        elif train_delta > 0:
            out.append("**Verdict:** rules helped on train but FAILED on test. "
                       "Curve-fitting. Don't deploy these specific rules.\n")
        else:
            out.append("**Verdict:** rules failed in both periods. Strategy needs "
                       "structural change, not filtering.\n")

    report = ROOT / "reports" / "phase7_loser_analysis.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out))
    print(f"\nReport: {report.relative_to(ROOT)}")


if __name__ == "__main__":
    run()
