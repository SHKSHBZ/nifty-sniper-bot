"""
Zone Framework Backtest — Simplified gates + ITM strikes + zone-based exits.

Replays the new 2026-05-26 logic against focus_zone CSV tick data.

Changes from replay_new_gates.py:
  1. Entry: Focus PCR only (no slope / OI-delta / build-up sub-gates)
  2. ITM strike: 1-2 strikes inside ATM for higher delta
  3. Zone exits: target at opposite S/R, zone invalidation, theta guard
  4. SL/TP/EOD kept as safety net

Usage:
    python backtesting/replay_zone_framework.py
"""
import pandas as pd
from pathlib import Path

# ── Config (matching live signal_engine.py + main.py) ──────────────────
PCR_CE_LOW, PCR_CE_HIGH = 1.00, 1.30
PCR_PE_LOW, PCR_PE_HIGH = 0.50, 0.95
PROXIMITY_PCT = 0.0015        # 0.15% for "near wall"
ZONE_TARGET_PCT = 0.0015      # 0.15% for zone target hit
SUSTAIN_TICKS = 3             # 3x 5m candles
SL_PCT = 0.15                 # 15% SL (tightened to beat zone-inv)
THETA_GUARD_MIN = 45          # exit after 45 min if no progress
THETA_PROGRESS_PCT = 0.0015   # 0.15% min spot progress
BROKERAGE = 60.0
STRIKE_STEP = 50
ITM_MAX_ATTEMPTS = 2          # try up to 2 strikes ITM

# ── 5-lot tiered profit booking ─────────────────────────────────────
TOTAL_LOTS = 5
TIER1_PTS = 20   # +20 pts → sell 3 lots
TIER2_PTS = 35   # +35 pts → sell 1 lot
TIER1_LOTS = 3
TIER2_LOTS = 1
TRAIL_LOTS = 1

ROOT = Path(__file__).resolve().parent.parent


