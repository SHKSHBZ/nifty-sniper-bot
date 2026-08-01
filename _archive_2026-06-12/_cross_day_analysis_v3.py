"""V3: extend V2 by measuring the 4 signals at THREE strike positions per day:
  - ATM-1 (one strike below ATM-at-reversal)
  - ATM   (at-the-money at the reversal moment)
  - ATM+1 (one strike above ATM-at-reversal)

Tells us whether each signal is strike-sensitive, and which strike gives the
cleanest read.
"""
import csv, glob

FILES = sorted(glob.glob('logs/focus_zone_nifty_2026-05-*.csv'))


def load_day(path):
    rows = {}
    strikes = set()
    with open(path, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            key = (r['timestamp'], float(r['strike']))
            if key not in rows:
                rows[key] = r
                strikes.add(float(r['strike']))
    if not rows:
        return None, None, None, None
    strikes = sorted(strikes)
    market_ts = sorted([t for t, _ in rows.keys() if "09:15" <= t[11:16] < "15:30"])
    market_ts = sorted(set(market_ts))
    if len(market_ts) < 50:
        return None, None, None, None
    spot_by_t = {}
    for ts in market_ts:
        for s in strikes:
            if (ts, s) in rows:
                spot_by_t[ts] = float(rows[(ts, s)]['spot'])
                break
    return rows, strikes, market_ts, spot_by_t


def analyze_at_strike(rows, strikes, times, spots, target_strike, rev_i, winning_side):
    """Run 4 signals at a specific strike. Returns dict of YES/NO/?"""
    rev_t = times[rev_i]
    rev_spot = spots[rev_i]
    pre_idx = max(0, rev_i - 15)
    pre30_idx = max(0, rev_i - 30)

    def get(ts, col):
        r = rows.get((ts, target_strike))
        return float(r[col]) if r else None

    # Signal 1: rejection (spot-only — same regardless of strike, but include for symmetry)
    threshold = rev_spot * 0.001
    bucket_touches = set()
    for i in range(max(0, rev_i - 60), rev_i):
        if abs(spots[i] - rev_spot) <= threshold:
            bucket_touches.add(times[i][11:16][:4])
    sig_rejection = "YES" if len(bucket_touches) >= 2 else "NO"

    win_oi_col = 'pe_oi' if winning_side == 'PE' else 'ce_oi'
    los_oi_col = 'ce_oi' if winning_side == 'PE' else 'pe_oi'
    los_d_col = 'ce_delta' if winning_side == 'PE' else 'pe_delta'
    win_d_col = 'pe_delta' if winning_side == 'PE' else 'ce_delta'
    los_p_col = 'ce_ltp' if winning_side == 'PE' else 'pe_ltp'

    # Signal 2: OI shift (losing-side build OR winning-side unwind)
    pre_los = get(times[pre_idx], los_oi_col); rev_los = get(rev_t, los_oi_col)
    pre_win = get(times[pre_idx], win_oi_col); rev_win = get(rev_t, win_oi_col)
    fired = False; los_pct = None; win_pct = None
    if pre_los and rev_los and pre_los > 0:
        los_pct = (rev_los - pre_los) / pre_los * 100
        if los_pct >= 10: fired = True
    if pre_win and rev_win and pre_win > 0:
        win_pct = (rev_win - pre_win) / pre_win * 100
        if win_pct <= -10: fired = True
    sig_oi = "YES" if fired else ("NO" if (los_pct is not None or win_pct is not None) else "?")

    # Signal 3: delta crossover
    pre_ld = get(times[pre_idx], los_d_col); rev_ld = get(rev_t, los_d_col)
    pre_wd = get(times[pre_idx], win_d_col); rev_wd = get(rev_t, win_d_col)
    if None not in (pre_ld, rev_ld, pre_wd, rev_wd):
        crossover = (pre_ld > 0.50 and rev_ld < 0.50) or (pre_wd < 0.50 and rev_wd > 0.50)
        sig_delta = "YES" if crossover else "NO"
    else:
        sig_delta = "?"

    # Signal 4: premium stall on losing side
    pre30_prem = get(times[pre30_idx], los_p_col); rev_prem = get(rev_t, los_p_col)
    pre30_spot = spots[pre30_idx]
    rev_spot_act = spots[rev_i]
    spot_in_losing_dir = (
        (winning_side == 'PE' and rev_spot_act >= pre30_spot)
        or (winning_side == 'CE' and rev_spot_act <= pre30_spot)
    )
    if pre30_prem and rev_prem and pre30_prem > 0 and spot_in_losing_dir:
        prem_gain = (rev_prem - pre30_prem) / pre30_prem * 100
        sig_prem = "YES" if prem_gain < 5 else "NO"
    else:
        sig_prem = "?"

    return dict(rejection=sig_rejection, oi=sig_oi, delta=sig_delta, prem=sig_prem,
                los_pct=los_pct, win_pct=win_pct)


def analyze_day(path):
    rows, strikes, times, spot_by_t = load_day(path)
    if not rows:
        return None
    spots = list(spot_by_t.values())
    times = list(spot_by_t.keys())
    hi = max(spots); lo = min(spots)
    hi_i = spots.index(hi); lo_i = spots.index(lo)

    if hi_i < lo_i:
        rev_type = "HIGH-then-DOWN"; rev_i = hi_i; winning_side = "PE"
    else:
        rev_type = "LOW-then-UP"; rev_i = lo_i; winning_side = "CE"

    day_range = hi - lo
    date = path[-14:-4]

    if day_range < 30:
        return dict(date=date, rev_type="FLAT/CHOP", range_pts=day_range, results={})

    rev_spot = spots[rev_i]
    atm = min(strikes, key=lambda x: abs(x - rev_spot))
    strike_step = 50
    # Adjacent strikes — make sure they exist in the chain
    targets = {
        'ATM-1': atm - strike_step if (atm - strike_step) in strikes else None,
        'ATM':   atm,
        'ATM+1': atm + strike_step if (atm + strike_step) in strikes else None,
    }
    results = {}
    for label, k in targets.items():
        if k is None:
            results[label] = None
        else:
            results[label] = analyze_at_strike(rows, strikes, times, spots, k, rev_i, winning_side)

    return dict(date=date, rev_type=rev_type, range_pts=day_range, atm=atm,
                rev_time=times[rev_i][11:16], targets=targets, results=results)


all_results = []
for f in FILES:
    r = analyze_day(f)
    if r:
        all_results.append(r)

# Print main table — show YES/NO per strike per signal
print(f"{'Date':<11} {'RevType':<14} {'Rng':>4} {'ATM':>6} {'Time':<6}  "
      f"{'Reject(A-1/A/A+1)':<18} {'OI(A-1/A/A+1)':<14} "
      f"{'DltX(A-1/A/A+1)':<16} {'Prem(A-1/A/A+1)':<16}")
print('-' * 110)

def cell(r, label, key):
    if not r['results'].get(label):
        return '_'
    return r['results'][label][key][0]  # Y/N/?

for r in all_results:
    if r['rev_type'] == 'FLAT/CHOP':
        print(f"{r['date']:<11} {'FLAT':<14} {r['range_pts']:>4.0f}")
        continue
    rej = '/'.join(cell(r, l, 'rejection') for l in ['ATM-1', 'ATM', 'ATM+1'])
    oi  = '/'.join(cell(r, l, 'oi')        for l in ['ATM-1', 'ATM', 'ATM+1'])
    dlt = '/'.join(cell(r, l, 'delta')     for l in ['ATM-1', 'ATM', 'ATM+1'])
    prm = '/'.join(cell(r, l, 'prem')      for l in ['ATM-1', 'ATM', 'ATM+1'])
    print(f"{r['date']:<11} {r['rev_type']:<14} {r['range_pts']:>4.0f} "
          f"{r['atm']:>6.0f} {r['rev_time']:<6}  "
          f"  {rej:<16}   {oi:<12}   {dlt:<14}   {prm:<14}")

# Aggregate per strike position
print()
print("===== AGGREGATE HIT RATE BY STRIKE POSITION =====")
eligible = [r for r in all_results if r['rev_type'] != 'FLAT/CHOP']
labels = ['ATM-1', 'ATM', 'ATM+1']
signals = [('rejection', 'REJECTION at level'),
           ('oi',        'OI SHIFT (>10%)'),
           ('delta',     'DELTA CROSSOVER'),
           ('prem',      'PREMIUM STALL')]
header = f"{'Signal':<25}"
for l in labels:
    header += f"{l:>12}"
print(header)
print('-' * (25 + 12*3))
for sk, sname in signals:
    line = f"{sname:<25}"
    for l in labels:
        y = sum(1 for r in eligible if r['results'].get(l) and r['results'][l][sk] == 'YES')
        n = sum(1 for r in eligible if r['results'].get(l) and r['results'][l][sk] == 'NO')
        u = sum(1 for r in eligible if r['results'].get(l) and r['results'][l][sk] == '?')
        if y + n == 0:
            line += f"{'-':>12}"
        else:
            pct = y / (y + n) * 100
            line += f"{y}/{y+n} ({pct:.0f}%)".rjust(12)
    print(line)
