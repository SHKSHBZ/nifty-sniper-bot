import os
import csv
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
from kiteconnect import KiteTicker, KiteConnect

load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ZerodhaFeed:
    def __init__(self, api_key, access_token, instruments, csv_export=True, csv_path="data/ticks.csv"):
        self.api_key = api_key
        self.access_token = access_token
        self.instruments = instruments  # List of instrument tokens
        self.csv_export = csv_export
        self.csv_path = csv_path
        self.kws = KiteTicker(api_key, access_token)
        self._setup_callbacks()
        
        if self.csv_export:
            self._init_csv()

    def _init_csv(self):
        # Create data directory if not exists
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        # Check if file exists to write header
        if not os.path.isfile(self.csv_path):
            with open(self.csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'instrument_token', 'last_price', 'ohlc', 'change', 'tradable', 'mode', 'volume', 'buy_quantity', 'sell_quantity', 'depth'])

    def _setup_callbacks(self):
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        self.kws.on_reconnect = self.on_reconnect
        self.kws.on_noreconnect = self.on_noreconnect

    def on_ticks(self, ws, ticks):
        if self.csv_export:
            self._write_ticks_to_csv(ticks)
        # Here we'll pass ticks to the orderbook parser in the future
        logger.debug(f"Received ticks: {len(ticks)}")

    def _write_ticks_to_csv(self, ticks):
        with open(self.csv_path, 'a', newline='') as f:
            writer = csv.writer(f)
            for tick in ticks:
                # Basic fields, can be expanded based on need
                writer.writerow([
                    datetime.now().isoformat(),
                    tick.get('instrument_token'),
                    tick.get('last_price'),
                    json.dumps(tick.get('ohlc')),
                    tick.get('change'),
                    tick.get('tradable'),
                    tick.get('mode'),
                    tick.get('volume'),
                    tick.get('buy_quantity'),
                    tick.get('sell_quantity'),
                    json.dumps(tick.get('depth'))
                ])

    def on_connect(self, ws, response):
        logger.info("Successfully connected to Zerodha WebSocket.")
        ws.subscribe(self.instruments)
        ws.set_mode(ws.MODE_FULL, self.instruments)

    def on_close(self, ws, code, reason):
        logger.warning(f"WebSocket closed: {reason} (code: {code})")

    def on_error(self, ws, code, reason):
        logger.error(f"WebSocket error: {reason} (code: {code})")

    def on_reconnect(self, ws, attempts_count):
        logger.info(f"Reconnecting... (attempt: {attempts_count})")

    def on_noreconnect(self, ws):
        logger.error("Failed to reconnect after multiple attempts.")

    def connect(self):
        # This will block the thread
        self.kws.connect(threaded=False)

if __name__ == "__main__":
    # Test stub for initialization
    API_KEY = os.getenv("KITE_API_KEY")
    ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
    
    # Example tokens (Nifty & Bank Nifty) - these should be fetched dynamically
    # INSTRUMENTS = [256265, 260105] 
    
    # if API_KEY and ACCESS_TOKEN:
    #     feed = ZerodhaFeed(API_KEY, ACCESS_TOKEN, INSTRUMENTS)
    #     feed.connect()
    # else:
    #     logger.error("API_KEY or ACCESS_TOKEN missing in environment.")
