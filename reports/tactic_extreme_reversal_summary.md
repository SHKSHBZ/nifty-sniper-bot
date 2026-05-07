# Tactic 1: Spot-Extreme Reversal Scalper — Backtest

Capital Rs.20,000/trade. Lot 65. 
Premium gate Rs.20-Rs.100. 
TP +30.0% / SL -25.0% / time-stop 20m.
Range gate >30.0 pts in 5-min window.
Slippage Rs.0.05/leg. Brokerage Rs.60/trade.

## Headline

- Expiries: **46**
- Total trades: **226**
- Win rate: **88/226 = 38.9%**
- Total net P&L: **Rs.-162,215**
- Avg / trade: Rs.-717
- Avg / expiry: Rs.-3,526
- Max drawdown: Rs.172,429

## Per-expiry breakdown
```
   expiry  trades  wins    pnl
03_OCT_24       0     0      0
10_OCT_24       4     2   2771
17_OCT_24       3     0  -9754
24_OCT_24       3     0 -11464
31_OCT_24       3     1   -267
07_NOV_24       5     3   4614
14_NOV_24       3     0 -19443
21_NOV_24       4     1  -4595
28_NOV_24      12     4 -28221
05_DEC_24       0     0      0
12_DEC_24       1     1    213
19_DEC_24       0     0      0
02_SEP_25       7     3  -1154
09_SEP_25       0     0      0
16_SEP_25       0     0      0
23_SEP_25       6     4  10352
30_SEP_25       5     2  -3943
07_OCT_25       3     0 -14252
14_OCT_25      11     5  -8898
20_OCT_25       0     0      0
28_OCT_25      10     5   3807
04_NOV_25       2     0  -4829
11_NOV_25       6     2  -4871
18_NOV_25       1     0  -1945
25_NOV_25       3     0 -16010
02_DEC_25       1     0  -4285
09_DEC_25       7     2 -13803
16_DEC_25       2     2   4111
23_DEC_25       2     1   2252
30_DEC_25       3     1    172
06_JAN_26       2     0  -5388
13_JAN_26      11     5  -1813
20_JAN_26       2     0  -5476
27_JAN_26      12     6  -5049
03_FEB_26       8     2 -19349
10_FEB_26       4     3   3981
17_FEB_26       8     4   8503
24_FEB_26       9     4    386
02_MAR_26       2     2  13032
10_MAR_26      12     7  28539
17_MAR_26      17     5 -27416
24_MAR_26       0     0      0
30_MAR_26      14     6   1698
07_APR_26      13     3 -35808
13_APR_26       3     2   8302
21_APR_26       2     0  -6912
```

## By exit reason
```
              n      total          avg
exit_reason                            
SL           88 -508413.15 -5777.422159
TIME_STOP    80  -47373.05  -592.163125
TP           58  393571.20  6785.710345
```
