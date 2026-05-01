# Phase 11 — Near-Miss Aggregate Analysis

Total near-misses captured: **956**
Date range: 2024-01-08 12:00:00+05:30 -> 2026-04-21 13:25:00+05:30
A near-miss is a 5-min bar where exactly ONE gate blocked the tactic from firing. For each, we simulate the would-have-been trade with default exits (TP +50%, SL -30%, time stop 120m) and classify the outcome.

## Per-Tactic Summary

| Tactic | Near-misses | Hypothetical W | L | Breakeven | Unknown | Net hypothetical P&L |
|---|---:|---:|---:|---:|---:|---:|
| bearish_orb | 26 | 1 | 10 | 0 | 15 | Rs -9,596 |
| bullish_orb | 48 | 2 | 12 | 0 | 34 | Rs -27,392 |
| trend_pullback | 430 | 164 | 235 | 26 | 5 | Rs -32,370 |
| vwap_hybrid | 452 | 95 | 175 | 16 | 166 | Rs -129,748 |

## Blocker Analysis (per tactic)

For each (tactic, blocker), counts how often it fired AND the net hypothetical P&L from those rejected trades. **A blocker with high positive net P&L is a candidate for relaxation** — it's been rejecting profitable setups. Negative net P&L means the blocker is doing its job.

### bearish_orb

| Blocker | Times fired | W | L | BE | UNK | Net hypothetical P&L | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `volume_confirmation` | 26 | 1 | 10 | 0 | 15 | Rs -9,596 | ✅ KEEP — rejecting losers |

### bullish_orb

| Blocker | Times fired | W | L | BE | UNK | Net hypothetical P&L | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `volume_confirmation` | 48 | 2 | 12 | 0 | 34 | Rs -27,392 | ✅ KEEP — rejecting losers |

### trend_pullback

| Blocker | Times fired | W | L | BE | UNK | Net hypothetical P&L | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `regime_is_TREND_DOWN` | 104 | 47 | 51 | 5 | 1 | Rs 18,128 | 🔴 RELAX — rejecting winners |
| `dte_ok` | 63 | 29 | 31 | 2 | 1 | Rs 10,071 | 🔴 RELAX — rejecting winners |
| `vix_ok` | 2 | 1 | 1 | 0 | 0 | Rs 5,127 | (too few samples) |
| `reclaim_close_lt_prev` | 2 | 1 | 1 | 0 | 0 | Rs 1,473 | (too few samples) |
| `close_below_midpoint` | 2 | 0 | 2 | 0 | 0 | Rs -2,096 | (too few samples) |
| `close_above_midpoint` | 6 | 1 | 5 | 0 | 0 | Rs -2,298 | ✅ KEEP — rejecting losers |
| `reclaim_close_gt_prev` | 4 | 1 | 3 | 0 | 0 | Rs -2,614 | (too few samples) |
| `regime_is_TREND_UP` | 75 | 34 | 39 | 2 | 0 | Rs -6,448 | ✅ KEEP — rejecting losers |
| `oi_bias_magnitude` | 60 | 20 | 36 | 2 | 2 | Rs -13,390 | ✅ KEEP — rejecting losers |
| `oi_bias_ratio` | 112 | 30 | 66 | 15 | 1 | Rs -40,323 | ✅ KEEP — rejecting losers |

### vwap_hybrid

| Blocker | Times fired | W | L | BE | UNK | Net hypothetical P&L | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `price_extended_above_vwap` | 10 | 6 | 2 | 2 | 0 | Rs 34,312 | 🔴 RELAX — rejecting winners |
| `ce_oi_buildup` | 17 | 0 | 0 | 0 | 17 | Rs 0 | (too few samples) |
| `pcr_ok_for_PE` | 1 | 0 | 0 | 0 | 1 | Rs 0 | (too few samples) |
| `failure_of_lows` | 6 | 3 | 3 | 0 | 0 | Rs -4,534 | ✅ KEEP — rejecting losers |
| `dte_ok` | 18 | 6 | 12 | 0 | 0 | Rs -6,469 | ✅ KEEP — rejecting losers |
| `lod_proximity` | 4 | 0 | 4 | 0 | 0 | Rs -8,027 | (too few samples) |
| `vix_ok_for_CE` | 6 | 0 | 6 | 0 | 0 | Rs -16,030 | ✅ KEEP — rejecting losers |
| `pcr_ok_for_CE` | 12 | 1 | 11 | 0 | 0 | Rs -18,937 | ✅ KEEP — rejecting losers |
| `pe_oi_buildup` | 168 | 4 | 14 | 2 | 148 | Rs -23,233 | ✅ KEEP — rejecting losers |
| `price_extended_below_vwap` | 178 | 72 | 94 | 12 | 0 | Rs -23,726 | ✅ KEEP — rejecting losers |
| `reclaim_close` | 10 | 0 | 10 | 0 | 0 | Rs -23,766 | ✅ KEEP — rejecting losers |
| `vix_ok_for_PE` | 22 | 3 | 19 | 0 | 0 | Rs -39,338 | ✅ KEEP — rejecting losers |

