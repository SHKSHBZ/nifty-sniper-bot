# Phase 7 — Loser Analysis & Skip-Rule Discovery

Records: 124, params SL 30% / TP 50% / 120m

Baseline net P&L: **Rs -53,393**, win rate 37.1% (46/124)


## Slice Tables

### Sliced by Entry time-of-day (30-min buckets)

| hour_bucket | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| 10:00 | 45 | 16 | 35.6 | Rs -23,836 | Rs -530 |
| 11:00 | 14 | 3 | 21.4 | Rs -11,910 | Rs -851 |
| 13:30 | 9 | 3 | 33.3 | Rs -8,980 | Rs -998 |
| 10:30 | 29 | 14 | 48.3 | Rs -6,382 | Rs -220 |
| 12:30 | 4 | 1 | 25.0 | Rs -4,238 | Rs -1,059 |
| 13:00 | 4 | 0 | 0.0 | Rs -3,071 | Rs -768 |
| 12:00 | 9 | 3 | 33.3 | Rs -2,715 | Rs -302 |
| 11:30 | 10 | 6 | 60.0 | Rs 7,738 | Rs 774 |

### Sliced by Day of week

| dow | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| Monday | 23 | 7 | 30.4 | Rs -28,242 | Rs -1,228 |
| Friday | 27 | 11 | 40.7 | Rs -22,201 | Rs -822 |
| Tuesday | 27 | 10 | 37.0 | Rs -8,468 | Rs -314 |
| Wednesday | 29 | 10 | 34.5 | Rs -8,310 | Rs -287 |
| Thursday | 18 | 8 | 44.4 | Rs 13,828 | Rs 768 |

### Sliced by Month

| month | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| 2024-10 | 11 | 1 | 9.1 | Rs -29,452 | Rs -2,677 |
| 2024-09 | 4 | 0 | 0.0 | Rs -10,697 | Rs -2,674 |
| 2024-11 | 8 | 2 | 25.0 | Rs -8,801 | Rs -1,100 |
| 2026-01 | 14 | 4 | 28.6 | Rs -8,202 | Rs -586 |
| 2024-12 | 3 | 0 | 0.0 | Rs -7,549 | Rs -2,516 |
| 2026-02 | 14 | 4 | 28.6 | Rs -6,721 | Rs -480 |
| 2026-04 | 4 | 2 | 50.0 | Rs -1,915 | Rs -479 |
| 2025-09 | 15 | 7 | 46.7 | Rs -1,079 | Rs -72 |
| 2025-10 | 12 | 5 | 41.7 | Rs -370 | Rs -31 |
| 2025-12 | 15 | 6 | 40.0 | Rs 2,356 | Rs 157 |
| 2025-08 | 7 | 3 | 42.9 | Rs 2,623 | Rs 375 |
| 2026-03 | 4 | 3 | 75.0 | Rs 5,628 | Rs 1,407 |
| 2025-11 | 13 | 9 | 69.2 | Rs 10,786 | Rs 830 |

### Sliced by Direction (CE vs PE)

| direction | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| CE | 116 | 41 | 35.3 | Rs -57,105 | Rs -492 |
| PE | 8 | 5 | 62.5 | Rs 3,712 | Rs 464 |

### Sliced by Regime at entry

| regime | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| RANGE | 109 | 40 | 36.7 | Rs -46,213 | Rs -424 |
| TREND_DOWN | 7 | 1 | 14.3 | Rs -11,341 | Rs -1,620 |
| TREND_UP | 5 | 2 | 40.0 | Rs -199 | Rs -40 |
| TREND_UP_GAP | 3 | 3 | 100.0 | Rs 4,360 | Rs 1,453 |

### Sliced by Entry premium size

| premium_bucket | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| 3: 100-200 | 78 | 24 | 30.8 | Rs -49,190 | Rs -631 |
| 4: 200-400 | 12 | 4 | 33.3 | Rs -6,605 | Rs -550 |
| 2: 50-100 | 26 | 13 | 50.0 | Rs -1,922 | Rs -74 |
| 1: <50 | 8 | 5 | 62.5 | Rs 4,324 | Rs 541 |

