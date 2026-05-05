# Expiry Gamma Hero — Variant Comparison

Held constant: capital ₹20k, lot 65, premium ₹5–20, time-exit 15:25,
₹0.05/leg slippage, ₹60 round-trip brokerage.

## Variants

- **V0 baseline**: OI ratio at ATM+50, exit when spot crosses ATM
- **V1 wider OI**: OI ratio at the ATM strike itself, exit when spot crosses ATM
- **V2 wider stop**: OI at ATM+50, exit only after spot moves 30 pts past ATM
- **V3 both**: OI at ATM, exit only after spot moves 30 pts past ATM

## Headline

```
         name  n_trades  wins  win_rate_%  total_pnl  avg_pnl  median_ret_%   best  worst  max_dd ce_pe  time_exits  avg_minutes
  V0_baseline        33    10        30.3      51118     1549         -14.2 129862 -15907   86316  0/33           4            5
  V1_wider_OI        19     8        42.1      23157     1218         -11.5  33970 -15348   34612  8/11           7           10
V2_wider_stop        33     9        27.3    -125363    -3798         -84.8 129862 -20015  183585  0/33          18           23
      V3_both        19    10        52.6       8637      454          11.8  33970 -20041   68603  8/11          15           22
```
