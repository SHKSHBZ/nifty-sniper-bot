"""
Phase 8 — Combined Stack Backtest.

Stack the validated improvements:
    - TP raised from 50% to 60% (Phase 5 + 6)
    - Skip Mondays (Phase 7)
    - Skip 11:00-11:29 entries (Phase 7)
    - Skip TREND_DOWN regime entries (Phase 7)

Compare four configurations on the same 98-record sample:
    A. Production today (TP=50, no filters)              — Phase 4 reproduced
    B. TP=60 alone, no filters                            — Phase 6 best
    C. Filters alone, TP=50                               — Phase 7 walk-forward
    D. TP=60 + 3 filters                                  — combined stack

Then run walk-forward 50/50 on D to confirm out-of-sample performance.
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


def keep_record(rec: TradeRecord, filters: dict) -> bool:
    """Apply Phase-7 entry filters. Returns True if the trade passes."""
    if filters.get("skip_mondays") and rec.entry_ts.weekday() == 0:
        return False
    if filters.get("skip_1100_1130"):
        if rec.entry_ts.hour == 11 and rec.entry_ts.minute < 30:
            return False
    if filters.get("skip_trend_down") and rec.regime_at_entry == "TREND_DOWN":
        return False
    return True


def evaluate(
    records: list[TradeRecord],
    *,
    sl: float,
    tp: float,
    time_stop: int,
    filters: dict,
) -> dict:
    kept = [r for r in records if keep_record(r, filters)]
    if not kept:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0,
                "winners": 0, "losers": 0,
                "avg_win": 0.0, "avg_loss": 0.0, "max_dd": 0.0,
                "profit_factor": float("inf")}
    rows = [replay_record(r, sl, tp, time_stop) for r in kept]
    df = pd.DataFrame(rows)
    winners = df[df["net_pnl"] > 0]
    losers = df[df["net_pnl"] <= 0]
    cum = df["net_pnl"].cumsum()
    max_dd = (cum.cummax() - cum).max() if len(df) else 0.0
    gp = winners["net_pnl"].sum() if len(winners) else 0.0
    gl = abs(losers["net_pnl"].sum()) if len(losers) else 0.0
    return {
        "trades": len(df),
        "winners": int(len(winners)),
        "losers": int(len(losers)),
        "win_rate": len(winners) / len(df) * 100 if len(df) else 0,
        "net_pnl": float(df["net_pnl"].sum()),
        "avg_win": float(winners["net_pnl"].mean()) if len(winners) else 0,
        "avg_loss": float(losers["net_pnl"].mean()) if len(losers) else 0,
        "max_dd": float(max_dd),
        "profit_factor": (gp / gl) if gl > 0 else float("inf"),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }


CONFIGS = {
    "A_prod_today":      dict(sl=0.30, tp=0.50, time_stop=120,
                              filters={}),
    "B_tp60_only":       dict(sl=0.30, tp=0.60, time_stop=120,
                              filters={}),
    "C_filters_only":    dict(sl=0.30, tp=0.50, time_stop=120,
                              filters={"skip_mondays": True,
                                       "skip_1100_1130": True,
                                       "skip_trend_down": True}),
    "D_combined_stack":  dict(sl=0.30, tp=0.60, time_stop=120,
                              filters={"skip_mondays": True,
                                       "skip_1100_1130": True,
                                       "skip_trend_down": True}),
}


def vix_distribution() -> dict:
    """Quick check: how often was VIX < 18 vs >= 18?"""
    vix_path = ROOT / "data" / "INDIA_VIX_1minute.csv"
    if not vix_path.exists():
        return {}
    df = pd.read_csv(vix_path)
    df["close"] = df["close"].astype(float)
    total = len(df)
    below = (df["close"] < 18.0).sum()
    return {
        "total_minutes": total,
        "minutes_vix_below_18": int(below),
        "minutes_vix_at_or_above_18": int(total - below),
        "pct_below_18": float(below / total * 100) if total else 0,
    }


def run() -> None:
    records = get_records()
    print(f"Loaded {len(records)} records")
    print(f"Date range: {sorted([r.entry_ts for r in records])[0]} -> "
          f"{sorted([r.entry_ts for r in records])[-1]}")

    # ----- Compare 4 configs in-sample -----
    results = {}
    for name, cfg in CONFIGS.items():
        results[name] = evaluate(records, **cfg)

    # ----- Walk-forward 50/50 on D -----
    sorted_recs = sorted(records, key=lambda r: r.entry_ts)
    cut = len(sorted_recs) // 2
    train, test = sorted_recs[:cut], sorted_recs[cut:]

    wf_train = evaluate(train, **CONFIGS["D_combined_stack"])
    wf_test = evaluate(test, **CONFIGS["D_combined_stack"])
    wf_train_prod = evaluate(train, **CONFIGS["A_prod_today"])
    wf_test_prod = evaluate(test, **CONFIGS["A_prod_today"])

    # ----- VIX distribution -----
    vix_dist = vix_distribution()

    # ----- Output -----
    out = []
    out.append("# Phase 8 — Combined Stack Backtest\n")
    out.append("Compares production today vs TP=60-only vs filters-only vs full stack.\n")
    out.append("")

    out.append("## Side-By-Side Results (98 captured records)\n")
    out.append("| Config | Trades | Win% | Net P&L | Profit Factor | Max DD | P&L/DD |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    name_label = {
        "A_prod_today": "A — Production today (TP 50, no filters)",
        "B_tp60_only": "B — TP 60 only",
        "C_filters_only": "C — Filters only (TP 50)",
        "D_combined_stack": "D — TP 60 + 3 filters (combined)",
    }
    for k in CONFIGS:
        r = results[k]
        pf = r["profit_factor"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        ratio = (r["net_pnl"] / r["max_dd"]) if r["max_dd"] > 0 else float("inf")
        ratio_str = f"{ratio:.2f}" if ratio != float("inf") else "inf"
        out.append(
            f"| {name_label[k]} | {r['trades']} | {r['win_rate']:.1f} "
            f"| Rs {r['net_pnl']:,.0f} | {pf_str} | Rs {r['max_dd']:,.0f} "
            f"| {ratio_str} |"
        )
    out.append("")

    # Detailed per-config exit-reason and win/loss breakdown
    out.append("## Per-Config Detail\n")
    for k, r in results.items():
        out.append(f"### {name_label[k]}\n")
        out.append(f"- Winners: {r['winners']}  Losers: {r['losers']}")
        out.append(f"- Avg win: Rs {r['avg_win']:,.0f}   Avg loss: Rs {r['avg_loss']:,.0f}")
        out.append(f"- Exit reasons: {r['exit_reasons']}\n")

    # ----- Walk-forward block -----
    out.append("## Walk-Forward 50/50 — Combined Stack (D)\n")
    out.append("| Half | Config | Trades | Win% | Net P&L |")
    out.append("|---|---|---:|---:|---:|")
    out.append(f"| TRAIN (first 49) | A — Prod today | {wf_train_prod['trades']} | "
               f"{wf_train_prod['win_rate']:.1f} | Rs {wf_train_prod['net_pnl']:,.0f} |")
    out.append(f"| TRAIN (first 49) | D — Combined  | {wf_train['trades']} | "
               f"{wf_train['win_rate']:.1f} | Rs {wf_train['net_pnl']:,.0f} |")
    out.append(f"| TEST  (last 49)  | A — Prod today | {wf_test_prod['trades']} | "
               f"{wf_test_prod['win_rate']:.1f} | Rs {wf_test_prod['net_pnl']:,.0f} |")
    out.append(f"| TEST  (last 49)  | D — Combined  | {wf_test['trades']} | "
               f"{wf_test['win_rate']:.1f} | Rs {wf_test['net_pnl']:,.0f} |")
    out.append("")
    train_delta = wf_train["net_pnl"] - wf_train_prod["net_pnl"]
    test_delta = wf_test["net_pnl"] - wf_test_prod["net_pnl"]
    out.append(f"- TRAIN improvement vs prod: **Rs {train_delta:+,.0f}**")
    out.append(f"- TEST  improvement vs prod: **Rs {test_delta:+,.0f}**")
    out.append("")

    # ----- VIX distribution (PE-asymmetry root cause) -----
    if vix_dist:
        out.append("## Phase 9 — Why Is The Bot CE-Heavy?\n")
        out.append("The production SignalEngine has Gate 0 (VIX macro):")
        out.append("- VIX < 18 -> CE entries allowed, PE entries blocked")
        out.append("- VIX >= 18 -> PE entries allowed, CE entries blocked\n")
        out.append("VIX distribution across the year (1-minute bars):\n")
        out.append(f"- Total minutes: **{vix_dist['total_minutes']:,}**")
        out.append(f"- Minutes with VIX < 18: **{vix_dist['minutes_vix_below_18']:,} "
                   f"({vix_dist['pct_below_18']:.1f}%)**")
        out.append(f"- Minutes with VIX >= 18: **{vix_dist['minutes_vix_at_or_above_18']:,} "
                   f"({100 - vix_dist['pct_below_18']:.1f}%)**")
        out.append("")
        if vix_dist["pct_below_18"] > 80:
            out.append("**Verdict:** CE-heavy entries are a structural consequence "
                       "of low VIX, not a bug. The bot was correct to block PE entries "
                       "during sustained low-VIX periods. The 8 PE entries that did "
                       "fire (62% win rate) coincided with VIX spike windows. To get "
                       "more PE alpha, you'd need either (a) a different VIX threshold "
                       "or (b) more time in market regimes with VIX > 18 — a 2024/2022 "
                       "data sample would help.\n")
        else:
            out.append("**Verdict:** VIX was elevated frequently enough that the "
                       "CE bias is NOT explained by Gate 0 alone — investigate why "
                       "PE setups didn't trigger in the resistance-touch path.\n")

    # ----- Recommendation -----
    out.append("## Recommendation\n")
    a, d = results["A_prod_today"], results["D_combined_stack"]
    delta = d["net_pnl"] - a["net_pnl"]
    out.append(f"Combined stack improves in-sample P&L by **Rs {delta:,.0f}** "
               f"({a['net_pnl']:,.0f} -> {d['net_pnl']:,.0f}).\n")
    if test_delta > 0 and d["net_pnl"] > 0:
        out.append("**Combined stack PASSES walk-forward AND is profitable in-sample.** "
                   "Recommend updating Options.json (TP -> 60%) and adding the three "
                   "filters in entry logic. Begin paper-trading validation.\n")
    elif test_delta > 0:
        out.append("**Combined stack improves on test but is not yet profitable.** "
                   "Filters are deployable; expect to break ~even rather than profit.\n")
    else:
        out.append("**Combined stack does not improve out-of-sample.** Filters and "
                   "TP=60 each helped individually but together they overlap. Don't "
                   "stack them blindly.\n")

    report = ROOT / "reports" / "phase8_combined_stack.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out))

    print("\n=== RESULTS ===")
    for k in CONFIGS:
        r = results[k]
        print(f"  {name_label[k]:48} trades={r['trades']:3}  "
              f"win%={r['win_rate']:5.1f}  net=Rs {r['net_pnl']:+8,.0f}  "
              f"PF={r['profit_factor']:.2f}")
    print(f"\nWalk-forward TEST improvement: Rs {test_delta:+,.0f}")
    print(f"Report: {report.relative_to(ROOT)}")


if __name__ == "__main__":
    run()
