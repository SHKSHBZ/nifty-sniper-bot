import os
import time
from dotenv import load_dotenv
from executor.upstox import UpstoxFeed
from upstox_auth import UpstoxAuth

load_dotenv(override=True)

class Debugger:
    def __init__(self):
        self.count = 0
        self.feed = None
        
    def on_tick(self, ws, ticks):
        for tick in ticks:
            print(f"RAW TICK: {tick}")
            self.count += 1
            if self.count >= 5:
                print("Captured 5 ticks. Disconnecting...")
                self.feed.disconnect()
                os._exit(0)

if __name__ == "__main__":
    auth = UpstoxAuth()
    api_key = auth.get_client_id()
    access_token = auth.get_access_token()
    
    if not api_key or not access_token:
        print("Auth failed.")
        os._exit(1)
        
    debugger = Debugger()
    feed = UpstoxFeed(api_key, access_token, ["NSE_INDEX|Nifty 50"])
    debugger.feed = feed
    feed.on_ticks = debugger.on_tick
    feed.connect()
    
    # Wait for ticks
    time.sleep(15)
    print("Timeout reached.")
    os._exit(1)
