"""
Phase 6 — Walk-Forward Validation.

Question:
    Is the SL/TP/time-stop optimum found in Phase 5 (SL 30 / TP 60 /
    time 120) a robust signal, or curve-fitting on a single sample?

Method:
    1. Capture trade records once (cached to disk via pickle).
    2. Define multiple chronological train/test splits.
    3. For each split:
         a. Sweep all (sl, tp, time) combos on the TRAIN set only.
         b. Pick the params that maximise net P&L on TRAIN.
         c. Apply those params to the TEST set — that is the "honest"
            out-of-sample performance.
    4. Also evaluate fixed candidate params (production default,
       Phase-5 winner) on each TEST set so we can compare them.
    5. Report per-split + aggregate stability: how often did TP=60
       win on train, how did it perform on test, etc.

If TP=60 wins on most train sets AND keeps producing positive test
P&L, the parameter is real. If train winner is unstable across splits
or test P&L diverges from train P&L, the Phase 5 winner is over-
fitted and we should NOT update Options.json based on it.
"""
from __future__ import annotations

import pickle
import sys
from itertools import product
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.backtest_regime_phase1 import load_spot, load_vix  # noqa: E402
from backtesting.backtest_regime_phase3 import discover_expiries  # noqa: E402
from backtesting.backtest_regime_phase5 import (  # noqa: E402
    SL_GRID, TP_GRID, TIME_STOP_GRID,
    simulate_and_capture, replay_record, TradeRecord,
)


CACHE = ROOT / "reports" / "phase6_records_baseline.pkl"


# ---------------------------------------------------------------------------
# Records — capture once, cache to disk, reuse across runs
# ---------------------------------------------------------------------------

def get_records() -> list[TradeRecord]:
    if CACHE.exists():
        print(f"Loading cached records from {CACHE.relative_to(ROOT)}")
        with CACHE.open("rb") as fh:
            return pickle.load(fh)

    print("No cache — capturing trade paths (this will take ~15-20 min)...")
    spot_1m = load_spot()
    vix_1m = load_vix()
    expiries_by_date = discover_expiries(ROOT / "data")
    records = simulate_and_capture(
        spot_1m, vix_1m, expiries_by_date, regime_gated=False
    )
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("wb") as fh:
        pickle.dump(records, fh)
    print(f"Captured {len(records)} records, cached to "
          f"{CACHE.relative_to(ROOT)}")
    return records


# ---------------------------------------------------------------------------
# Sweep + evaluation helpers
# ---------------------------------------------------------------------------

def sweep_records(records: list[TradeRecord]) -> pd.DataFrame:
    rows = []
    for sl, tp, t in product(SL_GRID, TP_GRID, TIME_STOP_GRID):
        results = [replay_record(r, sl, tp, t) for r in records]
        df = pd.DataFrame(results)
        winners = df[df["net_pnl"] > 0]
        rows.append({
            "sl_pct": sl,
            "tp_pct": tp,
            "time_stop_min": t,
            "trades": len(df),
            "win_rate": len(winners) / len(df) * 100 if len(df) else 0,
            "net_pnl": float(df["net_pnl"].sum()),
        })
    return pd.DataFrame(rows)


def evaluate_fixed_combo(
    records: list[TradeRecord], sl: float, tp: float, t: int
) -> dict:
    if not records:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0}
    results = [replay_record(r, sl, tp, t) for r in records]
    df = pd.DataFrame(results)
    winners = df[df["net_pnl"] > 0]
    return {
        "trades": len(df),
        "net_pnl": float(df["net_pnl"].sum()),
        "win_rate": len(winners) / len(df) * 100 if len(df) else 0,
    }


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

def chronological_splits(records: list[TradeRecord]) -> list[tuple]:
    """Return list of (label, train_records, test_records)."""
    sorted_recs = sorted(records, key=lambda r: r.entry_ts)
    n = len(sorted_recs)

    splits = []

    # 50/50
    cut = n // 2
    splits.append(("50/50", sorted_recs[:cut], sorted_recs[cut:]))

    # 66 train / 33 test
    cut = (2 * n) // 3
    splits.append(("66/33", sorted_recs[:cut], sorted_recs[cut:]))

    # 33 train / 66 test (catches whether early-period params generalise)
    cut = n // 3
    splits.append(("33/66", sorted_recs[:cut], sorted_recs[cut:]))

    # Rolling 4-window walk-forward: train on each successive quarter,
    # test on the next. Captures regime drift.
    q = n // 4
    for i in range(3):
        train = sorted_recs[i * q:(i + 1) * q]
        test = sorted_recs[(i + 1) * q:(i + 2) * q]
        splits.append((f"rolling Q{i+1}->Q{i+2}", train, test))

    return splits


# ---------------------------------------------------------------------------
# Run + report
# ---------------------------------------------------------------------------

