"""Backtest the Iron Condor seller bot v1 against historical focus_zone CSVs.

Methodology:
  1. For each NIFTY focus_zone CSV with sufficient data:
     a. Replay every tick through IndicatorTracker to build trend_confidence
     b. At each minute in [10:30, 13:30], if TC < 1.0 (= chop = RANGE-proxy),
        attempt an Iron Condor entry using the bot's config rules.
     c. If entered, track close_cost minute by minute until:
        - TP (close_cost <= 50% of net_credit)
        - SL (close_cost >= 250% of net_credit)
        - Force close at 15:15 IST
     d. Record per-day P&L
  2. Aggregate.

Caveats:
  - Uses trend_confidence as RANGE proxy. Real classifier might be stricter.
  - No IV percentile filter (data not available historically).
  - One IC per day max.
  - Paper P&L (Rs.30 × 8 = Rs.240 brokerage per round-trip).
"""
import csv
import glob
import json
import math
from collections import defaultdict, OrderedDict
from datetime import datetime, time as dtime

# Load seller config
with open('project_config_seller.json') as fh:
    cfg = json.load(fh)
strat = cfg['seller_strategy']

WING_WIDTH = int(strat['wing_width_pts'])               # 100
SHORT_DIST = int(strat['short_strike_distance_pts'])    # 200
MIN_CREDIT = float(strat['min_net_credit_per_lot'])     # 15.0
TP_PCT = float(strat['profit_target_pct'])              # 0.50
SL_MULT = float(strat['stop_loss_multiplier'])          # 1.5
WINDOW_START = dtime.fromisoformat(strat['no_entry_before'])  # 10:30
WINDOW_END = dtime.fromisoformat(strat['no_entry_after'])     # 13:30
FORCE_CLOSE = dtime.fromisoformat(strat['force_close_time'])  # 15:15
LOT_SIZE = 65
BROKERAGE_TOTAL = 240  # 8 orders × Rs.30

STRIKE_STEP = 50


def trend_confidence(prices: list[float], window: int = 30) -> tuple[float, str]:
    """Same formula as IndicatorTracker.trend_confidence_score."""
    if len(prices) < window + 1:
        return 0.0, "FLAT"
    recent = prices[-(window + 1):]
    log_rets = [math.log(recent[i] / recent[i-1])
                for i in range(1, len(recent))
                if recent[i-1] > 0 and recent[i] > 0]
    if len(log_rets) < 3:
        return 0.0, "FLAT"
    mean_r = sum(log_rets) / len(log_rets)
    var = sum((r - mean_r) ** 2 for r in log_rets) / (len(log_rets) - 1)
    stdev = math.sqrt(var)
    noise_floor = stdev * math.sqrt(window) * recent[-1]
    if noise_floor <= 0:
        return 0.0, "FLAT"
    move = recent[-1] - recent[0]
    score = abs(move) / noise_floor
    direction = "UP" if move > 0 else "DOWN" if move < 0 else "FLAT"
    return score, direction


