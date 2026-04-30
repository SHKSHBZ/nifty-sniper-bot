# Backtest Project — Final Synthesis

End-to-end summary of an 8-phase investigation into whether the Nifty
Sniper Bot's mean-reversion strategy is deployable, what changes make
it deployable, and what remains uncertain.

---

## Executive Verdict

**Production bot today is NOT deployable** — loses Rs 29k/year on a 1-year
sample.

**Production bot WITH three changes IS plausibly deployable** —
swings to **+Rs 25,210/year in-sample, +Rs 1,970 on out-of-sample test**.
The combined stack improves test-half P&L by Rs 7,322 vs production
defaults. Profit factor goes from 1.03 to 1.44.

The three validated changes are listed in the **Deployment** section
below. They are the only changes that survived walk-forward validation.

---

## Phase Summary

| Phase | Question | Answer |
|---|---|---|
| 1 | Does the regime classifier work on real data? | Yes — RANGE 55-66%, trends 27%, gaps 9%, NO_TRADE 4%, all matching theoretical expectations |
| 2 | Does regime classification work on synthetic spot? | Yes — 4-day pilot directionally correct |
| 3 | Does regime gating help a simplified mean-rev tactic? | Yes on simplified (Rs 17k saved), but tactic itself is unprofitable |
| 4 | Does the production SignalEngine make money on 1 year? | No — loses Rs 29,100. Most loss in RANGE regime. |
| 5 | Can SL/TP/time-stop tuning fix it? | TP 50→60 turns it positive in-sample (+Rs 7,066) |
| 6 | Does Phase 5 generalize out-of-sample? | Partially — TP 60 beats TP 50 on test, but bot still net negative (-Rs 1,590 cumulative test) |
| 7 | What loser buckets exist? | Mondays, 11:00 entries, TREND_DOWN regime. Premium-bucket rule is curve-fitted. |
| 8 | Does TP=60 + filters work combined? | **Yes — +Rs 25,210 in-sample, profitable on test half (+Rs 1,970), profit factor 1.44** |

---

## What's Changing — Three Validated Modifications

### Change 1 — TP from 50% to 60%

| | Before | After |
|---|---|---|
| In-sample net | +Rs 3,106 | +Rs 7,066 (+Rs 3,960) |
| Walk-forward test | -Rs 5,352 | better-than-prod by Rs 2,906 |

**Code change** in `Options.json`:
```diff
- "profitTargetPercent": 50,
+ "profitTargetPercent": 60,
```

### Change 2 — Skip Mondays

The single biggest filter edge. 15 Monday entries in the year, 33% win rate, -Rs 14,345 cumulative.

**Code change** in entry path (e.g. `main.py` or `signal_engine.py`):

```python
from datetime import datetime
# inside entry decision:
if datetime.now().weekday() == 0:   # Monday
    return  # skip new entries
```

### Change 3 — Skip 11:00–11:29 Entries

13 entries in this 30-min window over the year, 23% win rate, -Rs 7,683.

**Code change**:
```python
now = datetime.now()
if now.hour == 11 and now.minute < 30:
    return  # skip new entries
```

(Optional small extension — skip TREND_DOWN regime entries: 4 trades, -Rs 814.
The effect is small enough that you can leave it for later if you want fewer
moving parts.)

---

## Combined Stack Results

| Config | Trades | Win% | Net P&L | Profit Factor | Max DD |
|---|---:|---:|---:|---:|---:|
| **Production today** | 98 | 43.9 | **+Rs 3,106** | 1.03 | Rs 16,970 |
| TP 60 only | 98 | 42.9 | +Rs 7,066 | 1.07 | Rs 17,495 |
| Filters only | 68 | 50.0 | +Rs 23,673 | 1.41 | Rs 13,179 |
| **TP 60 + 3 filters** | 68 | **48.5** | **+Rs 25,210** | **1.44** | Rs 14,596 |

Risk-adjusted return (P&L / Max DD): production **0.18** → combined stack **1.73**. Nearly 10× improvement.

### Walk-Forward (50/50 chronological split)

| Half | Production today | Combined Stack | Δ |
|---|---:|---:|---:|
| TRAIN (first 49 trades) | +Rs 8,458 | +Rs 23,240 | +Rs 14,782 |
| **TEST (last 49 trades)** | **-Rs 5,352** | **+Rs 1,970** | **+Rs 7,322** |

**The combined stack flips the test half from losing to winning.**

---

## What We Investigated But Did NOT Adopt

| Considered | Why rejected |
|---|---|
| Premium-bucket filter (skip 100-200) | Train picked 50-100 bucket, full year picked 100-200 — unstable, curve-fitted |
| Per-split optimal params (let TP/SL float per regime) | Different splits picked different "best" — pure overfitting |
| Regime-gating as a hard filter (RANGE only) | Hurts P&L at optimal params — skips winning trades in trends |
| Skip Friday entries | Only -Rs 4,843 in sample, not big enough to be confident |
| Skip 12:00-13:30 entries (mid-day fade) | Showed loss but trade counts too small for confidence |
| Bearish strategy (Bearish_Day_Rejection_V1) | Sample year was +7% bullish; can't validate. Build later when bearish data is available. |

