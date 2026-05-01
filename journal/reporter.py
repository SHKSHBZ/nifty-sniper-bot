"""
Daily Markdown report generator.

Layout:
    # Trading Journal — YYYY-MM-DD
    ## Summary
    ## Trades
        ### Trade N — WIN/LOSS — entry-time → exit-time — tactic + direction
            **Entry reason**
            **Trade execution**
            **Why it [profited/lost]**
            **What could have been better** (counterfactuals)
            **Suggestions for code change**
    ## Missed Entries (Near-Misses)
        ### Missed N — HH:MM — tactic + direction
            **Tactic wanted to fire because:** ...
            **Blocker:** ...
            **Counterfactual:** would have made/lost ...
            **Suggestion:** ...
    ## Events Log

The aim: a trader can read the day's report top to bottom and
understand what happened, why, and what to improve.
"""
from __future__ import annotations

from pathlib import Path

from journal.models import JournalDay, ExecutedTrade, MissedEntry


def write_daily_report(day: JournalDay, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"journal_{day.day.isoformat()}.md"

    lines: list[str] = []
    lines.append(f"# Trading Journal — {day.day.isoformat()} "
                 f"({day.day.strftime('%a')})\n")

    # ---- Summary ----
    lines.append("## Summary\n")
    n_trades = len(day.trades)
    n_missed = len(day.missed)
    pnl_sign = "+" if day.realized_pnl >= 0 else ""
    lines.append(f"- Trades executed: **{n_trades}** "
                 f"(wins: {day.win_count} / losses: {day.loss_count})")
    lines.append(f"- Realized P&L: **{pnl_sign}₹{day.realized_pnl:,.0f}**")
    lines.append(f"- Cumulative equity after day: ₹{day.cumulative_pnl_after_day:,.0f}")
    lines.append(f"- Near-miss entries logged: {n_missed}")
    lines.append("")

    # ---- Per-trade narratives ----
    if day.trades:
        lines.append("## Executed Trades\n")
        for i, t in enumerate(day.trades, 1):
            _append_trade(lines, i, t)
    else:
        lines.append("## Executed Trades\n")
        lines.append("(No trades executed today.)\n")

    # ---- Missed entries ----
    if day.missed:
        lines.append("## Missed Entries (Near-Misses)\n")
        lines.append("These are bars where a tactic was *almost* going to fire "
                     "but a single specific gate blocked it. Use to identify "
                     "thresholds worth tuning.\n")
        for i, m in enumerate(day.missed, 1):
            _append_missed(lines, i, m)
    else:
        lines.append("## Missed Entries (Near-Misses)\n")
        lines.append("(No near-misses recorded today.)\n")

    # ---- Events ----
    if day.events:
        lines.append("## Events Log\n")
        lines.append("| Time | Kind | Message |")
        lines.append("|---|---|---|")
        for e in day.events:
            lines.append(f"| {e.ts.strftime('%H:%M')} | {e.kind} | {e.message} |")
        lines.append("")

    report_path.write_text("\n".join(lines))
    return report_path


def _append_trade(lines: list, idx: int, t: ExecutedTrade) -> None:
    outcome = "WIN" if t.net_pnl > 0 else "LOSS"
    pnl_sign = "+" if t.net_pnl >= 0 else ""
    title = (f"### Trade {idx} — {outcome} — "
             f"{t.entry_ts.strftime('%H:%M')} → {t.exit_ts.strftime('%H:%M')} — "
             f"`{t.tactic}` {t.direction}")
    lines.append(f"\n{title}\n")
    lines.append(f"- Strike: **{t.strike}**, lots: {t.qty_lots}")
    lines.append(f"- Entry premium: ₹{t.entry_premium:.0f}, "
                 f"Exit premium: ₹{t.exit_premium:.0f}")
    lines.append(f"- Net P&L: **{pnl_sign}₹{t.net_pnl:,.0f}**")
    lines.append(f"- Exit reason: `{t.exit_reason}`")
    lines.append("")
    lines.append("**Why it fired (entry):**")
    lines.append(f"  {t.entry_reason}")
    lines.append("")
    lines.append("**How it exited:**")
    lines.append(f"  {t.exit_reason_text}")
    lines.append("")
    lines.append("**Why it won/lost:**")
    lines.append(f"  {t.win_lose_explanation}")
    lines.append("")

    # Optimal exit
    if t.optimal_exit_premium > 0 and t.path_close:
        lines.append("**What could have been better — optimal-exit analysis:**")
        lines.append(f"  - Best post-entry premium: ₹{t.optimal_exit_premium:.0f} "
                     f"at {t.optimal_exit_ts.strftime('%H:%M') if t.optimal_exit_ts else 'n/a'}")
        lines.append(f"  - Captured {t.captured_pct_of_optimum:.0f}% of the "
                     f"available profit")
        lines.append("")

    if t.counterfactual_notes:
        lines.append("**Counterfactual: alternative exits tested:**")
        for note in t.counterfactual_notes:
            lines.append(note)
        lines.append("")

    if t.suggestions:
        lines.append("**Suggestions:**")
        for s in t.suggestions:
            lines.append(f"  - {s}")
        lines.append("")


def _append_missed(lines: list, idx: int, m: MissedEntry) -> None:
    title = (f"### Missed {idx} — {m.ts.strftime('%H:%M')} — "
             f"`{m.tactic}` {m.direction} blocked")
    lines.append(f"\n{title}\n")
    lines.append("**Blocker:**")
    lines.append(f"  - {m.blocker_detail} (gate: `{m.blocked_by}`)")
    lines.append("")
    if m.hypothetical_outcome:
        sign = "+" if m.hypothetical_pnl >= 0 else ""
        lines.append("**Counterfactual — would have happened if we had taken it:**")
        lines.append(f"  - Hypothetical strike: {m.hypothetical_strike}")
        lines.append(f"  - Hypothetical entry premium: "
                     f"₹{m.hypothetical_entry_premium:.0f}")
        lines.append(f"  - Hypothetical exit premium: "
                     f"₹{m.hypothetical_exit_premium:.0f}")
        lines.append(f"  - Hypothetical P&L: **{sign}₹{m.hypothetical_pnl:,.0f}** "
                     f"(outcome: {m.hypothetical_outcome})")
        if m.sl_pct > 0 or m.tp_pct > 0 or m.time_stop_min > 0:
            lines.append(f"  - Tactic exit params used: "
                         f"SL -{m.sl_pct*100:.0f}% / TP +{m.tp_pct*100:.0f}% "
                         f"/ time {m.time_stop_min}m")
        if m.poll_count > 0:
            lines.append(f"  - Tracking window: {m.time_stop_min} min, "
                         f"{m.poll_count} polls")
        if m.hypothetical_explanation:
            lines.append(f"  - {m.hypothetical_explanation}")
        lines.append("")

    # Suggestions
    suggestion = _missed_entry_suggestion(m)
    if suggestion:
        lines.append("**Suggestion:**")
        lines.append(f"  - {suggestion}")
        lines.append("")


def _missed_entry_suggestion(m: MissedEntry) -> str:
    if m.hypothetical_outcome == "WIN":
        return (f"This blocker rejected a winning trade. Investigate whether "
                f"the `{m.blocked_by}` gate is too strict — a small relaxation "
                f"may unlock real edge. Note: do not change a single threshold "
                f"based on one trade; collect a sample then re-test.")
    if m.hypothetical_outcome == "LOSS":
        return (f"Blocker correctly avoided a losing trade. Keep the "
                f"`{m.blocked_by}` threshold as is.")
    if m.hypothetical_outcome == "BREAKEVEN":
        return (f"Skipped trade was a wash; blocker did not cost or save P&L "
                f"meaningfully on this instance.")
    return ""
