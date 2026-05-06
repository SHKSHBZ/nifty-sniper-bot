"""Download SENSEX historical option data for the 104 expiry dates
in reports/sensex_expiry_calendar.csv.

Two-phase pipeline:

  Phase 1: bulk-download SENSEX index 1-min OHLCV for the full
           window (2024-05-05 -> today) into one CSV at
           data/SENSEX_INDEX_1minute.csv. Skip if already present.

  Phase 2: for each expiry, determine ATM (=spot at 14:50 rounded
           to 100), list expired option contracts via Upstox Plus
           API, filter to SENSEX + strikes in ATM±500 (11 strikes
           total = 22 instruments), download each instrument's 1-min
           candles for the expiry day only, save under data/ as
           SENSEX_<strike>_<CE|PE>_<DD_MMM_YY>_1min.csv to mirror
           the NIFTY filename convention.

Resumable: skips files that already exist. Safe to re-run after
network hiccups.

Usage:
    python -m backtesting.sensex_historical_downloader

Requires:
    - Upstox Plus access (uses /v2/expired-instruments/* endpoints)
    - Valid session in state/upstox_session.json
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from upstox_auth import UpstoxAuth


DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")
SENSEX_SPOT_FILE = DATA_DIR / "SENSEX_INDEX_1minute.csv"
CALENDAR_CSV = REPORTS_DIR / "sensex_expiry_calendar.csv"

# Upstox endpoints
SPOT_URL = "https://api.upstox.com/v2/historical-candle/{key}/{interval}/{to}/{frm}"
EXPIRED_CONTRACTS_URL = (
    "https://api.upstox.com/v2/expired-instruments/option/contract"
    "?instrument_key={key}&expiry_date={expiry}"
)
EXPIRED_CANDLE_URL = (
    "https://api.upstox.com/v2/expired-instruments/historical-candle"
    "/{key}/{interval}/{to}/{frm}"
)
SENSEX_KEY = "BSE_INDEX|SENSEX"

# Tunables
STRIKE_STEP = 100
STRIKES_EITHER_SIDE = 10   # 21 strikes total (ATM and ±10) — wider chain
                           # for premium-richness analysis. Was 5.
SPOT_BULK_CHUNK_DAYS = 30  # historical-candle 1-min limit per call
SLEEP_SECONDS = 0.5
ENTRY_HHMM = "14:50"

MONTHS = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
          7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sensex-dl")


# ---------- Phase 1: SENSEX spot ----------

def download_sensex_spot(headers: dict, start: date, end: date) -> pd.DataFrame:
    if SENSEX_SPOT_FILE.exists():
        log.info(f"SPOT: cache hit at {SENSEX_SPOT_FILE} — skipping bulk download")
        return pd.read_csv(SENSEX_SPOT_FILE)

    log.info(f"SPOT: bulk downloading SENSEX 1-min from {start} to {end}")
    chunks: list[pd.DataFrame] = []
    cur_to = end
    while cur_to >= start:
        cur_from = max(start, cur_to - timedelta(days=SPOT_BULK_CHUNK_DAYS - 1))
        url = SPOT_URL.format(key=SENSEX_KEY, interval="1minute",
                              to=cur_to.isoformat(), frm=cur_from.isoformat())
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                log.warning(f"SPOT: {cur_from}..{cur_to} HTTP {resp.status_code} {resp.text[:80]}")
            else:
                candles = resp.json().get("data", {}).get("candles", [])
                if candles:
                    df = pd.DataFrame(
                        candles,
                        columns=["timestamp", "open", "high", "low",
                                 "close", "volume", "open_interest"],
                    )
                    chunks.append(df)
                    log.info(f"SPOT: {cur_from}..{cur_to}  {len(df)} rows")
        except Exception as e:
            log.error(f"SPOT: {cur_from}..{cur_to} error {e}")
        cur_to = cur_from - timedelta(days=1)
        time.sleep(SLEEP_SECONDS)

    if not chunks:
        log.error("SPOT: no data downloaded")
        return pd.DataFrame()

    full = pd.concat(chunks, ignore_index=True)
    full["timestamp"] = pd.to_datetime(full["timestamp"])
    full = full.drop_duplicates(subset="timestamp").sort_values("timestamp")
    DATA_DIR.mkdir(exist_ok=True)
    full.to_csv(SENSEX_SPOT_FILE, index=False)
    log.info(f"SPOT: wrote {len(full)} rows -> {SENSEX_SPOT_FILE}")
    return full


# ---------- Phase 2 helpers ----------

def expiry_token(d: date) -> str:
    """Match NIFTY filename style: 30_MAR_26."""
    return f"{d.day:02d}_{MONTHS[d.month]}_{d.year % 100:02d}"


def get_atm_for(spot_df: pd.DataFrame, exp: date) -> Optional[int]:
    """Spot at 14:50 IST on expiry day rounded to nearest 100.
    Falls back to last 14:xx tick or last available bar that day."""
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
        key=SENSEX_KEY, expiry=expiry.isoformat()
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


# ---------- Main ----------

def main():
    auth = UpstoxAuth()
    if not auth.is_session_valid():
        log.error("Upstox session invalid. Authenticate via dashboard first.")
        return

    headers = {
        "Authorization": f"Bearer {auth.get_access_token()}",
        "Accept": "application/json",
    }

    if not CALENDAR_CSV.exists():
        log.error(f"{CALENDAR_CSV} missing — run sensex_expiry_calendar first")
        return
    cal = pd.read_csv(CALENDAR_CSV)
    cal["expiry_date"] = pd.to_datetime(cal["expiry_date"]).dt.date
    expiries = sorted(cal["expiry_date"].unique())
    log.info(f"Loaded {len(expiries)} expiries from calendar")

    # Phase 1: bulk SENSEX spot
    spot_df = download_sensex_spot(headers, expiries[0], expiries[-1])
    if spot_df.empty:
        log.error("Cannot proceed without spot data")
        return

    # Phase 2: per-expiry option chains
    DATA_DIR.mkdir(exist_ok=True)
    n_done = n_skip = n_fail = 0
    for exp in expiries:
        token = expiry_token(exp)
        atm = get_atm_for(spot_df, exp)
        if atm is None:
            log.warning(f"{exp} ({token}): no spot data — skipping expiry")
            n_fail += 1
            continue

        target_strikes = {
            atm + i * STRIKE_STEP
            for i in range(-STRIKES_EITHER_SIDE, STRIKES_EITHER_SIDE + 1)
        }
        contracts = get_expired_contracts(headers, exp)
        time.sleep(SLEEP_SECONDS)
        if not contracts:
            log.warning(f"{exp} ({token}): zero contracts returned — skipping")
            n_fail += 1
            continue

        wanted = [
            c for c in contracts
            if c.get("name") == "SENSEX"
            and int(c.get("strike_price", 0)) in target_strikes
        ]
        log.info(f"{exp} ({token}): atm={atm}, "
                 f"{len(wanted)}/{len(contracts)} contracts in ±{STRIKES_EITHER_SIDE} band")

        for c in wanted:
            strike = int(c["strike_price"])
            opt = c["instrument_type"]   # CE or PE
            ikey = c["instrument_key"]
            fname = DATA_DIR / f"SENSEX_{strike}_{opt}_{token}_1min.csv"
            if fname.exists():
                n_skip += 1
                continue
            df = download_candle(headers, ikey, exp, exp)
            time.sleep(SLEEP_SECONDS)
            if df is None or df.empty:
                log.debug(f"  {fname.name}: empty")
                continue
            df.to_csv(fname, index=False)
            n_done += 1
            if n_done % 25 == 0:
                log.info(f"  progress: {n_done} downloaded, {n_skip} cached, {n_fail} expiry-failed")

    log.info(f"DONE. downloaded={n_done}, cached={n_skip}, expiry-failed={n_fail}")


if __name__ == "__main__":
    main()
