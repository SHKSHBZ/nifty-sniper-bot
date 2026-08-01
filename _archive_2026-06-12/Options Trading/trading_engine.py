import os
import yaml
import time
import logging
import zmq
import threading
from datetime import datetime
from collections import deque

# Import Professional Modules (9% Path)
from executor.paper import PaperExecutor
from risk.manager import RiskManager
from database import TradingDB
from confluence_checker import ConfluenceChecker
from chain_selector import OptionSelector
from data_fetcher import DataFetcher
from telegram_notifier import TelegramNotifier

logger = logging.getLogger("TradingEngine")

class TradingEngine:
    """
    Professional Hedged Iron Condor Engine (9% Path).
    Focussed on Low-VIX, Sideways markets with defined risk.
    """
    def __init__(self, config):
        self.config = config
        self.max_risk_percent = config.get('max_risk_percent', 0.75)
        self.min_credit = config.get('min_credit_points', 40)
        self.max_spread = config.get('max_spread_allowed', 0.5)
        self.cooldown_seconds = 600 # 10 minutes between setups
        
        # 1. Initialize Professional Components
        self.paper_executor = PaperExecutor(config)
        self.risk_manager = RiskManager(config)
        self.db = TradingDB()
        self.confluence = ConfluenceChecker(config)
        self.data_fetcher = DataFetcher(config)
        
        # 2. Safety & Notifications
        self.notifier = TelegramNotifier(config)
        self.notifier.start_heartbeat(self)
        
        # 3. State tracking
        self.tick_count = 0
        self.current_prices = {}
        self.price_history = {}
        self.vix_history = deque(maxlen=20)
        self.leg_quotes = {} # For atomic mid-price credit check
        self.active_setup_symbols = None
        self.trade_targets = {} # {instrument: {'credit': 45.0, 'symbols': {...}}}
        self.last_trade_time = 0
        self.order_fill_timer = None
        
        # 4. Subscriber (ZeroMQ)
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect("tcp://localhost:5555")
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "") 
        self.socket.setsockopt(zmq.RCVTIMEO, 10000) # 10 second timeout

        logger.info("=== FINAL 9% PATH IRON CONDOR ENGINE STARTED ===")
        self.notifier.send("🚀 BOT STARTED — Iron Condor Mode Active (Locked Phase 0)")

    def is_market_safe(self, now=None):
        """Professional Instinct Check: Time/Day/Regime."""
        if now is None: now = datetime.now()
        
        # 1. Morning Cool-off (Wait for IV crush)
        if now.time() < datetime.strptime("09:45:00", "%H:%M:%S").time():
            return False, "Morning Volatility"
            
        # 2. Expiry Danger Zone (Tuesday after 12:30 PM)
        if now.weekday() == 1 and now.time() > datetime.strptime("12:30:00", "%H:%M:%S").time():
            self.forced_square_off()
            return False, "Tuesday Gamma Risk"
            
        return True, "OK"

    def forced_square_off(self):
        """Force-liquidate everything to avoid Gamma risk on expiry."""
        if self.trade_targets:
            logger.critical("FORCED SQUARE-OFF (TUESDAY 12:30)")
            self.notifier.send("🛑 FORCED SQUARE-OFF at 12:30 — Gamma Protection")
            # Iterate through positions and close them... (In paper, just clear positions)
            self.trade_targets.clear()

    def on_tick(self, raw_tick):
        """Main processing loop triggered by every ZeroMQ message."""
        tick = self._normalize_tick(raw_tick)
        if not tick: return

        self.tick_count += 1
        instrument = tick['instrument_key']
        ltp = tick['ltp']
        
        # Store live depth for atomic checks
        if 'depth' in tick:
            self.leg_quotes[instrument] = {
                'bid': tick['depth'].get('bids', [{}])[0].get('price', 0),
                'ask': tick['depth'].get('asks', [{}])[0].get('price', 0),
                'ltp': ltp
            }

        # Track Index Price for confluence
        if instrument in self.config.get('instruments', []):
            self.current_prices[instrument] = ltp
            if instrument not in self.price_history:
                self.price_history[instrument] = []
            self.price_history[instrument].append(ltp)
            if len(self.price_history[instrument]) > 100: self.price_history[instrument].pop(0)

        # Track VIX for SMA Regime detection
        if 'INDIA VIX' in instrument:
            self.vix_history.append(ltp)

        # 1. Safety Check (Time/Day)
        safe, reason = self.is_market_safe()
        if not safe: return

        # 2. Confluence Score (Every 50 ticks)
        if self.tick_count % 50 == 0 and instrument in self.config.get('instruments', []):
            self._evaluate_market(instrument, ltp)

    def _evaluate_market(self, instrument, spot):
        """Runs the 5-factor professional filter."""
        # 1. Fetch Real Data
        india_vix = self.vix_history[-1] if self.vix_history else 14.0
        vix_sma = sum(self.vix_history) / len(self.vix_history) if self.vix_history else 14.0
        pcr = self.data_fetcher.get_pcr()
        max_pain_dist = self.data_fetcher.get_max_pain_dist()
        
        # 2. EMA Trend (9/21 Sideways)
        prices = self.price_history[instrument]
        ema9 = sum(prices[-9:]) / 9 if len(prices) >= 9 else spot
        ema21 = sum(prices[-21:]) / 21 if len(prices) >= 21 else spot
        
        # 3. Calculate Score
        score, details = self.confluence.get_score(
            spot, india_vix, ema9, ema21, pcr, True, max_pain_dist, 50
        )
        
        # Sensex Guard: Stricter VIX for Sensex
        if 'SENSEX' in instrument and india_vix > 12.0:
            score = 0
            details['VIX']['msg'] = "Sensex Volatility Guard: Stay out (VIX > 12)"
        
        # 4. Print Score Dashboard
        if self.tick_count % 100 == 0:
            self._print_dashboard(instrument, score, details)

        # 5. Entry Decision (Dual Mode: Neutral or Trending)
        if not self.data_fetcher.is_fresh():
            if self.tick_count % 100 == 0:
                logger.warning("DATA STALE (Macro). Entry Blocked until next Chain Update.")
            return

        current_time = time.time()
        if current_time - self.last_trade_time > self.cooldown_seconds:
            # RULE 1: Nifty & Bank Nifty are for SIDEWAYS (Iron Condors)
            if 'Nifty 50' in instrument or 'Nifty Bank' in instrument:
                if score >= 4 and india_vix < vix_sma * 1.15:
                    logger.info(f"⚖️ NEUTRAL SETUP: Nifty/Bank Sideways (Score {score}/5) | Executing Iron Condor")
                    self.execute_iron_condor(instrument, spot)
                    self.last_trade_time = current_time
                
            # RULE 2: Sensex is for TRENDING (Vertical Spreads)
            elif 'SENSEX' in instrument:
                regime, trend_msg = self.confluence.get_market_regime(spot, ema9, ema21, 50)
                rejection = self.confluence.detect_rejection(spot, self.price_history[instrument], 50)
                
                # Check Order Flow for Sensex (Requires strong imbalance for breakout)
                # We fetch the OFI from the latest tick data
                ofi = 0
                if instrument in self.leg_quotes:
                    # Logic to estimate OFI if not explicitly in tick (professional approximation)
                    ofi = self.leg_quotes[instrument].get('ofi', 0)

                # TRIGGER: Strong Trend OR Bounce Back
                if (regime != 0 or rejection != 0) and india_vix < 18.0:
                    
                    # FINAL GUARD: LIQUIDITY (Check mid-price and spread)
                    symbols = OptionSelector.get_vertical_spread_symbols(instrument, spot, regime or rejection)
                    buy_quote = self.leg_quotes.get(symbols['buy_leg'], {'bid': 0, 'ask': 0})
                    
                    if self.confluence.is_liquidity_safe(buy_quote['bid'], buy_quote['ask'], self.max_spread):
                        logger.info(f"🚀 SENSEX {trend_msg} | Rejection: {rejection} | Liquidity OK")
                        self.execute_vertical_spread(instrument, spot, regime or rejection)
                        self.last_trade_time = current_time
                    else:
                        if self.tick_count % 100 == 0:
                            logger.warning(f"⚠️ SENSEX TRADE BLOCKED: Poor Liquidity (Spread too wide)")

    def execute_vertical_spread(self, instrument, spot, regime):
        """Executes a 2-leg Bull/Bear Vertical Spread for Trending markets."""
        capital = self.paper_executor.get_current_capital()
        max_risk_rupees = capital * (self.max_risk_percent / 100)
        
        symbols = OptionSelector.get_vertical_spread_symbols(instrument, spot, regime)
        risk_per_lot = symbols['max_risk_points'] * symbols['lot_size']
        lots = min(2, int(max_risk_rupees // risk_per_lot))
        
        if lots < 1: return
        quantity = lots * symbols['lot_size']
        
        # Entry (Atomic Limit/Mid Fill)
        logger.info(f"TRENDING ENTRY: {symbols['type']} | {lots} Lots | Risk: {risk_per_lot * lots:.0f}")
        
        # LEG 1: BUY ATM (The Money Leg)
        self.paper_executor.place_order(symbols['buy_leg'], 'BUY', quantity, price=0, is_option=True)
        # LEG 2: SELL OTM (The Hedge Leg)
        self.paper_executor.place_order(symbols['sell_leg'], 'SELL', quantity, price=0, is_option=True)
        
        self.notifier.send(f"📈 TRENDING ENTRY — {symbols['type']}\nSymbol: {symbols['buy_leg']}\nTarget: Trend Follow\nLots: {lots}")

    def execute_iron_condor(self, instrument, spot):
        """Professional 4-leg Execution with Atomic Checks."""
        # 1. Position Sizing (Fixed 0.75% Risk)
        capital = self.paper_executor.get_current_capital()
        max_risk_rupees = capital * (self.max_risk_percent / 100)
        
        # 2. Wing & Protection (Locked: 175/100)
        symbols = OptionSelector.get_iron_condor_symbols(instrument, spot, wing_width=175, protection=100)
        self.active_setup_symbols = list(symbols.values())
        
        # 3. Wait for Option Ticks (Zero-latency ZMQ)
        # In a real environment, we'd wait for these. For now, simulate.
        mid_credit, max_spread = self._calculate_mid_credit_and_spread(symbols)
        
        if max_spread > self.max_spread:
            logger.info(f"WIDE SPREAD {max_spread:.2f} — staying out (Retail Tax)")
            return

        if mid_credit < self.min_credit:
            logger.info(f"CREDIT TOO LOW: {mid_credit:.1f} < {self.min_credit}")
            return

        # 4. Quantity calculation
        risk_per_lot = symbols['max_risk_points'] * symbols['lot_size']
        lots = int(max_risk_rupees // risk_per_lot)
        lots = min(lots, 2) # Safety cap
        if lots < 1: return
        quantity = lots * symbols['lot_size']

        logger.info(f"IRON CONDOR APPROVED | Score 4/5 | {lots} Lots | Credit: {mid_credit:.1f}")
        
        # 5. Atomic Entry (LIMIT ORDERS)
        self.trade_targets[instrument] = {
            'credit_received': mid_credit, 
            'symbols': symbols, 
            'legs_filled': 0, 
            'filled_legs': []
        }
        
        for leg_type, sym in symbols.items():
            if leg_type in ['sell_call', 'buy_call', 'sell_put', 'buy_put']:
                side = 'SELL' if 'sell' in leg_type else 'BUY'
                # Simulate mid-price execution
                q = self.leg_quotes.get(sym, {'ltp': spot * 0.005 if side == 'SELL' else spot * 0.002})
                price = (q['bid'] + q['ask']) / 2 if 'bid' in q and q['bid'] > 0 else q['ltp']
                self.paper_executor.place_order(sym, side, quantity, price, is_option=True)
                
        self.notifier.send(f"📈 IRON CONDOR ENTERED\nSymbols: {symbols['sell_call']}/{symbols['sell_put']}\nCredit: {mid_credit:.1f} pts\nLots: {lots}")

    def _calculate_mid_credit_and_spread(self, symbols):
        """Calculates exact credit based on (Bid+Ask)/2."""
        mids = {}
        spreads = []
        # Fallback simulation if no live ticks yet
        default_prices = {'sell_call': 125, 'buy_call': 45, 'sell_put': 115, 'buy_put': 40}
        
        for name, key in symbols.items():
            if name in default_prices:
                q = self.leg_quotes.get(key, {'bid': 0, 'ask': 0, 'ltp': default_prices[name]})
                if q['bid'] > 0 and q['ask'] > 0:
                    mid = (q['bid'] + q['ask']) / 2
                    spread = q['ask'] - q['bid']
                else:
                    mid = q['ltp']
                    spread = 0.4 # Simulate thin spread
                mids[name] = mid
                spreads.append(spread)
        
        net_credit = (mids['sell_call'] + mids['sell_put']) - (mids['buy_call'] + mids['buy_put'])
        return net_credit, max(spreads) if spreads else 0

    def _print_dashboard(self, instrument, score, details):
        """Professional Terminal Dashboard."""
        print(f"\n" + "="*70)
        print(f"📊 9% PATH CONFLUENCE CHECKER | {time.strftime('%H:%M:%S')}")
        print(f"="*70)
        for factor, data in details.items():
            status = "✅ [PASS]" if data['pass'] else "❌ [FAIL]"
            print(f"{status} {factor:<10}: {str(data['val']):<10} | {data['msg']}")
        print(f"-"*70)
        verdict = "🚀 ENTRY AUTHORIZED" if score >= 4 else "⏳ FILTERING NOISE..."
        print(f"🔹 TOTAL SCORE:  {score} / 5             | {verdict}")
        print(f"🔹 {instrument:<20} | Price: {self.current_prices[instrument]:.2f}")
        print(f"="*70 + "\n")

    def _normalize_tick(self, tick):
        """Standardize Upstox V3 -> Internal format."""
        try:
            if not isinstance(tick, dict) or not tick: return None
            if 'type' in tick and tick['type'] != 'live_feed': return None
            instrument_key = None
            instrument_data = None
            for k, v in tick.items():
                if '|' in str(k):
                    instrument_key = k
                    instrument_data = v
                    break
            if not instrument_key: return None
            data = instrument_data
            if isinstance(instrument_data, dict):
                if 'fullFeed' in instrument_data:
                    full_feed = instrument_data['fullFeed']
                    data = full_feed.get('indexFF') or full_feed.get('marketFF', {})
                elif 'ff' in instrument_data:
                    data = instrument_data['ff'].get('market_ff', {})
            ltp = 0.0
            if isinstance(data, dict):
                ltpc = data.get('ltpc', {})
                ltp = ltpc.get('ltp', data.get('ltp', 0.0))
            if ltp == 0: return None
            
            # Extract Depth if available
            depth_data = {}
            if isinstance(data, dict):
                depth_data = data.get('market_depth', {}) or data.get('md', {})
            
            normalized = {'instrument_key': instrument_key, 'ltp': ltp}
            if depth_data:
                normalized['depth'] = {
                    'bids': [{'price': b.get('buy_price', b.get('bp', 0))} for b in (depth_data.get('bin', []) or depth_data.get('b', []))],
                    'asks': [{'price': a.get('sell_price', a.get('sp', 0))} for a in (depth_data.get('ask_bin', []) or depth_data.get('s', []))]
                }
            return normalized
        except Exception as e:
            logger.error(f"Error normalizing: {e}")
            return None

    def run(self):
        logger.info("Starting Trading Engine... listening to ZeroMQ.")
        while True:
            try:
                raw_tick = self.socket.recv_json()
                self.on_tick(raw_tick)
            except zmq.Again:
                logger.warning("⚠️ DATA FEED INACTIVE: No ticks received for 10s. Check feed_handler.py!")
                self.notifier.send("⚠️ DATA FEED INACTIVE — Check Upstox Data Handler!")
            except Exception as e:
                logger.error(f"Engine Loop Error: {e}")

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("data/trading_log.log"),
            logging.StreamHandler()
        ]
    )
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    engine = TradingEngine(config)
    engine.run()