def simulate_day(csv_path, lot_size, label):
    """Run the zone-framework simulation on one day's focus_zone CSV."""
    df = pd.read_csv(csv_path)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("ts").reset_index(drop=True)

    # Fix UAE timezone offset (UTC+4 → IST +1:30)
    if not df.empty and df["ts"].iloc[-1].hour <= 14:
        df["ts"] = df["ts"] + pd.Timedelta(hours=1, minutes=30)

    minutes = sorted(df["ts"].unique())

    # ── Build per-minute snapshots ─────────────────────────────────
    snaps = {}
    for ts, g in df.groupby("ts"):
        spot = g["spot"].iloc[0]
        ce_oi = g["ce_oi"].sum()
        pe_oi = g["pe_oi"].sum()
        pcr = pe_oi / ce_oi if ce_oi > 0 else 1.0
        below = g[g["strike"] < spot]
        above = g[g["strike"] > spot]
        sup = int(below.loc[below["pe_oi"].idxmax(), "strike"]) if len(below) else None
        res = int(above.loc[above["ce_oi"].idxmax(), "strike"]) if len(above) else None
        atm_row = g.loc[(g["strike"] - spot).abs().idxmin()]
        snaps[ts] = {
            "spot": spot, "pcr": pcr,
            "support": sup, "resistance": res,
            "atm_strike": int(atm_row["strike"]),
            "atm_ce_ltp": atm_row["ce_ltp"],
            "atm_pe_ltp": atm_row["pe_ltp"],
            "group": g.set_index("strike")[["ce_ltp", "pe_ltp"]],
        }

    # ── Helpers ────────────────────────────────────────────────────
    def near(level, spot_val):
        return level and abs(spot_val - level) / level <= PROXIMITY_PCT

    def sustained(level, hist):
        if level is None or len(hist) < SUSTAIN_TICKS:
            return False
        for _, s in hist[-SUSTAIN_TICKS:]:
            if not near(level, s):
                return False
        return True

    def get_itm_strike(atm, direction, snap_grp):
        """Pick 1-2 strikes ITM. CE → below ATM, PE → above ATM."""
        for attempt in range(ITM_MAX_ATTEMPTS):
            if direction == "CE":
                candidate = atm - ((attempt + 1) * STRIKE_STEP)
            else:
                candidate = atm + ((attempt + 1) * STRIKE_STEP)
            if candidate in snap_grp.index:
                col = "ce_ltp" if direction == "CE" else "pe_ltp"
                ltp = snap_grp.at[candidate, col]
                if ltp > 0:
                    return candidate, ltp
        # Fallback to ATM
        col = "ce_ltp" if direction == "CE" else "pe_ltp"
        return atm, snap_grp.at[atm, col]

    # ── Simulation ─────────────────────────────────────────────────
    trades = []
    pos = None
    spot_hist = []

    for ts in minutes:
        snap = snaps[ts]
        spot = snap["spot"]

        # 5-min spot history for sustain check
        if not spot_hist or (ts - spot_hist[-1][0]).total_seconds() >= 300:
            spot_hist.append((ts, spot))
            if len(spot_hist) > 10:
                spot_hist.pop(0)

        # ── Manage open position ────────────────────────────────
        if pos:
            strike = pos["strike"]
            if strike in snap["group"].index:
                ltp_col = "ce_ltp" if pos["opt"] == "CE" else "pe_ltp"
                ltp = snap["group"].at[strike, ltp_col]
                time_held = (ts - pos["entry_t"]).total_seconds() / 60
                pts_from_entry = ltp - pos["entry_p"]
                itm_tag = pos.get("itm_tag", "")

                # ── Tiered profit booking ──────────────────────────
                # Tier 1: +20 pts -> sell 3 lots
                if not pos.get("tier1_done") and pts_from_entry >= TIER1_PTS:
                    lots = min(TIER1_LOTS, pos["remaining"])
                    pnl = pts_from_entry * lots * lot_size - 40
                    trades.append({
                        "index": label, "opt": pos["opt"],
                        "strike": f"{pos['strike']}({itm_tag})",
                        "entry_t": pos["entry_t"].strftime("%H:%M"),
                        "exit_t": ts.strftime("%H:%M"),
                        "entry_p": round(pos["entry_p"], 2),
                        "exit_p": round(ltp, 2),
                        "pcr": round(pos["pcr"], 2),
                        "pnl": round(pnl, 0), "reason": "TIER1",
                        "zone": pos.get("zone", ""), "lots": lots,
                    })
                    pos["tier1_done"] = True
                    pos["remaining"] -= lots

                # Tier 2: +35 pts -> sell 1 lot
                if pos.get("tier1_done") and not pos.get("tier2_done") and pts_from_entry >= TIER2_PTS:
                    lots = min(TIER2_LOTS, pos["remaining"])
                    pnl = pts_from_entry * lots * lot_size - 40
                    trades.append({
                        "index": label, "opt": pos["opt"],
                        "strike": f"{pos['strike']}({itm_tag})",
                        "entry_t": pos["entry_t"].strftime("%H:%M"),
                        "exit_t": ts.strftime("%H:%M"),
                        "entry_p": round(pos["entry_p"], 2),
                        "exit_p": round(ltp, 2),
                        "pcr": round(pos["pcr"], 2),
                        "pnl": round(pnl, 0), "reason": "TIER2",
                        "zone": pos.get("zone", ""), "lots": lots,
                    })
                    pos["tier2_done"] = True
                    pos["remaining"] -= lots
                    pos["trail_sl"] = pos["entry_p"]  # breakeven for trail lot

                # All lots sold via tiers -> close
                if pos["remaining"] <= 0:
                    pos = None
                    continue

                exit_p, reason = None, None

                # Trail lot SL at breakeven
                trail_sl = pos.get("trail_sl")
                if trail_sl and ltp <= trail_sl:
                    exit_p, reason = ltp, "TRAIL-SL"

                # Zone target
                if not exit_p:
                    if pos["opt"] == "CE" and pos["zone_res"]:
                        if abs(spot - pos["zone_res"]) / spot <= ZONE_TARGET_PCT:
                            exit_p, reason = ltp, "ZONE-TGT"
                    elif pos["opt"] == "PE" and pos["zone_sup"]:
                        if abs(spot - pos["zone_sup"]) / spot <= ZONE_TARGET_PCT:
                            exit_p, reason = ltp, "ZONE-TGT"

                # Zone invalidation (last-resort; 15% SL should fire first)
                if not exit_p:
                    if pos["opt"] == "CE" and pos["zone_sup"] and spot < pos["zone_sup"]:
                        exit_p, reason = ltp, "ZONE-INV"
                    elif pos["opt"] == "PE" and pos["zone_res"] and spot > pos["zone_res"]:
                        exit_p, reason = ltp, "ZONE-INV"

                # Theta guard
                if not exit_p and time_held >= THETA_GUARD_MIN:
                    if pos["opt"] == "CE":
                        progress = (spot - pos["entry_spot"]) / pos["entry_spot"]
                    else:
                        progress = (pos["entry_spot"] - spot) / pos["entry_spot"]
                    if progress < THETA_PROGRESS_PCT:
                        exit_p, reason = ltp, "THETA"

                # Static SL (only if trail not active)
                if not exit_p and not trail_sl:
                    sl_price = pos["entry_p"] * (1 - SL_PCT)
                    if ltp <= sl_price:
                        exit_p, reason = ltp, "SL"

                # EOD force close
                if not exit_p and ts.time() >= pd.Timestamp("15:30").time():
                    exit_p, reason = ltp, "EOD"

                if exit_p is not None:
                    pnl = (exit_p - pos["entry_p"]) * pos["remaining"] * lot_size - BROKERAGE
                    trades.append({
                        "index": label,
                        "opt": pos["opt"],
                        "strike": f"{pos['strike']}({itm_tag})",
                        "entry_t": pos["entry_t"].strftime("%H:%M"),
                        "exit_t": ts.strftime("%H:%M"),
                        "entry_p": round(pos["entry_p"], 2),
                        "exit_p": round(exit_p, 2),
                        "pcr": round(pos["pcr"], 2),
                        "pnl": round(pnl, 0),
                        "reason": reason,
                        "zone": f"S={pos.get('zone_sup','?')}→R={pos.get('zone_res','?')}",
                    })
                    pos = None

        # ── Entry scan ───────────────────────────────────────────
        time_ok = (ts.time() >= pd.Timestamp("10:00").time() and
                   ts.time() < pd.Timestamp("15:30").time())
        if pos is None and time_ok:
            sup = snap["support"]
            res = snap["resistance"]

            # CE entry: near support + sustained + PCR bullish
            if near(sup, spot) and sustained(sup, spot_hist):
                if PCR_CE_LOW <= snap["pcr"] <= PCR_CE_HIGH:
                    strike, ltp = get_itm_strike(snap["atm_strike"], "CE", snap["group"])
                    itm_offset = (snap["atm_strike"] - strike) // STRIKE_STEP
                    itm_tag_str = f"ITM{itm_offset}" if itm_offset else "ATM"
                    if ltp > 0:
                        pos = {
                            "opt": "CE", "strike": strike, "entry_p": ltp,
                            "entry_t": ts, "entry_spot": spot, "pcr": snap["pcr"],
                            "sl": ltp * (1 - SL_PCT),
                            "zone_sup": sup, "zone_res": res,
                            "zone": f"S={sup}->R={res}",
                            "itm_tag": itm_tag_str, "itm_offset": itm_offset,
                            "remaining": TOTAL_LOTS, "total_lots": TOTAL_LOTS,
                            "tier1_done": False, "tier2_done": False,
                            "trail_sl": None,
                        }

            # PE entry: near resistance + sustained + PCR bearish
            elif near(res, spot) and sustained(res, spot_hist):
                if PCR_PE_LOW <= snap["pcr"] <= PCR_PE_HIGH:
                    strike, ltp = get_itm_strike(snap["atm_strike"], "PE", snap["group"])
                    itm_offset = (strike - snap["atm_strike"]) // STRIKE_STEP
                    itm_tag_str = f"ITM{itm_offset}" if itm_offset else "ATM"
                    if ltp > 0:
                        pos = {
                            "opt": "PE", "strike": strike, "entry_p": ltp,
                            "entry_t": ts, "entry_spot": spot, "pcr": snap["pcr"],
                            "sl": ltp * (1 - SL_PCT),
                            "zone_sup": sup, "zone_res": res,
                            "zone": f"S={sup}->R={res}",
                            "itm_tag": itm_tag_str, "itm_offset": itm_offset,
                            "remaining": TOTAL_LOTS, "total_lots": TOTAL_LOTS,
                            "tier1_done": False, "tier2_done": False,
                            "trail_sl": None,
                        }

    # ── Force-close any open position at last tick ────────────────
    if pos:
        last_ts = minutes[-1]
        snap = snaps[last_ts]
        strike = pos["strike"]
        if strike in snap["group"].index:
            ltp_col = "ce_ltp" if pos["opt"] == "CE" else "pe_ltp"
            ltp = snap["group"].at[strike, ltp_col]
            pnl = (ltp - pos["entry_p"]) * pos["remaining"] * lot_size - BROKERAGE
            itm_tag = pos.get("itm_tag", "")
            trades.append({
                "index": label, "opt": pos["opt"],
                "strike": f"{pos['strike']}({itm_tag})",
                "entry_t": pos["entry_t"].strftime("%H:%M"),
                "exit_t": last_ts.strftime("%H:%M"),
                "entry_p": round(pos["entry_p"], 2),
                "exit_p": round(ltp, 2),
                "pcr": round(pos["pcr"], 2),
                "pnl": round(pnl, 0), "reason": "TIMEOUT",
                "zone": pos.get("zone", ""), "lots": pos["remaining"],
            })

    return pd.DataFrame(trades)


