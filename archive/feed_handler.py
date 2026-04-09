import zmq
import json
import logging
import time
from executor.upstox_options import UpstoxOptionsFeed
from upstox_auth import UpstoxAuth
import yaml
import os
from dotenv import load_dotenv

logger = logging.getLogger("FeedPublisher")

class FeedHandler:
    """
    Hedge-Fund Grade Data Feed:
    Listens to Broker and publishes ticks to ZeroMQ for the Trading Brain.
    """
    def __init__(self, config):
        self.config = config
        
        # ZeroMQ Setup (PUB Pattern)
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind("tcp://*:5555") # Publishing port
        
        # Broker Setup
        self.broker_feed = self._init_broker()

    def _init_broker(self):
        load_dotenv(override=True)
        auth = UpstoxAuth()
        if not auth.is_session_valid():
            auth.authenticate()
        
        api_key = auth.get_client_id()
        access_token = auth.get_access_token()
        instruments = self.config.get('instruments', [])
        
        feed = UpstoxOptionsFeed(api_key, access_token, instruments)
        feed.on_ticks = self.on_market_tick
        return feed

    def on_market_tick(self, ws, ticks):
        """Standardizes and publishes ticks to the ZMQ queue."""
        for tick in ticks:
            # Send raw tick to ZeroMQ
            # The Trading Engine will handle normalization
            self.socket.send_json(tick)

    def run(self):
        logger.info("Starting Feed Publisher Service...")
        self.broker_feed.connect()
        while True:
            time.sleep(1)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    handler = FeedHandler(config)
    handler.run()
