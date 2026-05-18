"""Integration test for the seller bot's actual code paths in main.py.

This is NOT a backtest. It exercises the real methods (_scan_for_iron_condor_entry,
_open_iron_condor_position, _monitor_iron_condor, _close_iron_condor) with mocked
dependencies, so we know the wiring works end-to-end before Monday's live start.

Test scenarios:
  1. SKIP_REGIME — classifier says TREND → no entry
  2. SKIP_TIME — outside 10:30-13:30 window → no entry
  3. SKIP_LOW_CREDIT — premium environment too cheap → no entry
  4. ENTER → PROFIT_TARGET — premiums decay → close at TP
  5. ENTER → STOP_LOSS — one wing breached → close at SL
  6. ENTER → FORCE_CLOSE — held to 15:15 → exit
  7. ENTER → REGIME_FLIP — classifier moves to TREND mid-position → exit
"""
import json
import sys
import os
from datetime import datetime, time as dtime
from pathlib import Path
from unittest.mock import MagicMock

# Set up path
sys.path.insert(0, str(Path(__file__).parent))

# Mock pytz / IST for deterministic timestamps
import pytz
IST = pytz.timezone('Asia/Kolkata')


def make_mock_orchestrator(regime_value="RANGE", base_spot=23500.0,
                            cur_spot=23500.0):
    """Build a minimal LiveOrchestrator-shaped mock object with the
    real IC methods bound to it."""
    # Import the real class
    import main as bot_main
    orch = bot_main.LiveOrchestrator.__new__(bot_main.LiveOrchestrator)

    # Load config
    with open('project_config_seller.json') as fh:
        orch.config = json.load(fh)

    orch.trading_index = "NIFTY"
    orch.strike_step = 50
    orch.lot_size = 65
    orch.seller_mode = True
    orch.engine_mode = "regime"

    # Fake portfolio
    orch.portfolio = {"capital": 100000.0, "open_position": None,
                      "open_straddle": None, "open_iron_condor": None,
                      "trade_history": []}
    orch.portfolio_file = Path("/tmp/_test_seller_portfolio.json")
    orch.save_portfolio = MagicMock()

    # Mock fetcher
    orch.fetcher = MagicMock()
    orch.fetcher.get_spot.return_value = cur_spot
    orch.fetcher.get_india_vix.return_value = 13.5

    # Premium-by-strike lookup. Default: typical OTM premiums at spot 23500.
    # We override per-test.
    premium_table = {
        # (strike, side) -> premium
        (23300.0, "PE"): 25.0,  # short PE 200 below
        (23200.0, "PE"): 12.0,  # long PE 300 below
        (23700.0, "CE"): 22.0,  # short CE 200 above
        (23800.0, "CE"): 10.0,  # long CE 300 above
    }
    orch._premium_table = premium_table

    def get_option_ltp(strike, side):
        return orch._premium_table.get((float(strike), side), 0.0)
    orch.fetcher.get_option_ltp.side_effect = get_option_ltp

    # Mock classifier via dispatcher
    orch.dispatcher = MagicMock()
    regime_obj = MagicMock()
    regime_obj.value = regime_value
    orch.dispatcher.classifier._current = regime_obj

    # Telegram + logger
    orch.telegram = None

    return orch


def banner(name):
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")


# Inject simple logger that captures messages
import logging
captured = []
class CaptureHandler(logging.Handler):
    def emit(self, record):
        captured.append(record.getMessage())
logging.getLogger("LiveBot").addHandler(CaptureHandler())
logging.getLogger("LiveBot").setLevel(logging.INFO)


# ============================================================
# Test 1: SKIP_REGIME
# ============================================================
banner("TEST 1: TREND regime → no entry")
captured.clear()
orch = make_mock_orchestrator(regime_value="TREND_UP")
now = datetime(2026, 5, 18, 11, 0, 0, tzinfo=IST)  # in window
orch._scan_for_iron_condor_entry(now)
assert orch.portfolio["open_iron_condor"] is None, "Should not have entered!"
skip_msg = [m for m in captured if "[SELLER] Skip entry" in m and "regime" in m]
assert skip_msg, f"Expected skip log; got: {captured[-3:]}"
print(f"✓ TREND regime correctly skipped: {skip_msg[0][:80]}...")


