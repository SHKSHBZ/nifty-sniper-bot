"""Sweep VIX threshold values to find the optimal gate.

For each threshold X in [12, 13, 14, 15, 16, 17, 18, 20, 999 (no gate)]:
- Run the proxy model on all 213 days, skip if VIX > X
- Report: trades, win rate, total P&L, worst day, % wins missed
"""
import csv
import statistics
from collections import Counter

SHORT_DIST = 200
WING_WIDTH = 100
LOT_SIZE = 65
BROKERAGE = 240

rows = []
with open('reports/premium_vix_dte_daily.csv') as fh:
    for r in csv.DictReader(fh):
        try:
            rows.append({
                'date': r['trade_date'],
                'vix': float(r['vix_close']),
                'spot_move': float(r['spot_move']),
                'atm_ce': float(r['ce_at_0930']),
                'atm_pe': float(r['pe_at_0930']),
            })
        except (KeyError, ValueError, TypeError):
            continue

def simulate(max_vix):
    skipped = 0
    traded = []
    for d in rows:
        if d['vix'] > max_vix:
            skipped += 1
            continue
        short_ratio = 0.20 + d['vix'] / 100
        long_ratio = short_ratio * 0.45
        ce_short_est = d['atm_ce'] * short_ratio
        ce_long_est = d['atm_ce'] * long_ratio
        pe_short_est = d['atm_pe'] * short_ratio
        pe_long_est = d['atm_pe'] * long_ratio
        net_credit = (ce_short_est - ce_long_est) + (pe_short_est - pe_long_est)
        if net_credit < 15:
            continue
        abs_move = abs(d['spot_move'])
        max_loss = WING_WIDTH - net_credit
        if abs_move <= SHORT_DIST:
            close_cost = net_credit * (1 - (1.0 - (abs_move / SHORT_DIST) * 0.6))
            pnl_per_lot = net_credit - close_cost
        elif abs_move <= SHORT_DIST + WING_WIDTH:
            close_cost = abs_move - SHORT_DIST
            pnl_per_lot = net_credit - close_cost
        else:
            pnl_per_lot = -max_loss
        pnl = pnl_per_lot * LOT_SIZE - BROKERAGE
        traded.append({**d, 'pnl': pnl})
    wins = [t for t in traded if t['pnl'] > 0]
    losses = [t for t in traded if t['pnl'] <= 0]
    total_pnl = sum(t['pnl'] for t in traded)
    worst = min(t['pnl'] for t in traded) if traded else 0
    best = max(t['pnl'] for t in traded) if traded else 0
    return {
        'max_vix': max_vix,
        'skipped': skipped,
        'trades': len(traded),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(traded) * 100 if traded else 0,
        'total_pnl': total_pnl,
        'avg_pnl': total_pnl / len(traded) if traded else 0,
        'worst': worst,
        'best': best,
    }


thresholds = [12, 13, 14, 15, 16, 17, 18, 19, 20, 999]
print(f"{'VIX≤':<6} {'Skip':>5} {'Trd':>5} {'Wins':>5} {'Loss':>5} {'Win%':>6} "
      f"{'Total':>10} {'Avg':>7} {'Worst':>8} {'Best':>8}")
print('-' * 80)
for vix_max in thresholds:
    r = simulate(vix_max)
    gate_str = "none" if vix_max == 999 else f"≤{vix_max}"
    print(f"{gate_str:<6} {r['skipped']:>5} {r['trades']:>5} {r['wins']:>5} "
          f"{r['losses']:>5} {r['win_rate']:>5.1f}% "
          f"Rs.{r['total_pnl']:>+8,.0f}  Rs.{r['avg_pnl']:>+5,.0f}  "
          f"Rs.{r['worst']:>+5,.0f}  Rs.{r['best']:>+5,.0f}")

print()
# Find the threshold with best total_pnl AND lowest |worst|
print("Optimal thresholds:")
results = [simulate(v) for v in thresholds]
best_total = max(results, key=lambda r: r['total_pnl'])
print(f"  Max total P&L:   VIX ≤ {best_total['max_vix']}: Rs.{best_total['total_pnl']:+,.0f}")
# Best risk-adjusted (Sharpe-like): total / abs(worst)
def score(r):
    return r['total_pnl'] / max(abs(r['worst']), 1)
best_ra = max(results, key=score)
print(f"  Best risk-adj:   VIX ≤ {best_ra['max_vix']}: "
      f"Rs.{best_ra['total_pnl']:+,.0f} / |worst| Rs.{best_ra['worst']:+,.0f} = "
      f"score {score(best_ra):.1f}")
