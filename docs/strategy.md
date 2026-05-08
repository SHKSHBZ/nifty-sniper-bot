# Unified Multi-Pillar Options Strategy — v1.0

Bot-executable spec combining the user's 4-Pillar checklist with backtest-validated findings. See `config/strategy_spec.json` for the canonical machine-readable version.

## What this strategy is

The bot does **3-4 different things on different days**, never one thing every day:

| Tactic | When it fires | Expected Annual P&L | Source of edge |
|---|---|---|---|
| **T1 — VIX-Direction Tuned** | ~7 days/yr at 10:30 IST | +₹16,000 | Backtest confirmed |
| **T2 — Expiry Straddle** | ~12 expiry days/yr at 14:50 IST | +₹30,000 | Backtest confirmed |
| **T3 — S/R-Bounce Reversal** | When spot tests S/R | TBD | Today's manual trade — needs backtest |
| **T4 — Late-Day Lottery** | Last 30 min of expiry | TBD | Pattern observed, needs backtest |

**Combined target: ₹50,000–₹100,000/year** on ₹40-80k working capital.

## The 5 pillars (concrete thresholds)

We replaced "Volume" with "VIX Context" because index spot has no real volume. We added "VIX Direction" as a fifth pillar with extra weight because the backtest showed it as the strongest single signal.

| # | Pillar | Status | What we check |
|---|---|---|---|
| 1 | Candlestick Pattern | ⏸️ Skipped (need pattern detector) | 5-min reversal candle at level |
| 2 | Support / Resistance | ✅ Active | Spot within 0.2% of Camarilla S3/R3 or Classic S1/R1 |
| 3 | Volatility Context | ✅ Active | VIX in [12, 18], premium not in top 30% richness |
| 4 | Fibonacci | ⏸️ Skipped (need swing detector) | Spot near 23.6/38.2/61.8% retracement |
| 5 | VIX Direction (intraday) | ✅ Active, weight 1.5× | At 10:30: VIX moved >1% from open |

## Decision logic

Sum pillar weights for those that pass:
- **Score ≥ 3.5** → STRONG signal → 100% capital → ATM + ITM combo strikes
- **Score ≥ 2.5** → NORMAL signal → 50% capital → ATM only
- **Score < 2.5** → SKIP

(Currently with active pillars 2, 3, 5: max score = 3.5)

## Strike selection (the critical piece you taught me today)

**At S/R levels (high conviction)** → split capital 50/50 between ATM and **ATM±2 ITM**
- Higher delta on ITM = more rupees per move
- Less theta on ITM = better hold characteristics
- This is what you did today with the 24000 + 23900 CE combo

**Trend continuation (mid conviction)** → ATM only
- Standard directional bet
- Cheaper, more lots, more leverage

**Expiry-day no-direction-view** → ATM straddle (both legs)

## Exit rules (the half nobody talks about)

| Tactic | TP | SL | Trail | Force close |
|---|---|---|---|---|
| T1 VIX Direction | +30% | −30% | BE at +15% | 15:25 |
| T2 Expiry Straddle | None | −50% combined | None | 15:25 |
| T3 S/R Bounce | +50% | break of level | BE at +20% | 15:25 |
| T4 Late Lottery | +50% | None | None | 15:25 |

## Time gates

- **No entries before 09:30 IST** (skip open noise)
- **No entries after 14:30 IST** (except T4 late lottery)
- **Force close all at 15:25 IST** (never overnight)
- **Min 30 min between entries** (cool-down)

## Pre-trade gates

Before any tactic fires, ALL of these must pass:

- VIX in {Normal, Elevated} regime (12–18)
- DTE in [2,3,4,5,6] (skip 0,1 — they bleed)
- ATM premium ₹10-200 NIFTY / ₹50-800 SENSEX
- Not within 30 min of last entry

## Risk management

| Limit | Value |
|---|---|
| Max loss per trade | ₹6,000 |
| Max daily loss | ₹25,000 |
| Max weekly loss | ₹60,000 |
| 3 consecutive losses → pause | 60 min |
| Session kill switch at DD | ₹30,000 |

## What the bot still needs

1. **Candlestick pattern detector** for Pillar 1
2. **Fibonacci swing detector** for Pillar 4
3. **Backtest for T3 (S/R-Bounce)** — most important pending item
4. **Backtest for T4 (late lottery)**
5. **Two new tactic classes** under `tactics/` for T1 and T2

## Open questions

These aren't yet decided in the spec — flag your preference:

1. T3 without candle confirmation: should S/R touch alone trigger if no pattern detector? (yes = more trades, more noise / no = wait for pattern detector)
2. T4 sizing: cap at ₹5k per trade since it's lottery profile? (default is ₹20k full position)
3. Trail-to-BE trigger: same +15% across all tactics or per-tactic?
4. Multi-tactic same day: separate capital pools or first-come-first-served?

## Versioning

Bump `version` field in `strategy_spec.json` whenever rules change. Keep dated copies under `config/archive/strategy_spec_<date>.json` so backtests can replay against old rules.

## How this maps to the existing codebase

| Tactic | Existing file to extend / new file to create |
|---|---|
| T1 VIX Direction | NEW: `tactics/vix_direction.py` |
| T2 Expiry Straddle | NEW: `tactics/expiry_straddle_sl.py` |
| T3 S/R Bounce | NEW: `tactics/sr_bounce_reversal.py` (after backtest validates) |
| T4 Late Lottery | NEW: `tactics/late_day_lottery.py` (after backtest) |
| Pillar checks | NEW: `regime/pillar_evaluator.py` (returns score 0–3.5) |
| S/R levels | EXTEND: `regime/sr_provider.py` reading `reports/sr_levels_*.csv` |

Each new tactic plugs into the existing `regime/dispatcher.py` machinery without changing it.
