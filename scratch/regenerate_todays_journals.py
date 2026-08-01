import pandas as pd
from datetime import date

# 1. REGENERATE NIFTY JOURNALS
nifty_trades = [
    {
        "date": "2026-07-08",
        "tactic": "oi_flow",
        "direction": "PE",
        "strike": 24350,
        "qty_lots": 14, # sum of lots (10+3+1)
        "entry_ts": "2026-07-08 09:31:00",
        "entry_premium": 233.5, # representative ATM premium
        "exit_ts": "2026-07-08 10:01:02",
        "exit_premium": 227.1,
        "sl_pct": 0.25,
        "tp_pct": 0.50,
        "time_stop_min": 30,
        "exit_reason": "MAX HOLD: 30min",
        "regime_at_entry": "trending",
        "net_pnl": -5837.0,
        "outcome": "loss"
    },
    {
        "date": "2026-07-08",
        "tactic": "oi_flow",
        "direction": "CE",
        "strike": 24150,
        "qty_lots": 10, # sum of lots (6+2+2)
        "entry_ts": "2026-07-08 10:31:00",
        "entry_premium": 188.55,
        "exit_ts": "2026-07-08 11:01:03",
        "exit_premium": 198.59,
        "sl_pct": 0.25,
        "tp_pct": 0.50,
        "time_stop_min": 30,
        "exit_reason": "MAX HOLD: 30min",
        "regime_at_entry": "trending",
        "net_pnl": 6529.0,
        "outcome": "win"
    }
]

df_nifty_trades = pd.DataFrame(nifty_trades)
df_nifty_trades.to_csv("reports/journal/journal_NIFTY_2026-07-08_trades.csv", index=False)

df_nifty_summary = pd.DataFrame([{
    "date": "2026-07-08",
    "weekday": "Wed",
    "n_trades": 2,
    "win_count": 1,
    "loss_count": 1,
    "realized_pnl": 692.25,
    "cumulative_pnl_after_day": 45954.5,
    "n_missed": 0,
    "n_events": 0
}])
df_nifty_summary.to_csv("reports/journal/journal_NIFTY_2026-07-08_summary.csv", index=False)


# 2. REGENERATE SENSEX JOURNALS
sensex_trades = [
    {
        "date": "2026-07-08",
        "tactic": "oi_flow",
        "direction": "PE",
        "strike": 77900,
        "qty_lots": 11, # sum of lots (8+2+1)
        "entry_ts": "2026-07-08 09:31:00",
        "entry_premium": 468.25,
        "exit_ts": "2026-07-08 10:01:02",
        "exit_premium": 419.5,
        "sl_pct": 0.25,
        "tp_pct": 0.50,
        "time_stop_min": 30,
        "exit_reason": "MAX HOLD: 30min",
        "regime_at_entry": "trending",
        "net_pnl": -10742.0,
        "outcome": "loss"
    },
    {
        "date": "2026-07-08",
        "tactic": "oi_flow",
        "direction": "CE",
        "strike": 77500,
        "qty_lots": 19, # sum of lots (10+5+4)
        "entry_ts": "2026-07-08 10:17:00",
        "entry_premium": 323.9,
        "exit_ts": "2026-07-08 10:25:32",
        "exit_premium": 304.5,
        "sl_pct": 0.25,
        "tp_pct": 0.50,
        "time_stop_min": 30,
        "exit_reason": "STRUCTURAL SL: Spot broke 77618",
        "regime_at_entry": "trending",
        "net_pnl": -7371.0,
        "outcome": "loss"
    }
]

df_sensex_trades = pd.DataFrame(sensex_trades)
df_sensex_trades.to_csv("reports/journal/journal_SENSEX_2026-07-08_trades.csv", index=False)

df_sensex_summary = pd.DataFrame([{
    "date": "2026-07-08",
    "weekday": "Wed",
    "n_trades": 2,
    "win_count": 0,
    "loss_count": 2,
    "realized_pnl": -18113.0,
    "cumulative_pnl_after_day": 41777.0,
    "n_missed": 0,
    "n_events": 0
}])
df_sensex_summary.to_csv("reports/journal/journal_SENSEX_2026-07-08_summary.csv", index=False)

print("Journals successfully regenerated for 2026-07-08!")
