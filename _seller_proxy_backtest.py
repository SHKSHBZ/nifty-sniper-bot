"""Proxy backtest for Iron Condor seller over ~18 months of daily data.

Uses reports/premium_vix_dte_daily.csv which has daily ATM CE+PE prices and
spot moves but NO wing strike data. So we model IC payoff approximately:

  Position: SHORT ATM±200 CE/PE, LONG ATM±300 CE/PE (100-pt wings)
  Net credit collected: estimated from ATM straddle × ratio
                        (typical ATM±200 strike has ~30% of ATM premium)
  P&L at end of day depends on where spot lands vs short strikes:

  - If |spot_move| <= 200: both shorts expire / decay → near max profit
  - If 200 < |spot_move| <= 300: one short broken, hedged by long → moderate loss
  - If |spot_move| > 300: both wings of breached side maxed out → max loss

  Model: loss scales linearly from 0 at 200-pt breach to (wing_width - credit) at 300+.
"""
import csv
import statistics

# Read daily data
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
                'straddle_peak': float(r['straddle_peak']),
                'straddle_trough': float(r['straddle_trough']),
                'straddle_out': float(r['straddle_out']),
            })
        except (KeyError, ValueError, TypeError):
            continue

print(f"Loaded {len(rows)} daily records")
print(f"Date range: {rows[0]['date']} → {rows[-1]['date']}")

# Iron Condor model parameters (match the seller bot config)
SHORT_DIST = 200
WING_WIDTH = 100
LOT_SIZE = 65
TP_PCT = 0.50
SL_MULT = 1.5
BROKERAGE = 240

# For each day, model the IC outcome
results = []
for d in rows:
    # Only consider entries on days with DTE >= 2 (not 0/1-DTE - too risky for IC)
    # Real seller bot doesn't have this gate yet, but it's standard practice.
    # Actually let's include all days to match the bot's current config:
    # (bot doesn't filter on DTE for IC; it gates on regime)

    # Estimate net credit per lot:
    # Empirically, OTM strikes 200 pts away have ~25-35% of ATM premium for moderate VIX.
    # Higher VIX inflates the OTM tail more. Approx: short_premium = atm × (0.20 + vix/100)
    # Long premium (100 pts further OTM) = ~50% of short premium.
    # Net credit = short - long for each side, sum across CE+PE.
    short_ratio = 0.20 + d['vix'] / 100  # rough proxy
    long_ratio = short_ratio * 0.45
    ce_short_est = d['atm_ce'] * short_ratio
    ce_long_est = d['atm_ce'] * long_ratio
    pe_short_est = d['atm_pe'] * short_ratio
    pe_long_est = d['atm_pe'] * long_ratio
    net_credit = (ce_short_est - ce_long_est) + (pe_short_est - pe_long_est)

    # Skip if credit too low (matches bot's min_net_credit gate)
    if net_credit < 15:
        results.append({**d, 'outcome': 'NO_ENTRY', 'reason': 'credit_too_low',
                       'net_credit': net_credit, 'pnl': 0})
        continue

    abs_move = abs(d['spot_move'])
    max_loss = WING_WIDTH - net_credit

    # Estimate P&L outcome based on how far spot moved
    # Approximation — assumes we held to close (not intraday TP)
    if abs_move <= SHORT_DIST:
        # No breach — both shorts decay. P&L depends on how much premium remained.
        # Model: residual close cost = net_credit × (1 - decay_factor)
        # Decay factor proportional to (SHORT_DIST - abs_move) / SHORT_DIST
        # When abs_move=0: full decay (close_cost near 0). When abs_move=200: minimal decay.
        decay = 1.0 - (abs_move / SHORT_DIST) * 0.6   # 60-100% decay
        close_cost = net_credit * (1 - decay)
        pnl_per_lot = net_credit - close_cost
        outcome = 'WIN_HELD'
    elif abs_move <= SHORT_DIST + WING_WIDTH:
        # Partial breach: one short is ITM, long is OTM
        # Close cost = (move - SHORT_DIST), capped at WING_WIDTH
        intrinsic = abs_move - SHORT_DIST
        close_cost = intrinsic  # rough — actual would include some extrinsic
        pnl_per_lot = net_credit - close_cost
        outcome = 'PARTIAL_BREACH'
    else:
        # Full breach: both wings ITM on the breached side
        # Loss = max_loss (wing_width - net_credit)
        close_cost = WING_WIDTH
        pnl_per_lot = -max_loss
        outcome = 'FULL_BREACH'

    # Convert to total P&L (1 lot × 65 qty)
    pnl_total = pnl_per_lot * LOT_SIZE - BROKERAGE
    results.append({**d, 'outcome': outcome, 'net_credit': net_credit,
                   'max_loss': max_loss, 'abs_move': abs_move,
                   'pnl_per_lot': pnl_per_lot, 'pnl': pnl_total})

# Aggregate
traded = [r for r in results if r.get('outcome') not in (None, 'NO_ENTRY')]
no_entry = [r for r in results if r.get('outcome') == 'NO_ENTRY']
wins = [r for r in traded if r['pnl'] > 0]
losses = [r for r in traded if r['pnl'] <= 0]

print(f"\n===== PROXY BACKTEST AGGREGATE =====")
print(f"Days analyzed: {len(results)}")
print(f"  TRADED: {len(traded)}")
print(f"  NO_ENTRY (credit too low): {len(no_entry)}")
print(f"\nWins: {len(wins)} | Losses: {len(losses)} | "
      f"Win rate: {len(wins)/len(traded)*100:.1f}%")
total = sum(r['pnl'] for r in traded)
avg = total / len(traded) if traded else 0
print(f"Total P&L: Rs.{total:+,.0f}")
print(f"Avg per trade: Rs.{avg:+,.0f}")
print(f"Best day: Rs.{max(r['pnl'] for r in traded):+,.0f}")
print(f"Worst day: Rs.{min(r['pnl'] for r in traded):+,.0f}")

# Breakdown by outcome
from collections import Counter
oc = Counter(r['outcome'] for r in traded)
print(f"\nOutcome distribution:")
for k, v in oc.most_common():
    avg_pnl = sum(r['pnl'] for r in traded if r['outcome'] == k) / v
    print(f"  {k:<18} {v:>4} days  avg P&L Rs.{avg_pnl:+,.0f}")

# Spot move distribution
moves = [abs(r['spot_move']) for r in traded]
print(f"\nDaily |spot move| distribution (these are the days we 'traded'):")
print(f"  Median: {statistics.median(moves):.0f} pts")
print(f"  Mean:   {statistics.mean(moves):.0f} pts")
print(f"  P75:    {statistics.quantiles(moves, n=4)[2]:.0f} pts")
print(f"  P90:    {statistics.quantiles(moves, n=10)[8]:.0f} pts")

# Breach rate by VIX regime
print(f"\nBreach rate by VIX regime:")
for regime in ['Low', 'Normal', 'High']:
    days = [r for r in traded if r.get('vix_regime') == regime]
    if not days: continue
    breaches = sum(1 for r in days if r['outcome'] in ('PARTIAL_BREACH', 'FULL_BREACH'))
    print(f"  {regime:<7}: {breaches}/{len(days)} breaches ({breaches/len(days)*100:.0f}%)")

# Sample worst days
print(f"\n10 worst days (would have most damaged the seller bot):")
worst = sorted(traded, key=lambda r: r['pnl'])[:10]
for r in worst:
    print(f"  {r['date']}  move={r['spot_move']:+.0f}  credit={r['net_credit']:.1f}  "
          f"outcome={r['outcome']:<14}  P&L Rs.{r['pnl']:+,.0f}")
