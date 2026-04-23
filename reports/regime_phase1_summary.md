# Phase 1 Regime Classifier Smoke Test

- Source: `NIFTY50_INDEX_1minute.csv`
- Rows: 1,500
- Range: 2026-04-10 09:15:00+05:30 to 2026-04-16 15:29:00+05:30

## Known Limitations

- **No futures data**: spot used as proxy. VWAP degraded.
- **No VIX data**: VIX gate disabled (stubbed at 15.0).
- **No trade simulation**: classifier output only.

## Aggregate Regime Distribution

| Regime | Bars | Share |
|---|---:|---:|
| RANGE | 160 | 65.6% |
| TREND_UP | 44 | 18.0% |
| TREND_DOWN | 22 | 9.0% |
| WAIT | 18 | 7.4% |

## Per-Day Timeline (regime changes only)


### 2026-04-10

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-04-13

- `09:30` → **WAIT**
- `10:00` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-04-15

- `09:30` → **WAIT**
- `10:00` → **RANGE**

### 2026-04-16

- `09:30` → **WAIT**
- `10:00` → **RANGE**
- `12:45` → **TREND_DOWN**
