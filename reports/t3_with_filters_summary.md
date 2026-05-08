# T3 with Pillar Filters — Comparison

Base config: camarilla_3, tolerance 0.25%, ATM±2 ITM strike.

## Results
```
        variant   n  win_rate  total  avg     dd
    V0_baseline 449      23.6  36433   81  91622
V1_candles_only 377      22.8 -55280 -146 133726
    V2_vix_only 309      26.2  33555  108  51716
        V3_both 248      25.0  -2199   -8  68680
```

Variants:
- V0: no filter (baseline)
- V1: + Pillar 1 (candle pattern at 5-min bar)
- V2: + Pillar 5 (VIX direction agrees)
- V3: both filters
