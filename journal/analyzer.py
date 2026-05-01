"""
Post-trade analyzer.

For each ExecutedTrade, produces:
  - entry_reason: a clean narrative of WHICH conditions triggered entry
  - exit_reason_text: HOW the trade exited and what that means
  - win_lose_explanation: WHY the trade made/lost money in plain English
  - optimal_exit_premium / ts: the BEST possible exit on the recorded path
  - captured_pct_of_optimum: how much of the available profit we captured
  - counterfactual_notes: alternative SL / TP / time stops and their P&L
  - suggestions: improvement ideas based on observed behavior

For MissedEntry, computes whether the would-be trade would have been
profitable using simple SL/TP simulation.
"""
from __future__ import annotations

from typing import Optional

from journal.models import ExecutedTrade, MissedEntry


# ---------------------------------------------------------------------------
# Trade analysis
# ---------------------------------------------------------------------------

def _explain_entry(trade: ExecutedTrade) -> str:
    s = trade.entry_state or {}
    parts: list[str] = []
    parts.append(f"`{trade.tactic}` armed in regime `{trade.regime_at_entry}`.")

    if trade.tactic == "vwap_hybrid":
        if "vwap" in s and "spot" in s:
            dist = abs(s.get("spot", 0) - s.get("vwap", 0))
            side = "below" if s.get("spot", 0) < s.get("vwap", 0) else "above"
            parts.append(f"Spot {s.get('spot'):,.0f} was extended {side} VWAP "
                         f"{s.get('vwap'):,.0f} by {dist:.0f} points.")
        if "focus_pcr" in s:
            parts.append(f"Focus-zone PCR {s['focus_pcr']:.2f}, "
                         f"OI bias {'PE' if trade.direction == 'CE' else 'CE'} "
                         f"writers were defending the wall.")
    elif trade.tactic == "trend_pullback":
        parts.append(
            f"After a confirmed afternoon trend, price pulled back to EMA9 and "
            f"reclaimed it on the 5m candle (close > prev close, close > "
            f"midpoint). OI ratio favoured the move."
        )
        if "adx_15m" in s:
            parts.append(f"ADX(15m) was {s['adx_15m']:.1f} — strong trend.")
    elif trade.tactic == "bullish_orb":
        parts.append("Two consecutive 5m candles closed above the OR high "
                     "with breakout volume >= 1.5x average.")
    elif trade.tactic == "bearish_orb":
        parts.append("Two consecutive 5m candles closed below the OR low "
                     "with breakdown volume >= 1.5x average.")
    else:
        parts.append("(Reason details unavailable.)")

    parts.append(f"Bought 1-strike-ITM {trade.direction} at {trade.strike} "
                 f"strike at premium ₹{trade.entry_premium:.0f}.")
    return " ".join(parts)


def _explain_exit(trade: ExecutedTrade) -> str:
    held_min = max(1, int((trade.exit_ts - trade.entry_ts).total_seconds() / 60))
    pnl_str = f"+₹{trade.net_pnl:,.0f}" if trade.net_pnl >= 0 else f"-₹{abs(trade.net_pnl):,.0f}"
    if trade.exit_reason == "TP":
        return (f"Take-profit hit at +{trade.tp_pct*100:.0f}% premium gain after "
                f"{held_min} minutes. Net P&L: {pnl_str}.")
    if trade.exit_reason == "SL":
        return (f"Stop-loss triggered at -{trade.sl_pct*100:.0f}% premium drawdown "
                f"after {held_min} minutes. Net P&L: {pnl_str}.")
    if trade.exit_reason == "TIME_STOP":
        return (f"Time-stop at {trade.time_stop_min} minutes — neither TP nor SL "
                f"was reached. Exited at market close of the {held_min}-minute "
                f"holding bar. Net P&L: {pnl_str}.")
    if trade.exit_reason in ("EOD", "EOD_FORCE"):
        return (f"Forced flat at end of session ({trade.exit_ts.strftime('%H:%M')}) "
                f"after {held_min} minutes in trade. Net P&L: {pnl_str}.")
    return f"Exited via {trade.exit_reason}. Net P&L: {pnl_str}."


