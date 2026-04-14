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
from pydantic import BaseModel
from typing import Optional
import requests as http_requests
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz

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
tracked_pids = {"NIFTY": None, "SENSEX": None}


def find_running_bots():
    """Scan system for running main.py processes."""
    found = {"NIFTY": None, "SENSEX": None}
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            cmd_str = " ".join(cmdline).lower()
            if 'main.py' in cmd_str and 'python' in cmd_str:
                if 'config_sensex' in cmd_str:
                    found["SENSEX"] = proc.pid
                else:
                    found["NIFTY"] = proc.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


# --- Status ---
@app.get("/status")
def get_status():
    discovered = find_running_bots()
    result = []
    for name in ["NIFTY", "SENSEX"]:
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
    if bot_type not in ["NIFTY", "SENSEX"]:
        raise HTTPException(400, "Use NIFTY or SENSEX")

    # Check if already running
    discovered = find_running_bots()
    if discovered.get(bot_type):
        tracked_pids[bot_type] = discovered[bot_type]
        return {"message": f"{bot_type} already running", "pid": discovered[bot_type]}

    cmd = [sys.executable, str(BASE_DIR / "main.py")]
    if bot_type == "SENSEX":
        cmd.append("config_sensex.json")

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
        tracked_pids[bot_type] = proc.pid
        return {"message": f"{bot_type} started", "pid": proc.pid}
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
@app.get("/stats")
def get_stats():
    if not PORTFOLIO_FILE.exists():
        return {"capital": 100000, "open_position": None, "trade_history": []}
    with open(PORTFOLIO_FILE, "r") as f:
        return json.load(f)


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
    log_files = sorted(LOG_DIR.glob("sniper_bot_*.log"), reverse=True)
    if not log_files:
        return {"logs": "No log files found. Start the bot to generate logs."}
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


# --- Health ---
@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
