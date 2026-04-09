import requests
from upstox_auth import UpstoxAuth
import json

auth = UpstoxAuth()
token = auth.get_access_token()

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
url = "https://api.upstox.com/v2/option/contract?instrument_key=NSE_INDEX|Nifty 50"
resp = requests.get(url, headers=headers)
print(resp.status_code)
data = resp.json()
print("Expiries:", data.get("data", [{}])[0].get("expiry")) # Just to see structure
