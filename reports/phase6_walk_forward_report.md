# Phase 6 — Walk-Forward Validation

Records: 98 captured trades, 2025-08-19 13:35:00+05:30 to 2026-04-20 10:50:00+05:30

Question answered: is Phase-5 winner (SL 30 / TP 60 / time 120) robust across out-of-sample splits, or curve-fitted?


## Per-Split Results

| Split | Train n | Test n | Best Train Params | Train Rs | Test Rs (best) | Prod-default Test Rs | P5-winner Test Rs |
|---|---:|---:|---|---:|---:|---:|---:|
| 50/50 | 49 | 49 | SL 30%/TP 35%/120m | 10,419 | -17,657 | -5,352 | -2,446 |
| 66/33 | 65 | 33 | SL 30%/TP 60%/120m | 16,816 | -9,750 | -10,023 | -9,750 |
| 33/66 | 32 | 66 | SL 30%/TP 30%/120m | 9,957 | -22,810 | -3,858 | 950 |
| rolling Q1->Q2 | 24 | 24 | SL 30%/TP 35%/120m | 7,728 | 4,026 | 6,236 | 8,052 |
| rolling Q2->Q3 | 24 | 24 | SL 35%/TP 40%/30m | 9,085 | -11,025 | -6,963 | -5,974 |
| rolling Q3->Q4 | 24 | 24 | SL 15%/TP 60%/120m | 2,727 | -9,233 | 5,661 | 7,578 |

## Aggregate Stability

- Splits evaluated: **6**
- Splits where Phase-5 params (SL30/TP60/120) won the train sweep: **1/6**
- Cumulative TEST P&L using each split's train winner: **Rs -66,449**
- Cumulative TEST P&L using production default everywhere: **Rs -14,300**
- Cumulative TEST P&L using Phase-5 winner everywhere: **Rs -1,590**

## Verdict

**Phase-5 winner is BETTER THAN PRODUCTION but NOT STABLE** — TP 60% wins on cumulative test P&L but didn't dominate the train sweeps. There may be a better param. Investigate before deploying.