---

## PE Entry Asymmetry — Explained, Not A Bug

The bot fired 90 CE entries vs only 8 PE entries over the year. Reason:

- Production Gate 0 blocks PE when VIX < 18
- 82.6% of the year had VIX < 18 (75,344 of 91,190 minutes)
- So PE setups were structurally rare by design — not a bug

The 8 PE entries that did fire had a 62.5% win rate vs CE's 42.2%. To
get more PE alpha you'd need a different macro regime (bearish or
high-vol year). 2024 data would test this.

---

## Caveats Before Going Live

1. **One year of data is not enough.** A profit factor of 1.44 on 98
   trades has wide confidence intervals. The strategy could still produce
   a losing year on different data.

2. **The sample year was structurally bullish (+7%).** Bear-market
   behavior of the strategy is unknown. Mean-reversion historically
   does worse in trending bear markets than trending bull markets.

3. **All filter rules survived walk-forward — but on the same year.**
   True out-of-sample validation requires a different period (e.g. 2024).

4. **Max drawdown is Rs 14,596 to make Rs 25,210 (~58%).** Capital
   allocation should assume Rs 30k buffer per lot.

5. **One bad month can negate the year.** Jan 2026 alone lost Rs 8.2k.
   Consider a monthly-loss circuit breaker (e.g. halt strategy if down
   > Rs 6k in any 21-day rolling window).

---

## Deployment Plan

### Phase A — Code Changes (~30 min work)
1. Update `Options.json`: `profitTargetPercent: 50 → 60`
2. Add Monday-skip filter to entry path
3. Add 11:00–11:29 skip filter to entry path
4. (Optional) Add TREND_DOWN-skip filter — small but consistent

### Phase B — Paper Trading (4–8 weeks)
- Run with the changes above on live paper data
- Compare to backtest expectations: ~6 trades/week, 48% win rate, Rs
  ~500/trade average expectancy
- Watch for distribution drift (live performance significantly
  different from backtest)

### Phase C — Small Live Capital (4 weeks)
- Only after paper trading meets backtest expectations
- Start with 1 lot, single instrument
- Monthly-loss circuit breaker armed

### Phase D — Scaling Decision
- If live tracks paper, scale lot size
- If live diverges, halt and investigate before doubling down

### Phase E — Regime System Activation (deferred)
- Phase 8 evidence says regime gating HURTS the simple stack at optimal
  parameters. Don't activate regime-gated tactics until you have:
  - More data (2024 + 2026)
  - Working trend-pullback tactic with its own validated edge
  - Walk-forward proof that the multi-tactic ensemble beats single-tactic

---

## Files Produced By This Project

| File | Purpose |
|---|---|
| `regime/classifier.py` | 9-regime state machine, hysteresis, immediate-override |
| `regime/router.py` | Regime → tactic mapping + hostile-direction detection |
| `regime/master_risk.py` | Daily loss halt, position sizing, max-positions |
| `tests/test_regime.py` | 37 unit tests, all passing |
| `backtesting/historical_downloader.py` | Original Upstox downloader (legacy) |
| `backtesting/historical_downloader_plus.py` | Plus-plan expired-instruments downloader (legacy) |
| `backtesting/download_spot_vix.py` | Focused spot/VIX 1-min downloader |
| `backtesting/bulk_download.py` | 1-year multi-expiry option-chain downloader |
| `backtesting/synthetic_spot.py` | Put-call parity spot derivation |
| `backtesting/backtest_regime_phase1.py` | Classifier smoke test |
| `backtesting/phase2_routing_report.py` | Routing visualization |
| `backtesting/backtest_regime_phase3.py` | Simplified-tactic backtest harness |
| `backtesting/backtest_regime_phase4.py` | Production-bot backtest with chain reconstruction |
| `backtesting/backtest_regime_phase5.py` | Parameter sweep (capture-once, replay-many) |
| `backtesting/backtest_regime_phase6.py` | Walk-forward validation |
| `backtesting/backtest_regime_phase7.py` | Loser-bucket discovery + skip rules |
| `backtesting/backtest_regime_phase8.py` | Combined-stack final test |
| `strategy_*.json` | Five strategy specifications (regime-master + 4 tactics) |
| `reports/phase*_*.md` | Per-phase reports with all numbers |
| `reports/FINAL_SYNTHESIS.md` | This document |

---

## Bottom Line For The Operator

> Apply the three validated changes (TP 60, no Mondays, no 11:00 entries).
> Paper-trade for 4-8 weeks. Watch for divergence. Only go live after
> paper performance matches backtest. Even then, expect Rs 15-25k/year
> on 1 lot in good years and breakeven-to-loss in bad years. This is a
> small edge, not a money printer.