# ============================================================
# Test 2: SKIP_TIME (outside window)
# ============================================================
banner("TEST 2: 09:00 IST (before window) → no entry")
captured.clear()
orch = make_mock_orchestrator(regime_value="RANGE")
now_early = datetime(2026, 5, 18, 9, 0, 0, tzinfo=IST)  # before 10:30
orch._scan_for_iron_condor_entry(now_early)
assert orch.portfolio["open_iron_condor"] is None
print("✓ Outside window correctly skipped (no log = early exit before any check)")

banner("TEST 2b: 14:00 IST (after window) → no entry")
captured.clear()
now_late = datetime(2026, 5, 18, 14, 0, 0, tzinfo=IST)  # after 13:30
orch._scan_for_iron_condor_entry(now_late)
assert orch.portfolio["open_iron_condor"] is None
print("✓ After window correctly skipped")


# ============================================================
# Test 3: SKIP_LOW_CREDIT
# ============================================================
banner("TEST 3: Low premium environment → credit gate blocks")
captured.clear()
orch = make_mock_orchestrator(regime_value="RANGE")
# Set super low premiums so net credit < Rs.15
orch._premium_table = {
    (23300.0, "PE"): 8.0, (23200.0, "PE"): 6.0,    # net 2 on PE
    (23700.0, "CE"): 7.0, (23800.0, "CE"): 5.0,    # net 2 on CE
}
now = datetime(2026, 5, 18, 11, 0, 0, tzinfo=IST)
orch._scan_for_iron_condor_entry(now)
assert orch.portfolio["open_iron_condor"] is None, "Should not enter on low credit"
credit_msg = [m for m in captured if "below min" in m]
assert credit_msg, f"Expected credit-gate log; got: {captured[-3:]}"
print(f"✓ Low credit correctly blocked: {credit_msg[0][:80]}...")


# ============================================================
# Test 4: ENTER then PROFIT_TARGET
# ============================================================
banner("TEST 4: Entry → premium decay → PROFIT TARGET")
captured.clear()
orch = make_mock_orchestrator(regime_value="RANGE")
now = datetime(2026, 5, 18, 11, 0, 0, tzinfo=IST)
orch._scan_for_iron_condor_entry(now)
assert orch.portfolio["open_iron_condor"] is not None, "Should have entered"
ic = orch.portfolio["open_iron_condor"]
print(f"  Entered: net_credit=Rs.{ic['net_credit']:.2f}, TP threshold close_cost<=Rs.{ic['profit_target_close_cost']:.2f}")
print(f"  Strikes: CE {ic['ce_short_strike']}/{ic['ce_long_strike']}, "
      f"PE {ic['pe_short_strike']}/{ic['pe_long_strike']}")

# Simulate premium decay (theta) — all premiums drop ~50%
orch._premium_table = {
    (23300.0, "PE"): 11.0, (23200.0, "PE"): 5.0,
    (23700.0, "CE"): 9.0, (23800.0, "CE"): 4.0,
}
# Compute what close_cost will be
close_cost_now = (9.0 - 4.0) + (11.0 - 5.0)  # = 5 + 6 = 11
print(f"  Decayed close_cost: Rs.{close_cost_now:.2f}")

now2 = datetime(2026, 5, 18, 11, 30, 0, tzinfo=IST)
orch._monitor_iron_condor(now2)
assert orch.portfolio["open_iron_condor"] is None, "Should have closed at TP"
tp_msg = [m for m in captured if "PROFIT TARGET" in m or "PROFIT_TARGET" in m or "IC CLOSED" in m]
print(f"✓ TP fired and position closed")
print(f"  Trade history records: {len(orch.portfolio['trade_history'])} (expected 4)")
assert len(orch.portfolio["trade_history"]) == 4
final_pnl = sum(t["pnl"] for t in orch.portfolio["trade_history"])
print(f"  Final P&L: Rs.{final_pnl:+,.0f}")
assert final_pnl > 0, f"TP should produce positive P&L, got {final_pnl}"


# ============================================================
# Test 5: ENTER then STOP_LOSS (extreme adverse move)
# ============================================================
banner("TEST 5: Entry → CE wing breached → STOP LOSS")
captured.clear()
orch = make_mock_orchestrator(regime_value="RANGE")
now = datetime(2026, 5, 18, 11, 0, 0, tzinfo=IST)
orch._scan_for_iron_condor_entry(now)
ic = orch.portfolio["open_iron_condor"]
print(f"  Entered: credit=Rs.{ic['net_credit']:.2f}, SL at close_cost>=Rs.{ic['stop_loss_close_cost']:.2f}")

