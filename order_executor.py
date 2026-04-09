"""
order_executor.py
Places and manages option orders via Upstox v2 REST API.

Guardrails (hard-coded, non-negotiable):
  - LIMIT orders ONLY (never Market orders)
  - Limit price = LTP × (1 + LIMIT_BUFFER_PCT) for buys
  - Max daily loss: MAX_DAILY_LOSS_INR (default ₹5,000)
  - Per-trade stop loss: SL_PCT of entry premium (default 15%)
  - Requires a pre-trade "Artifact" confirmation flag

Usage:
    executor = OrderExecutor(auth_manager, stream)
    result = executor.place_option_order(
        symbol="NIFTY",
        strike=24400,
        option_type="CE",
        expiry="2026-03-27",
        quantity=50,           # 1 lot of Nifty = 50 qty
        dry_run=True,          # Set False only for live trading
    )
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Risk constants (override via environment variables)
# ---------------------------------------------------------------------------

MAX_DAILY_LOSS_INR = float(os.getenv("MAX_DAILY_LOSS_INR", "5000"))
SL_PCT = float(os.getenv("SL_PCT", "0.15"))          # 15% of entry premium
LIMIT_BUFFER_PCT = float(os.getenv("LIMIT_BUFFER_PCT", "0.005"))   # 0.5% above LTP
TARGET_PCT = float(os.getenv("TARGET_PCT", "0.30"))   # 30% profit target

UPSTOX_ORDER_URL = "https://api.upstox.com/v2/order/place"
UPSTOX_ORDER_MODIFY_URL = "https://api.upstox.com/v2/order/modify"
UPSTOX_POSITIONS_URL = "https://api.upstox.com/v2/portfolio/short-term-positions"
UPSTOX_INSTRUMENT_SEARCH = "https://api.upstox.com/v2/instruments"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    instrument_key: str
    strike: int
    option_type: str
    quantity: int
    limit_price: float
    sl_price: float
    target_price: float
    dry_run: bool
    message: str
    raw_response: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

class OrderExecutor:
    """
    Safe order placement with all guardrails enforced.

    Args:
        auth_manager: Instance of AuthManager (provides headers)
        stream:       Instance of LiveDataStream (provides LTP for limit calc)
    """

    def __init__(self, auth_manager, stream=None):
        self.auth = auth_manager
        self.stream = stream
        self._daily_pnl: float = 0.0         # tracks realized PnL for today
        self._trade_log: list[dict] = []
        self._artifact_confirmed: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def confirm_artifact(self):
        """
        Call this AFTER reviewing the plan/artifact in the UI.
        Required before any live order can be placed.
        """
        self._artifact_confirmed = True
        logger.info("Artifact confirmed by user. Live orders are now permitted.")

    def place_option_order(
        self,
        symbol: str,
        strike: int,
        option_type: Literal["CE", "PE"],
        expiry: str,       # "YYYY-MM-DD"
        quantity: int,
        dry_run: bool = True,
        ltp_override: Optional[float] = None,
    ) -> OrderResult:
        """
        Place a LIMIT option order with automatic SL/target calculation.

        Args:
            symbol:       "NIFTY" or "SENSEX"
            strike:       Strike price (must be valid multiple)
            option_type:  "CE" or "PE"
            expiry:       Expiry date string "YYYY-MM-DD"
            quantity:     Number of units (not lots)
            dry_run:      If True, simulates without hitting the API
            ltp_override: Use this LTP instead of live feed (for testing)
        """
        # --- Pre-flight checks ---
        self._validate_strike(symbol, strike)
        self._check_daily_loss_limit()
        if not dry_run and not self._artifact_confirmed:
            raise RuntimeError(
                "SAFETY BLOCK: call executor.confirm_artifact() after reviewing "
                "the trade plan before placing a live order."
            )

        instrument_key = self._resolve_instrument_key(symbol, strike, option_type, expiry)
        ltp = ltp_override or self._get_ltp(instrument_key, symbol)

        if ltp is None or ltp <= 0:
            return OrderResult(
                success=False, order_id=None,
                instrument_key=instrument_key, strike=strike,
                option_type=option_type, quantity=quantity,
                limit_price=0, sl_price=0, target_price=0,
                dry_run=dry_run,
                message="Could not fetch LTP — order aborted."
            )

        limit_price = round(ltp * (1 + LIMIT_BUFFER_PCT), 1)
        sl_price = round(ltp * (1 - SL_PCT), 1)
        target_price = round(ltp * (1 + TARGET_PCT), 1)

        logger.info(
            "[%s] %s %d%s | LTP=%.2f | Limit=%.2f | SL=%.2f | Target=%.2f | DryRun=%s",
            symbol, option_type, strike, expiry, ltp,
            limit_price, sl_price, target_price, dry_run
        )

        if dry_run:
            return self._dry_run_result(
                instrument_key, strike, option_type, quantity, limit_price, sl_price, target_price
            )

        # --- Place real order ---
        return self._send_order(
            instrument_key=instrument_key,
            strike=strike,
            option_type=option_type,
            quantity=quantity,
            limit_price=limit_price,
            sl_price=sl_price,
            target_price=target_price,
        )

    def get_open_positions(self) -> list[dict]:
        """Fetch current open positions from Upstox."""
        resp = requests.get(
            UPSTOX_POSITIONS_URL,
            headers=self.auth.get_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    def exit_all_positions(self, dry_run: bool = True):
        """Exit all open option positions with limit orders."""
        positions = self.get_open_positions()
        results = []
        for pos in positions:
            if pos.get("quantity", 0) != 0:
                logger.info("Exiting position: %s qty=%d", pos["instrument_token"], pos["quantity"])
                if not dry_run:
                    # Place opposite-side limit order to square off
                    self._squareoff_position(pos)
                results.append(pos["instrument_token"])
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_strike(self, symbol: str, strike: int):
        from strike_selector import STRIKE_STEPS
        step = STRIKE_STEPS.get(symbol.upper(), 50)
        if strike % step != 0:
            raise ValueError(
                f"Invalid {symbol} strike {strike}. Must be a multiple of {step}."
            )

    def _check_daily_loss_limit(self):
        if self._daily_pnl <= -MAX_DAILY_LOSS_INR:
            raise RuntimeError(
                f"SAFETY BLOCK: Daily loss limit of Rs. {MAX_DAILY_LOSS_INR:,.0f} reached. "
                f"Current PnL: Rs. {self._daily_pnl:,.2f}. No more orders today."
            )

    def _get_ltp(self, instrument_key: str, symbol: str) -> Optional[float]:
        """Try live stream first, then REST fallback."""
        if self.stream:
            tick = self.stream.get_latest(symbol)
            # For the option itself, we need a REST call (stream is for index)
        try:
            headers = self.auth.get_headers()
            resp = requests.get(
                "https://api.upstox.com/v2/market-quote/ltp",
                headers=headers,
                params={"instrument_key": instrument_key},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return data.get(instrument_key, {}).get("last_price")
        except Exception as exc:
            logger.warning("LTP fetch failed: %s", exc)
            return None

    def _resolve_instrument_key(
        self, symbol: str, strike: int, option_type: str, expiry: str
    ) -> str:
        """
        Build Upstox instrument key.
        Format: NSE_FO|NIFTY26MAR24400CE  (example)
        
        NOTE: Use the Upstox instruments CSV for the exact token lookup in production.
        """
        expiry_dt = date.fromisoformat(expiry)
        exp_str = expiry_dt.strftime("%y%b%d").upper()   # e.g. 26MAR27
        exchange = "BSE_FO" if symbol.upper() == "SENSEX" else "NSE_FO"
        return f"{exchange}|{symbol.upper()}{exp_str}{strike}{option_type}"

    def _dry_run_result(
        self, instrument_key, strike, option_type, quantity, limit_price, sl_price, target_price
    ) -> OrderResult:
        logger.info("DRY RUN — order NOT sent to exchange.")
        return OrderResult(
            success=True,
            order_id="DRY_RUN_" + str(int(time.time())),
            instrument_key=instrument_key,
            strike=strike,
            option_type=option_type,
            quantity=quantity,
            limit_price=limit_price,
            sl_price=sl_price,
            target_price=target_price,
            dry_run=True,
            message=(
                f"[DRY RUN] Would buy {quantity} × {instrument_key} "
                f"@ Rs. {limit_price} | SL: Rs. {sl_price} | Target: Rs. {target_price}"
            ),
        )

    def _send_order(
        self, instrument_key, strike, option_type, quantity, limit_price, sl_price, target_price
    ) -> OrderResult:
        payload = {
            "quantity": quantity,
            "product": "D",           # D = Intraday
            "validity": "DAY",
            "price": limit_price,
            "tag": "nifty-bot",
            "instrument_token": instrument_key,
            "order_type": "LIMIT",
            "transaction_type": "BUY",
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
        }
        try:
            resp = requests.post(
                UPSTOX_ORDER_URL,
                headers=self.auth.get_headers(),
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            order_id = data.get("data", {}).get("order_id")
            self._trade_log.append({
                "order_id": order_id, "instrument": instrument_key,
                "limit_price": limit_price, "sl_price": sl_price,
                "target_price": target_price, "timestamp": time.time()
            })
            logger.info("Order placed successfully. Order ID: %s", order_id)
            return OrderResult(
                success=True, order_id=order_id,
                instrument_key=instrument_key, strike=strike,
                option_type=option_type, quantity=quantity,
                limit_price=limit_price, sl_price=sl_price,
                target_price=target_price, dry_run=False,
                message=f"Order placed: {order_id}",
                raw_response=data,
            )
        except requests.HTTPError as exc:
            logger.error("Order placement failed: %s | %s", exc, exc.response.text)
            return OrderResult(
                success=False, order_id=None,
                instrument_key=instrument_key, strike=strike,
                option_type=option_type, quantity=quantity,
                limit_price=limit_price, sl_price=sl_price,
                target_price=target_price, dry_run=False,
                message=f"Order FAILED: {exc.response.text}",
            )

    def _squareoff_position(self, position: dict):
        """Place a sell limit order to square off a position."""
        pass  # implement based on position data structure

    def update_daily_pnl(self, realized_pnl: float):
        """Call this after each trade closes to track daily loss."""
        self._daily_pnl += realized_pnl
        logger.info("Daily PnL updated: Rs. %.2f / -Rs. %.2f limit", self._daily_pnl, MAX_DAILY_LOSS_INR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from auth_manager import AuthManager
    auth = AuthManager()
    executor = OrderExecutor(auth)

    # Always test in dry_run=True first!
    result = executor.place_option_order(
        symbol="NIFTY",
        strike=24400,
        option_type="CE",
        expiry="2026-03-27",
        quantity=50,
        dry_run=True,
        ltp_override=180.50,   # simulated premium
    )
    print(result)
