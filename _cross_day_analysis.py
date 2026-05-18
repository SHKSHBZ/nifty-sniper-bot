"""
Cross-day validation of 4 'missing signal' hypotheses identified on 2026-05-15:
  1. REJECTION at level    - did spot test a level 2+ times before reversing?
  2. OI UNWINDING          - did opposite-side OI drop sharply at the reversal?
  3. DELTA CROSSOVER       - did CE/PE delta cross at ATM around the reversal?
  4. PREMIUM VELOCITY STALL- did losing side's premium stall while spot continued?
Run on all usable NIFTY focus_zone CSVs.
"""
import csv, glob

FILES = sorted(glob.glob('logs/focus_zone_nifty_2026-05-*.csv'))


def analyze(path):
    rows = {}
    strikes = set()
    with open(path, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            key = (r['timestamp'], float(r['strike']))
            if key not in rows:
                rows[key] = r
                strikes.add(float(r['strike']))
    if not rows:
        return None
    all_ts = sorted({k[0] for k in rows.keys()})

    market_ts = [t for t in all_ts if "09:15" <= t[11:16] < "15:30"]
    if len(market_ts) < 50:
        return None

    spot_by_t = {}
    for ts in market_ts:
        for s in strikes:
            if (ts, s) in rows:
                spot_by_t[ts] = float(rows[(ts, s)]['spot'])
                break
    spots = list(spot_by_t.values())
    times = list(spot_by_t.keys())

    open_spot = spots[0]
    atm = round(open_spot / 50) * 50
    if atm not in strikes:
        atm = min(strikes, key=lambda x: abs(x - open_spot))

    hi = max(spots); lo = min(spots)
    hi_i = spots.index(hi); lo_i = spots.index(lo)
    hi_t = times[hi_i]; lo_t = times[lo_i]

    if hi_i < lo_i:
        rev_type = "HIGH-then-DOWN"
        rev_t = hi_t; rev_i = hi_i; rev_spot = hi
        winning_side = "PE"
    else:
        rev_type = "LOW-then-UP"
        rev_t = lo_t; rev_i = lo_i; rev_spot = lo
        winning_side = "CE"

    day_range = hi - lo
    base = dict(date=path[-14:-4], rev_type=rev_type, range_pts=day_range,
                atm=atm, reversal_time=rev_t[11:16],
                open_spot=open_spot, high=hi, low=lo)

    if day_range < 30:
        base.update(rev_type='FLAT/CHOP', sig_rejection='-', sig_oi_unwind='-',
                    sig_delta_cross='-', sig_premium_stall='-')
        return base

    # Signal 1: rejection at the level
    threshold = rev_spot * 0.001
    bucket_touches = set()
    lookback = max(0, rev_i - 60)
    for i in range(lookback, rev_i):
        if abs(spots[i] - rev_spot) <= threshold:
            bucket_touches.add(times[i][11:16][:4])
    sig_rejection = "YES" if len(bucket_touches) >= 2 else "NO"

    def get_field(ts, col):
        r = rows.get((ts, atm))
        return float(r[col]) if r else None

    win_oi_col = 'pe_oi' if winning_side == 'PE' else 'ce_oi'
    los_oi_col = 'ce_oi' if winning_side == 'PE' else 'pe_oi'
    los_d_col = 'ce_delta' if winning_side == 'PE' else 'pe_delta'
    win_d_col = 'pe_delta' if winning_side == 'PE' else 'ce_delta'
    los_p_col = 'ce_ltp' if winning_side == 'PE' else 'pe_ltp'

    pre_idx = max(0, rev_i - 15)
    pre30_idx = max(0, rev_i - 30)

    # Signal 2: opposite side OI unwind in 15 min before reversal
    rev_oi_w = get_field(rev_t, win_oi_col)
    pre_oi_w = get_field(times[pre_idx], win_oi_col)
    if rev_oi_w and pre_oi_w and pre_oi_w > 0:
        oi_unwind_pct = (rev_oi_w - pre_oi_w) / pre_oi_w * 100
        sig_oi_unwind = "YES" if oi_unwind_pct <= -10 else "NO"
    else:
        oi_unwind_pct = 0
        sig_oi_unwind = "?"

    # Signal 3: delta crossover at ATM in 15 min before reversal
    pre_los_d = get_field(times[pre_idx], los_d_col)
    rev_los_d = get_field(rev_t, los_d_col)
    pre_win_d = get_field(times[pre_idx], win_d_col)
    rev_win_d = get_field(rev_t, win_d_col)
    if None not in (pre_los_d, rev_los_d, pre_win_d, rev_win_d):
        crossover = (pre_los_d > 0.50 and rev_los_d < 0.50) or \
                    (pre_win_d < 0.50 and rev_win_d > 0.50)
        sig_delta_cross = "YES" if crossover else "NO"
    else:
        sig_delta_cross = "?"

    # Signal 4: losing-side premium stall while spot continued
    pre30_prem = get_field(times[pre30_idx], los_p_col)
    rev_prem = get_field(rev_t, los_p_col)
    pre30_spot = spots[pre30_idx]
    rev_spot_act = spots[rev_i]
    spot_moved_favorably = (
        (winning_side == 'PE' and rev_spot_act >= pre30_spot - 2)
        or (winning_side == 'CE' and rev_spot_act <= pre30_spot + 2)
    )
    if pre30_prem and rev_prem and pre30_prem > 0 and spot_moved_favorably:
        prem_gain = (rev_prem - pre30_prem) / pre30_prem * 100
        sig_premium_stall = "YES" if prem_gain < 5 else "NO"
    else:
        prem_gain = 0
        sig_premium_stall = "?"

    base.update(sig_rejection=sig_rejection,
                sig_oi_unwind=sig_oi_unwind,
                sig_delta_cross=sig_delta_cross,
                sig_premium_stall=sig_premium_stall,
                oi_unwind_pct=oi_unwind_pct,
                prem_gain=prem_gain)
    return base


results = []
for f in FILES:
    r = analyze(f)
    if r:
        results.append(r)

print(f"{'Date':<12} {'Rev type':<16} {'Range':>6} {'ATM':>6} {'RevT':<7} "
      f"{'Reject':>7} {'OIUnwd':>7} {'DeltaX':>7} {'PremStl':>7}")
print('-' * 92)
for r in results:
    print(f"{r['date']:<12} {r['rev_type']:<16} {r.get('range_pts', 0):>6.0f} "
          f"{r.get('atm', 0):>6.0f} {r.get('reversal_time', '-'):<7} "
          f"{r['sig_rejection']:>7} {r['sig_oi_unwind']:>7} "
          f"{r['sig_delta_cross']:>7} {r['sig_premium_stall']:>7}")

print()
print("===== AGGREGATE =====")
eligible = [r for r in results if r['rev_type'] != 'FLAT/CHOP']
print(f"Reversal-eligible days: {len(eligible)} of {len(results)}")
for key, name in [
    ('sig_rejection',   'REJECTION at level (2+ touches in 60min)'),
    ('sig_oi_unwind',   'OI UNWINDING (>10% drop in 15min)'),
    ('sig_delta_cross', 'DELTA CROSSOVER at ATM (through 0.5)'),
    ('sig_premium_stall','PREMIUM STALL (<5% gain in 30min)'),
]:
    y = sum(1 for r in eligible if r[key] == 'YES')
    n = sum(1 for r in eligible if r[key] == 'NO')
    u = sum(1 for r in eligible if r[key] == '?')
    pct = y / (y + n) * 100 if (y + n) else 0
    print(f"  {name:<42}  {y}/{y + n} fired ({pct:.0f}%)  [unknown: {u}]")
