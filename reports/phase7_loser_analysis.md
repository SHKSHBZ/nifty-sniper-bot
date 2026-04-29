# Phase 7 — Loser Analysis & Skip-Rule Discovery

Records: 98, params SL 30% / TP 50% / 120m

Baseline net P&L: **Rs 3,106**, win rate 43.9% (43/98)


## Slice Tables

### Sliced by Entry time-of-day (30-min buckets)

| hour_bucket | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| 11:00 | 13 | 3 | 23.1 | Rs -7,683 | Rs -591 |
| 12:30 | 4 | 1 | 25.0 | Rs -4,238 | Rs -1,059 |
| 13:00 | 4 | 0 | 0.0 | Rs -3,071 | Rs -768 |
| 12:00 | 6 | 2 | 33.3 | Rs -1,205 | Rs -201 |
| 13:30 | 6 | 3 | 50.0 | Rs 1,548 | Rs 258 |
| 11:30 | 8 | 4 | 50.0 | Rs 3,150 | Rs 394 |
| 10:30 | 23 | 14 | 60.9 | Rs 6,348 | Rs 276 |
| 10:00 | 34 | 16 | 47.1 | Rs 8,256 | Rs 243 |

### Sliced by Day of week

| dow | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| Monday | 15 | 5 | 33.3 | Rs -14,345 | Rs -956 |
| Friday | 22 | 11 | 50.0 | Rs -4,843 | Rs -220 |
| Wednesday | 25 | 10 | 40.0 | Rs 2,529 | Rs 101 |
| Tuesday | 20 | 10 | 50.0 | Rs 7,252 | Rs 363 |
| Thursday | 16 | 7 | 43.8 | Rs 12,513 | Rs 782 |

### Sliced by Month

| month | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| 2026-01 | 14 | 4 | 28.6 | Rs -8,202 | Rs -586 |
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
| CE | 90 | 38 | 42.2 | Rs -607 | Rs -7 |
| PE | 8 | 5 | 62.5 | Rs 3,712 | Rs 464 |

### Sliced by Regime at entry

| regime | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| TREND_DOWN | 4 | 1 | 25.0 | Rs -814 | Rs -204 |
| RANGE | 86 | 37 | 43.0 | Rs -241 | Rs -3 |
| TREND_UP | 5 | 2 | 40.0 | Rs -199 | Rs -40 |
| TREND_UP_GAP | 3 | 3 | 100.0 | Rs 4,360 | Rs 1,453 |

### Sliced by Entry premium size

| premium_bucket | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| 3: 100-200 | 59 | 22 | 37.3 | Rs -9,479 | Rs -161 |
| 2: 50-100 | 22 | 12 | 54.5 | Rs 1,355 | Rs 62 |
| 1: <50 | 8 | 5 | 62.5 | Rs 4,324 | Rs 541 |
| 4: 200-400 | 9 | 4 | 44.4 | Rs 6,905 | Rs 767 |

### Sliced by Exit reason

| exit_reason | Trades | Wins | Win% | Net P&L | Avg P&L |
|---|---:|---:|---:|---:|---:|
| SL | 22 | 0 | 0.0 | Rs -50,598 | Rs -2,300 |
| TIME_STOP | 49 | 22 | 44.9 | Rs -10,469 | Rs -214 |
| EOD | 7 | 1 | 14.3 | Rs -2,669 | Rs -381 |
| TP | 20 | 20 | 100.0 | Rs 66,841 | Rs 3,342 |

## Skip-Rule Candidates

Buckets where skipping all trades would improve cumulative P&L. Sorted by improvement.

| Dim | Value | Trades skipped | Wins | Win% skipped | P&L skipped | New P&L |
|---|---|---:|---:|---:|---:|---:|
| dow | Monday | 15 | 5 | 33% | Rs -14,345 | Rs 17,450 |
| premium_bucket | 3: 100-200 | 59 | 22 | 37% | Rs -9,479 | Rs 12,585 |
| hour_bucket | 11:00 | 13 | 3 | 23% | Rs -7,683 | Rs 10,788 |
| dow | Friday | 22 | 11 | 50% | Rs -4,843 | Rs 7,949 |
| hour_bucket | 12:30 | 4 | 1 | 25% | Rs -4,238 | Rs 7,343 |
| hour_bucket | 13:00 | 4 | 0 | 0% | Rs -3,071 | Rs 6,176 |
| hour_bucket | 12:00 | 6 | 2 | 33% | Rs -1,205 | Rs 4,310 |
| regime | TREND_DOWN | 4 | 1 | 25% | Rs -814 | Rs 3,920 |
| direction | CE | 90 | 38 | 42% | Rs -607 | Rs 3,712 |
| regime | RANGE | 86 | 37 | 43% | Rs -241 | Rs 3,347 |
| regime | TREND_UP | 5 | 2 | 40% | Rs -199 | Rs 3,304 |

## What-If: Top 4 Non-Overlapping Skip Rules Combined

- Skip if `dow == Monday`
- Skip if `premium_bucket == 3: 100-200`
- Skip if `hour_bucket == 11:00`
- Skip if `regime == TREND_DOWN`

- Trades kept: **24** (dropped 74)
- Net P&L after rules: **Rs 17,054** (was Rs 3,106)
- Win rate after: 62.5%
- Improvement: **Rs 13,948**

## Out-Of-Sample Sanity (50/50 chronological split)

**Rules derived from TRAIN half:**
- `dow == Monday` -> skip
- `premium_bucket == 2: 50-100` -> skip
- `hour_bucket == 11:00` -> skip
- `regime == TREND_DOWN` -> skip

- TRAIN  with these rules: Rs 22,610 (30 trades, was Rs 8,458)
- TEST   with these rules: Rs -3,423 (29 trades, was Rs -5,352)
- TRAIN improvement: Rs 14,152
- TEST improvement:  Rs 1,930

**Verdict:** rules improved P&L on both train AND test — candidate edge worth pursuing.
