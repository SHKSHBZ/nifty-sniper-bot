"""
seller_engine.py — Option Seller Bot Regime & Strategy Execution Core.

Responsibilities:
  1. Volatility Crush Mode Detection & State Propagation:
     - Tracks 15-minute rolling straddle premium decay and spot price range.
     - When straddle decay > 3% over 15m with range-bound spot (<=30 pts or <=0.3%),
       triggers VOLATILITY_CRUSH regime and locks out directional buyers (buyer_locked = True).
  2. Directional Hedging Mode:
     - When directional buyer enters Long CE (or Long PE), generates counter short leg
       (Short PE or Short CE) to capture theta decay on the losing side.
  3. Short Premium Strategy Execution:
     - Short Straddle (selling ATM CE + PE) and Short Strangle (selling OTM CE + PE).
     - Dynamic strike selection based on live spot price: `atm_strike = round(spot / step) * step`.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal

from regime.classifier import Regime
from risk.seller_risk import OptionSellerRiskManager, RiskEvaluationResult
from tactics.seller_tactics import (
    ShortStraddleTactic,
    ShortStrangleTactic,
    get_atm_strike,
    get_straddle_strikes,
    get_strangle_strikes,
)

log = logging.getLogger(__name__)


@dataclass
class SellerEngineConfig:
    strike_step: int = 50
    straddle_decay_threshold: float = 0.03   # 3% decay in 15 mins
    spot_range_max_pts: float = 30.0         # 30 pts max spot range
    spot_range_max_pct: float = 0.003        # 0.3% max spot range
    lockdown_duration_min: int = 15
    otm_strangle_steps: int = 2
    directional_hedging_enabled: bool = True
    seller_enabled: bool = True


@dataclass
class HedgedOrderPayload:
    primary_leg: dict                        # Long CE or Long PE
    counter_leg: dict                        # Counter Short PE or Short CE
    is_hedged: bool = True
    timestamp: Optional[str] = None


class OptionSellerEngine:
    """
    Core Option Seller Engine managing multi-regime execution, buyer lockouts,
    Short Straddle/Strangle strategies, Directional Hedging Mode, and Strict Risk Management.
    """

    def __init__(
        self,
        config: Optional[SellerEngineConfig] = None,
        premium_analyzer: Optional[Any] = None,
        risk_manager: Optional[OptionSellerRiskManager] = None,
    ):
        self.config = config or SellerEngineConfig()
        self.risk_manager = risk_manager or OptionSellerRiskManager(
            premium_analyzer=premium_analyzer,
            default_spot_range_pts=self.config.spot_range_max_pts,
        )

        # Rolling buffers for 15-minute straddle decay tracking
        self._recent_spots = deque(maxlen=15)
        self._recent_ces = deque(maxlen=15)
        self._recent_pes = deque(maxlen=15)
        self._recent_timestamps = deque(maxlen=15)

        # State flags
        self.regime: str = Regime.RANGE.value
        self.buyer_locked: bool = False
        self.seller_activated: bool = False
        self.lockdown_until: Optional[datetime] = None
        self.last_ts: Optional[datetime] = None
        self.last_decay_pct: float = 0.0
        self.last_spot_range: float = 0.0

        # Tactics
        self.straddle_tactic = ShortStraddleTactic()
        self.strangle_tactic = ShortStrangleTactic()

    def update_ticks(
        self,
        ts: datetime,
        spot: float,
        ce_prem: float,
        pe_prem: float,
    ) -> str:
        """
        Record tick data and evaluate Volatility Crush regime.
        Returns the current regime string ("VOLATILITY_CRUSH", "CHOP", "RANGE", etc.).
        """
        self.last_ts = ts
        if spot <= 0:
            return self.regime

        self._recent_spots.append(spot)
        self._recent_ces.append(ce_prem)
        self._recent_pes.append(pe_prem)
        self._recent_timestamps.append(ts)

        # Evaluate decay over 15-tick window
        if len(self._recent_spots) >= 15:
            spot_min, spot_max = min(self._recent_spots), max(self._recent_spots)
            spot_range = spot_max - spot_min
            spot_range_pct = spot_range / spot if spot > 0 else 0.0

            straddles = [c + p for c, p in zip(self._recent_ces, self._recent_pes)]
            peak_straddle = max(straddles)
            current_straddle = straddles[-1]

            decay_pct = (peak_straddle - current_straddle) / max(peak_straddle, 1.0)
            self.last_decay_pct = decay_pct
            self.last_spot_range = spot_range

            is_range_bound = (spot_range <= self.config.spot_range_max_pts or spot_range_pct <= self.config.spot_range_max_pct)

            if decay_pct >= self.config.straddle_decay_threshold and is_range_bound:
                self.regime = Regime.VOLATILITY_CRUSH.value
                self.buyer_locked = True
                self.seller_activated = True
                self.lockdown_until = ts + timedelta(minutes=self.config.lockdown_duration_min)
                log.info(
                    "VOLATILITY_CRUSH triggered: straddle decayed %.2f%% over 15m (spot range: %.1f pts). "
                    "Buyer locked until %s",
                    decay_pct * 100.0, spot_range, self.lockdown_until.isoformat()
                )
                return self.regime

        # Check lockdown expiry
        if self.lockdown_until and ts >= self.lockdown_until:
            if self.regime == Regime.VOLATILITY_CRUSH.value:
                self.regime = Regime.RANGE.value
                self.buyer_locked = False
                self.seller_activated = False
                log.info("Volatility Crush lockdown expired at %s", ts.isoformat())

        return self.regime

    def _is_lockdown_active(self, check_ts: Optional[datetime] = None) -> bool:
        if not self.lockdown_until:
            return False
        if check_ts is None:
            check_ts = self.last_ts
        if check_ts is None:
            if self.lockdown_until.tzinfo is not None:
                check_ts = datetime.now(timezone.utc)
            else:
                check_ts = datetime.now()

        lock_until = self.lockdown_until
        # Handle naive vs timezone-aware safely
        if lock_until.tzinfo is not None and check_ts.tzinfo is None:
            check_ts = check_ts.replace(tzinfo=lock_until.tzinfo)
        elif lock_until.tzinfo is None and check_ts.tzinfo is not None:
            check_ts = check_ts.replace(tzinfo=None)

        return check_ts < lock_until

    def set_regime(self, regime: str | Regime, ts: Optional[datetime] = None) -> None:
        reg_val = regime.value if isinstance(regime, Regime) else str(regime)
        self.regime = reg_val
        if reg_val in (Regime.VOLATILITY_CRUSH.value, Regime.CHOP.value):
            self.buyer_locked = True
            self.seller_activated = True
            check_ts = ts or self.last_ts
            if check_ts:
                self.lockdown_until = check_ts + timedelta(minutes=self.config.lockdown_duration_min)
        else:
            if not self._is_lockdown_active(ts):
                self.buyer_locked = False

    def create_directional_hedge(self, buyer_signal: dict, spot: float) -> HedgedOrderPayload:
        """
        Directional Hedging Mode (R1.2):
        When buyer takes a trade (e.g. Long CE), simultaneously trigger counter
        short leg (e.g. Short PE) to harvest theta decay on the losing counter-leg.
        """
        raw_dir = buyer_signal.get("direction")
        buyer_dir = (raw_dir or "CE").upper()
        counter_dir = "PE" if buyer_dir == "CE" else "CE"
        step = self.config.strike_step
        atm_strike = get_atm_strike(spot, step)

        primary_leg = {
            "symbol": buyer_signal.get("symbol", "NIFTY"),
            "strike": buyer_signal.get("strike", atm_strike),
            "option_type": buyer_dir,
            "transaction_type": "BUY",
            "quantity": buyer_signal.get("quantity", 50),
            "role": "directional_buyer",
        }

        counter_leg = {
            "symbol": buyer_signal.get("symbol", "NIFTY"),
            "strike": atm_strike,
            "option_type": counter_dir,
            "transaction_type": "SELL",
            "quantity": buyer_signal.get("quantity", 50),
            "role": "seller_theta_hedge",
            "sl_pct": 0.30,
            "tp_pct": 0.50,
        }

        return HedgedOrderPayload(
            primary_leg=primary_leg,
            counter_leg=counter_leg,
            is_hedged=True,
            timestamp=buyer_signal.get("timestamp"),
        )

    def generate_short_straddle(
        self,
        spot: float,
        quantity: int = 50,
        symbol: str = "NIFTY",
    ) -> dict:
        """
        Generate Short Straddle payload (selling ATM CE + ATM PE).
        """
        step = self.config.strike_step
        atm_strike = get_atm_strike(spot, step)

        return {
            "strategy": "short_straddle",
            "symbol": symbol,
            "spot": spot,
            "atm_strike": atm_strike,
            "ce_leg": {
                "strike": atm_strike,
                "option_type": "CE",
                "transaction_type": "SELL",
                "quantity": quantity,
            },
            "pe_leg": {
                "strike": atm_strike,
                "option_type": "PE",
                "transaction_type": "SELL",
                "quantity": quantity,
            },
            "is_short": True,
            "combined_sl_pct": 0.30,
            "combined_tp_pct": 0.50,
        }

    def generate_short_strangle(
        self,
        spot: float,
        otm_steps: Optional[int] = None,
        quantity: int = 50,
        symbol: str = "NIFTY",
    ) -> dict:
        """
        Generate Short Strangle payload (selling OTM CE + OTM PE).
        """
        step = self.config.strike_step
        steps = otm_steps if otm_steps is not None else self.config.otm_strangle_steps
        ce_strike, pe_strike = get_strangle_strikes(spot, step, steps)

        return {
            "strategy": "short_strangle",
            "symbol": symbol,
            "spot": spot,
            "ce_strike": ce_strike,
            "pe_strike": pe_strike,
            "otm_steps": steps,
            "ce_leg": {
                "strike": ce_strike,
                "option_type": "CE",
                "transaction_type": "SELL",
                "quantity": quantity,
            },
            "pe_leg": {
                "strike": pe_strike,
                "option_type": "PE",
                "transaction_type": "SELL",
                "quantity": quantity,
            },
            "is_short": True,
            "combined_sl_pct": 0.30,
            "combined_tp_pct": 0.50,
        }

    def register_position(
        self,
        position_id: str,
        strategy_type: str,
        spot: float,
        ce_strike: Optional[int] = None,
        ce_entry_premium: float = 0.0,
        pe_strike: Optional[int] = None,
        pe_entry_premium: float = 0.0,
        single_leg_strike: Optional[int] = None,
        single_leg_type: Optional[str] = None,
        single_leg_entry_premium: float = 0.0,
        spot_range_high: Optional[float] = None,
        spot_range_low: Optional[float] = None,
        leg_sl_pct: Optional[float] = None,
        combined_sl_pct: Optional[float] = None,
        trail_sl_pct: Optional[float] = None,
    ):
        return self.risk_manager.register_position(
            position_id=position_id,
            strategy_type=strategy_type,
            spot=spot,
            ce_strike=ce_strike,
            ce_entry_premium=ce_entry_premium,
            pe_strike=pe_strike,
            pe_entry_premium=pe_entry_premium,
            single_leg_strike=single_leg_strike,
            single_leg_type=single_leg_type,
            single_leg_entry_premium=single_leg_entry_premium,
            spot_range_high=spot_range_high,
            spot_range_low=spot_range_low,
            leg_sl_pct=leg_sl_pct,
            combined_sl_pct=combined_sl_pct,
            trail_sl_pct=trail_sl_pct,
        )

    def evaluate_active_positions(
        self,
        live_spot: float,
        ce_ltp: float = 0.0,
        pe_ltp: float = 0.0,
        single_leg_ltp: float = 0.0,
    ) -> list[RiskEvaluationResult]:
        results = []
        for pos_id, pos in list(self.risk_manager.active_positions.items()):
            res = self.risk_manager.evaluate_position(
                position_id=pos_id,
                live_spot=live_spot,
                ce_ltp=ce_ltp,
                pe_ltp=pe_ltp,
                single_leg_ltp=single_leg_ltp,
            )
            if res.should_exit:
                results.append(res)
        return results