def load_chain(path):
    rows = {}
    with open(path, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            key = (r['timestamp'], float(r['strike']))
            if key not in rows:
                rows[key] = r
    return rows


def get_premium(chain, ts, strike, side):
    r = chain.get((ts, float(strike)))
    if not r:
        return 0.0
    return float(r['ce_ltp' if side == 'CE' else 'pe_ltp'])


def get_spot_at(chain, ts):
    for s in (23000.0, 23500.0, 24000.0, 24500.0, 23700.0):
        r = chain.get((ts, s))
        if r:
            return float(r['spot'])
    # fallback: any strike
    for (t, s), r in chain.items():
        if t == ts:
            return float(r['spot'])
    return 0


def backtest_day(path):
    chain = load_chain(path)
    if not chain:
        return None
    all_ts = sorted({k[0] for k in chain.keys()})
    market_ts = [t for t in all_ts if "09:15" <= t[11:16] < "15:30"]
    if len(market_ts) < 60:
        return {"date": path[-14:-4], "skipped": "insufficient_data"}

    # Build spot series for trend_confidence
    spots = []
    times = []
    for ts in market_ts:
        sp = get_spot_at(chain, ts)
        if sp > 0:
            spots.append(sp); times.append(ts)

    date = path[-14:-4]

    # Find FIRST entry candidate in window where TC < 1.0
    entry_idx = None
    for i, ts in enumerate(times):
        t = dtime.fromisoformat(ts[11:19])
        if t < WINDOW_START or t >= WINDOW_END:
            continue
        tc_score, _ = trend_confidence(spots[:i+1])
        if 0 < tc_score < 1.0:
            entry_idx = i
            break

    if entry_idx is None:
        return {"date": date, "outcome": "NO_ENTRY",
                "reason": "no minute in window with TC < 1.0 (no chop signal)"}

    entry_ts = times[entry_idx]
    entry_spot = spots[entry_idx]
    atm = round(entry_spot / STRIKE_STEP) * STRIKE_STEP

    # Get strike prices
    ce_short_k = atm + SHORT_DIST
    ce_long_k = ce_short_k + WING_WIDTH
    pe_short_k = atm - SHORT_DIST
    pe_long_k = pe_short_k - WING_WIDTH

    # Get entry premiums
    ce_short_e = get_premium(chain, entry_ts, ce_short_k, 'CE')
    ce_long_e = get_premium(chain, entry_ts, ce_long_k, 'CE')
    pe_short_e = get_premium(chain, entry_ts, pe_short_k, 'PE')
    pe_long_e = get_premium(chain, entry_ts, pe_long_k, 'PE')

    if min(ce_short_e, ce_long_e, pe_short_e, pe_long_e) <= 0:
        return {"date": date, "outcome": "NO_ENTRY",
                "reason": f"premium unavailable at ATM={atm} entry={entry_ts[11:19]}"}

    net_credit = (ce_short_e - ce_long_e) + (pe_short_e - pe_long_e)
    if net_credit < MIN_CREDIT:
        return {"date": date, "outcome": "NO_ENTRY",
                "reason": f"net credit Rs.{net_credit:.2f} < Rs.{MIN_CREDIT}",
                "atm": atm, "entry_time": entry_ts[11:19]}

    tp_threshold = net_credit * (1 - TP_PCT)
    sl_threshold = net_credit * (1 + SL_MULT)
    max_loss = WING_WIDTH - net_credit

    # Track minute-by-minute until exit
    exit_reason = None
    exit_ts = None
    exit_close_cost = None
    for j in range(entry_idx + 1, len(times)):
        ts = times[j]
        t = dtime.fromisoformat(ts[11:19])
        # Force close
        if t >= FORCE_CLOSE:
            ce_short_x = get_premium(chain, ts, ce_short_k, 'CE') or ce_short_e
            ce_long_x = get_premium(chain, ts, ce_long_k, 'CE') or ce_long_e
            pe_short_x = get_premium(chain, ts, pe_short_k, 'PE') or pe_short_e
            pe_long_x = get_premium(chain, ts, pe_long_k, 'PE') or pe_long_e
            exit_close_cost = (ce_short_x - ce_long_x) + (pe_short_x - pe_long_x)
            exit_reason = "FORCE_CLOSE"
            exit_ts = ts
            break

        ce_short_n = get_premium(chain, ts, ce_short_k, 'CE')
        ce_long_n = get_premium(chain, ts, ce_long_k, 'CE')
        pe_short_n = get_premium(chain, ts, pe_short_k, 'PE')
        pe_long_n = get_premium(chain, ts, pe_long_k, 'PE')
        if min(ce_short_n, ce_long_n, pe_short_n, pe_long_n) <= 0:
            continue
        close_cost = (ce_short_n - ce_long_n) + (pe_short_n - pe_long_n)

        if close_cost <= tp_threshold:
            exit_reason = "TP"; exit_ts = ts; exit_close_cost = close_cost
            ce_short_x, ce_long_x, pe_short_x, pe_long_x = ce_short_n, ce_long_n, pe_short_n, pe_long_n
            break
        if close_cost >= sl_threshold:
            exit_reason = "SL"; exit_ts = ts; exit_close_cost = close_cost
            ce_short_x, ce_long_x, pe_short_x, pe_long_x = ce_short_n, ce_long_n, pe_short_n, pe_long_n
            break

    if exit_reason is None:
        # Loop ended without exit — use last available premiums
        last_ts = times[-1]
        ce_short_x = get_premium(chain, last_ts, ce_short_k, 'CE') or ce_short_e
        ce_long_x = get_premium(chain, last_ts, ce_long_k, 'CE') or ce_long_e
        pe_short_x = get_premium(chain, last_ts, pe_short_k, 'PE') or pe_short_e
        pe_long_x = get_premium(chain, last_ts, pe_long_k, 'PE') or pe_long_e
        exit_close_cost = (ce_short_x - ce_long_x) + (pe_short_x - pe_long_x)
        exit_reason = "EOD_DATA_END"
        exit_ts = last_ts

    # Per-leg P&L (× lot size)
    ce_short_pnl = (ce_short_e - ce_short_x) * LOT_SIZE
    ce_long_pnl = (ce_long_x - ce_long_e) * LOT_SIZE
    pe_short_pnl = (pe_short_e - pe_short_x) * LOT_SIZE
    pe_long_pnl = (pe_long_x - pe_long_e) * LOT_SIZE
    gross_pnl = ce_short_pnl + ce_long_pnl + pe_short_pnl + pe_long_pnl
    net_pnl = gross_pnl - BROKERAGE_TOTAL

    return {
        "date": date,
        "outcome": "TRADED",
        "atm": atm,
        "entry_time": entry_ts[11:19],
        "entry_spot": entry_spot,
        "ce_short_strike": ce_short_k, "ce_short_entry": ce_short_e, "ce_short_exit": ce_short_x,
        "ce_long_strike": ce_long_k,   "ce_long_entry": ce_long_e,   "ce_long_exit": ce_long_x,
        "pe_short_strike": pe_short_k, "pe_short_entry": pe_short_e, "pe_short_exit": pe_short_x,
        "pe_long_strike": pe_long_k,   "pe_long_entry": pe_long_e,   "pe_long_exit": pe_long_x,
        "net_credit": net_credit,
        "max_loss": max_loss,
        "exit_reason": exit_reason,
        "exit_time": exit_ts[11:19] if exit_ts else None,
        "exit_close_cost": exit_close_cost,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "exit_spot": get_spot_at(chain, exit_ts) if exit_ts else 0,
    }


# Run
files = sorted(glob.glob('logs/focus_zone_nifty_2026-05-*.csv'))
results = []
for f in files:
    r = backtest_day(f)
    if r:
        results.append(r)

print(f"{'Date':<11} {'Outcome':<14} {'ATM':>6} {'Entry':<8} {'Credit':>7} "
      f"{'Exit':<14} {'CloseCost':>10} {'Net P&L':>10}")
print('-' * 95)
for r in results:
    if r.get('outcome') == 'TRADED':
        print(f"{r['date']:<11} {r['outcome']:<14} {r['atm']:>6} {r['entry_time']:<8} "
              f"Rs.{r['net_credit']:>5.1f}  {r['exit_reason']:<14} "
              f"Rs.{r['exit_close_cost']:>7.1f}   Rs.{r['net_pnl']:>+7.0f}")
    elif r.get('outcome') == 'NO_ENTRY':
        print(f"{r['date']:<11} NO_ENTRY        ({r.get('reason', '')[:55]})")
    else:
        print(f"{r['date']:<11} SKIPPED         ({r.get('skipped', '')})")

# Aggregate
print()
traded = [r for r in results if r.get('outcome') == 'TRADED']
no_entry = [r for r in results if r.get('outcome') == 'NO_ENTRY']
skipped = [r for r in results if r.get('outcome') == 'SKIPPED']

print(f"===== BACKTEST AGGREGATE =====")
print(f"Days analyzed: {len(results)}")
print(f"  TRADED: {len(traded)}")
print(f"  NO_ENTRY: {len(no_entry)}")
print(f"  SKIPPED (insufficient data): {len(skipped)}")

if traded:
    wins = [r for r in traded if r['net_pnl'] > 0]
    losses = [r for r in traded if r['net_pnl'] <= 0]
    total_pnl = sum(r['net_pnl'] for r in traded)
    avg_pnl = total_pnl / len(traded)
    print(f"\nWins: {len(wins)} | Losses: {len(losses)} | "
          f"Win rate: {len(wins)/len(traded)*100:.0f}%")
    print(f"Total net P&L: Rs.{total_pnl:+,.0f}")
    print(f"Avg per trade: Rs.{avg_pnl:+,.0f}")
    print(f"Best day: Rs.{max(r['net_pnl'] for r in traded):+,.0f}")
    print(f"Worst day: Rs.{min(r['net_pnl'] for r in traded):+,.0f}")

    # Exit reason distribution
    from collections import Counter
    reasons = Counter(r['exit_reason'] for r in traded)
    print(f"\nExit reasons: {dict(reasons)}")
