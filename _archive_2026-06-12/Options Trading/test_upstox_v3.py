import os
import upstox_client
from upstox_client.rest import ApiException
from dotenv import load_dotenv
import time

load_dotenv()

def test_upstox_v3_streaming():
    api_key = os.getenv("UPSTOX_API_KEY")
    access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
    
    if not api_key or not access_token:
        print("Error: UPSTOX_API_KEY or UPSTOX_ACCESS_TOKEN missing in .env")
        return

    # Configure API client
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    
    # Initialize MarketDataStreamerV3
    streamer = upstox_client.MarketDataStreamerV3(api_client=upstox_client.ApiClient(configuration))

    def on_open():
        print("V3 Stream Connected!")
        # For V3, instrument keys should be a list of strings
        # "full" mode is for deep data
        streamer.subscribe(["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"], "full")

    def on_message(data):
        print(f"Market Message received: {data}")

    def on_error(error):
        print(f"Error: {error}")

    def on_close(code, reason):
        print(f"Connection closed: {code} - {reason}")

    # Set callbacks using the lowercase strings
    streamer.on("open", on_open)
    streamer.on("message", on_message)
    streamer.on("error", on_error)
    streamer.on("close", on_close)

    print("Attempting to connect to Upstox V3 Stream...")
    streamer.connect()
    
    # Keep the script running for a bit to receive data
    start_time = time.time()
    while time.time() - start_time < 20: # Wait for 20 seconds
        time.sleep(1)
        
    streamer.disconnect()

if __name__ == "__main__":
    test_upstox_v3_streaming()
