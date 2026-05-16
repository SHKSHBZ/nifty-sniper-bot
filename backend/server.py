import asyncio
import os
import sys
import subprocess
import json
import psutil
from pathlib import Path

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import requests as http_requests
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from datetime import datetime, timedelta, date
import pytz

from backend.live_quotes import IndicesCache

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

IST = pytz.timezone('Asia/Kolkata')

app = FastAPI()

# Allow ALL origins for mobile/Tailscale access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
BASE_DIR = Path(__file__).parent.parent
PORTFOLIO_FILE = BASE_DIR / "data" / "paper_portfolio.json"
CONFIG_FILE = BASE_DIR / "project_config.json"
CONFIG_SENSEX_FILE = BASE_DIR / "config_sensex.json"
LOG_DIR = BASE_DIR / "logs"
SESSION_FILE = BASE_DIR / "state" / "upstox_session.json"

# --- Process Tracking ---
# Consolidated 2-bot view (2026-05-17): regime-mode bots only. The dispatcher
# in regime mode already runs all tactics (T1 / T2 / trend_pullback / vwap /
# ORBs) — the previous T1/T2/legacy variants were running duplicate trades.
# Legacy/T1/T2 variants kept as lab-only labels for backward compat with the
# dashboard, but are not in the active set.
ACTIVE_BOT_TYPES = ["NIFTY_REGIME", "SENSEX_REGIME", "NIFTY_SELLER"]
# Lab-only labels still readable (so dashboard can show historical portfolios)
LAB_BOT_TYPES = ["NIFTY", "SENSEX", "NIFTY_T1", "NIFTY_T2"]
BOT_TYPES = ACTIVE_BOT_TYPES + LAB_BOT_TYPES
tracked_pids = {b: None for b in BOT_TYPES}


def _suffix(bot_type: str) -> str:
    """Return the on-disk file suffix for this bot type.
    NIFTY/SENSEX → 'NIFTY'/'SENSEX'  (legacy files, no extra suffix).
    NIFTY_REGIME → 'NIFTY_regime'.
    NIFTY_T1     → 'NIFTY_t1'.
    NIFTY_T2     → 'NIFTY_t2'.
    NIFTY_SELLER → 'NIFTY_seller'.
    """
    bt = bot_type.upper()
    if bt == "NIFTY_REGIME":  return "NIFTY_regime"
    if bt == "SENSEX_REGIME": return "SENSEX_regime"
    if bt == "NIFTY_T1":      return "NIFTY_t1"
    if bt == "NIFTY_T2":      return "NIFTY_t2"
    if bt == "NIFTY_SELLER":  return "NIFTY_seller"
    return bt

# --- Live indices cache (1s TTL) ---
indices_cache = IndicesCache(session_file=SESSION_FILE)


# --- IPC file helpers ---

def _engine_state_file(bot_type: str) -> Path:
    """Resolve the on-disk engine_state file written by main.py for `bot_type`."""
    return BASE_DIR / "data" / f"engine_state_{_suffix(bot_type)}.json"


def _missed_today_file(bot_type: str) -> Path:
    return BASE_DIR / "data" / f"missed_today_{_suffix(bot_type)}.json"


def _portfolio_file(bot_type: str) -> tuple[Path, float]:
    return BASE_DIR / "data" / f"paper_portfolio_{_suffix(bot_type)}.json", 100000.0


def _safe_load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        with path.open("r") as fh:
            return json.load(fh)
    except Exception:
        return default


def find_running_bots():
    """Scan system for running main.py processes. Returns map of
    bot_type -> pid. Distinguishes NIFTY_REGIME / SENSEX_REGIME from the
    plain legacy ones via the config-file argument on the cmdline.
    """
    found = {b: None for b in BOT_TYPES}
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            cmd_str = " ".join(cmdline).lower()
            if 'main.py' not in cmd_str or 'python' not in cmd_str:
                continue
            if 'scalper_main.py' in cmd_str:
                continue
            is_sensex = 'config_sensex' in cmd_str
            is_regime = '_regime' in cmd_str
            is_t1     = 'config_t1' in cmd_str
            is_t2     = 'config_t2' in cmd_str
            is_seller = '_seller' in cmd_str
            if is_seller:               found["NIFTY_SELLER"] = proc.pid
            elif is_t1:                 found["NIFTY_T1"] = proc.pid
            elif is_t2:                 found["NIFTY_T2"] = proc.pid
            elif is_sensex and is_regime: found["SENSEX_REGIME"] = proc.pid
            elif is_sensex:             found["SENSEX"] = proc.pid
            elif is_regime:             found["NIFTY_REGIME"] = proc.pid
            else:                       found["NIFTY"] = proc.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


# --- Status ---
@app.get("/status")
def get_status():
    discovered = find_running_bots()
    result = []
    for name in BOT_TYPES:
        pid = discovered.get(name) or tracked_pids.get(name)
        # Verify pid is still alive
        if pid:
            try:
                p = psutil.Process(pid)
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    result.append({"name": name, "status": "running", "pid": pid})
                    tracked_pids[name] = pid
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        tracked_pids[name] = None
        result.append({"name": name, "status": "stopped", "pid": None})
    return result


