# Intraday Bot Backtest Report 
**Date Tested:** Previous Full Market Session (2026-04-06)
**Strategy:** EMA Crossover (9/21) + RSI Momentum Filter
**Asset:** NIFTY 50 Options
**Capital Allocated:** ₹1,00,000 (1 Lot / 50 Qty)

### Performance Summary
* **Total Trades Taken:** 13
* **Target Hits (+30%):** 3
* **Stop Loss Hits (-15%):** 9
* **EOD Square-Off:** 1
* **Net P&L:** -₹4,500.00 (Due to heavy morning whipsawing)

### Detailed Execution Log

| Entry Time | Exit Time | Type | Nifty Strike | Entry Premium (Avg) | Exit Premium | Exit Reason | P&L (₹) | Account Balance (₹) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 09:17:00 | 09:20:00 | BUY PE | 22650 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 98875.0 |
| 09:30:00 | 09:33:00 | BUY PE | 22600 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 97750.0 |
| 09:35:00 | 09:41:00 | BUY CE | 22700 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 96625.0 |
| 09:52:00 | 10:12:00 | BUY PE | 22600 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 95500.0 |
| 10:14:00 | 10:26:00 | BUY CE | 22650 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 94375.0 |
| 10:34:00 | 11:03:00 | BUY CE | 22650 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 93250.0 |
| 11:08:00 | 11:37:00 | BUY PE | 22600 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 92125.0 |
| 11:38:00 | 12:20:00 | BUY CE | 22650 | 150.0 | 195.0 | TARGET HIT | 2250.0 | 94375.0 |
| 12:21:00 | 12:33:00 | BUY CE | 22700 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 93250.0 |
| 12:34:00 | 12:46:00 | BUY PE | 22650 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 92125.0 |
| 12:50:00 | 13:13:00 | BUY CE | 22750 | 150.0 | 195.0 | TARGET HIT | 2250.0 | 94375.0 |
| 13:20:00 | 15:02:00 | BUY CE | 22900 | 150.0 | 195.0 | TARGET HIT | 2250.0 | 96625.0 |
| 15:03:00 | 15:25:00 | BUY CE | 22950 | 150.0 | 135.0 | EOD SQUARE OFF | -1125.0 | 95500.0 |

> WARNING: The strategy suffered "whipsawing" (false breakouts) in the morning trading session because the market moved sideways, triggering multiple 15% stop-losses in a row. It recovered in the afternoon trend, scoring three +30% profit targets, but ended the day slightly red. This indicates the RSI volatility threshold needs to be tightened to avoid trading sideways markets.
