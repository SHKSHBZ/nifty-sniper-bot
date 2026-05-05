"""Backtest: Expiry Gamma Hero.

Buy ATM CE/PE at 14:50 (or 15:00 if no signal) on weekly expiry day,
direction chosen by OI dominance one strike above ATM. Exit when spot
crosses back through entry-ATM strike against us, otherwise hold to
15:25 and square off at market.

Inputs   : data/NIFTY_<strike>_<CE|PE>_<DD_MMM_YY>_1min.csv
           data/NIFTY50_INDEX_1minute.csv
Outputs  : reports/expiry_gamma_hero_trades.csv  (per-trade ledger)
           reports/expiry_gamma_hero_summary.md  (aggregate stats)

Run      : python -m backtesting.expiry_gamma_hero
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------- Strategy parameters ----------
CAPITAL_PER_TRADE = 20_000.0
LOT_SIZE = 65
STRIKE_STEP = 50
OI_RATIO_THRESHOLD = 1.5
MIN_PREMIUM = 5.0
MAX_PREMIUM = 20.0
ENTRY_TIMES = [dtime(14, 50), dtime(15, 0)]
TIME_EXIT = dtime(15, 25)
SLIPPAGE_PER_LEG = 0.05  # ₹0.05 per option per leg, conservative
BROKERAGE_PER_TRADE = 60.0  # round-trip total

# ---------- Paths ----------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
SPOT_CSV = DATA_DIR / "NIFTY50_INDEX_1minute.csv"

OPT_NAME_RE = re.compile(
    r"^NIFTY_(\d+)_(CE|PE)_(\d{1,2}_[A-Z]{3}_\d{2})_1min\.csv$"
)
MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


@dataclass
class Trade:
    expiry: str
    entry_ts: str
    direction: str
    atm_strike: int
    entry_spot: float
    entry_premium: float
    qty: int
    lots: int
    exit_ts: str
    exit_premium: float
    exit_spot: float
    exit_reason: str
    gross_pnl: float
    net_pnl: float
    return_pct: float
    minutes_held: int
    ce_oi_above: int
    pe_oi_above: int
    oi_ratio: float


def parse_expiry_token(token: str) -> datetime:
    """Convert '30_MAR_26' -> datetime(2026, 3, 30)."""
    d, m, y = token.split("_")
    return datetime(2000 + int(y), MONTHS[m], int(d))


def discover_expiries() -> list[tuple[str, datetime]]:
    """Return [(token, date)] for every expiry that has CSVs in data/."""
    seen: set[str] = set()
    for p in DATA_DIR.iterdir():
        m = OPT_NAME_RE.match(p.name)
        if m:
            seen.add(m.group(3))
    return sorted(((tok, parse_expiry_token(tok)) for tok in seen),
                  key=lambda x: x[1])


def load_option(strike: int, opt_type: str, expiry_token: str) -> Optional[pd.DataFrame]:
    path = DATA_DIR / f"NIFTY_{strike}_{opt_type}_{expiry_token}_1min.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("ts")
    return df


def get_value_at(df: pd.DataFrame, ts: pd.Timestamp, col: str) -> Optional[float]:
    """Return df[col] at the bar that *contains* ts (exact-match on minute)."""
    if df is None:
        return None
    try:
        row = df.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        v = row[col]
        if pd.isna(v):
            return None
        return float(v)
    except KeyError:
        return None


def evaluate_entry(
    spot_df: pd.DataFrame,
    expiry_token: str,
    entry_ts: pd.Timestamp,
) -> Optional[dict]:
    """At entry_ts, decide if we have a valid signal. Return dict on
    fire, None on skip."""
    spot = get_value_at(spot_df, entry_ts, "close")
    if spot is None or spot <= 0:
        return None

    atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
    strike_above = atm + STRIKE_STEP

    ce_above = load_option(strike_above, "CE", expiry_token)
    pe_above = load_option(strike_above, "PE", expiry_token)
    ce_oi = get_value_at(ce_above, entry_ts, "open_interest")
    pe_oi = get_value_at(pe_above, entry_ts, "open_interest")
    if ce_oi is None or pe_oi is None or ce_oi <= 0 or pe_oi <= 0:
        return None

    if pe_oi >= OI_RATIO_THRESHOLD * ce_oi:
        direction = "CE"
        ratio = pe_oi / ce_oi
    elif ce_oi >= OI_RATIO_THRESHOLD * pe_oi:
        direction = "PE"
        ratio = ce_oi / pe_oi
    else:
        return None

    atm_opt = load_option(atm, direction, expiry_token)
    entry_premium = get_value_at(atm_opt, entry_ts, "close")
    if entry_premium is None or not (MIN_PREMIUM <= entry_premium <= MAX_PREMIUM):
        return None

    lots = int(CAPITAL_PER_TRADE // (entry_premium * LOT_SIZE))
    if lots < 1:
        return None

    return {
        "atm": atm,
        "spot": spot,
        "direction": direction,
        "entry_premium": entry_premium,
        "lots": lots,
        "qty": lots * LOT_SIZE,
        "ce_oi_above": int(ce_oi),
        "pe_oi_above": int(pe_oi),
        "oi_ratio": round(ratio, 2),
        "atm_opt_df": atm_opt,
    }


def simulate_exit(
    spot_df: pd.DataFrame,
    atm_opt_df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    atm: int,
    direction: str,
) -> tuple[pd.Timestamp, float, float, str]:
    """Walk minute-by-minute from entry+1 to 15:25. Return
    (exit_ts, exit_premium, exit_spot, reason)."""
    expiry_date = entry_ts.date()
    deadline = pd.Timestamp.combine(expiry_date, TIME_EXIT).tz_localize(entry_ts.tz)

    cur = entry_ts + pd.Timedelta(minutes=1)
    while cur <= deadline:
        spot = get_value_at(spot_df, cur, "close")
        prem = get_value_at(atm_opt_df, cur, "close")
        if spot is not None:
            if direction == "CE" and spot <= atm:
                if prem is None:
                    prem = 0.0
                return cur, prem, spot, "ADVERSE_SPOT"
            if direction == "PE" and spot >= atm:
                if prem is None:
                    prem = 0.0
                return cur, prem, spot, "ADVERSE_SPOT"
        cur += pd.Timedelta(minutes=1)

    # Time exit at 15:25
    spot = get_value_at(spot_df, deadline, "close") or 0.0
    prem = get_value_at(atm_opt_df, deadline, "close")
    if prem is None:
        # Last available bar before deadline
        sub = atm_opt_df[atm_opt_df.index <= deadline]
        prem = float(sub["close"].iloc[-1]) if len(sub) else 0.0
    return deadline, prem, spot, "TIME_EXIT"


def run_one_expiry(expiry_token: str, expiry_date: datetime,
                   spot_df: pd.DataFrame) -> Optional[Trade]:
    tz = spot_df.index.tz
    for entry_t in ENTRY_TIMES:
        entry_ts = pd.Timestamp.combine(expiry_date.date(), entry_t).tz_localize(tz)
        sig = evaluate_entry(spot_df, expiry_token, entry_ts)
        if sig is None:
            continue

        exit_ts, exit_prem, exit_spot, reason = simulate_exit(
            spot_df, sig["atm_opt_df"], entry_ts, sig["atm"], sig["direction"],
        )

        # Apply slippage to both legs (entry pays up, exit gets less)
        eff_entry = sig["entry_premium"] + SLIPPAGE_PER_LEG
        eff_exit = max(0.0, exit_prem - SLIPPAGE_PER_LEG)
        gross = (eff_exit - eff_entry) * sig["qty"]
        net = gross - BROKERAGE_PER_TRADE
        ret_pct = (eff_exit - eff_entry) / eff_entry * 100.0
        held = int((exit_ts - entry_ts).total_seconds() // 60)

        return Trade(
            expiry=expiry_token,
            entry_ts=entry_ts.isoformat(),
            direction=sig["direction"],
            atm_strike=sig["atm"],
            entry_spot=round(sig["spot"], 2),
            entry_premium=round(sig["entry_premium"], 2),
            qty=sig["qty"],
            lots=sig["lots"],
            exit_ts=exit_ts.isoformat(),
            exit_premium=round(exit_prem, 2),
            exit_spot=round(exit_spot, 2),
            exit_reason=reason,
            gross_pnl=round(gross, 2),
            net_pnl=round(net, 2),
            return_pct=round(ret_pct, 2),
            minutes_held=held,
            ce_oi_above=sig["ce_oi_above"],
            pe_oi_above=sig["pe_oi_above"],
            oi_ratio=sig["oi_ratio"],
        )
    return None


def write_summary(trades: list[Trade], skipped: list[tuple[str, str]],
                  out_path: Path) -> None:
    if not trades:
        out_path.write_text("# Expiry Gamma Hero — no trades fired\n")
        return

    df = pd.DataFrame([asdict(t) for t in trades])
    n = len(df)
    wins = (df["net_pnl"] > 0).sum()
    losses = (df["net_pnl"] < 0).sum()
    total = df["net_pnl"].sum()
    avg_pnl = df["net_pnl"].mean()
    avg_ret = df["return_pct"].mean()
    median_ret = df["return_pct"].median()
    max_win = df["net_pnl"].max()
    max_loss = df["net_pnl"].min()
    avg_held = df["minutes_held"].mean()

    cum = df["net_pnl"].cumsum()
    running_max = cum.cummax()
    drawdown = (running_max - cum)
    max_dd = drawdown.max()

    by_dir = df.groupby("direction")["net_pnl"].agg(["count", "sum", "mean"])
    by_reason = df.groupby("exit_reason")["net_pnl"].agg(["count", "sum", "mean"])

    lines = []
    lines.append("# Expiry Gamma Hero — Backtest Summary\n")
    lines.append(f"- Expiries scanned: {len(trades) + len(skipped)}")
    lines.append(f"- Trades fired:    {n}")
    lines.append(f"- Trades skipped:  {len(skipped)}")
    lines.append(f"- Capital/trade:   ₹{CAPITAL_PER_TRADE:,.0f}")
    lines.append(f"- Lot size:        {LOT_SIZE}")
    lines.append("")
    lines.append("## P&L (after ₹0.05/leg slippage and ₹60 round-trip brokerage)")
    lines.append(f"- Total net P&L:   ₹{total:,.0f}")
    lines.append(f"- Avg per trade:   ₹{avg_pnl:,.0f}")
    lines.append(f"- Avg return:      {avg_ret:.1f}%")
    lines.append(f"- Median return:   {median_ret:.1f}%")
    lines.append(f"- Best trade:      ₹{max_win:,.0f}")
    lines.append(f"- Worst trade:     ₹{max_loss:,.0f}")
    lines.append(f"- Max drawdown:    ₹{max_dd:,.0f}")
    lines.append(f"- Win rate:        {wins}/{n} = {wins/n*100:.1f}%  ({losses} losses)")
    lines.append(f"- Avg time held:   {avg_held:.0f} min")
    lines.append("")
    lines.append("## By direction")
    lines.append("```")
    lines.append(by_dir.to_string())
    lines.append("```")
    lines.append("")
    lines.append("## By exit reason")
    lines.append("```")
    lines.append(by_reason.to_string())
    lines.append("```")
    out_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    print(f"Loading spot from {SPOT_CSV}...")
    spot_df = pd.read_csv(SPOT_CSV)
    spot_df["ts"] = pd.to_datetime(spot_df["timestamp"])
    spot_df = spot_df.set_index("ts")

    expiries = discover_expiries()
    print(f"Found {len(expiries)} expiries: "
          f"{expiries[0][0]} -> {expiries[-1][0]}")

    trades: list[Trade] = []
    skipped: list[tuple[str, str]] = []

    for token, dt in expiries:
        try:
            t = run_one_expiry(token, dt, spot_df)
            if t is None:
                skipped.append((token, "no_signal_or_premium_out_of_band"))
                print(f"  {token}: SKIP")
            else:
                trades.append(t)
                tag = "WIN" if t.net_pnl > 0 else ("LOSS" if t.net_pnl < 0 else "FLAT")
                print(f"  {token}: {t.direction} @{t.atm_strike} "
                      f"prem ₹{t.entry_premium:.2f} -> ₹{t.exit_premium:.2f} "
                      f"({t.exit_reason}, {t.minutes_held}m) "
                      f"net ₹{t.net_pnl:,.0f} [{tag}]")
        except Exception as e:
            skipped.append((token, f"error: {e}"))
            print(f"  {token}: ERROR {e}")

    csv_path = REPORTS_DIR / "expiry_gamma_hero_trades.csv"
    md_path = REPORTS_DIR / "expiry_gamma_hero_summary.md"
    if trades:
        pd.DataFrame([asdict(t) for t in trades]).to_csv(csv_path, index=False)
        print(f"\nWrote {csv_path}")
    write_summary(trades, skipped, md_path)
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
