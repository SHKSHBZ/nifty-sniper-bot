"""
historical_downloader.py
Downloads historical 5-minute candle data for Nifty/Sensex options 
from the Upstox v2 API and saves it as CSV for backtesting.

Usage:
    python historical_downloader.py
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

import requests
import pandas as pd

from auth_manager import AuthManager

logger = logging.getLogger(__name__)

# Constants
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

UPSTOX_HISTORICAL_URL = "https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"


class HistoricalDownloader:
    """
    Downloads and formats historical market data from Upstox.
    """

    def __init__(self, auth_manager: AuthManager):
        self.auth = auth_manager

    def download_options_data(
        self,
        symbol: str,
        strike: int,
        option_type: str,     # "CE" or "PE"
        expiry_str: str,      # Format required by Upstox, e.g., "26MAR", "26APR25"
        from_date: str,       # "YYYY-MM-DD"
        to_date: str,         # "YYYY-MM-DD"
        interval: str = "5minute"
    ) -> Optional[pd.DataFrame]:
        """
        Download historical option data for a specific date range.
        Handles Upstox's chunking limits by breaking requests if needed.
        """
        instrument_key = self._resolve_instrument_key(symbol, strike, option_type, expiry_str)
        logger.info(f"Downloading {interval} data for {instrument_key} from {from_date} to {to_date}...")

        url = UPSTOX_HISTORICAL_URL.format(
            instrument_key=instrument_key,
            interval=interval,
            to_date=to_date,
            from_date=from_date
        )

        headers = self.auth.get_headers()
        
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", {}).get("candles", [])

            if not data:
                logger.warning(f"No data found for {instrument_key} in the given date range.")
                return None

            # Upstox candle format: [timestamp, open, high, low, close, volume, open_interest]
            df = pd.DataFrame(data, columns=[
                "timestamp", "open", "high", "low", "close", "volume", "open_interest"
            ])
            
            # Format timestamp to readable datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)
            
            # Save to CSV
            filename = DATA_DIR / f"{symbol}_{expiry_str}_{strike}_{option_type}_{interval}.csv"
            df.to_csv(filename, index=False)
            logger.info(f"Saved {len(df)} rows to {filename}")
            
            return df
            
        except requests.HTTPError as exc:
            logger.error(f"API Error fetching historical data: {exc.response.text}")
            return None
        except Exception as exc:
            logger.error(f"Unexpected error: {exc}")
            return None

    def _resolve_instrument_key(
        self, symbol: str, strike: int, option_type: str, expiry_str: str
    ) -> str:
        """
        Build Upstox instrument key.
        Format: NSE_FO|NIFTY26MAR24400CE
        exchange: BSE_FO for Sensex, NSE_FO for Nifty
        """
        exchange = "BSE_FO" if symbol.upper() == "SENSEX" else "NSE_FO"
        return f"{exchange}|{symbol.upper()}{expiry_str.upper()}{strike}{option_type.upper()}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Initialize Auth Manager
    try:
        auth = AuthManager()
        downloader = HistoricalDownloader(auth)

        # Example execution variables
        _symbol = "NIFTY"
        _expiry = "26MAR"   # March 2026 Monthly (Replace with your actual backtest expiry)
        _from = "2026-03-01"
        _to = "2026-03-24"

        # Download a GRID of strikes to capture ATM at any point during this month
        min_strike = 23800
        max_strike = 24800
        step = 50

        print(f"Starting bulk downloader for {_symbol} {_expiry}...")
        
        for _strike in range(min_strike, max_strike + step, step):
            for _type in ["CE", "PE"]:
                print(f"Downloading {_symbol} {_strike} {_type}...")
                df = downloader.download_options_data(
                    symbol=_symbol,
                    strike=_strike,
                    option_type=_type,
                    expiry_str=_expiry,
                    from_date=_from,
                    to_date=_to,
                    interval="5minute"
                )
                
                # Sleep briefly to avoid hitting Upstox API rate limits
                time.sleep(1)
            
        print("\nFinished downloading the strike grid!")
            
    except Exception as e:
        logger.error(f"Failed to run downloader: {e}")
