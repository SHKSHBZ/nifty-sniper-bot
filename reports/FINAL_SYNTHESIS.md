# Backtest Project — Final Synthesis (2-Year Update)

End-to-end summary of the investigation into whether the Nifty
Sniper Bot's mean-reversion strategy is deployable, after expanding
from 1 year of data (Aug 2025 – Apr 2026) to 2 years (Sep 2024 – Apr
2026).

> **The 2-year results invalidate the 1-year optimism.** The validated
> filter stack now produces near-breakeven (-Rs 2,048) instead of
> +Rs 25k. The bot is **NOT deployable** on the broader sample, but
> the loss has been compressed from -Rs 53k to -Rs 2k by the same
> filters that emerged in Phase 7.

---

## Executive Verdict (Revised)

**Production bot today on 2-year sample: -Rs 53,393.** Catastrophic.

**With validated changes (TP 60 + 3 filters): -Rs 2,048.** Breakeven.

**Walk-forward test improvement vs production: +Rs 13,352.** The
filters DO survive out-of-sample.

**The honest answer:** the strategy as currently configured is not a
live-deployable money maker. The validated filter stack stops the
bleeding (production loss compressed 96%) but doesn't produce profit.
The 2024-Q4 macro regime in particular destroys this strategy — that
period alone accounts for 110% of the 2-year loss.

---

## What Changed Going From 1 Year To 2 Years

| Metric | 1-Year (Aug25-Apr26) | 2-Year (Sep24-Apr26) | Δ |
|---|---:|---:|---:|
| Captured records | 98 | 124 | +26 |
| Production net P&L | +Rs 3,106 | -Rs 53,393 | **-Rs 56,499** |
| Production win rate | 43.9% | 37.1% | -6.8 pp |
| Phase 5 best (TP 60) | +Rs 7,066 | -Rs 52,723 | **-Rs 59,789** |
| Combined stack | +Rs 25,210 | -Rs 2,048 | -Rs 27,258 |
| Combined stack PF | 1.44 | 0.98 | -0.46 |
| Walk-forward test improvement | +Rs 7,322 | **+Rs 13,352** | **+Rs 6,030** |

The 26 added records (mostly 2024-Q4) collectively lost ~Rs 60k.
Strategy edge in normal periods is small, but its **tail risk in bad
periods is large enough to wipe out a year of profits**.

---

## The 2024-Q4 Disaster — Why The Strategy Broke

| Month | Trades | Win % | Net P&L |
|---|---:|---:|---:|
| 2024-09 | 4 | 0% | -Rs 10,697 |
| 2024-10 | 11 | 9% | **-Rs 29,452** |
| 2024-11 | 8 | 25% | -Rs 8,801 |
| 2024-12 | 3 | 0% | -Rs 7,549 |
| **Total Q4-2024** | **26** | **8%** | **-Rs 56,499** |

The whole 2-year loss is essentially this period. Outside it, the
strategy is roughly breakeven.

What was different about 2024-Q4:
- US presidential election volatility
- Indian budget anticipation
- VIX elevated, regime shifts
- Major directional moves (Nifty 25k -> 23.5k -> 25k)
- Mean-reversion at OI walls fails when walls are repeatedly broken

This is not parameter-tunable. It's a **regime mismatch**.

---

## Findings Status — 1-Year vs 2-Year

| Phase Finding | 1-Year Verdict | 2-Year Verdict | Conclusion |
|---|---|---|---|
| Regime classifier accuracy | Validated (RANGE 55%, trend 27%) | Confirmed | ✅ Real |
| Phase 4 production losing | -Rs 29k | -Rs 53k | ✅ Confirmed (worse) |
| TP=60 alone helps | +Rs 4k swing | +Rs 670 swing | ⚠ Marginal at best |
| Skip Mondays helps | -Rs 14k saved | -Rs 28k saved | ✅ **Real, durable edge** |
| Skip 11:00 entries helps | -Rs 8k saved | -Rs 12k saved | ✅ **Real, durable edge** |
| Skip TREND_DOWN regime | Small benefit | -Rs 11k saved | ✅ Real, larger than thought |
| Premium-bucket filter | Curve-fit | Curve-fit | ❌ Reject |
| Skip 10:00 entries | Best hour (+Rs 8k) | Worst hour (-Rs 24k) | ❌ **Period-specific, do not use** |
| Combined stack profitable | +Rs 25k | -Rs 2k | ❌ NOT profitable on 2-year |
| Walk-forward improvement | +Rs 7,322 | **+Rs 13,352** | ✅ **Filters provide durable edge** |
| Bot deployable for live | "Plausibly yes" | **NO** | ❌ Reject |

---

## Three Filters Are Still Validated — But Not Enough

The Monday/11:00/TREND_DOWN filters survived the 2-year stress test.
They reduce loss by ~Rs 50k. But that only brings production from
-Rs 53k to -Rs 2k. **It stops the bleeding without producing profit.**

```diff
# Options.json
- "profitTargetPercent": 50,
+ "profitTargetPercent": 60,
```