def _explain_outcome(trade: ExecutedTrade) -> str:
    move_pct = (trade.exit_premium - trade.entry_premium) / trade.entry_premium * 100
    if trade.net_pnl > 0:
        return (
            f"WIN. Premium moved from ₹{trade.entry_premium:.0f} to "
            f"₹{trade.exit_premium:.0f} ({move_pct:+.1f}%). The setup played out "
            f"as expected — the {'reclaim of support' if trade.direction == 'CE' else 'rejection at resistance'} "
            f"was followed by directional follow-through."
        )
    return (
        f"LOSS. Premium moved from ₹{trade.entry_premium:.0f} to "
        f"₹{trade.exit_premium:.0f} ({move_pct:+.1f}%). The setup did not "
        f"deliver — possible reasons: option theta decay during the "
        f"{(trade.exit_ts - trade.entry_ts).seconds // 60}-minute hold, "
        f"directional thesis broke, or the underlying chopped sideways."
    )


def _compute_optimal_exit(trade: ExecutedTrade) -> tuple[float, Optional[object], float]:
    """
    Walk the recorded path and find the highest close price post-entry.
    Return (optimal_premium, optimal_ts, captured_pct).
    """
    if not trade.path_close:
        return trade.exit_premium, trade.exit_ts, 100.0
    best_idx = 0
    best_close = float("-inf")
    for i, c in enumerate(trade.path_close):
        if c > best_close:
            best_close = c
            best_idx = i
    optimal_premium = best_close
    optimal_ts = trade.path_ts[best_idx] if best_idx < len(trade.path_ts) else trade.exit_ts

    max_possible = optimal_premium - trade.entry_premium
    actual = trade.exit_premium - trade.entry_premium
    if max_possible <= 0:
        captured_pct = 100.0 if actual >= 0 else 0.0
    else:
        captured_pct = max(0.0, min(100.0, (actual / max_possible) * 100))
    return optimal_premium, optimal_ts, captured_pct


def _counterfactuals(trade: ExecutedTrade) -> list[str]:
    """
    Replay the path under alternative SL/TP/time combinations to see if a
    different exit configuration would have produced a better outcome.
    """
    notes: list[str] = []
    if not trade.path_close:
        return notes
    entry = trade.entry_premium

    alt_combos = [
        ("Tighter TP 35%", 0.35, trade.sl_pct, trade.time_stop_min),
        ("Tighter TP 25%", 0.25, trade.sl_pct, trade.time_stop_min),
        ("Wider TP 75%",   0.75, trade.sl_pct, trade.time_stop_min),
        ("Tighter SL 20%", trade.tp_pct, 0.20, trade.time_stop_min),
        ("Shorter time 45m", trade.tp_pct, trade.sl_pct, 45),
    ]
    for label, tp, sl, t_min in alt_combos:
        sim_pnl = _simulate_exit(trade, tp_pct=tp, sl_pct=sl, time_stop_min=t_min)
        if sim_pnl is None:
            continue
        delta = sim_pnl - trade.net_pnl
        if abs(delta) < 50:
            continue   # not worth flagging
        comparator = "better" if delta > 0 else "worse"
        notes.append(f"  - **{label}** would have produced ₹{sim_pnl:+,.0f} "
                     f"(₹{delta:+,.0f} {comparator} than actual).")
    return notes


def _simulate_exit(
    trade: ExecutedTrade,
    *,
    tp_pct: float,
    sl_pct: float,
    time_stop_min: int,
) -> Optional[float]:
    if not trade.path_close:
        return None
    entry = trade.entry_premium
    tp = entry * (1 + tp_pct)
    sl = entry * (1 - sl_pct)
    exit_premium = trade.path_close[-1]
    for i, ts in enumerate(trade.path_ts):
        if i == 0:
            continue
        elapsed = (ts - trade.entry_ts).total_seconds() / 60
        ohigh = trade.path_high[i] if i < len(trade.path_high) else trade.path_close[i]
        olow = trade.path_low[i] if i < len(trade.path_low) else trade.path_close[i]
        if ohigh >= tp:
            exit_premium = tp
            break
        if olow <= sl:
            exit_premium = sl
            break
        if elapsed >= time_stop_min:
            exit_premium = trade.path_close[i]
            break
    SLIPPAGE = 0.015
    LOT_SIZE = 75
    eff_entry = entry * (1 + SLIPPAGE)
    eff_exit = exit_premium * (1 - SLIPPAGE)
    return (eff_exit - eff_entry) * LOT_SIZE - 60.0   # less Rs 60 brokerage


