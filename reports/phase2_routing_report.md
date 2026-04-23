# Phase 2 — Regime Routing Report

Source: `NIFTY_SPOT_SYNTHETIC_1min.csv` (synthetic spot via put-call parity on option chain)

Range: 2026-03-24 09:15:00+05:30 to 2026-03-30 15:29:00+05:30


## Per-Day Breakdown


### 2026-03-24

Open: 22,860.0 | Close: 22,982.8 | Move: +0.54% | Range: [22,625.5, 23,056.6]


| Window | Regime | Tactic Armed | Direction |
|---|---|---|---|
| 09:30 – 12:45 | RANGE | OI_WALL_MEAN_REVERSION | - |
| 12:45 – 14:30 | TREND_UP | OI_TREND_PULLBACK | CE |

### 2026-03-25

Open: 22,982.8 | Close: 23,315.3 | Move: +1.45% | Range: [22,979.9, 23,460.2]


| Window | Regime | Tactic Armed | Direction |
|---|---|---|---|
| 09:30 – 12:45 | RANGE | OI_WALL_MEAN_REVERSION | - |
| 12:45 – 14:30 | TREND_UP | OI_TREND_PULLBACK | CE |

### 2026-03-27

Open: 23,315.3 | Close: 22,800.0 | Move: -2.21% | Range: [22,800.0, 23,315.3]


| Window | Regime | Tactic Armed | Direction |
|---|---|---|---|
| 09:30 – 12:45 | RANGE | OI_WALL_MEAN_REVERSION | - |
| 12:45 – 14:30 | TREND_DOWN | OI_TREND_PULLBACK | PE |

### 2026-03-30

Open: 22,800.0 | Close: 22,331.6 | Move: -2.05% | Range: [22,293.9, 22,807.7]


| Window | Regime | Tactic Armed | Direction |
|---|---|---|---|
| 09:30 – 14:30 | RANGE | OI_WALL_MEAN_REVERSION | - |

## Aggregate Tactic Dispatch (regime-segment count)

| Tactic | Segments Armed |
|---|---:|
| OI_WALL_MEAN_REVERSION | 4 |
| OI_TREND_PULLBACK | 3 |

## Baseline vs Regime-Switched Arming

_Baseline = existing bot always armed during the session._
_Regime-switched = existing bot (`OI_WALL_MEAN_REVERSION`) armed only during `RANGE` segments._

- Total in-session regime segments: **7**
- Segments where existing bot would fire under regime switching: **4**
- Reduction in armed-time: **43%**

### Implication

Under the regime-switched system, the existing OI-wall mean-reversion bot fires only during RANGE segments. On this 4-day sample, that reduces its trading surface by the percentage above. **The hypothesis being tested is that the trades skipped were the losing ones — i.e. your existing bot bleeds P&L during trend segments where mean reversion fails.**

A full trade-simulation P&L comparison requires:
- real futures data (for proper ADX / VWAP / volume-aware trigger math)
- real VIX (for Gate 0 VIX filter)
- OR continued use of synthetic spot (this run) as a directional probe

### Sample Size Caveat

**n = 4 trading days**. This cannot prove or disprove the regime hypothesis statistically. It only validates that the machinery (classifier + router) produces sensible outputs. Conclusions require ≥ 3 months of data.