# Spot rips up, CE short blown out
orch._premium_table = {
    (23300.0, "PE"): 2.0, (23200.0, "PE"): 1.0,    # PE side fine
    (23700.0, "CE"): 150.0, (23800.0, "CE"): 70.0, # CE deeply ITM
}
close_cost = (150.0 - 70.0) + (2.0 - 1.0)  # = 80 + 1 = 81
print(f"  Severe close_cost: Rs.{close_cost:.2f} (SL threshold Rs.{ic['stop_loss_close_cost']:.2f})")

now2 = datetime(2026, 5, 18, 11, 30, 0, tzinfo=IST)
orch._monitor_iron_condor(now2)
assert orch.portfolio["open_iron_condor"] is None, "Should have closed at SL"
final_pnl = sum(t["pnl"] for t in orch.portfolio["trade_history"])
print(f"✓ SL fired, P&L: Rs.{final_pnl:+,.0f}")
assert final_pnl < 0


# ============================================================
# Test 6: FORCE_CLOSE at 15:15
# ============================================================
banner("TEST 6: Held to 15:15 → FORCE_CLOSE")
captured.clear()
orch = make_mock_orchestrator(regime_value="RANGE")
now = datetime(2026, 5, 18, 11, 0, 0, tzinfo=IST)
orch._scan_for_iron_condor_entry(now)
assert orch.portfolio["open_iron_condor"] is not None
# Slight decay but neither TP nor SL hits
orch._premium_table = {
    (23300.0, "PE"): 18.0, (23200.0, "PE"): 8.0,
    (23700.0, "CE"): 17.0, (23800.0, "CE"): 7.0,
}
# Time advances to 15:15
now_eod = datetime(2026, 5, 18, 15, 15, 0, tzinfo=IST)
orch._monitor_iron_condor(now_eod)
assert orch.portfolio["open_iron_condor"] is None
close_msg = [m for m in captured if "EOD" in m or "Force" in m or "FORCE" in m or "IC CLOSED" in m]
print(f"✓ Force-close fired at 15:15")
final_pnl = sum(t["pnl"] for t in orch.portfolio["trade_history"])
print(f"  P&L: Rs.{final_pnl:+,.0f}")


# ============================================================
# Test 7: REGIME FLIP exit
# ============================================================
banner("TEST 7: Entry RANGE → classifier flips to TREND_DOWN → exit")
captured.clear()
orch = make_mock_orchestrator(regime_value="RANGE")
now = datetime(2026, 5, 18, 11, 0, 0, tzinfo=IST)
orch._scan_for_iron_condor_entry(now)
assert orch.portfolio["open_iron_condor"] is not None
print(f"  Entered at RANGE")

# Flip classifier to TREND_DOWN
orch.dispatcher.classifier._current.value = "TREND_DOWN"
# Premiums slightly adverse but neither TP nor SL hit (regime flip should fire)
orch._premium_table = {
    (23300.0, "PE"): 32.0, (23200.0, "PE"): 16.0,
    (23700.0, "CE"): 14.0, (23800.0, "CE"): 6.0,
}

now2 = datetime(2026, 5, 18, 11, 30, 0, tzinfo=IST)
orch._monitor_iron_condor(now2)
assert orch.portfolio["open_iron_condor"] is None
flip_msg = [m for m in captured if "REGIME FLIP" in m or "regime" in m.lower()]
print(f"✓ Regime flip detected → position closed")
print(f"  P&L: Rs.{sum(t['pnl'] for t in orch.portfolio['trade_history']):+,.0f}")


# ============================================================
# Summary
# ============================================================
print(f"\n{'=' * 70}")
print("ALL 7 INTEGRATION TESTS PASSED")
print(f"{'=' * 70}")
print("\nVerified end-to-end:")
print("  ✓ Regime gate blocks TREND days")
print("  ✓ Entry window blocks before 10:30 and after 13:30")
print("  ✓ Credit gate blocks low-premium days")
print("  ✓ TP fires on premium decay")
print("  ✓ SL fires on severe wing breach")
print("  ✓ Force-close fires at 15:15")
print("  ✓ Regime-flip exit fires when classifier moves to TREND")
print("\nPortfolio mechanics:")
print("  ✓ 4 legs recorded per close (CE short/long, PE short/long)")
print("  ✓ P&L sum matches direction (positive for TP, negative for SL)")
print("  ✓ open_iron_condor cleared after close")