def suggest_improvements(trade: ExecutedTrade) -> list[str]:
    s: list[str] = []
    if trade.exit_reason == "TIME_STOP":
        s.append("Trade exited on time-stop without hitting TP or SL — the target "
                 "was probably unrealistic for the available move; consider a "
                 "tighter TP target or trailing exit for this tactic.")
    if trade.captured_pct_of_optimum < 50 and trade.net_pnl > 0:
        s.append(f"Captured only {trade.captured_pct_of_optimum:.0f}% of the "
                 f"available profit — the underlying continued moving in our "
                 f"favour after exit. Investigate trailing-stop logic.")
    if trade.exit_reason == "SL" and trade.tactic in ("trend_pullback", "vwap_hybrid"):
        s.append("Stop-loss triggered. Was the OI / sentiment confirmation "
                 "too lagging? Check whether the gates were stale relative "
                 "to the actual price action.")
    if trade.tactic == "bullish_orb" and trade.exit_reason == "EOD":
        s.append("ORB held to end of day without TP — possible the breakout "
                 "stalled. Consider stricter volume confirmation on entry.")
    if not s:
        s.append("Trade played out as designed. No specific improvement suggested.")
    return s


def analyze_trade(trade: ExecutedTrade) -> ExecutedTrade:
    """Populate analytical fields in-place. Returns the same trade for chaining."""
    trade.entry_reason = _explain_entry(trade)
    trade.exit_reason_text = _explain_exit(trade)
    trade.win_lose_explanation = _explain_outcome(trade)
    optimal_prem, optimal_ts, captured = _compute_optimal_exit(trade)
    trade.optimal_exit_premium = optimal_prem
    trade.optimal_exit_ts = optimal_ts
    trade.captured_pct_of_optimum = captured
    trade.counterfactual_notes = _counterfactuals(trade)
    trade.suggestions = suggest_improvements(trade)
    return trade


# ---------------------------------------------------------------------------
# Missed entries
# ---------------------------------------------------------------------------

def analyze_missed(missed: MissedEntry, hypothetical_path) -> MissedEntry:
    """
    `hypothetical_path` is a list of (ts, close) tuples for the option that
    WOULD have been bought. Computes the hypothetical PnL using the
    blocker tactic's prescribed SL/TP/time-stop.
    """
    if not hypothetical_path:
        missed.hypothetical_outcome = "UNKNOWN"
        missed.hypothetical_explanation = (
            "No option price data available for the candidate strike at the "
            "missed-entry timestamp."
        )
        return missed

    entry_prem = missed.hypothetical_entry_premium
    if entry_prem <= 0:
        # Use the first close as entry
        entry_prem = hypothetical_path[0][1]
        missed.hypothetical_entry_premium = entry_prem

    # Simple replay: TP +50%, SL -30%, time stop 90 min — neutral defaults
    tp = entry_prem * 1.50
    sl = entry_prem * 0.70

    exit_prem = hypothetical_path[-1][1]
    exit_reason = "EOD"
    for ts, close in hypothetical_path[1:]:
        if close >= tp:
            exit_prem = tp
            exit_reason = "TP"
            break
        if close <= sl:
            exit_prem = sl
            exit_reason = "SL"
            break

    missed.hypothetical_exit_premium = exit_prem
    LOT = 75
    pnl = (exit_prem - entry_prem) * LOT - 60.0
    missed.hypothetical_pnl = pnl

    if pnl > 200:
        missed.hypothetical_outcome = "WIN"
    elif pnl < -200:
        missed.hypothetical_outcome = "LOSS"
    else:
        missed.hypothetical_outcome = "BREAKEVEN"

    missed.hypothetical_explanation = (
        f"If the {missed.blocked_by} gate had allowed entry, the trade would "
        f"have hit {exit_reason} (premium ₹{entry_prem:.0f} → ₹{exit_prem:.0f}, "
        f"P&L ₹{pnl:+,.0f})."
    )
    return missed
