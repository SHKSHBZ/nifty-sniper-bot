"""
data_collector.py — Standalone Market Data Collector
====================================================
Runs INDEPENDENTLY from the trading bot. Collects focus-zone option chain
data and macro snapshots every ~60 seconds during market hours (09:15-15:30 IST).

Usage:
    python data_collector.py NIFTY
    python data_collector.py SENSEX

This process has NO dependency on the bot. It just fetches, computes, and logs.
If the bot crashes, data keeps flowing. If this crashes, the bot keeps trading.
"""

import sys
import time
import json
import csv
import os
import logging
import threading
import requests
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from regime.market_hours import IST, MARKET_OPEN, MARKET_CLOSE

# ── Logging ──────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
index_arg = sys.argv[1].upper() if len(sys.argv) > 1 else "NIFTY"
log_path = f"logs/data_collector_{index_arg}.log"  # single log, overwritten each start

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("DataCollector")


# ── Config ───────────────────────────────────────────────────────────
if index_arg == "SENSEX":
    INSTRUMENT_KEY = "BSE_INDEX|SENSEX"
    STRIKE_STEP = 100
    EXPIRY_WEEKDAY = 4  # Friday (fallback only)
else:
    INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"
    STRIKE_STEP = 50
    EXPIRY_WEEKDAY = 1  # Tuesday (fallback only)

BASE_URL = "https://api.upstox.com/v2"
INDEX_NAME = index_arg.lower()

# Track current expiry for file grouping
_current_expiry: str = ""  # set on first fetch


