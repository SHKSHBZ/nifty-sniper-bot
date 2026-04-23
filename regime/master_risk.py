"""
MasterRiskLayer — per-trade, daily, and portfolio risk caps applied on top of
whichever tactic the router has armed.

Design:
  - One RiskDecision per entry attempt (`can_enter` returns allow/deny + reason)
  - Stateful across the trading day: tracks realized P&L, consecutive losses,
    trades taken, positions open.
  - Reset at start of each trading day via `reset_for_new_day(date)`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class DenyReason(str, Enum):
    OK = "OK"
    DAILY_LOSS_HALT = "daily_loss_halt"
    MAX_TRADES_HIT = "max_trades_hit"
    MAX_CONSEC_LOSSES = "max_consecutive_losses_hit"
    MAX_POSITIONS_OPEN = "max_concurrent_positions_open"
    OUTSIDE_ENTRY_WINDOW = "outside_entry_window"
    EVENT_BLACKOUT = "event_blackout"
    NO_TRADE_REGIME = "no_trade_regime"


@dataclass
class RiskDecision:
    allow: bool
    reason: DenyReason
    detail: str = ""


@dataclass
class RiskConfig:
    capital: float = 100_000.0
    risk_pct_per_trade: float = 0.01
    daily_loss_halt_pct: float = 0.03
    max_trades_per_day: int = 8
    max_consecutive_losses: int = 3
    max_concurrent_positions: int = 2
    no_entry_before: time = time(10, 0)
    no_entry_after: time = time(14, 30)
    force_flat_at: time = time(15, 10)


@dataclass
class _DayState:
    day: date
    realized_pnl: float = 0.0
    trades_taken: int = 0
    consecutive_losses: int = 0
    open_positions: int = 0
    halted: bool = False
    halt_reason: Optional[DenyReason] = None


class MasterRiskLayer:
    """
    Bot-wide risk caps. Consumed by the entry path:

        decision = risk.can_enter(ts, tactic_is_no_trade=False)
        if not decision.allow:
            log_and_skip(decision.reason)
            return

    Update after each close:

        risk.record_trade_close(pnl=...)
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        self._state: Optional[_DayState] = None

    # --- Lifecycle ----------------------------------------------------------

    def reset_for_new_day(self, d: date) -> None:
        """Call at the start of each trading session."""
        self._state = _DayState(day=d)

    # --- Position accounting ------------------------------------------------

    def on_position_open(self) -> None:
        self._require_state()
        self._state.open_positions += 1
        self._state.trades_taken += 1

    def record_trade_close(self, pnl: float) -> None:
        self._require_state()
        st = self._state
        st.realized_pnl += pnl
        st.open_positions = max(0, st.open_positions - 1)

        if pnl < 0:
            st.consecutive_losses += 1
        else:
            st.consecutive_losses = 0

        # Evaluate halt triggers after each close
        loss_halt = -self.config.capital * self.config.daily_loss_halt_pct
        if st.realized_pnl <= loss_halt and not st.halted:
            self._halt(DenyReason.DAILY_LOSS_HALT)
        elif st.consecutive_losses >= self.config.max_consecutive_losses and not st.halted:
            self._halt(DenyReason.MAX_CONSEC_LOSSES)

    # --- Entry gate ---------------------------------------------------------

    def can_enter(
        self,
        ts: datetime,
        *,
        tactic_is_no_trade: bool = False,
        event_blackout: bool = False,
    ) -> RiskDecision:
        self._require_state()
        st = self._state
        cfg = self.config
        t = ts.time()

        if tactic_is_no_trade:
            return RiskDecision(False, DenyReason.NO_TRADE_REGIME)
        if event_blackout:
            return RiskDecision(False, DenyReason.EVENT_BLACKOUT)
        if st.halted:
            return RiskDecision(False, st.halt_reason or DenyReason.DAILY_LOSS_HALT,
                                detail="halted earlier today")
        if t < cfg.no_entry_before or t >= cfg.no_entry_after:
            return RiskDecision(False, DenyReason.OUTSIDE_ENTRY_WINDOW,
                                detail=f"now={t} window={cfg.no_entry_before}-{cfg.no_entry_after}")
        if st.trades_taken >= cfg.max_trades_per_day:
            return RiskDecision(False, DenyReason.MAX_TRADES_HIT)
        if st.open_positions >= cfg.max_concurrent_positions:
            return RiskDecision(False, DenyReason.MAX_POSITIONS_OPEN)

        return RiskDecision(True, DenyReason.OK)

    def force_flat_now(self, ts: datetime) -> bool:
        """End-of-day squareoff trigger; true once time passes force_flat_at."""
        return ts.time() >= self.config.force_flat_at

    # --- Sizing -------------------------------------------------------------

    def position_size_lots(
        self,
        sl_premium_points: float,
        lot_size: int,
        *,
        expiry_day_multiplier: float = 1.0,
    ) -> int:
        """
        Risk-based position sizing.

            qty_lots = floor((capital * risk_pct) / (sl_premium_points * lot_size))

        Returns 0 if sizing math is invalid (e.g. sl <= 0).
        """
        if sl_premium_points <= 0 or lot_size <= 0:
            return 0
        risk_budget = self.config.capital * self.config.risk_pct_per_trade
        qty_units = risk_budget / sl_premium_points
        qty_lots = int(qty_units // lot_size)
        qty_lots = int(qty_lots * expiry_day_multiplier)
        return max(0, qty_lots)

    # --- Introspection ------------------------------------------------------

    def snapshot(self) -> dict:
        self._require_state()
        st = self._state
        return {
            "day": st.day.isoformat(),
            "realized_pnl": st.realized_pnl,
            "trades_taken": st.trades_taken,
            "consecutive_losses": st.consecutive_losses,
            "open_positions": st.open_positions,
            "halted": st.halted,
            "halt_reason": st.halt_reason.value if st.halt_reason else None,
        }

    # --- Internals ----------------------------------------------------------

    def _halt(self, reason: DenyReason) -> None:
        st = self._state
        st.halted = True
        st.halt_reason = reason
        logger.warning("risk: halting trading for %s (reason=%s)", st.day, reason.value)

    def _require_state(self) -> None:
        if self._state is None:
            raise RuntimeError(
                "MasterRiskLayer: call reset_for_new_day(date) before use"
            )