### Sliced by Exit reason

| exit_reason | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| SL | 34 | 0 | 0.0 | Rs -94,431 | Rs -2,777 |
| TIME_STOP | 60 | 23 | 38.3 | Rs -29,155 | Rs -486 |
| EOD | 8 | 1 | 12.5 | Rs -4,023 | Rs -503 |
| TP | 22 | 22 | 100.0 | Rs 74,216 | Rs 3,373 |

## Skip-Rule Candidates

Buckets where skipping all trades would improve cumulative P&L. Sorted by improvement.

| Dim | Value | Trades skipped | Wins | Win% skipped | P&L skipped | New P&L |
|---|---|---:|---:|---:|---:|---:|
| direction | CE | 116 | 41 | 35% | Rs -57,105 | Rs 3,712 |
| premium_bucket | 3: 100-200 | 78 | 24 | 31% | Rs -49,190 | Rs -4,202 |
| regime | RANGE | 109 | 40 | 37% | Rs -46,213 | Rs -7,180 |
| dow | Monday | 23 | 7 | 30% | Rs -28,242 | Rs -25,151 |
| hour_bucket | 10:00 | 45 | 16 | 36% | Rs -23,836 | Rs -29,557 |
| dow | Friday | 27 | 11 | 41% | Rs -22,201 | Rs -31,191 |
| hour_bucket | 11:00 | 14 | 3 | 21% | Rs -11,910 | Rs -41,483 |
| regime | TREND_DOWN | 7 | 1 | 14% | Rs -11,341 | Rs -42,051 |
| hour_bucket | 13:30 | 9 | 3 | 33% | Rs -8,980 | Rs -44,413 |
| dow | Tuesday | 27 | 10 | 37% | Rs -8,468 | Rs -44,925 |
| dow | Wednesday | 29 | 10 | 34% | Rs -8,310 | Rs -45,082 |
| premium_bucket | 4: 200-400 | 12 | 4 | 33% | Rs -6,605 | Rs -46,788 |
| hour_bucket | 10:30 | 29 | 14 | 48% | Rs -6,382 | Rs -47,011 |
| hour_bucket | 12:30 | 4 | 1 | 25% | Rs -4,238 | Rs -49,155 |
| hour_bucket | 13:00 | 4 | 0 | 0% | Rs -3,071 | Rs -50,322 |
| hour_bucket | 12:00 | 9 | 3 | 33% | Rs -2,715 | Rs -50,678 |
| premium_bucket | 2: 50-100 | 26 | 13 | 50% | Rs -1,922 | Rs -51,471 |
| regime | TREND_UP | 5 | 2 | 40% | Rs -199 | Rs -53,194 |

## What-If: Top 4 Non-Overlapping Skip Rules Combined

- Skip if `direction == CE`
- Skip if `premium_bucket == 3: 100-200`
- Skip if `regime == RANGE`
- Skip if `dow == Monday`

- Trades kept: **1** (dropped 123)
- Net P&L after rules: **Rs 2,387** (was Rs -53,393)
- Win rate after: 100.0%
- Improvement: **Rs 55,780**

## Out-Of-Sample Sanity (50/50 chronological split)

**Rules derived from TRAIN half:**
- `direction == CE` -> skip
- `regime == RANGE` -> skip
- `premium_bucket == 3: 100-200` -> skip
- `hour_bucket == 10:00` -> skip

- TRAIN  with these rules: Rs 0 (0 trades, was Rs -58,191)
- TEST   with these rules: Rs 0 (0 trades, was Rs 4,798)
- TRAIN improvement: Rs 58,191
- TEST improvement:  Rs -4,798

**Verdict:** rules helped on train but FAILED on test. Curve-fitting. Don't deploy these specific rules.
