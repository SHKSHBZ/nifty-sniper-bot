# SENSEX Long Straddle — Variant Comparison

Capital ₹20,000/trade. Lot 20. Strike step 100.
Premium gate ₹10–₹50 per leg. Entry 14:50/15:00. Time exit 15:25.
Slippage ₹0.05/leg. Brokerage ₹120 round-trip.

## Variants

- **S0 hold**: no SL/TP
- **S1 TP+50%**: close both legs at +50% combined return
- **S2 SL−50%**: close both legs at −50% combined return
- **S3 TP+SL**: both bumpers

## Headline

```
    name  n_trades  wins  win_rate_%  total_pnl  avg_pnl  median_ret_%  best  worst  max_dd  exit_TP  exit_SL  exit_TIME  avg_minutes
 S0_hold        43    18        41.9     -68972    -1604         -48.0 82920 -18880  161165        0        0         43           29
 S1_tp50        43    18        41.9    -143411    -3335         -48.0 17884 -18880  145026       14        0         29           22
 S2_sl50        43    16        37.2     -10372     -241         -50.6 82920 -12389  131117        0       25         18           19
S3_tp_sl        43    16        37.2     -84811    -1972         -50.6 17884 -12389  114978       14       25          4           13
```
