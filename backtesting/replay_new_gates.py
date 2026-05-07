"""Replay the new tightened-CE / loosened-PE gate logic against the
last 2 days of focus_zone tick data. Only ATM strike entries.
SL 30% / TP 50% / market-close exit.
"""
import pandas as pd

PCR_CE_LOW, PCR_CE_HIGH = 1.00, 1.30
PCR_PE_LOW, PCR_PE_HIGH = 0.50, 0.95
PROXIMITY_PCT = 0.0015
SUSTAIN_TICKS = 3
SL_PCT = 0.30
TP_PCT = 0.50
BROKERAGE = 60.0


def simulate_day(csv_path, lot_size, label):
    df = pd.read_csv(csv_path)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("ts").reset_index(drop=True)

    # CSVs were written by a bot running on a UAE-timezone server (UTC+4).
    # India is UTC+5:30, so add 1:30 to get IST.
    # Detect via last timestamp: IST market closes 15:30, so if last hour
    # in file is <= 14, it's UAE-stamped (UAE close hour ≈ 13:30-14:00).
    if not df.empty and df["ts"].iloc[-1].hour <= 14:
        df["ts"] = df["ts"] + pd.Timedelta(hours=1, minutes=30)

    minutes = sorted(df["ts"].unique())
    snaps = {}
    for ts, g in df.groupby("ts"):
        spot = g["spot"].iloc[0]
        ce_oi = g["ce_oi"].sum()
        pe_oi = g["pe_oi"].sum()
        pcr = pe_oi / ce_oi if ce_oi > 0 else 0
        below = g[g["strike"] < spot]
        above = g[g["strike"] > spot]
        sup = int(below.loc[below["pe_oi"].idxmax(), "strike"]) if len(below) else None
        res = int(above.loc[above["ce_oi"].idxmax(), "strike"]) if len(above) else None
        atm_row = g.loc[(g["strike"] - spot).abs().idxmin()]
        snaps[ts] = {
            "spot": spot,
            "pcr": pcr,
            "support": sup,
            "resistance": res,
            "atm_strike": int(atm_row["strike"]),
            "atm_ce_ltp": atm_row["ce_ltp"],
            "atm_pe_ltp": atm_row["pe_ltp"],
            "group": g.set_index("strike")[["ce_ltp", "pe_ltp"]],
        }

    def near(level, spot):
        return level and abs(spot - level) / level <= PROXIMITY_PCT

    def sustained(level, hist):
        if level is None or len(hist) < SUSTAIN_TICKS:
            return False
        for _, s in hist[-SUSTAIN_TICKS:]:
            if not near(level, s):
                return False
        return True

    trades = []
    pos = None
    spot_hist = []
    last_5min = None

    for ts in minutes:
        snap = snaps[ts]
        spot = snap["spot"]
        if last_5min is None or (ts - last_5min).total_seconds() >= 300:
            spot_hist.append((ts, spot))
            last_5min = ts
            if len(spot_hist) > 10:
                spot_hist.pop(0)

        if pos:
            strike = pos["strike"]
            if strike in snap["group"].index:
                ltp = snap["group"].at[strike, "ce_ltp" if pos["opt"] == "CE" else "pe_ltp"]
                exit_p, reason = None, None
                if ltp >= pos["tp"]:
                    exit_p, reason = ltp, "TP"
                elif ltp <= pos["sl"]:
                    exit_p, reason = ltp, "SL"
                elif ts.time() >= pd.Timestamp("15:30").time():
                    exit_p, reason = ltp, "EOD"
                if exit_p is not None:
                    pnl = (exit_p - pos["entry_p"]) * lot_size - BROKERAGE
                    trades.append({
                        "index": label, "opt": pos["opt"], "strike": strike,
                        "entry_t": pos["entry_t"].strftime("%H:%M"),
                        "exit_t": ts.strftime("%H:%M"),
                        "entry_p": round(pos["entry_p"], 2),
                        "exit_p": round(exit_p, 2),
                        "pcr": round(pos["pcr"], 2),
                        "pnl": round(pnl, 0),
                        "reason": reason,
                    })
                    pos = None

        # IST market entry window: 10:00 - 15:30
        time_ok = (ts.time() >= pd.Timestamp("10:00").time() and
                   ts.time() < pd.Timestamp("15:30").time())
        if pos is None and time_ok:
            sup = snap["support"]
            res = snap["resistance"]
            if near(sup, spot) and sustained(sup, spot_hist):
                if PCR_CE_LOW <= snap["pcr"] <= PCR_CE_HIGH and snap["atm_ce_ltp"] > 0:
                    ltp = snap["atm_ce_ltp"]
                    pos = {
                        "opt": "CE", "strike": snap["atm_strike"],
                        "entry_p": ltp, "entry_t": ts,
                        "sl": ltp * (1 - SL_PCT), "tp": ltp * (1 + TP_PCT),
                        "pcr": snap["pcr"],
                    }
            elif near(res, spot) and sustained(res, spot_hist):
                if PCR_PE_LOW <= snap["pcr"] <= PCR_PE_HIGH and snap["atm_pe_ltp"] > 0:
                    ltp = snap["atm_pe_ltp"]
                    pos = {
                        "opt": "PE", "strike": snap["atm_strike"],
                        "entry_p": ltp, "entry_t": ts,
                        "sl": ltp * (1 - SL_PCT), "tp": ltp * (1 + TP_PCT),
                        "pcr": snap["pcr"],
                    }

    if pos:
        last_ts = minutes[-1]
        snap = snaps[last_ts]
        strike = pos["strike"]
        if strike in snap["group"].index:
            ltp = snap["group"].at[strike, "ce_ltp" if pos["opt"] == "CE" else "pe_ltp"]
            pnl = (ltp - pos["entry_p"]) * lot_size - BROKERAGE
            trades.append({
                "index": label, "opt": pos["opt"], "strike": strike,
                "entry_t": pos["entry_t"].strftime("%H:%M"),
                "exit_t": last_ts.strftime("%H:%M"),
                "entry_p": round(pos["entry_p"], 2),
                "exit_p": round(ltp, 2),
                "pcr": round(pos["pcr"], 2),
                "pnl": round(pnl, 0),
                "reason": "TIMEOUT",
            })

    return pd.DataFrame(trades)


