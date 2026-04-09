"""
strike_selector.py
ATM / OTM strike selection for Nifty 50 and Sensex options.

Two modes:
  1. LOCAL (Ollama):  Sends spot price to local Qwen model for strike math
                      and RSI/EMA indicator calculations.
  2. FALLBACK:        Pure Python math (instant, no model required).

News sentiment gating:
  - If sentiment_score < SENTIMENT_THRESHOLD (0.6), signal is suppressed.
  - Sentiment is expected to be provided by the caller (from an AI provider).
"""

import json
import logging
import math
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:32b-coder")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", "0.6"))

STRIKE_STEPS = {
    "NIFTY": 50,
    "SENSEX": 100,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
}


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


@dataclass
class StrikeRecommendation:
    symbol: str
    spot_price: float
    atm_strike: int
    recommended_strike: int
    option_type: OptionType
    signal_strength: float        # 0.0 – 1.0
    sentiment_score: float
    reasoning: str
    blocked_by_sentiment: bool = False


# ---------------------------------------------------------------------------
# Core selector
# ---------------------------------------------------------------------------

class StrikeSelector:
    """
    Selects the optimal option strike given market conditions.

    Usage:
        selector = StrikeSelector()
        rec = selector.select(
            symbol="NIFTY",
            spot_price=24350.5,
            direction="bullish",      # or "bearish"
            sentiment_score=0.75,
            rsi=62.3,
            ema_fast=24300,
            ema_slow=24200,
        )
    """

    def __init__(self, use_ollama: bool = True):
        self.use_ollama = use_ollama and self._ollama_available()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(
        self,
        symbol: str,
        spot_price: float,
        direction: str,         # "bullish" or "bearish"
        sentiment_score: float,
        rsi: Optional[float] = None,
        ema_fast: Optional[float] = None,
        ema_slow: Optional[float] = None,
        otm_buffer: int = 0,    # number of strikes away from ATM (0 = ATM)
    ) -> StrikeRecommendation:
        
        symbol = symbol.upper()
        step = STRIKE_STEPS.get(symbol, 50)
        atm = self._round_to_strike(spot_price, step)

        # Sentiment gate
        if sentiment_score < SENTIMENT_THRESHOLD:
            return StrikeRecommendation(
                symbol=symbol,
                spot_price=spot_price,
                atm_strike=atm,
                recommended_strike=atm,
                option_type=OptionType.CE if direction == "bullish" else OptionType.PE,
                signal_strength=0.0,
                sentiment_score=sentiment_score,
                reasoning=f"Signal BLOCKED: sentiment score {sentiment_score:.2f} < threshold {SENTIMENT_THRESHOLD}",
                blocked_by_sentiment=True,
            )

        # Indicator-based signal strength
        signal_strength = self._compute_signal_strength(
            direction=direction, rsi=rsi, ema_fast=ema_fast, ema_slow=ema_slow
        )

        # Strike: ATM ± buffer
        option_type = OptionType.CE if direction == "bullish" else OptionType.PE
        if direction == "bullish":
            rec_strike = atm + otm_buffer * step
        else:
            rec_strike = atm - otm_buffer * step

        reasoning = f"ATM={atm} | Step={step} | RSI={rsi:.1f if rsi else 'N/A'} | Signal={signal_strength:.2f}"

        # Optionally delegate to Ollama for enhanced reasoning
        if self.use_ollama:
            ollama_reasoning = self._query_ollama(
                symbol=symbol, spot=spot_price, atm=atm,
                direction=direction, rsi=rsi, ema_fast=ema_fast,
                ema_slow=ema_slow, sentiment_score=sentiment_score
            )
            if ollama_reasoning:
                reasoning = ollama_reasoning

        return StrikeRecommendation(
            symbol=symbol,
            spot_price=spot_price,
            atm_strike=atm,
            recommended_strike=rec_strike,
            option_type=option_type,
            signal_strength=signal_strength,
            sentiment_score=sentiment_score,
            reasoning=reasoning,
        )

    # ------------------------------------------------------------------
    # Strike math
    # ------------------------------------------------------------------

    @staticmethod
    def _round_to_strike(price: float, step: int) -> int:
        """Round price to nearest valid strike (e.g. Nifty → multiple of 50)."""
        return int(round(price / step) * step)

    @staticmethod
    def _compute_signal_strength(
        direction: str,
        rsi: Optional[float],
        ema_fast: Optional[float],
        ema_slow: Optional[float],
    ) -> float:
        """Simple 0-1 signal strength from RSI + EMA crossover."""
        score = 0.5  # baseline

        if rsi is not None:
            if direction == "bullish":
                # RSI 55-70 ideal for momentum; above 70 = overbought risk
                score += 0.2 if 55 <= rsi <= 70 else (-0.1 if rsi > 75 else 0.0)
            else:
                score += 0.2 if 30 <= rsi <= 45 else (-0.1 if rsi < 25 else 0.0)

        if ema_fast is not None and ema_slow is not None:
            if direction == "bullish" and ema_fast > ema_slow:
                score += 0.2
            elif direction == "bearish" and ema_fast < ema_slow:
                score += 0.2

        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------
    # Ollama integration
    # ------------------------------------------------------------------

    def _ollama_available(self) -> bool:
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            logger.info("Ollama not reachable — using pure-Python fallback for strike math.")
            return False

    def _query_ollama(self, **kwargs) -> Optional[str]:
        """Ask local Qwen model to validate/explain the strike selection."""
        prompt = f"""
You are a Nifty/Sensex options trading analyst. Given the following market data:
- Symbol: {kwargs['symbol']}
- Spot Price: {kwargs['spot']}
- ATM Strike: {kwargs['atm']} (step size: {STRIKE_STEPS.get(kwargs['symbol'], 50)})
- Direction: {kwargs['direction']}
- RSI: {kwargs.get('rsi', 'N/A')}
- EMA Fast: {kwargs.get('ema_fast', 'N/A')}
- EMA Slow: {kwargs.get('ema_slow', 'N/A')}
- Sentiment Score: {kwargs['sentiment_score']:.2f} (threshold: {SENTIMENT_THRESHOLD})

In ONE sentence, confirm the ATM strike is correct and state whether this is a high-confidence trade.
Do NOT use markdown. Respond with a plain sentence only.
"""
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as exc:
            logger.warning("Ollama query failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Technical indicator helpers
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_rsi(prices: list[float], period: int = 14) -> float:
        """Calculate RSI from a list of closing prices."""
        if len(prices) < period + 1:
            return 50.0  # neutral default
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period or 1e-9
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calculate_ema(prices: list[float], period: int) -> float:
        """Calculate EMA for the given period."""
        if not prices:
            return 0.0
        k = 2 / (period + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    selector = StrikeSelector(use_ollama=True)

    # Example: Nifty bullish breakout
    rec = selector.select(
        symbol="NIFTY",
        spot_price=24357.30,
        direction="bullish",
        sentiment_score=0.78,
        rsi=63.5,
        ema_fast=24320,
        ema_slow=24250,
    )
    print(f"\n{'='*60}")
    print(f"Symbol:     {rec.symbol}")
    print(f"Spot:       {rec.spot_price}")
    print(f"ATM Strike: {rec.atm_strike}")
    print(f"Recommended:{rec.recommended_strike} {rec.option_type.value}")
    print(f"Signal:     {rec.signal_strength:.2f}")
    print(f"Sentiment:  {rec.sentiment_score:.2f}")
    print(f"Blocked:    {rec.blocked_by_sentiment}")
    print(f"Reasoning:  {rec.reasoning}")
    print(f"{'='*60}\n")
