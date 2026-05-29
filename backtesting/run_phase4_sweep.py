"""
Minimal wrapper: runs Phase 4 main() for each parameter combo
in a single process so the daily chain cache persists.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.backtest_regime_phase4 import main as phase4_main

time_stops = [60, 90, 120, 150]
focus_halves = [1, 2, 3]  # ±1=3 strikes, ±2=5 strikes, ±3=7 strikes

total = len(time_stops) * len(focus_halves)
count = 0

for ts in time_stops:
    for fz in focus_halves:
        count += 1
        label = f"TS={ts}min FZ=±{fz} ({fz*2+1} strikes)"
        print(f"\n{'='*60}")
        print(f"[{count}/{total}] {label}")
        print(f"{'='*60}")
        phase4_main(time_stop_override=ts, focus_zone_half=fz)
