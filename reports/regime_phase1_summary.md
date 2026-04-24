# Phase 1 Regime Classifier Smoke Test

- Source: `NIFTY50_INDEX_1minute.csv`
- Rows: 7,125
- Range: 2026-03-24 09:15:00+05:30 to 2026-04-23 15:29:00+05:30

## Known Limitations

- **No futures data**: spot used as proxy. VWAP degraded.
- **No VIX data**: VIX gate disabled (stubbed at 15.0).
- **No trade simulation**: classifier output only.

## Aggregate Regime Distribution

| Regime | Bars | Share |
|---|---:|---:|
| RANGE | 639 | 55.1% |
| TREND_UP | 223 | 19.2% |
| TREND_DOWN | 87 | 7.5% |
| TREND_DOWN_GAP | 61 | 5.3% |
| WAIT | 54 | 4.7% |
| NO_TRADE | 51 | 4.4% |
| TREND_UP_GAP | 44 | 3.8% |

## Per-Day Timeline (regime changes only)


### 2026-03-24

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-03-25

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-03-27

- `09:30` → **WAIT**
- `10:00` → **TREND_DOWN_GAP**

### 2026-03-30

- `09:30` → **NO_TRADE**
- `12:45` → **TREND_DOWN**
- `12:50` → **NO_TRADE**
- `13:35` → **TREND_DOWN**
- `13:50` → **NO_TRADE**
- `14:05` → **TREND_DOWN_GAP**

### 2026-04-01

- `09:30` → **WAIT**
- `10:00` → **RANGE**
- `12:45` → **TREND_UP**
- `13:50` → **TREND_DOWN**

### 2026-04-02

- `09:30` → **WAIT**
- `10:00` → **RANGE**
- `13:15` → **TREND_UP**

### 2026-04-06

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-04-07

- `09:30` → **WAIT**
- `10:00` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-04-08

- `09:30` → **WAIT**
- `10:00` → **RANGE**
- `10:55` → **TREND_UP_GAP**

### 2026-04-09

- `09:30` → **RANGE**
- `13:00` → **TREND_DOWN**

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

### 2026-04-17

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-04-20

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**
- `13:40` → **TREND_DOWN**

### 2026-04-21

- `09:30` → **RANGE**
- `12:45` → **TREND_UP**

### 2026-04-22

- `09:30` → **RANGE**
- `14:00` → **TREND_UP**

### 2026-04-23

- `09:30` → **WAIT**
- `10:00` → **RANGE**
- `12:45` → **TREND_DOWN**
