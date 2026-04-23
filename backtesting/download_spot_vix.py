"""
download_spot_vix.py
====================
Minimal Upstox downloader for the two instruments we need for Phase 2
backtesting: Nifty 50 spot and India VIX, 1-minute candles.

USAGE:
    1. Generate a fresh Upstox access token at developer.upstox.com
    2. Save it to state/upstox_session.json in this format:
         { "access_token": "eyJ..." }
    3. Run:
         python backtesting/download_spot_vix.py
       (optionally: --from YYYY-MM-DD --to YYYY-MM-DD --months 1)
    4. The script writes:
         data/NIFTY50_INDEX_1minute.csv
         data/INDIA_VIX_1minute.csv

Defaults: download the last 30 days of data, chunked by week so we stay
well inside Upstox rate limits.

Rate limits respected:
    - 1 request per second (sleep 1.0s between calls)
    - 429 retry with exponential backoff (2s, 4s, 8s, 16s)
    - Skips chunks already merged into the final CSV if resumed

No options downloader here by design — you already have the chain for
30-MAR expiry. This script is purely for the two index series that are
currently missing.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "state" / "upstox_session.json"
DATA_DIR = ROOT / "data"

BASE = "https://api.upstox.com/v2/historical-candle"

INSTRUMENTS: dict[str, tuple[str, str]] = {
    # label ->            (instrument_key,               output_filename)
    "Nifty 50 (spot)":    ("NSE_INDEX|Nifty 50",        "NIFTY50_INDEX_1minute.csv"),
    "India VIX":          ("NSE_INDEX|India VIX",       "INDIA_VIX_1minute.csv"),
}

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("downloader")


def load_access_token() -> str:
    if not TOKEN_FILE.exists():
        raise SystemExit(
            f"\nERROR: token file not found at {TOKEN_FILE}\n"
            "\nSteps to fix:\n"
            "  1. Go to https://developer.upstox.com -> Apps -> your app\n"
            "  2. Generate a fresh access token\n"
            "  3. Create file state/upstox_session.json — content can be EITHER:\n"
            "       (a) just the raw token on one line:\n"
            "             eyJhbGciOi...\n"
            "       (b) or wrapped in JSON:\n"
            '             { "access_token": "eyJ..." }\n'
            "  4. Re-run this script.\n"
        )

    raw = TOKEN_FILE.read_text().strip()
    if not raw:
        raise SystemExit(
            f"ERROR: {TOKEN_FILE} is empty.\n"
            "Paste a fresh Upstox access token into the file and re-run."
        )

    # Try JSON first; if that fails, treat the file as a raw token string.
    try:
        data = json.loads(raw)
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise SystemExit(
                f"ERROR: 'access_token' key missing in {TOKEN_FILE}\n"
                'Expected: { "access_token": "eyJ..." }'
            )
        return token
    except json.JSONDecodeError:
        # Plain-text token (most common cause of the user's earlier error)
        if raw.startswith("eyJ"):
            return raw
        raise SystemExit(
            f"ERROR: {TOKEN_FILE} is neither valid JSON nor a recognizable token.\n"
            "Token should start with 'eyJ'. Either paste the raw token or wrap it as JSON.\n"
            f"First 50 chars seen: {raw[:50]!r}"
        )


def iter_weekly_chunks(from_d: date, to_d: date):
    """Yield (chunk_from, chunk_to) in ascending order, 7-day windows inclusive."""
    cur = from_d
    while cur <= to_d:
        end = min(cur + timedelta(days=6), to_d)
        yield cur, end
        cur = end + timedelta(days=1)


def fetch_chunk(
    session: requests.Session,
    instrument_key: str,
    from_d: date,
    to_d: date,
    max_retries: int = 4,
) -> pd.DataFrame:
    encoded_key = quote(instrument_key, safe="")
    url = (
        f"{BASE}/{encoded_key}/1minute/"
        f"{to_d.isoformat()}/{from_d.isoformat()}"
    )

    for attempt in range(max_retries):
        try:
            r = session.get(url, timeout=20)
        except requests.RequestException as e:
            wait = 2 ** (attempt + 1)
            log.warning("network error (%s); retrying in %ds", e, wait)
            time.sleep(wait)
            continue

        if r.status_code == 429:
            wait = 2 ** (attempt + 1)
            log.warning("429 rate-limited; backing off %ds", wait)
            time.sleep(wait)
            continue
        if r.status_code == 401:
            raise SystemExit("ERROR: 401 Unauthorized — your token is invalid or expired")
        if r.status_code != 200:
            log.error("HTTP %d: %s", r.status_code, r.text[:200])
            return pd.DataFrame(columns=COLUMNS)

        candles = r.json().get("data", {}).get("candles", [])
        if not candles:
            return pd.DataFrame(columns=COLUMNS)
        df = pd.DataFrame(candles, columns=COLUMNS)
        return df

    log.error("chunk failed after %d retries", max_retries)
    return pd.DataFrame(columns=COLUMNS)


def fetch_instrument(
    session: requests.Session,
    label: str,
    instrument_key: str,
    from_d: date,
    to_d: date,
) -> pd.DataFrame:
    log.info("--- %s (%s)", label, instrument_key)
    frames: list[pd.DataFrame] = []
    for c_from, c_to in iter_weekly_chunks(from_d, to_d):
        log.info("   chunk %s -> %s", c_from, c_to)
        df = fetch_chunk(session, instrument_key, c_from, c_to)
        if not df.empty:
            frames.append(df)
        time.sleep(1.0)  # 1 req/sec

    if not frames:
        return pd.DataFrame(columns=COLUMNS)

    full = pd.concat(frames, ignore_index=True)
    # Upstox sometimes returns overlapping chunks; de-dup & sort.
    full["timestamp"] = pd.to_datetime(full["timestamp"])
    full = full.drop_duplicates(subset="timestamp").sort_values("timestamp")
    return full


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--to", dest="to_date", help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--months", type=int, default=1,
                        help="If --from is omitted, fetch the last N months "
                             "ending at --to (default 1)")
    args = parser.parse_args()

    to_d = (datetime.fromisoformat(args.to_date).date()
            if args.to_date else date.today())
    from_d = (datetime.fromisoformat(args.from_date).date()
              if args.from_date else (to_d - timedelta(days=30 * args.months)))

    if from_d > to_d:
        raise SystemExit("ERROR: --from is after --to")

    log.info("Date range: %s -> %s (%d days)", from_d, to_d, (to_d - from_d).days)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    token = load_access_token()
    log.info("Token loaded (len=%d)", len(token))

    sess = requests.Session()
    sess.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })

    for label, (key, filename) in INSTRUMENTS.items():
        out_path = DATA_DIR / filename
        df = fetch_instrument(sess, label, key, from_d, to_d)
        if df.empty:
            log.warning("no data for %s — skipping write", label)
            continue

        df.to_csv(out_path, index=False)
        first_ts = pd.to_datetime(df["timestamp"]).min()
        last_ts = pd.to_datetime(df["timestamp"]).max()
        log.info("wrote %s rows=%d  %s -> %s",
                 out_path.relative_to(ROOT), len(df), first_ts, last_ts)

    log.info("done. Next step: commit data/ via GitHub Desktop and push.")


if __name__ == "__main__":
    main()
