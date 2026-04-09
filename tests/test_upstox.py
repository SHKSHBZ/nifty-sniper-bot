import os
import upstox_client
from upstox_client.rest import ApiException
from dotenv import load_dotenv
from upstox_auth import UpstoxAuth

load_dotenv()

def test_upstox_connection():
    api_key = os.getenv("UPSTOX_API_KEY")
    
    auth = UpstoxAuth()
    access_token = auth.get_access_token()
    
    if not api_key or not access_token:
        print("Error: Could not retrieve API Key or Access Token. Make sure to authenticate first!")
        return

    # Configure API client
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    
    # Test 1: Get Profile
    try:
        api_instance = upstox_client.UserApi(upstox_client.ApiClient(configuration))
        api_response = api_instance.get_profile("2.0")
        print("Successfully retrieved profile!")
        print(f"User Name: {api_response.data.user_name}")
    except ApiException as e:
        print(f"Exception when calling UserApi->get_profile: {e}")

    # Test 2: Get Market Data Feed Authorize URL
    try:
        ws_api_instance = upstox_client.WebsocketApi(upstox_client.ApiClient(configuration))
        ws_response = ws_api_instance.get_market_data_feed_authorize("2.0")
        print("\nSuccessfully authorized market data feed!")
        print(f"WS URL: {ws_response.data.authorized_redirect_url}")
    except ApiException as e:
        print(f"Exception when calling WebsocketApi->get_market_data_feed_authorize: {e}")

if __name__ == "__main__":
    test_upstox_connection()
