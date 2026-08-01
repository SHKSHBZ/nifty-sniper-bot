import pandas as pd
df = pd.read_csv('logs/macro_nifty_expiry_2026-06-23.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])
df.set_index('timestamp', inplace=True)
resampled = df.resample('15Min').last().dropna()
out = resampled[['spot', 'focus_ce_oi_change', 'focus_pe_oi_change']]
print('| Time | Spot | Focus CE OI Change | Focus PE OI Change |')
print('|---|---|---|---|')
for i, row in out.iterrows():
    print(f'| {i.strftime("%H:%M")} | {row["spot"]:.1f} | {row["focus_ce_oi_change"]:,.0f} | {row["focus_pe_oi_change"]:,.0f} |')
