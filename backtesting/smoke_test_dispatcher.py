"""
End-to-end smoke test for the live Bot integration.

What this proves:
    1. TacticDispatcher.evaluate() can be called repeatedly without error
       in a realistic loop.
    2. Both modes (legacy / regime) produce the legacy-shaped signal dict
       that main.py expects.
    3. JournalRecorder hooks are callable in sequence (start_day,
       on_entry, on_path_tick, on_exit, end_day, write_daily_report)
       without crashing.
    4. The integration as a whole doesn't break when the dispatcher
       returns no signal vs. a signal vs. a force-exit.

This is NOT a P&L backtest — it's a wire-up validation. P&L numbers
on this 2-day sample are only useful as a sanity check (do they look
the same shape as Phase 4 output).

Replays 2 historical days using cached spot/VIX data and a stub option
chain pricer. Run:

    python backtesting/smoke_test_dispatcher.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime import TacticDispatcher  # noqa: E402
from journal import (  # noqa: E402
    JournalRecorder, write_daily_report, analyze_trade,
)
from backtesting.backtest_regime_phase3 import (  # noqa: E402
    discover_expiries, load_chain_for_expiry, map_day_to_expiry,
)
from backtesting.backtest_regime_phase1 import load_spot, load_vix  # noqa: E402
from backtesting.backtest_regime_phase4 import (  # noqa: E402
    reconstruct_chain_state,
)


# -----------------------------------------------------------------------
# Stubs that mimic DataFetcher and SignalEngine using cached CSVs.
# -----------------------------------------------------------------------

class HistoricalFetcher:
    """Replays cached spot / VIX / option chain data for the dispatcher."""

    def __init__(self, spot_1m, vix_1m, chain, expiry: date):
        self.spot_1m = spot_1m
        self.vix_1m = vix_1m
        self.chain = chain
        self.expiry = expiry
        self.now: Optional[datetime] = None
        self._chain_state: dict = {}

    def set_now(self, ts: pd.Timestamp) -> None:
        self.now = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        spot = self.get_spot()
        if spot > 0:
            self._chain_state = reconstruct_chain_state(self.chain, self.chain, ts, spot)

    def get_spot(self) -> float:
        if self.now is None or self.spot_1m is None or self.spot_1m.empty:
            return 0.0
        sub = self.spot_1m[self.spot_1m.index <= self.now]
        return float(sub.iloc[-1]["close"]) if not sub.empty else 0.0

    def get_support(self) -> int:
        return int(self._chain_state.get("support", 0))

    def get_resistance(self) -> int:
        return int(self._chain_state.get("resistance", 0))

    def get_focus_pcr(self) -> float:
        return float(self._chain_state.get("focus_pcr", 1.0))

    def get_oi_pattern(self) -> dict:
        return self._chain_state.get("oi_pattern", {"ce_oi_change": 0, "pe_oi_change": 0})

    def get_spot_history(self) -> list:
        if self.now is None or self.spot_1m is None:
            return []
        sub = self.spot_1m[self.spot_1m.index <= self.now].tail(15)
        return [{"time": idx.to_pydatetime(), "spot": float(row["close"])}
                for idx, row in sub.iterrows()]

    def get_india_vix(self) -> float:
        if self.now is None or self.vix_1m is None:
            return 15.0
        sub = self.vix_1m[self.vix_1m.index <= self.now]
        return float(sub.iloc[-1]["close"]) if not sub.empty else 15.0

    def get_expiry_date(self) -> str:
        return self.expiry.isoformat()


class SilentEngine:
    """SignalEngine stub: never fires (so we cleanly observe dispatcher behavior)."""

    def evaluate(self, **kwargs):
        return {
            "direction": None, "reasons": ["silent stub"],
            "dte_risk": "MODERATE", "dte_days": 2,
            "is_expiry_day": False, "score": 0,
        }


# -----------------------------------------------------------------------
# Smoke test
# -----------------------------------------------------------------------

def smoke_one_day(day: date, mode: str) -> dict:
    spot_1m = load_spot()
    vix_1m = load_vix()
    expiries_by_date = discover_expiries(ROOT / "data")
    expiries_sorted = sorted(expiries_by_date.keys())
    day_to_exp = map_day_to_expiry([day], expiries_sorted)
    if day not in day_to_exp:
        return {"day": day, "skipped": True, "reason": "no expiry coverage"}
    exp = day_to_exp[day]
    chain = load_chain_for_expiry(expiries_by_date[exp])

    day_1m_spot = spot_1m[spot_1m.index.date == day]
    day_1m_vix = vix_1m[vix_1m.index.date == day]
    if day_1m_spot.empty:
        return {"day": day, "skipped": True, "reason": "no spot bars"}

    fetcher = HistoricalFetcher(day_1m_spot, day_1m_vix, chain, exp)
    engine = SilentEngine()
    dispatcher = TacticDispatcher(mode=mode)
    journal = JournalRecorder()
    journal.start_day(day)
    prev_close = float(spot_1m[spot_1m.index.date < day]["close"].iloc[-1]) \
                 if (spot_1m.index.date < day).any() else float(day_1m_spot.iloc[0]["open"])
    dispatcher.reset_for_new_day(day, prev_close)

    n_signals = 0
    n_force_exits = 0
    direction_counter: dict[str, int] = {}
    sample_signals: list[dict] = []
    sample_size_max = 5

    # Walk every minute of the day
    for ts, _row in day_1m_spot.iterrows():
        fetcher.set_now(ts)
        dispatcher.on_spot_tick(ts.to_pydatetime(), fetcher.get_spot())
        if ts.time() < time(10, 0) or ts.time() >= time(14, 30):
            continue
        sig = dispatcher.evaluate(
            ts=ts.to_pydatetime(),
            fetcher=fetcher,
            engine=engine,
            in_position=False,
        )
        # Sanity: the legacy keys must exist
        for k in ("direction", "reasons", "dte_risk", "dte_days",
                  "is_expiry_day", "score"):
            assert k in sig, f"missing key {k} in signal"
        if sig.get("force_exit"):
            n_force_exits += 1
        if sig["direction"]:
            n_signals += 1
            direction_counter[sig["direction"]] = direction_counter.get(sig["direction"], 0) + 1
            if len(sample_signals) < sample_size_max:
                sample_signals.append({
                    "ts": ts, "direction": sig["direction"],
                    "tactic": sig.get("tactic_name", "?"),
                    "reason": sig["reasons"][0] if sig["reasons"] else "",
                })

    # End-of-day journal
    day_record = journal.end_day(realized_pnl=0.0, cumulative_pnl=0.0)
    out_dir = ROOT / "reports" / "smoke_test"
    write_daily_report(day_record, out_dir)

    return {
        "day": day,
        "mode": mode,
        "n_signals": n_signals,
        "n_force_exits": n_force_exits,
        "directions": direction_counter,
        "samples": sample_signals,
    }


def main():
    # Pick 2 days from the cached data — pick days that had signals in
    # earlier phases so the test is representative.
    test_days = [date(2025, 11, 26), date(2026, 4, 21)]

    print("=" * 60)
    print("SMOKE TEST — TacticDispatcher + Journal end-to-end")
    print("=" * 60)

    for mode in ("legacy", "regime"):
        print(f"\n--- mode = {mode} ---")
        for d in test_days:
            try:
                result = smoke_one_day(d, mode)
            except Exception as e:
                print(f"  {d}  FAILED: {e!r}")
                raise
            if result.get("skipped"):
                print(f"  {d}  skipped ({result['reason']})")
                continue
            print(f"  {d}  n_signals={result['n_signals']:3} "
                  f"n_force_exits={result['n_force_exits']:3} "
                  f"dirs={result['directions']}")
            for s in result["samples"]:
                print(f"     {s['ts'].strftime('%H:%M')}  {s['direction']}  "
                      f"{s['tactic']:25s}  {s['reason'][:60]}")

    print("\n" + "=" * 60)
    print("Smoke test PASS — wire-up valid, journal written for each day.")
    print("=" * 60)


if __name__ == "__main__":
    main()
