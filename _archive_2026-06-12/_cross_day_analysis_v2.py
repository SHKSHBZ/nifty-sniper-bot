"""V2: measure signals at the DYNAMIC ATM (strike closest to spot AT THE MOMENT
of measurement), not the open's ATM. Should give a fairer test of the 4 signals.
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
    strikes = sorted(strikes)
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

    def atm_at(idx):
        sp = spots[idx]
        return min(strikes, key=lambda x: abs(x - sp))

    hi = max(spots); lo = min(spots)
    hi_i = spots.index(hi); lo_i = spots.index(lo)

    if hi_i < lo_i:
        rev_type = "HIGH-then-DOWN"
        rev_i = hi_i; rev_spot = hi
        winning_side = "PE"
    else:
        rev_type = "LOW-then-UP"
        rev_i = lo_i; rev_spot = lo
        winning_side = "CE"

    rev_t = times[rev_i]
    day_range = hi - lo

    base = dict(date=path[-14:-4], rev_type=rev_type, range_pts=day_range,
                reversal_time=rev_t[11:16], reversal_spot=rev_spot,
                open_spot=spots[0])

    if day_range < 30:
        base.update(rev_type='FLAT/CHOP', sig_rejection='-', sig_oi_unwind='-',
                    sig_delta_cross='-', sig_premium_stall='-')
        return base

    pre_idx = max(0, rev_i - 15)
    pre30_idx = max(0, rev_i - 30)

    atm_rev = atm_at(rev_i)
    atm_pre = atm_at(pre_idx)
    atm_pre30 = atm_at(pre30_idx)

    base['atm_rev'] = atm_rev

    def get(ts, atm_strike, col):
        r = rows.get((ts, atm_strike))
        return float(r[col]) if r else None

    # Signal 1: rejection at level (no change — uses spot only)
    threshold = rev_spot * 0.001
    bucket_touches = set()
    lookback = max(0, rev_i - 60)
    for i in range(lookback, rev_i):
        if abs(spots[i] - rev_spot) <= threshold:
            bucket_touches.add(times[i][11:16][:4])
    sig_rejection = "YES" if len(bucket_touches) >= 2 else "NO"

    win_oi_col = 'pe_oi' if winning_side == 'PE' else 'ce_oi'
    los_d_col = 'ce_delta' if winning_side == 'PE' else 'pe_delta'
    win_d_col = 'pe_delta' if winning_side == 'PE' else 'ce_delta'
    los_p_col = 'ce_ltp' if winning_side == 'PE' else 'pe_ltp'

    # Signal 2: winning-side OI unwinding at the DYNAMIC ATM
    # ('Unwinding' on winning side = put writers covering for PE-win = bullish→bearish flip)
    # Actually we want the LOSING side OI to be GROWING (call writers stacking for HIGH reversal)
    # OR the winning-side OI to be unwinding. Either signals the regime flip.
    los_oi_col = 'ce_oi' if winning_side == 'PE' else 'pe_oi'
    pre_los_oi = get(times[pre_idx], atm_pre, los_oi_col)
    rev_los_oi = get(rev_t, atm_rev, los_oi_col)
    pre_win_oi = get(times[pre_idx], atm_pre, win_oi_col)
    rev_win_oi = get(rev_t, atm_rev, win_oi_col)
    los_oi_pct = None; win_oi_pct = None
    if pre_los_oi and rev_los_oi and pre_los_oi > 0:
        los_oi_pct = (rev_los_oi - pre_los_oi) / pre_los_oi * 100
    if pre_win_oi and rev_win_oi and pre_win_oi > 0:
        win_oi_pct = (rev_win_oi - pre_win_oi) / pre_win_oi * 100
    # Signal fires if losing OI grew >10% OR winning OI unwound >10%
    fired = False
    if los_oi_pct is not None and los_oi_pct >= 10:
        fired = True
    if win_oi_pct is not None and win_oi_pct <= -10:
        fired = True
    sig_oi_shift = "YES" if fired else ("NO" if (los_oi_pct is not None or win_oi_pct is not None) else "?")

    # Signal 3: delta crossover at ATM-rev
    pre_los_d = get(times[pre_idx], atm_pre, los_d_col)
    rev_los_d = get(rev_t, atm_rev, los_d_col)
    pre_win_d = get(times[pre_idx], atm_pre, win_d_col)
    rev_win_d = get(rev_t, atm_rev, win_d_col)
    if None not in (pre_los_d, rev_los_d, pre_win_d, rev_win_d):
        crossover = (pre_los_d > 0.50 and rev_los_d < 0.50) or \
                    (pre_win_d < 0.50 and rev_win_d > 0.50)
        sig_delta_cross = "YES" if crossover else "NO"
    else:
        sig_delta_cross = "?"

    # Signal 4: losing-side premium stall while spot continued in losing direction
    # Use ATM-pre30 strike for measurement (it was ATM 30 min before reversal)
    pre30_prem = get(times[pre30_idx], atm_pre30, los_p_col)
    rev_prem = get(rev_t, atm_pre30, los_p_col)
    pre30_spot = spots[pre30_idx]
    rev_spot_act = spots[rev_i]
    spot_moved_in_losing_dir = (
        (winning_side == 'PE' and rev_spot_act >= pre30_spot)
        or (winning_side == 'CE' and rev_spot_act <= pre30_spot)
    )
    if pre30_prem and rev_prem and pre30_prem > 0 and spot_moved_in_losing_dir:
        prem_gain = (rev_prem - pre30_prem) / pre30_prem * 100
        sig_premium_stall = "YES" if prem_gain < 5 else "NO"
    else:
        prem_gain = None
        sig_premium_stall = "?"

    base.update(sig_rejection=sig_rejection,
                sig_oi_shift=sig_oi_shift,
                sig_delta_cross=sig_delta_cross,
                sig_premium_stall=sig_premium_stall,
                los_oi_pct=los_oi_pct,
                win_oi_pct=win_oi_pct,
                prem_gain=prem_gain)
    return base


results = []
for f in FILES:
    r = analyze(f)
    if r:
        results.append(r)

print(f"{'Date':<12} {'Rev type':<16} {'Rng':>4} {'ATM_rev':>7} {'RevT':<7} "
      f"{'Reject':>7} {'OIShft':>7} {'DeltaX':>7} {'PremStl':>7}")
print('-' * 90)
for r in results:
    print(f"{r['date']:<12} {r['rev_type']:<16} {r.get('range_pts', 0):>4.0f} "
          f"{r.get('atm_rev', '-'):>7} {r.get('reversal_time', '-'):<7} "
          f"{r['sig_rejection']:>7} {r['sig_oi_shift']:>7} "
          f"{r['sig_delta_cross']:>7} {r['sig_premium_stall']:>7}")

print()
print("===== AGGREGATE (V2 — dynamic ATM) =====")
eligible = [r for r in results if r['rev_type'] != 'FLAT/CHOP']
print(f"Reversal-eligible days: {len(eligible)} of {len(results)}\n")
for key, name in [
    ('sig_rejection',   'REJECTION at level (2+ touches/60min)  '),
    ('sig_oi_shift',    'OI SHIFT (>10% adverse build OR unwind)'),
    ('sig_delta_cross', 'DELTA CROSSOVER at ATM-rev (through 0.5)'),
    ('sig_premium_stall','PREMIUM STALL on losing side (<5%/30m) '),
]:
    y = sum(1 for r in eligible if r[key] == 'YES')
    n = sum(1 for r in eligible if r[key] == 'NO')
    u = sum(1 for r in eligible if r[key] == '?')
    pct = y / (y + n) * 100 if (y + n) else 0
    print(f"  {name}  {y}/{y + n} fired ({pct:.0f}%)  [unknown: {u}]")

# Show the actual % numbers per day for OI / premium so we see the distribution
print("\n=== Per-day numbers ===")
print(f"{'Date':<12} {'LosOI%':>8} {'WinOI%':>8} {'PremGain%':>10}")
for r in eligible:
    los = f"{r.get('los_oi_pct'):+.1f}" if r.get('los_oi_pct') is not None else "  -"
    win = f"{r.get('win_oi_pct'):+.1f}" if r.get('win_oi_pct') is not None else "  -"
    pg = f"{r.get('prem_gain'):+.1f}" if r.get('prem_gain') is not None else "  -"
    print(f"{r['date']:<12} {los:>8} {win:>8} {pg:>10}")