## Direction Breakdown

| Tactic | Dir | Near-misses | W | L | Net hypothetical P&L |
|---|---|---:|---:|---:|---:|
| bearish_orb | PE | 26 | 1 | 10 | Rs -9,596 |
| bullish_orb | CE | 48 | 2 | 12 | Rs -27,392 |
| trend_pullback | CE | 211 | 80 | 122 | Rs -33,399 |
| trend_pullback | PE | 219 | 84 | 113 | Rs 1,030 |
| vwap_hybrid | CE | 398 | 86 | 150 | Rs -117,684 |
| vwap_hybrid | PE | 54 | 9 | 25 | Rs -12,064 |

## Top 20 Highest-Impact Near-Misses

| Date | Time | Tactic | Dir | Blocker | Hypo P&L | Outcome |
|---|---|---|---|---|---:|---|
| 2026-03-20 | 10:55 | vwap_hybrid | PE | `price_extended_above_vwap` | Rs +8,751 | WIN |
| 2026-03-20 | 11:40 | vwap_hybrid | PE | `price_extended_above_vwap` | Rs +8,700 | WIN |
| 2024-11-22 | 13:20 | trend_pullback | CE | `reclaim_close_gt_prev` | Rs +8,659 | WIN |
| 2026-03-20 | 11:35 | vwap_hybrid | PE | `price_extended_above_vwap` | Rs +8,468 | WIN |
| 2026-03-20 | 11:30 | vwap_hybrid | PE | `price_extended_above_vwap` | Rs +8,300 | WIN |
| 2024-11-05 | 11:55 | vwap_hybrid | CE | `failure_of_lows` | Rs +8,057 | WIN |
| 2024-11-22 | 13:45 | trend_pullback | CE | `oi_bias_magnitude` | Rs +8,003 | WIN |
| 2024-11-04 | 10:00 | trend_pullback | PE | `regime_is_TREND_DOWN` | Rs +7,387 | WIN |
| 2024-11-05 | 12:05 | vwap_hybrid | CE | `price_extended_below_vwap` | Rs +7,363 | WIN |
| 2024-11-05 | 12:15 | vwap_hybrid | CE | `price_extended_below_vwap` | Rs +7,230 | WIN |
| 2024-10-04 | 13:10 | vwap_hybrid | CE | `reclaim_close` | Rs -7,210 | LOSS |
| 2024-11-05 | 12:00 | vwap_hybrid | CE | `price_extended_below_vwap` | Rs +6,961 | WIN |
| 2026-01-29 | 12:45 | trend_pullback | CE | `oi_bias_magnitude` | Rs +6,622 | WIN |
| 2024-10-04 | 13:25 | vwap_hybrid | CE | `failure_of_lows` | Rs -6,590 | LOSS |
| 2024-09-20 | 13:15 | trend_pullback | CE | `oi_bias_magnitude` | Rs -6,163 | LOSS |
| 2026-04-02 | 13:30 | trend_pullback | CE | `vix_ok` | Rs +5,934 | WIN |
| 2024-10-07 | 10:15 | vwap_hybrid | CE | `price_extended_below_vwap` | Rs -5,698 | LOSS |
| 2024-12-17 | 10:15 | trend_pullback | PE | `regime_is_TREND_DOWN` | Rs +5,677 | WIN |
| 2025-10-08 | 12:30 | vwap_hybrid | CE | `price_extended_below_vwap` | Rs +5,613 | WIN |
| 2026-04-16 | 10:20 | trend_pullback | PE | `regime_is_TREND_DOWN` | Rs +5,607 | WIN |
