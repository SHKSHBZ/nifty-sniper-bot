# Phase 6 — Walk-Forward Validation

Records: 124 captured trades, 2024-09-24 10:00:00+05:30 to 2026-04-20 10:50:00+05:30

Question answered: is Phase-5 winner (SL 30 / TP 60 / time 120) robust across out-of-sample splits, or curve-fitted?


## Per-Split Results

| Split | Train n | Test n | Best Train Params | Train Rs | Test Rs (best) | Prod-default Test Rs | P5-winner Test Rs |
|---|---:|---:|---|---:|---:|---:|---:|
| 50/50 | 62 | 62 | SL 25%/TP 60%/30m | -37,816 | -22,583 | 4,798 | 9,606 |
| 66/33 | 82 | 42 | SL 40%/TP 40%/30m | -35,412 | -23,209 | -8,896 | -6,578 |
| 33/66 | 41 | 83 | SL 15%/TP 60%/30m | -29,078 | -35,514 | -4,699 | -1,543 |
| rolling Q1->Q2 | 31 | 31 | SL 15%/TP 60%/30m | -30,364 | -8,095 | -2,835 | -3,682 |
| rolling Q2->Q3 | 31 | 31 | SL 25%/TP 35%/90m | 3,153 | 806 | 15,963 | 18,853 |
| rolling Q3->Q4 | 31 | 31 | SL 35%/TP 60%/120m | 20,137 | -11,277 | -11,165 | -9,247 |

## Aggregate Stability

- Splits evaluated: **6**
- Splits where Phase-5 params (SL30/TP60/120) won the train sweep: **0/6**
- Cumulative TEST P&L using each split's train winner: **Rs -99,872**
- Cumulative TEST P&L using production default everywhere: **Rs -6,833**
- Cumulative TEST P&L using Phase-5 winner everywhere: **Rs 7,409**

## Verdict

**Phase-5 winner is BETTER THAN PRODUCTION but NOT STABLE** — TP 60% wins on cumulative test P&L but didn't dominate the train sweeps. There may be a better param. Investigate before deploying.