# --- Start / Stop ---
@app.post("/start/{bot_type}")
def start_bot(bot_type: str):
    bot_type = bot_type.upper()
    if bot_type not in BOT_TYPES:
        raise HTTPException(400, "Invalid bot type")
    # Block lab-only configs from being launched. They duplicated the
    # active regime bots' trades; retired 2026-05-17 to avoid 3x order
    # flow through the single Upstox API on live deployment.
    if bot_type in LAB_BOT_TYPES:
        raise HTTPException(
            403,
            f"{bot_type} is a lab-only bot type (retired 2026-05-17). "
            f"Use NIFTY_REGIME or SENSEX_REGIME — the regime dispatcher "
            f"already runs the tactics this bot used to fire."
        )

    # Check if already running
    discovered = find_running_bots()
    if discovered.get(bot_type):
        tracked_pids[bot_type] = discovered[bot_type]
        return {"message": f"{bot_type} already running", "pid": discovered[bot_type]}

    config_map = {
        "NIFTY":         "project_config.json",
        "NIFTY_REGIME":  "project_config_regime.json",
        "SENSEX":        "config_sensex.json",
        "SENSEX_REGIME": "config_sensex_regime.json",
        "NIFTY_T1":      "project_config_t1.json",
        "NIFTY_T2":      "project_config_t2.json",
        "NIFTY_SELLER":  "project_config_seller.json",
    }
    cmd = [sys.executable, "-u", str(BASE_DIR / "main.py"), config_map[bot_type]]

    # Capture startup output so silent crashes (missing .env, bad creds,
    # interactive input EOF, import errors) are visible. Each launch writes
    # to its own file so the previous run's diagnostics aren't overwritten.
    LOG_DIR.mkdir(exist_ok=True)
    startup_log = LOG_DIR / f"startup_{_suffix(bot_type)}.log"
    try:
        log_fh = open(startup_log, "w", encoding="utf-8", buffering=1)
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_fh, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
        tracked_pids[bot_type] = proc.pid
        return {
            "message": f"{bot_type} started",
            "pid": proc.pid,
            "startup_log": str(startup_log),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/stop/{bot_type}")
def stop_bot(bot_type: str):
    bot_type = bot_type.upper()
    discovered = find_running_bots()
    pid = discovered.get(bot_type) or tracked_pids.get(bot_type)

    if not pid:
        return {"message": f"{bot_type} is not running"}

    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except Exception:
        pass

    tracked_pids[bot_type] = None
    return {"message": f"{bot_type} stopped"}


# --- Stats ---
@app.get("/stats/{bot_type}")
def get_stats(bot_type: str):
    bot_type = bot_type.upper()
    f, cap = _portfolio_file(bot_type)
    if not f.exists():
        return {"capital": cap, "open_position": None, "trade_history": []}
    with open(f, "r") as reader:
        return json.load(reader)


# --- Config ---
@app.get("/config")
def get_config():
    if not CONFIG_FILE.exists():
        raise HTTPException(404, "Config not found")
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


@app.post("/config")
def update_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
    return {"message": "Updated"}


# --- Logs ---
@app.get("/logs/{bot_type}")
def get_logs(bot_type: str, lines: int = 100):
    # Each bot variant writes to logs/sniper_bot_{INDEX}[_regime]_DATE.log.
    # Match the latest file for the requested variant.
    prefix = f"sniper_bot_{_suffix(bot_type)}_"
    log_files = sorted(LOG_DIR.glob(f"{prefix}*.log"), reverse=True)
    # Backward compat: pre-split logs lived at sniper_bot_DATE.log (no
    # index suffix). Fall back to those for legacy NIFTY/SENSEX only.
    if not log_files and bot_type.upper() in ("NIFTY", "SENSEX"):
        log_files = sorted(LOG_DIR.glob("sniper_bot_*.log"), reverse=True)
    if not log_files:
        return {"logs": f"No log files found for {bot_type}. Start the bot to generate logs."}
    try:
        with open(log_files[0], "r", encoding="utf-8") as f:
            content = f.readlines()
            return {"logs": "".join(content[-lines:])}
    except Exception as e:
        return {"logs": f"Error: {e}"}


# --- Auth ---
@app.get("/auth/status")
def auth_status():
    try:
        if not SESSION_FILE.exists():
            return {"is_authenticated": False}
        with open(SESSION_FILE, "r") as f:
            session = json.load(f)
        expires_at = datetime.fromisoformat(session.get("expires_at", "2000-01-01T00:00:00+05:30"))
        now = datetime.now(IST)
        return {"is_authenticated": now < expires_at}
    except Exception:
        return {"is_authenticated": False}


@app.get("/auth/url")
def auth_url():
    client_id = os.getenv("UPSTOX_API_KEY")
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise HTTPException(400, "UPSTOX_API_KEY or UPSTOX_REDIRECT_URI not set in .env")
    url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}"
    )
    return {"url": url}


class AuthSubmit(BaseModel):
    url: str


