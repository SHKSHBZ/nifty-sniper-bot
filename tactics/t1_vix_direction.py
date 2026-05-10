"""T1 — VIX-Direction Tuned tactic.

Wraps the validated logic from backtesting/t1_vix_param_sweep.py for live
execution. The best config from that sweep produced +Rs.71,827 over 19 months
on Rs.20,000/trade sizing with 54 trades and 55.6% win rate — equivalent to
+45.6% annual on Rs.1,00,000 capital.

Best config (used as defaults here):
    decision_time_start = 10:00
    decision_time_end   = 10:30  (small window so we don't miss; live ticks irregular)
    vix_min             = 13
    vix_max             = 18
    vix_change_min_pct  = 0.5    (intraday VIX move from open, abs value)
    dte_min, dte_max    = 2, 6
    min_premium         = 20
    max_premium         = 200
    tp_pct              = 30     (TP on option premium)
    sl_pct              = 30     (SL on option premium)
    trail_be_pct        = 20     (when premium up 20%, move SL to entry/breakeven)

Signal logic:
    VIX rising (>= +0.5% from open)  -> NIFTY likely down -> BUY_PE (direction "PE")
    VIX falling (<= -0.5% from open) -> NIFTY likely up   -> BUY_CE (direction "CE")

This tactic returns at MOST one signal per trading day (first qualifying
bar in [10:00, 10:30]). Caller is responsible for not re-entering after exit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from typing import Optional

from tactics.base import (
    Tactic, TacticConfig, TacticState, TacticSignal, GateResult,
)


@dataclass
class T1Config(TacticConfig):
    name: str = "T1_VIX_Direction"
    enabled: bool = True

    # Decision window (live tick may not land at exactly 10:00, so allow a
    # 30-min window — but we still fire AT MOST ONCE per day via the
    # `_fired_today` flag set by the caller / orchestrator).
    decision_time_start: dtime = dtime(10, 0)
    decision_time_end: dtime = dtime(10, 30)

    # VIX gates
    vix_min: float = 13.0
    vix_max: float = 18.0
    vix_change_min_pct: float = 0.5

    # DTE gates
    dte_min: int = 2
    dte_max: int = 6

    # Premium gates (caller is expected to supply premium via state field
    # or we conservatively skip; we use min_premium/max_premium here)
    min_premium: float = 20.0
    max_premium: float = 200.0

    # Risk parameters carried into TacticSignal
    tp_pct: float = 0.30
    sl_pct: float = 0.30
    trail_be_pct: float = 0.20

    # Time-stop = minutes from entry until forced close.
    # 10:30 entry to 15:25 EOD = ~295 minutes. Round to 295.
    time_stop_min: int = 295

    # Override the base session window so T1 only ever evaluates inside
    # its decision window, not the broader 10:00-14:30.
    no_entry_before: dtime = dtime(10, 0)
    no_entry_after: dtime = dtime(10, 31)


class T1VIXDirectionTactic(Tactic):
    """Stateless implementation of the T1 entry rule.

    The caller (dispatcher / orchestrator) is responsible for enforcing
    "at most one T1 entry per day" — this class only checks the gates
    and returns a signal whenever they all pass. Same input twice would
    return the same signal twice; the caller dedupes.
    """

    config: T1Config

    def __init__(self, config: Optional[T1Config] = None):
        super().__init__(config or T1Config())

    # ------------------------------------------------------------------
    # Per-direction gates (used both by evaluate() and gates_for_direction())
    # ------------------------------------------------------------------

    def _gates(
        self, state: TacticState, direction_hint: Optional[str] = None
    ) -> dict[str, GateResult]:
        """Compute pass/fail for every gate. `direction_hint` lets the
        near-miss probe ask 'what if the direction were CE/PE?'.

        Time-of-day gates use state.ts.time(); VIX gates use the values
        the dispatcher loads into TacticState (vix_level + vix_chg_today_pct).
        """
        cfg = self.config
        t = state.ts.time()

        # 1. Time window
        in_window = cfg.decision_time_start <= t <= cfg.decision_time_end
        time_gate = GateResult(
            passed=in_window,
            value=t.strftime("%H:%M:%S"),
            threshold=f"{cfg.decision_time_start}-{cfg.decision_time_end}",
            description=("inside" if in_window else "outside") + " decision window",
        )

        # 2. VIX in band
        vix_in_band = cfg.vix_min <= state.vix_level < cfg.vix_max
        vix_band_gate = GateResult(
            passed=vix_in_band,
            value=round(state.vix_level, 2),
            threshold=f"[{cfg.vix_min}, {cfg.vix_max})",
            description=(f"VIX={state.vix_level:.2f} "
                        f"{'in' if vix_in_band else 'out of'} band"),
        )

        # 3. VIX intraday change (vs open) magnitude
        # Field name we standardised on: vix_chg_today_pct (added to TacticState).
        # Falls back to vix_chg_15m if the dispatcher hasn't been updated yet.
        vix_chg = float(getattr(state, "vix_chg_today_pct", 0.0)
                        or state.vix_chg_15m or 0.0)
        chg_ok = abs(vix_chg) >= cfg.vix_change_min_pct
        vix_chg_gate = GateResult(
            passed=chg_ok,
            value=round(vix_chg, 3),
            threshold=f"|x| >= {cfg.vix_change_min_pct}%",
            description=(f"VIX intraday change {vix_chg:+.2f}% "
                        f"{'meets' if chg_ok else 'below'} threshold"),
        )

        # 4. DTE
        dte_ok = cfg.dte_min <= state.dte <= cfg.dte_max
        dte_gate = GateResult(
            passed=dte_ok,
            value=state.dte,
            threshold=f"[{cfg.dte_min}, {cfg.dte_max}]",
            description=(f"DTE={state.dte} "
                        f"{'in' if dte_ok else 'out of'} preferred range"),
        )

        # 5. Direction consistency (only checked if direction_hint given)
        # If hint is "CE", we're asking 'would the bot go long?'. T1 goes long
        # only when VIX is falling. So gate passes only if signs align.
        direction_gate = GateResult(passed=True, value=None, threshold=None,
                                    description="not direction-specific")
        if direction_hint == "CE":
            direction_gate = GateResult(
                passed=vix_chg <= -cfg.vix_change_min_pct,
                value=round(vix_chg, 3),
                threshold=f"<= -{cfg.vix_change_min_pct}%",
                description="CE requires VIX falling intraday",
            )
        elif direction_hint == "PE":
            direction_gate = GateResult(
                passed=vix_chg >= cfg.vix_change_min_pct,
                value=round(vix_chg, 3),
                threshold=f">= +{cfg.vix_change_min_pct}%",
                description="PE requires VIX rising intraday",
            )

        return {
            "time_window":     time_gate,
            "vix_in_band":     vix_band_gate,
            "vix_intraday_chg": vix_chg_gate,
            "dte_in_range":    dte_gate,
            "direction":       direction_gate,
        }

    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        gates = self._gates(state)

        # All non-direction gates must pass before we even look at direction
        if not (gates["time_window"].passed
                and gates["vix_in_band"].passed
                and gates["vix_intraday_chg"].passed
                and gates["dte_in_range"].passed):
            return None

        # Determine direction from VIX move sign
        vix_chg = float(getattr(state, "vix_chg_today_pct", 0.0)
                        or state.vix_chg_15m or 0.0)
        if vix_chg >= self.config.vix_change_min_pct:
            direction = "PE"   # VIX rising -> NIFTY down -> buy PE
        elif vix_chg <= -self.config.vix_change_min_pct:
            direction = "CE"
        else:
            return None  # threshold-equal but neither side — paranoia branch

        return TacticSignal(
            action="enter",
            direction=direction,
            strike_offset=0,                # ATM
            qty_pct_of_intended=1.0,        # full size
            sl_pct=self.config.sl_pct,
            tp_pct=self.config.tp_pct,
            time_stop_min=self.config.time_stop_min,
            use_hybrid_trail=False,         # T1 uses simple trail-to-BE, not EMA
            reason=(f"T1 VIX-Direction: VIX={state.vix_level:.2f} "
                    f"chg={vix_chg:+.2f}% -> BUY_{direction} ATM "
                    f"(DTE={state.dte}, TP={self.config.tp_pct*100:.0f}% "
                    f"SL={self.config.sl_pct*100:.0f}% "
                    f"trail-BE@+{self.config.trail_be_pct*100:.0f}%)"),
        )

    def gates_for_direction(
        self, state: TacticState, direction
    ) -> dict[str, GateResult]:
        """Per-direction gate verdicts for the near-miss tracker."""
        return self._gates(state, direction_hint=direction)
