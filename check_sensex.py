import os, requests, pandas as pd
from datetime import datetime, date
from upstox_auth import UpstoxAuth

auth = UpstoxAuth()
headers = auth.headers if hasattr(auth, 'headers') else {'Authorization': f'Bearer {auth.access_token}', 'Accept': 'application/json'}

SPOT_URL = 'https://api.upstox.com/v2/historical-candle/intraday/{key}/{interval}/{to}/{frm}'
SENSEX_KEY = 'BSE_INDEX|SENSEX'

to_str = '2026-06-20T00:00:00+05:30'
from_str = '2026-06-19T00:00:00+05:30'

url = SPOT_URL.format(key=SENSEX_KEY, interval='1minute', to=to_str, frm=from_str)
resp = requests.get(url, headers=headers, timeout=15)

if resp.status_code == 200:
    data = resp.json().get('data', {}).get('candles', [])
    if data:
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        at_3pm = df.between_time('14:50', '15:20')
        print('SENSEX Spot around 3 PM on June 19:')
        print(at_3pm[['close']])
        
        atm_strike = round(at_3pm.iloc[0]['close'] / 100) * 100
        print(f'\nATM Strike at 3 PM: {atm_strike}')
        
        # Now fetch the options for this strike
        CE_KEY = f'BFO|SENSEX26JUN19{atm_strike}CE'
        PE_KEY = f'BFO|SENSEX26JUN19{atm_strike}PE'
        
        for key in [CE_KEY, PE_KEY]:
            url_opt = SPOT_URL.format(key=key, interval='1minute', to=to_str, frm=from_str)
            r = requests.get(url_opt, headers=headers, timeout=15)
            if r.status_code == 200:
                opt_data = r.json().get('data', {}).get('candles', [])
                if opt_data:
                    odf = pd.DataFrame(opt_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
                    odf['timestamp'] = pd.to_datetime(odf['timestamp'])
                    odf.set_index('timestamp', inplace=True)
                    odf.sort_index(inplace=True)
                    opt_3pm = odf.between_time('14:50', '15:30')
                    print(f'\n{key} around 3 PM:')
                    print(opt_3pm[['close']])
                else:
                    print(f'No data for {key}')
            else:
                print(f'Error fetching {key}')
    else:
        print('No spot data returned.')
else:
    print(f'API Error: {resp.status_code}')

