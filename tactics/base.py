"""
Shared types for the tactics package.

A `Tactic` is a pure function over `TacticState` that returns a
`TacticSignal` or None. Tactics are stateless across calls — any state
they need (sustain counters, pyramid history, etc.) is provided in
TacticState by the caller.

Design notes:
  - Tactics never read from disk or call APIs. They only inspect their
    input state. This keeps them trivially testable and replayable
    against historical data.
  - TacticSignal includes the exit-management parameters (sl_pct, tp_pct,
    time_stop_min) so each tactic can prescribe its own risk profile.
    The simulator/live-engine respects these.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional, Literal


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class TacticState:
    """Snapshot of everything a tactic could need at one 5-minute bar."""

    # Core
    ts: datetime
    spot: float = 0.0
    futures: float = 0.0            # current-month future close (== spot if futures unavailable)
    dte: int = 99                   # days to expiry
    expiry_date: Optional[str] = None
    current_date: Optional[str] = None

    # Today's session OHLC (for OR / day-high / day-low logic)
    day_open: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    or_high: float = 0.0            # 09:15-09:30 high
    or_low: float = 0.0             # 09:15-09:30 low
    or_volume_avg: float = 0.0      # average volume across OR candles
    prev_day_close: float = 0.0

    # Indicators
    vwap: float = 0.0
    vwap_slope_30m: float = 0.0     # (vwap_now - vwap_30m_ago) / price
    ema9_5m: float = 0.0
    ema21_5m: float = 0.0
    atr_5m: float = 0.0
    adx_15m: float = 0.0
    range_ratio: float = 1.0

    # Latest 5-min candle
    bar_open: float = 0.0
    bar_high: float = 0.0
    bar_low: float = 0.0
    bar_close: float = 0.0
    bar_volume: float = 0.0
    prev_bar_open: float = 0.0
    prev_bar_high: float = 0.0
    prev_bar_low: float = 0.0
    prev_bar_close: float = 0.0

    # Recent N×5m bars for pullback / failure detection (most-recent last).
    recent_5m_lows: tuple[float, ...] = ()
    recent_5m_highs: tuple[float, ...] = ()

    # Full OHLC history of recent 5m bars (most-recent last). Used by tactics
    # that need swing-pivot / structure-break / OB / FVG analysis (IEF).
    # Each entry is (ts_iso, open, high, low, close).
    recent_5m_bars: tuple[tuple, ...] = ()

    # Option chain & macro
    support_strike: int = 0
    resistance_strike: int = 0
    focus_pcr: float = 1.0
    ce_oi_change: float = 0.0
    pe_oi_change: float = 0.0
    vix_level: float = 15.0
    vix_chg_15m: float = 0.0
    vix_chg_today_pct: float = 0.0   # VIX % change since today's open (used by T1)
    vix_open_today: float = 0.0      # raw open-of-day VIX (for diagnostics)

    # Regime classifier output (string form of Regime enum)
    regime: str = "RANGE"

    # Pyramiding context — managed by caller, supplied here read-only
    is_in_position: bool = False
    open_position_direction: Optional[str] = None  # "CE" | "PE" | None
    open_position_entry_premium: float = 0.0
    open_position_entry_ts: Optional[datetime] = None
    open_position_lots_added: int = 0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

DirectionType = Literal["CE", "PE"]
ActionType = Literal["enter", "add_lot", "exit"]


@dataclass
class TacticSignal:
    """What a tactic returns when it wants to act."""

    action: ActionType                  # "enter" | "add_lot" | "exit"
    direction: Optional[DirectionType]  # CE or PE; None for "exit"
    strike_offset: int = 0              # 0=ATM, +1 = 1 strike ITM (for CE that's spot+strike_step), etc.
    qty_pct_of_intended: float = 1.0    # 1.0 = full size; 0.5 = half (for pyramid scaling)

    # Risk management for this signal (tactic prescribes its own)
    sl_pct: float = 0.30
    tp_pct: float = 0.50
    time_stop_min: int = 120

    # Optional: override default trail / exit logic at the simulator level
    use_hybrid_trail: bool = False      # True => tactic wants EMA9-based trail

    # Audit trail
    reason: str = ""


# ---------------------------------------------------------------------------
# Config base
# ---------------------------------------------------------------------------

@dataclass
class TacticConfig:
    """Base config — subclasses add their own fields."""
    name: str = ""
    enabled: bool = True
    min_premium: float = 20.0
    no_entry_before: time = time(10, 0)
    no_entry_after: time = time(14, 30)


# ---------------------------------------------------------------------------
# Diagnostic — per-gate pass/fail with values, used for near-miss detection
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    """One gate's verdict at a moment in time."""
    passed: bool
    value: object         # actual measured value (float / bool / str)
    threshold: object     # the threshold being compared against
    description: str = "" # human-readable explanation

    def detail(self) -> str:
        """Compact single-line description suitable for journals."""
        if self.description:
            return self.description
        if isinstance(self.value, float) and isinstance(self.threshold, float):
            return f"value={self.value:.4f} threshold={self.threshold:.4f}"
        return f"value={self.value} threshold={self.threshold}"


# ---------------------------------------------------------------------------
# Abstract Tactic
# ---------------------------------------------------------------------------

class Tactic(ABC):
    """
    Subclass and implement `evaluate(state) -> Optional[TacticSignal]`.
    Tactic instances are stateless across invocations; any state the
    tactic needs comes through `state`.
    """

    config: TacticConfig

    def __init__(self, config: Optional[TacticConfig] = None):
        self.config = config or TacticConfig()

    def in_session_window(self, state: TacticState) -> bool:
        t = state.ts.time()
        return self.config.no_entry_before <= t < self.config.no_entry_after

    @abstractmethod
    def evaluate(self, state: TacticState) -> Optional[TacticSignal]:
        ...

    def gates_for_direction(
        self, state: TacticState, direction: DirectionType
    ) -> dict[str, GateResult]:
        """
        Return per-gate verdicts assuming the tactic was considering an
        entry in the given direction. Used by the runner / journal to
        identify near-misses (cases where all-but-one gate passed).

        Default implementation returns an empty dict. Each tactic
        subclass overrides with its real per-gate evaluation.
        """
        return {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.config.name})"
