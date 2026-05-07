"""Download NIFTY historical option data with wider strike range.

Mirrors backtesting/sensex_historical_downloader.py but for NIFTY.
Default ATM ±10 strikes (21 total) to enable premium-richness analysis.

Usage:
    python -m backtesting.nifty_historical_downloader

Requires Upstox Plus access (uses /v2/expired-instruments/* endpoints).
Reads expiry dates from focus_zone_nifty_2026-05-05.csv if available, else
discovers from existing data/NIFTY_*.csv filenames. For a fresh window,
edit EXPIRIES_TO_FETCH below.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from upstox_auth import UpstoxAuth


DATA_DIR = Path("data")
NIFTY_SPOT_FILE = DATA_DIR / "NIFTY50_INDEX_1minute.csv"

EXPIRED_CONTRACTS_URL = (
    "https://api.upstox.com/v2/expired-instruments/option/contract"
    "?instrument_key={key}&expiry_date={expiry}"
)
EXPIRED_CANDLE_URL = (
    "https://api.upstox.com/v2/expired-instruments/historical-candle"
    "/{key}/{interval}/{to}/{frm}"
)
NIFTY_KEY = "NSE_INDEX|Nifty 50"

STRIKE_STEP = 50
STRIKES_EITHER_SIDE = 10   # 21 strikes total
SLEEP_SECONDS = 0.5
ENTRY_HHMM = "14:50"

MONTHS = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
          7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}

OPT_NAME_RE = re.compile(
    r"^NIFTY_(\d+)_(CE|PE)_(\d{1,2}_[A-Z]{3}_\d{2})_1min\.csv$"
)


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("nifty-dl")


def expiry_token(d: date) -> str:
    return f"{d.day:02d}_{MONTHS[d.month]}_{d.year % 100:02d}"


def discover_expiries_from_existing() -> list[date]:
    """Reverse-engineer expiry dates from existing data/NIFTY_*.csv files."""
    seen = set()
    if not DATA_DIR.exists():
        return []
    for p in DATA_DIR.iterdir():
        m = OPT_NAME_RE.match(p.name)
        if m:
            tok = m.group(3)
            d, mon, y = tok.split("_")
            seen.add(date(2000 + int(y),
                          {v: k for k, v in MONTHS.items()}[mon],
                          int(d)))
    return sorted(seen)


def get_atm_for(spot_df: pd.DataFrame, exp: date) -> Optional[int]:
    target = pd.Timestamp(f"{exp.isoformat()} {ENTRY_HHMM}+05:30")
    sub = spot_df[
        pd.to_datetime(spot_df["timestamp"]).dt.date == exp
    ]
    if sub.empty:
        return None
    sub = sub.copy()
    sub["ts"] = pd.to_datetime(sub["timestamp"])
    exact = sub[sub["ts"] == target]
    spot = float(exact["close"].iloc[0]) if len(exact) else \
           float(sub.iloc[(sub["ts"] - target).abs().argmin()]["close"])
    return int(round(spot / STRIKE_STEP) * STRIKE_STEP)


def get_expired_contracts(headers: dict, expiry: date) -> list[dict]:
    url = EXPIRED_CONTRACTS_URL.format(
        key=NIFTY_KEY, expiry=expiry.isoformat()
    )
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        log.warning(f"  contracts {expiry}: HTTP {resp.status_code} {resp.text[:80]}")
        return []
    return resp.json().get("data", [])


def download_candle(headers: dict, instrument_key: str,
                    frm: date, to: date) -> Optional[pd.DataFrame]:
    url = EXPIRED_CANDLE_URL.format(
        key=instrument_key, interval="1minute",
        to=to.isoformat(), frm=frm.isoformat(),
    )
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        return None
    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        return None
    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low",
                 "close", "volume", "open_interest"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def main():
    auth = UpstoxAuth()
    if not auth.is_session_valid():
        log.error("Upstox session invalid. Authenticate via dashboard first.")
        return
    headers = {
        "Authorization": f"Bearer {auth.get_access_token()}",
        "Accept": "application/json",
    }

    if not NIFTY_SPOT_FILE.exists():
        log.error(f"{NIFTY_SPOT_FILE} missing — need spot data first")
        return
    spot_df = pd.read_csv(NIFTY_SPOT_FILE)

    expiries = discover_expiries_from_existing()
    if not expiries:
        log.error("No existing NIFTY_*.csv files — set EXPIRIES manually")
        return
    log.info(f"Found {len(expiries)} existing expiries to widen")

    DATA_DIR.mkdir(exist_ok=True)
    n_done = n_skip = n_fail = 0
    for exp in expiries:
        tok = expiry_token(exp)
        atm = get_atm_for(spot_df, exp)
        if atm is None:
            log.warning(f"{exp} ({tok}): no spot data — skipping")
            n_fail += 1
            continue

        target_strikes = {
            atm + i * STRIKE_STEP
            for i in range(-STRIKES_EITHER_SIDE, STRIKES_EITHER_SIDE + 1)
        }
        contracts = get_expired_contracts(headers, exp)
        time.sleep(SLEEP_SECONDS)
        if not contracts:
            n_fail += 1
            continue

        wanted = [
            c for c in contracts
            if c.get("name") == "NIFTY"
            and int(c.get("strike_price", 0)) in target_strikes
        ]
        log.info(f"{exp} ({tok}): atm={atm}, "
                 f"{len(wanted)}/{len(contracts)} contracts in ±{STRIKES_EITHER_SIDE} band")

        for c in wanted:
            strike = int(c["strike_price"])
            opt = c["instrument_type"]
            ikey = c["instrument_key"]
            fname = DATA_DIR / f"NIFTY_{strike}_{opt}_{tok}_1min.csv"
            if fname.exists():
                n_skip += 1
                continue
            df = download_candle(headers, ikey, exp, exp)
            time.sleep(SLEEP_SECONDS)
            if df is None or df.empty:
                continue
            df.to_csv(fname, index=False)
            n_done += 1
            if n_done % 25 == 0:
                log.info(f"  progress: {n_done} new, {n_skip} cached, {n_fail} failed")

    log.info(f"DONE. new={n_done}, cached={n_skip}, failed={n_fail}")


if __name__ == "__main__":
    main()
