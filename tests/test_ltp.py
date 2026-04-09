import os
from dotenv import load_dotenv
from upstox_auth import UpstoxAuth
import upstox_client
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_ltp_fetch():
    print("--- Starting LTP Fetch Test ---")
    
    # Authenticate
    auth = UpstoxAuth()
    if not auth.is_session_valid():
        print("Session is not valid. Running authentication...")
        if not auth.authenticate():
            print("Authentication failed. Exiting.")
            return

    access_token = auth.get_access_token()
    api_key = auth.get_client_id()

    if not access_token or not api_key:
        print("Failed to get credentials after auth. Exiting.")
        return

    print(f"Using API Key: {api_key}")
    
    # Configure API client
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_client = upstox_client.ApiClient(configuration)
    
    market_quote_api = upstox_client.MarketQuoteApi(api_client)
    symbol = "NSE_INDEX|Nifty 50"
    api_version = "2.0"

    try:
        print(f"\nFetching full market quote for: '{symbol}' using API version '{api_version}'...")
        quote = market_quote_api.get_full_market_quote(symbol, api_version)
        
        print("\n--- RAW API RESPONSE ---")
        print(quote)
        print("------------------------")

        # Try to access LTP
        data = quote.data
        if symbol in data:
            ltp = data[symbol].last_price
            print(f"\nSUCCESS! LTP for {symbol} is: {ltp}")
        else:
            print(f"\nFAILURE! Symbol '{symbol}' not found in response data keys.")
            print(f"Available keys: {list(data.keys())}")

    except upstox_client.ApiException as e:
        print(f"\nAPI EXCEPTION: {e.status} {e.reason}")
        print(f"BODY: {e.body}")
    except Exception as e:
        print(f"\nUNEXPECTED EXCEPTION: {e}")

if __name__ == "__main__":
    test_ltp_fetch()
