# VIX-Direction Signal Backtest

Decision at 10:30 IST. Entry: ATM CE (if VIX falling) or ATM PE (if VIX rising), only when VIX >= 15.
Capital Rs.20,000. Lot 65.
TP +50.0% / SL -30.0% / EOD 15:25.

## Headline

- Signals fired:    **14**
- Win rate:         **6/14 = 42.9%**
- Total net P&L:    **Rs.5,897**
- Avg / trade:      Rs.421
- Max drawdown:     Rs.27,494

## By signal
```
        n  wins  win_rate_pct  total_pnl  avg_pnl
signal                                           
BUY_CE  8     3          38.0    -6658.0   -832.0
BUY_PE  6     3          50.0    12556.0   2093.0
```

## By VIX regime
```
             n  win_rate_pct  total_pnl  avg_pnl
vix_regime                                      
Elevated    11          54.0    24261.0   2206.0
High         3           0.0   -18364.0  -6121.0
```

## By exit reason
```
             count      sum    mean
exit_reason                        
EOD              1   1695.0  1695.0
SL               8 -44810.0 -5601.0
TP               5  49012.0  9802.0
```

## By DTE
```
     count     sum    mean
dte                       
0        7  6379.0   911.0
1        3 -3001.0 -1000.0
2        1 -5169.0 -5169.0
3        2  5993.0  2997.0
4        1  1695.0  1695.0
```
