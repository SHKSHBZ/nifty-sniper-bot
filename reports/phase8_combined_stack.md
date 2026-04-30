# Phase 8 — Combined Stack Backtest

Compares production today vs TP=60-only vs filters-only vs full stack.


## Side-By-Side Results (98 captured records)

| Config | Trades | Win% | Net P&L | Profit Factor | Max DD | P&L/DD |
|---|---:|---:|---:|---:|---:|---:|
| A — Production today (TP 50, no filters) | 98 | 43.9 | Rs 3,106 | 1.03 | Rs 16,970 | 0.18 |
| B — TP 60 only | 98 | 42.9 | Rs 7,066 | 1.07 | Rs 17,495 | 0.40 |
| C — Filters only (TP 50) | 68 | 50.0 | Rs 23,673 | 1.41 | Rs 13,179 | 1.80 |
| D — TP 60 + 3 filters (combined) | 68 | 48.5 | Rs 25,210 | 1.44 | Rs 14,596 | 1.73 |

## Per-Config Detail

### A — Production today (TP 50, no filters)

- Winners: 43  Losers: 55
- Avg win: Rs 2,259   Avg loss: Rs -1,710
- Exit reasons: {'TIME_STOP': 49, 'SL': 22, 'TP': 20, 'EOD': 7}

### B — TP 60 only

- Winners: 42  Losers: 56
- Avg win: Rs 2,419   Avg loss: Rs -1,688
- Exit reasons: {'TIME_STOP': 55, 'SL': 22, 'TP': 14, 'EOD': 7}

### C — Filters only (TP 50)

- Winners: 34  Losers: 34
- Avg win: Rs 2,377   Avg loss: Rs -1,681
- Exit reasons: {'TIME_STOP': 36, 'TP': 16, 'SL': 12, 'EOD': 4}

### D — TP 60 + 3 filters (combined)

- Winners: 33  Losers: 35
- Avg win: Rs 2,511   Avg loss: Rs -1,647
- Exit reasons: {'TIME_STOP': 42, 'SL': 12, 'TP': 10, 'EOD': 4}

## Walk-Forward 50/50 — Combined Stack (D)

| Half | Config | Trades | Win% | Net P&L |
|---|---|---:|---:|---:|
| TRAIN (first 49) | A — Prod today | 49 | 49.0 | Rs 8,458 |
| TRAIN (first 49) | D — Combined  | 35 | 54.3 | Rs 23,240 |
| TEST  (last 49)  | A — Prod today | 49 | 38.8 | Rs -5,352 |
| TEST  (last 49)  | D — Combined  | 33 | 42.4 | Rs 1,970 |

- TRAIN improvement vs prod: **Rs +14,782**
- TEST  improvement vs prod: **Rs +7,322**

## Phase 9 — Why Is The Bot CE-Heavy?

The production SignalEngine has Gate 0 (VIX macro):
- VIX < 18 -> CE entries allowed, PE entries blocked
- VIX >= 18 -> PE entries allowed, CE entries blocked

VIX distribution across the year (1-minute bars):

- Total minutes: **91,190**
- Minutes with VIX < 18: **75,344 (82.6%)**
- Minutes with VIX >= 18: **15,846 (17.4%)**

**Verdict:** CE-heavy entries are a structural consequence of low VIX, not a bug. The bot was correct to block PE entries during sustained low-VIX periods. The 8 PE entries that did fire (62% win rate) coincided with VIX spike windows. To get more PE alpha, you'd need either (a) a different VIX threshold or (b) more time in market regimes with VIX > 18 — a 2024/2022 data sample would help.

## Recommendation

Combined stack improves in-sample P&L by **Rs 22,104** (3,106 -> 25,210).

**Combined stack PASSES walk-forward AND is profitable in-sample.** Recommend updating Options.json (TP -> 60%) and adding the three filters in entry logic. Begin paper-trading validation.
