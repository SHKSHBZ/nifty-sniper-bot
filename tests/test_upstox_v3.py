import os
import requests
import upstox_client
from upstox_client.rest import ApiException
from dotenv import load_dotenv
import time
from datetime import datetime, timedelta
from upstox_auth import UpstoxAuth

load_dotenv()

def test_upstox_v3_streaming():
    print("Testing Upstox V3 Market Data Streamer (ATM ± 3 Strikes)...")
    
    # 1. Authenticate
    auth = UpstoxAuth()
    access_token = auth.get_access_token()
    
    if not access_token:
        print("Error: Could not retrieve Access Token. Please run 'python upstox_auth.py' first.")
        return

    # 2. Fetch Option Chain dynamically for the nearest valid expiry
    base_url = "https://api.upstox.com/v2"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    
    chain_data = None
    today = datetime.now()
    print("\nLooking for current Nifty 50 Option Chain...")
    
    for i in range(8):
        test_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        url = f"{base_url}/option/chain"
        params = {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": test_date}
        resp = requests.get(url, params=params, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if len(data) > 0:
                chain_data = data
                print(f"[OK] Found option chain for expiry: {test_date}")
                break

    if not chain_data:
        print("[ERROR] Could not find any option chain data for the next 7 days.")
        return

    # 3. Calculate ATM and target strikes
    spot_price = chain_data[0].get("underlying_spot_price", 0)
    print(f"\n[DATA] Nifty 50 Current Spot Price: {spot_price}")

    step = 50
    atm_strike = int(round(spot_price / step) * step)
    print(f"[TARGET] ATM Strike Calculated: {atm_strike}")

    # We want ATM, 3 below, and 3 above
    target_strikes = [
        atm_strike - 150, atm_strike - 100, atm_strike - 50,
        atm_strike,
        atm_strike + 50, atm_strike + 100, atm_strike + 150
    ]
    print(f"[STRIKES] Monitoring Strikes: {target_strikes}")

    # 4. Filter the exact instrument keys for CE and PE
    instrument_keys = ["NSE_INDEX|Nifty 50"] # Monitor the spot price too
    
    for item in chain_data:
        if item["strike_price"] in target_strikes:
            ce_key = item.get("call_options", {}).get("instrument_key")
            pe_key = item.get("put_options", {}).get("instrument_key")
            if ce_key: instrument_keys.append(ce_key)
            if pe_key: instrument_keys.append(pe_key)

    print(f"\n[ACTION] Subscribing to {len(instrument_keys)} instruments...")
    for k in instrument_keys:
        print(f"   -> {k}")

    # 5. Connect to Websocket Stream
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    streamer = upstox_client.MarketDataStreamerV3(api_client=upstox_client.ApiClient(configuration))

    def on_open():
        print("\n[STREAM] V3 Stream Connected!")
        streamer.subscribe(instrument_keys, "full")

    def on_message(data):
        try:
            if isinstance(data, dict):
                feeds = data.get("feeds", {})
                for key, feed_data in feeds.items():
                    # Upstox SDK wraps payload in 'fullFeed' instead of 'ff'
                    if 'fullFeed' in feed_data:
                        ff = feed_data['fullFeed']
                        if 'indexFF' in ff:
                            ltp = ff['indexFF'].get('ltpc', {}).get('ltp')
                            print(f"[SPOT] {key} -> LTP: {ltp}")
                        elif 'marketFF' in ff:
                            ltp = ff['marketFF'].get('ltpc', {}).get('ltp')
                            print(f"[OPTION] {key} -> LTP: {ltp}")
        except Exception as e:
            print(f"[ON_MESSAGE ERROR] {e}")

    def on_error(error):
        print(f"[STREAM ERROR] {error}")

    def on_close(code, reason):
        print(f"[STREAM CLOSED] Code: {code}, Reason: {reason}")

    # Set callbacks
    streamer.on("open", on_open)
    streamer.on("message", on_message)
    streamer.on("error", on_error)
    streamer.on("close", on_close)

    print("\nAttempting to connect to Upstox V3 Stream...")
    streamer.connect()
    
    # Keep running indefinitely until you press Ctrl+C
    print("\n[STREAM] Streaming indefinitely. Press Ctrl+C in terminal to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[STREAM] Manual stop requested by user...")
        pass
        
    print("\nDisconnecting stream...")
    streamer.disconnect()

if __name__ == "__main__":
    test_upstox_v3_streaming()
