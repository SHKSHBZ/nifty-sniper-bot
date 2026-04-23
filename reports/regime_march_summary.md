# Phase 1 Regime Classifier Smoke Test

- Source: `NIFTY_SPOT_SYNTHETIC_1min.csv`
- Rows: 1,500
- Range: 2026-03-24 09:15:00+05:30 to 2026-03-30 15:29:00+05:30

## Known Limitations

- **No futures data**: spot used as proxy. VWAP degraded.
- **No VIX data**: VIX gate disabled (stubbed at 15.0).
- **No trade simulation**: classifier output only.

## Aggregate Regime Distribution

| Regime | Bars | Share |
|---|---:|---:|
| RANGE | 178 | 73.0% |
| TREND_UP | 44 | 18.0% |
| TREND_DOWN | 22 | 9.0% |

## Per-Day Timeline (regime changes only)


### 2026-03-24

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-03-25

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-03-27

- `09:30` → **RANGE**
- `12:45` → **TREND_DOWN**

### 2026-03-30

- `09:30` → **RANGE**
