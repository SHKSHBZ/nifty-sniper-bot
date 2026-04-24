# Phase 3 — Mean-Reversion Backtest: Baseline vs Regime-Gated

Period: 2026-03-24 to 2026-03-30 (4 trading days)

Tactic: simplified VWAP-extension mean reversion on ATM options


**Important caveat** — this simplified tactic is NOT the full 3-gate OI-wall mean-reversion logic of the live bot. It captures the same behavior class (fade extremes back to VWAP) using fewer inputs so that it's cleanly regime-gatable. Use this as a directional signal, not a production-accuracy forecast.

## Side-by-Side Results

| Metric | Baseline (always armed) | Regime-gated (RANGE only) |
|---|---:|---:|
| trades | 3 | 2 |
| winners | 2 | 1 |
| losers | 1 | 1 |
| win_rate | 66.67 | 50.00 |
| net_pnl | 1,411.63 | 1,074.71 |
| gross_pnl | 1,591.63 | 1,194.71 |
| avg_win | 2,057.06 | 3,777.21 |
| avg_loss | -2,702.49 | -2,702.49 |
| max_dd_estimate | 2,702.49 | 2,702.49 |

## Exit Reason Breakdown

| Reason | Baseline | Regime-gated |
|---|---:|---:|
| EOD | 1 | 0 |
| TIME_STOP | 2 | 2 |

## Per-Trade Log — Baseline

| Day | Enter | Exit | Reg@Entry | Dir | Strike | EntryPrem | ExitPrem | Reason | Net PnL |
|---|---|---|---|---|---:|---:|---:|---|---:|
| 2026-03-24 | 10:40 | 12:10 | RANGE | CE | 22650 | 348.30 | 410.85 | TIME_STOP | 3,777 |
| 2026-03-24 | 12:25 | 13:55 | RANGE | PE | 22850 | 306.00 | 279.55 | TIME_STOP | -2,702 |
| 2026-03-24 | 13:55 | 14:30 | TREND_UP | PE | 23000 | 344.00 | 359.85 | EOD | 337 |

## Per-Trade Log — Regime-gated

| Day | Enter | Exit | Reg@Entry | Dir | Strike | EntryPrem | ExitPrem | Reason | Net PnL |
|---|---|---|---|---|---:|---:|---:|---|---:|
| 2026-03-24 | 10:40 | 12:10 | RANGE | CE | 22650 | 348.30 | 410.85 | TIME_STOP | 3,777 |
| 2026-03-24 | 12:25 | 13:55 | RANGE | PE | 22850 | 306.00 | 279.55 | TIME_STOP | -2,702 |

## Interpretation

- Regime-gated took **2** trades vs baseline's **3** — that's a 33% reduction.

- P&L delta (gated - baseline): **Rs -337**

- Sample is only 4 days — no statistical conclusion, but the DIRECTIONAL result shows whether regime gating helps or hurts on this window.
