# T3 — Support/Resistance Bounce Reversal

Tested 15 variants across 489 trading days.
Strike: ATM±2 ITM (100pts ITM).

## Sweep results (sorted by total P&L)
```
     method  tol   n  win_rate   total  avg     dd
camarilla_3 0.25 449      23.6   36433   81  91622
       or30 0.25 458      26.9   24719   53  77239
camarilla_3 0.15 396      21.5   19946   50  87138
camarilla_3 0.40 459      27.7    2450    5  74284
       or30 0.40 481      28.5   -1022   -2 120548
camarilla_4 0.15 299      24.1  -30239 -101  79796
  classic_1 0.15 334      23.4  -34608 -103  91649
camarilla_4 0.40 404      30.0  -36812  -91 111013
camarilla_4 0.25 348      27.9  -53000 -152 123786
       or30 0.15 458      23.6  -53664 -117 108373
    prev_hl 0.40 423      26.0  -55145 -130  92052
  classic_1 0.40 430      26.3  -79309 -184 144020
  classic_1 0.25 379      24.3 -113140 -298 152899
    prev_hl 0.15 374      19.8 -136100 -363 143723
    prev_hl 0.25 394      22.8 -150640 -382 182032
```

**Best**: camarilla_3@0.25 = Rs.36,433
