# Phase 8 — Combined Stack Backtest

Compares production today vs TP=60-only vs filters-only vs full stack.


## Side-By-Side Results (98 captured records)

| Config | Trades | Win% | Net P&L | Profit Factor | Max DD | P&L/DD |
|---|---:|---:|---:|---:|---:|---:|
| A — Production today (TP 50, no filters) | 124 | 37.1 | Rs -53,393 | 0.67 | Rs 56,309 | -0.95 |
| B — TP 60 only | 124 | 36.3 | Rs -52,723 | 0.67 | Rs 60,447 | -0.87 |
| C — Filters only (TP 50) | 82 | 42.7 | Rs -4,174 | 0.95 | Rs 28,673 | -0.15 |
| D — TP 60 + 3 filters (combined) | 82 | 41.5 | Rs -2,048 | 0.98 | Rs 28,673 | -0.07 |

## Per-Config Detail

### A — Production today (TP 50, no filters)

- Winners: 46  Losers: 78
- Avg win: Rs 2,313   Avg loss: Rs -2,049
- Exit reasons: {'TIME_STOP': 60, 'SL': 34, 'TP': 22, 'EOD': 8}

### B — TP 60 only

- Winners: 45  Losers: 79
- Avg win: Rs 2,390   Avg loss: Rs -2,029
- Exit reasons: {'TIME_STOP': 67, 'SL': 34, 'TP': 15, 'EOD': 8}

### C — Filters only (TP 50)

- Winners: 35  Losers: 47
- Avg win: Rs 2,387   Avg loss: Rs -1,866
- Exit reasons: {'TIME_STOP': 44, 'SL': 17, 'TP': 17, 'EOD': 4}

### D — TP 60 + 3 filters (combined)

- Winners: 34  Losers: 48
- Avg win: Rs 2,534   Avg loss: Rs -1,838
- Exit reasons: {'TIME_STOP': 50, 'SL': 17, 'TP': 11, 'EOD': 4}

## Walk-Forward 50/50 — Combined Stack (D)

| Half | Config | Trades | Win% | Net P&L |
|---|---|---:|---:|---:|
| TRAIN (first 49) | A — Prod today | 62 | 29.0 | Rs -58,191 |
| TRAIN (first 49) | D — Combined  | 40 | 30.0 | Rs -20,198 |
| TEST  (last 49)  | A — Prod today | 62 | 45.2 | Rs 4,798 |
| TEST  (last 49)  | D — Combined  | 42 | 52.4 | Rs 18,150 |

- TRAIN improvement vs prod: **Rs +37,993**
- TEST  improvement vs prod: **Rs +13,352**

## Phase 9 — Why Is The Bot CE-Heavy?

The production SignalEngine has Gate 0 (VIX macro):
- VIX < 18 -> CE entries allowed, PE entries blocked
- VIX >= 18 -> PE entries allowed, CE entries blocked

VIX distribution across the year (1-minute bars):

- Total minutes: **183,709**
- Minutes with VIX < 18: **160,384 (87.3%)**
- Minutes with VIX >= 18: **23,325 (12.7%)**

**Verdict:** CE-heavy entries are a structural consequence of low VIX, not a bug. The bot was correct to block PE entries during sustained low-VIX periods. The 8 PE entries that did fire (62% win rate) coincided with VIX spike windows. To get more PE alpha, you'd need either (a) a different VIX threshold or (b) more time in market regimes with VIX > 18 — a 2024/2022 data sample would help.

## Recommendation

Combined stack improves in-sample P&L by **Rs 51,345** (-53,393 -> -2,048).

**Combined stack improves on test but is not yet profitable.** Filters are deployable; expect to break ~even rather than profit.