```python
# Entry-time filters (1-year and 2-year both validated)
if datetime.now().weekday() == 0:                # Monday
    return  # skip entry
if datetime.now().hour == 11 and datetime.now().minute < 30:
    return  # skip entry
if current_regime == Regime.TREND_DOWN:
    return  # skip entry
```

Net effect: brings production from -Rs 53k loss/2yr to -Rs 2k loss/2yr.
**Damage limitation, not edge.**

---

## What This Means

### The Strategy Has A Structural Problem

A profit factor of 0.98 over 124 trades on 2 years means the strategy
genuinely doesn't have edge. With:
- Win rate 41.5%
- Avg win ≈ Avg loss in absolute size
- Mean-reversion fails when walls break (2024-Q4 phenomenon)

**No amount of parameter tuning fixes a strategy that doesn't have edge.**

### What MIGHT Work (Untested)

The data hints at three places where actual edge exists:

1. **Skip CE entries entirely** — CE direction lost Rs 57k on 116 trades
   over 2 years. PE direction made +Rs 3,712 on just 8 trades (62.5%
   win). The bot's CE bias actively destroys money. **This is the
   single biggest skipped finding.**

2. **Trade only in TREND_UP_GAP regime** — 3 trades, 100% win rate,
   +Rs 4,360. Tiny sample but unique 100% win rate suggests genuine
   edge in gap-up days for this fade-the-extension setup.

3. **Run the strategy on bullish-only periods** — Removing 2024-Q4
   from the sample makes the strategy roughly breakeven. If you could
   identify "this is one of those bad periods" and pause, the bot
   would not have bled in 2024-Q4.

None of these are adopted in the current code.

---

## Honest Recommendations

### DON'T

1. ❌ **Don't deploy the current strategy live.** Even with all the
   validated filters, P&L over 2 years is essentially zero. Live
   slippage and friction will tip it negative.
2. ❌ **Don't trust the 1-year +Rs 25k number.** It was period-specific.
3. ❌ **Don't add more filters from Phase 7's 2-year run** (skip CE, skip
   RANGE) — those are statistical artifacts on a losing sample, not
   actionable rules.
4. ❌ **Don't tune parameters further.** Phase 5 sweep on 2-year shows
   ALL 210 combos lose money. The grid does not contain a profitable
   point.

### DO (in priority order)

1. ✅ **Apply the 3 validated filters** to your codebase as
   damage-limitation. They survived 2-year out-of-sample (TEST
   improvement +Rs 13k). Even if you stop here, your bot bleeds
   less in bad regimes.
2. ✅ **Investigate the CE-bias finding.** PE entries (8 of 124) had
   62.5% win rate and made money. CE entries (116) had 35.3% win
   rate and lost Rs 57k. Why is the bot SO biased to CE? Look at
   Gate 0 VIX threshold (currently 18). Maybe lowering it to 16
   would let more PE setups through.
3. ✅ **Build a regime-pause mechanism.** A monthly-loss circuit
   breaker (e.g. -Rs 8k in any 21-day window halts trading for 2
   weeks) would have saved most of 2024-Q4. Don't try to predict
   the regime — just detect bleeding and pause.
4. ✅ **Replace the strategy class.** Mean-reversion at OI walls
   doesn't have edge on 2-year data. The trend-pullback and ORB
   specs we wrote earlier might. Backtest those instead of trying
   to fix this one.
5. ❌ **Don't paper-trade the current bot expecting profit.** It will
   slowly bleed.

---

## What's In The Branch

| File | Purpose |
|---|---|
| `reports/FINAL_SYNTHESIS.md` | This document (2-year update) |
| `reports/phase4_production_backtest_report.md` | 2-year baseline P&L |
| `reports/phase5_param_sweep_report.md` | All 210 combos lose on 2-year |
| `reports/phase6_walk_forward_report.md` | 6 splits, no robust optimum |
| `reports/phase7_loser_analysis.md` | Slice tables identifying loser buckets |
| `reports/phase8_combined_stack.md` | Combined stack still loses (-Rs 2k) |
| `regime/`, `backtesting/` | Full code, all phases |
| `data/` | 2 years of spot, VIX, options |

---

## Bottom Line For The Operator

> Your strategy works in benign markets and fails in volatile/transitional
> ones. On a 2-year sample including 2024-Q4, the bot loses ~Rs 53k
> uncontrolled. With the 3 validated filters it bleeds only ~Rs 2k —
> close to break-even but not actually profitable. **Apply the filters
> for damage limitation, but do not deploy live as a profit-seeking
> system.** The next investigation that has the highest expected
> return is fixing the CE/PE imbalance (the bot ignores high-quality
> PE setups) and adding a circuit breaker to pause trading when
> losses exceed Rs 8k in any 21-day window.

> The 1-year FINAL_SYNTHESIS recommendation to "paper-trade for 4-8
> weeks then go live with 1 lot" is **withdrawn** based on this 2-year
> evidence. Don't go live. Investigate CE/PE first.
