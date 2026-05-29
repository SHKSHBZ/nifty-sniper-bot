"""
Quick analysis script - run from project root.
"""
import sys, json
sys.path.insert(0, '.')
sys.path.insert(0, 'backtesting')

from backtesting.backtest_regime_phase1 import load_spot, load_vix
from backtesting.backtest_regime_phase4 import simulate_one_pass
from datetime import date, time
from pathlib import Path
import pandas as pd
import numpy as np

spot_1m = load_spot()
vix_1m = load_vix()
trades = simulate_one_pass(spot_1m, vix_1m, {}, regime_gated=False)
print(f"Got {len(trades)} trades")

df = pd.DataFrame([t.__dict__ for t in trades])

# 1. Direction breakdown
print("\n=== DIRECTION BREAKDOWN ===")
for d in sorted(df['direction'].unique()):
    sub = df[df['direction']==d]
    wr = len(sub[sub['net_pnl']>0])/len(sub)*100
    print(f"  {d}: {len(sub):4d} trades  PnL={sub['net_pnl'].sum():>8,.0f}  WR={wr:.1f}%  avgEntry={sub['entry_premium'].mean():.0f}")

# 2. Exit reasons
print("\n=== EXIT REASONS ===")
for r in sorted(df['exit_reason'].unique()):
    sub = df[df['exit_reason']==r]
    wr = len(sub[sub['net_pnl']>0])/len(sub)*100
    print(f"  {r:12s}: {len(sub):4d} trades  PnL={sub['net_pnl'].sum():>8,.0f}  WR={wr:.1f}%")

# 3. Avg win/loss
w = df[df['net_pnl']>0]
l = df[df['net_pnl']<=0]
print(f"\n=== RISK/REWARD ===")
print(f"  Avg Win: Rs {w['net_pnl'].mean():,.0f}  ({len(w)} trades)")
print(f"  Avg Loss: Rs {l['net_pnl'].mean():,.0f}  ({len(l)} trades)")
be_wr = -l['net_pnl'].mean()/(w['net_pnl'].mean()-l['net_pnl'].mean())*100
print(f"  Breakeven WR needed: {be_wr:.1f}%")
print(f"  Actual WR: {len(w)/len(df)*100:.1f}%")

# 4. Entry hour analysis
print("\n=== ENTRY HOUR ===")
df['entry_hour'] = df['entry_ts'].apply(lambda x: x.hour)
for h in sorted(df['entry_hour'].unique()):
    sub = df[df['entry_hour']==h]
    print(f"  {h:02d}:00 : {len(sub):3d} trades  PnL={sub['net_pnl'].sum():>8,.0f}  WR={len(sub[sub['net_pnl']>0])/len(sub)*100:.1f}%")

# 5. Entry premium buckets
print("\n=== ENTRY PREMIUM BUCKETS ===")
df['prem_bucket'] = pd.cut(df['entry_premium'], bins=[0,25,50,75,100,150,200,300,500,1000])
for bucket in sorted(df['prem_bucket'].dropna().unique()):
    sub = df[df['prem_bucket']==bucket]
    if len(sub)==0: continue
    wr = len(sub[sub['net_pnl']>0])/len(sub)*100
    print(f"  {bucket}: {len(sub):4d} trades  PnL={sub['net_pnl'].sum():>8,.0f}  WR={wr:.1f}%")

# 6. TP/SL analysis
tp = df[df['exit_reason']=='TP']
sl = df[df['exit_reason']=='SL']
ts = df[df['exit_reason']=='TIME_STOP']
print(f"\n=== TP/SL PARAMS ===")
opts = json.loads(Path('Options.json').read_text())
cp = opts.get('configurableParameters', {})
print(f"  SL={cp.get('normalDayStopLossPercent', '?')}%  TP={cp.get('normalDayTargetPercent', '?')}%  TimeStop={cp.get('thetaShieldNormalMins', '?')}min")
print(f"  TP avg: Rs {tp['net_pnl'].mean():,.0f} (hits: {len(tp)}/{len(df)} = {len(tp)/len(df)*100:.1f}%)")
print(f"  SL avg: Rs {sl['net_pnl'].mean():,.0f} (hits: {len(sl)}/{len(df)} = {len(sl)/len(df)*100:.1f}%)")
print(f"  TS avg: Rs {ts['net_pnl'].mean():,.0f} (hits: {len(ts)}/{len(df)} = {len(ts)/len(df)*100:.1f}%)")
ts_loss = ts['net_pnl'].sum()
total_pnl = df['net_pnl'].sum()
print(f"  TimeStop contribution: Rs {ts_loss:,.0f} / Rs {total_pnl:,.0f} = {ts_loss/total_pnl*100:.1f}% of total")

# 7. Regime detail for CE only
ce = df[df['direction']=='CE']
print(f"\n=== CE REGIME BREAKDOWN ===")
for regime in sorted(ce['regime_at_entry'].dropna().unique()):
    sub = ce[ce['regime_at_entry']==regime]
    wr = len(sub[sub['net_pnl']>0])/len(sub)*100
    print(f"  {regime.value:20s}: {len(sub):4d} trades  PnL={sub['net_pnl'].sum():>8,.0f}  WR={wr:.1f}%")

# 8. Entry premium trend over months
df['month'] = df['day'].apply(lambda x: x[:7])
print(f"\n=== MONTHLY AVG ENTRY PREMIUM ===")
for m in sorted(df['month'].unique()):
    sub = df[df['month']==m]
    print(f"  {m}: avg entry prem={sub['entry_premium'].mean():.0f}  avg exit prem={sub['exit_premium'].mean():.0f}  diff={sub['exit_premium'].mean()-sub['entry_premium'].mean():+.0f}")
