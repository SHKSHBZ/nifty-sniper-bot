import pandas as pd
import requests
import io
import gzip

def fetch_upstox_instrument_keys():
    print("Downloading master instrument list from Upstox to find REAL option keys...")
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE_FO.csv.gz"
    response = requests.get(url)
    
    with gzip.open(io.BytesIO(response.content), 'rt') as f:
        df = pd.read_csv(f)
        
    nifty_options = df[(df['name'] == 'NIFTY') & (df['instrument_type'].isin(['OPTIDX']))].copy()
    
    # Show us the exact instrument keys for Nifty strikes around 22600-22700 for the current expiry
    sample = nifty_options[nifty_options['strike'].between(22600, 22700)].head(10)
    print("Exact Instrument Keys for NIFTY Options:")
    print(sample[['instrument_key', 'tradingsymbol', 'expiry', 'strike', 'instrument_type']].to_string())

if __name__ == "__main__":
    fetch_upstox_instrument_keys()