def main():
    print("=" * 72)
    print("  NEW BOT LOGIC REPLAY — last 2 days")
    print(f"  Gate 2 BAND mode: CE [{PCR_CE_LOW:.2f}-{PCR_CE_HIGH:.2f}] | "
          f"PE [{PCR_PE_LOW:.2f}-{PCR_PE_HIGH:.2f}]")
    print("=" * 72)
    total = 0
    for date in ("2026-05-05", "2026-05-06", "2026-05-07"):
        print(f"\n--- {date} ---")
        for idx, lot in (("nifty", 65), ("sensex", 20)):
            path = f"logs/focus_zone_{idx}_{date}.csv"
            try:
                t = simulate_day(path, lot, idx.upper())
                n = len(t)
                pnl = float(t["pnl"].sum()) if n else 0.0
                wins = int((t["pnl"] > 0).sum()) if n else 0
                print(f"\n  {idx.upper()} (lot {lot}): {n} trades, {wins} winners, P&L Rs.{pnl:,.0f}")
                for _, r in t.iterrows():
                    print(f"    {r['entry_t']} -> {r['exit_t']}  BUY {r['strike']} {r['opt']:>2}  "
                          f"Rs.{r['entry_p']:>7.2f} -> Rs.{r['exit_p']:>7.2f}  "
                          f"PCR={r['pcr']:.2f}  {r['reason']:<7}  Rs.{r['pnl']:+,.0f}")
                total += pnl
            except Exception as e:
                print(f"  {idx.upper()}: ERR {e}")
    print("\n" + "=" * 72)
    print(f"  2-DAY TOTAL with NEW gates: Rs.{total:,.0f}")
    print("=" * 72)


if __name__ == "__main__":
    main()
