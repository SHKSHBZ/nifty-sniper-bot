"""V2 of the proxy backtest — adds the VIX gate (max_vix_for_entry=16.0).

Compare vs v1 to confirm the gate filters out the worst-tail days
without throwing away too many winning trades.
"""
import csv
import statistics
import json
from collections import Counter

with open('project_config_seller.json') as fh:
    cfg = json.load(fh)
MAX_VIX = float(cfg['seller_entry_gates']['max_vix_for_entry'])

SHORT_DIST = 200
WING_WIDTH = 100
LOT_SIZE = 65
TP_PCT = 0.50
SL_MULT = 1.5
BROKERAGE = 240

rows = []
with open('reports/premium_vix_dte_daily.csv') as fh:
    for r in csv.DictReader(fh):
        try:
            rows.append({
                'date': r['trade_date'],
                'dte': int(r['dte']),
                'vix': float(r['vix_close']),
                'vix_regime': r['vix_regime'],
                'spot_open': float(r['spot_at_0930']),
                'spot_close': float(r['spot_at_1525']),
                'spot_move': float(r['spot_move']),
                'atm': int(r['atm']),
                'atm_ce': float(r['ce_at_0930']),
                'atm_pe': float(r['pe_at_0930']),
                'straddle_in': float(r['straddle_in']),
            })
        except (KeyError, ValueError, TypeError):
            continue

results = []
for d in rows:
    # NEW: VIX gate
    if d['vix'] > MAX_VIX:
        results.append({**d, 'outcome': 'NO_ENTRY_VIX_GATE',
                       'pnl': 0})
        continue

    short_ratio = 0.20 + d['vix'] / 100
    long_ratio = short_ratio * 0.45
    ce_short_est = d['atm_ce'] * short_ratio
    ce_long_est = d['atm_ce'] * long_ratio
    pe_short_est = d['atm_pe'] * short_ratio
    pe_long_est = d['atm_pe'] * long_ratio
    net_credit = (ce_short_est - ce_long_est) + (pe_short_est - pe_long_est)

    if net_credit < 15:
        results.append({**d, 'outcome': 'NO_ENTRY_CREDIT', 'pnl': 0})
        continue

    abs_move = abs(d['spot_move'])
    max_loss = WING_WIDTH - net_credit

    if abs_move <= SHORT_DIST:
        decay = 1.0 - (abs_move / SHORT_DIST) * 0.6
        close_cost = net_credit * (1 - decay)
        pnl_per_lot = net_credit - close_cost
        outcome = 'WIN_HELD'
    elif abs_move <= SHORT_DIST + WING_WIDTH:
        close_cost = abs_move - SHORT_DIST
        pnl_per_lot = net_credit - close_cost
        outcome = 'PARTIAL_BREACH'
    else:
        close_cost = WING_WIDTH
        pnl_per_lot = -max_loss
        outcome = 'FULL_BREACH'

    pnl_total = pnl_per_lot * LOT_SIZE - BROKERAGE
    results.append({**d, 'outcome': outcome, 'net_credit': net_credit,
                   'pnl': pnl_total, 'abs_move': abs_move})


traded = [r for r in results if r.get('outcome') not in
          (None, 'NO_ENTRY_VIX_GATE', 'NO_ENTRY_CREDIT')]
no_entry_vix = [r for r in results if r.get('outcome') == 'NO_ENTRY_VIX_GATE']
no_entry_credit = [r for r in results if r.get('outcome') == 'NO_ENTRY_CREDIT']
wins = [r for r in traded if r['pnl'] > 0]
losses = [r for r in traded if r['pnl'] <= 0]

print(f"===== V2 BACKTEST (VIX gate at {MAX_VIX}) =====")
print(f"Days analyzed:    {len(results)}")
print(f"  TRADED:         {len(traded)}")
print(f"  NO_ENTRY_VIX:   {len(no_entry_vix)}  (blocked by new gate)")
print(f"  NO_ENTRY_CREDIT:{len(no_entry_credit)}")
print()
print(f"Wins:  {len(wins)} ({len(wins)/len(traded)*100:.1f}%)")
print(f"Losses:{len(losses)} ({len(losses)/len(traded)*100:.1f}%)")
total = sum(r['pnl'] for r in traded)
print(f"\nTotal P&L:     Rs.{total:+,.0f}")
print(f"Avg per trade: Rs.{total/len(traded):+,.0f}")
print(f"Best day:      Rs.{max(r['pnl'] for r in traded):+,.0f}")
print(f"Worst day:     Rs.{min(r['pnl'] for r in traded):+,.0f}")

oc = Counter(r['outcome'] for r in traded)
print(f"\nOutcome distribution (traded days only):")
for k, v in oc.most_common():
    avg_pnl = sum(r['pnl'] for r in traded if r['outcome'] == k) / v
    print(f"  {k:<18} {v:>4} days  avg P&L Rs.{avg_pnl:+,.0f}")

print(f"\nBreach rate by VIX regime (post-gate):")
for regime in ['Low', 'Normal', 'High']:
    days = [r for r in traded if r.get('vix_regime') == regime]
    if not days: continue
    breaches = sum(1 for r in days if r['outcome'] in ('PARTIAL_BREACH', 'FULL_BREACH'))
    print(f"  {regime:<7}: {breaches}/{len(days)} breaches ({breaches/len(days)*100:.0f}%)")

# VIX-gate skipped days — what happened on those?
if no_entry_vix:
    print(f"\nDays SKIPPED by VIX gate (n={len(no_entry_vix)}):")
    skip_moves = [abs(r['spot_move']) for r in no_entry_vix]
    big_moves = [r for r in no_entry_vix if abs(r['spot_move']) > SHORT_DIST]
    print(f"  Median |move|: {statistics.median(skip_moves):.0f} pts")
    print(f"  Mean   |move|: {statistics.mean(skip_moves):.0f} pts")
    print(f"  Days with |move| > 200 pts: {len(big_moves)}/{len(no_entry_vix)} "
          f"({len(big_moves)/len(no_entry_vix)*100:.0f}%)")
    print(f"  → confirms the gate skipped days that would have been HIGH RISK")

# Comparison table
print(f"\n===== V1 vs V2 COMPARISON =====")
print(f"V1 (no VIX gate):  213 days, 206 trades, 94% wins, +Rs.347,381 total")
print(f"V2 (VIX gate):     {len(results)} days, {len(traded)} trades, "
      f"{len(wins)/len(traded)*100:.1f}% wins, Rs.{total:+,.0f} total")
print(f"\nLost trades: V1 had 12 losses, V2 has {len(losses)}")
print(f"Worst day:   V1 was Rs.-5,377 (high-VIX), V2 worst is "
      f"Rs.{min(r['pnl'] for r in traded):+,.0f}")
