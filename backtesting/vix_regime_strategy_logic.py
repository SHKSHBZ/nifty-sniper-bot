"""Verify the user's hypothesis and find the strategy logic.

Hypothesis under test:
  H1: VIX high (>18) -> NIFTY goes DOWN
  H2: VIX low (<12)  -> NIFTY goes UP
  H3: VIX neutral (12-15) -> NIFTY range-bound, no big moves

Then for each regime show how CE and PE premiums actually behaved:
  - Which side made money (CE buyer or PE buyer)?
  - Did intraday CE peaks beat PE peaks or vice versa?
  - What was the typical premium decay vs spike pattern?

Builds the case for a directional CE/PE selection rule.
Reads existing reports/vix_nifty_daily.csv and
reports/premium_vix_dte_daily.csv.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


def main():
    daily = pd.read_csv(REPORTS / "vix_nifty_daily.csv")
    prem = pd.read_csv(REPORTS / "premium_vix_dte_daily.csv")

    # Merge so each row has both spot direction and premium behaviour
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    prem["trade_date"] = pd.to_datetime(prem["trade_date"]).dt.date
    df = prem.merge(daily, left_on="trade_date", right_on="date",
                    how="left", suffixes=("", "_d"))
    print(f"Joined dataset: {len(df)} days\n")

    # ---------------- HYPOTHESIS CHECKS ----------------
    print("=" * 70)
    print("HYPOTHESIS CHECK")
    print("=" * 70)

    h_table = daily.groupby("vix_regime").agg(
        n_days=("date", "count"),
        avg_vix=("vix_close", "mean"),
        pct_up_days=("nifty_pct_chg", lambda x: (x > 0.1).mean() * 100),
        pct_down_days=("nifty_pct_chg", lambda x: (x < -0.1).mean() * 100),
        pct_flat_days=("nifty_pct_chg",
                       lambda x: (x.abs() <= 0.1).mean() * 100),
        avg_pct_chg=("nifty_pct_chg", "mean"),
        median_pct_chg=("nifty_pct_chg", "median"),
        avg_range=("nifty_range", "mean"),
    ).round(2)

    print("\n>>> Does VIX level predict NIFTY direction? <<<\n")
    print(h_table.to_string())

    print("\n--- Verdict on hypotheses ---")
    high = daily[daily["vix_regime"] == "High"]
    low = daily[daily["vix_regime"] == "Low"]
    normal = daily[daily["vix_regime"] == "Normal"]
    elevated = daily[daily["vix_regime"] == "Elevated"]

    print(f"H1 (VIX HIGH -> down): "
          f"{(high['nifty_pct_chg'] < -0.1).sum()}/{len(high)} = "
          f"{(high['nifty_pct_chg'] < -0.1).mean()*100:.0f}% of high-VIX "
          f"days went DOWN (avg {high['nifty_pct_chg'].mean():.2f}%)")
    print(f"H2 (VIX LOW -> up): "
          f"{(low['nifty_pct_chg'] > 0.1).sum()}/{len(low)} = "
          f"{(low['nifty_pct_chg'] > 0.1).mean()*100:.0f}% of low-VIX "
          f"days went UP (avg {low['nifty_pct_chg'].mean():.2f}%)")
    print(f"H3 (VIX NEUTRAL -> small range): "
          f"normal-VIX avg range = {normal['nifty_range'].mean():.0f} pts "
          f"(vs {high['nifty_range'].mean():.0f} for High, "
          f"{low['nifty_range'].mean():.0f} for Low)")

    # ---------------- CE/PE PERFORMANCE BY REGIME ----------------
    print("\n" + "=" * 70)
    print("CE vs PE PERFORMANCE BY VIX REGIME")
    print("=" * 70)
    # We already have straddle peaks; need separately per leg.
    # Recompute from daily premium file
    df["ce_peak_uplift"] = df.apply(lambda r:
        ((r["straddle_peak"] - r["pe_at_0930"]) - r["ce_at_0930"])
        / max(r["ce_at_0930"], 0.01) * 100, axis=1)
    # Actually we don't have per-leg peaks recorded; use straddle peak
    # plus indication of which side dominated (from spot direction)

    # Simpler: classify which leg "won" based on direction of spot move
    df["winning_leg"] = df.apply(lambda r:
        "CE" if r.get("spot_move", 0) > 5 else
        ("PE" if r.get("spot_move", 0) < -5 else "FLAT"), axis=1)

    leg_by_regime = df.groupby(["vix_regime", "winning_leg"]).size().unstack(
        fill_value=0
    )
    leg_by_regime["total"] = leg_by_regime.sum(axis=1)
    if "CE" in leg_by_regime.columns:
        leg_by_regime["CE_pct"] = (leg_by_regime["CE"] /
                                   leg_by_regime["total"] * 100).round(1)
    if "PE" in leg_by_regime.columns:
        leg_by_regime["PE_pct"] = (leg_by_regime["PE"] /
                                   leg_by_regime["total"] * 100).round(1)

    print("\n>>> Which leg paid off, by VIX regime? <<<")
    print("(winning_leg = CE if spot moved +5pts net, PE if -5pts, FLAT otherwise)")
    print()
    print(leg_by_regime.to_string())

    # ---------------- VIX CHANGE direction matters more? ----------------
    print("\n" + "=" * 70)
    print("VIX CHANGE direction vs NIFTY direction (intraday)")
    print("=" * 70)
    daily["vix_dir"] = daily["vix_pct_chg"].apply(
        lambda x: "VIX_UP" if x > 1 else
                  ("VIX_DOWN" if x < -1 else "VIX_FLAT")
    )
    daily["nifty_dir"] = daily["nifty_pct_chg"].apply(
        lambda x: "UP" if x > 0.1 else ("DOWN" if x < -0.1 else "FLAT")
    )
    cross = pd.crosstab(daily["vix_dir"], daily["nifty_dir"], margins=True)
    print("\n>>> Cross-tab: same-day VIX direction vs NIFTY direction <<<")
    print(cross.to_string())

    # When VIX rises >1%, what % of those days did NIFTY actually drop?
    vix_up = daily[daily["vix_dir"] == "VIX_UP"]
    vix_down = daily[daily["vix_dir"] == "VIX_DOWN"]
    print(f"\nVIX UP days (n={len(vix_up)}): "
          f"NIFTY DOWN {(vix_up['nifty_pct_chg'] < -0.1).mean()*100:.0f}%, "
          f"NIFTY UP {(vix_up['nifty_pct_chg'] > 0.1).mean()*100:.0f}%")
    print(f"VIX DOWN days (n={len(vix_down)}): "
          f"NIFTY UP {(vix_down['nifty_pct_chg'] > 0.1).mean()*100:.0f}%, "
          f"NIFTY DOWN {(vix_down['nifty_pct_chg'] < -0.1).mean()*100:.0f}%")

    # ---------------- COMBINED REGIME + VIX MOVE STRATEGY ----------------
    print("\n" + "=" * 70)
    print("STRATEGY SIGNAL TABLE")
    print("=" * 70)
    daily["combined"] = daily["vix_regime"] + "_" + daily["vix_dir"]
    sig = daily.groupby("combined").agg(
        n_days=("date", "count"),
        pct_up=("nifty_pct_chg", lambda x: (x > 0.1).mean() * 100),
        pct_down=("nifty_pct_chg", lambda x: (x < -0.1).mean() * 100),
        avg_chg=("nifty_pct_chg", "mean"),
        avg_range=("nifty_range", "mean"),
    ).round(2).sort_values("avg_chg")
    print("\n>>> Combined regime + VIX-move signal (sorted by NIFTY avg %chg) <<<")
    print(sig.to_string())

    # Save markdown
    md = ["# VIX Regime Strategy Logic\n",
          "## Hypothesis check\n",
          "```", h_table.to_string(), "```", "",
          "## Which leg won by regime\n",
          "```", leg_by_regime.to_string(), "```", "",
          "## VIX direction vs NIFTY direction (same-day)\n",
          "```", cross.to_string(), "```", "",
          "## Combined signal table\n",
          "```", sig.to_string(), "```", ""]
    out = REPORTS / "vix_strategy_logic.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
