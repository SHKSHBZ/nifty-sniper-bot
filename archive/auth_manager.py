"""
auth_manager.py
Handles Upstox v2 OAuth2 authentication flow.
Credentials are loaded from environment variables — never hardcoded.

Setup:
    Set the following environment variables (or use a .env file):
        UPSTOX_API_KEY
        UPSTOX_API_SECRET
        UPSTOX_REDIRECT_URI   (e.g. http://127.0.0.1:5000/callback)

    On first run, a browser will open for Upstox login.
    The access token is cached in .upstox_token_cache.json for the session.
"""

import os
import json
import time
import webbrowser
import threading
import logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TOKEN_CACHE_FILE = Path(".upstox_token_cache.json")
UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


class _CallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler to capture the OAuth2 redirect code."""
    auth_code: str | None = None

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Upstox login successful. You may close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>Login failed — no code received.</h2>")

    def log_message(self, *args):  # suppress server logs
        pass


class AuthManager:
    """
    Manages Upstox v2 OAuth2 token lifecycle.

    Usage:
        auth = AuthManager()
        headers = auth.get_headers()   # Pass to every API / websocket call
    """

    def __init__(self):
        self.api_key = os.getenv("UPSTOX_API_KEY")
        self.api_secret = os.getenv("UPSTOX_API_SECRET")
        self.redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:5000/callback")

        if not self.api_key or not self.api_secret:
            raise EnvironmentError(
                "Missing UPSTOX_API_KEY or UPSTOX_API_SECRET environment variables. "
                "Add them to your .env file or shell environment."
            )

        self.access_token: str | None = None
        self._load_cached_token()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_headers(self) -> dict:
        """Return HTTP headers with a valid Bearer token."""
        if not self.access_token or self._is_token_expired():
            self._authenticate()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def get_access_token(self) -> str:
        if not self.access_token or self._is_token_expired():
            self._authenticate()
        return self.access_token

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _authenticate(self):
        """Full OAuth2 flow: opens browser → captures code → exchanges for token."""
        logger.info("Starting Upstox OAuth2 flow...")
        auth_url = (
            f"{UPSTOX_AUTH_URL}"
            f"?response_type=code"
            f"&client_id={self.api_key}"
            f"&redirect_uri={self.redirect_uri}"
        )
        _CallbackHandler.auth_code = None

        # Start temporary local server to catch the redirect
        port = int(urlparse(self.redirect_uri).port or 5000)
        server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        webbrowser.open(auth_url)
        logger.info("Browser opened for Upstox login. Waiting for callback...")

        thread.join(timeout=120)
        if not _CallbackHandler.auth_code:
            raise TimeoutError("Upstox login timed out after 120 seconds.")

        self._exchange_code_for_token(_CallbackHandler.auth_code)

    def _exchange_code_for_token(self, code: str):
        resp = requests.post(
            UPSTOX_TOKEN_URL,
            data={
                "code": code,
                "client_id": self.api_key,
                "client_secret": self.api_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self._cache_token(data)
        logger.info("Upstox authentication successful. Token cached.")

    def _cache_token(self, token_data: dict):
        token_data["_cached_at"] = time.time()
        TOKEN_CACHE_FILE.write_text(json.dumps(token_data, indent=2))

    def _load_cached_token(self):
        if TOKEN_CACHE_FILE.exists():
            try:
                data = json.loads(TOKEN_CACHE_FILE.read_text())
                if not self._is_token_expired(data):
                    self.access_token = data.get("access_token")
                    logger.info("Loaded cached Upstox token.")
            except (json.JSONDecodeError, KeyError):
                logger.warning("Token cache corrupted. Will re-authenticate.")

    def _is_token_expired(self, data: dict | None = None) -> bool:
        """Upstox tokens are valid for the trading day (~6-8 hours). We re-auth after 6h."""
        if data is None:
            if not TOKEN_CACHE_FILE.exists():
                return True
            data = json.loads(TOKEN_CACHE_FILE.read_text())
        cached_at = data.get("_cached_at", 0)
        return (time.time() - cached_at) > 6 * 3600


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mgr = AuthManager()
    print("Headers ready:", mgr.get_headers())
