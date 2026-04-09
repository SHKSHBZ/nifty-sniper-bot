import os
import requests
import logging
from datetime import datetime, timedelta
from upstox_auth import UpstoxAuth
from order_executor import OrderExecutor, LIMIT_BUFFER_PCT, SL_PCT, TARGET_PCT

logging.basicConfig(level=logging.INFO, format='%(message)s')

class AdapterAuth:
    def __init__(self):
        auth = UpstoxAuth()
        self.access_token = auth.get_access_token()
    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

def run_dry_run():
    print("\n" + "="*40)
    print(" PAPER TRADING DRY RUN ".center(40, "="))
    print("="*40)
    auth = AdapterAuth()
    
    if not auth.access_token:
        print("Not authenticated. Please run 'python upstox_auth.py' first.")
        return

    # Fetch nearest option chain for Nifty 50 to get live spot and premiums
    base_url = "https://api.upstox.com/v2"
    chain_data = None
    today = datetime.now()
    expiry = None
    
    for i in range(8):
        exp_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        url = f"{base_url}/option/chain"
        params = {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": exp_date}
        resp = requests.get(url, params=params, headers=auth.get_headers())
        if resp.status_code == 200 and resp.json().get("data"):
            chain_data = resp.json().get("data")
            expiry = exp_date
            break
            
    if not chain_data:
        print("Market data not found. The API might be down or market fully closed.")
        return

    spot = chain_data[0].get("underlying_spot_price", 0)
    atm_strike = int(round(spot / 50) * 50)
    
    # Extract the true live premium for ATM Call (CE)
    live_ce_premium = 0
    instrument_key = ""
    for item in chain_data:
        if item["strike_price"] == atm_strike:
            call_option = item.get("call_options", {})
            instrument_key = call_option.get("instrument_key", "")
            live_ce_premium = call_option.get("market_data", {}).get("ltp", 0)
            break

    print(f"\n[LIVE MARKET] Nifty Spot is {spot}")
    print(f"[LIVE MARKET] Expiry selected: {expiry}")
    print(f"[LIVE MARKET] AT-The-Money Strike is {atm_strike} CE")
    print(f"[LIVE MARKET] Exact Current Premium: Rs. {live_ce_premium}")
    
    executor = OrderExecutor(auth)
    
    # Note: we bypass the daily loss limit blocker for dry runs
    executor._daily_pnl = 0 
    
    print("\nExecutin Paper Trade...")
    result = executor.place_option_order(
        symbol="NIFTY",
        strike=atm_strike,
        option_type="CE",
        expiry=expiry,
        quantity=50, # 1 Lot of Nifty
        dry_run=True,
        ltp_override=live_ce_premium # Force the bot to calculate risk off the TRUE live premium
    )
    
    print("\n" + "="*40)
    print(" ALGORITHMIC EXECUTION PLAN ".center(40, "="))
    print("="*40)
    print(f"Option Ordered: {result.instrument_key}")
    print(f"Limit Bid placed at: Rs. {result.limit_price}   (+{LIMIT_BUFFER_PCT*100}% buffer snippet)")
    print(f"Stop Loss set at:    Rs. {result.sl_price}   (-{SL_PCT*100}% Risk tolerance)")
    print(f"Target set at:       Rs. {result.target_price}   (+{TARGET_PCT*100}% Profit target)")
    print("\n" + result.message)

if __name__ == "__main__":
    run_dry_run()