@app.post("/auth/submit")
def auth_submit(data: AuthSubmit):
    client_id = os.getenv("UPSTOX_API_KEY")
    client_secret = os.getenv("UPSTOX_API_SECRET")
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI")

    if not all([client_id, client_secret, redirect_uri]):
        raise HTTPException(400, "Credentials missing in .env")

    parsed = urlparse(data.url)
    params = parse_qs(parsed.query)
    code = params.get("code", [None])[0]

    if not code:
        raise HTTPException(400, "No authorization code in URL")

    resp = http_requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    )

    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text)

    token_data = resp.json()
    access_token = token_data["access_token"]

    # Save session
    now = datetime.now(IST)
    next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now.hour >= 6:
        next_6am += timedelta(days=1)

    session = {
        "access_token": access_token,
        "client_id": client_id,
        "created_at": now.isoformat(),
        "expires_at": next_6am.isoformat(),
        "token_data": token_data,
        "broker": "UPSTOX",
    }
    SESSION_FILE.parent.mkdir(exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        json.dump(session, f, indent=2)

    return {"message": "Authenticated", "is_authenticated": True}


# --- Live indices ticker ---
@app.get("/quotes/indices")
def quotes_indices():
    """Return LTP / change / change% for the configured set of indices.
    Cached at IndicesCache TTL (default 1 s)."""
    return {"quotes": indices_cache.get_all()}


# --- Engine state (regime, spot, signal, etc.) ---
@app.get("/engine/state/{bot_type}")
def engine_state(bot_type: str):
    """Return the latest engine_state JSON written by the bot. If the bot
    is not running (or hasn't written yet), returns a minimal stub with
    `available: false`."""
    path = _engine_state_file(bot_type)
    state = _safe_load_json(path, None)
    if state is None:
        return {
            "available": False,
            "bot_type": bot_type.upper(),
            "message": "Bot has not published engine state yet (start the bot to populate).",
        }
    state["available"] = True
    state["bot_type"] = bot_type.upper()
    return state


# --- Today's near-misses (live + finalised) ---
@app.get("/near-miss/today/{bot_type}")
def near_miss_today(bot_type: str):
    path = _missed_today_file(bot_type)
    payload = _safe_load_json(path, None)
    if payload is None:
        return {
            "available": False,
            "bot_type": bot_type.upper(),
            "missed": [],
        }
    payload["available"] = True
    payload["bot_type"] = bot_type.upper()
    return payload


# --- Today's P&L summary ---
@app.get("/pnl/today/{bot_type}")
def pnl_today(bot_type: str):
    """Derive today's realized P&L + sparkline from the existing
    paper_portfolio_<BOT>.json. Filters trade_history to today's date
    (IST) and computes a cumulative P&L timeseries."""
    portfolio_path, default_cap = _portfolio_file(bot_type)
    portfolio = _safe_load_json(portfolio_path, None)
    today_str = datetime.now(IST).date().isoformat()

    if portfolio is None:
        return {
            "date": today_str, "bot_type": bot_type.upper(),
            "starting_capital": default_cap, "current_capital": default_cap,
            "realized_pnl": 0.0, "trade_count": 0, "win_count": 0,
            "loss_count": 0, "trades": [], "pnl_timeseries": [],
        }

    capital_now = float(portfolio.get("capital", default_cap))
    history = portfolio.get("trade_history", []) or []

    today_trades = [
        t for t in history
        if (t.get("exit_time", "") or "")[:10] == today_str
    ]

    cumulative = 0.0
    timeseries = []
    realized = 0.0
    wins = 0
    losses = 0
    for t in today_trades:
        pnl = float(t.get("pnl", 0) or 0)
        cumulative += pnl
        realized += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        timeseries.append({
            "ts": t.get("exit_time", ""),
            "pnl": pnl,
            "cumulative_pnl": round(cumulative, 2),
        })

    return {
        "date": today_str,
        "bot_type": bot_type.upper(),
        "starting_capital": round(capital_now - realized, 2),
        "current_capital": round(capital_now, 2),
        "realized_pnl": round(realized, 2),
        "trade_count": len(today_trades),
        "win_count": wins,
        "loss_count": losses,
        "trades": today_trades,
        "pnl_timeseries": timeseries,
    }


# --- Live SSE stream — pushes everything every 1 second ---
@app.get("/stream/{bot_type}")
async def stream(bot_type: str):
    """Server-Sent Events: emits one JSON event per second carrying the
    indices ticker, engine state, missed-today snapshot, P&L summary,
    and bot status. Frontend opens this with EventSource(...) and
    avoids running 5 polling loops."""

    async def event_gen():
        # SSE retry hint (browser auto-reconnects after 3 s on disconnect)
        yield "retry: 3000\n\n"
        while True:
            try:
                payload = {
                    "type": "tick",
                    "ts": datetime.now(IST).isoformat(),
                    "indices": indices_cache.get_all(),
                    "engine_state": _safe_load_json(_engine_state_file(bot_type), None),
                    "near_miss": _safe_load_json(_missed_today_file(bot_type), None),
                    "pnl": pnl_today(bot_type),
                    "status": get_status(),
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# --- Health ---
@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
