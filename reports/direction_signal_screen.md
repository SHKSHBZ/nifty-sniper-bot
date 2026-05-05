# Direction Signal Screen — Expiry 14:50

Sample: 38 expiries with complete data.
Actual winner distribution: 20 CE / 18 PE / 0 ties

## Signal accuracy

| Signal | n | accuracy | CE calls (right) | PE calls (right) |
|---|---|---|---|---|
| Spot momentum last 30 min | 38 | **52.6%** (20/38) | 18 (10) | 20 (10) |
| Spot momentum last 60 min | 38 | **57.9%** (22/38) | 12 (8) | 26 (14) |
| Spot vs day-open (intraday trend) | 38 | **52.6%** (20/38) | 14 (8) | 24 (12) |
| Spot vs VWAP | 38 | **50.0%** (19/38) | 13 (7) | 25 (12) |
| ΔPE OI − ΔCE OI at ATM (last 60 min) | 37 | **48.6%** (18/37) | 11 (6) | 26 (12) |
| PE − CE premium at 14:50 (fade) | 38 | **50.0%** (19/38) | 11 (6) | 27 (13) |

## Read

- A signal is *useful* if accuracy > 55%.
- A signal is *strong* if accuracy > 60%.
- 50% = coin flip — useless.

## Combined-signal idea

If two independent signals each have ~58% accuracy, taking only the trades where both *agree* on direction roughly compounds to ~67% accuracy on a smaller sample. Identify the top two non-overlapping signals from the table above and try the AND-rule.
