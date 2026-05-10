"""T2 — Expiry-Day Long Straddle (variant S2: -50% combined SL).

Wraps the validated logic from backtesting/expiry_straddle.py for live
execution. The S2 variant produced +Rs.67,997 over 18 months on
Rs.20,000/trade sizing with 18 trades and 50% win rate — equivalent to
+45.0% annual on Rs.1,00,000 capital.

Entry rule (only fires on weekly expiry days):
    - DTE == 0 (today is the expiry being traded)
    - Time in [14:50, 15:00] (single qualifying tick per day suffices)
    - Both ATM CE and ATM PE premiums in [Rs.5, Rs.20] band
      (cheap = small theta tail risk, max upside on a sharp move)

Order:
    BUY equal lots of ATM CE + ATM PE (the straddle)

Exit (S2 variant):
    - Combined SL: combined_premium <= 50% of entry combined
    - Time exit: 15:25 IST (force-close both legs at market)
    - No TP (let winners run to time-exit)

The tactic returns a TacticSignal with `direction="CE"` + `second_direction="PE"`.
The caller (main.py) recognises `signal.is_straddle` and routes to its
straddle execution path (separate `open_straddle` portfolio slot, combined-
premium monitoring, two-leg force-close).

Premium gates need data from the option chain — TacticState doesn't carry
ATM CE/PE premiums today. We expose those via two new optional fields:
    state.atm_ce_premium  (caller populates from fetcher.get_option_ltp)
    state.atm_pe_premium
If the dispatcher hasn't populated them, the tactic safely skips
(returns None) — gates report "data_unavailable" instead of firing blind.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from typing import Optional

from tactics.base import (
    Tactic, TacticConfig, TacticState, TacticSignal, GateResult,
)


@dataclass
class T2Config(TacticConfig):
    name: str = "T2_Expiry_Straddle"
    enabled: bool = True

    # Entry window
    decision_time_start: dtime = dtime(14, 50)
    decision_time_end: dtime = dtime(15, 0)

    # Premium band per leg
    min_premium_per_leg: float = 5.0
    max_premium_per_leg: float = 20.0

    # Combined SL (S2 variant of the backtest)
    combined_sl_pct: float = 0.50           # close if combined drops 50%
    combined_tp_pct: Optional[float] = None # no TP — let winners run

    # Time stop = minutes from 14:50 entry to 15:25 EOD = 35 min
    time_stop_min: int = 35

    # Override base session window so T2 only evaluates inside its window
    no_entry_before: dtime = dtime(14, 50)
    no_entry_after: dtime = dtime(15, 1)


class T2ExpiryStraddleTactic(Tactic):
    """Stateless T2 entry rule. Caller enforces 'at most one entry per day'
    via the dispatcher's `_t2_fired_date` flag.
    """

    config: T2Config

    def __init__(self, config: Optional[T2Config] = None):
        super().__init__(config or T2Config())

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

        # 3. Premium gates (need both legs available)
        ce_premium = float(getattr(state, "atm_ce_premium", 0.0) or 0.0)
        pe_premium = float(getattr(state, "atm_pe_premium", 0.0) or 0.0)

        ce_in_band = cfg.min_premium_per_leg <= ce_premium <= cfg.max_premium_per_leg
        ce_gate = GateResult(
            passed=ce_in_band,
            value=round(ce_premium, 2),
            threshold=f"[{cfg.min_premium_per_leg}, {cfg.max_premium_per_leg}]",
            description=(f"ATM CE premium {ce_premium:.2f} "
                        f"{'in' if ce_in_band else 'out of'} band"),
        )

        pe_in_band = cfg.min_premium_per_leg <= pe_premium <= cfg.max_premium_per_leg
        pe_gate = GateResult(
            passed=pe_in_band,
            value=round(pe_premium, 2),
            threshold=f"[{cfg.min_premium_per_leg}, {cfg.max_premium_per_leg}]",
            description=(f"ATM PE premium {pe_premium:.2f} "
                        f"{'in' if pe_in_band else 'out of'} band"),
        )

        # 4. Direction consistency: T2 is direction-agnostic (it's a straddle).
        # The near-miss probe asks per-direction; we always pass.
        direction_gate = GateResult(
            passed=True, value=direction_hint, threshold="any",
            description="T2 is a straddle — direction-agnostic",
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

        ce_premium = float(getattr(state, "atm_ce_premium", 0.0) or 0.0)
        pe_premium = float(getattr(state, "atm_pe_premium", 0.0) or 0.0)
        combined = ce_premium + pe_premium

        return TacticSignal(
            action="enter",
            direction="CE",                        # leg 1
            strike_offset=0,                       # ATM
            qty_pct_of_intended=1.0,
            second_direction="PE",                 # leg 2
            second_strike_offset=0,                # ATM (same strike as CE)
            combined_sl_pct=self.config.combined_sl_pct,
            combined_tp_pct=self.config.combined_tp_pct,
            # Per-leg sl/tp are unused for straddle — caller checks combined_*
            sl_pct=self.config.combined_sl_pct,
            tp_pct=0.0,
            time_stop_min=self.config.time_stop_min,
            use_hybrid_trail=False,
            reason=(f"T2 Expiry Straddle: ATM CE={ce_premium:.2f} + "
                    f"PE={pe_premium:.2f} = combined {combined:.2f} "
                    f"(SL -{self.config.combined_sl_pct*100:.0f}% combined, "
                    f"force-close 15:25)"),
        )

    def gates_for_direction(
        self, state: TacticState, direction
    ) -> dict[str, GateResult]:
        return self._gates(state, direction_hint=direction)
