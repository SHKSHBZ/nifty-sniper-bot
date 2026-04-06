import os
import json
import logging
import upstox_client
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class UpstoxFeed:
    def __init__(self, api_key, access_token, instruments):
        self.api_key = api_key
        self.access_token = access_token
        self.instruments = instruments # List of strings e.g. ["NSE_INDEX|Nifty 50"]
        self.is_connected = False
        
        # Configure Upstox client
        self.configuration = upstox_client.Configuration()
        self.configuration.access_token = self.access_token
        self.api_client = upstox_client.ApiClient(self.configuration)
        
        # Initialize Streamer V3
        self.streamer = upstox_client.MarketDataStreamerV3(api_client=self.api_client)
        self.on_ticks = None # Callback to be assigned by MainOrchestrator
        
        self._setup_callbacks()

    def _setup_callbacks(self):
        self.streamer.on("open", self._on_open)
        self.streamer.on("message", self._on_message)
        self.streamer.on("error", self._on_error)
        self.streamer.on("close", self._on_close)

    def _on_open(self):
        logger.info("Upstox V3 Stream Connected.")
        # Subscribe to instruments in full mode
        self.streamer.subscribe(self.instruments, "full")

    def _on_message(self, data):
        # Normalize Upstox message to our system's tick format if possible
        if self.on_ticks:
            if hasattr(data, 'to_dict'):
                data = data.to_dict()
            
            if isinstance(data, dict):
                # If it has a 'feeds' key, it's a multi-instrument tick
                if 'feeds' in data:
                    # Flatten 'feeds' dict into a list of single-instrument dicts
                    # This allows MainOrchestrator to process each one simply
                    ticks = []
                    for key, value in data['feeds'].items():
                        ticks.append({key: value})
                    self.on_ticks(None, ticks)
                else:
                    self.on_ticks(None, [data])
            else:
                logger.warning(f"Unexpected data type from Upstox stream: {type(data)}")

    def _on_error(self, error):
        logger.error(f"Upstox Stream Error: {error}")

    def _on_close(self, code, reason):
        logger.warning(f"Upstox Stream Closed: {code} - {reason}")

    def connect(self):
        logger.info("Connecting to Upstox V3 Stream...")
        self.streamer.connect()

    def disconnect(self):
        self.streamer.disconnect()

if __name__ == "__main__":
    # Test stub
    API_KEY = os.getenv("UPSTOX_API_KEY")
    ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")
    
    if API_KEY and ACCESS_TOKEN:
        feed = UpstoxFeed(API_KEY, ACCESS_TOKEN, ["NSE_INDEX|Nifty 50"])
        feed.on_ticks = lambda ws, ticks: print(f"Received {len(ticks)} ticks")
        feed.connect()
        import time
        time.sleep(5)
        feed.disconnect()
    else:
        logger.error("Upstox credentials missing.")
