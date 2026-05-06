# VIX Regime Strategy Logic

## Hypothesis check

```
            n_days  avg_vix  pct_up_days  pct_down_days  pct_flat_days  avg_pct_chg  median_pct_chg  avg_range
vix_regime                                                                                                    
Elevated        72    16.05        43.06          45.83          11.11        -0.05           -0.01     274.59
High            64    21.19        46.88          46.88           6.25        -0.07           -0.00     327.25
Low            123    10.92        39.02          45.53          15.45        -0.01           -0.05     176.49
Normal         234    13.45        39.32          43.59          17.09        -0.04           -0.04     222.59
```

## Which leg won by regime

```
winning_leg  CE  FLAT  PE  total  CE_pct  PE_pct
vix_regime                                      
Elevated      5     0  13     18    27.8    72.2
High         11     0  10     21    52.4    47.6
Low          42     4  42     88    47.7    47.7
Normal       38     1  47     86    44.2    54.7
```

## VIX direction vs NIFTY direction (same-day)

```
nifty_dir  DOWN  FLAT   UP  All
vix_dir                        
VIX_DOWN     75    36  110  221
VIX_FLAT     36    15   34   85
VIX_UP      110    20   57  187
All         221    71  201  493
```

## Combined signal table

```
                   n_days  pct_up  pct_down  avg_chg  avg_range
combined                                                       
Elevated_VIX_UP        31   16.13     70.97    -0.51     303.21
High_VIX_UP            34   26.47     64.71    -0.45     337.82
Normal_VIX_UP          86   36.05     51.16    -0.24     245.07
Low_VIX_UP             36   33.33     61.11    -0.15     209.11
Low_VIX_FLAT           25   32.00     36.00    -0.03     164.39
Normal_VIX_FLAT        39   30.77     53.85    -0.02     203.80
Low_VIX_DOWN           62   45.16     40.32     0.07     162.42
Normal_VIX_DOWN       109   44.95     33.94     0.10     211.57
Elevated_VIX_FLAT      13   61.54     30.77     0.22     245.28
Elevated_VIX_DOWN      28   64.29     25.00     0.33     256.51
High_VIX_DOWN          22   68.18     27.27     0.35     333.47
High_VIX_FLAT           8   75.00     25.00     0.41     265.26
```
