# VIX-Direction Signal — TUNED Backtest

Changes vs baseline: skip High VIX, TP 30% (was 50%), trailing breakeven at +15%.

Decision 10:30 IST. Capital Rs.20,000. Lot 65. TP +30.0% / SL -30.0%.

## Headline

- Signals fired:    **11**
- Win rate:         **6/11 = 54.5%**
- Trail armed:      8/11
- Total net P&L:    **Rs.25,277**
- Avg / trade:      Rs.2,297
- Max drawdown:     Rs.5,071

## By signal
```
        n  wins  win_rate  total_pnl  avg_pnl
signal                                       
BUY_CE  5     3      60.0     4738.0    948.0
BUY_PE  6     3      50.0    20540.0   3423.0
```

## By exit reason
```
             count      sum    mean
exit_reason                        
BE_STOP          2   -653.0  -326.0
EOD              1   1695.0  1695.0
SL               3 -14100.0 -4700.0
TP               5  38335.0  7667.0
```

## By DTE
```
     count      sum    mean
dte                        
0        4  32188.0  8047.0
1        3 -10496.0 -3498.0
2        1   -222.0  -222.0
3        2   2113.0  1056.0
4        1   1695.0  1695.0
```
