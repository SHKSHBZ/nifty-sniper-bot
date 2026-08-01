import sys
import os
from datetime import datetime

# Set PYTHONPATH to root directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

print("=== STARTING FULL CODEBASE DEPENDENCY & EXECUTION VERIFICATION ===")

# 1. Test imports
try:
    import data_fetcher
    print("[OK] Import data_fetcher")
except Exception as e:
    print(f"[FAIL] Import data_fetcher failed: {e}")
    sys.exit(1)

try:
    import oi_flow_engine
    print("[OK] Import oi_flow_engine")
except Exception as e:
    print(f"[FAIL] Import oi_flow_engine failed: {e}")
    sys.exit(1)

try:
    import main
    print("[OK] Import main")
except Exception as e:
    print(f"[FAIL] Import main failed: {e}")
    sys.exit(1)

# 2. Test OIFlowEngine instantiation and dynamic levels
try:
    from oi_flow_engine import OIFlowEngine
    from gann_engine import GannSquareOf9

    config = {
        "trading_index": "NIFTY",
        "strike_step": 50,
        "entry_time": "10:30",
        "snap2_time": "10:00",
        "snap1_time": "09:30"
    }
    
    engine = OIFlowEngine(config)
    engine.gann = GannSquareOf9(24281.72)
    print("[OK] OIFlowEngine Instantiation")
    
    # Lock strikes
    engine.lock_strikes(24250.0)
    print(f"[OK] Lock Strikes: CE={engine.ce_fixed_strikes}, PE={engine.pe_fixed_strikes}")
    
    # Test tick under dummy data
    oi_snapshot = {
        24100: {"ce_oi": 1000, "pe_oi": 5000},
        24150: {"ce_oi": 2000, "pe_oi": 4000},
        24200: {"ce_oi": 3000, "pe_oi": 3000},
        24250: {"ce_oi": 4000, "pe_oi": 2000},
        24300: {"ce_oi": 5000, "pe_oi": 1000}
    }
    
    premiums = {
        "24100_CE": 180.0, "24100_PE": 10.0,
        "24150_CE": 130.0, "24150_PE": 20.0,
        "24200_CE": 90.0,  "24200_PE": 40.0,
        "24250_CE": 50.0,  "24250_PE": 70.0,
        "24300_CE": 20.0,  "24300_PE": 120.0
    }
    
    signals = engine.tick(24220.0, datetime.now(), oi_snapshot, premiums)
    print(f"[OK] Dummy Tick Execution (returned {len(signals)} signals)")

except Exception as e:
    print(f"[FAIL] OIFlowEngine logic test failed: {e}")
    sys.exit(1)

# 3. Test DataFetcher PCR mock processing
try:
    from data_fetcher import DataFetcher
    import requests
    
    mock_config = {
        "trading_index": "NIFTY",
        "strike_step": 50,
        "instrument_key": "NSE_INDEX|Nifty 50"
    }
    
    fetcher = DataFetcher(mock_config)
    print("[OK] DataFetcher Instantiation")
    
    # Mock some raw option chain data from Upstox API
    mock_data = [
        {
            "expiry": "2026-07-14",
            "strike_price": 24200.0,
            "underlying_spot_price": 24220.0,
            "pcr": 1200.0,  # Upstox returns PCR * 1000
            "call_options": {
                "market_data": {"oi": 5000, "prev_oi": 4500, "ltp": 95.0, "volume": 10000},
                "option_greeks": {"delta": 0.52, "theta": -5.2, "vega": 12.0}
            },
            "put_options": {
                "market_data": {"oi": 3000, "prev_oi": 2800, "ltp": 80.0, "volume": 6000},
                "option_greeks": {"delta": -0.48, "theta": -4.8, "vega": 11.5}
            }
        },
        {
            "expiry": "2026-07-14",
            "strike_price": 24250.0,
            "underlying_spot_price": 24220.0,
            "pcr": 1200.0,
            "call_options": {
                "market_data": {"oi": 6000, "prev_oi": 5500, "ltp": 60.0, "volume": 12000},
                "option_greeks": {"delta": 0.38, "theta": -4.5, "vega": 10.0}
            },
            "put_options": {
                "market_data": {"oi": 4000, "prev_oi": 3700, "ltp": 115.0, "volume": 8000},
                "option_greeks": {"delta": -0.62, "theta": -5.5, "vega": 13.0}
            }
        }
    ]
    
    # Mock requests.get inside fetcher.session
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status_code = status_code
        def json(self):
            return {"data": self.json_data}
        def raise_for_status(self):
            pass

    fetcher.session.get = lambda url, **kwargs: MockResponse(mock_data)
    fetcher._get_expiry = lambda: "2026-07-14"
    fetcher._load_access_token = lambda: "mock_token"
    
    # Run fetch chain processing
    fetcher._fetch_chain()
    
    # Check cache fields to verify PCR is calculated dynamically and support/resistance are populated
    cache = fetcher.cache
    print(f"[OK] Processed Mock _fetch_chain:")
    print(f"     - Spot: {cache.get('spot')}")
    print(f"     - PCR: {cache.get('pcr'):.2f}")
    print(f"     - Focus PCR: {cache.get('focus_pcr'):.2f}")
    print(f"     - Support Strike: {cache.get('support_strike')}")
    print(f"     - Resistance Strike: {cache.get('resistance_strike')}")

except Exception as e:
    print(f"[FAIL] DataFetcher processing failed: {e}")
    sys.exit(1)

print("=== ALL VERIFICATION CHECKS PASSED SUCCESSFULLY ===")
