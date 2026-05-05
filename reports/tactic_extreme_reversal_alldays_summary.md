# Tactic 1 - All Trading Days Backtest

Spot range: 2024-01-01 -> 2026-04-27
Capital Rs.20,000/trade. Lot 65. Premium gate Rs.10-Rs.200.
TP +30.0% / SL -25.0% / time-stop 20m.
Range gate >30.0 pts in 5-min window.

## Headline

- Trading days tested:   **489**
- Days with trades:      190
- Total trades:          **912**
- Win rate:              **405/912 = 44.4%**
- Total net P&L:         **Rs.-244,061**
- Avg / trade:           Rs.-267
- Avg / day-with-trades: Rs.-1,284
- Max daily drawdown:    Rs.247,383

## By exit reason
```
             count        sum         mean
exit_reason                               
SL             146 -765716.20 -5244.631507
TIME_STOP      661 -127872.15  -193.452572
TP             105  649526.60  6185.967619
```

## By days-to-expiry (DTE 0 = expiry day)
```
                count        sum         mean
days_to_expiry                               
0                 243 -152721.25  -628.482510
1                 162   18073.35   111.563889
2                  86  -22253.05  -258.756395
3                  84  -38448.05  -457.714881
4                 112   -5905.55   -52.728125
5                 100  -29853.70  -298.537000
6                 114  -11087.75   -97.260965
7                   4    -730.75  -182.687500
8                   1     538.00   538.000000
11                  1    -180.25  -180.250000
12                  1   -2062.00 -2062.000000
13                  3     752.75   250.916667
14                  1    -183.50  -183.500000
```