def run() -> None:
    records = get_records()
    print(f"\nTotal records: {len(records)}")
    if records:
        ts_first = sorted(records, key=lambda r: r.entry_ts)[0].entry_ts
        ts_last = sorted(records, key=lambda r: r.entry_ts)[-1].entry_ts
        print(f"Entry-ts range: {ts_first} -> {ts_last}")

    splits = chronological_splits(records)

    summary_rows = []
    for label, train, test in splits:
        if len(train) < 5 or len(test) < 5:
            print(f"Split {label}: too few records ({len(train)}/{len(test)}), skip")
            continue

        sweep_train = sweep_records(train)
        best = sweep_train.loc[sweep_train["net_pnl"].idxmax()]
        # Apply to test
        test_with_best = evaluate_fixed_combo(
            test, best.sl_pct, best.tp_pct, int(best.time_stop_min)
        )
        # Reference combos — production default and Phase-5 winner
        test_with_prod = evaluate_fixed_combo(test, 0.30, 0.50, 120)
        test_with_p5 = evaluate_fixed_combo(test, 0.30, 0.60, 120)
        train_with_prod = evaluate_fixed_combo(train, 0.30, 0.50, 120)
        train_with_p5 = evaluate_fixed_combo(train, 0.30, 0.60, 120)

        row = {
            "split": label,
            "train_n": len(train),
            "test_n": len(test),
            "best_train_sl": best.sl_pct,
            "best_train_tp": best.tp_pct,
            "best_train_time": int(best.time_stop_min),
            "train_pnl_best": float(best.net_pnl),
            "test_pnl_best": test_with_best["net_pnl"],
            "tp_60_won_train": bool(best.tp_pct == 0.60 and best.sl_pct == 0.30),
            "train_pnl_prod": train_with_prod["net_pnl"],
            "test_pnl_prod": test_with_prod["net_pnl"],
            "train_pnl_p5": train_with_p5["net_pnl"],
            "test_pnl_p5": test_with_p5["net_pnl"],
        }
        summary_rows.append(row)

    df = pd.DataFrame(summary_rows)

    # ---- Stdout ----
    print("\n" + "=" * 80)
    print("WALK-FORWARD SUMMARY")
    print("=" * 80)
    for _, r in df.iterrows():
        print(
            f"\n[{r['split']}]  train_n={r['train_n']}  test_n={r['test_n']}\n"
            f"  Best on train:  SL {r['best_train_sl']:.0%}  TP {r['best_train_tp']:.0%}  "
            f"time {r['best_train_time']}m  ->  train Rs {r['train_pnl_best']:,.0f}\n"
            f"  Apply best->test:                                 ->  test Rs {r['test_pnl_best']:,.0f}\n"
            f"  Prod default (SL30/TP50/120):  train Rs {r['train_pnl_prod']:,.0f}   test Rs {r['test_pnl_prod']:,.0f}\n"
            f"  Phase-5 winner (SL30/TP60/120): train Rs {r['train_pnl_p5']:,.0f}   test Rs {r['test_pnl_p5']:,.0f}"
        )

    # ---- Markdown report ----
    out = ROOT / "reports" / "phase6_walk_forward_report.md"
    lines = []
    lines.append("# Phase 6 — Walk-Forward Validation\n")
    lines.append(f"Records: {len(records)} captured trades, "
                 f"{ts_first} to {ts_last}\n")
    lines.append("Question answered: is Phase-5 winner (SL 30 / TP 60 / "
                 "time 120) robust across out-of-sample splits, or curve-fitted?\n")
    lines.append("")
    lines.append("## Per-Split Results\n")
    lines.append("| Split | Train n | Test n | Best Train Params | Train Rs | "
                 "Test Rs (best) | Prod-default Test Rs | P5-winner Test Rs |")
    lines.append("|---|---:|---:|---|---:|---:|---:|---:|")
    for _, r in df.iterrows():
        lines.append(
            f"| {r['split']} | {r['train_n']} | {r['test_n']} | "
            f"SL {r['best_train_sl']:.0%}/TP {r['best_train_tp']:.0%}/{r['best_train_time']}m | "
            f"{r['train_pnl_best']:,.0f} | {r['test_pnl_best']:,.0f} | "
            f"{r['test_pnl_prod']:,.0f} | {r['test_pnl_p5']:,.0f} |"
        )
    lines.append("")

    # Aggregate
    n_splits = len(df)
    p5_wins_train = df["tp_60_won_train"].sum()
    p5_test_pnl_total = df["test_pnl_p5"].sum()
    prod_test_pnl_total = df["test_pnl_prod"].sum()
    best_test_pnl_total = df["test_pnl_best"].sum()

    lines.append("## Aggregate Stability\n")
    lines.append(f"- Splits evaluated: **{n_splits}**")
    lines.append(f"- Splits where Phase-5 params (SL30/TP60/120) won the train sweep: "
                 f"**{int(p5_wins_train)}/{n_splits}**")
    lines.append(f"- Cumulative TEST P&L using each split's train winner: "
                 f"**Rs {best_test_pnl_total:,.0f}**")
    lines.append(f"- Cumulative TEST P&L using production default everywhere: "
                 f"**Rs {prod_test_pnl_total:,.0f}**")
    lines.append(f"- Cumulative TEST P&L using Phase-5 winner everywhere: "
                 f"**Rs {p5_test_pnl_total:,.0f}**\n")

    lines.append("## Verdict\n")
    if p5_wins_train >= n_splits * 0.66 and p5_test_pnl_total > prod_test_pnl_total:
        lines.append("**Phase-5 winner appears ROBUST** — TP 60% won the train "
                     "sweep on a majority of splits AND its cumulative test P&L "
                     "beats the production default. Recommend updating "
                     "`Options.json` to TP=60.\n")
    elif p5_test_pnl_total > prod_test_pnl_total:
        lines.append("**Phase-5 winner is BETTER THAN PRODUCTION but NOT STABLE** "
                     "— TP 60% wins on cumulative test P&L but didn't dominate "
                     "the train sweeps. There may be a better param. Investigate "
                     "before deploying.\n")
    else:
        lines.append("**Phase-5 winner FAILED out-of-sample** — TP 60% does not "
                     "consistently win on test data. The Phase 5 result was likely "
                     "curve-fitted to the full-year sample. **Do NOT update "
                     "Options.json based on this evidence alone.** More data "
                     "or a different optimisation target is required.\n")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"\nReport: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    run()
