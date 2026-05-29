# India VIX vs NIFTY — Relationship Analysis

Trading days: **246**  (spot 2025-05-26 -> 2026-05-21)
VIX range: **9.13 - 27.76** (median 12.16)
NIFTY range: **22,379 - 26,335**

## Same-day correlation

- VIX % change vs NIFTY % change (same day): **-0.304**
  - Strong negative (-0.6 to -0.8 expected): textbook fear-gauge
  - Near zero: weakly related, day-to-day noise
- VIX today vs NIFTY tomorrow: 0.127
  - If significantly negative -> VIX leads NIFTY by 1 day

## First-hour VIX as predictor of full-day range

- Correlation: **0.219**
- VIX UP >2% in first hour (n=78): avg full-day range = **263.0 pts**
- VIX DOWN >2% in first hour (n=42): avg = **226.0 pts**
- VIX FLAT first hour (n=37): avg = **213.0 pts**

## VIX regime breakdown
```
            n_days  avg_nifty_range  median_nifty_range  avg_nifty_pct_chg  pct_up_days  pct_down_days  biggest_up_move  biggest_down_move
vix_regime                                                                                                                                
Elevated        18           285.50              269.03               0.06        55.56          33.33             1.07              -2.23
High            46           314.94              303.05              -0.02        50.00          47.83             1.42              -1.58
Low            107           176.46              168.55              -0.01        40.19          46.73             0.95              -0.96
Normal          75           228.89              209.00              -0.09        40.00          46.67             1.40              -2.26
```

## Hourly average pattern (across all days)
```
      n_days  avg_nifty_pct_chg  avg_nifty_range  avg_vix_pct_chg
hour                                                             
9        245              -0.03           124.76             0.91
10       245              -0.00            83.34            -0.18
11       245               0.01            69.90            -0.08
12       245               0.02            70.99            -0.01
13       246              -0.02            71.88            -0.02
14       246               0.01            80.24            -0.30
15       245              -0.02            56.80            -0.27
```
