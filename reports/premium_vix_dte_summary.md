# Premium behaviour: VIX regime x DTE x straddle

Days analysed: **213**

ATM straddle entered at 09:30 IST, exited at 15:25 IST.
Capital Rs.20,000/trade. Lot 65.

## By VIX regime
```
            n_days  avg_vix  avg_straddle_in  avg_straddle_peak  avg_straddle_out  avg_peak_uplift  avg_pnl_hold  avg_pnl_peak  pct_winners_hold
vix_regime                                                                                                                                      
Elevated        18     16.0            264.3              341.8             271.0             34.0        -361.4        3877.8              16.7
High            21     22.1            418.6              486.3             441.0             20.1         315.9        1111.3              14.3
Low             88     10.8            190.6              216.5             177.5             17.5       -1712.0        2548.9              28.4
Normal          86     13.4            257.4              303.8             252.2             21.7         -78.2        2617.9              32.6
```

## By Days-to-Expiry
```
     n_days  avg_vix  avg_straddle_in  avg_straddle_out  avg_peak_uplift  avg_pnl_hold  pct_winners_hold
dte                                                                                                     
0        42     13.8            129.1             106.3             42.7       -3142.4              33.3
1        35     13.0            186.9             174.4             17.4        -811.2              28.6
2        13     14.5            279.3             308.4             24.2        1073.7              38.5
3        15     15.0            307.0             298.8             20.2        -180.8              26.7
4        31     12.7            240.5             245.8             16.3          63.6              35.5
5        30     13.5            297.6             299.6             13.6        -192.4              23.3
6        37     13.2            331.3             332.3             12.7         -43.8              21.6
7         2     12.0            297.7             291.5              6.4        -570.0               0.0
8         2     12.1            327.9             303.8              0.4        -786.0               0.0
9         1     13.4            402.0             378.8              2.2           0.0               0.0
10        1     13.8            442.2             413.0              2.6           0.0               0.0
11        1     11.7            335.0             328.4              2.0           0.0               0.0
12        1     11.4            373.4             348.0              2.4           0.0               0.0
13        1     11.8            383.6             404.1              8.3           0.0               0.0
14        1     11.8            423.2             418.0              3.9           0.0               0.0
```

## VIX regime x DTE pivot (avg straddle P&L)
```
dte             0       1       2       3      4      5      6       7       8    9    10   11   12   13   14
vix_regime                                                                                                   
Elevated    -114.0 -1560.0   134.0 -1079.0    0.0    0.0    0.0     NaN     NaN  NaN  NaN  NaN  NaN  NaN  NaN
High        1379.0  -131.0     NaN     0.0    0.0    0.0    0.0     NaN     NaN  NaN  NaN  NaN  NaN  NaN  NaN
Low        -7643.0   -78.0 -4205.0  1433.0 -224.0 -732.0 -532.0     0.0 -1572.0  NaN  NaN  0.0  0.0  0.0  0.0
Normal     -1060.0 -1584.0  2204.0   -91.0  667.0  447.0  459.0 -1140.0     0.0  0.0  0.0  NaN  NaN  NaN  NaN
```
