"""End-of-day Telegram summary across all 4 bot variants.

Reads each bot's paper_portfolio_*.json, filters today's trades, computes
P&L, and sends one Telegram message. Designed to run as a Windows
scheduled task at 15:35 IST (= 14:05 UAE) Mon-Fri.

Safe to run anytime — if a bot didn't run today, its line says "not run".
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# Force UTF-8 stdout so emoji/rupee glyphs don't crash on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Load .env from repo root
ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DATA = ROOT / "data"

BOTS = [
    ("NIFTY",          "paper_portfolio_NIFTY.json"),
    ("SENSEX",         "paper_portfolio_SENSEX.json"),
    ("NIFTY REGIME",   "paper_portfolio_NIFTY_regime.json"),
    ("SENSEX REGIME",  "paper_portfolio_SENSEX_regime.json"),
]


def build_summary() -> str:
    today = datetime.now().date().isoformat()
    lines = [f"📊 EOD SUMMARY — {today}"]
    total_pnl = 0.0
    total_trades = 0
    any_run = False

    for name, fname in BOTS:
        path = DATA / fname
        if not path.exists():
            lines.append(f"  {name:<14}: (not run)")
            continue
        try:
            p = json.loads(path.read_text())
        except Exception as e:
            lines.append(f"  {name:<14}: read error ({e})")
            continue

        any_run = True
        today_trades = [
            t for t in p.get("trade_history", []) or []
            if (t.get("exit_time", "") or "")[:10] == today
        ]
        pnl = sum(float(t.get("pnl", 0) or 0) for t in today_trades)
        wins = sum(1 for t in today_trades if float(t.get("pnl", 0) or 0) > 0)
        cap = p.get("capital", 100000.0)
        total_pnl += pnl
        total_trades += len(today_trades)
        sign = "+" if pnl >= 0 else ""
        wr = f"{wins}/{len(today_trades)}" if today_trades else "—"
        lines.append(
            f"  {name:<14}: {sign}₹{pnl:>7,.0f}  "
            f"{len(today_trades):>2} trades  W:{wr}  "
            f"cap ₹{cap:,.0f}"
        )

    if any_run and total_trades > 0:
        sign = "+" if total_pnl >= 0 else ""
        lines.append("")
        lines.append(f"  TOTAL across 4: {sign}₹{total_pnl:,.0f} on {total_trades} trades")

    return "\n".join(lines)


def send_telegram(msg: str) -> bool:
    if not TOKEN or not CHAT_ID:
        print("[!] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[!] Telegram returned {r.status_code}: {r.text}")
            return False
        return True
    except Exception as e:
        print(f"[!] Telegram send failed: {e}")
        return False


if __name__ == "__main__":
    msg = build_summary()
    print(msg)
    if send_telegram(msg):
        print("[OK] sent to Telegram")
    else:
        sys.exit(1)