def get_expiry_for_filename(session, token) -> str:
    """Return the ACTUAL nearest expiry date from Upstox API.
    Files are grouped by expiry week — when expiry changes, a new file starts.
    Handles holiday-shifted expiries (e.g., NIFTY on Monday instead of Tuesday)."""
    global _current_expiry

    # Try API first
    try:
        url = f"{BASE_URL}/option/contract"
        params = {"instrument_key": INSTRUMENT_KEY}
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = session.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            expiries = sorted({item.get("expiry") for item in data if item.get("expiry")})
            if expiries:
                today = datetime.now(IST).date()
                valid = [e for e in expiries if datetime.strptime(e, "%Y-%m-%d").date() >= today]
                if valid:
                    api_expiry = valid[0]  # nearest valid expiry
                    if _current_expiry and api_expiry != _current_expiry:
                        log.info(f"Expiry changed: {_current_expiry} -> {api_expiry} (new file)")
                    _current_expiry = api_expiry
                    return api_expiry
    except Exception as e:
        log.warning(f"Expiry API call failed, using fallback: {e}")

    # Fallback: compute based on configured weekday
    today = datetime.now(IST).date()
    days_ahead = (EXPIRY_WEEKDAY - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    fallback = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    if _current_expiry and fallback != _current_expiry:
        log.info(f"Expiry changed (fallback): {_current_expiry} -> {fallback}")
    _current_expiry = fallback
    return fallback


# ── Auth ─────────────────────────────────────────────────────────────
def load_access_token():
    """Load the latest Upstox access token from the session file. Auto-refresh if expired."""
    try:
        from upstox_auth import UpstoxAuth
        auth = UpstoxAuth()
        if not auth.is_session_valid():
            log.info("Session invalid or expired. Attempting auto-authentication...")
            auth.auto_authenticate()
        if auth.is_session_valid():
            return auth.access_token
    except Exception as e:
        log.error(f"Auth load failed: {e}")
    return None


# ── API Helpers ──────────────────────────────────────────────────────
def fetch_expiry(session, token):
    """Get the nearest valid expiry date."""
    url = f"{BASE_URL}/option/contract"
    params = {"instrument_key": INSTRUMENT_KEY}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = session.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            expiries = {item.get("expiry") for item in data if item.get("expiry")}
            if expiries:
                today = datetime.now().date()
                valid = []
                for e in expiries:
                    dt = datetime.strptime(e, "%Y-%m-%d").date()
                    if dt >= today:
                        valid.append(dt)
                if valid:
                    return min(valid).strftime("%Y-%m-%d")
    except Exception as e:
        log.error(f"Expiry fetch failed: {e}")

    # Fallback: compute Thursday (NIFTY weekly)
    today = datetime.now().date()
    target = 3  # Thursday
    days_ahead = (target - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def fetch_vix(session, token):
    """Get current India VIX."""
    try:
        url = f"{BASE_URL}/market-quote/quotes"
        params = {"instrument_key": "NSE_INDEX|INDIA VIX"}
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = session.get(url, params=params, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            for v in data.values():
                return float(v.get("last_price", 15.0))
    except Exception:
        pass
    return 15.0


def fetch_chain(session, token):
    """Fetch the full option chain and return (spot, chain_data, expiry)."""
    expiry = fetch_expiry(session, token)
    url = f"{BASE_URL}/option/chain"
    params = {"instrument_key": INSTRUMENT_KEY, "expiry_date": expiry}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    for attempt in range(3):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    spot = data[0].get("underlying_spot_price", 0)
                    return spot, data, expiry
            if resp.status_code == 401:
                log.error("Token expired (401). Needs re-auth.")
                return None, [], expiry
            time.sleep(2 ** attempt)
        except Exception as e:
            log.error(f"Chain fetch attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)

    return None, [], expiry


# ── Compute Metrics ──────────────────────────────────────────────────
def compute_max_pain(chain, spot):
    """Max pain within ATM ± 5 strikes."""
    strikes = sorted(item["strike_price"] for item in chain)
    atm = min(strikes, key=lambda x: abs(x - spot))
    zone = [atm + i * STRIKE_STEP for i in range(-5, 6)]
    zone = [s for s in zone if s in strikes]

    best_strike, best_pain = atm, float("inf")
    for candidate in zone:
        pain = 0
        for item in chain:
            s = item["strike_price"]
            ce_oi = item.get("call_options", {}).get("market_data", {}).get("oi", 0)
            pe_oi = item.get("put_options", {}).get("market_data", {}).get("oi", 0)
            if s > candidate:
                pain += (s - candidate) * ce_oi
            elif s < candidate:
                pain += (candidate - s) * pe_oi
        if pain < best_pain:
            best_pain = pain
            best_strike = candidate

    dist = abs(spot - best_strike)
    return best_strike, dist


def compute_sr(chain, spot):
    """Cluster-based S/R from OI concentration."""
    strikes = sorted(item["strike_price"] for item in chain)
    atm = min(strikes, key=lambda x: abs(x - spot))
    zone = [atm + i * STRIKE_STEP for i in range(-8, 9)]
    zone = [s for s in zone if s in strikes]

    ce_wall = max(zone, key=lambda s: next(
        (i.get("call_options", {}).get("market_data", {}).get("oi", 0)
         for i in chain if i["strike_price"] == s), 0))
    pe_wall = max(zone, key=lambda s: next(
        (i.get("put_options", {}).get("market_data", {}).get("oi", 0)
         for i in chain if i["strike_price"] == s), 0))

    support = pe_wall if pe_wall < spot else min(s for s in zone if s < spot)
    resistance = ce_wall if ce_wall > spot else max(s for s in zone if s > spot)
    return support, resistance


def compute_focus_zone(chain, spot):
    """ATM ± 3 strikes PCR and OI deltas."""
    strikes = [item["strike_price"] for item in chain]
    atm = min(strikes, key=lambda x: abs(x - spot))
    zone = [atm + i * STRIKE_STEP for i in range(-3, 4)]

    total_ce_oi = total_pe_oi = 0
    ce_oi_change = pe_oi_change = 0

    for item in chain:
        s = item["strike_price"]
        if s not in zone:
            continue
        ce = item.get("call_options", {}).get("market_data", {})
        pe = item.get("put_options", {}).get("market_data", {})
        total_ce_oi += ce.get("oi", 0)
        total_pe_oi += pe.get("oi", 0)
        ce_oi_change += ce.get("oi", 0) - ce.get("prev_oi", ce.get("oi", 0))
        pe_oi_change += pe.get("oi", 0) - pe.get("prev_oi", pe.get("oi", 0))

    pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0
    return pcr, {
        "ce_oi_change": ce_oi_change,
        "pe_oi_change": pe_oi_change,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
    }


def compute_oi_iv(chain, spot):
    """Total CE/PE OI change and ATM IV."""
    ce_change = pe_change = 0
    atm_iv = 0
    strikes = [item["strike_price"] for item in chain]
    atm = min(strikes, key=lambda x: abs(x - spot))

    for item in chain:
        ce = item.get("call_options", {}).get("market_data", {})
        pe = item.get("put_options", {}).get("market_data", {})
        ce_change += ce.get("oi", 0) - ce.get("prev_oi", ce.get("oi", 0))
        pe_change += pe.get("oi", 0) - pe.get("prev_oi", pe.get("oi", 0))
        if item["strike_price"] == atm:
            atm_iv = item.get("call_options", {}).get("option_greeks", {}).get("iv", 0)

    return ce_change, pe_change, atm_iv


# ── CSV Writers ──────────────────────────────────────────────────────
def write_focus_zone(spot, chain, atm_strike, expiry_date):
    """Write per-strike focus zone rows. Files grouped by expiry week.
    Expiry date from Upstox API — handles holiday-shifted expiries."""
    now_ist = datetime.now(IST)
    if now_ist.weekday() >= 5:
        return
    t = now_ist.time()
    if t < MARKET_OPEN or t >= MARKET_CLOSE:
        return

    timestamp = now_ist.strftime("%Y-%m-%d %H:%M:%S")
    csv_path = f"logs/focus_zone_{INDEX_NAME}_expiry_{expiry_date}.csv"

    focus_strikes = [atm_strike + i * STRIKE_STEP for i in range(-3, 4)]

    rows = []
    for item in chain:
        strike = item["strike_price"]
        if strike not in focus_strikes:
            continue
        ce = item.get("call_options", {})
        pe = item.get("put_options", {})
        ce_m = ce.get("market_data", {})
        pe_m = pe.get("market_data", {})
        ce_g = ce.get("option_greeks", {})
        pe_g = pe.get("option_greeks", {})

        if strike == atm_strike:
            pos_label = "ATM"
        elif strike > atm_strike:
            pos_label = f"+{(strike - atm_strike) // STRIKE_STEP}"
        else:
            pos_label = str((strike - atm_strike) // STRIKE_STEP)

        rows.append({
            "timestamp": timestamp,
            "spot": round(spot, 2),
            "strike": strike,
            "pos": pos_label,
            "ce_ltp": ce_m.get("ltp", 0),
            "ce_oi": ce_m.get("oi", 0),
            "ce_prev_oi": ce_m.get("prev_oi", 0),
            "ce_volume": ce_m.get("volume", 0),
            "ce_iv": ce_g.get("iv", 0),
            "ce_delta": ce_g.get("delta", 0),
            "ce_theta": ce_g.get("theta", 0),
            "ce_gamma": ce_g.get("gamma", 0),
            "ce_vega": ce_g.get("vega", 0),
            "pe_ltp": pe_m.get("ltp", 0),
            "pe_oi": pe_m.get("oi", 0),
            "pe_prev_oi": pe_m.get("prev_oi", 0),
            "pe_volume": pe_m.get("volume", 0),
            "pe_iv": pe_g.get("iv", 0),
            "pe_delta": abs(pe_g.get("delta", 0)),
            "pe_theta": pe_g.get("theta", 0),
            "pe_gamma": pe_g.get("gamma", 0),
            "pe_vega": pe_g.get("vega", 0),
        })

    if not rows:
        return

    def _write():
        try:
            exists = os.path.isfile(csv_path)
            with open(csv_path, 'a', newline='') as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys())
                if not exists:
                    w.writeheader()
                w.writerows(rows)
        except Exception as e:
            log.error(f"FZ CSV write error: {e}")

    threading.Thread(target=_write, daemon=True).start()


def write_macro(spot, pcr, focus_pcr, max_pain_strike, max_pain_dist,
                support, resistance, ce_chg, pe_chg, oi_pattern, atm_iv, vix, expiry_date):
    """Write one-row macro snapshot. Files grouped by expiry week."""
    now_ist = datetime.now(IST)
    if now_ist.weekday() >= 5:
        return
    t = now_ist.time()
    if t < MARKET_OPEN or t >= MARKET_CLOSE:
        return

    csv_path = f"logs/macro_{INDEX_NAME}_expiry_{expiry_date}.csv"

    row = {
        "timestamp": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "spot": round(spot, 2),
        "india_vix": vix,
        "pcr": pcr,
        "focus_pcr": focus_pcr,
        "max_pain_strike": max_pain_strike,
        "max_pain_dist": max_pain_dist,
        "support_strike": support,
        "resistance_strike": resistance,
        "total_ce_oi_change": ce_chg,
        "total_pe_oi_change": pe_chg,
        "focus_ce_oi_change": oi_pattern.get("ce_oi_change", 0),
        "focus_pe_oi_change": oi_pattern.get("pe_oi_change", 0),
        "total_ce_oi": oi_pattern.get("total_ce_oi", 0),
        "total_pe_oi": oi_pattern.get("total_pe_oi", 0),
        "atm_iv": atm_iv,
    }

    def _write():
        try:
            exists = os.path.isfile(csv_path)
            with open(csv_path, 'a', newline='') as f:
                w = csv.DictWriter(f, fieldnames=row.keys())
                if not exists:
                    w.writeheader()
                w.writerow(row)
        except Exception as e:
            log.error(f"Macro CSV write error: {e}")

    threading.Thread(target=_write, daemon=True).start()


# ── Main Loop ────────────────────────────────────────────────────────
def wait_until_market():
    """Sleep until 09:15 IST on a trading day."""
    while True:
        now = datetime.now(IST)
        if now.weekday() >= 5:  # Weekend
            days_to_mon = (7 - now.weekday()) % 7
            next_open = (now + timedelta(days=days_to_mon)).replace(
                hour=9, minute=15, second=0, microsecond=0)
            secs = (next_open - now).total_seconds()
            log.info(f"Weekend. Waiting {secs/3600:.1f}h until {next_open}")
            time.sleep(min(600, secs))
            continue

        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

        if now < market_open:
            secs = (market_open - now).total_seconds()
            log.info(f"Pre-market. Waiting {secs/60:.0f}m until 09:15 IST")
            time.sleep(min(60, secs))
        elif now >= market_close:
            next_open = (now + timedelta(days=1)).replace(
                hour=9, minute=15, second=0, microsecond=0)
            secs = (next_open - now).total_seconds()
            log.info(f"Post-market. Waiting {secs/3600:.1f}h until tomorrow")
            time.sleep(min(600, secs))
        else:
            return  # Market is open


def run():
    log.info(f"=== Data Collector STARTED | Index={index_arg} ===")
    session = requests.Session()
    consecutive_failures = 0

    while True:
        try:
            wait_until_market()

            token = load_access_token()
            if not token:
                log.error("No access token. Retrying in 60s...")
                time.sleep(60)
                continue

            while True:
                now = datetime.now(IST)
                if now.time() >= MARKET_CLOSE or now.weekday() >= 5:
                    log.info("Market closed. Waiting for next session.")
                    break

                try:
                    spot, chain, expiry = fetch_chain(session, token)
                    if spot is None or not chain:
                        consecutive_failures += 1
                        if consecutive_failures > 5:
                            log.error("Too many failures. Re-authenticating...")
                            token = load_access_token()
                            consecutive_failures = 0
                        time.sleep(10)
                        continue

                    consecutive_failures = 0

                    vix = fetch_vix(session, token)
                    max_pain, mp_dist = compute_max_pain(chain, spot)
                    support, resistance = compute_sr(chain, spot)
                    ce_chg, pe_chg, atm_iv = compute_oi_iv(chain, spot)
                    focus_pcr, oi_pat = compute_focus_zone(chain, spot)

                    # PCR from API (raw/1000)
                    pcr_raw = chain[0].get("pcr", 0) if chain else 0
                    api_pcr = pcr_raw / 1000.0 if pcr_raw > 0 else 0.0

                    # ATM strike
                    strikes = [item["strike_price"] for item in chain]
                    atm_strike = min(strikes, key=lambda x: abs(x - spot))

                    # Write CSVs — grouped by Upstox-provided expiry date
                    write_focus_zone(spot, chain, atm_strike, expiry)
                    write_macro(spot, api_pcr, focus_pcr, max_pain, mp_dist,
                                support, resistance, ce_chg, pe_chg, oi_pat, atm_iv, vix, expiry)

                    log.info(f"OK | Spot={spot:.1f} PCR={focus_pcr:.2f} "
                             f"S={support} R={resistance} MP={max_pain} IV={atm_iv:.1f}")

                    # Sleep ~60s between fetches
                    time.sleep(55)

                except Exception as e:
                    log.error(f"Loop error: {e}")
                    consecutive_failures += 1
                    time.sleep(10)

        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break
        except Exception as e:
            log.error(f"FATAL outer loop: {e}. Restarting in 30s...")
            time.sleep(30)


if __name__ == "__main__":
    run()
