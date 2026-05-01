# Phase 11 — Near-Miss Aggregate Analysis

Total near-misses captured: **35,518**
Date range: 2024-01-01 10:00:00+05:30 -> 2026-04-21 13:25:00+05:30
A near-miss is a 5-min bar where exactly ONE gate blocked the tactic from firing. For each, we simulate the would-have-been trade with default exits (TP +50%, SL -30%, time stop 120m) and classify the outcome.

## Per-Tactic Summary

| Tactic | Near-misses | Hypothetical W | L | Breakeven | Unknown | Net hypothetical P&L |
|---|---:|---:|---:|---:|---:|---:|
| bearish_orb | 26 | 1 | 10 | 0 | 15 | Rs -9,596 |
| bullish_orb | 48 | 2 | 12 | 0 | 34 | Rs -27,392 |
| ief | 34562 | 3847 | 6812 | 781 | 23122 | Rs -5,396,197 |
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

### ief

| Blocker | Times fired | W | L | BE | UNK | Net hypothetical P&L | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `price_in_golden_zone` | 1749 | 248 | 324 | 49 | 1128 | Rs 15,223 | 🔴 RELAX — rejecting winners |
| `dte_ok` | 7 | 4 | 2 | 0 | 1 | Rs 3,943 | 🔴 RELAX — rejecting winners |
| `close_lower_than_prev` | 2 | 0 | 0 | 0 | 2 | Rs 0 | (too few samples) |
| `close_higher_than_prev` | 1 | 0 | 0 | 0 | 1 | Rs 0 | (too few samples) |
| `ob_or_fvg_confluence` | 21 | 1 | 2 | 1 | 17 | Rs -1,593 | (too few samples) |
| `choch_recent` | 23 | 0 | 7 | 3 | 13 | Rs -7,714 | ✅ KEEP — rejecting losers |
| `choch_direction_match` | 5581 | 614 | 1102 | 160 | 3705 | Rs -930,661 | ✅ KEEP — rejecting losers |
| `enough_history` | 12066 | 1410 | 2395 | 214 | 8047 | Rs -1,947,035 | ✅ KEEP — rejecting losers |
| `choch_present` | 15112 | 1570 | 2980 | 354 | 10208 | Rs -2,528,360 | ✅ KEEP — rejecting losers |

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
| ief | CE | 17434 | 1825 | 3488 | Rs -3,486,213 |
| ief | PE | 17128 | 2022 | 3324 | Rs -1,909,984 |
| trend_pullback | CE | 211 | 80 | 122 | Rs -33,399 |
| trend_pullback | PE | 219 | 84 | 113 | Rs 1,030 |
| vwap_hybrid | CE | 398 | 86 | 150 | Rs -117,684 |
| vwap_hybrid | PE | 54 | 9 | 25 | Rs -12,064 |

## Top 20 Highest-Impact Near-Misses

| Date | Time | Tactic | Dir | Blocker | Hypo P&L | Outcome |
|---|---|---|---|---|---:|---|
| 2026-04-02 | 13:00 | ief | CE | `choch_present` | Rs +12,216 | WIN |
| 2026-04-02 | 12:00 | ief | CE | `choch_present` | Rs +11,737 | WIN |
| 2026-04-02 | 13:10 | ief | CE | `choch_present` | Rs +11,605 | WIN |
| 2026-04-02 | 12:45 | ief | CE | `choch_present` | Rs +11,449 | WIN |
| 2026-04-02 | 12:40 | ief | CE | `choch_present` | Rs +11,422 | WIN |
| 2026-04-02 | 12:15 | ief | CE | `choch_present` | Rs +11,418 | WIN |
| 2026-04-02 | 12:10 | ief | CE | `choch_present` | Rs +11,389 | WIN |
| 2026-04-02 | 12:05 | ief | CE | `choch_present` | Rs +11,298 | WIN |
| 2026-04-02 | 11:50 | ief | CE | `choch_present` | Rs +11,165 | WIN |
| 2026-04-02 | 12:50 | ief | CE | `choch_present` | Rs +11,161 | WIN |
| 2026-04-02 | 12:20 | ief | CE | `choch_present` | Rs +11,099 | WIN |
| 2026-04-02 | 12:30 | ief | CE | `choch_present` | Rs +11,030 | WIN |
| 2026-04-02 | 12:55 | ief | CE | `choch_present` | Rs +10,986 | WIN |
| 2026-04-02 | 13:05 | ief | CE | `choch_present` | Rs +10,854 | WIN |
| 2026-04-02 | 11:45 | ief | CE | `choch_present` | Rs +10,811 | WIN |
| 2026-04-02 | 13:15 | ief | CE | `choch_present` | Rs +10,705 | WIN |
| 2026-04-02 | 11:35 | ief | CE | `choch_present` | Rs +10,695 | WIN |
| 2026-04-02 | 12:35 | ief | CE | `choch_present` | Rs +10,622 | WIN |
| 2026-04-02 | 12:25 | ief | CE | `choch_present` | Rs +10,339 | WIN |
| 2026-04-02 | 13:20 | ief | CE | `choch_present` | Rs +10,225 | WIN |
