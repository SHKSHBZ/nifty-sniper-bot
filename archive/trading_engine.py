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
from macro_intelligence import MacroIntelligence
from technical_geometry import TechnicalGeometry

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
        self.strike_selector = StrikeSelector(config)
        self.macro = MacroIntelligence(config)
        
        # Volatility AI Integration (Intraday Polling)
        self.last_ai_fetch_time = 0
        self.daily_macro_bias = self.macro.get_pre_market_bias()
        self.geometry = TechnicalGeometry()
        
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
        
        # Active Position Tracker for Naked Option Exits
        self.active_naked_positions = {} # {symbol: {'entry_price': 100, 'entry_time': 9:15, 'qty': 50, 'highest_pnl': 0}}
        
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
            
        # 2. Universal Intraday Auto-Square-Off (3:15 PM)
        if now.time() >= datetime.strptime("15:15:00", "%H:%M:%S").time():
            self.forced_square_off()
            return False, "INTRADAY Market Closed"
            
        return True, "OK"

    def forced_square_off(self):
        """Force-liquidate everything for Intraday Square-off."""
        if self.active_naked_positions:
            logger.critical("🚨 UNIVERSAL AUTO-SQUARE-OFF (3:15 PM) INITIATED")
            self.notifier.send("🛑 INTRADAY AUTO-SQUARE-OFF: Liquidating all open positions.")
            for symbol, pos in list(self.active_naked_positions.items()):
                self._close_position(symbol, "AUTO_SQUARE_OFF")
            self.active_naked_positions.clear()

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
            
            # Form ORB Geometry
            self.geometry.process_tick(ltp)

        # Track VIX for SMA Regime detection
        if 'INDIA VIX' in instrument:
            self.vix_history.append(ltp)
            if len(self.vix_history) > 20:
                self.vix_history.pop(0)
                
            # --- VOLATILITY-TRIGGERED AI SCRAPING PROTOCOL ---
            if len(self.vix_history) == 20:
                vix_sma = sum(self.vix_history) / 20
                
                # If VIX suddenly violently spikes +6% above its smooth moving average
                if ltp > vix_sma * 1.06:
                    current_time_sec = time.time()
                    # 45-Minute Cooldown so we don't spam Groq API if VIX stays elevated
                    if (current_time_sec - self.last_ai_fetch_time) > 2700: 
                        logger.warning(f"🚨 INDIA VIX ANOMALY DETECTED ({ltp} > SMA {vix_sma:.2f}). Activating Live AI Surveillance!")
                        self.last_ai_fetch_time = current_time_sec
                        
                        # Spin off a background thread so the heavy AI internet request doesn't freeze the rapid tick pipe!
                        def run_live_ai_interrogation():
                            try:
                                score = self.macro.fetch_ai_news_sentiment()
                                logger.warning(f"🧠 LIVE AI VERDICT RETURNED: Sentiment {score:.2f} / +1.00")
                                if score <= -0.5:
                                    logger.error("🛑 CRASH DETECTED BY AI: Severing all Intraday Call Options!")
                                    self._panic_sell_long_calls()
                            except Exception as e:
                                logger.error(f"Live AI Interrogation failed: {e}")
                                
                        threading.Thread(target=run_live_ai_interrogation, daemon=True).start()
            return

        # 1. Safety Check (Time/Day)
        safe, reason = self.is_market_safe()
        
        # 1.5 Monitor Active Direct Positions (Stop Loss / Trailing / Theta Cut)
        self._enforce_exits(tick)

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
        
        # 3. Calculate RSI 14
        rsi_14 = 50.0
        if len(prices) > 15:
            deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
            gains = [d for d in deltas if d > 0][-14:]
            losses = [-d for d in deltas if d < 0][-14:]
            avg_gain = sum(gains) / 14 if gains else 0
            avg_loss = sum(losses) / 14 if losses else 1e-9
            rs = avg_gain / avg_loss
            rsi_14 = 100 - (100 / (1 + rs))

        # 4. Fear & Greed Index
        fg_index = self.confluence.calculate_fear_greed(pcr, india_vix, vix_sma, rsi_14)

        # 5. Calculate Score
        score, details = self.confluence.get_score(
            spot, india_vix, ema9, ema21, pcr, True, max_pain_dist, rsi_14
        )
        
        # Sensex Guard: Stricter VIX for Sensex
        if 'SENSEX' in instrument and india_vix > 12.0:
            score = 0
            details['VIX']['msg'] = "Sensex Volatility Guard: Stay out (VIX > 12)"
        
        # 6. Print Score Dashboard
        if self.tick_count % 100 == 0:
            self._print_dashboard(instrument, score, details, fg_index)

        # 7. Entry Decision (Dual Mode: Neutral or Trending)
        if not self.data_fetcher.is_fresh():
            if self.tick_count % 100 == 0:
                logger.warning("DATA STALE (Macro). Entry Blocked until next Chain Update.")
            return

        current_time = time.time()
        if current_time - self.last_trade_time > self.cooldown_seconds:
            
            # PURE DIRECTIONAL BUYING (Iron Condors and Vertical Spreads Disabled)
            if instrument in self.config.get('instruments', []):
                regime, trend_msg = self.confluence.get_market_regime(spot, ema9, ema21, 50)
                rejection = self.confluence.detect_rejection(spot, self.price_history[instrument], 50)
                
                orb_signal = self.geometry.check_orb_breakout(spot)
                if orb_signal != 0:
                    regime = orb_signal
                    trend_msg = "ORB BREAKOUT"

                # TRIGGER: Strong Trend OR Bounce Back OR Breakout
                if regime != 0 or rejection != 0:
                    
                    is_bullish = (regime == 1 or rejection == 1)
                    
                    # Sentiment Gate
                    if is_bullish and "FEAR" in fg_index['sentiment']:
                        return
                    if not is_bullish and "GREED" in fg_index['sentiment']:
                        return
                    
                    # Macro Intelligence Gate
                    if self.macro.is_bias_conflict("BULLISH" if is_bullish else "BEARISH"):
                        return
                        
                    logger.info(f"🚀 {instrument} {trend_msg} | Validated Directional Buying")
                    self.execute_naked_options(instrument, spot, regime or rejection)
                    self.last_trade_time = current_time

    def execute_naked_options(self, instrument, spot, regime):
        """Execute Pure Option Buying with Staggered Limit Bids for 0 Slippage."""
        capital = self.paper_executor.get_current_capital()
        # Allocate 5% of capital for naked buying
        trade_capital = capital * 0.05 
        
        symbols = OptionSelector.get_naked_option_symbols(instrument, spot, regime)
        sym = symbols['symbol']
        lot_size = symbols['lot_size']
        
        # Get live Option depth
        quote = self.leg_quotes.get(sym, {'bid': 100, 'ask': 102, 'ltp': 101})
        ask_price = quote['ask'] if quote['ask'] > 0 else quote['ltp']
        
        if ask_price <= 0: return
        
        # Calculate how many lots we can buy
        max_qty = int(trade_capital // (ask_price * lot_size))
        # Ensure we have at least 3 lots to stagger, if possible. Otherwise fall back to what we can afford.
        lots_to_deploy = max(1, min(3, max_qty))
        
        if lots_to_deploy < 1:
            logger.warning("Insufficient capital for 1 lot.")
            return
            
        logger.info(f"🔥 INITIATING SNIPER BUYING: {sym} | Total Lots: {lots_to_deploy}")
        
        # Staggered Limit Bidding (e.g. at Ask, Ask - 1%, Ask - 2%)
        stagger_percentages = [0.0, 0.01, 0.02]
        
        placed_qty = 0
        avg_entry = 0.0
        
        for i in range(lots_to_deploy):
            drop_pct = stagger_percentages[i % len(stagger_percentages)]
            limit_price = ask_price * (1 - drop_pct)
            
            logger.info(f"   -> Placing Limit Bid for 1 Lot at ₹{limit_price:.2f} (Chase Trigger)")
            # In a real system, we submit limit orders here. For our active tracker, we'll assume they fill at the limit.
            self.paper_executor.place_order(sym, 'BUY', lot_size, price=limit_price, is_option=True)
            placed_qty += lot_size
            avg_entry += limit_price
            
        avg_entry = avg_entry / lots_to_deploy
            
        # Register the position into the tracker for Trailing SL and Theta monitoring
        self.active_naked_positions[sym] = {
            'qty': placed_qty,
            'avg_price': avg_entry,
            'highest_price': avg_entry,
            'entry_time': time.time()
        }
        
        self.notifier.send(f"🏹 SNIPER ENTRY: {symbols['type']}\nSymbol: {sym}\nAvg Target Price: ₹{avg_entry:.2f}\nQty: {placed_qty}")

    def _enforce_exits(self, tick):
        """Actively trails stops and cuts losers based on Theta and Price."""
        instrument = tick['instrument_key']
        ltp = tick['ltp']
        
        if instrument in self.active_naked_positions:
            pos = self.active_naked_positions[instrument]
            
            # Update Highest Price for Trailing
            if ltp > pos['highest_price']:
                pos['highest_price'] = ltp
                
            entry_price = pos['avg_price']
            profit_pct = (ltp - entry_price) / entry_price * 100
            highest_profit_pct = (pos['highest_price'] - entry_price) / entry_price * 100
            
            minutes_held = (time.time() - pos['entry_time']) / 60.0
            
            # --- EXIT LOGIC ---
            reason = None
            
            # 1. Hard Stop Loss (-15%)
            if profit_pct <= -15.0:
                reason = f"HARD STOP LOSS HIT (-15%)"
                
            # 2. Trailing Stop Loss
            # If we achieved +20%, never let it fall below +5%
            elif highest_profit_pct >= 20.0 and profit_pct < 5.0:
                reason = f"TRAILING STOP (Locked +5%)"
            # If we achieved +40%, never let it fall below +20%
            elif highest_profit_pct >= 40.0 and profit_pct < 20.0:
                reason = f"TRAILING STOP (Locked +20%)"
                
            # 3. Theta / Time Decay Cut 
            # If held for > 25 mins and profit is less than 5%, kill it to avoid bleeding.
            elif minutes_held >= 25.0 and profit_pct < 5.0:
                reason = f"THETA DECAY TIMEOUT (Held {minutes_held:.1f}m, flat return)"

            if reason:
                self._close_position(instrument, reason)
                
    def _close_position(self, symbol, reason):
        pos = self.active_naked_positions[symbol]
        logger.warning(f"🔔 TRIGGERING EXIT on {symbol}: {reason}")
        self.paper_executor.place_order(symbol, 'SELL', pos['qty'], price=0, is_option=True) # Market exit
        self.notifier.send(f"🛡️ POSITION CLOSED: {symbol}\nReason: {reason}")
        del self.active_naked_positions[symbol]

    def _panic_sell_long_calls(self):
        """Emergency AI intervention to dump highly vulnerable CE trades if news is catastrophically bearish."""
        liquidations = []
        for symbol, pos in list(self.active_naked_positions.items()):
            if "CE" in symbol:
                qty = pos['qty']
                if qty > 0:
                    self.paper_executor.place_order(symbol, "SELL", qty, price=0, is_option=True)
                    logger.error(f"🔥 AI PANIC EJECTION: Liquidating {qty} {symbol} Long Calls to escape dropping knives!")
                    liquidations.append(symbol)
        
        for s in liquidations:
            del self.active_naked_positions[s]

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

    def execute_long_call_butterfly(self, instrument, spot):
        """Builds a precisely targeted 3-leg Long Call Butterfly."""
        symbols = OptionSelector.get_butterfly_symbols(instrument, spot)
        risk_per_lot = symbols['max_risk_points'] * symbols['lot_size']
        capital = self.paper_executor.get_current_capital()
        lots = min(2, int((capital * (self.max_risk_percent / 100)) // risk_per_lot))
        if lots < 1: return
        
        qty = lots * symbols['lot_size']
        logger.info(f"BUTTERFLY ENTRY: Buy ITM, Sell 2x ATM, Buy OTM | {lots} Lots")
        
        # 1 ITM Buy
        self.paper_executor.place_order(symbols['buy_itm_call'], 'BUY', qty, price=0, is_option=True)
        # 2 ATM Sells
        self.paper_executor.place_order(symbols['sell_atm_calls'], 'SELL', qty * 2, price=0, is_option=True)
        # 1 OTM Buy
        self.paper_executor.place_order(symbols['buy_otm_call'], 'BUY', qty, price=0, is_option=True)
        
        self.notifier.send(f"🦋 BUTTERFLY ENTERED\nPin Target: {spot}\nLots: {lots}")

    def execute_long_straddle(self, instrument, spot):
        """Executes a 2-leg Long Straddle (Buy ATM Call & Put) for massive volatility events."""
        capital = self.paper_executor.get_current_capital()
        max_risk_rupees = capital * (self.max_risk_percent / 100)
        
        symbols = OptionSelector.get_straddle_symbols(instrument, spot)
        
        # In a real straddle, max risk is the combined premium paid. We estimate it.
        q_call = self.leg_quotes.get(symbols['buy_call'], {'ltp': 200})
        q_put = self.leg_quotes.get(symbols['buy_put'], {'ltp': 200})
        risk_per_lot = (q_call['ltp'] + q_put['ltp']) * symbols['lot_size']
        
        lots = min(2, int((max_risk_rupees * 1.5) // risk_per_lot)) # Straddles use slightly padded risk profiles
        if lots < 1: return
        quantity = lots * symbols['lot_size']
        
        logger.info(f"STRADDLE ENTRY: Buying ATM CE & PE | {lots} Lots")
        self.paper_executor.place_order(symbols['buy_call'], 'BUY', quantity, price=0, is_option=True)
        self.paper_executor.place_order(symbols['buy_put'], 'BUY', quantity, price=0, is_option=True)
        
        # Naked Strategy Alert Manager
        self.risk_manager.log_naked_exposure("LONG_STRADDLE", quantity)
        
        self.notifier.send(f"🧨 LONG STRADDLE ENTERED\nSymbols: {symbols['buy_call']} / {symbols['buy_put']}\nLots: {lots}")

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

    def _print_dashboard(self, instrument, score, details, fg_index=None):
        """Professional Terminal Dashboard."""
        print(f"\n" + "="*70)
        print(f"📊 9% PATH CONFLUENCE CHECKER | {time.strftime('%H:%M:%S')}")
        print(f"="*70)
        
        if fg_index:
            print(f"🌡️ MARKET SENTIMENT: {fg_index['sentiment']:<15} (Score: {fg_index['score']:>5} / 100)")
            print(f"   [PCR: {fg_index['pcr_comp']:>3.0f} | VIX: {fg_index['vix_comp']:>3.0f} | RSI: {fg_index['rsi_comp']:>3.0f}]")
            print(f"-"*70)

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
