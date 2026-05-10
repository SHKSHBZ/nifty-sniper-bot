"""Download NIFTY monthly futures 1-min OHLCV from Upstox.

Discovers the last ~24 monthly NIFTY futures expiries, fetches each
contract's full life-cycle 1-min OHLCV via the Upstox Plus
/v2/expired-instruments/* endpoints, and saves one CSV per contract:

  data/NIFTY_FUT_<DD_MMM_YY>_1min.csv

The companion script `nifty_futures_continuous.py` then stitches
these into a single continuous front-month series with volume-based
roll, plus exports a per-minute volume-only file we can join to the
existing spot data.

Resumable: skips files already on disk.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from upstox_auth import UpstoxAuth


DATA_DIR = Path("data")
NIFTY_KEY = "NSE_INDEX|Nifty 50"

# Upstox endpoints (Plus subscription required for historical expired-fut data)
EXPIRED_FUT_CONTRACT_URL = (
    "https://api.upstox.com/v2/expired-instruments/future/contract"
    "?instrument_key={key}&expiry_date={expiry}"
)
EXPIRED_CANDLE_URL = (
    "https://api.upstox.com/v2/expired-instruments/historical-candle"
    "/{key}/{interval}/{to}/{frm}"
)

SLEEP_SECONDS = 1.0
LOOKBACK_PER_CONTRACT_DAYS = 90  # contracts trade for 60-90 days before expiry
WINDOW_MONTHS = 24


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("nifty-fut-dl")


MONTHS = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
          7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}


def last_thursday_of(year: int, month: int) -> date:
    """NIFTY monthly futures expire on the last Thursday of each month."""
    # Start from end of month, walk backward to find Thursday
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    last_day = first_next - timedelta(days=1)
    while last_day.weekday() != 3:  # 3 = Thursday
        last_day -= timedelta(days=1)
    return last_day


def generate_recent_expiries(months_back: int = WINDOW_MONTHS) -> list[date]:
    """Last `months_back` monthly NIFTY futures expiries (last Thu of month)."""
    today = date.today()
    out = []
    for offset in range(months_back, -1, -1):
        target_month = today.month - offset
        target_year = today.year
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        out.append(last_thursday_of(target_year, target_month))
    # If today is past the current-month expiry, that contract is already
    # expired and we want it. Otherwise we don't (it's not yet expired).
    out = [d for d in out if d < today]
    return sorted(out)


def expiry_token(d: date) -> str:
    return f"{d.day:02d}_{MONTHS[d.month]}_{d.year % 100:02d}"


def get_futures_contract(headers: dict, expiry: date) -> Optional[dict]:
    """Find the NIFTY futures contract for given expiry."""
    url = EXPIRED_FUT_CONTRACT_URL.format(
        key=NIFTY_KEY, expiry=expiry.isoformat()
    )
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        log.warning(f"  contract {expiry}: HTTP {resp.status_code} {resp.text[:100]}")
        return None
    contracts = resp.json().get("data", [])
    # Filter to NIFTY futures
    for c in contracts:
        if c.get("name") == "NIFTY":
            return c
    return None


def download_candles(headers: dict, instrument_key: str,
                     frm: date, to: date) -> Optional[pd.DataFrame]:
    """Upstox limit: ~90 days for 1-min candles. Single call should suffice."""
    url = EXPIRED_CANDLE_URL.format(
        key=instrument_key, interval="1minute",
        to=to.isoformat(), frm=frm.isoformat()
    )
    resp = requests.get(url, headers=headers, timeout=20)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=WINDOW_MONTHS,
                        help="How many monthly contracts back to fetch "
                             "(default %(default)d). Upstox typically retains "
                             "~18 months of expired-instrument data.")
    args = parser.parse_args()

    auth = UpstoxAuth()
    if not auth.is_session_valid():
        log.error("Upstox session invalid. Authenticate via dashboard first.")
        return
    headers = {
        "Authorization": f"Bearer {auth.get_access_token()}",
        "Accept": "application/json",
    }

    expiries = generate_recent_expiries(months_back=args.months)
    log.info(f"Generated {len(expiries)} monthly expiries: "
             f"{expiries[0]} → {expiries[-1]}")

    DATA_DIR.mkdir(exist_ok=True)
    n_done = n_skip = n_fail = 0
    for exp in expiries:
        tok = expiry_token(exp)
        fname = DATA_DIR / f"NIFTY_FUT_{tok}_1min.csv"
        if fname.exists():
            n_skip += 1
            continue

        contract = get_futures_contract(headers, exp)
        time.sleep(SLEEP_SECONDS)
        if contract is None:
            log.warning(f"{exp} ({tok}): no contract returned")
            n_fail += 1
            continue
        ikey = contract["instrument_key"]
        sym = contract.get("trading_symbol", "?")
        log.info(f"{exp} ({tok}): {sym} → {ikey}")

        # Download from (expiry - LOOKBACK days) to expiry
        frm = exp - timedelta(days=LOOKBACK_PER_CONTRACT_DAYS)
        df = download_candles(headers, ikey, frm, exp)
        time.sleep(SLEEP_SECONDS)
        if df is None or df.empty:
            log.warning(f"  {tok}: no candles")
            n_fail += 1
            continue
        df.to_csv(fname, index=False)
        n_done += 1
        log.info(f"  {tok}: wrote {len(df)} bars → {fname.name}")

    log.info(f"DONE. downloaded={n_done}, cached={n_skip}, failed={n_fail}")


if __name__ == "__main__":
    main()
