"""
IndicesCache — fetches index LTP / change / change% from the Upstox
market-quote API and caches per-instrument with a TTL (default 1
second). Used by the dashboard backend so the indices ticker can
update once per second without hammering the Upstox API.

Token is loaded from `state/upstox_session.json` (the same file the
auth flow already writes). If no valid session exists, we return
empty quotes — the dashboard will still render with a "no auth"
indicator instead of crashing.

Threadsafe-enough for FastAPI: a single asyncio event loop calls
`get_all()` concurrently with refreshes; we guard with a threading
lock since the underlying requests session is shared.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("live_quotes")

# Default index list — Upstox instrument keys.
# Operator can override by passing a list of (symbol, instrument_key)
# tuples to the IndicesCache constructor.
DEFAULT_INDICES: list[tuple[str, str]] = [
    ("NIFTY 50",        "NSE_INDEX|Nifty 50"),
    ("BANK NIFTY",      "NSE_INDEX|Nifty Bank"),
    ("SENSEX",          "BSE_INDEX|SENSEX"),
    ("NIFTY MIDCAP",    "NSE_INDEX|NIFTY MID SELECT"),
    ("INDIA VIX",       "NSE_INDEX|India VIX"),
]


class IndicesCache:
    def __init__(
        self,
        session_file: Path,
        *,
        indices: Optional[list[tuple[str, str]]] = None,
        ttl_seconds: float = 1.0,
        timeout: float = 4.0,
    ):
        self.session_file = Path(session_file)
        self.indices = list(indices) if indices is not None else list(DEFAULT_INDICES)
        self.ttl = float(ttl_seconds)
        self.timeout = float(timeout)
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._http = requests.Session()
        self._base_url = "https://api.upstox.com/v2"
        self._token_loaded_at: float = 0.0
        self._token: Optional[str] = None

    # ----- public API --------------------------------------------------

    def get_all(self) -> list[dict]:
        """Return a list of quote dicts, one per configured index. Each:
            {symbol, instrument_key, ltp, prev_close, change, change_pct,
             ts (ISO), stale (bool)}
        Stale=True if the cache wasn't refreshed within ttl seconds."""
        now = time.time()
        out: list[dict] = []
        for symbol, key in self.indices:
            quote = self._cache.get(key)
            if quote is None or (now - quote["_fetched_at"]) > self.ttl:
                quote = self._refresh(symbol, key, now)
            out.append(_strip_internal(quote))
        return out

    def get_one(self, instrument_key: str) -> Optional[dict]:
        for symbol, key in self.indices:
            if key == instrument_key:
                quote = self._cache.get(key)
                now = time.time()
                if quote is None or (now - quote["_fetched_at"]) > self.ttl:
                    quote = self._refresh(symbol, key, now)
                return _strip_internal(quote)
        return None

    # ----- refresh -----------------------------------------------------

    def _refresh(self, symbol: str, instrument_key: str, now: float) -> dict:
        token = self._access_token()
        empty = {
            "symbol": symbol,
            "instrument_key": instrument_key,
            "ltp": 0.0,
            "prev_close": 0.0,
            "change": 0.0,
            "change_pct": 0.0,
            "ts": datetime.now().astimezone().isoformat(),
            "stale": True,
            "_fetched_at": now,
        }
        if not token:
            with self._lock:
                self._cache[instrument_key] = empty
            return empty
        try:
            resp = self._http.get(
                f"{self._base_url}/market-quote/quotes",
                params={"instrument_key": instrument_key},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                log.debug("indices fetch %s -> %s", instrument_key, resp.status_code)
                with self._lock:
                    self._cache[instrument_key] = empty
                return empty
            payload = resp.json().get("data", {})
            # The Upstox response keys the data by a slightly mangled form
            # of the instrument key (with spaces -> _ etc), so just take
            # the first (and only) entry.
            block = next(iter(payload.values()), {}) if payload else {}
            ltp = float(block.get("last_price", 0) or 0)
            prev = float(
                block.get("prev_close", 0)
                or block.get("ohlc", {}).get("close", 0)
                or 0
            )
            change = ltp - prev if prev else 0.0
            change_pct = (change / prev * 100) if prev else 0.0
            quote = {
                "symbol": symbol,
                "instrument_key": instrument_key,
                "ltp": round(ltp, 2),
                "prev_close": round(prev, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 3),
                "ts": datetime.now().astimezone().isoformat(),
                "stale": False,
                "_fetched_at": now,
            }
            with self._lock:
                self._cache[instrument_key] = quote
            return quote
        except Exception as e:
            log.debug("indices fetch %s raised: %s", instrument_key, e)
            with self._lock:
                self._cache[instrument_key] = empty
            return empty

    # ----- token -------------------------------------------------------

    def _access_token(self) -> Optional[str]:
        """Re-read the session file at most once every 30 s (cheap, but
        not on every quote call)."""
        now = time.time()
        if self._token and (now - self._token_loaded_at) < 30:
            return self._token
        try:
            if not self.session_file.exists():
                self._token = None
                return None
            with self.session_file.open("r") as fh:
                session = json.load(fh)
            expires_at_str = session.get("expires_at", "")
            try:
                exp = datetime.fromisoformat(expires_at_str)
                if datetime.now().astimezone() >= exp:
                    self._token = None
                    return None
            except (TypeError, ValueError):
                pass
            self._token = session.get("access_token")
            self._token_loaded_at = now
            return self._token
        except Exception as e:
            log.debug("access_token load failed: %s", e)
            return None


def _strip_internal(q: dict) -> dict:
    return {k: v for k, v in q.items() if not k.startswith("_")}
