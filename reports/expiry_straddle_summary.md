# Expiry Long Straddle — Variant Comparison

Capital ₹20,000/trade split equally across CE+PE
(equal lots both legs). Lot 65. Premium gate ₹5–₹20 per leg. Entry windows 14:50 / 15:00. Time exit 15:25.
Slippage ₹0.05/leg. Brokerage ₹120 round-trip.

## Variants

- **S0 hold**: no SL/TP, square off at 15:25
- **S1 TP+50%**: close both legs when combined premium up 50%
- **S2 SL−50%**: close both legs when combined premium down 50%
- **S3 TP+SL**: both bumpers active

## Headline

```
    name  n_trades  wins  win_rate_%  total_pnl  avg_pnl  median_ret_%  best  worst  max_dd  exit_TP  exit_SL  exit_TIME  avg_minutes
 S0_hold        18     9        50.0      52832     2935          -1.1 59290 -19051   30429        0        0         18           31
 S1_tp50        18     9        50.0       4433      246           4.8 18112 -19051   30429        8        0         10           23
 S2_sl50        18     9        50.0      67997     3777          -1.1 59290 -11004   21472        0        6         12           24
S3_tp_sl        18     9        50.0      19598     1088           4.8 18112 -11004   21472        8        6          4           16
```
