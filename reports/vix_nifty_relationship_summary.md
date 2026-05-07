# India VIX vs NIFTY — Relationship Analysis

Trading days: **493**  (spot 2024-01-01 -> 2026-04-27)
VIX range: **9.13 - 27.76** (median 13.62)
NIFTY range: **21,243 - 26,335**

## Same-day correlation

- VIX % change vs NIFTY % change (same day): **-0.355**
  - Strong negative (-0.6 to -0.8 expected): textbook fear-gauge
  - Near zero: weakly related, day-to-day noise
- VIX today vs NIFTY tomorrow: 0.108
  - If significantly negative -> VIX leads NIFTY by 1 day

## First-hour VIX as predictor of full-day range

- Correlation: **0.119**
- VIX UP >2% in first hour (n=151): avg full-day range = **265.0 pts**
- VIX DOWN >2% in first hour (n=78): avg = **237.0 pts**
- VIX FLAT first hour (n=76): avg = **216.0 pts**

## VIX regime breakdown
```
            n_days  avg_nifty_range  median_nifty_range  avg_nifty_pct_chg  pct_up_days  pct_down_days  biggest_up_move  biggest_down_move
vix_regime                                                                                                                                
Elevated        72           274.59              256.67              -0.05        43.06          45.83             1.97              -2.23
High            64           327.25              292.90              -0.07        46.88          46.88             2.05              -5.10
Low            123           176.49              168.40              -0.01        39.02          45.53             1.09              -0.96
Normal         234           222.59              196.60              -0.04        39.32          43.59             1.75              -2.26
```

## Hourly average pattern (across all days)
```
      n_days  avg_nifty_pct_chg  avg_nifty_range  avg_vix_pct_chg
hour                                                             
9        491              -0.04           120.99             0.82
10       489              -0.00            89.31            -0.15
11       491               0.00            71.75            -0.01
12       491              -0.00            71.05             0.01
13       490               0.01            70.88            -0.12
14       490              -0.01            79.54            -0.17
15       489              -0.00            58.06            -0.31
```
