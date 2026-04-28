"""
bulk_download.py
================
Bulk downloader for 1 year of Nifty option chain data via Upstox's
expired-instruments API (available on Plus plan).

Loops through every weekly expiry in the requested date range, fetches
the contract list for each expiry, filters to ATM ± N strikes, downloads
1-minute candles for every surviving contract, saves each as a CSV that
matches the existing naming convention used by the backtest harnesses.

Safeguards:
  - Skip files already on disk (resumable)
  - 0.5 s sleep between calls; exponential-backoff retry on 429 / 5xx
  - Per-expiry progress + ETA
  - --dry-run prints the full plan without hitting the API

Typical run:
    python backtesting/bulk_download.py --months 12 --strike-halfwidth 10

Defaults: 1 year back, Nifty only, weekly Thursdays, ATM ± 10 strikes.

Auth: reads access token from state/upstox_session.json (either a raw
'eyJ...' string or JSON with an 'access_token' key).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "state" / "upstox_session.json"
DATA_DIR = ROOT / "data"
SPOT_FILE = DATA_DIR / "NIFTY50_INDEX_1minute.csv"

# Upstox Plus endpoints
EXPIRED_OPTION_CONTRACT_URL = "https://api.upstox.com/v2/expired-instruments/option/contract"
EXPIRED_HISTORICAL_URL = "https://api.upstox.com/v2/expired-instruments/historical-candle"

# Month code map (matches existing file-naming convention e.g. 30_MAR_26)
MON_CODE = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
            "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bulk_download")


# ------------------------- Auth -------------------------

def load_access_token() -> str:
    if not TOKEN_FILE.exists():
        raise SystemExit(
            f"token file not found: {TOKEN_FILE}\n"
            "Create it with either a raw 'eyJ...' string, "
            'or JSON: {"access_token": "eyJ..."}'
        )
    raw = TOKEN_FILE.read_text().strip()
    if raw.startswith("eyJ"):
        return raw
    try:
        data = json.loads(raw)
        tok = data.get("access_token") if isinstance(data, dict) else None
        if tok:
            return tok
    except json.JSONDecodeError:
        pass
    raise SystemExit(
        "Token file is neither a raw 'eyJ...' token nor valid JSON with access_token."
    )


# ------------------------- Expiry generation -------------------------

def weekly_expiry_candidates(from_date: date, to_date: date) -> list[date]:
    """
    Return candidate dates for Nifty weekly expiry.

    Standard Nifty weekly expiry day is **Tuesday** (post-2024 SEBI changes).
    On weeks where Tuesday is a public holiday, expiry shifts to Monday
    (preceding) or Wednesday (following).

    Strategy: emit Tuesday first, then Monday and Wednesday of the same
    week as fallbacks. The caller queries the contract-list API for each
    candidate and uses whichever returns a non-empty response — the API
    is the source of truth, we just need to enumerate plausible dates.
    """
    out: list[date] = []
    # Find the Monday on/after from_date
    d = from_date
    while d.weekday() != 0:  # Mon=0
        d += timedelta(days=1)
    while d <= to_date:
        for offset in (1, 0, 2):  # Tue, Mon, Wed (in priority order)
            candidate = d + timedelta(days=offset)
            if from_date <= candidate <= to_date:
                out.append(candidate)
        d += timedelta(days=7)
    return out


def fmt_expiry_for_filename(d: date) -> str:
    """Return '30_MAR_26' format used in existing data filenames."""
    return f"{d.day:02d}_{MON_CODE[d.month - 1]}_{d.year % 100:02d}"


# ------------------------- Spot price lookup -------------------------

def load_spot_close_at(reference_date: date) -> Optional[float]:
    """
    Return the close price of NIFTY 50 on the most recent trading day at or
    before `reference_date`. Used to centre the ATM strike grid.
    """
    if not SPOT_FILE.exists():
        return None
    df = pd.read_csv(SPOT_FILE)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df[df["ts"].dt.date <= reference_date]
    if df.empty:
        return None
    return float(df.iloc[-1]["close"])


# ------------------------- API calls -------------------------

def _request_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = 20,
    max_retries: int = 4,
) -> Optional[requests.Response]:
    for attempt in range(max_retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            wait = 2 ** (attempt + 1)
            log.warning("network error: %s — retrying in %ds", e, wait)
            time.sleep(wait)
            continue

        if r.status_code == 429:
            wait = 2 ** (attempt + 1)
            log.warning("429 rate-limited; backing off %ds", wait)
            time.sleep(wait)
            continue
        if r.status_code == 401:
            raise SystemExit("401 Unauthorized — token invalid/expired")
        if r.status_code in (502, 503, 504):
            wait = 2 ** (attempt + 1)
            log.warning("upstream %d; retry in %ds", r.status_code, wait)
            time.sleep(wait)
            continue
        return r
    return None


def fetch_contract_list(
    session: requests.Session,
    underlying_key: str,
    expiry: date,
) -> list[dict]:
    r = _request_with_retry(
        session,
        EXPIRED_OPTION_CONTRACT_URL,
        params={"instrument_key": underlying_key,
                "expiry_date": expiry.isoformat()},
    )
    if r is None or r.status_code != 200:
        code = r.status_code if r is not None else "?"
        log.info("  no contracts for %s (HTTP %s)", expiry, code)
        return []
    return r.json().get("data", []) or []


def fetch_candles_for_contract(
    session: requests.Session,
    instrument_key: str,
    from_d: date,
    to_d: date,
) -> Optional[pd.DataFrame]:
    url = (
        f"{EXPIRED_HISTORICAL_URL}/{instrument_key}/1minute/"
        f"{to_d.isoformat()}/{from_d.isoformat()}"
    )
    r = _request_with_retry(session, url)
    if r is None or r.status_code != 200:
        return None
    candles = r.json().get("data", {}).get("candles", [])
    if not candles:
        return None
    df = pd.DataFrame(candles, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "open_interest"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ------------------------- Core loop -------------------------

@dataclass
class Plan:
    expiry: date
    atm: int
    min_strike: int
    max_strike: int


def build_plan(
    from_date: date,
    to_date: date,
    *,
    strike_halfwidth: int,
    strike_step: int,
) -> list[Plan]:
    plans: list[Plan] = []
    candidates = weekly_expiry_candidates(from_date, to_date)
    log.info("Generated %d expiry candidates (Tue/Mon/Wed for each week) "
             "between %s and %s. The contract-list API decides which "
             "candidates are real expiries.", len(candidates), from_date, to_date)

    for exp in candidates:
        # Center strike grid on spot at expiry-week start
        ref = exp - timedelta(days=6)
        spot = load_spot_close_at(ref)
        if spot is None:
            # If no spot yet, we'll discover strikes from API later.
            plans.append(Plan(expiry=exp, atm=0, min_strike=0, max_strike=0))
            continue
        atm = int(round(spot / strike_step) * strike_step)
        plans.append(Plan(
            expiry=exp,
            atm=atm,
            min_strike=atm - strike_halfwidth * strike_step,
            max_strike=atm + strike_halfwidth * strike_step,
        ))
    return plans


def file_for_contract(symbol: str, strike: int, side: str, expiry: date) -> Path:
    return DATA_DIR / f"NIFTY_{strike}_{side}_{fmt_expiry_for_filename(expiry)}_1min.csv"


def run(
    *,
    from_date: date,
    to_date: date,
    strike_halfwidth: int,
    strike_step: int,
    dry_run: bool,
) -> None:
    plans = build_plan(
        from_date, to_date,
        strike_halfwidth=strike_halfwidth,
        strike_step=strike_step,
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if dry_run:
        log.info("DRY RUN — listing the plan only")
        for p in plans:
            log.info("  %s  ATM=%s  strikes [%s..%s]",
                     p.expiry, p.atm, p.min_strike, p.max_strike)
        log.info("Done. Plan spans %d expiries.", len(plans))
        return

    token = load_access_token()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })

    total_written = 0
    total_skipped = 0
    total_missing = 0
    t_start = time.time()
    # Track Monday-of-week for weeks we've already found an expiry for —
    # avoids re-trying Mon/Wed when Tue already succeeded.
    weeks_with_expiry_found: set[date] = set()

    for i, p in enumerate(plans, 1):
        # Monday-of-week for this candidate
        monday = p.expiry - timedelta(days=p.expiry.weekday())
        if monday in weeks_with_expiry_found:
            continue

        log.info("[%d/%d] === Expiry candidate %s (%s, ATM~%s) ===",
                 i, len(plans), p.expiry,
                 ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][p.expiry.weekday()],
                 p.atm)

        contracts = fetch_contract_list(session, "NSE_INDEX|Nifty 50", p.expiry)
        time.sleep(0.5)
        if not contracts:
            continue
        weeks_with_expiry_found.add(monday)

        # Filter to our strike window + NIFTY only
        if p.min_strike > 0:
            contracts = [
                c for c in contracts
                if c.get("name") == "NIFTY"
                and p.min_strike <= int(c.get("strike_price", 0)) <= p.max_strike
            ]
        else:
            # no spot anchor — take the whole list but trim to CE/PE of NIFTY
            contracts = [c for c in contracts if c.get("name") == "NIFTY"]

        log.info("  %d contracts in strike window", len(contracts))

        # Download within the expiry's trading week
        # Upstox returns weekly-expiry option candles for their full trading
        # life, so a wide range is safe; we clamp to 1 year prior at most.
        window_from = max(p.expiry - timedelta(days=14), from_date)
        window_to = min(p.expiry, to_date)

        for c in contracts:
            strike = int(c["strike_price"])
            side = c["instrument_type"].upper()   # CE / PE
            if side not in ("CE", "PE"):
                continue
            out_path = file_for_contract(c.get("trading_symbol", ""),
                                         strike, side, p.expiry)

            if out_path.exists():
                total_skipped += 1
                continue

            df = fetch_candles_for_contract(
                session, c["instrument_key"], window_from, window_to,
            )
            time.sleep(0.5)

            if df is None or df.empty:
                total_missing += 1
                continue

            df.to_csv(out_path, index=False)
            total_written += 1

            # Progress log every 10 writes
            if total_written % 10 == 0:
                elapsed = time.time() - t_start
                done = total_written + total_skipped + total_missing
                log.info("  progress: wrote=%d skipped=%d missing=%d  elapsed=%.0fs",
                         total_written, total_skipped, total_missing, elapsed)

    elapsed = time.time() - t_start
    log.info("---------------------------------------------------------------")
    log.info("DONE  wrote=%d  skipped(existing)=%d  missing(empty)=%d  time=%.0fs",
             total_written, total_skipped, total_missing, elapsed)
    log.info("Commit the new files in data/ via GitHub Desktop, then push.")


# ------------------------- CLI -------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="from_date", help="YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="to_date", help="YYYY-MM-DD (inclusive, default today)")
    p.add_argument("--months", type=int, default=12,
                   help="If --from omitted, fetch the last N months (default 12).")
    p.add_argument("--strike-halfwidth", type=int, default=10,
                   help="Download ATM +/- N strikes per expiry (default 10).")
    p.add_argument("--strike-step", type=int, default=50,
                   help="Strike step (default 50 for Nifty).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan without hitting the API.")
    args = p.parse_args()

    to_d = (datetime.fromisoformat(args.to_date).date()
            if args.to_date else date.today())
    from_d = (datetime.fromisoformat(args.from_date).date()
              if args.from_date else to_d - timedelta(days=30 * args.months))

    if from_d > to_d:
        raise SystemExit("--from is after --to")

    log.info("Range: %s -> %s (%d days)", from_d, to_d, (to_d - from_d).days)
    log.info("Strike grid: ATM +/- %d x %d pts",
             args.strike_halfwidth, args.strike_step)

    run(
        from_date=from_d,
        to_date=to_d,
        strike_halfwidth=args.strike_halfwidth,
        strike_step=args.strike_step,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
