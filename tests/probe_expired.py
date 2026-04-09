import requests
import json
from upstox_auth import UpstoxAuth

def probe():
    auth = UpstoxAuth()
    access_token = auth.get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    print("--- 2. Probing Option Contracts ---")
    url = "https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key=NSE_INDEX|Nifty 50&expiry_date=2024-10-10"
    resp = requests.get(url, headers=headers)
    print("Status:", resp.status_code)
    try:
        data = resp.json()
        print(json.dumps(data)[:500])
    except:
        pass

if __name__ == "__main__":
    probe()
