"""T3 — Expiry-Day OTM Long Strangle.

Implements the Fyers Options Strategy guidelines for a Long Strangle.
Neutral on direction, extremely bullish on volatility/breakouts.
Utilizes Out-of-the-Money (OTM) options to significantly lower debit cost (net premium paid)
relative to a Straddle, while capping downside strictly to the net premium.

Entry rule (only fires on weekly expiry days):
    - DTE == 0 (today is the expiry being traded)
    - Time in [14:50, 15:00] (qualifying tick per day)
    - OTM Call strike (+1 offset) and OTM Put strike (-1 offset) premiums in target band [Rs.2, Rs.12]
      (extremely cheap, maximum gamma leverage on a sharp trend extension)

Order:
    BUY equal lots of OTM CE (+1 strike) + OTM PE (-1 strike)

Exit:
    - Combined SL: combined_premium <= 50% of entry combined
    - Time exit: 15:25 IST (force-close both legs at market)
    - No TP (let winners run to time-exit)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from typing import Optional

from tactics.base import (
    Tactic, TacticConfig, TacticState, TacticSignal, GateResult,
)


@dataclass
class T3Config(TacticConfig):
    name: str = "T3_Expiry_Strangle"
    enabled: bool = True

    # Entry window
    decision_time_start: dtime = dtime(14, 50)
    decision_time_end: dtime = dtime(15, 0)

    # Strangle Strike Offset (in steps of strike step, e.g. 1 step = 50 pts on Nifty)
    strike_offset_steps: int = 1

    # Premium band per leg (OTM premiums are cheaper than ATM ATM 5-20)
    min_premium_per_leg: float = 2.0
    max_premium_per_leg: float = 12.0

    # Combined SL
    combined_sl_pct: float = 0.50           # close if combined drops 50%
    combined_tp_pct: Optional[float] = None # let winners run

    # Time stop = minutes from 14:50 entry to 15:25 EOD = 35 min
    time_stop_min: int = 35

    # Override base session window so T3 only evaluates inside its window
    no_entry_before: dtime = dtime(14, 50)
    no_entry_after: dtime = dtime(15, 1)


class T3ExpiryStrangleTactic(Tactic):
    """Stateless T3 strangle entry rule.
    """

    config: T3Config

    def __init__(self, config: Optional[T3Config] = None):
        super().__init__(config or T3Config())

    def _gates(
        self, state: TacticState, direction_hint: Optional[str] = None
    ) -> dict[str, GateResult]:
        cfg = self.config
        t = state.ts.time()

        # 1. Time window
        in_window = cfg.decision_time_start <= t <= cfg.decision_time_end
        time_gate = GateResult(
            passed=in_window,
            value=t.strftime("%H:%M:%S"),
            threshold=f"{cfg.decision_time_start}-{cfg.decision_time_end}",
            description=("inside" if in_window else "outside") + " entry window",
        )

        # 2. Expiry day only (DTE == 0)
        is_expiry = state.dte == 0
        expiry_gate = GateResult(
            passed=is_expiry,
            value=state.dte,
            threshold="DTE == 0",
            description=("today is" if is_expiry else "today is NOT") + " an expiry day",
        )

        # 3. Premium gates (need both legs available from state)
        # TacticState doesn't carry arbitrary OTM premiums directly.
        # We assume the caller exposes:
        #   state.otm_ce_premium (calculated at ATM + offset)
        #   state.otm_pe_premium (calculated at ATM - offset)
        # If not populated, we try to fall back to ATM attributes (for safety in testing)
        ce_premium = float(getattr(state, "otm_ce_premium", 0.0) or getattr(state, "atm_ce_premium", 0.0) or 0.0)
        pe_premium = float(getattr(state, "otm_pe_premium", 0.0) or getattr(state, "atm_pe_premium", 0.0) or 0.0)

        ce_in_band = cfg.min_premium_per_leg <= ce_premium <= cfg.max_premium_per_leg
        ce_gate = GateResult(
            passed=ce_in_band,
            value=round(ce_premium, 2),
            threshold=f"[{cfg.min_premium_per_leg}, {cfg.max_premium_per_leg}]",
            description=(f"OTM CE premium {ce_premium:.2f} "
                         f"{'in' if ce_in_band else 'out of'} band"),
        )

        pe_in_band = cfg.min_premium_per_leg <= pe_premium <= cfg.max_premium_per_leg
        pe_gate = GateResult(
            passed=pe_in_band,
            value=round(pe_premium, 2),
            threshold=f"[{cfg.min_premium_per_leg}, {cfg.max_premium_per_leg}]",
            description=(f"OTM PE premium {pe_premium:.2f} "
                         f"{'in' if pe_in_band else 'out of'} band"),
        )

        # 4. Direction consistency
        direction_gate = GateResult(
            passed=True, value=direction_hint, threshold="any",
            description="T3 is a strangle — direction-agnostic",
        )

        return {
            "time_window":   time_gate,
            "expiry_day":    expiry_gate,
            "ce_premium":    ce_gate,
            "pe_premium":    pe_gate,
            "direction":     direction_gate,
        }

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        gates = self._gates(state)

        # All gates must pass
        if not (gates["time_window"].passed
                and gates["expiry_day"].passed
                and gates["ce_premium"].passed
                and gates["pe_premium"].passed):
            return None

        ce_premium = float(getattr(state, "otm_ce_premium", 0.0) or getattr(state, "atm_ce_premium", 0.0) or 0.0)
        pe_premium = float(getattr(state, "otm_pe_premium", 0.0) or getattr(state, "atm_pe_premium", 0.0) or 0.0)
        combined = ce_premium + pe_premium

        return TacticSignal(
            action="enter",
            direction="CE",                                # leg 1
            strike_offset=self.config.strike_offset_steps, # OTM strike offset for CE (+1)
            qty_pct_of_intended=1.0,
            second_direction="PE",                         # leg 2
            second_strike_offset=-self.config.strike_offset_steps, # OTM strike offset for PE (-1)
            combined_sl_pct=self.config.combined_sl_pct,
            combined_tp_pct=self.config.combined_tp_pct,
            sl_pct=self.config.combined_sl_pct,
            tp_pct=0.0,
            time_stop_min=self.config.time_stop_min,
            use_hybrid_trail=False,
            reason=(f"T3 Expiry Strangle: OTM CE={ce_premium:.2f} (offset +{self.config.strike_offset_steps}) + "
                    f"PE={pe_premium:.2f} (offset -{self.config.strike_offset_steps}) = combined {combined:.2f} "
                    f"(SL -{self.config.combined_sl_pct*100:.0f}% combined, "
                    f"force-close 15:25)"),
        )

    def gates_for_direction(
        self, state: TacticState, direction
    ) -> dict[str, GateResult]:
        return self._gates(state, direction_hint=direction)
