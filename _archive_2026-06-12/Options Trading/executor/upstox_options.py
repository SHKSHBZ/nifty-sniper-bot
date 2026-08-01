import os
import time
import logging
import threading
from executor.upstox import UpstoxFeed
from chain_selector import ChainSelector

logger = logging.getLogger(__name__)

class UpstoxOptionsFeed(UpstoxFeed):
    def __init__(self, api_key, access_token, underlying_instruments):
        super().__init__(api_key, access_token, underlying_instruments)
        self.underlyings = underlying_instruments
        self.selector = ChainSelector(self.api_client)
        self.current_symbols = []
        
        # Start the 5-minute refresh thread
        self.refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)

    def _refresh_loop(self):
        """Refreshes the option chain every 5 minutes."""
        logger.info("Option Refresh Loop started.")
        while True:
            try:
                if not self.is_connected:
                    time.sleep(1)
                    continue

                new_symbols = []
                for u in self.underlyings:
                    # Select 4-8 ATM options for each underlying
                    new_symbols.extend(self.selector.get_weekly_options(u, strike_range=2))
                
                # Deduplicate and compare
                new_symbols = list(set(new_symbols))
                
                if set(new_symbols) != set(self.current_symbols):
                    logger.info(f"Subscribing to {len(new_symbols)} symbols: {new_symbols[:3]}...")
                    # Unsubscribe old if needed, or just subscribe new
                    if self.current_symbols:
                        self.streamer.unsubscribe(self.current_symbols)
                    
                    self.current_symbols = new_symbols
                    self.streamer.subscribe(self.current_symbols, "full")
                
            except Exception as e:
                logger.error(f"Option refresh failed: {e}")
            
            time.sleep(300) # 5 minutes

    def connect(self):
        super().connect()
        # Start refreshing symbols once connected
        if not self.refresh_thread.is_alive():
            self.refresh_thread.start()
