# Institutional 'Real-Data' Backtest Report
**Date Tested:** Monday, March 30, 2026 (Nifty Expiry Day)
**Data Source:** True Downloaded Upstox CSV Option Contracts (0 DTE)
**Strategy:** EMA Crossover (9/21) + RSI (14) + ATR Compression Filter
**Capital Allocated:** ₹1,00,000 (Trade Size: 1 Lot / 65 Qty)

## The '0 DTE' Expiry Day Reality Check
We just crossed the threshold from amateur simulation into quantitative reality. By feeding the absolute physical Option Premiums into the backtester, we exposed the brutal nature of "Zero Days To Expiry" (0 DTE) option trading.

### Performance Summary
* **Total True Trades:** 38
* **Target Hits (+30%):** 10
* **Stop Loss Hits (-15%):** 27
* **EOD Square-Off:** 1
* **Net P&L:** -₹2,205.29

> WARNING: Because it's Expiry Day, the option premiums were so cheap and volatile in the afternoon (e.g. trading at Rs. 13.00, Rs. 7.00, Rs. 1.05) that a tiny 2-rupee tick movement instantly triggered a mathematically perfect +30% Profit Target or a -15% Stop Loss within seconds! This caused the bot to execute 38 rapid-fire algorithmic scalp trades. 

### Exhaustive Real-Execution Log
Below are the final hour's hyper-active expiry day scalp trades dynamically executed down to the last penny:

| In Time | Out Time | Type | Strike | In Prem (₹) | Out Prem (₹) | Reason | P&L (₹) | Balance (₹) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 14:12:00 | 14:15:00 | BUY PE | 22450 | 48.10 | 62.53 | TARGET HIT | 937.95 | 97351.90 |
| 14:16:00 | 14:20:00 | BUY PE | 22400 | 31.15 | 26.48 | STOP LOSS HIT | -303.71 | 97048.19 |
| 14:21:00 | 14:23:00 | BUY PE | 22400 | 35.30 | 45.89 | TARGET HIT | 688.35 | 97736.54 |
| 14:24:00 | 14:31:00 | BUY PE | 22400 | 45.55 | 38.72 | STOP LOSS HIT | -444.11 | 97292.43 |
| 14:32:00 | 14:34:00 | BUY PE | 22400 | 40.30 | 52.39 | TARGET HIT | 785.85 | 98078.28 |
| 14:35:00 | 14:36:00 | BUY PE | 22350 | 30.75 | 26.14 | STOP LOSS HIT | -299.81 | 97778.46 |
| 14:46:00 | 14:48:00 | BUY PE | 22350 | 23.70 | 30.81 | TARGET HIT | 462.15 | 97507.90 |
| 14:55:00 | 14:57:00 | BUY PE | 22350 | 23.00 | 29.90 | TARGET HIT | 448.50 | 97956.40 |
| 15:00:00 | 15:01:00 | BUY PE | 22300 | 13.00 | 11.05 | STOP LOSS HIT | -126.75 | 97829.65 |
| 15:02:00 | 15:03:00 | BUY PE | 22300 | 10.55 | 13.72 | TARGET HIT | 205.73 | 98035.38 |
| 15:10:00 | 15:11:00 | BUY PE | 22300 | 7.05 | 5.99 | STOP LOSS HIT | -68.74 | 97572.25 |
| 15:12:00 | 15:13:00 | BUY PE | 22350 | 25.25 | 32.83 | TARGET HIT | 492.38 | 98064.63 |
| 15:17:00 | 15:18:00 | BUY CE | 22350 | 1.05 | 1.37 | TARGET HIT | 20.48 | 97801.38 |
| 15:19:00 | 15:20:00 | BUY CE | 22350 | 0.40 | 0.35 | EOD SQUARE OFF | -3.25 | 97798.13 |

 *(To view all 38 trades, please check TrueData_Backtest_Report.xlsx in your folder.)*
