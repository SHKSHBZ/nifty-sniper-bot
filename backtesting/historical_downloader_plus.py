"""
historical_downloader_plus.py
A specialized script for Upstox Plus members to fetch EXPIRED Options Data
using the secret v2/expired-instruments/ API endpoints!
"""

import os
import time
import logging
from pathlib import Path
from datetime import datetime
import requests
import pandas as pd
from upstox_auth import UpstoxAuth

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

class UpstoxPlusDownloader:
    def __init__(self):
        self.auth = UpstoxAuth()
        access_token = self.auth.get_access_token()
        if not access_token:
            raise ValueError("Failed to get Access Token!")
            
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        self.base_url = "https://api.upstox.com/v2/expired-instruments"
        
    def get_expired_contracts(self, underlying_key: str, expiry_date: str) -> list:
        """Step 1: Get the hidden exchange keys for all options on this expiry"""
        logger.info(f"Fetching Option Contracts for {underlying_key} expiring on {expiry_date}...")
        url = f"{self.base_url}/option/contract?instrument_key={underlying_key}&expiry_date={expiry_date}"
        
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            logger.error(f"Failed to fetch contracts: {resp.text}")
            return []
            
        return resp.json().get("data", [])

    def download_candle_data(self, instrument_key: str, trading_symbol: str, from_date: str, to_date: str, interval: str = "1minute"):
        """Step 2: Use the hidden numerical key to fetch the dead candles"""
        logger.info(f"Downloading historical candles for {trading_symbol}...")
        url = f"{self.base_url}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
        
        resp = requests.get(url, headers=self.headers, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"No data or rate limit for {trading_symbol}. HTTP {resp.status_code}")
            return None
            
        data = resp.json().get("data", {}).get("candles", [])
        if not data:
            logger.info("Empty candle array returned.")
            return None
            
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "open_interest"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values(by="timestamp").reset_index(drop=True)
        return df

def run_downloader_job():
    downloader = UpstoxPlusDownloader()
    
    # --------------------------------------------------------------------
    # CONFIGURATION: March 30, 2026 Nifty Expiry (Full Week Download)
    # --------------------------------------------------------------------
    UNDERLYING = "NSE_INDEX|Nifty 50"
    EXPIRY = "2026-03-30" 
    FROM_DATE = "2026-03-24"
    TO_DATE = "2026-03-30"
    
    # Nifty fell from 22912 down to 22331 over this week.
    CENTER_STRIKE = 22600
    STRIKE_GAP = 50
    RANGE_COUNT = 10  # Covers 22100 to 23100
    # --------------------------------------------------------------------
    
    strikes_to_fetch = [CENTER_STRIKE + (i * STRIKE_GAP) for i in range(-RANGE_COUNT, RANGE_COUNT + 1)]
    
    # 1. Get the list of all available secret keys for the Expiry
    all_contracts = downloader.get_expired_contracts(UNDERLYING, EXPIRY)
    if not all_contracts:
        print("No contracts found for this expiry. Is the date perfectly formatted?")
        return
        
    print(f"Found {len(all_contracts)} total contracts. Filtering for our specific Strike Range...")
    
    # 2. Filter down
    target_contracts = [
        c for c in all_contracts 
        if c.get("strike_price") in strikes_to_fetch
        # And only Nifty options
        and c.get("name") == "NIFTY"
    ]
    
    print(f"Filtered down to {len(target_contracts)} Call/Put contracts.")
    
    for contract in target_contracts:
        hidden_key = contract['instrument_key'] # e.g. NSE_FO|47067|10-10-2024
        symbol = contract['trading_symbol']
        
        # Format the filename cleanly
        safe_sym = symbol.replace(" ", "_")
        filename = DATA_DIR / f"{safe_sym}_1min.csv"
        
        if filename.exists():
            print(f"Skipping {symbol}, file already exists!")
            continue
            
        df = downloader.download_candle_data(hidden_key, symbol, FROM_DATE, TO_DATE)
        
        if df is not None:
            df.to_csv(filename, index=False)
            print(f"[SUCCESS] Saved {symbol} ({len(df)} rows)")
            
        # IMPORTANT: Sleep for 0.5s to completely avoid Upstox 429 Too Many Requests Limit
        time.sleep(0.5)
        
    print("\n[DOWNLOAD COMPLETE] Your real Options data is sitting in the /data folder!")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_downloader_job()
