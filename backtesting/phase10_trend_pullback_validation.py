"""
Phase 10 — Trend Pullback Walk-Forward Validation.

Phase 9 found Trend Pullback profitable on the 2-year sample (+Rs 9,939
across 32 trades, PF 1.64). This phase asks: is that result robust, or
period-specific?

Tests:
    1. Chronological splits — does the strategy profit in BOTH halves?
    2. Quarterly performance — is profitability month-to-month consistent
       or driven by one anomalous quarter?
    3. Direction split — do CE and PE trades both make money?
    4. Drawdown timeline — what's the deepest equity dip?
    5. Hour-of-day distribution — is the strategy concentrated in a
       single time window we should be cautious about?

If the strategy passes all five, it's robust enough to advance to
paper-trading. If it fails any, document where the fragility is.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


PICKLE = ROOT / "reports" / "phase9_tactic_trades.pkl"
REPORT = ROOT / "reports" / "phase10_trend_pullback_validation.md"


def load_trades() -> list:
    if not PICKLE.exists():
        raise SystemExit(
            f"Cache not found: {PICKLE}\n"
            "Re-run backtesting/run_all_tactics.py first."
        )
    with PICKLE.open("rb") as fh:
        all_trades = pickle.load(fh)
    return all_trades.get("trend_pullback", [])


def to_df(trades) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        rows.append({
            "day": t.day,
            "entry_ts": t.entry_ts,
            "direction": t.direction,
            "strike": t.strike,
            "regime_at_entry": t.regime_at_entry,
            "entry_premium": t.entry_premium,
            "exit_premium": t.exit_premium,
            "exit_reason": t.exit_reason,
            "net_pnl": t.net_pnl,
            "is_winner": t.net_pnl > 0,
        })
    df = pd.DataFrame(rows).sort_values("entry_ts").reset_index(drop=True)
    df["month"] = df["entry_ts"].dt.strftime("%Y-%m")
    df["dow"] = df["entry_ts"].dt.day_name()
    df["hour_bucket"] = df["entry_ts"].dt.strftime("%H:") + df["entry_ts"].dt.minute.apply(
        lambda m: "00" if m < 30 else "30"
    )
    return df


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "pf": float("inf"),
                "wins": 0, "losses": 0, "avg_win": 0.0, "avg_loss": 0.0,
                "max_dd": 0.0}
    winners = df[df["net_pnl"] > 0]
    losers = df[df["net_pnl"] <= 0]
    gp = winners["net_pnl"].sum() if len(winners) else 0.0
    gl = abs(losers["net_pnl"].sum()) if len(losers) else 0.0
    cum = df["net_pnl"].cumsum()
    max_dd = (cum.cummax() - cum).max() if len(df) else 0.0
    return {
        "trades": len(df),
        "wins": int(len(winners)),
        "losses": int(len(losers)),
        "win_rate": len(winners) / len(df) * 100,
        "net_pnl": float(df["net_pnl"].sum()),
        "avg_win": float(winners["net_pnl"].mean()) if len(winners) else 0.0,
        "avg_loss": float(losers["net_pnl"].mean()) if len(losers) else 0.0,
        "pf": (gp / gl) if gl > 0 else float("inf"),
        "max_dd": float(max_dd),
    }


def fmt_summary_row(label: str, s: dict) -> str:
    pf = s["pf"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
    return (f"| {label} | {s['trades']} | {s.get('wins', 0)} "
            f"| {s['win_rate']:.1f} | Rs {s['net_pnl']:,.0f} "
            f"| {pf_str} | Rs {s.get('max_dd', 0):,.0f} |")


def main():
    trades = load_trades()
    df = to_df(trades)
    if df.empty:
        raise SystemExit("No trend_pullback trades in cache.")

    base = summarize(df)
    out = []
    out.append("# Phase 10 — Trend Pullback Walk-Forward Validation\n")
    out.append(f"Total trades: {len(df)}")
    out.append(f"Date range: {df['entry_ts'].min()} -> {df['entry_ts'].max()}\n")

    out.append("## Baseline (full 2-year sample)\n")
    out.append("| Metric | Value |")
    out.append("|---|---:|")
    out.append(f"| Trades | {base['trades']} |")
    out.append(f"| Win rate | {base['win_rate']:.1f}% |")
    out.append(f"| Net P&L | Rs {base['net_pnl']:,.0f} |")
    out.append(f"| Profit factor | {base['pf']:.2f} |")
    out.append(f"| Max drawdown | Rs {base['max_dd']:,.0f} |")
    out.append(f"| Avg win | Rs {base['avg_win']:,.0f} |")
    out.append(f"| Avg loss | Rs {base['avg_loss']:,.0f} |")
    out.append("")

    # Chronological splits
    out.append("## Test 1 — Chronological Splits\n")
    out.append("Does the strategy profit in BOTH halves of the data?")
    out.append("If only one half profits, the result is period-specific.\n")
    out.append("| Split | Half | Trades | Wins | Win% | Net P&L | PF | Max DD |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")

    n = len(df)
    splits = {
        "50/50": n // 2,
        "66/33": (2 * n) // 3,
        "33/66": n // 3,
    }
    splits_pass = []
    for label, cut in splits.items():
        train = df.iloc[:cut]
        test = df.iloc[cut:]
        s_train = summarize(train)
        s_test = summarize(test)
        out.append(fmt_summary_row(f"{label} TRAIN", s_train))
        out.append(fmt_summary_row(f"{label} TEST", s_test))
        splits_pass.append(s_train["net_pnl"] > 0 and s_test["net_pnl"] > 0)

    out.append("")
    pass_count = sum(splits_pass)
    out.append(f"**Splits where BOTH halves are profitable: {pass_count}/3**\n")

    # Quarterly stability
    out.append("## Test 2 — Quarterly Stability\n")
    out.append("How many calendar quarters were profitable?\n")
    out.append("| Quarter | Trades | Net P&L |")
    out.append("|---|---:|---:|")
    df["quarter"] = (
        df["entry_ts"].dt.year.astype(str)
        + "-Q" + ((df["entry_ts"].dt.month - 1) // 3 + 1).astype(str)
    )
    quarter_groups = df.groupby("quarter").agg(
        trades=("net_pnl", "count"),
        net_pnl=("net_pnl", "sum"),
    ).reset_index()
    profitable_q = 0
    for _, r in quarter_groups.iterrows():
        out.append(f"| {r['quarter']} | {int(r['trades'])} | Rs {r['net_pnl']:,.0f} |")
        if r["net_pnl"] > 0:
            profitable_q += 1
    out.append("")
    out.append(f"**Profitable quarters: {profitable_q} of {len(quarter_groups)}**\n")

    # Direction split
    out.append("## Test 3 — Direction Performance (CE vs PE)\n")
    out.append("Does ONE direction carry the strategy or are both contributing?\n")
    out.append("| Direction | Trades | Wins | Win% | Net P&L | PF |")
    out.append("|---|---:|---:|---:|---:|---:|")
    dir_pass = 0
    for d in ["CE", "PE"]:
        sub = df[df["direction"] == d]
        s = summarize(sub)
        pf = s["pf"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        out.append(f"| {d} | {s['trades']} | {s.get('wins', 0)} "
                   f"| {s['win_rate']:.1f} | Rs {s['net_pnl']:,.0f} | {pf_str} |")
        if s["net_pnl"] > 0:
            dir_pass += 1
    out.append("")
    out.append(f"**Profitable directions: {dir_pass}/2**\n")

    # Regime breakdown
    out.append("## Test 4 — Regime At Entry\n")
    out.append("| Regime | Trades | Wins | Win% | Net P&L |")
    out.append("|---|---:|---:|---:|---:|")
    for reg, sub in df.groupby("regime_at_entry"):
        s = summarize(sub)
        out.append(f"| {reg} | {s['trades']} | {s.get('wins', 0)} "
                   f"| {s['win_rate']:.1f} | Rs {s['net_pnl']:,.0f} |")
    out.append("")

    # Drawdown timeline
    out.append("## Test 5 — Equity Curve\n")
    cum = df["net_pnl"].cumsum()
    peak = cum.cummax()
    dd = peak - cum
    max_dd_idx = dd.idxmax() if len(dd) else None
    out.append(f"- Peak equity at trade #{peak.idxmax()+1}: Rs {peak.max():,.0f}")
    if max_dd_idx is not None:
        out.append(f"- Trough: Rs {cum[max_dd_idx]:,.0f} at trade #{max_dd_idx+1}")
        out.append(f"- Max drawdown depth: Rs {dd.max():,.0f}")
    out.append(f"- Final equity: Rs {cum.iloc[-1]:,.0f}")
    out.append("")

    # Hour-of-day distribution
    out.append("## Test 6 — Hour-Of-Day Distribution\n")
    out.append("Is the strategy concentrated at one entry window?\n")
    out.append("| Hour bucket | Trades | Wins | Win% | Net P&L |")
    out.append("|---|---:|---:|---:|---:|")
    for hb, sub in df.groupby("hour_bucket"):
        s = summarize(sub)
        out.append(f"| {hb} | {s['trades']} | {s.get('wins', 0)} "
                   f"| {s['win_rate']:.1f} | Rs {s['net_pnl']:,.0f} |")
    out.append("")

    # Verdict
    out.append("## Verdict\n")
    rules = [
        ("Both halves profitable on at least one split",
         pass_count >= 1),
        ("Both halves profitable on the 50/50 split (strict)",
         splits_pass[0]),
        ("Profitable quarters >= 60%",
         profitable_q / len(quarter_groups) >= 0.6 if len(quarter_groups) else False),
        ("Both CE and PE profitable",
         dir_pass == 2),
        ("Profit factor >= 1.30",
         base["pf"] >= 1.30),
        ("Max drawdown <= net P&L",
         base["max_dd"] <= base["net_pnl"]),
    ]

    out.append("| Robustness Check | Result |")
    out.append("|---|:---:|")
    pass_total = 0
    for desc, ok in rules:
        out.append(f"| {desc} | {'PASS' if ok else 'FAIL'} |")
        if ok:
            pass_total += 1
    out.append("")
    out.append(f"**Score: {pass_total} / {len(rules)} robustness checks passed**\n")

    if pass_total >= 5:
        out.append("**Verdict: ROBUST.** Trend Pullback shows consistent edge across "
                   "multiple validation slices. Move forward to paper-trading.\n")
    elif pass_total >= 3:
        out.append("**Verdict: PROMISING BUT FRAGILE.** Edge exists but is concentrated "
                   "in specific sub-periods or directions. Investigate the failure modes "
                   "before trusting the result.\n")
    else:
        out.append("**Verdict: NOT ROBUST.** The Phase 9 result was likely period-"
                   "specific. Do not deploy.\n")

    REPORT.write_text("\n".join(out))
    print(f"\nReport: {REPORT.relative_to(ROOT)}")
    print(f"Robustness score: {pass_total}/{len(rules)}")
    print(f"Net P&L: Rs {base['net_pnl']:,.0f}")


if __name__ == "__main__":
    main()
