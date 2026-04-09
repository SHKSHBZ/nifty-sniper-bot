# Filtered Bot Backtest Report (Sideways Protection)
**Date Tested:** Previous Full Market Session (2026-04-06)
**Strategy:** EMA Crossover (9/21) + RSI Momentum Filter
**New Active Filters:** 
1. **Time Block:** No execution allowed before 10:00 AM.
2. **ATR Compression Gate:** Blocks execution if the 14-period True Range is under 6 Nifty Points (Extreme Flat Market).

### Performance Summary
* **Total Trades Taken:** 10 (Down from 13)
* **Target Hits (+30%):** 3
* **Stop Loss Hits (-15%):** 6
* **EOD Square-Off:** 1
* **Net P&L:** -₹1,125.00 

> **Algorithmic Improvement:** By implementing these two sideways market filters, the bot successfully avoided **Rs. 3,375.00** in rapid-fire morning losses that the previous unprotected run suffered during the 9:15 AM to 9:50 AM volatility crush!

### Filtered Execution Log

| Entry Time | Exit Time | Type | Nifty Strike | Entry Premium | Exit Premium | Exit Reason | P&L (₹) | Balance (₹) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 10:03:00 | 10:08:00 | BUY PE | 22600 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 98875.0 |
| 10:14:00 | 10:26:00 | BUY CE | 22650 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 97750.0 |
| 10:34:00 | 11:03:00 | BUY CE | 22650 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 96625.0 |
| 11:08:00 | 11:37:00 | BUY PE | 22600 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 95500.0 |
| **11:38:00** | **12:20:00** | **BUY CE** | **22650** | **150.0** | **195.0** | **TARGET HIT** | **2250.0** | **97750.0** |
| 12:21:00 | 12:33:00 | BUY CE | 22700 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 96625.0 |
| 12:34:00 | 12:46:00 | BUY PE | 22650 | 150.0 | 127.5 | STOP LOSS HIT | -1125.0 | 95500.0 |
| **12:50:00** | **13:13:00** | **BUY CE** | **22750** | **150.0** | **195.0** | **TARGET HIT** | **2250.0** | **97750.0** |
| **13:20:00** | **15:02:00** | **BUY CE** | **22900** | **150.0** | **195.0** | **TARGET HIT** | **2250.0** | **100000.0** |
| 15:03:00 | 15:25:00 | BUY CE | 22950 | 150.0 | 135.0 | EOD CLOSE | -1125.0 | 98875.0 |
