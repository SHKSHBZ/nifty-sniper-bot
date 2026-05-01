# Phase 10 — Trend Pullback Walk-Forward Validation

Total trades: 32
Date range: 2024-09-30 13:15:00+05:30 -> 2026-02-27 12:55:00+05:30

## Baseline (full 2-year sample)

| Metric | Value |
|---|---:|
| Trades | 32 |
| Win rate | 46.9% |
| Net P&L | Rs 9,939 |
| Profit factor | 1.64 |
| Max drawdown | Rs 4,718 |
| Avg win | Rs 1,698 |
| Avg loss | Rs -914 |

## Test 1 — Chronological Splits

Does the strategy profit in BOTH halves of the data?
If only one half profits, the result is period-specific.

| Split | Half | Trades | Wins | Win% | Net P&L | PF | Max DD |
|---|---|---:|---:|---:|---:|---:|---:|
| 50/50 TRAIN | 16 | 9 | 56.2 | Rs 9,536 | 2.97 | Rs 1,867 |
| 50/50 TEST | 16 | 6 | 37.5 | Rs 403 | 1.04 | Rs 4,718 |
| 66/33 TRAIN | 21 | 10 | 47.6 | Rs 7,138 | 1.88 | Rs 2,398 |
| 66/33 TEST | 11 | 5 | 45.5 | Rs 2,801 | 1.38 | Rs 4,718 |
| 33/66 TRAIN | 10 | 6 | 60.0 | Rs 6,217 | 2.94 | Rs 1,867 |
| 33/66 TEST | 22 | 9 | 40.9 | Rs 3,722 | 1.30 | Rs 4,718 |

**Splits where BOTH halves are profitable: 3/3**

## Test 2 — Quarterly Stability

How many calendar quarters were profitable?

| Quarter | Trades | Net P&L |
|---|---:|---:|
| 2024-Q3 | 1 | Rs 2,353 |
| 2024-Q4 | 4 | Rs 2,993 |
| 2025-Q3 | 3 | Rs 2,022 |
| 2025-Q4 | 13 | Rs -229 |
| 2026-Q1 | 11 | Rs 2,801 |

**Profitable quarters: 4 of 5**

## Test 3 — Direction Performance (CE vs PE)

Does ONE direction carry the strategy or are both contributing?

| Direction | Trades | Wins | Win% | Net P&L | PF |
|---|---:|---:|---:|---:|---:|
| CE | 13 | 7 | 53.8 | Rs 2,884 | 1.53 |
| PE | 19 | 8 | 42.1 | Rs 7,055 | 1.70 |

**Profitable directions: 2/2**

## Test 4 — Regime At Entry

| Regime | Trades | Wins | Win% | Net P&L |
|---|---:|---:|---:|---:|
| TREND_DOWN | 19 | 8 | 42.1 | Rs 7,055 |
| TREND_UP | 13 | 7 | 53.8 | Rs 2,884 |

## Test 5 — Equity Curve

- Peak equity at trade #28: Rs 14,657
- Trough: Rs 9,939 at trade #32
- Max drawdown depth: Rs 4,718
- Final equity: Rs 9,939

## Test 6 — Hour-Of-Day Distribution

Is the strategy concentrated at one entry window?

| Hour bucket | Trades | Wins | Win% | Net P&L |
|---|---:|---:|---:|---:|
| 12:30 | 9 | 5 | 55.6 | Rs 363 |
| 13:00 | 9 | 5 | 55.6 | Rs 7,785 |
| 13:30 | 10 | 5 | 50.0 | Rs 3,043 |
| 14:00 | 4 | 0 | 0.0 | Rs -1,251 |

## Verdict

| Robustness Check | Result |
|---|:---:|
| Both halves profitable on at least one split | PASS |
| Both halves profitable on the 50/50 split (strict) | PASS |
| Profitable quarters >= 60% | PASS |
| Both CE and PE profitable | PASS |
| Profit factor >= 1.30 | PASS |
| Max drawdown <= net P&L | PASS |

**Score: 6 / 6 robustness checks passed**

**Verdict: ROBUST.** Trend Pullback shows consistent edge across multiple validation slices. Move forward to paper-trading.