def find_available_days(logs_dir, index="nifty"):
    """Find all focus_zone CSV files available for backtesting."""
    files = sorted(logs_dir.glob(f"focus_zone_{index}_*.csv"))
    days = []
    for f in files:
        # Extract date from filename: focus_zone_nifty_2026-05-25.csv
        date_str = f.stem.replace(f"focus_zone_{index}_", "")
        days.append(date_str)
    return days


def main():
    logs_dir = ROOT / "logs"

    # Find available days
    nifty_days = find_available_days(logs_dir, "nifty")
    sensex_days = find_available_days(logs_dir, "sensex")

    print("=" * 72)
    print("  ZONE FRAMEWORK BACKTEST")
    print(f"  Entry: Focus PCR only | ITM strikes | Zone exits")
    print(f"  Available NIFTY days: {nifty_days}")
    print(f"  Available SENSEX days: {sensex_days}")
    print("=" * 72)

    grand_total = 0.0
    all_trades = []

    for date_str in nifty_days[-5:]:  # last 5 NIFTY days
        path = logs_dir / f"focus_zone_nifty_{date_str}.csv"
        if not path.exists():
            continue
        print(f"\n─── NIFTY {date_str} ───")
        try:
            t = simulate_day(str(path), 75, "NIFTY")
            n = len(t)
            pnl = float(t["pnl"].sum()) if n else 0.0
            wins = int((t["pnl"] > 0).sum()) if n else 0
            print(f"  {n} trades, {wins}W/{n-wins}L, P&L Rs.{pnl:,.0f}")
            for _, r in t.iterrows():
                lots_str = f"x{r['lots']}L" if 'lots' in r and pd.notna(r['lots']) else ""
                print(f"    {r['entry_t']}→{r['exit_t']}  {r['opt']} {r['strike']}  "
                      f"Rs.{r['entry_p']:.0f}→Rs.{r['exit_p']:.0f}  "
                      f"PCR={r['pcr']:.2f}  {r['reason']:<10}  Rs.{r['pnl']:+,.0f}  "
                      f"{lots_str}  [{r['zone']}]")
            grand_total += pnl
            all_trades.append(t)
        except Exception as e:
            print(f"  ERR: {e}")

    for date_str in sensex_days[-3:]:  # last 3 SENSEX days
        path = logs_dir / f"focus_zone_sensex_{date_str}.csv"
        if not path.exists():
            continue
        print(f"\n─── SENSEX {date_str} ───")
        try:
            t = simulate_day(str(path), 20, "SENSEX")
            n = len(t)
            pnl = float(t["pnl"].sum()) if n else 0.0
            wins = int((t["pnl"] > 0).sum()) if n else 0
            print(f"  {n} trades, {wins}W/{n-wins}L, P&L Rs.{pnl:,.0f}")
            for _, r in t.iterrows():
                print(f"    {r['entry_t']}→{r['exit_t']}  {r['opt']} {r['strike']}  "
                      f"Rs.{r['entry_p']:.0f}→Rs.{r['exit_p']:.0f}  "
                      f"PCR={r['pcr']:.2f}  {r['reason']:<10}  Rs.{r['pnl']:+,.0f}")
            grand_total += pnl
            all_trades.append(t)
        except Exception as e:
            print(f"  ERR: {e}")

    # ── Summary ──────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"  GRAND TOTAL P&L: Rs.{grand_total:,.0f}")
    if all_trades:
        combined = pd.concat(all_trades, ignore_index=True)
        n_total = len(combined)
        n_wins = int((combined["pnl"] > 0).sum())
        print(f"  Total trades: {n_total} | Win rate: {n_wins}/{n_total} "
              f"({n_wins/n_total*100:.0f}%)")
        print(f"  Avg win: Rs.{combined[combined['pnl']>0]['pnl'].mean():,.0f} | "
              f"Avg loss: Rs.{combined[combined['pnl']<0]['pnl'].mean():,.0f}")
        # Exit reason breakdown
        print(f"\n  Exit reasons:")
        for reason, grp in combined.groupby("reason"):
            cnt = len(grp)
            pnl_sum = grp["pnl"].sum()
            print(f"    {reason:<12}: {cnt:>3} trades, P&L Rs.{pnl_sum:>,.0f}")
    print("=" * 72)


if __name__ == "__main__":
    main()
