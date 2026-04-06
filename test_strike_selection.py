import unittest
from unittest.mock import MagicMock
from chain_selector import ChainSelector

class TestStrikeSelection(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.selector = ChainSelector(self.mock_api)

    def test_nifty_atm_rounding(self):
        # Mock get_ltp directly
        self.selector.get_ltp = MagicMock(return_value=22534.0)
        
        atm, ltp = self.selector.get_atm_strike("NSE_INDEX|Nifty 50")
        self.assertEqual(int(atm), 22550)
        self.assertEqual(float(ltp), 22534.0)

    def test_banknifty_atm_rounding(self):
        self.selector.get_ltp = MagicMock(return_value=48120.0)
        
        atm, ltp = self.selector.get_atm_strike("NSE_INDEX|Nifty Bank")
        self.assertEqual(int(atm), 48100)

    def test_symbol_generation(self):
        self.selector.get_ltp = MagicMock(return_value=22500.0)
        
        symbols = self.selector.get_weekly_options("NSE_INDEX|Nifty 50", strike_range=1)
        self.assertEqual(len(symbols), 7)
        self.assertIn("NSE_INDEX|Nifty 50", symbols)
        # Check if a CE symbol is present (format: NSE_FO|NIFTY...CE)
        ce_exists = any("CE" in s for s in symbols)
        self.assertTrue(ce_exists)

if __name__ == "__main__":
    unittest.main()
