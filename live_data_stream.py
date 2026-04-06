"""
live_data_stream.py
Upstox v2 WebSocket market data feed for Nifty 50 / Sensex.

Subscribes to FULL market data (LTP, OI, volume, Greeks) via the
Upstox Protobuf WebSocket. Also exposes helper methods to:
  - Get the current 5-minute candle OHLCV
  - Detect volume spikes (>Nx the rolling average)
  - Detect 5-min high breakouts

Dependencies:
    pip install upstox-python-sdk websocket-client protobuf
"""

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Tick:
    instrument_key: str
    ltp: float
    volume: int
    oi: int
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime


# ---------------------------------------------------------------------------
# Instrument keys for indices (Upstox format)
# ---------------------------------------------------------------------------

INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "SENSEX": "BSE_INDEX|SENSEX",
}

UPSTOX_WS_AUTH_URL = "https://api.upstox.com/v2/feed/market-data-streamer/gentoken"
UPSTOX_MARKET_DATA_URL = "https://api.upstox.com/v2/market-quote/quotes"


class LiveDataStream:
    """
    Streams live tick data from Upstox WebSocket.

    Usage:
        stream = LiveDataStream(auth_manager)
        stream.subscribe(["NIFTY", "SENSEX"])
        stream.start()

        # Later:
        tick = stream.get_latest("NIFTY")
        spike = stream.is_volume_spike("NIFTY", multiplier=3)
        breakout = stream.is_5min_high_breakout("NIFTY")
    """

    CANDLE_WINDOW = 5 * 60  # seconds

    def __init__(self, auth_manager, volume_window: int = 20):
        self.auth = auth_manager
        self.volume_window = volume_window  # candles for rolling avg

        self._subscribed_keys: list[str] = []
        self._ticks: dict[str, deque[Tick]] = {}
        self._candles: dict[str, list[Candle]] = {}
        self._current_candle: dict[str, Candle | None] = {}
        self._lock = threading.Lock()
        self._running = False
        self._ws = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(self, symbols: list[str]):
        """Subscribe to a list of symbol names (e.g. ['NIFTY', 'SENSEX'])."""
        for sym in symbols:
            key = INSTRUMENT_KEYS.get(sym, sym)
            if key not in self._subscribed_keys:
                self._subscribed_keys.append(key)
                self._ticks[key] = deque(maxlen=500)
                self._candles[key] = []
                self._current_candle[key] = None

    def start(self):
        """Start the WebSocket feed in a background thread."""
        self._running = True
        t = threading.Thread(target=self._connect_ws, daemon=True, name="UpstoxWS")
        t.start()
        logger.info("LiveDataStream started for: %s", self._subscribed_keys)

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()

    def get_latest(self, symbol: str) -> Tick | None:
        """Return the most recent tick for a symbol."""
        key = INSTRUMENT_KEYS.get(symbol, symbol)
        ticks = self._ticks.get(key)
        if ticks:
            return ticks[-1]
        return None

    def get_5min_high(self, symbol: str) -> float | None:
        """Return the HIGH of the current 5-minute candle."""
        key = INSTRUMENT_KEYS.get(symbol, symbol)
        candle = self._current_candle.get(key)
        return candle.high if candle else None

    def is_volume_spike(self, symbol: str, multiplier: float = 3.0) -> bool:
        """True if current candle volume > multiplier × rolling average volume."""
        key = INSTRUMENT_KEYS.get(symbol, symbol)
        candle = self._current_candle.get(key)
        if not candle:
            return False
        history = self._candles.get(key, [])[-self.volume_window:]
        if len(history) < 5:
            return False  # not enough history
        avg_vol = sum(c.volume for c in history) / len(history)
        return avg_vol > 0 and candle.volume > multiplier * avg_vol

    def is_5min_high_breakout(self, symbol: str) -> bool:
        """
        True if the latest LTP exceeds the 5-min candle HIGH and
        there is a volume spike (>3x avg).
        """
        tick = self.get_latest(symbol)
        high = self.get_5min_high(symbol)
        if not tick or not high:
            return False
        price_break = tick.ltp > high
        vol_spike = self.is_volume_spike(symbol, multiplier=3.0)
        if price_break and vol_spike:
            logger.info(
                "BREAKOUT DETECTED on %s: LTP=%.2f > 5min High=%.2f | Vol Spike=True",
                symbol, tick.ltp, high
            )
        return price_break and vol_spike

    # ------------------------------------------------------------------
    # REST fallback: snapshot quotes (for when WS isn't available)
    # ------------------------------------------------------------------

    def get_quote_rest(self, symbol: str) -> dict:
        """Fetch a single market quote via REST (fallback/testing)."""
        key = INSTRUMENT_KEYS.get(symbol, symbol)
        headers = self.auth.get_headers()
        resp = requests.get(
            UPSTOX_MARKET_DATA_URL,
            headers=headers,
            params={"instrument_key": key},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get(key, {})

    # ------------------------------------------------------------------
    # WebSocket internals
    # ------------------------------------------------------------------

    def _connect_ws(self):
        """Obtain a short-lived WS auth token, then open the WebSocket."""
        try:
            import websocket  # pip install websocket-client
        except ImportError:
            logger.error("websocket-client not installed. Run: pip install websocket-client")
            return

        while self._running:
            try:
                ws_token = self._get_ws_token()
                ws_url = f"wss://api.upstox.com/v2/feed/market-data-streamer?token={ws_token}"

                self._ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(reconnect=5)
            except Exception as exc:
                logger.warning("WebSocket error: %s. Reconnecting in 5s...", exc)
                time.sleep(5)

    def _get_ws_token(self) -> str:
        headers = self.auth.get_headers()
        resp = requests.get(UPSTOX_WS_AUTH_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()["data"]["authorized_redirect_uri"].split("token=")[-1]

    def _on_open(self, ws):
        logger.info("Upstox WebSocket connected.")
        subscribe_msg = {
            "guid": "nifty-sensex-stream",
            "method": "sub",
            "data": {
                "mode": "full",
                "instrumentKeys": self._subscribed_keys,
            },
        }
        ws.send(json.dumps(subscribe_msg))

    def _on_message(self, ws, message):
        """Parse incoming feed messages and update candle state."""
        try:
            # Upstox sends protobuf; for simplicity we handle JSON mode here.
            # For full protobuf, use the upstox_python_sdk MarketDataStreamer.
            data = json.loads(message)
            feeds = data.get("feeds", {})
            for key, feed_data in feeds.items():
                ltpc = feed_data.get("fullFeed", {}).get("marketFF", {}).get("ltpc", {})
                ltp = ltpc.get("ltp", 0)
                vol = feed_data.get("fullFeed", {}).get("marketFF", {}).get("tv", 0)
                oi = feed_data.get("fullFeed", {}).get("optionGreeks", {}).get("oi", 0)
                tick = Tick(instrument_key=key, ltp=ltp, volume=int(vol), oi=int(oi))
                with self._lock:
                    self._ticks[key].append(tick)
                    self._update_candle(key, tick)
        except Exception as exc:
            logger.debug("Feed parse error: %s", exc)

    def _on_error(self, ws, error):
        logger.warning("WebSocket error: %s", error)

    def _on_close(self, ws, *args):
        logger.info("WebSocket closed.")

    def _update_candle(self, key: str, tick: Tick):
        """Build 5-minute OHLCV candles from ticks."""
        now = tick.timestamp
        current = self._current_candle.get(key)

        # Check if we need a new candle (every 5 minutes)
        if current is None or (now - current.timestamp) >= timedelta(minutes=5):
            if current:
                self._candles[key].append(current)  # archive completed candle
            self._current_candle[key] = Candle(
                open=tick.ltp, high=tick.ltp, low=tick.ltp,
                close=tick.ltp, volume=tick.volume, timestamp=now
            )
        else:
            current.high = max(current.high, tick.ltp)
            current.low = min(current.low, tick.ltp)
            current.close = tick.ltp
            current.volume += tick.volume


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from auth_manager import AuthManager
    auth = AuthManager()
    stream = LiveDataStream(auth)
    stream.subscribe(["NIFTY", "SENSEX"])
    stream.start()
    logger.info("Streaming... press Ctrl+C to stop")
    try:
        while True:
            tick = stream.get_latest("NIFTY")
            if tick:
                print(f"NIFTY LTP: {tick.ltp} | Breakout: {stream.is_5min_high_breakout('NIFTY')}")
            time.sleep(2)
    except KeyboardInterrupt:
        stream.stop()
