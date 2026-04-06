import logging
import datetime
import upstox_client

logger = logging.getLogger("ChainSelector")

class ChainSelector:
    """
    Hedge-Fund Grade Chain Selector for Upstox V3.
    Retrieves live Index prices to identify ATM/ITM/OTM options for subscription.
    """
    def __init__(self, api_client):
        self.api_client = api_client
        self.market_quote_api = upstox_client.MarketQuoteApi(self.api_client)

    def get_ltp(self, instrument_key):
        """Fetches the latest Index price from Upstox Market Quote API."""
        try:
            api_version = "2.0"
            quote = self.market_quote_api.get_full_market_quote(instrument_key, api_version)
            if quote.data and instrument_key in quote.data:
                return float(quote.data[instrument_key].last_price)
        except Exception as e:
            logger.error(f"Error fetching LTP for {instrument_key}: {e}")
        return 0.0

    def get_atm_strike(self, index_name, ltp=None):
        """Calculates ATM strike based on Index price."""
        if ltp is None or ltp == 0.0:
            ltp = self.get_ltp(index_name)
        
        if ltp == 0.0: return 0, 0.0
        
        interval = 50 if "Nifty 50" in index_name else 100
        atm = round(ltp / interval) * interval
        return int(atm), ltp

    def get_weekly_options(self, index_name, strike_range=2):
        """
        Dynamically generates a list of option symbols for subscription.
        Format: NSE_FO|NIFTY26FEB25500CE
        """
        atm, ltp = self.get_atm_strike(index_name)
        if atm == 0:
            logger.warning(f"Could not determine ATM for {index_name}, subscribing to index only.")
            return [index_name]

        interval = 50 if "Nifty 50" in index_name else 100
        expiry = OptionSelector.get_weekly_expiry_date()
        prefix = "NIFTY" if "Nifty 50" in index_name else "BANKNIFTY"
        
        # Subscribe to Index + ATM/ITM/OTM range
        symbols = [index_name]
        for i in range(-strike_range, strike_range + 1):
            strike = atm + (i * interval)
            symbols.append(f"NSE_FO|{prefix}{expiry}{strike}CE")
            symbols.append(f"NSE_FO|{prefix}{expiry}{strike}PE")
            
        return symbols

class OptionSelector:
    """
    Professional Strike Selection for 4-leg Iron Condor.
    """
    @staticmethod
    def get_weekly_expiry_date(index_name="NIFTY"):
        """Nifty/BankNifty = Tuesday (NSE). Sensex = Friday (BSE)."""
        today = datetime.date.today()
        # Nifty/BankNifty (Tuesday=1), Sensex (Friday=4)
        target_day = 4 if "SENSEX" in index_name else 1
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead == 0: days_ahead = 7
        expiry_date = today + datetime.timedelta(days=days_ahead)
        return expiry_date.strftime("%d%b%y").upper() 

    @staticmethod
    def get_iron_condor_symbols(index_name, spot, wing_width=175, protection=100):
        """
        Returns 4 symbols: Sell CE, Buy CE (Hedge), Sell PE, Buy PE (Hedge).
        """
        prefix = "NIFTY" if "Nifty 50" in index_name else "BANKNIFTY"
        exchange = "NSE_FO"
        expiry = OptionSelector.get_weekly_expiry_date(index_name)
        lot_size = 65 if "Nifty 50" in index_name else 30
        
        # Calculate Strikes (Rounded to nearest 50)
        sell_call_strike = round((spot + wing_width) / 50) * 50
        buy_call_strike = sell_call_strike + protection
        
        sell_put_strike = round((spot - wing_width) / 50) * 50
        buy_put_strike = sell_put_strike - protection
        
        return {
            'sell_call': f"{exchange}|{prefix}{expiry}{sell_call_strike}CE",
            'buy_call':  f"{exchange}|{prefix}{expiry}{buy_call_strike}CE",
            'sell_put':  f"{exchange}|{prefix}{expiry}{sell_put_strike}PE",
            'buy_put':   f"{exchange}|{prefix}{expiry}{buy_put_strike}PE",
            'lot_size': lot_size,
            'max_risk_points': protection
        }

    @staticmethod
    def get_vertical_spread_symbols(index_name, spot, regime, spread_width=100):
        """
        Returns 2 symbols: Buy (ATM) and Sell (OTM Hedge).
        """
        if "SENSEX" in index_name:
            prefix = "SENSEX"
            exchange = "BSE_FO"
            lot_size = 10 # BSE Sensex Lot Size
            round_base = 100
        else:
            prefix = "NIFTY" if "Nifty 50" in index_name else "BANKNIFTY"
            exchange = "NSE_FO"
            lot_size = 65 if "Nifty 50" in index_name else 30
            round_base = 50
            
        expiry = OptionSelector.get_weekly_expiry_date(index_name)
        atm_strike = round(spot / round_base) * round_base
        
        if regime == 1: # Bullish
            buy_strike = atm_strike
            sell_strike = buy_strike + spread_width
            option_type = "CE"
            side = "BULL_CALL"
        else: # Bearish
            buy_strike = atm_strike
            sell_strike = buy_strike - spread_width
            option_type = "PE"
            side = "BEAR_PUT"
            
        return {
            'buy_leg':  f"{exchange}|{prefix}{expiry}{buy_strike}{option_type}",
            'sell_leg': f"{exchange}|{prefix}{expiry}{sell_strike}{option_type}",
            'lot_size': lot_size,
            'max_risk_points': spread_width,
            'type': side
        }
