import requests
import json
from upstox_auth import UpstoxAuth

def test_formats():
    auth = UpstoxAuth()
    access_token = auth.get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    # Try different expiry string formats for April 9, 2026
    # 26409 (YYMDD), 26APR, 09APR26, 26APR09
    test_formats = ["26409", "26APR", "09APR26", "26APR09", "26APR26"]
    
    to_date = "2026-04-06"
    from_date = "2026-04-06"
    
    for fmt in test_formats:
        instrument_key = f"NSE_FO|NIFTY{fmt}22650CE"
        url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/1minute/{to_date}/{from_date}"
        
        print(f"Testing format: {instrument_key}")
        resp = requests.get(url, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data", {}).get("candles"):
                print(f"SUCCESS! Found valid format: {instrument_key}")
                print(f"Sample data: {data['data']['candles'][0]}")
                return
            else:
                print(f"Key was accepted, but no data available.")
        else:
            print(f"Failed. Error: {resp.json()}")

if __name__ == "__main__":
    test_formats()
